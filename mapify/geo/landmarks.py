"""Nearby named features from OpenStreetMap, via Overpass.

Two uses. First, enrichment: telling the analyst what is actually around the
predicted point. Second, and more useful, verification: if OCR read a road
number or a business name off a sign, and Overpass reports that same road
number or name within a kilometre of a candidate, that candidate has
independent corroboration that did not come from the model.

Overpass is donated infrastructure. We send one query per analysis, cache it,
rate-limit it, and give up quietly on failure rather than retrying.

Data is ODbL-licensed and carries the required attribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .cache import LookupCache, RateLimiter
from .geodesy import bearing_deg, compass_point, haversine_km

ATTRIBUTION = "Data (c) OpenStreetMap contributors, ODbL 1.0"

# Tag groups worth asking about, chosen because each is both visible in a
# photograph and useful for narrowing a location.
_SELECTORS = (
    '["tourism"~"attraction|museum|viewpoint|artwork"]',
    '["historic"]',
    '["amenity"~"place_of_worship|university|college|hospital|townhall|fuel"]',
    '["man_made"~"tower|lighthouse|water_tower|windmill|obelisk"]',
    '["natural"~"peak|volcano|waterfall"]',
    '["aeroway"="aerodrome"]',
    '["railway"~"station|halt"]',
    '["leisure"="stadium"]',
)


@dataclass
class Feature:
    name: str
    kind: str
    latitude: float
    longitude: float
    distance_m: int
    bearing_deg: float
    osm_id: str | None = None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def direction(self) -> str:
        return compass_point(self.bearing_deg)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "distance_m": self.distance_m,
            "bearing_deg": self.bearing_deg,
            "direction": self.direction,
            "osm_id": self.osm_id,
            "tags": self.tags,
        }


@dataclass
class Surroundings:
    features: list[Feature] = field(default_factory=list)
    # Road reference numbers ("A4", "NH 44", "I-95") signposted nearby. These
    # are the single most checkable thing OCR can give us.
    road_refs: list[str] = field(default_factory=list)
    road_names: list[str] = field(default_factory=list)
    attribution: str = ATTRIBUTION
    from_cache: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "features": [f.as_dict() for f in self.features],
            "road_refs": self.road_refs,
            "road_names": self.road_names,
            "attribution": self.attribution,
            "from_cache": self.from_cache,
            "error": self.error,
        }


def _kind_of(tags: dict[str, str]) -> str:
    for key in ("tourism", "historic", "amenity", "man_made", "natural",
                "aeroway", "railway", "leisure", "highway"):
        if key in tags:
            return f"{key}={tags[key]}"
    return "feature"


class OverpassClient:
    """One cached, rate-limited Overpass query per analysis."""

    def __init__(
        self,
        endpoint: str,
        user_agent: str,
        cache: LookupCache,
        min_interval_s: float = 2.0,
        timeout_s: float = 25.0,
    ):
        self.endpoint = endpoint
        self.user_agent = user_agent
        self.cache = cache
        self.limiter = RateLimiter(min_interval_s)
        self.timeout_s = timeout_s

    def _build_query(self, lat: float, lon: float, radius_m: int) -> str:
        around = f"around:{radius_m},{lat:.6f},{lon:.6f}"
        parts = []
        for sel in _SELECTORS:
            parts.append(f'node({around}){sel}["name"];')
            parts.append(f'way({around}){sel}["name"];')
        # Road refs come from ways carrying a ref, which is what appears on
        # direction signs.
        parts.append(f'way({around})["highway"]["ref"];')
        parts.append(f'way({around})["highway"~"motorway|trunk|primary"]["name"];')
        body = "\n  ".join(parts)
        timeout = int(self.timeout_s)
        return f"[out:json][timeout:{timeout}];\n(\n  {body}\n);\nout center tags 80;"

    def nearby(self, lat: float, lon: float, radius_m: int = 1200,
               limit: int = 15) -> Surroundings:
        key = LookupCache.key_for(
            "overpass", lat=round(lat, 4), lon=round(lon, 4), r=radius_m
        )
        cached = self.cache.get(key)
        if cached is not None:
            out = self._parse(cached, lat, lon, limit)
            out.from_cache = True
            return out

        query = self._build_query(lat, lon, radius_m)
        self.limiter.acquire()
        try:
            resp = httpx.post(
                self.endpoint,
                content=query.encode("utf-8"),
                headers={
                    "User-Agent": self.user_agent,
                    "Content-Type": "text/plain; charset=utf-8",
                },
                timeout=self.timeout_s + 10.0,
            )
        except httpx.HTTPError as exc:
            return Surroundings(error=f"{type(exc).__name__}: {exc}")
        if resp.status_code == 429:
            return Surroundings(error="Overpass rate limit hit; skipped")
        if resp.status_code != 200:
            return Surroundings(error=f"Overpass HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            return Surroundings(error="Overpass response was not JSON")
        self.cache.put(key, payload)
        return self._parse(payload, lat, lon, limit)

    @staticmethod
    def _parse(payload: dict[str, Any], lat: float, lon: float, limit: int
               ) -> Surroundings:
        out = Surroundings()
        elements = payload.get("elements") if isinstance(payload, dict) else None
        if not isinstance(elements, list):
            return out
        seen_names: set[str] = set()
        refs: dict[str, None] = {}
        road_names: dict[str, None] = {}
        scored: list[tuple[float, Feature]] = []

        for el in elements:
            if not isinstance(el, dict):
                continue
            tags = el.get("tags") or {}
            if not isinstance(tags, dict):
                continue
            if el.get("type") == "node":
                elat, elon = el.get("lat"), el.get("lon")
            else:
                centre = el.get("center") or {}
                elat, elon = centre.get("lat"), centre.get("lon")
            if elat is None or elon is None:
                continue

            if tags.get("highway"):
                ref = tags.get("ref")
                if isinstance(ref, str):
                    for piece in ref.split(";"):
                        piece = piece.strip()
                        if piece:
                            refs.setdefault(piece, None)
                name = tags.get("name")
                if isinstance(name, str) and name.strip():
                    road_names.setdefault(name.strip(), None)
                continue

            name = tags.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            if name in seen_names:
                continue
            seen_names.add(name)
            dist_km = haversine_km(lat, lon, float(elat), float(elon))
            scored.append(
                (
                    dist_km,
                    Feature(
                        name=name,
                        kind=_kind_of(tags),
                        latitude=round(float(elat), 6),
                        longitude=round(float(elon), 6),
                        distance_m=int(round(dist_km * 1000)),
                        bearing_deg=round(
                            bearing_deg(lat, lon, float(elat), float(elon)), 1
                        ),
                        osm_id=f"{el.get('type')}/{el.get('id')}"
                        if el.get("id")
                        else None,
                        tags={
                            k: str(v)
                            for k, v in list(tags.items())[:12]
                            if k
                            in {
                                "name", "name:en", "tourism", "historic",
                                "amenity", "man_made", "natural", "religion",
                                "aeroway", "railway", "leisure", "wikidata",
                                "operator", "ele",
                            }
                        },
                    ),
                )
            )

        scored.sort(key=lambda kv: kv[0])
        out.features = [f for _, f in scored[:limit]]
        out.road_refs = list(refs)[:25]
        out.road_names = list(road_names)[:25]
        return out
