"""Techniques 16-42: scene readings, and the sun, season and Moon checks.

The solar checks are tested against constructed situations whose answer
is known in advance: a photograph that reads as night taken at noon in
Chennai, bare trees in Berlin in July, a shoreline 800 km inland. Each
must come back as a contradiction, and its mirror image as consistent --
a check that can only ever say "consistent" is not checking anything.
"""

from __future__ import annotations

import io
import math
from datetime import datetime, timezone

import pytest
from PIL import Image, ImageDraw
from PIL.TiffImagePlugin import IFDRational

from conftest import gps_exif, make_jpeg

import mapify.techniques.phase2_scene      # noqa: F401  registers 16-32
import mapify.techniques.phase3_solar      # noqa: F401  registers 33-42
from mapify.techniques import REGISTRY, Context, run_all


class Reading:
    def __init__(self, key, option, label, confidence, margin=0.3,
                 ranked=None, constrains=False):
        self.group_key = key
        self.option_key = option
        self.option_label = label
        self.confidence = confidence
        self.margin = margin
        self.constrains = constrains
        self.called = constrains and confidence >= 0.34
        self.runners_up = []
        self.ranked = ranked or [(option, confidence)]


class Evidence:
    def __init__(self, *readings):
        self.readings = list(readings)

    def by_group(self, key):
        return next((r for r in self.readings if r.group_key == key), None)


class Cand:
    def __init__(self, lat, lon, probability=0.4):
        self.latitude = lat
        self.longitude = lon
        self.probability = probability
        self.location = {}


def _gps_timed(lat, lon, when: datetime, heading=None, f35=None):
    """A JPEG whose GPS block carries a UTC time (and optionally more)."""
    gps = {7: (IFDRational(when.hour, 1), IFDRational(when.minute, 1),
               IFDRational(when.second, 1)),
           29: when.strftime("%Y:%m:%d")}
    if heading is not None:
        gps.update({16: "T", 17: IFDRational(int(heading * 10), 10)})
    ex = gps_exif(lat, lon, gps=gps)
    ex[0x010F] = "TestMake"
    ex[0x0110] = "TestCam"
    ex[0x0132] = when.strftime("%Y:%m:%d %H:%M:%S")
    if f35:
        ex[0x8769] = {0xA405: f35, 0x9003: when.strftime("%Y:%m:%d %H:%M:%S")}
    return make_jpeg(exif=ex)


def _ctx(raw, candidates, evidence=None, image=None):
    from mapify.exif import read_exif
    img = image or Image.open(io.BytesIO(raw))
    img.load()
    return Context(image=img, raw_bytes=raw, exif=read_exif(img),
                   candidates=candidates, evidence=evidence)


def _one(ctx, tid):
    return next(f for f in run_all(ctx, only=[tid]))


NOON_CHENNAI = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)   # 12:00 IST


# ------------------------------------------------------------ framework

def test_the_registry_is_contiguous_through_42():
    assert set(range(1, 43)) <= set(REGISTRY)


def test_measured_groups_steer_and_new_ones_only_report():
    for tid in (16, 22, 23, 28):
        assert REGISTRY[tid].feeds_fusion, tid
    for tid in (17, 18, 19, 20, 21, 24, 29, 30, 31, 32) + tuple(range(33, 43)):
        assert not REGISTRY[tid].feeds_fusion, tid
        assert REGISTRY[tid].fusion_note


def test_new_descriptive_group_says_it_moves_nothing():
    ev = Evidence(Reading("traffic_lights", "countdown", "Countdown timer signals", 0.6))
    f = _one(_ctx(make_jpeg(), [Cand(13.08, 80.27)], ev), 21)
    assert f.found and "moves no coordinate" in f.summary


# ------------------------------------------------------------ coastline

def test_a_shoreline_far_inland_is_a_contradiction():
    pytest.importorskip("shapefile")
    from mapify.geo.coast import coast_available
    if not coast_available():
        pytest.skip("Natural Earth data not fetched")
    ev = Evidence(Reading("terrain", "coast", "Coastal", 0.8,
                          ranked=[("coast", 0.8), ("flat", 0.1)]))
    inland = _one(_ctx(make_jpeg(), [Cand(28.61, 77.21)], ev), 27)   # Delhi
    assert inland.detail["contradiction"] is True
    coastal = _one(_ctx(make_jpeg(), [Cand(13.05, 80.28)], ev), 27)  # Chennai
    assert coastal.detail["contradiction"] is False


# ----------------------------------------------------------------- sun

def test_sun_position_uses_the_gps_clock():
    from mapify.chrono.solar import sun_position
    raw = _gps_timed(13.08, 80.27, NOON_CHENNAI)
    f = _one(_ctx(raw, [Cand(13.08, 80.27)]), 33)
    expected = sun_position(13.08, 80.27, NOON_CHENNAI).elevation
    assert f.found
    assert f.detail["elevation"] == pytest.approx(expected, abs=0.05)
    assert f.detail["capture_time"]["basis"] == "gps"


def test_night_light_at_noon_contradicts_and_daylight_agrees():
    raw = _gps_timed(13.08, 80.27, NOON_CHENNAI)
    night = Evidence(Reading("lighting", "night", "Night, artificial light", 0.8))
    f = _one(_ctx(raw, [Cand(13.08, 80.27)], night), 38)
    assert f.detail["contradiction"] is True
    day = Evidence(Reading("lighting", "day", "Daylight, high sun", 0.8))
    f = _one(_ctx(raw, [Cand(13.08, 80.27)], day), 38)
    assert f.detail["contradiction"] is False


def test_the_same_light_is_checked_in_the_candidates_time_zone():
    """Night at 00:30 UTC is broad daylight in Chennai (06:00 IST... dawn)."""
    raw = _gps_timed(13.08, 80.27, datetime(2026, 9, 28, 18, 30, tzinfo=timezone.utc))
    night = Evidence(Reading("lighting", "night", "Night, artificial light", 0.8))
    f = _one(_ctx(raw, [Cand(13.08, 80.27)], night), 42)     # 00:00 IST
    assert f.detail["contradiction"] is False


def test_a_missing_capture_time_is_not_applicable_not_guessed():
    f = _one(_ctx(make_jpeg(), [Cand(13.08, 80.27)]), 33)
    assert not f.ran and f.skipped_because


def test_shadows_are_predicted_relative_to_the_camera_heading():
    # Noon in Chennai in late September: the sun is nearly overhead and a
    # little south, so shadows point north. Facing north, they fall away.
    raw = _gps_timed(13.08, 80.27, NOON_CHENNAI, heading=0.0)
    f = _one(_ctx(raw, [Cand(13.08, 80.27)]), 34)
    assert f.found
    assert f.detail["measured_from_pixels"] is False
    assert "away" in f.summary


def test_shadow_length_follows_elevation():
    from mapify.chrono.solar import sun_position
    raw = _gps_timed(13.08, 80.27, NOON_CHENNAI)
    f = _one(_ctx(raw, [Cand(13.08, 80.27)]), 35)
    el = sun_position(13.08, 80.27, NOON_CHENNAI).elevation
    assert f.detail["shadow_to_height"] == pytest.approx(
        1 / math.tan(math.radians(el)), rel=0.01)


# ----------------------------------------------------------- sun in frame

def _sky_with_sun(sun_xy=(0.5, 0.2)) -> Image.Image:
    img = Image.new("RGB", (1200, 800), (110, 160, 215))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 520, 1200, 800], fill=(70, 90, 60))          # ground
    cx, cy = int(sun_xy[0] * 1200), int(sun_xy[1] * 800)
    for r, c in ((60, (200, 215, 235)), (40, (235, 240, 245)), (24, (255, 255, 255))):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    return img


def test_the_suns_disc_is_found_and_a_lamp_on_black_is_not():
    from mapify.chrono.sun_in_frame import find_sun
    disc = find_sun(_sky_with_sun((0.5, 0.2)))
    assert disc is not None
    assert disc.x == pytest.approx(0.5, abs=0.03)
    assert disc.y == pytest.approx(0.2, abs=0.03)
    lamp = Image.new("RGB", (1200, 800), (5, 5, 10))
    ImageDraw.Draw(lamp).ellipse([580, 150, 620, 190], fill=(255, 255, 255))
    assert find_sun(lamp) is None, "no glare around it: not the sun"


def test_elevation_is_measured_from_the_disc_and_the_lens():
    from mapify.chrono.sun_in_frame import find_sun, measured_angles, field_of_view
    img = _sky_with_sun((0.5, 0.2))
    fov = field_of_view({"FocalLengthIn35mmFilm": 26}, img.width, img.height)
    angles = measured_angles(find_sun(img), fov, heading=180.0)
    # 0.3 of the frame above centre on a 26 mm lens: roughly 20 degrees.
    assert 12 < angles["elevation"] < 28
    assert angles["azimuth"] == pytest.approx(180.0, abs=3)


# ------------------------------------------------------------------ moon

def test_the_moon_phase_is_reported_for_the_capture_instant():
    full = datetime(2026, 8, 28, 4, 18, tzinfo=timezone.utc)
    f = _one(_ctx(_gps_timed(13.08, 80.27, full), [Cand(13.08, 80.27)]), 40)
    assert f.detail["phase"] == "full moon"
    assert f.detail["illuminated"] > 0.98
