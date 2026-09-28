"""Google Open Buildings footprints, fetched per neighbourhood and kept.

Google Research's Open Buildings v3: 1.8 billion building footprints
detected in satellite imagery across Africa, South and South-East Asia,
Latin America and the Caribbean -- exactly where OpenStreetMap is often
thinnest. Released under CC BY 4.0 or ODbL 1.0, at the user's choice.

The original distribution is gzipped CSV by S2 cell, up to 7.8 GB a
file. This reads the FlatGeobuf copy on Source Cooperative instead,
whose spatial index lets a landing fetch only the buildings around it.
What is fetched is filed into the local store, per z14 tile, and each
tile is marked as fetched -- including the empty ones -- so the same
neighbourhood is never asked for twice.

What these footprints are not
-----------------------------

They are machine detections with a confidence score (0.65 to 1.0 in
v3), not surveyed buildings, and they carry no height, no type and no
name. The view draws them at a nominal height, in their own colour, and
the legend says so.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from urllib.parse import quote
from pathlib import Path
from typing import Any

from .fgb import FgbSource, open_url
from .store import GeoStore, encode_ring, ring_bbox, tile_of, tiles_for_bbox

log = logging.getLogger("mapify.geodata")

__all__ = ["OpenBuildings", "GOB_LICENCE", "GOB_ATTRIBUTION", "SOURCE"]

SOURCE = "gob"
GOB_LICENCE = "CC BY 4.0 or ODbL 1.0 (user's choice); used here under CC BY 4.0"
GOB_ATTRIBUTION = "Building footprints: Google Open Buildings v3 (CC BY 4.0)"
GOB_LINK = "https://sites.research.google/gr/open-buildings/"

_BUCKET = "https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop"
_PREFIX = "google-research-open-buildings/fgb-s2/"
_UA = ("Mapify/0.1 (open-source image geolocation; "
       "https://github.com/mdyounus-git/mapify)")

# Nominal height for a detected footprint: nothing about it is known but
# its outline, and one or two storeys is the modest guess.
NOMINAL_HEIGHT_M = 4.0

# Envelopes of every file in the mirror, built once from their headers
# and shipped, because the release is fixed and asking 370 files for
# their headers costs the first landing half a minute.
SHIPPED_INDEX = Path(__file__).resolve().parent.parent / "data" / \
    "open_buildings_index.json"


def _cell_token(key: str) -> str:
    return key.rsplit("/", 1)[-1].split("_")[0]


def _without_duplicate_cells(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop split parts of any S2 cell that is also present whole.

    The mirror holds five dense cells twice: once as a single file of 13
    to 19 GB, and again as numbered parts in "split buildings/". Keeping
    both would draw every building in those cells twice, and one cell's
    parts are incomplete (50 of its 73 million footprints). The whole
    file has one spatial index covering everything, so it is the one
    kept.
    """
    whole = {_cell_token(r["key"]) for r in rows if "split" not in r["key"]}
    return [r for r in rows
            if "split" not in r["key"] or _cell_token(r["key"]) not in whole]


class OpenBuildings:
    """Find, fetch and cache Open Buildings footprints for an area."""

    def __init__(self, store: GeoStore, index_path: Path):
        self.store = store
        self.index_path = Path(index_path)
        self._index: list[dict[str, Any]] | None = None
        self._sources: dict[str, FgbSource] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------- the index

    def _list_files(self) -> list[tuple[str, int]]:
        url = f"{_BUCKET}?list-type=2&max-keys=1000&prefix={_PREFIX}"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            xml = resp.read().decode("utf-8")
        files = re.findall(r"<Key>(.*?)</Key>.*?<Size>(\d+)</Size>", xml, re.S)
        return [(k, int(s)) for k, s in files if k.endswith(".fgb")]

    def index(self) -> list[dict[str, Any]]:
        """Every file's envelope. Built once, from the headers, then kept.

        The dataset is a fixed release, so this never goes stale; if it is
        ever re-released the index file is simply deleted and rebuilt.
        """
        if self._index is not None:
            return self._index
        for path in (self.index_path, SHIPPED_INDEX):
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                files = data["files"] if isinstance(data, dict) else data
                self._index = {"files": _without_duplicate_cells(files)}
                return self._index

        from concurrent.futures import ThreadPoolExecutor

        files = [(k, n) for k, n in self._list_files() if n > 1024]

        def head(item):
            key, size = item
            try:
                src = open_url(f"{_BUCKET}/{quote(key)}")
                return {"key": key, "bytes": size,
                        "envelope": list(src.header.envelope),
                        "features": src.header.features_count}
            except Exception as exc:
                log.warning("Open Buildings header %s: %s", key, exc)
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            rows = [r for r in pool.map(head, files) if r and r["envelope"]]
        rows = _without_duplicate_cells(rows)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps({"built": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": rows}, indent=1),
            encoding="utf-8")
        self._index = {"files": rows}
        return self._index

    def files_for(self, bbox: tuple[float, float, float, float]
                  ) -> list[dict[str, Any]]:
        files = self.index()
        files = files["files"] if isinstance(files, dict) else files
        out = []
        for f in files:
            e = f["envelope"]
            if e[2] >= bbox[0] and e[0] <= bbox[2] and e[3] >= bbox[1] \
                    and e[1] <= bbox[3]:
                out.append(f)
        return out

    def covers(self, lat: float, lon: float) -> bool:
        return bool(self.files_for((lon, lat, lon, lat)))

    # --------------------------------------------------------- fetching

    def _source(self, key: str) -> FgbSource:
        src = self._sources.get(key)
        if src is None:
            src = open_url(f"{_BUCKET}/{quote(key)}")
            if len(self._sources) > 8:
                self._sources.clear()
            self._sources[key] = src
        return src

    def ensure(self, bbox: tuple[float, float, float, float]) -> dict[str, int]:
        """Make sure every z14 tile touching ``bbox`` has been fetched."""
        with self._lock:
            tiles = tiles_for_bbox(bbox, 14)
            missing = [t for t in tiles
                       if not self.store.was_fetched(SOURCE, 14, *t)]
            if not missing:
                return {"fetched": 0, "tiles": 0, "cached": len(tiles)}

            # One query over the missing tiles' union, rather than a query
            # per tile: the tree walk is the expensive part.
            from .store import _tile_bounds
            boxes = [_tile_bounds(14, x, y) for x, y in missing]
            union = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes))

            fetched = 0
            files = self.files_for(union)
            if files:
                self.store.begin_stage()
                for f in files:
                    src = self._source(f["key"])
                    token = int(f["key"].rsplit("/", 1)[-1].split("_")[0], 16)
                    hits = src.search(union)
                    for index, props, rings in src.features(hits):
                        if not rings:
                            continue
                        rb = ring_bbox([rings[0]])
                        cx, cy = (rb[0] + rb[2]) / 2, (rb[1] + rb[3]) / 2
                        # File each footprint only in a tile being filled
                        # now; one already cached keeps what it has.
                        if tile_of(cx, cy, 14) not in missing:
                            continue
                        self.store.stage("building", SOURCE, rb, [
                            (token << 28) | index, None, "building",
                            int(NOMINAL_HEIGHT_M * 10), 0, 0, None, 0,
                            [encode_ring(r) for r in rings],
                            round(float(props.get("confidence", 0.0)), 3),
                            round(float(props.get("area_in_meters", 0.0)), 1),
                        ])
                        fetched += 1
                self.store.finish_stage()
            for x, y in missing:
                self.store.mark_fetched(SOURCE, 14, x, y)
            return {"fetched": fetched, "tiles": len(missing),
                    "cached": len(tiles) - len(missing)}
