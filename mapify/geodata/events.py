"""What is happening, or happened, around a place: earthquakes and natural events.

Two public catalogues, no key, no account:

  * the USGS earthquake catalogue (FDSN event web service) -- a U.S.
    Government work, public domain, credited "U.S. Geological Survey";
  * NASA's Earth Observatory Natural Event Tracker (EONET v3) -- wildfires,
    volcanoes, severe storms, floods, sea and lake ice, dust -- NASA, public
    domain, credited "NASA EONET".

Only a coordinate, a radius and a date range are sent -- never the
photograph. Answers are kept on disk: recent ones for an hour, ones about a
past date for a month.

This is context for the reader: what was shaking or burning near the place
then, or is now. It never moves the estimate.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

__all__ = ["NearbyEvents", "ATTRIBUTION"]

ATTRIBUTION = ("Earthquakes: U.S. Geological Survey (public domain). Natural "
               "events: NASA Earth Observatory Natural Event Tracker, EONET "
               "(public domain).")
_UA = ("Mapify/0.1 (open-source image geolocation; "
       "https://github.com/mdyounus-git/mapify)")
_USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
_EONET = "https://eonet.gsfc.nasa.gov/api/v3/events"
_RECENT_TTL_S = 3600
_PAST_TTL_S = 30 * 86400


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))


class NearbyEvents:
    def __init__(self, cache_dir: Path, online: bool = True):
        self.cache = Path(cache_dir)
        self.online = online
        self._lock = threading.Lock()
        self._last = 0.0

    # ------------------------------------------------------------- plumbing

    def _get(self, url: str, params: dict[str, Any], ttl: float) -> Any:
        """One request at a time, a second apart; answers kept for ``ttl``."""
        full = url + "?" + urllib.parse.urlencode(params)
        path = self.cache / f"{hashlib.sha1(full.encode()).hexdigest()[:24]}.json"
        if path.is_file() and time.time() - path.stat().st_mtime < ttl:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        if not self.online:
            return None
        with self._lock:
            wait = 1.0 - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(full, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = json.load(resp)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                return None
            finally:
                self._last = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")
        return body

    @staticmethod
    def _window(around: datetime | None, days: int) -> tuple[datetime, datetime, bool]:
        """(start, end, is_past): a span centred on a past moment, or the last
        ``days`` days."""
        now = datetime.now(timezone.utc)
        if around is not None:
            when = around if around.tzinfo else around.replace(tzinfo=timezone.utc)
            half = timedelta(days=max(1, days // 2))
            end = min(now, when + half)
            return when - half, end, end < now - timedelta(days=1)
        return now - timedelta(days=days), now, False

    # ------------------------------------------------------------ catalogues

    def earthquakes(self, lat: float, lon: float, radius_km: float = 300.0,
                    days: int = 30, around: datetime | None = None,
                    min_magnitude: float = 2.5, limit: int = 20) -> list[dict[str, Any]]:
        """Earthquakes within the radius and time span, strongest first."""
        start, end, past = self._window(around, days)
        body = self._get(_USGS, {
            "format": "geojson", "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "maxradiuskm": f"{min(20001.6, radius_km):.0f}",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "minmagnitude": f"{min_magnitude:.1f}", "orderby": "magnitude",
            "limit": str(int(limit))}, _PAST_TTL_S if past else _RECENT_TTL_S)
        out = []
        for f in (body or {}).get("features") or []:
            p = f.get("properties") or {}
            co = (f.get("geometry") or {}).get("coordinates") or []
            if len(co) < 2 or p.get("mag") is None or p.get("time") is None:
                continue
            out.append({
                "magnitude": round(float(p["mag"]), 1), "place": p.get("place") or "",
                "time": datetime.fromtimestamp(p["time"] / 1000, timezone.utc).isoformat(),
                "lat": co[1], "lon": co[0], "depth_km": co[2] if len(co) > 2 else None,
                "km": round(_km(lat, lon, co[1], co[0]), 1),
                "url": p.get("url") or "", "source": "USGS"})
        return out

    def natural(self, lat: float, lon: float, radius_km: float = 300.0,
                days: int = 60, around: datetime | None = None) -> list[dict[str, Any]]:
        """Natural events (fires, volcanoes, storms...) within the radius and
        span, nearest first, each at its latest position in the span."""
        start, end, past = self._window(around, days)
        dlat = radius_km / 111.32
        dlon = radius_km / (111.32 * max(0.05, math.cos(math.radians(lat))))
        params = {"bbox": f"{max(-180, lon - dlon):.3f},{min(90, lat + dlat):.3f},"
                          f"{min(180, lon + dlon):.3f},{max(-90, lat - dlat):.3f}",
                  "start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"),
                  "status": "all"}
        body = self._get(_EONET, params, _PAST_TTL_S if past else _RECENT_TTL_S)
        out = []
        for e in (body or {}).get("events") or []:
            best = None
            for g in e.get("geometry") or []:
                co = g.get("coordinates")
                if g.get("type") != "Point" or not co or len(co) < 2:
                    continue
                d = _km(lat, lon, co[1], co[0])
                if d <= radius_km and (best is None or g.get("date", "") > best[1]):
                    best = (d, g.get("date", ""), co)
            if best is None:
                continue
            cats = [c.get("title") for c in e.get("categories") or [] if c.get("title")]
            out.append({
                "title": e.get("title") or "", "category": ", ".join(cats),
                "date": best[1], "lat": best[2][1], "lon": best[2][0],
                "km": round(best[0], 1), "closed": e.get("closed"),
                "url": next((s.get("url") for s in e.get("sources") or [] if s.get("url")),
                            e.get("link") or ""),
                "source": "NASA EONET"})
        return sorted(out, key=lambda r: r["km"])

    def near(self, lat: float, lon: float, around: datetime | None = None) -> dict[str, Any]:
        quakes = self.earthquakes(lat, lon, around=around)
        events = self.natural(lat, lon, around=around)
        return {"available": True, "around": around.isoformat() if around else None,
                "earthquakes": quakes, "events": events, "attribution": ATTRIBUTION}
