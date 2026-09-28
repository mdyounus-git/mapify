"""What is around a coordinate: buildings, streets, water, green, places.

The one entry point the application uses. It reads the local store,
tops it up from Google Open Buildings where that dataset covers the
area and OpenStreetMap's buildings are thin, and returns plain
coordinates the 3D view can draw -- each feature tagged with where it
came from and, for buildings, how its height is known.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ..paths import geodata_dir
from .open_buildings import (GOB_ATTRIBUTION, GOB_LICENCE, GOB_LINK,
                             OpenBuildings)
from .osm_import import OSM_ATTRIBUTION, OSM_LICENCE
from .store import GeoStore, bbox_around, decode_ring
from .vector_tiles import (OFM_ATTRIBUTION, OFM_LICENCE, OFM_LINK,
                           OpenFreeMap)

log = logging.getLogger("mapify.geodata")

__all__ = ["GeoService", "get_service"]

# How a building's height is known. "published" is OpenMapTiles' single
# number: measured or from a floor count, and the tiles do not say which.
_HSRC = {0: "assumed", 1: "levels", 2: "measured", 3: "published"}

# Where a building footprint comes from, most trusted first. When two
# sources both have a building at the same spot, the earlier one wins:
# a local OSM import keeps every height tag, the vector tiles keep one
# number, and a detection keeps only an outline.
_TRUST = ("osm", "ofm", "gob")

# Carriageway widths in metres by road class, for drawing. Approximate
# by design: the point is that a motorway reads as wider than a lane.
ROAD_WIDTH = {
    "motorway": 14.0, "trunk": 12.0, "primary": 10.0, "secondary": 8.0,
    "tertiary": 7.0, "unclassified": 5.5, "residential": 5.0,
    "living_street": 4.5, "service": 3.5, "pedestrian": 4.0, "track": 3.0,
    "footway": 1.6, "cycleway": 1.8, "path": 1.4, "steps": 1.6, "road": 5.0,
    "motorway_link": 7.0, "trunk_link": 7.0, "primary_link": 6.0,
    "secondary_link": 6.0, "tertiary_link": 5.0,
}


def _inside(x: float, y: float, ring: list[list[float]]) -> bool:
    """Point in polygon, even-odd rule."""
    hit = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


class GeoService:
    def __init__(self, online: bool = True):
        self.dir = geodata_dir()
        self.store = GeoStore(self.dir / "store.sqlite")
        self.open_buildings = OpenBuildings(
            self.store, self.dir / "open_buildings_index.json")
        self.vector_tiles = OpenFreeMap(self.store, self.dir)
        self.online = online

    # ------------------------------------------------------------ status

    def status(self, lat: float | None = None, lon: float | None = None
               ) -> dict[str, Any]:
        regions = self.store.regions()
        out: dict[str, Any] = {
            "regions": regions,
            "open_buildings_index": self.open_buildings.index_path.is_file(),
            "online": self.online,
            "directory": str(self.dir),
        }
        if lat is not None and lon is not None:
            out["osm_region"] = [r["name"] for r in
                                 self.store.covering(lat, lon)]
        return out

    # ------------------------------------------------------------ around

    def around(self, lat: float, lon: float, radius_m: float = 800.0,
               fetch_open_buildings: bool = True) -> dict[str, Any]:
        radius_m = max(100.0, min(3000.0, float(radius_m)))
        bbox = bbox_around(lat, lon, radius_m)
        notes: list[str] = []

        osm_regions = [r["name"] for r in self.store.covering(lat, lon)]
        # A region's bounding box is a rectangle; the extract inside it is
        # not. If the local import has nothing here, the point is outside
        # the extract's real outline and the worldwide tiles take over.
        local_has_data = bool(osm_regions) and any(
            src == "osm" for src, _f in self.store.query(bbox, ("building", "road"))
            ["road"][:1] + self.store.query(bbox, ("building",))["building"][:1])
        ofm_status: dict[str, Any] = {"used": False}
        if not local_has_data and self.online:
            try:
                ofm_status = {"used": True, **self.vector_tiles.ensure(bbox)}
            except Exception as exc:
                log.warning("OpenFreeMap fetch failed: %s", exc)
                ofm_status = {"used": False, "error": str(exc)}
                notes.append("OpenStreetMap vector tiles could not be reached; "
                             "only locally stored features are shown.")
        if not local_has_data and not self.online:
            notes.append(
                "Offline, and no OpenStreetMap region is imported for this "
                "location: only features stored earlier are shown.")

        gob_status: dict[str, Any] = {"covered": False}
        if fetch_open_buildings and self.online:
            try:
                if self.open_buildings.covers(lat, lon):
                    gob_status = {"covered": True,
                                  **self.open_buildings.ensure(bbox)}
            except Exception as exc:
                log.warning("Open Buildings fetch failed: %s", exc)
                gob_status = {"covered": None, "error": str(exc)}
                notes.append("Google Open Buildings could not be reached; "
                             "only locally stored buildings are shown.")

        raw = self.store.query(bbox)
        buildings = []
        heights = {"measured": 0, "levels": 0, "published": 0, "assumed": 0}

        def in_box(x, y):
            return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]

        for source, f in raw["building"]:
            rings = [decode_ring(r) for r in f[8]]
            if not rings or len(rings[0]) < 3:
                continue
            cx = sum(p[0] for p in rings[0]) / len(rings[0])
            cy = sum(p[1] for p in rings[0]) / len(rings[0])
            if not in_box(cx, cy):
                continue
            entry = {
                "id": f"{source}:{f[0]}", "source": source,
                "polygon": rings, "name": f[1], "type": f[2],
                "height": f[3] / 10.0, "base": f[4] / 10.0,
                "height_source": _HSRC.get(f[5], "assumed"),
                "levels": f[6], "part": bool(f[7]),
                "centroid": [cx, cy],
            }
            if source == "gob":
                entry["confidence"] = f[9] if len(f) > 9 else None
            buildings.append(entry)

        # Drop a footprint when a more trusted source already has a
        # building there: the local OSM import beats the vector tiles
        # (same data, fewer losses), and both beat a detection, which has
        # an outline and nothing else.
        buildings = self._dedupe(buildings)
        for b in buildings:
            heights[b["height_source"]] = heights.get(b["height_source"], 0) + 1

        roads = []
        for source, f in raw["road"]:
            path = decode_ring(f[4])
            roads.append({"id": f"{source}:{f[0]}", "class": f[1],
                          "name": f[2], "lanes": f[3], "path": path,
                          "width": ROAD_WIDTH.get(f[1], 4.0)})
        water = [{"id": f"{s}:{f[0]}", "name": f[1],
                  "polygon": [decode_ring(r) for r in f[2]]}
                 for s, f in raw["water"]]
        waterways = [{"id": f"{s}:{f[0]}", "kind": f[1], "name": f[2],
                      "path": decode_ring(f[3])} for s, f in raw["waterway"]]
        green = [{"id": f"{s}:{f[0]}", "kind": f[1], "name": f[2],
                  "polygon": [decode_ring(r) for r in f[3]]}
                 for s, f in raw["green"]]
        pois = []
        for s, f in raw["poi"]:
            x, y = f[3] / 1e6, f[4] / 1e6
            if in_box(x, y):
                pois.append({"id": f"{s}:{f[0]}", "kind": f[1], "name": f[2],
                             "position": [x, y],
                             "distance_m": round(self._metres(lat, lon, y, x))})
        pois.sort(key=lambda p: p["distance_m"])

        n_osm = sum(1 for b in buildings if b["source"] == "osm")
        n_ofm = sum(1 for b in buildings if b["source"] == "ofm")
        n_gob = sum(1 for b in buildings if b["source"] == "gob")
        used = {s for s, _f in raw["road"]} | {s for s, _f in raw["building"]}
        sources = []
        if "osm" in used:
            sources.append({"name": "OpenStreetMap (local import)",
                            "licence": OSM_LICENCE,
                            "attribution": OSM_ATTRIBUTION,
                            "link": "https://www.openstreetmap.org/copyright",
                            "buildings": n_osm, "regions": osm_regions})
        if "ofm" in used:
            sources.append({"name": "OpenStreetMap via OpenFreeMap",
                            "licence": OFM_LICENCE,
                            "attribution": OFM_ATTRIBUTION, "link": OFM_LINK,
                            "buildings": n_ofm})
        if n_gob:
            sources.append({"name": "Google Open Buildings v3",
                            "licence": GOB_LICENCE,
                            "attribution": GOB_ATTRIBUTION, "link": GOB_LINK,
                            "buildings": n_gob})
        if heights["assumed"] and buildings:
            share = heights["assumed"] / len(buildings)
            notes.append(
                f"{share:.0%} of these buildings have no recorded height and "
                f"are drawn at a nominal one; they are shaded differently. "
                f"Only the measured and floor-count heights are data.")

        return {
            "center": [lon, lat], "radius_m": radius_m, "bbox": list(bbox),
            "buildings": buildings, "roads": roads, "water": water,
            "waterways": waterways, "green": green, "pois": pois[:200],
            "heights": heights, "sources": sources, "notes": notes,
            "open_buildings": gob_status, "vector_tiles": ofm_status,
        }

    @staticmethod
    def _dedupe(buildings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep one footprint per building, from the most trusted source."""
        by_source: dict[str, list[dict[str, Any]]] = {}
        for b in buildings:
            by_source.setdefault(b["source"], []).append(b)
        kept: list[dict[str, Any]] = []
        # A coarse grid of the kept footprints' boxes, so each test only
        # looks at its neighbours rather than every building in view.
        grid: dict[tuple[int, int], list[tuple]] = {}
        cell = 0.001                                     # about 110 m

        def cells(bb):
            for gx in range(int(bb[0] // cell), int(bb[2] // cell) + 1):
                for gy in range(int(bb[1] // cell), int(bb[3] // cell) + 1):
                    yield gx, gy

        for source in _TRUST + tuple(k for k in by_source if k not in _TRUST):
            for b in by_source.get(source, []):
                x, y = b["centroid"]
                key = (int(x // cell), int(y // cell))
                if any(bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]
                       and _inside(x, y, ring)
                       for ring, bb, src in grid.get(key, ()) if src != source):
                    continue
                kept.append(b)
                ring = b["polygon"][0]
                bb = GeoService._bounds(ring)
                for c in cells(bb):
                    grid.setdefault(c, []).append((ring, bb, source))
        return kept

    @staticmethod
    def _bounds(ring):
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _metres(lat1, lon1, lat2, lon2) -> float:
        r = 6_371_008.8
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp, dl = p2 - p1, math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))


_SHARED: dict[str, GeoService] = {}


def get_service(online: bool | None = None) -> GeoService:
    """One service per process, shared by the web routes and the window.

    Both open the same SQLite store; two connections in one process would
    only contend with each other for the write lock.

    ``online`` is set only by callers that state it -- the server and the
    pipeline, from the configuration. A caller that only reads (a
    technique looking up nearby landmarks) passes nothing, so it can never
    switch the 3D view's fetching off by happening to run first.
    """
    svc = _SHARED.get("svc")
    if svc is None:
        svc = GeoService(online=True if online is None else online)
        _SHARED["svc"] = svc
    elif online is not None:
        svc.online = online
    return svc
