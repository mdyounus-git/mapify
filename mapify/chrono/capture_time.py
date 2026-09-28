"""When was the photograph taken -- as an instant, not a clock reading.

A camera writes ``DateTimeOriginal`` as a wall-clock reading with no
time zone: "2026:09:28 14:05:11" means nothing astronomically until you
know which zone the camera's clock was set to. Three routes to a real
instant, in order of how little they assume:

  gps      the GPS block's date and time are UTC by definition. Exact to
           the second, when present.
  offset   EXIF 2.31 added ``OffsetTimeOriginal`` ("+05:30"). Newer
           phones write it; with it the wall clock becomes an instant.
  zone     otherwise, assume the camera was set to local time at the
           candidate location, and take that location's zone. This is
           an assumption and the result says so -- and it is circular
           when used to test that same location, which the checks that
           use it state.

The compass direction the camera faced, ``GPSImgDirection``, is read
here too: it is what turns "the sun is on the left of the frame" into a
statement about azimuth.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from PIL import Image

__all__ = ["CaptureTime", "resolve", "camera_heading"]

_OFFSET = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


@dataclass
class CaptureTime:
    utc: datetime | None = None
    local_text: str | None = None       # the wall clock as the file has it
    basis: str | None = None            # "gps" | "offset" | "zone" | None
    zone: str | None = None
    uncertainty_min: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.utc is not None

    @property
    def assumed(self) -> bool:
        """Does the instant rest on an assumed time zone?"""
        return self.basis == "zone"

    def as_dict(self) -> dict[str, Any]:
        return {"utc": self.utc.isoformat() if self.utc else None,
                "local_text": self.local_text, "basis": self.basis,
                "zone": self.zone, "uncertainty_min": self.uncertainty_min,
                "assumed_zone": self.assumed, "notes": self.notes}


def _raw_exif(raw: bytes) -> tuple[dict, dict]:
    """(Exif sub-IFD, GPS IFD) straight from the original bytes."""
    try:
        exif = Image.open(io.BytesIO(raw)).getexif()
        return (dict(exif.get_ifd(0x8769) or {}), dict(exif.get_ifd(0x8825) or {}))
    except Exception:
        return {}, {}


def _ratio(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            return v[0] / v[1]
        except Exception:
            return None


def camera_heading(raw: bytes) -> tuple[float | None, str | None]:
    """(degrees clockwise from north, "true"|"magnetic") or (None, None)."""
    _sub, gps = _raw_exif(raw)
    value = _ratio(gps.get(17))                     # GPSImgDirection
    if value is None or not 0.0 <= value <= 360.0:
        return None, None
    ref = str(gps.get(16) or "T").strip().upper()   # GPSImgDirectionRef
    return value % 360.0, ("magnetic" if ref.startswith("M") else "true")


def _parse_local(text: str) -> datetime | None:
    try:
        return datetime.strptime(str(text).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def resolve(raw: bytes, exif: Any, lat: float | None = None,
            lon: float | None = None) -> CaptureTime:
    """Turn what the file says into a UTC instant, with its basis."""
    out = CaptureTime()
    local_text = getattr(exif, "captured_at", None)
    out.local_text = str(local_text) if local_text else None

    gps = getattr(exif, "gps", None)
    gps_ts = getattr(gps, "timestamp_utc", None) if gps else None
    if gps_ts:
        try:
            dt = datetime.fromisoformat(str(gps_ts))
            out.utc = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            out.basis, out.zone, out.uncertainty_min = "gps", "UTC", 1.0
            return out
        except ValueError:
            out.notes.append(f"the GPS timestamp {gps_ts!r} could not be read")

    local = _parse_local(local_text) if local_text else None
    if local is None:
        out.notes.append("the file records no capture time")
        return out

    sub, _gps = _raw_exif(raw)
    offset = sub.get(0x9011) or sub.get(0x9010)     # OffsetTimeOriginal / OffsetTime
    m = _OFFSET.match(str(offset).strip()) if offset else None
    if m:
        sign = 1 if m.group(1) == "+" else -1
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
        out.utc = (local - sign * delta).replace(tzinfo=timezone.utc)
        out.basis, out.zone, out.uncertainty_min = "offset", str(offset), 1.0
        return out

    if lat is None or lon is None:
        out.notes.append("the capture time has no time zone, and there is no "
                         "location to take one from")
        return out
    try:
        from zoneinfo import ZoneInfo
        from ..geo import timezone as tz
        info = tz.lookup(lat, lon)
        if not info.iana_name:
            raise LookupError("no zone")
        zoned = local.replace(tzinfo=ZoneInfo(info.iana_name))
        out.utc = zoned.astimezone(timezone.utc)
        out.basis, out.zone = "zone", info.iana_name
        # A camera clock left on another zone, or never corrected for
        # daylight saving, is common; an hour is the honest error bar.
        out.uncertainty_min = 60.0
        out.notes.append(
            f"assumes the camera clock was set to local time in "
            f"{info.iana_name}; if the location is wrong, so is this")
    except Exception as exc:
        out.notes.append(f"no time zone could be found for the location ({exc})")
    return out
