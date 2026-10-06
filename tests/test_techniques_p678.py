"""Techniques 73-131: pixels, astronomy and street data, regional clues.

Most of what is tested here is the line between a clue and an invention.
Every guard below exists because a real photograph tripped it: London
Bridge at night came back as a mosque and as "bare winter trees", the
Gateway of India as a mosque, a "Metro" sign as a shoe shop, a fire
station as public transport, Shibuya's horizon as 139% above the frame.
The text matchers are tested in both directions -- what they must read,
and the slogan, price or abbreviation they must not.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from conftest import gps_exif, make_jpeg

import mapify.techniques  # noqa: F401  registers everything built so far
from mapify.techniques import REGISTRY, Context, Finding, run_all
from mapify.techniques import common, phase5_temporal, phase6_pixels, phase8_regional
from mapify.techniques.spec import NAMES, phase_of


# ------------------------------------------------------------------ fakes

class Reading:
    def __init__(self, key, option, label, confidence, margin=0.3, title=None):
        self.group_key, self.option_key, self.option_label = key, option, label
        self.group_title = title or key
        self.confidence, self.margin = confidence, margin
        self.constrains = self.called = False
        self.runners_up, self.ranked = [], [(option, confidence)]


class Evidence:
    def __init__(self, *readings):
        self.readings = list(readings)

    def by_group(self, key):
        return next((r for r in self.readings if r.group_key == key), None)


class Cand:
    def __init__(self, lat, lon, iso=None):
        self.latitude, self.longitude, self.iso_a2 = lat, lon, iso
        self.probability, self.adjustments, self.provenance = 0.5, [], "model"


class Line:
    def __init__(self, text, confidence=0.9, box=None):
        self.text, self.confidence, self.box = text, confidence, box

    def as_dict(self):
        return {"text": self.text}


class Ocr:
    def __init__(self, *lines):
        self.lines = [l if isinstance(l, Line) else Line(l) for l in lines]


E0 = np.array([1, 0, 0, 0], dtype=np.float32)


class FakeClip:
    """Text prompts containing ``winner`` align with every image; the rest
    are orthogonal. Enough to decide any zero-shot question either way."""

    def __init__(self, winner: str):
        self.winner = winner

    def encode_texts_cached(self, texts, tag):
        out = np.zeros((len(texts), 4), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i, 0 if self.winner in t else 1] = 1.0
        return out

    def encode_image(self, image):
        return E0


def _ctx(raw=None, *, candidates=(), evidence=None, ocr=None, engine=None,
         embedding=None, online=False):
    from mapify.exif import read_exif
    raw = raw or make_jpeg()
    img = Image.open(io.BytesIO(raw))
    img.load()
    return Context(image=img, raw_bytes=raw, exif=read_exif(img),
                   candidates=list(candidates), evidence=evidence, ocr=ocr,
                   engine=engine, embedding=embedding, online=online)


def _one(ctx, tid):
    return next(iter(run_all(ctx, only=[tid])))


def _text(tid, *lines, candidates=()):
    return _one(_ctx(ocr=Ocr(*lines), candidates=candidates), tid)


# ------------------------------------------------------------- registry

def test_techniques_73_to_131_are_registered_as_specified():
    for tid in range(73, 132):
        assert tid in REGISTRY, tid
        assert REGISTRY[tid].name == NAMES[tid], tid
        assert REGISTRY[tid].phase == phase_of(tid), tid


def test_no_technique_in_73_to_131_raises_on_a_bare_image():
    findings = run_all(_ctx(make_jpeg(800, 600)), phases=[6, 7, 8])
    assert len(findings) == 131 - 72
    assert not [f.technique for f in findings
                if f.skipped_because == "the technique raised"]


# ------------------------------------------------- absent is not a finding

def test_an_absent_reading_is_reported_but_is_not_a_finding():
    ev = Evidence(Reading("traffic_side", "indeterminate", "No usable traffic cue", 0.97),
                  Reading("sign_style", "none", "No road signs visible", 0.99))
    for tid in (112, 10):                       # phase 8 and phase 1 alike
        f = _one(_ctx(evidence=ev), tid)
        assert f.ran and not f.found
        assert "Nothing of this kind" in f.summary


def test_a_present_reading_is_still_a_finding():
    ev = Evidence(Reading("traffic_side", "left", "Left-hand traffic", 0.8))
    f = _one(_ctx(evidence=ev), 112)
    assert f.found and "Left-hand" in f.summary


# ------------------------------------------------------------ phase 6

def test_the_detail_window_lands_on_the_detailed_quarter():
    img = Image.new("L", (400, 300), 128)
    noise = (np.random.default_rng(1).random((150, 200)) * 255).astype("uint8")
    img.paste(Image.fromarray(noise), (200, 150))
    assert common.detail_window(img.convert("RGB")) == (0.5, 0.5, 1.0, 1.0)


def test_a_small_frame_gets_no_second_look():
    f = _one(_ctx(make_jpeg(320, 240), embedding=E0,
                  engine=SimpleNamespace(clip=FakeClip("x"))), 73)
    assert not f.found and "small" in f.summary


def test_the_region_of_interest_reports_only_what_the_whole_view_missed():
    whole = Evidence(Reading("setting", "suburb", "Suburban", 0.6, title="Settlement type"))
    close = Evidence(Reading("setting", "cbd", "High-rise city centre", 0.7,
                             title="Settlement type"),
                     Reading("snow_cover", "none", "No snow", 0.9, title="Snow cover"))
    engine = SimpleNamespace(clip=FakeClip("x"),
                             evidence_extractor=SimpleNamespace(score_embedding=lambda e: close))
    f = _one(_ctx(make_jpeg(1200, 900), evidence=whole, embedding=E0, engine=engine), 73)
    assert f.found and "High-rise city centre" in f.summary
    assert "No snow" not in f.summary            # an absence is not news
    assert len(f.regions) == 1


def test_colour_names_are_reported_once_each():
    img = Image.new("RGB", (200, 100), (10, 10, 10))
    img.paste((20, 20, 20), (100, 0, 200, 100))  # two clusters, both "black"
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    f = _one(_ctx(buf.getvalue()), 74)
    names = [n for n, _s, _rgb in f.detail["palette"]]
    assert len(names) == len(set(names))


def test_a_dark_frame_with_no_sign_of_text_is_not_re_read():
    f = _one(_ctx(make_jpeg(640, 480, colour=(20, 20, 20)), ocr=Ocr()), 77)
    assert not f.found and "not re-read" in f.summary


def test_unreadable_fragments_are_re_read_in_a_crop_and_mapped_back():
    seen = {}

    class FakeOcr:
        def read(self, image):
            seen["size"] = image.size
            return SimpleNamespace(lines=[Line("CHAI 20", 0.8, box=(0.0, 0.0, 1.0, 1.0))])

    ocr = Ocr(Line("Bi", 0.1, box=(0.4, 0.4, 0.5, 0.45)))
    f = _one(_ctx(make_jpeg(640, 480, colour=(20, 20, 20)), ocr=ocr,
                  engine=SimpleNamespace(ocr=FakeOcr())), 77)
    assert f.found and "CHAI 20" in f.summary
    x0, y0, x1, y1 = f.detail["reread_box"]
    assert (x0, y0) > (0.0, 0.0) and (x1, y1) < (1.0, 1.0)   # a crop, not the frame
    r = f.regions[0]
    assert (round(r.x0, 3), round(r.y0, 3), round(r.x1, 3), round(r.y1, 3)) == \
        (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3))


@pytest.mark.parametrize("geometry, words", [
    (SimpleNamespace(horizon_y=-1.39, focal_px=850.0, pitch_deg=None, focal_assumed=True),
     "outside the frame"),
    (SimpleNamespace(horizon_y=0.40, focal_px=850.0, pitch_deg=16.0, focal_assumed=True),
     "disagree"),
    (SimpleNamespace(horizon_y=0.15, focal_px=400.0, pitch_deg=None, focal_assumed=True),
     "tilted"),
])
def test_single_view_metrology_refuses_an_unusable_horizon(monkeypatch, geometry, words):
    monkeypatch.setattr(phase6_pixels, "_horizon", lambda ctx: geometry)
    for tid in (85, 86):
        f = _one(_ctx(make_jpeg(1000, 750)), tid)
        assert not f.found and words in f.summary, (tid, f.summary)


def test_a_level_horizon_gives_a_distance(monkeypatch):
    g = SimpleNamespace(horizon_y=0.5, focal_px=850.0, pitch_deg=0.0, focal_assumed=True)
    monkeypatch.setattr(phase6_pixels, "_horizon", lambda ctx: g)
    f = _one(_ctx(make_jpeg(1000, 750)), 85)
    # Bottom row 375 px below a level horizon with f = 850 px: 1.6 / tan(23.8 deg).
    assert f.found and f.detail["bottom_distance_m"] == pytest.approx(3.6, abs=0.1)


@pytest.mark.parametrize("winner, found", [("ordinary city street", False),
                                           ("wet road at night", True)])
def test_reflections_must_beat_ordinary_scenes(winner, found):
    f = _one(_ctx(engine=SimpleNamespace(clip=FakeClip(winner)), embedding=E0), 79)
    assert f.found is found


# ------------------------------------------------------------ phase 7

def test_transit_is_matched_by_tag_not_by_the_word_station():
    from mapify.techniques.phase7_geo import _is_transit
    assert not any(map(_is_transit, ["amenity=fire_station", "amenity=charging_station",
                                     "shop=stationery"]))
    assert all(map(_is_transit, ["railway=station", "bus=bus_stop",
                                 "amenity=bus_station", "railway=halt"]))


def test_an_indoor_reading_gives_no_density_verdict(monkeypatch):
    from mapify.geodata import service
    store = SimpleNamespace(query=lambda bbox, layers: {"building": [("osm", [1])] * 40})
    monkeypatch.setattr(service, "get_service", lambda *a, **k: SimpleNamespace(store=store))
    ev = Evidence(Reading("setting", "interior", "Indoors", 0.99))
    f = _one(_ctx(evidence=ev, candidates=[Cand(18.92, 72.83, "IN")]), 101)
    assert not f.found and "consistent" not in f.summary


def test_no_road_means_no_road_marking_or_kerb():
    ev = Evidence(Reading("road_markings", "none", "No road visible", 0.9),
                  Reading("pavement", "none", "No pavement visible", 0.9))
    for tid in (105, 106):
        f = _one(_ctx(evidence=ev, candidates=[Cand(13.08, 80.27, "IN")]), tid)
        assert not f.found, tid


# --------------------------------------------------- phase 8: the text

def test_a_country_calling_code_is_read():
    f = _text(119, "Call +44 20 7946 0958")
    assert f.found and f.detail["countries"] == ["GB"] and f.detail["narrow"]


def test_a_price_is_not_a_calling_code():
    assert not _text(119, "Total 1.00 44").found


def test_an_indian_mobile_number_as_indians_write_it():
    f = _text(119, "Contact 98450 12345")
    assert f.found and f.detail["countries"] == ["IN"]


def test_a_trunk_code_alone_is_weak():
    f = _text(119, "Ph: 040 1234567")          # Hyderabad, or Hamburg
    assert f.found and not f.detail["narrow"] and f.confidence < 0.2
    assert "weak" in f.summary


@pytest.mark.parametrize("text, iso", [("www.example.co.in", "IN"),
                                       ("Mail info@laden.de", "DE"),
                                       ("Visit shop.example.uk", "GB")])
def test_country_code_domains_are_read(text, iso):
    f = _text(120, text)
    assert f.found and iso in f.detail["countries"]


@pytest.mark.parametrize("text", ["Dr.In clinic", "St.Mary", "example.com",
                                  "Built with app.io"])
def test_abbreviations_and_generic_domains_are_not_countries(text):
    assert not _text(120, text).found


@pytest.mark.parametrize("text", ["TRY OUR NEW MENU", "PHP developers wanted",
                                  "Hong Kong SAR"])
def test_a_word_is_not_a_currency(text):
    assert not _text(121, text).found


def test_the_rupee_sign_is_india_and_rs_is_shared():
    rupee = _text(121, "Chai ₹ 20")
    assert rupee.detail["countries"] == ["IN"] and rupee.detail["narrow"]
    rs = _text(121, "Tea Rs. 20")
    assert rs.found and not rs.detail["narrow"] and "PK" in rs.detail["countries"]


def test_us_dollars_are_not_singapore_dollars():
    # Had "S$" matched inside "US$", Singapore alone would be a narrow hit.
    f = _text(121, "Entry US$ 5")
    assert f.found and not f.detail["narrow"] and "US" in f.detail["countries"]


@pytest.mark.parametrize("text, style", [("Exp 25/12/2023", "day-first"),
                                         ("Exp 12/25/2023", "month-first"),
                                         ("Made 2023-12-25", "year-first")])
def test_date_formats(text, style):
    f = _text(122, text)
    assert f.found and style in f.summary


@pytest.mark.parametrize("text", ["192.168.1.1", "version 1.2.3", "10/11/12"])
def test_versions_addresses_and_ambiguous_dates_are_not_formats(text):
    assert not _text(122, text).found


def test_miles_need_a_number():
    assert not _text(123, "Miles Davis Quintet").found
    f = _text(123, "SPEED LIMIT 30 mph")
    assert f.found and f.detail["narrow"]
    assert {"US", "GB"} <= set(f.detail["countries"])
    assert f.summary.index("United States") < f.summary.index("Liberia")  # majors first


def test_kilometres_narrow_little():
    f = _text(123, "Chennai 42 km")
    assert f.found and f.confidence <= 0.1


@pytest.mark.parametrize("text, iso", [("〒100-0001 Tokyo", "JP"),
                                       ("Bengaluru - 560001", "IN"),
                                       ("London SW1A 1AA", "GB")])
def test_postcodes_in_their_context(text, iso):
    f = _text(124, text)
    assert f.found and iso in f.detail["countries"]


@pytest.mark.parametrize("text", ["Call 555-1234", "560001", "1030 AM"])
def test_bare_numbers_are_not_postcodes(text):
    assert not _text(124, text).found


def test_a_narrow_clue_against_the_leader_is_a_contradiction():
    away = _text(121, "Chai ₹ 20", candidates=[Cand(51.5, -0.1, "GB")])
    assert away.detail["contradiction"] is True
    home = _text(121, "Chai ₹ 20", candidates=[Cand(13.0, 80.2, "IN")])
    assert home.detail["contradiction"] is False


def test_a_weak_clue_never_contradicts():
    f = _text(121, "Coffee $5", candidates=[Cand(13.0, 80.2, "IN")])
    assert f.detail["leader_inside"] is False and f.detail["contradiction"] is False


def test_every_text_clue_marks_where_it_was_read():
    f = _text(119, Line("Call +44 20 7946 0958", box=(0.1, 0.2, 0.4, 0.3)))
    assert f.regions and f.regions[0].x0 == pytest.approx(0.1)


def test_an_indian_plate_names_its_state():
    ctx = _ctx(ocr=Ocr("KA 01 AB 1234"))
    ctx.memo["findings"] = {11: Finding(
        technique=11, key="plates", name="x", phase=1, found=True,
        detail={"candidates": [{"text": "KA 01 AB 1234",
                                "schemes": [{"countries": ["IN"]}]}]})}
    f = _one(ctx, 111)
    assert f.found and "Karnataka" in f.summary


@pytest.mark.parametrize("winner, found", [("a city at night", False),
                                           ("pointed arches", True)])
def test_cultural_features_are_resemblances_that_must_beat_ordinary_scenes(winner, found):
    f = _one(_ctx(engine=SimpleNamespace(clip=FakeClip(winner)), embedding=E0), 116)
    assert f.found is found
    if found:
        assert "resembles" in f.summary and "not an identification" in f.summary
        assert "person" in f.summary


def test_a_one_word_sign_does_not_match_a_one_word_shop(monkeypatch):
    monkeypatch.setattr(phase8_regional, "_names_near",
                        lambda lat, lon, r: [("Metro", "shop=shoes")])
    f = _text(125, "Namma Metro", candidates=[Cand(12.97, 77.59, "IN")])
    assert not f.found


def test_a_distinctive_name_in_the_text_matches(monkeypatch):
    monkeypatch.setattr(phase8_regional, "_names_near",
                        lambda lat, lon, r: [("Phoenix Marketcity", "shop=mall")])
    f = _text(125, "PHOENIX MARKETCITY", candidates=[Cand(12.99, 77.69, "IN")])
    assert f.found and "Phoenix Marketcity" in f.summary


def _with_elevations(monkeypatch, values):
    svc = SimpleNamespace(elevations=lambda pts: list(values))
    monkeypatch.setattr(phase5_temporal, "_weather", lambda ctx: svc)


def test_lying_snow_low_in_the_tropics_is_implausible(monkeypatch):
    _with_elevations(monkeypatch, [900.0] * 25)
    ev = Evidence(Reading("snow_cover", "snow", "Snow on the ground", 0.9))
    f = _one(_ctx(evidence=ev, candidates=[Cand(12.97, 77.59, "IN")]), 127)
    assert f.detail["contradiction"] is True
    f = _one(_ctx(evidence=ev, candidates=[Cand(46.0, 7.7, "CH")]), 127)
    assert "contradiction" not in f.detail


def test_mountains_need_relief(monkeypatch):
    _with_elevations(monkeypatch, [10.0 + i for i in range(25)])
    ev = Evidence(Reading("terrain", "mountains", "High mountains", 0.8))
    f = _one(_ctx(evidence=ev, candidates=[Cand(13.08, 80.27, "IN")]), 128)
    assert f.detail["contradiction"] is True and "disagree" in f.summary


def test_no_elevation_data_is_said_plainly(monkeypatch):
    monkeypatch.setattr(phase5_temporal, "_weather", lambda ctx: None)
    ev = Evidence(Reading("terrain", "flat", "Flat plain", 0.8))
    f = _one(_ctx(evidence=ev, candidates=[Cand(13.08, 80.27, "IN")]), 127)
    assert not f.ran and "no elevation data" in f.summary


def test_road_bearing_from_the_compass_heading(monkeypatch):
    from mapify.geodata import service
    from mapify.geodata.store import encode_ring
    lat, lon = 12.9716, 77.5946
    road = encode_ring([(lon, lat - 0.001), (lon, lat + 0.001)])     # runs north-south
    store = SimpleNamespace(query=lambda bbox, layers: {
        "road": [("osm", [7, "primary", "MG Road", 2, road])]})
    monkeypatch.setattr(service, "get_service", lambda *a, **k: SimpleNamespace(store=store))
    for heading, relation in ((2.0, "along"), (90.0, "across")):
        ex = gps_exif(lat, lon, gps={16: "T", 17: IFDRational(int(heading * 100), 100)})
        f = _one(_ctx(make_jpeg(exif=ex)), 129)
        assert f.found and relation in f.summary and "MG Road" in f.summary


def test_viewpoint_matching_says_why_it_cannot_run():
    for tid in (130, 131):
        f = _one(_ctx(), tid)
        assert not f.ran and f.skipped_because
