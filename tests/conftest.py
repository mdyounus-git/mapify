"""Shared fixtures.

The model layer is behind protocols, so the whole analysis chain can be
exercised with deterministic stand-ins and no model weights. Tests that
genuinely need the bundled Natural Earth data skip cleanly when it has not
been fetched.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NE_DIR = ROOT / "data" / "naturalearth"

requires_geodata = pytest.mark.skipif(
    not (NE_DIR / "ne_10m_admin_0_countries.shp").exists(),
    reason="Natural Earth data not fetched; run scripts/fetch_data.py",
)


@pytest.fixture(scope="session")
def geocoder():
    from mapify.geo.offline import OfflineGeocoder

    if not (NE_DIR / "ne_10m_admin_0_countries.shp").exists():
        pytest.skip("Natural Earth data not fetched")
    return OfflineGeocoder()


@pytest.fixture(scope="session")
def countries():
    from mapify.data.countries import get_table

    return get_table()


def make_jpeg(width: int = 320, height: int = 240, colour=(70, 110, 90),
              exif=None) -> bytes:
    """A real, decodable JPEG, optionally carrying an EXIF block."""
    from PIL import Image

    img = Image.new("RGB", (width, height), colour)
    buf = io.BytesIO()
    if exif is not None:
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def gps_exif(lat: float, lon: float, **extra):
    """Build an EXIF block carrying a GPS fix, the way a camera would."""
    from PIL import Image
    from PIL.TiffImagePlugin import IFDRational

    def dms(value: float):
        """Degrees/minutes/seconds as EXIF rationals, the way a camera writes."""
        value = abs(value)
        d = int(value)
        m_full = (value - d) * 60
        m = int(m_full)
        s = (m_full - m) * 60
        return (
            IFDRational(d, 1),
            IFDRational(m, 1),
            IFDRational(int(round(s * 10000)), 10000),
        )

    exif = Image.Exif()
    gps = {
        1: "N" if lat >= 0 else "S",
        2: dms(lat),
        3: "E" if lon >= 0 else "W",
        4: dms(lon),
    }
    gps.update(extra.pop("gps", {}))
    exif[0x8825] = gps
    for tag, value in extra.items():
        exif[tag] = value
    return exif


@pytest.fixture
def plain_jpeg() -> bytes:
    return make_jpeg()
