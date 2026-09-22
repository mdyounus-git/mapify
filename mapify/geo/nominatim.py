"""Nominatim reverse geocoding (OpenStreetMap).

Nominatim is run as a free public service by the OpenStreetMap Foundation, and
its usage policy is a condition of access, not a suggestion. This client
enforces the parts that are ours to enforce:

  * an identifying User-Agent on every request;
  * a hard floor of one request per second, shared across threads;
  * an on-disk cache, so repeat analysis of the same image is free;
  * a per-analysis budget, so one upload can never fan out into a crawl;
  * no bulk or systematic querying, and no use as a geocoding backend for
    anything other than the image the user just handed us.

Results are ODbL-licensed data and carry the required attribution.

Policy: https://operations.osmfoundation.org/policies/nominatim/
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from .cache import LookupCache, RateLimiter

ATTRIBUTION = "Data (c) OpenStreetMap contributors, ODbL 1.0"


@dataclass
class ReverseResult:
    display_name: str | None = None
    country: str | None = None
    country_code: str | None = None
    state: str | None = None
    county: str | None = None
    city: str | None = None
    suburb: str | None = None
    road: str | None = None
    postcode: str | None = None
    osm_type: str | None = None
    osm_id: int | None = None
    place_rank: int | None = None
    category: str | None = None
    raw_address: dict[str, str] = field(default_factory=dict)
    attribution: str = ATTRIBUTION
    from_cache: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items()}


class NominatimClient:
    """Rate-limited, cached reverse geocoder."""

    # Keys Nominatim may use for the settlement, most specific first.
    _CITY_KEYS = (
        "city", "town", "village", "municipality", "hamlet", "borough",
        "city_district", "suburb",
    )
    _STATE_KEYS = ("state", "province", "region", "state_district")

    def __init__(
        self,
        endpoint: str,
        user_agent: str,
        cache: LookupCache,
        min_interval_s: float = 1.1,
        timeout_s: float = 20.0,
        max_lookups: int = 6,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.user_agent = user_agent
        self.cache = cache
        self.limiter = RateLimiter(min_interval_s)
        self.timeout_s = timeout_s
        self.max_lookups = max_lookups
        self._spent = 0
        self.last_error: str | None = None

    def reset_budget(self) -> None:
        """Call once per analysis so one image cannot fan out into a crawl."""
        self._spent = 0
        self.last_error = None

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_lookups - self._spent)

    def reverse(self, lat: float, lon: float, zoom: int = 14
                ) -> ReverseResult | None:
        key = LookupCache.key_for(
            "nominatim", lat=round(lat, 5), lon=round(lon, 5), zoom=zoom
        )
        cached = self.cache.get(key)
        if cached is not None:
            result = self._parse(cached)
            if result:
                result.from_cache = True
            return result

        if self._spent >= self.max_lookups:
            self.last_error = (
                f"per-analysis lookup budget of {self.max_lookups} reached"
            )
            return None

        params = {
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
            "format": "jsonv2",
            "zoom": str(zoom),
            "addressdetails": "1",
            # Ask for names in English plus whatever is local, so the report
            # can show both.
            "accept-language": "en",
        }
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        self.limiter.acquire()
        self._spent += 1
        try:
            resp = httpx.get(
                f"{self.endpoint}/reverse",
                params=params,
                headers=headers,
                timeout=self.timeout_s,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        if resp.status_code != 200:
            self.last_error = f"HTTP {resp.status_code}"
            return None
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            self.last_error = "response was not JSON"
            return None
        if not isinstance(payload, dict) or "error" in payload:
            self.last_error = str(payload.get("error", "empty result"))[:200]
            return None
        self.cache.put(key, payload)
        return self._parse(payload)

    @classmethod
    def _parse(cls, payload: dict[str, Any]) -> ReverseResult | None:
        if not isinstance(payload, dict):
            return None
        addr = payload.get("address") or {}
        if not isinstance(addr, dict):
            addr = {}

        def first(keys: tuple[str, ...]) -> str | None:
            for k in keys:
                v = addr.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return None

        cc = addr.get("country_code")
        try:
            osm_id = int(payload["osm_id"]) if payload.get("osm_id") else None
        except (TypeError, ValueError):
            osm_id = None
        try:
            rank = int(payload["place_rank"]) if payload.get("place_rank") else None
        except (TypeError, ValueError):
            rank = None

        return ReverseResult(
            display_name=payload.get("display_name"),
            country=addr.get("country"),
            country_code=cc.upper() if isinstance(cc, str) else None,
            state=first(cls._STATE_KEYS),
            county=addr.get("county"),
            city=first(cls._CITY_KEYS),
            suburb=addr.get("suburb") or addr.get("neighbourhood"),
            road=addr.get("road"),
            postcode=addr.get("postcode"),
            osm_type=payload.get("osm_type"),
            osm_id=osm_id,
            place_rank=rank,
            category=payload.get("category") or payload.get("class"),
            raw_address={k: str(v) for k, v in addr.items()},
        )
