"""Named landmarks near a coordinate, from local map data.

This replaces a query per analysis to the public Overpass servers. Their
usage policy names "setting up an app ... and relying on the public
instances as backend" as the thing not to do, and that is exactly what
one query per analysis was. The same OpenStreetMap features are now
read from the local store -- an imported region, or OpenFreeMap vector
tiles fetched once and kept -- so the lookup costs a public server
nothing and works offline for any place seen before.

Returned in the shape the Overpass client used, so the report and the
interface do not change.
"""

from __future__ import annotations

import logging
import math

from ..geo.geodesy import bearing_deg, haversine_km
from ..geo.landmarks import Feature, Surroundings
from .store import bbox_around

log = logging.getLogger("mapify.geodata")

__all__ = ["nearby"]

ATTRIBUTION = "Nearby features: OpenStreetMap contributors, ODbL 1.0 (local data)"

# Kinds worth naming as landmarks: visible in a photograph, and specific
# enough to corroborate a place. The same families the Overpass query
# asked for, matched on either store's kind strings ("amenity=hospital"
# from an import, "hospital=hospital" from the vector tiles).
_LANDMARK_WORDS = (
    "attraction", "museum", "viewpoint", "artwork", "historic", "monument",
    "memorial", "castle", "ruins", "place_of_worship", "university",
    "college", "hospital", "townhall", "fuel", "tower", "lighthouse",
    "water_tower", "windmill", "obelisk", "peak", "volcano", "waterfall",
    "aerodrome", "station", "halt", "stadium", "temple", "church", "mosque",
)


def _is_landmark(kind: str) -> bool:
    k = (kind or "").lower()
    return any(w in k for w in _LANDMARK_WORDS)


def nearby(service, lat: float, lon: float, radius_m: float = 1000.0,
           fetch: bool = True, limit: int = 25) -> Surroundings:
    """Landmarks and street names within ``radius_m`` of a point.

    ``fetch`` allows OpenFreeMap tiles to be fetched for a place with no
    local data; Google Open Buildings is never fetched here, because
    footprints name nothing.
    """
    out = Surroundings(attribution=ATTRIBUTION)
    try:
        bbox = bbox_around(lat, lon, radius_m)
        local = service.store.covering(lat, lon)
        held = service.store.query(bbox, ("poi", "road"))
        has_local = any(src == "osm" for src, _ in held["road"])
        if not has_local and fetch and service.online:
            service.vector_tiles.ensure(bbox)
            held = service.store.query(bbox, ("poi", "road"))
            out.from_cache = False
        else:
            out.from_cache = True
        if not held["poi"] and not held["road"] and not local:
            out.error = "no local map data for this place"
            return out

        features = []
        for _src, f in held["poi"]:
            flon, flat = f[3] / 1e6, f[4] / 1e6
            if not _is_landmark(f[1]):
                continue
            d_km = haversine_km(lat, lon, flat, flon)
            if d_km * 1000.0 > radius_m:
                continue
            features.append(Feature(
                name=f[2], kind=f[1], latitude=flat, longitude=flon,
                distance_m=int(round(d_km * 1000.0)),
                bearing_deg=round(bearing_deg(lat, lon, flat, flon), 1)))
        features.sort(key=lambda feat: feat.distance_m)
        out.features = features[:limit]

        names = []
        for _src, f in held["road"]:
            name = f[2]
            if name and name not in names:
                names.append(name)
        out.road_names = names[:40]
    except Exception as exc:                          # never costs the analysis
        log.warning("local nearby lookup failed: %s", exc)
        out.error = f"local lookup failed ({type(exc).__name__})"
    return out
