"""EXIF extraction, with an explicit stance on trust.

An embedded GPS tag is a *claim made by whatever wrote the file*. It is
trivially forgeable, is often copied along when an image is re-encoded, and is
frequently wrong on images that have passed through messaging apps. So this
module returns it as its own provenance class -- never merged into the model's
estimate -- together with the tamper signals we can actually check.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from PIL import ExifTags, Image

# Software strings that mean the file was re-encoded after capture, so any
# surviving GPS tag was carried across rather than measured.
_EDITOR_MARKERS = (
    "photoshop", "gimp", "lightroom", "snapseed", "picasa", "paint.net",
    "affinity", "pixelmator", "luminar", "capture one", "darktable",
    "rawtherapee", "canva", "inkscape", "imagemagick", "ffmpeg",
    "midjourney", "stable diffusion", "dall-e", "generated",
)

_NUL = chr(0)


@dataclass
class GpsFix:
    """A position the file claims for itself."""

    latitude: float
    longitude: float
    altitude_m: float | None = None
    timestamp_utc: str | None = None
    # Horizontal positioning error in metres, when the writer recorded one.
    hpositioning_error_m: float | None = None
    # "GPS" / "MANUAL" / "CELLID" ... straight from GPSProcessingMethod.
    processing_method: str | None = None
    datum: str | None = None


@dataclass
class ExifReport:
    has_exif: bool = False
    gps: GpsFix | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    lens: str | None = None
    software: str | None = None
    captured_at: str | None = None
    orientation: int | None = None
    pixel_size: tuple[int, int] | None = None
    # Human-readable reasons to distrust the embedded fix.
    trust_warnings: list[str] = field(default_factory=list)
    # Everything else we could read, for the raw view in the UI.
    other: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.pixel_size:
            d["pixel_size"] = list(self.pixel_size)
        return d


def _ratio(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    try:
        return value.numerator / value.denominator
    except Exception:
        return None


def _dms_to_degrees(dms: Any, ref: Any) -> float | None:
    """Convert EXIF degrees/minutes/seconds to a signed decimal degree."""
    if dms is None:
        return None
    try:
        parts = list(dms)
    except TypeError:
        return None
    if len(parts) != 3:
        return None
    d, m, s = (_ratio(x) for x in parts)
    if d is None or m is None or s is None:
        return None
    deg = d + m / 60.0 + s / 3600.0
    if isinstance(ref, str) and ref.strip().upper()[:1] in {"S", "W"}:
        deg = -deg
    return deg


def _clean(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace").replace(_NUL, "").strip()
        except Exception:
            return repr(value[:32])
    if isinstance(value, str):
        return value.replace(_NUL, "").strip()
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, (tuple, list)):
        return [_clean(v) for v in value]
    return str(value)


def _looks_hand_typed(value: float) -> bool:
    """True when a coordinate has implausibly few significant decimals.

    A real receiver writes many decimal places; 12.34 or 12.340 is what a
    person types.  Three decimals is ~110 m, so anything coarser than that on
    both axes is worth flagging.
    """
    return abs(value * 1000.0 - round(value * 1000.0)) < 1e-6 and abs(
        value * 100.0 - round(value * 100.0)
    ) < 1e-6


def _parse_gps(gps_ifd: dict[int, Any]) -> tuple[GpsFix | None, list[str]]:
    warnings: list[str] = []
    named = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in gps_ifd.items()}

    lat = _dms_to_degrees(named.get("GPSLatitude"), _clean(named.get("GPSLatitudeRef")))
    lon = _dms_to_degrees(
        named.get("GPSLongitude"), _clean(named.get("GPSLongitudeRef"))
    )
    if lat is None or lon is None:
        return None, warnings
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        warnings.append(
            f"Embedded coordinates are out of range ({lat:.5f}, {lon:.5f}); discarded."
        )
        return None, warnings
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        warnings.append(
            "Embedded coordinates are exactly 0,0 (Null Island) -- a common "
            "placeholder written when no fix was available. Discarded."
        )
        return None, warnings

    alt = _ratio(named.get("GPSAltitude"))
    alt_ref = named.get("GPSAltitudeRef")
    if alt is not None and alt_ref in (1, b"\x01", "1"):
        alt = -alt

    ts: str | None = None
    gdate = _clean(named.get("GPSDateStamp"))
    gtime = named.get("GPSTimeStamp")
    if gdate and gtime:
        try:
            h, m, s = ((_ratio(x) or 0.0) for x in list(gtime))
            base = datetime.strptime(str(gdate), "%Y:%m:%d").replace(
                tzinfo=timezone.utc
            )
            ts = (base + timedelta(hours=h, minutes=m, seconds=s)).isoformat()
        except Exception:
            ts = None

    method = _clean(named.get("GPSProcessingMethod"))
    if isinstance(method, str):
        method = method.replace("ASCII", "").strip() or None
    else:
        method = None

    fix = GpsFix(
        latitude=round(lat, 7),
        longitude=round(lon, 7),
        altitude_m=round(alt, 2) if alt is not None else None,
        timestamp_utc=ts,
        hpositioning_error_m=_ratio(named.get("GPSHPositioningError")),
        processing_method=method,
        datum=_clean(named.get("GPSMapDatum")) or None,
    )

    if fix.processing_method and "manual" in fix.processing_method.lower():
        warnings.append(
            "GPSProcessingMethod says the position was entered MANUALLY, not "
            "measured by a receiver."
        )
    if fix.datum and "WGS" not in str(fix.datum).upper():
        warnings.append(
            f"Coordinates use datum {fix.datum!r} rather than WGS-84; they may "
            "be offset by tens to hundreds of metres."
        )
    if _looks_hand_typed(lat) and _looks_hand_typed(lon):
        warnings.append(
            "Coordinates are rounded to ~100 m on both axes, which is typical "
            "of a hand-entered or synthesised tag rather than a receiver fix."
        )
    if fix.hpositioning_error_m and fix.hpositioning_error_m > 500:
        warnings.append(
            "The writer recorded a horizontal error of "
            f"{fix.hpositioning_error_m:.0f} m, so the fix is coarse."
        )
    return fix, warnings


def read_exif(image: Image.Image) -> ExifReport:
    """Read what the file claims about itself."""
    report = ExifReport(pixel_size=(image.width, image.height))
    try:
        exif = image.getexif()
    except Exception:
        return report
    if not exif:
        report.trust_warnings.append(
            "No EXIF block at all. Social platforms and messaging apps strip "
            "metadata on upload, so its absence says nothing about origin."
        )
        return report

    report.has_exif = True
    named = {ExifTags.TAGS.get(k, str(k)): _clean(v) for k, v in exif.items()}
    report.camera_make = named.get("Make") or None
    report.camera_model = named.get("Model") or None
    report.software = named.get("Software") or None
    report.captured_at = named.get("DateTimeOriginal") or named.get("DateTime") or None
    try:
        raw_orient = named.get("Orientation")
        report.orientation = int(raw_orient) if raw_orient is not None else None
    except (TypeError, ValueError):
        report.orientation = None

    try:
        gps_ifd = exif.get_ifd(0x8825) or {}
    except Exception:
        gps_ifd = {}
    if gps_ifd:
        fix, warns = _parse_gps(gps_ifd)
        report.gps = fix
        report.trust_warnings.extend(warns)

    try:
        exif_ifd = exif.get_ifd(0x8769) or {}
    except Exception:
        exif_ifd = {}
    original = digitized = None
    for key, value in exif_ifd.items():
        name = ExifTags.TAGS.get(key, str(key))
        if name in {"LensModel", "LensMake"}:
            report.lens = _clean(value) or report.lens
        elif name == "DateTimeOriginal":
            original = _clean(value) or None
        elif name == "DateTimeDigitized":
            digitized = _clean(value) or None
        elif name in {
            "FNumber", "ExposureTime", "ISOSpeedRatings",
            "FocalLength", "FocalLengthIn35mmFilm",
        }:
            report.other[name] = _clean(value)

    # The capture time is when the shutter fired (DateTimeOriginal), else
    # when the image was digitised. IFD0's DateTime is when the file was
    # last *changed* -- every editor rewrites it -- and it was being taken
    # as the capture time whenever the original sat in the Exif sub-IFD,
    # which is where cameras put it: a photograph taken in February and
    # edited in July was being timed, and its sun and weather checked, in
    # July.
    modified = named.get("DateTime") or None
    report.captured_at = original or digitized or report.captured_at
    if modified and modified != report.captured_at:
        report.other["DateTime"] = modified

    for key in ("Artist", "Copyright", "ImageDescription", "HostComputer"):
        if named.get(key):
            report.other[key] = named[key]

    if report.software:
        low = str(report.software).lower()
        for marker in _EDITOR_MARKERS:
            if marker in low:
                report.trust_warnings.append(
                    f"Software tag {report.software!r} shows the file was "
                    "re-encoded after capture, so any GPS tag survived an edit "
                    "rather than being measured at the scene."
                )
                break

    if report.gps and not report.camera_make and not report.camera_model:
        report.trust_warnings.append(
            "GPS is present but there is no camera make or model -- unusual "
            "for a real capture and typical of an injected tag."
        )

    if report.gps and report.captured_at and report.gps.timestamp_utc:
        try:
            local = datetime.strptime(str(report.captured_at), "%Y:%m:%d %H:%M:%S")
            gps_dt = datetime.fromisoformat(report.gps.timestamp_utc)
            delta = abs(
                (local.replace(tzinfo=timezone.utc) - gps_dt).total_seconds()
            )
            # No real UTC offset exceeds 14 h; allow 10 min of clock drift.
            if delta > 14 * 3600 + 600:
                report.trust_warnings.append(
                    "Capture time and GPS time differ by more than any real "
                    f"time-zone offset ({delta / 3600:.1f} h apart)."
                )
        except Exception:
            pass

    return report


def read_exif_from_path(path: str) -> ExifReport:
    with Image.open(path) as img:
        return read_exif(img)
