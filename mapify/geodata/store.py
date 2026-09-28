"""A tiled, compressed store of map features, queried by radius.

One SQLite file. Features are grouped into Web Mercator tiles and each
tile's features are stored together as one compressed blob, which is
what makes seven million buildings fit in a few hundred megabytes and
come back in milliseconds: a landing reads a handful of tiles, not a
table scan.

Three tile levels, chosen by the size of the feature:

  z14   about 2.4 km across at the equator. Buildings, most streets.
  z10   about 39 km. Long roads, rivers, large lakes and forests.
  z6    about 626 km. The few features bigger than that.

A feature is filed at the finest level where it spans no more than two
tiles in each direction, in every tile it touches, and de-duplicated by
id on the way out. A query reads all three levels.

Coordinates are stored as integers in millionths of a degree (about
11 cm), delta-encoded along each ring, which compresses well and loses
nothing a building footprint can resolve.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any, Iterable

__all__ = ["GeoStore", "LAYERS", "tile_of", "tiles_for_bbox", "bbox_around"]

LAYERS = ("building", "road", "water", "waterway", "green", "poi")
_LAYER_ID = {name: i for i, name in enumerate(LAYERS)}
LEVELS = (14, 10, 6)
SCALE = 1_000_000          # integer micro-degrees


def tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 1 << z
    lat = max(-85.05112878, min(85.05112878, lat))
    x = int((lon + 180.0) / 360.0 * n)
    lr = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lr)) / math.pi) / 2.0 * n)
    return min(n - 1, max(0, x)), min(n - 1, max(0, y))


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) of one tile."""
    n = 1 << z

    def lat(yy: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    return (x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0,
            lat(y))


def tiles_for_bbox(bbox: tuple[float, float, float, float], z: int
                   ) -> list[tuple[int, int]]:
    """Every tile at zoom ``z`` touching (min_lon, min_lat, max_lon, max_lat)."""
    x0, y1 = tile_of(bbox[0], bbox[1], z)
    x1, y0 = tile_of(bbox[2], bbox[3], z)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def bbox_around(lat: float, lon: float, radius_m: float
                ) -> tuple[float, float, float, float]:
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(0.05, math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def encode_ring(ring: Iterable[tuple[float, float]]) -> list[int]:
    out: list[int] = []
    px = py = 0
    for lon, lat in ring:
        x, y = int(round(lon * SCALE)), int(round(lat * SCALE))
        out.append(x - px)
        out.append(y - py)
        px, py = x, y
    return out


def decode_ring(data: list[int]) -> list[list[float]]:
    out = []
    x = y = 0
    for i in range(0, len(data) - 1, 2):
        x += data[i]
        y += data[i + 1]
        out.append([x / SCALE, y / SCALE])
    return out


def ring_bbox(rings: list[list[tuple[float, float]]]
              ) -> tuple[float, float, float, float]:
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return (min(xs), min(ys), max(xs), max(ys))


def _placement(bbox: tuple[float, float, float, float]
               ) -> tuple[int, list[tuple[int, int]]]:
    for z in LEVELS:
        tiles = tiles_for_bbox(bbox, z)
        xs = {t[0] for t in tiles}
        ys = {t[1] for t in tiles}
        if len(xs) <= 2 and len(ys) <= 2:
            return z, tiles
    z = LEVELS[-1]
    return z, tiles_for_bbox(bbox, z)[:256]


class GeoStore:
    """Read and write the tiled feature store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A long import and the running application can share the file;
        # WAL lets them, and the timeout makes a writer wait its turn
        # rather than fail.
        self.db = sqlite3.connect(str(self.path), check_same_thread=False,
                                  timeout=120)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS tiles(
                z INTEGER, x INTEGER, y INTEGER, layer INTEGER,
                source TEXT, n INTEGER, data BLOB,
                PRIMARY KEY (z, x, y, layer, source));
            CREATE TABLE IF NOT EXISTS regions(
                name TEXT PRIMARY KEY, source TEXT, licence TEXT,
                attribution TEXT, bbox TEXT, imported_at TEXT,
                checksum TEXT, counts TEXT);
            -- Tiles fetched from a remote source that held nothing. Without
            -- this an empty tile is indistinguishable from an unfetched one
            -- and would be asked for again on every landing.
            CREATE TABLE IF NOT EXISTS fetched(
                source TEXT, z INTEGER, x INTEGER, y INTEGER, at TEXT,
                PRIMARY KEY (source, z, x, y));
        """)
        self._cache: dict[tuple, list] = {}

    # ------------------------------------------------------------ writing

    def begin_stage(self) -> None:
        # Named per writer: an import in one process and a fetch in the
        # application must never share -- or drop -- one staging table.
        import os
        import uuid
        self._stage_table = f"stage_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        self.db.execute(f"CREATE TABLE {self._stage_table}(z INTEGER, "
                        f"x INTEGER, y INTEGER, layer INTEGER, source TEXT, "
                        f"feat TEXT)")
        self.db.commit()
        self._pending: list[tuple] = []

    def stage(self, layer: str, source: str,
              bbox: tuple[float, float, float, float], feature: list) -> None:
        z, tiles = _placement(bbox)
        text = json.dumps(feature, separators=(",", ":"), ensure_ascii=False)
        lid = _LAYER_ID[layer]
        for x, y in tiles:
            self._pending.append((z, x, y, lid, source, text))
        if len(self._pending) >= 50_000:
            self._flush_stage()

    def _flush_stage(self) -> None:
        if self._pending:
            self.db.executemany(
                f"INSERT INTO {self._stage_table} VALUES (?,?,?,?,?,?)",
                self._pending)
            self.db.commit()
            self._pending = []

    def finish_stage(self, replace_source: str | None = None,
                     progress=None) -> int:
        """Compress staged features into tiles. Returns tiles written."""
        self._flush_stage()
        if replace_source:
            self.db.execute("DELETE FROM tiles WHERE source = ?",
                            (replace_source,))
        table = self._stage_table
        self.db.execute(f"CREATE INDEX IF NOT EXISTS {table}_key ON {table}"
                        f"(z, x, y, layer, source)")
        cur = self.db.execute(f"SELECT z, x, y, layer, source, feat FROM "
                              f"{table} ORDER BY z, x, y, layer, source")
        written = 0
        key = None
        batch: list[str] = []
        out: list[tuple] = []

        def emit():
            nonlocal written
            if key is None or not batch:
                return
            blob = zlib.compress(("[" + ",".join(batch) + "]").encode("utf-8"), 6)
            out.append((*key, len(batch), blob))
            written += 1

        for z, x, y, layer, source, feat in cur:
            k = (z, x, y, layer, source)
            if k != key:
                emit()
                key, batch = k, []
                if len(out) >= 2_000:
                    self._write_tiles(out)
                    out = []
                    if progress:
                        progress(written)
            batch.append(feat)
        emit()
        self._write_tiles(out)
        self.db.execute(f"DROP TABLE {table}")
        self.db.commit()
        self._cache.clear()
        return written

    def _write_tiles(self, rows: list[tuple]) -> None:
        # Merge with what is already there for the same key: a second
        # import of a neighbouring region can share border tiles.
        for z, x, y, layer, source, n, blob in rows:
            old = self.db.execute(
                "SELECT n, data FROM tiles WHERE z=? AND x=? AND y=? "
                "AND layer=? AND source=?", (z, x, y, layer, source)).fetchone()
            if old:
                # New first, so a re-import's version of a feature wins
                # over the stale one it replaces.
                merged = (json.loads(zlib.decompress(blob))
                          + json.loads(zlib.decompress(old[1])))
                seen: set = set()
                uniq = []
                for f in merged:
                    if f[0] in seen:
                        continue
                    seen.add(f[0])
                    uniq.append(f)
                blob = zlib.compress(json.dumps(uniq, separators=(",", ":"),
                                                ensure_ascii=False)
                                     .encode("utf-8"), 6)
                n = len(uniq)
            self.db.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?,?,?,?)",
                            (z, x, y, layer, source, n, blob))
        self.db.commit()

    def record_region(self, name: str, source: str, licence: str,
                      attribution: str, bbox: tuple | None, checksum: str,
                      counts: dict[str, int]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO regions VALUES (?,?,?,?,?,?,?,?)",
            (name, source, licence, attribution,
             json.dumps(bbox) if bbox else None,
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             checksum, json.dumps(counts)))
        self.db.commit()

    def mark_fetched(self, source: str, z: int, x: int, y: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO fetched VALUES (?,?,?,?,?)",
                        (source, z, x, y,
                         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        self.db.commit()

    def was_fetched(self, source: str, z: int, x: int, y: int) -> bool:
        return self.db.execute(
            "SELECT 1 FROM fetched WHERE source=? AND z=? AND x=? AND y=?",
            (source, z, x, y)).fetchone() is not None

    # ------------------------------------------------------------ reading

    def regions(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT name, source, licence, attribution, bbox, imported_at, "
            "checksum, counts FROM regions ORDER BY name").fetchall()
        return [{"name": r[0], "source": r[1], "licence": r[2],
                 "attribution": r[3],
                 "bbox": json.loads(r[4]) if r[4] else None,
                 "imported_at": r[5], "checksum": r[6],
                 "counts": json.loads(r[7]) if r[7] else {}} for r in rows]

    def covering(self, lat: float, lon: float) -> list[dict[str, Any]]:
        """Regions whose bounding box contains the point."""
        out = []
        for region in self.regions():
            b = region.get("bbox")
            if b and b[0] <= lon <= b[2] and b[1] <= lat <= b[3]:
                out.append(region)
        return out

    def _tile(self, z: int, x: int, y: int, layer: int) -> list[tuple[str, list]]:
        key = (z, x, y, layer)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        rows = self.db.execute(
            "SELECT source, data FROM tiles WHERE z=? AND x=? AND y=? "
            "AND layer=?", key).fetchall()
        feats = [(source, f) for source, blob in rows
                 for f in json.loads(zlib.decompress(blob))]
        if len(self._cache) > 512:
            self._cache.clear()
        self._cache[key] = feats
        return feats

    def query(self, bbox: tuple[float, float, float, float],
              layers: Iterable[str] = LAYERS) -> dict[str, list[tuple[str, list]]]:
        """Raw features by layer, de-duplicated, for a bounding box."""
        out: dict[str, list[tuple[str, list]]] = {}
        for layer in layers:
            lid = _LAYER_ID[layer]
            seen: set = set()
            items: list[tuple[str, list]] = []
            for z in LEVELS:
                for x, y in tiles_for_bbox(bbox, z):
                    for source, f in self._tile(z, x, y, lid):
                        k = (source, f[0])
                        if k in seen:
                            continue
                        seen.add(k)
                        items.append((source, f))
            out[layer] = items
        return out

    def close(self) -> None:
        self.db.close()
