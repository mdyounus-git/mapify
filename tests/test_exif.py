"""EXIF reading, and the trust signals attached to it.

The point of these is not that we can parse EXIF -- Pillow does that -- but
that a GPS tag is never presented as fact when there is a reason to doubt it.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from conftest import gps_exif, make_jpeg
from mapify.exif import read_exif


def read_bytes(data: bytes):
    with Image.open(io.BytesIO(data)) as img:
        return read_exif(img)


class TestNoExif:
    def test_absent_exif_is_reported_without_alarm(self, plain_jpeg):
        report = read_bytes(plain_jpeg)
        assert report.gps is None
        assert any("strip metadata" in w for w in report.trust_warnings)

    def test_pixel_size_is_always_present(self, plain_jpeg):
        assert read_bytes(plain_jpeg).pixel_size == (320, 240)


class TestGpsParsing:
    @pytest.mark.parametrize(
        "lat, lon",
        [
            (12.9716, 77.5946),     # Bengaluru
            (48.8584, 2.2945),      # Paris
            (-33.8688, 151.2093),   # Sydney, southern hemisphere
            (64.1466, -21.9426),    # Reykjavik, western hemisphere
            (-22.9519, -43.2105),   # Rio, both negative
        ],
    )
    def test_round_trips_a_coordinate(self, lat, lon):
        data = make_jpeg(exif=gps_exif(lat, lon))
        report = read_bytes(data)
        assert report.gps is not None
        # EXIF stores DMS as rationals, so a few metres of rounding is normal.
        assert report.gps.latitude == pytest.approx(lat, abs=1e-4)
        assert report.gps.longitude == pytest.approx(lon, abs=1e-4)

    def test_hemisphere_refs_are_honoured(self):
        data = make_jpeg(exif=gps_exif(-10.0, -20.0))
        gps = read_bytes(data).gps
        assert gps.latitude < 0 and gps.longitude < 0


class TestTrustSignals:
    def test_null_island_is_discarded(self):
        data = make_jpeg(exif=gps_exif(0.0, 0.0))
        report = read_bytes(data)
        assert report.gps is None
        assert any("Null Island" in w for w in report.trust_warnings)

    def test_manual_processing_method_is_flagged(self):
        exif = gps_exif(12.9716, 77.5946, gps={27: "MANUAL"})
        report = read_bytes(make_jpeg(exif=exif))
        assert report.gps is not None
        assert any("MANUALLY" in w for w in report.trust_warnings)

    def test_editor_software_tag_is_flagged(self):
        exif = gps_exif(12.9716, 77.5946)
        exif[0x0131] = "Adobe Photoshop 25.0"     # Software
        exif[0x010F] = "Canon"                    # Make, so the "no camera"
        exif[0x0110] = "EOS R6"                   # warning does not fire too
        report = read_bytes(make_jpeg(exif=exif))
        assert any("re-encoded after capture" in w for w in report.trust_warnings)

    def test_gps_without_any_camera_identity_is_flagged(self):
        report = read_bytes(make_jpeg(exif=gps_exif(12.9716, 77.5946)))
        assert any("no camera make or model" in w for w in report.trust_warnings)

    def test_camera_identity_suppresses_that_warning(self):
        exif = gps_exif(12.9716, 77.5946)
        exif[0x010F] = "Apple"
        exif[0x0110] = "iPhone 15 Pro"
        report = read_bytes(make_jpeg(exif=exif))
        assert report.camera_make == "Apple"
        assert not any("no camera make or model" in w for w in report.trust_warnings)

    def test_rounded_coordinates_are_flagged(self):
        """12.34, 56.78 is what a person types, not what a receiver writes."""
        report = read_bytes(make_jpeg(exif=gps_exif(12.34, 56.78)))
        assert any("rounded to ~100 m" in w for w in report.trust_warnings)

    def test_precise_coordinates_are_not_flagged_as_typed(self):
        report = read_bytes(make_jpeg(exif=gps_exif(12.971598, 77.594627)))
        assert not any("rounded to" in w for w in report.trust_warnings)

    def test_out_of_range_coordinates_are_discarded(self):
        from PIL.TiffImagePlugin import IFDRational

        zero = IFDRational(0, 1)
        exif = gps_exif(12.0, 77.0)
        exif[0x8825] = {
            1: "N", 2: (IFDRational(200, 1), zero, zero),
            3: "E", 4: (IFDRational(77, 1), zero, zero),
        }
        report = read_bytes(make_jpeg(exif=exif))
        assert report.gps is None
        assert any("out of range" in w for w in report.trust_warnings)

    def test_non_wgs84_datum_is_flagged(self):
        exif = gps_exif(12.9716, 77.5946, gps={18: "TOKYO"})
        report = read_bytes(make_jpeg(exif=exif))
        assert any("datum" in w for w in report.trust_warnings)


class TestCameraFields:
    def test_reads_make_model_and_software(self):
        exif = gps_exif(1.0, 1.0)
        exif[0x010F] = "NIKON CORPORATION"
        exif[0x0110] = "NIKON Z 6"
        exif[0x0131] = "Ver.3.50"
        exif[0x0132] = "2026:03:14 09:26:53"
        report = read_bytes(make_jpeg(exif=exif))
        assert report.camera_make == "NIKON CORPORATION"
        assert report.camera_model == "NIKON Z 6"
        assert report.software == "Ver.3.50"
        assert report.captured_at == "2026:03:14 09:26:53"

    def test_serialises_without_losing_the_fix(self):
        report = read_bytes(make_jpeg(exif=gps_exif(35.6762, 139.6503)))
        d = report.as_dict()
        assert d["gps"]["latitude"] == pytest.approx(35.6762, abs=1e-4)
        assert isinstance(d["pixel_size"], list)
