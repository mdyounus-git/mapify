"""Spherical geometry on the WGS-84 mean sphere.

Distances here are used for clustering candidates and for reporting an
uncertainty radius, where sub-metre accuracy is irrelevant; the mean-sphere
approximation is good to ~0.3%, far below the error of any prediction we make.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, in degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def compass_point(bearing: float) -> str:
    names = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return names[int((bearing % 360.0) / 22.5 + 0.5) % 16]


def destination(lat: float, lon: float, bearing: float, distance_km: float
                ) -> tuple[float, float]:
    """The point ``distance_km`` away from (lat, lon) along ``bearing``."""
    d = distance_km / EARTH_RADIUS_KM
    brg = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(
        math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(brg)
    )
    l2 = l1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(p1),
        math.cos(d) - math.sin(p1) * math.sin(p2),
    )
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def weighted_centroid(points: list[tuple[float, float, float]]
                      ) -> tuple[float, float]:
    """Weighted mean direction of (lat, lon, weight) triples.

    Averaging degrees directly breaks across the antimeridian and near the
    poles, so we average unit vectors instead.
    """
    if not points:
        raise ValueError("no points")
    x = y = z = 0.0
    total = 0.0
    for lat, lon, w in points:
        if w <= 0:
            continue
        p, l = math.radians(lat), math.radians(lon)
        x += w * math.cos(p) * math.cos(l)
        y += w * math.cos(p) * math.sin(l)
        z += w * math.sin(p)
        total += w
    if total == 0.0:
        return points[0][0], points[0][1]
    x, y, z = x / total, y / total, z / total
    hyp = math.hypot(x, y)
    if hyp < 1e-12 and abs(z) < 1e-12:
        # Vectors cancelled out: the points are spread over the whole globe.
        return points[0][0], points[0][1]
    return math.degrees(math.atan2(z, hyp)), math.degrees(math.atan2(y, x))


def circle_polygon(lat: float, lon: float, radius_km: float, segments: int = 72
                   ) -> list[list[float]]:
    """A closed GeoJSON-order ring approximating a circle, for the globe."""
    ring = []
    for i in range(segments + 1):
        plat, plon = destination(lat, lon, 360.0 * i / segments, radius_km)
        ring.append([round(plon, 6), round(plat, 6)])
    return ring


def containment_radius_km(points: list[tuple[float, float, float]],
                          centre: tuple[float, float],
                          mass: float = 0.68) -> float:
    """Radius around ``centre`` holding ``mass`` of the total weight.

    This is the number we report as uncertainty: not a guess at the model's
    internal variance, but the observed spread of the candidates it produced.
    """
    if not points:
        return 0.0
    total = sum(max(0.0, w) for _, _, w in points)
    if total <= 0:
        return 0.0
    ranked = sorted(
        ((haversine_km(centre[0], centre[1], lat, lon), w)
         for lat, lon, w in points),
        key=lambda kv: kv[0],
    )
    acc = 0.0
    for dist, w in ranked:
        acc += max(0.0, w)
        if acc / total >= mass:
            return dist
    return ranked[-1][0]
