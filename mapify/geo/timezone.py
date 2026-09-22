"""Time zone for a coordinate.

``timezonefinder`` (MIT) resolves an exact IANA zone offline from the
timezone-boundary-builder polygons, which are derived from OpenStreetMap and
carry the ODbL. When it is not installed we fall back to a longitude estimate
and say so in the result, rather than presenting a guess as a lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:  # pragma: no cover - import guard
    from timezonefinder import TimezoneFinder

    _FINDER: "TimezoneFinder | None" = TimezoneFinder()
    _AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _FINDER = None
    _AVAILABLE = False

ATTRIBUTION = (
    "Time zone boundaries from timezone-boundary-builder, derived from "
    "OpenStreetMap data, ODbL 1.0"
)


@dataclass
class TimezoneInfo:
    iana_name: str | None
    utc_offset_hours: float | None
    abbreviation: str | None
    # "lookup" when it came from the boundary polygons, "estimated" when it is
    # nothing more than longitude / 15.
    method: str
    local_time: str | None = None
    attribution: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return vars(self)


def available() -> bool:
    return _AVAILABLE


def lookup(lat: float, lon: float, at: datetime | None = None) -> TimezoneInfo:
    """Resolve the time zone at a coordinate."""
    when = at or datetime.now(dt_timezone.utc)

    if _AVAILABLE and _FINDER is not None:
        try:
            name = _FINDER.timezone_at(lat=lat, lng=lon)
        except Exception:
            name = None
        if name:
            try:
                zone = ZoneInfo(name)
                local = when.astimezone(zone)
                offset = local.utcoffset()
                return TimezoneInfo(
                    iana_name=name,
                    utc_offset_hours=(
                        round(offset.total_seconds() / 3600.0, 2)
                        if offset is not None
                        else None
                    ),
                    abbreviation=local.tzname(),
                    method="lookup",
                    local_time=local.isoformat(timespec="seconds"),
                    attribution=ATTRIBUTION,
                )
            except (ZoneInfoNotFoundError, ValueError):
                return TimezoneInfo(
                    iana_name=name,
                    utc_offset_hours=None,
                    abbreviation=None,
                    method="lookup",
                    attribution=ATTRIBUTION,
                    note="zone found but the local tz database could not load it",
                )
        # A None result is normal over open ocean.
        return TimezoneInfo(
            iana_name=None,
            utc_offset_hours=round(lon / 15.0, 2),
            abbreviation=None,
            method="estimated",
            note=(
                "No time-zone polygon covers this point (usually open water). "
                "The offset shown is longitude/15, not a real zone."
            ),
        )

    return TimezoneInfo(
        iana_name=None,
        utc_offset_hours=round(lon / 15.0, 2),
        abbreviation=None,
        method="estimated",
        note=(
            "timezonefinder is not installed, so this is longitude/15 rather "
            "than a zone lookup. Install it for the real IANA zone."
        ),
    )
