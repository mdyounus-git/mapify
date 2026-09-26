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
from .geodesy import haversine_km

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


@dataclass
class SearchResult:
    """A place Nominatim found for a string of text read off a sign.

    ``importance`` is Nominatim's own 0-1 estimate of how prominent the place
    is. ``place_rank`` is how specific it is: roughly 30 for a building, 26
    for a street, 16 for a town, 4 for a country. Both matter here, and for
    different reasons -- prominence says how likely this is the place someone
    meant, specificity says how much precision the answer actually carries.
    """

    display_name: str
    latitude: float
    longitude: float
    osm_type: str | None = None
    osm_id: int | None = None
    place_rank: int | None = None
    category: str | None = None
    kind: str | None = None
    importance: float = 0.0
    bounding_box: tuple[float, float, float, float] | None = None
    address: dict[str, str] = field(default_factory=dict)
    query: str = ""
    attribution: str = ATTRIBUTION
    from_cache: bool = False

    # Roughly how large the matched thing is, from its bounding box. A
    # country-sized match dressed up as a precise answer is the failure mode
    # this exists to make visible.
    @property
    def extent_km(self) -> float:
        if not self.bounding_box:
            return 0.0
        south, north, west, east = self.bounding_box
        return max(
            haversine_km(south, west, north, west),
            haversine_km(south, west, south, east),
        )

    @property
    def specificity(self) -> str:
        """What kind of thing was matched, in words a person can read."""
        rank = self.place_rank or 0
        if rank >= 30:
            return "building"
        if rank >= 26:
            return "street"
        if rank >= 19:
            return "neighbourhood"
        if rank >= 13:
            return "settlement"
        if rank >= 8:
            return "region"
        return "country"

    def as_dict(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "osm_type": self.osm_type,
            "osm_id": self.osm_id,
            "place_rank": self.place_rank,
            "category": self.category,
            "kind": self.kind,
            "importance": round(self.importance, 4),
            "specificity": self.specificity,
            "extent_km": round(self.extent_km, 2),
            "query": self.query,
            "attribution": self.attribution,
            "from_cache": self.from_cache,
        }


class NominatimClient:
    """Rate-limited, cached geocoder: coordinates to places, and back."""

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

    def search(self, query: str, limit: int = 3,
               countrycodes: str | None = None) -> list[SearchResult]:
        """Find the places a piece of read text might name.

        This is the other half of the geocoder and the one that makes a
        photographed address usable: Natural Earth knows 7,300 settlements,
        and OSM knows every named building, street and hamlet on earth.

        It is the same service, under the same policy, so it goes through the
        same rate limiter, the same cache and the same per-analysis budget as
        every reverse lookup. The budget is what stops one photograph of a
        noticeboard turning into a dozen queries.
        """
        query = " ".join(query.split())
        if len(query) < 3:
            return []

        key = LookupCache.key_for(
            "nominatim-search", q=query.casefold(), limit=limit,
            cc=countrycodes or "",
        )
        cached = self.cache.get(key)
        if cached is not None:
            out = self._parse_search(cached, query)
            for item in out:
                item.from_cache = True
            return out

        if self._spent >= self.max_lookups:
            self.last_error = (
                f"per-analysis lookup budget of {self.max_lookups} reached"
            )
            return []

        params = {
            "q": query,
            "format": "jsonv2",
            "limit": str(max(1, min(10, limit))),
            "addressdetails": "1",
            "accept-language": "en",
        }
        if countrycodes:
            params["countrycodes"] = countrycodes
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        self.limiter.acquire()
        self._spent += 1
        try:
            resp = httpx.get(
                f"{self.endpoint}/search",
                params=params,
                headers=headers,
                timeout=self.timeout_s,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []
        if resp.status_code != 200:
            self.last_error = f"HTTP {resp.status_code}"
            return []
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            self.last_error = "response was not JSON"
            return []
        if not isinstance(payload, list):
            self.last_error = "search response was not a list"
            return []
        self.cache.put(key, payload)
        return self._parse_search(payload, query)

    def search_structured(self, fields: dict[str, str],
                          limit: int = 3) -> list[SearchResult]:
        """Search field by field rather than as free text.

        Nominatim's free-text search requires every term to match, which
        makes it brittle against a signboard: one slogan or one misread
        word and it returns nothing. The structured form matches each field
        against one kind of thing, so `city=Kelambakkam` finds the town
        whatever else is written on the board.

        It cannot find a building, which is why it is the fallback and not
        the first attempt -- but it reliably answers the question the user
        actually asked, which is roughly where the photograph was taken.
        """
        allowed = ("amenity", "street", "city", "county", "state",
                   "country", "postalcode")
        clean = {
            k: " ".join(str(v).split())
            for k, v in fields.items()
            if k in allowed and str(v).strip()
        }
        if not clean:
            return []

        label = ", ".join(f"{k}={v}" for k, v in sorted(clean.items()))
        key = LookupCache.key_for("nominatim-structured", **clean, limit=limit)
        cached = self.cache.get(key)
        if cached is not None:
            out = self._parse_search(cached, label)
            for item in out:
                item.from_cache = True
            return out

        if self._spent >= self.max_lookups:
            self.last_error = (
                f"per-analysis lookup budget of {self.max_lookups} reached"
            )
            return []

        params = {
            **clean,
            "format": "jsonv2",
            "limit": str(max(1, min(10, limit))),
            "addressdetails": "1",
            "accept-language": "en",
        }
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        self.limiter.acquire()
        self._spent += 1
        try:
            resp = httpx.get(
                f"{self.endpoint}/search",
                params=params,
                headers=headers,
                timeout=self.timeout_s,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []
        if resp.status_code != 200:
            self.last_error = f"HTTP {resp.status_code}"
            return []
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            self.last_error = "response was not JSON"
            return []
        if not isinstance(payload, list):
            self.last_error = "search response was not a list"
            return []
        self.cache.put(key, payload)
        return self._parse_search(payload, label)

    @staticmethod
    def _parse_search(payload: Any, query: str) -> list[SearchResult]:
        out: list[SearchResult] = []
        if not isinstance(payload, list):
            return out
        for row in payload:
            if not isinstance(row, dict):
                continue
            try:
                lat = float(row["lat"])
                lon = float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                continue
            box = None
            raw_box = row.get("boundingbox")
            if isinstance(raw_box, (list, tuple)) and len(raw_box) == 4:
                try:
                    box = (float(raw_box[0]), float(raw_box[1]),
                           float(raw_box[2]), float(raw_box[3]))
                except (TypeError, ValueError):
                    box = None
            address = row.get("address")
            out.append(
                SearchResult(
                    display_name=str(row.get("display_name") or "")[:400],
                    latitude=lat,
                    longitude=lon,
                    osm_type=row.get("osm_type"),
                    osm_id=row.get("osm_id") if isinstance(row.get("osm_id"), int)
                           else None,
                    place_rank=row.get("place_rank")
                               if isinstance(row.get("place_rank"), int) else None,
                    category=row.get("category") or row.get("class"),
                    kind=row.get("type"),
                    importance=float(row.get("importance") or 0.0),
                    bounding_box=box,
                    address=address if isinstance(address, dict) else {},
                    query=query,
                )
            )
        return out

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
