"""Weighted points on the globe, grouped into places.

Shared by the locators that answer with many points rather than a ranked
list -- the street-photo memory's neighbours, PLONK's samples. Places are
taken greedily by the weight within ``radius_km`` of a point, each reported
at the weighted centre of its points, on the sphere.
"""

from __future__ import annotations

import math

import numpy as np

from .base import RawCandidate

__all__ = ["places", "km_between"]


def km_between(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def places(lat, lon, weight, source: str, radius_km: float = 25.0,
           top: int = 5) -> list[RawCandidate]:
    """At most ``top`` places, each scored by its share of the total weight."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    w = np.asarray(weight, float)
    if not len(w) or w.sum() <= 0:
        return []
    w = w / w.sum()
    left = np.ones(len(w), dtype=bool)
    out: list[RawCandidate] = []
    while left.any() and len(out) < top:
        best_mask, best_mass = None, 0.0
        for i in np.flatnonzero(left):
            mask = left & (km_between(lat[i], lon[i], lat, lon) <= radius_km)
            mass = float(w[mask].sum())
            if mass > best_mass:
                best_mask, best_mass = mask, mass
        if best_mask is None:
            break
        ww = w[best_mask]
        rl, rn = np.radians(lat[best_mask]), np.radians(lon[best_mask])
        x = float((ww * np.cos(rl) * np.cos(rn)).sum())
        y = float((ww * np.cos(rl) * np.sin(rn)).sum())
        z = float((ww * np.sin(rl)).sum())
        out.append(RawCandidate(latitude=math.degrees(math.atan2(z, math.hypot(x, y))),
                                longitude=math.degrees(math.atan2(y, x)),
                                score=best_mass, source=source))
        left &= ~best_mask
    return out
