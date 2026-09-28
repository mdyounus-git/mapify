"""What the weather was, at a place and hour in the past.

Open-Meteo's historical API serves the ERA5 reanalysis (Copernicus
Climate Change Service): hourly cloud, rain, snow and weather codes on a
~25 km grid back to 1940, and CAMS air quality from 2013. Free for
non-commercial use, no key, under 10,000 calls a day; data CC BY 4.0,
credited as "Weather data by Open-Meteo.com". A commercial user of
Mapify needs Open-Meteo's paid plan, and ``network.weather_history_enabled``
is the switch.

Only a coordinate and a date are sent -- never the photograph. Each
answer is cached on disk, so re-analysing an image costs nothing, and
requests are spaced a second apart.

What a reanalysis is not: a record of the sky over one street at one
minute. It is a grid-cell average for the hour. A passing shower can
wet one street and not the model, so a disagreement is reported as a
question for the reader, never as a refutation.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["WeatherHistory", "ATTRIBUTION", "describe_code"]

ATTRIBUTION = ("Weather data by Open-Meteo.com (CC BY 4.0), from the Copernicus "
               "Climate Change Service ERA5 reanalysis and CAMS")
_UA = ("Mapify/0.1 (open-source image geolocation; "
       "https://github.com/mdyounus-git/mapify)")
_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
_AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"

_CODES = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
          45: "fog", 48: "rime fog", 51: "light drizzle", 53: "drizzle",
          55: "dense drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
          61: "slight rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
          67: "freezing rain", 71: "slight snow", 73: "snow", 75: "heavy snow",
          77: "snow grains", 80: "rain showers", 81: "rain showers",
          82: "violent rain showers", 85: "snow showers", 86: "snow showers",
          95: "thunderstorm", 96: "thunderstorm with hail",
          99: "thunderstorm with hail"}


def describe_code(code: int | None) -> str:
    return _CODES.get(int(code), f"code {code}") if code is not None else "unknown"


class WeatherHistory:
    def __init__(self, cache_dir: Path, online: bool = True):
        self.online = online
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(cache_dir / "weather.sqlite"),
                                  check_same_thread=False, timeout=30)
        self.db.execute("CREATE TABLE IF NOT EXISTS answers(key TEXT PRIMARY "
                        "KEY, body TEXT, at TEXT)")
        self._lock = threading.Lock()
        self._last = 0.0

    def _get(self, url: str, params: dict[str, Any]) -> dict | None:
        key = url + "?" + urllib.parse.urlencode(sorted(params.items()))
        row = self.db.execute("SELECT body FROM answers WHERE key=?",
                              (key,)).fetchone()
        if row:
            return json.loads(row[0])
        if not self.online:
            return None
        with self._lock:
            wait = 1.0 - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            req = urllib.request.Request(key, headers={"User-Agent": _UA})
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    body = json.load(resp)
            except (urllib.error.URLError, TimeoutError, ValueError):
                return None
        self.db.execute("INSERT OR REPLACE INTO answers VALUES (?,?,?)",
                        (key, json.dumps(body),
                         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        self.db.commit()
        return body

    @staticmethod
    def _at_hour(body: dict | None, when: datetime) -> dict[str, Any] | None:
        if not body or "hourly" not in body:
            return None
        h = body["hourly"]
        stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
        times = h.get("time") or []
        if stamp not in times:
            return None
        i = times.index(stamp)
        return {k: (v[i] if isinstance(v, list) and i < len(v) else None)
                for k, v in h.items() if k != "time"}

    def hour(self, lat: float, lon: float, when: datetime) -> dict[str, Any] | None:
        """ERA5 at the grid cell and UTC hour of ``when``, or None."""
        day = when.astimezone(timezone.utc).strftime("%Y-%m-%d")
        body = self._get(_ARCHIVE, {
            "latitude": round(lat, 2), "longitude": round(lon, 2),
            "start_date": day, "end_date": day, "timezone": "GMT",
            "hourly": "weather_code,cloud_cover,precipitation,snowfall"})
        return self._at_hour(body, when)

    def air(self, lat: float, lon: float, when: datetime) -> dict[str, Any] | None:
        """CAMS air quality at the hour of ``when`` (from 2013), or None."""
        day = when.astimezone(timezone.utc).strftime("%Y-%m-%d")
        body = self._get(_AIR, {
            "latitude": round(lat, 2), "longitude": round(lon, 2),
            "start_date": day, "end_date": day, "timezone": "GMT",
            "hourly": "pm2_5,pm10,dust,aerosol_optical_depth"})
        return self._at_hour(body, when)
