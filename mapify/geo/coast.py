"""Distance to the sea, from the country polygons Mapify already ships.

No coastline layer is bundled, and none is needed: Natural Earth's
admin-0 polygons are topologically consistent, so an edge shared by two
countries is a land border and appears in both, while an edge belonging
to one country only is where that country meets the sea (or, rarely, an
unclaimed area). Keeping the edges that occur once gives the coastline.

Used by technique 27 to ask a plain question of a candidate: the
photograph shows a shoreline -- is this place anywhere near one? The
answer is approximate, to the few kilometres Natural Earth's 1:10m
scale supports, and the finding says so.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path

import numpy as np

from ..paths import cache_dir, read_root

__all__ = ["distance_to_coast_km", "coast_available"]

_LOCK = threading.Lock()
_STATE: dict = {}
_CELL = 1.0                     # grid cell in degrees for the lookup


def _source() -> Path:
    return read_root() / "data" / "naturalearth" / "ne_10m_admin_0_countries.shp"


def coast_available() -> bool:
    return _source().exists()


def _build() -> np.ndarray:
    """Coastline vertices, about 2 km apart, as an (N, 2) lon/lat array."""
    import shapefile                                  # pyshp, MIT

    counts: dict[tuple, int] = {}
    rings: list[list[tuple[float, float]]] = []
    with shapefile.Reader(str(_source())) as sf:
        for shape in sf.iterShapes():
            pts = shape.points
            parts = list(shape.parts) + [len(pts)]
            for a, b in zip(parts, parts[1:]):
                ring = [(round(x, 6), round(y, 6)) for x, y in pts[a:b]]
                rings.append(ring)
                for p, q in zip(ring, ring[1:]):
                    key = (p, q) if p <= q else (q, p)
                    counts[key] = counts.get(key, 0) + 1

    out: list[tuple[float, float]] = []
    for ring in rings:
        for p, q in zip(ring, ring[1:]):
            key = (p, q) if p <= q else (q, p)
            if counts.get(key) != 1:
                continue                             # a shared land border
            # Densify long edges so a vertex is never far from the line.
            dx, dy = q[0] - p[0], q[1] - p[1]
            steps = max(1, int(math.hypot(dx, dy) / 0.02))   # ~2 km
            for k in range(steps):
                out.append((p[0] + dx * k / steps, p[1] + dy * k / steps))
    arr = np.asarray(out, dtype=np.float32)
    # Thin to one vertex per ~0.01 degree: the 10m data is far denser than
    # a kilometre-level answer needs.
    keys = np.round(arr / 0.01).astype(np.int32)
    _, idx = np.unique(keys, axis=0, return_index=True)
    return arr[np.sort(idx)]


def _load() -> tuple[np.ndarray, dict]:
    with _LOCK:
        if "grid" in _STATE:
            return _STATE["pts"], _STATE["grid"]
        path = cache_dir() / "coastline-10m.npy"
        if path.exists():
            pts = np.load(path)
        else:
            pts = _build()
            np.save(path, pts)
        grid: dict[tuple[int, int], np.ndarray] = {}
        cells = np.floor(pts / _CELL).astype(np.int32)
        order = np.lexsort((cells[:, 1], cells[:, 0]))
        cells, sorted_pts = cells[order], pts[order]
        keys, starts = np.unique(cells, axis=0, return_index=True)
        ends = list(starts[1:]) + [len(sorted_pts)]
        for (cx, cy), s, e in zip(keys, starts, ends):
            grid[(int(cx), int(cy))] = sorted_pts[s:e]
        _STATE.update(pts=pts, grid=grid)
        return pts, grid


def distance_to_coast_km(lat: float, lon: float, max_km: float = 600.0
                         ) -> float | None:
    """Approximate distance to the nearest coastline, or None if > max_km."""
    if not coast_available():
        return None
    _pts, grid = _load()
    reach = int(math.ceil(max_km / (111.0 * _CELL))) + 1
    cx, cy = int(math.floor(lon / _CELL)), int(math.floor(lat / _CELL))
    best = None
    for ring in range(reach + 1):
        found_here = False
        for gx in range(cx - ring, cx + ring + 1):
            for gy in range(cy - ring, cy + ring + 1):
                if max(abs(gx - cx), abs(gy - cy)) != ring:
                    continue
                cand = grid.get((gx, gy))
                if cand is None:
                    continue
                found_here = True
                la1, lo1 = math.radians(lat), math.radians(lon)
                la2 = np.radians(cand[:, 1].astype(np.float64))
                lo2 = np.radians(cand[:, 0].astype(np.float64))
                a = (np.sin((la2 - la1) / 2) ** 2
                     + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2)
                d = float(np.min(2 * 6371.0088 * np.arcsin(np.sqrt(a))))
                best = d if best is None else min(best, d)
        # Once something is found, one more ring settles the minimum.
        if best is not None and not found_here and ring * 111.0 * _CELL > best:
            break
        if best is not None and ring * 111.0 * _CELL > best + 111.0 * _CELL:
            break
    if best is None or best > max_km:
        return None
    return best
