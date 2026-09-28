"""OpenStreetMap everywhere, as vector tiles from OpenFreeMap.

A regional import gives the richest data -- every height tag, and a
record of which heights were measured -- but only for the regions the
user has imported. For everywhere else there is OpenFreeMap: the whole
planet's OpenStreetMap data as vector tiles in the OpenMapTiles schema,
free, keyless, and offered with "no limits on the number of map views or
requests" (openfreemap.org). Its buildings carry ``render_height``, so
they can be extruded wherever the camera goes.

Each z14 tile is fetched once, decoded, filed into the local store and
marked as fetched, so revisiting a place costs nothing and works
offline.

What the tiles lose
-------------------

OpenMapTiles collapses OSM's height information into one number:
``height`` if mapped, else ``building:levels`` x 3.66 m, else a flat
5 m. The tiles do not say which. A building at exactly 5 m is therefore
treated as having no recorded height -- it is the schema's default far
more often than a measurement -- and everything else as "published",
meaning measured or derived from a floor count, not distinguishable.

Attribution, as OpenFreeMap asks: "OpenFreeMap © OpenMapTiles Data from
OpenStreetMap". The data is ODbL 1.0.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .mvt import decode_tile
from .store import GeoStore, encode_ring, ring_bbox, tiles_for_bbox

log = logging.getLogger("mapify.geodata")

__all__ = ["OpenFreeMap", "OFM_ATTRIBUTION", "OFM_LICENCE", "SOURCE"]

SOURCE = "ofm"
OFM_ATTRIBUTION = "OpenFreeMap © OpenMapTiles Data from OpenStreetMap"
OFM_LICENCE = "ODbL 1.0 (OpenStreetMap data); OpenMapTiles schema"
OFM_LINK = "https://openfreemap.org"
TILEJSON = "https://tiles.openfreemap.org/planet"
_UA = ("Mapify/0.1 (open-source image geolocation; "
       "https://github.com/mdyounus-git/mapify)")

# The schema's own default for a building with no height at all.
DEFAULT_HEIGHT = 5

# OpenMapTiles transportation classes, onto the road classes the store uses.
_ROAD = {"motorway": "motorway", "trunk": "trunk", "primary": "primary",
         "secondary": "secondary", "tertiary": "tertiary",
         "minor": "residential", "service": "service", "track": "track",
         "busway": "service", "raceway": "service"}
_PATH_SUB = {"footway", "cycleway", "steps", "pedestrian", "path", "bridleway"}
_GREEN_COVER = {"grass", "wood", "wetland", "forest", "meadow", "scrub"}
_LAYERS = {"building", "transportation", "water", "waterway", "landcover",
           "park", "poi"}


class OpenFreeMap:
    def __init__(self, store: GeoStore, cache_dir: Path):
        self.store = store
        self.cache_dir = Path(cache_dir)
        self._tilejson: dict[str, Any] | None = None
        self._tj_at = 0.0
        self._lock = threading.Lock()

    def _template(self) -> str:
        """The current tile URL; the planet build it names changes weekly."""
        path = self.cache_dir / "openfreemap-tilejson.json"
        now = time.time()
        if self._tilejson is None and path.is_file():
            try:
                self._tilejson = json.loads(path.read_text(encoding="utf-8"))
                self._tj_at = path.stat().st_mtime
            except (OSError, ValueError):
                self._tilejson = None
        if self._tilejson is None or now - self._tj_at > 86_400:
            req = urllib.request.Request(TILEJSON, headers={"User-Agent": _UA})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self._tilejson = json.load(resp)
                self._tj_at = now
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(self._tilejson), encoding="utf-8")
            except Exception:
                if self._tilejson is None:
                    raise
        return self._tilejson["tiles"][0]

    def _fetch(self, url: str) -> bytes | None:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (204, 404):
                return b""
            raise

    def ensure(self, bbox: tuple[float, float, float, float]) -> dict[str, int]:
        """Fetch and file every z14 tile touching ``bbox`` not already held."""
        with self._lock:
            tiles = tiles_for_bbox(bbox, 14)
            missing = [t for t in tiles
                       if not self.store.was_fetched(SOURCE, 14, *t)]
            if not missing:
                return {"tiles": 0, "cached": len(tiles), "features": 0}
            template = self._template()
            from concurrent.futures import ThreadPoolExecutor

            def get(t):
                x, y = t
                url = (template.replace("{z}", "14").replace("{x}", str(x))
                       .replace("{y}", str(y)))
                return t, self._fetch(url)

            with ThreadPoolExecutor(max_workers=4) as pool:
                fetched = list(pool.map(get, missing))

            self.store.begin_stage()
            count = 0
            for (x, y), raw in fetched:
                if raw:
                    count += self._file(raw, x, y)
            self.store.finish_stage()
            for (x, y), raw in fetched:
                if raw is not None:
                    self.store.mark_fetched(SOURCE, 14, x, y)
            return {"tiles": len(missing), "cached": len(tiles) - len(missing),
                    "features": count}

    def _file(self, raw: bytes, x: int, y: int) -> int:
        layers = decode_tile(raw, 14, x, y, _LAYERS)
        base_id = (x << 34) | (y << 20)
        n = 0

        def stage(layer, geometry_rings, feature):
            nonlocal n
            bb = ring_bbox(geometry_rings)
            # Clipped exactly to the tile edge; nudge the box inside so
            # the feature is filed in this tile rather than its neighbour.
            eps = 1e-9
            bb = (bb[0] + eps, bb[1] + eps, bb[2] - eps, bb[3] - eps)
            self.store.stage(layer, SOURCE, bb, feature)
            n += 1

        # Every layer can mix geometry types -- OpenMapTiles puts
        # pedestrian plazas and piers in "transportation" as polygons, and
        # label points in "park" -- so each loop takes only the type it
        # knows how to file.
        for i, f in enumerate(layers.get("building", [])):
            if f["type"] != "polygon":
                continue
            p = f["props"]
            if p.get("hide_3d"):
                continue                 # an outline whose parts are drawn
            h = p.get("render_height")
            base = p.get("render_min_height") or 0
            height = float(h) if isinstance(h, (int, float)) else float(DEFAULT_HEIGHT)
            hsrc = 0 if (h is None or h == DEFAULT_HEIGHT) else 3
            for poly in f["geometry"]:
                stage("building", poly, [
                    base_id | (i & 0xFFFFF), None, "building",
                    int(round(max(height, base + 0.5) * 10)),
                    int(round(float(base) * 10)), hsrc, None, 0,
                    [encode_ring(r) for r in poly]])

        for i, f in enumerate(layers.get("transportation", [])):
            if f["type"] != "line":
                continue
            p = f["props"]
            cls = p.get("class")
            sub = p.get("subclass")
            if cls == "path":
                road = sub if sub in _PATH_SUB else "path"
            else:
                road = _ROAD.get(cls)
            if road is None:
                continue                 # rail, ferry, transit: not streets
            for line in f["geometry"]:
                if len(line) >= 2:
                    stage("road", [line], [base_id | (0x40000 + i), road,
                                           p.get("name"), None,
                                           encode_ring(line)])

        for i, f in enumerate(layers.get("water", [])):
            if f["type"] != "polygon":
                continue
            for poly in f["geometry"]:
                stage("water", poly, [base_id | (0x80000 + i), None,
                                      [encode_ring(r) for r in poly]])

        for i, f in enumerate(layers.get("waterway", [])):
            if f["type"] != "line":
                continue
            p = f["props"]
            for line in f["geometry"]:
                if len(line) >= 2:
                    stage("waterway", [line], [base_id | (0xA0000 + i),
                                               p.get("class") or "stream",
                                               p.get("name"),
                                               encode_ring(line)])

        greens = [(f, f["props"].get("subclass") or f["props"].get("class"))
                  for f in layers.get("landcover", [])
                  if f["type"] == "polygon"
                  and f["props"].get("class") in _GREEN_COVER]
        greens += [(f, "park") for f in layers.get("park", [])
                   if f["type"] == "polygon"]
        for i, (f, kind) in enumerate(greens):
            for poly in f["geometry"]:
                stage("green", poly, [base_id | (0xC0000 + i), kind, None,
                                      [encode_ring(r) for r in poly]])

        for i, f in enumerate(layers.get("poi", [])):
            if f["type"] != "point":
                continue
            p = f["props"]
            name = p.get("name") or p.get("name:latin")
            if not name:
                continue
            lon, lat = f["geometry"]
            kind = f"{p.get('class')}={p.get('subclass') or p.get('class')}"
            self.store.stage("poi", SOURCE, (lon, lat, lon, lat),
                             [base_id | (0xE0000 + i), kind, name,
                              int(round(lon * 1e6)), int(round(lat * 1e6))])
            n += 1
        return n
