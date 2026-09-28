"""The technique framework, and the first fifteen techniques.

Two things are being checked here, and the second matters more than the
first. One: the machinery works -- techniques register, unmet needs skip
with a reason, a technique that explodes does not take the run with it.
Two: the *distinction the framework exists for* holds. Techniques that
were measured out of the fusion must report and not steer, and the test
suite is where that stops being a comment and becomes enforceable.
"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from conftest import gps_exif, make_jpeg

from mapify.techniques import REGISTRY, Context, Finding, by_phase, run_all
from mapify.techniques.base import Constraint, Region, Technique, technique


# --------------------------------------------------------------- helpers

class FakeLine:
    def __init__(self, text, confidence=0.9, box=None):
        self.text = text
        self.confidence = confidence
        self.box = box

    def as_dict(self):
        return {"text": self.text, "confidence": self.confidence,
                "box": list(self.box) if self.box else None}


class FakeOcr:
    def __init__(self, lines, engine="fake", languages=("en",)):
        self.lines = lines
        self.engine = engine
        self.languages = list(languages)
        self.error = None

    @property
    def text(self):
        return "\n".join(l.text for l in self.lines)


class FakeReading:
    def __init__(self, group_key, option_key, label, confidence, margin,
                 constrains=False, ranked=None, runners_up=()):
        self.group_key = group_key
        self.group_title = group_key
        self.option_key = option_key
        self.option_label = label
        self.confidence = confidence
        self.margin = margin
        self.called = constrains and confidence >= 0.34 and margin >= 0.08
        self.constrains = constrains
        self.runners_up = list(runners_up)
        self.ranked = ranked or [(option_key, confidence)]


class FakeEvidence:
    def __init__(self, readings):
        self.readings = readings
        self.country_prior = {}
        self.model = "fake"
        self.error = None

    def by_group(self, key):
        for r in self.readings:
            if r.group_key == key:
                return r
        return None


def image_ctx(**kw) -> Context:
    raw = kw.pop("raw", None) or make_jpeg()
    img = Image.open(io.BytesIO(raw))
    img.load()
    return Context(image=img, raw_bytes=raw, **kw)


# ------------------------------------------------------------ framework

def test_every_registered_technique_has_a_unique_number_and_key():
    keys = [t.key for t in REGISTRY.values()]
    assert len(keys) == len(set(keys)), "two techniques share a key"
    assert sorted(REGISTRY) == list(range(1, len(REGISTRY) + 1)), \
        "technique numbers must be the specification's, with no gaps"


def test_declaring_an_unknown_need_is_refused():
    with pytest.raises(ValueError, match="unknown needs"):
        @technique(id=9001, key="bogus", name="Bogus", phase=99,
                   needs=("telepathy",))
        def _bogus(ctx):                                  # pragma: no cover
            return Finding(technique=9001, key="bogus", name="Bogus", phase=99)


def test_registering_the_same_number_twice_is_refused():
    with pytest.raises(ValueError, match="already registered"):
        @technique(id=1, key="duplicate", name="Duplicate", phase=1)
        def _dupe(ctx):                                   # pragma: no cover
            return Finding(technique=1, key="duplicate", name="D", phase=1)


def test_an_unmet_need_skips_with_a_reason_a_person_can_read():
    ctx = Context()                      # nothing available at all
    findings = run_all(ctx, phases=[1])
    assert findings, "phase 1 should still produce a finding for each technique"
    for f in findings:
        assert not f.ran
        assert f.skipped_because
        # The reason must be words, not the internal need key.
        assert f.skipped_because not in {"exif", "ocr", "pixels", "evidence"}
        assert f.summary.startswith("Not applicable:")


def test_one_broken_technique_does_not_cost_the_others():
    @technique(id=9002, key="explodes", name="Explodes", phase=98)
    def _explodes(ctx):
        raise RuntimeError("detector went bang")

    @technique(id=9003, key="fine", name="Fine", phase=98)
    def _fine(ctx):
        return Finding(technique=9003, key="fine", name="Fine", phase=98,
                       found=True, summary="worked")

    try:
        findings = run_all(Context(), phases=[98])
        assert len(findings) == 2
        broken = next(f for f in findings if f.key == "explodes")
        assert not broken.ran and "went bang" in broken.summary
        good = next(f for f in findings if f.key == "fine")
        assert good.found
    finally:
        REGISTRY.pop(9002, None)
        REGISTRY.pop(9003, None)


def test_a_disabled_technique_does_not_run():
    @technique(id=9004, key="off", name="Off", phase=97, enabled=False)
    def _off(ctx):                                        # pragma: no cover
        raise AssertionError("a disabled technique must not be called")

    try:
        assert run_all(Context(), phases=[97]) == []
    finally:
        REGISTRY.pop(9004, None)


def test_findings_serialise_to_json():
    f = Finding(technique=1, key="k", name="N", phase=1, found=True,
                summary="s", regions=[Region(0, 0, 1, 1, "box")],
                constraint=Constraint(kind="countries",
                                      value=frozenset({"DE", "AT"})))
    text = json.dumps(f.as_dict())
    assert "DE" in text
    # Sets are not JSON; the constraint has to sort them on the way out.
    assert json.loads(text)["constraint"]["value"] == ["AT", "DE"]


# ----------------------------------------------- the reporting/steering line

# The techniques measured out in Phase 22, and the numbers they map to.
# If somebody wires one of these into the fusion without re-measuring,
# this test is what stops it.
BARRED_FROM_FUSION = {10, 14, 15}


def test_measured_out_techniques_report_but_never_steer():
    for tid in BARRED_FROM_FUSION:
        spec = REGISTRY[tid]
        assert not spec.feeds_fusion, (
            f"technique {tid} ({spec.key}) was measured and rejected as a "
            f"fusion input; turning it back on needs a new benchmark run, "
            f"not an edit to this flag"
        )
        assert spec.fusion_note, "a technique kept out of the fusion must say why"


def test_every_technique_that_steers_explains_itself():
    for spec in REGISTRY.values():
        if spec.feeds_fusion:
            assert spec.fusion_note, (
                f"technique {spec.id} ({spec.key}) moves the answer and must "
                f"record the measurement that earned it that"
            )


def test_the_descriptive_prompt_groups_constrain_nothing():
    import mapify.evidence.prompts as P

    for key in ("sign_style", "facade", "openings"):
        group = P.GROUPS_BY_KEY[key]
        assert not any(o.constrains for o in group.options), (
            f"{key} is a report-only group; giving one of its options a "
            f"constraint would put a measured-out clue back into the score"
        )
        assert group not in P.SCORING_GROUPS


# ---------------------------------------------------------- techniques 1-5

def test_exif_and_gps_and_time_run_on_a_tagged_file():
    from mapify.exif import read_exif

    exif = gps_exif(48.8584, 2.2945)
    exif[0x0110] = "TestCam"                       # Model
    raw = make_jpeg(exif=exif)
    img = Image.open(io.BytesIO(raw))
    img.load()
    ctx = Context(image=img, raw_bytes=raw, exif=read_exif(img))

    findings = {f.key: f for f in run_all(ctx, only=[1, 2, 4, 5])}
    assert findings["exif"].found
    assert findings["gps_tag"].found
    assert findings["gps_tag"].constraint.kind == "point"
    lat, lon = findings["gps_tag"].constraint.value
    assert abs(lat - 48.8584) < 1e-3 and abs(lon - 2.2945) < 1e-3
    assert findings["pixels"].found


def test_gps_is_skipped_rather_than_guessed_when_there_is_no_tag():
    from mapify.exif import read_exif

    raw = make_jpeg()
    img = Image.open(io.BytesIO(raw))
    img.load()
    ctx = Context(image=img, raw_bytes=raw, exif=read_exif(img))
    gps = next(f for f in run_all(ctx, only=[2]))
    assert not gps.ran
    assert "GPS" in gps.skipped_because
    assert gps.constraint is None


def test_pixel_inspection_notices_a_screen_sized_image_with_no_metadata():
    from mapify.exif import read_exif
    from mapify.forensics import inspect_pixels

    raw = make_jpeg(1920, 1080)
    img = Image.open(io.BytesIO(raw))
    img.load()
    rep = inspect_pixels(img, raw, read_exif(img))
    assert rep.width == 1920 and rep.aspect_name == "16:9"
    assert rep.likely_screenshot
    # Every signal has to come with its innocent explanation attached.
    assert all(meaning for _, meaning in rep.signals)


def test_pixel_inspection_notices_a_crop_the_metadata_disagrees_with():
    from mapify.forensics import inspect_pixels

    class Ex:
        has_exif = True
        pixel_size = (4032, 3024)
        software = None

    img = Image.new("RGB", (800, 600))
    rep = inspect_pixels(img, b"", Ex())
    assert any("4032x3024" in observed for observed, _ in rep.signals)


def test_jpeg_quality_estimate_tracks_the_save_quality():
    from mapify.forensics.pixels import inspect_pixels

    def quality_of(q):
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (120, 80, 60)).save(buf, "JPEG", quality=q)
        data = buf.getvalue()
        img = Image.open(io.BytesIO(data))
        img.load()
        return inspect_pixels(img, data).jpeg_quality

    low, high = quality_of(40), quality_of(95)
    assert low is not None and high is not None
    # The recovered number is approximate by nature; what has to hold is
    # that it orders correctly and lands in the right neighbourhood.
    assert low < high
    assert abs(low - 40) <= 12
    assert abs(high - 95) <= 12


# --------------------------------------------------------- techniques 6-12

def test_signboards_picks_the_largest_text_not_the_most_confident():
    lines = [
        FakeLine("tiny print at the bottom", 0.99, (0.1, 0.9, 0.9, 0.92)),
        FakeLine("BIG SIGN", 0.55, (0.2, 0.1, 0.8, 0.35)),
    ]
    ctx = image_ctx(ocr=FakeOcr(lines))
    finding = next(f for f in run_all(ctx, only=[7]))
    assert finding.found
    assert "BIG SIGN" in finding.summary
    assert "tiny print" not in finding.summary


def test_a_discriminating_legal_form_is_reported_with_its_countries():
    ctx = image_ctx(ocr=FakeOcr([FakeLine("Moebelhaus Weber GmbH", 0.9)]))
    finding = next(f for f in run_all(ctx, only=[9]))
    assert finding.found
    assert set(finding.detail["countries"]) >= {"DE", "AT", "CH"}
    assert finding.confidence > 0.5


def test_a_ubiquitous_legal_form_is_not_dressed_up_as_evidence():
    ctx = image_ctx(ocr=FakeOcr([FakeLine("Sharma Traders Ltd", 0.9)]))
    finding = next(f for f in run_all(ctx, only=[9]))
    assert finding.found
    assert finding.detail["countries"] == []
    assert "too many countries" in finding.summary
    assert finding.confidence < 0.3


def test_the_longer_legal_form_wins_over_the_one_inside_it():
    from mapify.data.business_forms import find_forms

    forms = find_forms("Outback Tours Pty Ltd")
    labels = [f.label for f in forms]
    assert any("Pty Ltd" in l for l in labels)
    assert not any(l.startswith("Ltd ") for l in labels)


def test_legal_form_matching_does_not_fire_on_a_word_containing_it():
    from mapify.data.business_forms import find_forms

    assert find_forms("Altdorf Bakery") == []
    assert find_forms("Aginco Services") == []


def test_a_plate_matching_one_scheme_says_so_and_one_matching_many_does_not():
    # An Indian registration: state code, district, series, four digits.
    # No other scheme in the table has that shape.
    sole = image_ctx(ocr=FakeOcr([FakeLine("MH 12 AB 1234", 0.8,
                                           (0.4, 0.6, 0.58, 0.65))]))
    finding = next(f for f in run_all(sole, only=[11]))
    assert finding.found
    assert finding.detail["single_jurisdiction"]
    assert finding.detail["countries"] == ["IN"]
    assert "one jurisdiction only" in finding.summary

    # A UK plate is *not* a unique match, and the tool must not pretend it
    # is: Poland issues two or three letters followed by four or five
    # alphanumerics, and Indonesia letters-digits-letters. Both subsume it.
    shared = image_ctx(ocr=FakeOcr([FakeLine("AB12 CDE", 0.8,
                                             (0.4, 0.6, 0.55, 0.65))]))
    finding = next(f for f in run_all(shared, only=[11]))
    assert finding.found
    assert not finding.detail["single_jurisdiction"]
    assert set(finding.detail["countries"]) >= {"GB", "PL", "ID"}
    assert "jurisdictions share this shape" in finding.summary


def test_prices_and_phone_numbers_are_not_read_as_plates():
    from mapify.data.plate_formats import looks_like_plate

    assert not looks_like_plate("1234567890")
    assert not looks_like_plate("OPEN")
    assert not looks_like_plate("$19.99")
    assert looks_like_plate("AB12CDE")


def test_a_tall_square_box_is_not_a_plate():
    from mapify.data.plate_formats import looks_like_plate

    wide = (0.40, 0.60, 0.52, 0.63)
    tall = (0.40, 0.30, 0.46, 0.80)
    assert looks_like_plate("AB12CDE", wide)
    assert not looks_like_plate("AB12CDE", tall)


def test_script_reports_the_dominant_writing_system_and_constrains_on_it():
    from mapify.evidence import script_detect

    text = "Καλημέρα " \
           "Αθήνα"
    reading = script_detect.analyse(text)
    ctx = image_ctx(ocr=FakeOcr([FakeLine(text, 0.9)]),
                    script_reading=reading)
    finding = next(f for f in run_all(ctx, only=[12]))
    assert finding.found
    assert finding.constraint is not None
    assert finding.constraint.kind == "script"
    assert finding.constraint.value == "Grek"


# -------------------------------------------------------- techniques 13-15

def test_a_descriptive_group_says_that_it_moves_nothing():
    ev = FakeEvidence([FakeReading("sign_style", "mutcd",
                                   "MUTCD (North American)", 0.62, 0.30)])
    ctx = image_ctx(evidence=ev)
    finding = next(f for f in run_all(ctx, only=[10]))
    assert finding.found
    assert "moves no coordinate" in finding.summary
    assert finding.detail["feeds_location"] is False
    assert finding.constraint is None


def test_a_coin_flip_reading_is_reported_as_no_reading():
    ev = FakeEvidence([FakeReading("facade", "brick", "Exposed brick",
                                   0.21, 0.02)])
    ctx = image_ctx(evidence=ev)
    finding = next(f for f in run_all(ctx, only=[14]))
    assert finding.ran
    assert not finding.found
    assert "too close to call" in finding.summary
    assert finding.confidence == 0.0


def test_architecture_is_allowed_to_steer_and_says_when_it_did():
    ev = FakeEvidence([FakeReading("architecture", "colonial",
                                   "Colonial", 0.55, 0.20, constrains=True)])
    ctx = image_ctx(evidence=ev)
    finding = next(f for f in run_all(ctx, only=[13]))
    assert finding.found
    assert finding.detail["feeds_location"] is True
    assert "moves no coordinate" not in finding.summary


# ------------------------------------------------------------- case file

def test_a_case_file_keeps_skipped_techniques_and_survives_a_round_trip(
        tmp_path, monkeypatch):
    import mapify.techniques.casefile as cf

    monkeypatch.setattr(cf, "state_dir", lambda: tmp_path)

    raw = make_jpeg()
    img = Image.open(io.BytesIO(raw))
    img.load()
    findings = run_all(Context(image=img, raw_bytes=raw), phases=[1])

    case = cf.CaseFile.for_image(raw, "photo.jpg")
    run = case.add_run(findings, prediction=None,
                       provenance={"tools": ["EasyOCR", "CLIP"]})
    assert run["techniques_skipped"] > 0, \
        "a bare image cannot satisfy most of phase 1; that must be recorded"
    path = case.save()
    assert path.exists()

    again = cf.CaseFile.load(case.case_id)
    assert again is not None
    assert again.filename == "photo.jpg"
    assert len(again.runs) == 1
    # The reasons have to survive the round trip, or the case file is
    # a list of absences with no explanation.
    skipped = [f for f in again.runs[0]["findings"] if not f["ran"]]
    assert skipped and all(f["skipped_because"] for f in skipped)


def test_the_same_image_under_a_new_name_is_the_same_case(tmp_path, monkeypatch):
    import mapify.techniques.casefile as cf

    monkeypatch.setattr(cf, "state_dir", lambda: tmp_path)
    raw = make_jpeg()

    first = cf.CaseFile.for_image(raw, "a.jpg")
    first.add_run([], prediction=None)
    first.save()

    second = cf.CaseFile.for_image(raw, "renamed.jpg")
    assert second.case_id == first.case_id
    assert len(second.runs) == 1, "the earlier run must still be there"
    second.add_run([], prediction=None)
    second.save()
    assert len(cf.CaseFile.load(first.case_id).runs) == 2
