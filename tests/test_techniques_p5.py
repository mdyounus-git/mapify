"""Techniques 57-72: time in the evidence, and the correlation stage.

The checks that can rule something out are tested in both directions:
a photograph that reads as night, timed by its GPS clock at a moment
when it is night in India and day in the Americas, must leave the
Indian candidates standing and flag the American ones.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from conftest import gps_exif, make_jpeg

import mapify.techniques  # noqa: F401  registers everything built so far
from mapify.techniques import REGISTRY, Context, run_all
from mapify.techniques.spec import NAMES, phase_of


class Reading:
    def __init__(self, key, option, label, confidence, margin=0.3,
                 constrains=False, called=None):
        self.group_key = key
        self.option_key = option
        self.option_label = label
        self.confidence = confidence
        self.margin = margin
        self.constrains = constrains
        self.called = constrains if called is None else called
        self.runners_up = []
        self.ranked = [(option, confidence)]


class Evidence:
    def __init__(self, *readings):
        self.readings = list(readings)

    def by_group(self, key):
        return next((r for r in self.readings if r.group_key == key), None)


class Adj:
    def __init__(self, source, delta):
        self.source, self.delta, self.detail = source, delta, ""


class Cand:
    def __init__(self, lat, lon, probability=0.4, iso=None, adjustments=()):
        self.latitude, self.longitude = lat, lon
        self.probability = probability
        self.iso_a2 = iso
        self.adjustments = list(adjustments)
        self.provenance = "model"
        self.location = {}


def _gps_timed(lat, lon, when):
    gps = {7: (IFDRational(when.hour, 1), IFDRational(when.minute, 1),
               IFDRational(when.second, 1)), 29: when.strftime("%Y:%m:%d")}
    ex = gps_exif(lat, lon, gps=gps)
    ex[0x010F], ex[0x0110] = "TestMake", "TestCam"
    ex[0x0132] = when.strftime("%Y:%m:%d %H:%M:%S")
    return make_jpeg(exif=ex)


class Line:
    def __init__(self, text, confidence=0.9, box=None):
        self.text, self.confidence, self.box = text, confidence, box

    def as_dict(self):
        return {"text": self.text}


class Ocr:
    def __init__(self, *texts):
        self.lines = [Line(t) for t in texts]
        self.engine, self.languages, self.error = "fake", ["en"], None


def _ctx(raw, candidates=(), evidence=None, ocr=None, prediction=None):
    from mapify.exif import read_exif
    img = Image.open(io.BytesIO(raw))
    img.load()
    return Context(image=img, raw_bytes=raw, exif=read_exif(img),
                   candidates=list(candidates), evidence=evidence, ocr=ocr,
                   prediction=prediction or {})


def _one(ctx, tid):
    return next(f for f in run_all(ctx, only=[tid]))


# ---------------------------------------------------------- the registry

def test_every_registered_technique_matches_the_specification():
    for tid, t in REGISTRY.items():
        if tid > 153:
            continue                      # test-only registrations
        assert t.name == NAMES[tid], tid
        assert t.phase == phase_of(tid), tid
    assert set(range(1, 73)) <= set(REGISTRY)


# ---------------------------------------------------------------- season

def test_bare_trees_in_a_northern_july_contradict_and_january_agrees():
    ev = Evidence(Reading("season", "bare", "Bare deciduous trees", 0.7))
    july = _gps_timed(52.52, 13.40, datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc))
    f = _one(_ctx(july, [Cand(52.52, 13.40)], ev), 59)
    assert f.detail["contradiction"] is True
    jan = _gps_timed(52.52, 13.40, datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc))
    f = _one(_ctx(jan, [Cand(52.52, 13.40)], ev), 59)
    assert f.detail["contradiction"] is False


def test_the_southern_hemisphere_is_six_months_out():
    ev = Evidence(Reading("season", "bare", "Bare deciduous trees", 0.7))
    july = _gps_timed(-37.81, 144.96, datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc))
    f = _one(_ctx(july, [Cand(-37.81, 144.96)], ev), 59)
    assert f.detail["contradiction"] is False


def test_the_tropics_infer_no_month():
    ev = Evidence(Reading("season", "bare", "Bare deciduous trees", 0.7))
    f = _one(_ctx(make_jpeg(), [Cand(13.08, 80.27)], ev), 59)
    assert "tropics" in f.summary and "contradiction" not in f.detail


# ------------------------------------------------------------ dated text

def test_a_year_in_the_text_bounds_the_date_from_below():
    f = _one(_ctx(make_jpeg(), ocr=Ocr("SALE ENDS 31.12.2024", "Est. 1998")), 60)
    assert f.found and f.detail["not_before"] == 2024
    assert f.detail["founding_years"] == [1998]


def test_a_founding_year_alone_bounds_nothing():
    f = _one(_ctx(make_jpeg(), ocr=Ocr("Sharma Sweets Est. 1962")), 60)
    assert not f.found and "founding" in f.summary


def test_a_file_dated_before_its_own_text_is_a_contradiction():
    raw = _gps_timed(13.08, 80.27, datetime(2021, 5, 1, 6, 0, tzinfo=timezone.utc))
    f = _one(_ctx(raw, [Cand(13.08, 80.27)], ocr=Ocr("Election 2024")), 60)
    assert f.detail["contradiction"] is True


# -------------------------------------------------- the sun, all candidates

def test_night_light_eliminates_candidates_where_the_sun_was_up():
    # 18:30 UTC: midnight in Chennai, early afternoon in New York.
    when = datetime(2026, 9, 28, 18, 30, tzinfo=timezone.utc)
    raw = _gps_timed(13.08, 80.27, when)
    ev = Evidence(Reading("lighting", "night", "Night, artificial light", 0.8))
    cands = [Cand(13.08, 80.27), Cand(40.71, -74.00), Cand(19.07, 72.88)]
    f = _one(_ctx(raw, cands, ev), 62)
    verdict = {r["rank"]: r["consistent"] for r in f.detail["candidates"]}
    assert verdict == {1: True, 2: False, 3: True}
    assert "#2" in f.summary


# ------------------------------------------------------------ correlation

def test_constraints_intersect_to_countries_and_place_the_leader():
    import mapify.evidence.prompts as P
    left = next((o for o in P.GROUPS_BY_KEY["traffic_side"].options
                 if o.drive_side == "left"), None)
    if left is None:
        pytest.skip("traffic group has no left-driving option")
    ev = Evidence(Reading("traffic_side", left.key, left.label, 0.8,
                          constrains=True, called=True))
    f = _one(_ctx(make_jpeg(), [Cand(13.08, 80.27, iso="IN")], ev), 66)
    assert f.found and "IN" in f.detail["countries"]
    assert f.detail["leader_inside"] is True
    f = _one(_ctx(make_jpeg(), [Cand(48.85, 2.35, iso="FR")], ev), 66)
    assert f.detail["leader_inside"] is False


def test_the_final_coordinate_is_the_reported_one():
    pred = {"locatable": True, "determinate": True, "confidence": "medium",
            "uncertainty_km": 12.0, "precision_km": 0.02,
            "estimated": {"latitude": 12.78, "longitude": 80.22,
                          "provenance": "text"}}
    f = _one(_ctx(make_jpeg(), prediction=pred), 67)
    assert f.found and "12.78000, 80.22000" in f.summary


def test_a_refused_image_has_no_final_coordinate():
    f = _one(_ctx(make_jpeg(), prediction={"locatable": False}), 67)
    assert not f.ran


def test_the_time_window_from_light_alone_is_wide_and_says_so():
    ev = Evidence(Reading("lighting", "golden", "Low sun, golden light", 0.7))
    f = _one(_ctx(make_jpeg(), evidence=ev), 68)
    assert f.found and "sunrise or sunset" in f.summary
    assert f.confidence < 0.5


def test_the_evidence_chain_names_what_steered():
    ctx = _ctx(make_jpeg(), [Cand(13.08, 80.27, adjustments=[Adj("toponym", 0.8)])],
               ocr=Ocr("Election 2024"))
    findings = {f.technique: f for f in run_all(ctx, phases=[1, 5])}
    assert findings[72].found
    assert isinstance(findings[72].detail["skipped"], list)
    assert findings[64].detail["by_source"] == {"toponym": 0.8}
