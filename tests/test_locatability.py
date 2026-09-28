"""The locatability gate: is this a photograph of a place at all?

These tests pin the behaviour the measurement justified, including the
cases that forced the design. The first version refused on the
zero-shot reading alone, and a choir photographed in a tent read as 91%
"not a photograph". So the two failure directions are both tested: a
drawn poster must be refused, and a real photograph that merely *reads*
like one must not be.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from conftest import make_jpeg
from mapify.locatability import DEFAULT_RULE, assess


def kinds(**p):
    """A full image-kind distribution; unspecified options share the rest."""
    keys = ("outdoor", "indoor", "object", "document", "poster", "screenshot",
            "chart", "slide", "logo", "illustration", "map")
    rest = max(0.0, 1.0 - sum(p.values()))
    others = [k for k in keys if k not in p]
    out = {k: rest / len(others) for k in others} if others else {}
    out.update(p)
    return out


class Pixels:
    def __init__(self, flat_mid=0.0, flat=None, palette=0.0, white=0.0):
        self.flat_mid_fraction = flat_mid
        self.flat_fraction = flat_mid if flat is None else flat
        self.palette_share = palette
        self.white_fraction = white


class Exif:
    def __init__(self, make=None, model=None):
        self.camera_make = make
        self.camera_model = model


DRAWN = Pixels(flat_mid=0.6, palette=0.9)          # a designed poster
PHOTO = Pixels(flat_mid=0.05, palette=0.25)        # a camera's pixels
PAGE = Pixels(flat_mid=0.02, flat=0.7, palette=0.95, white=0.7)


# ------------------------------------------------------------- verdicts

def test_a_drawn_poster_is_refused():
    g = assess(kinds(poster=0.6, screenshot=0.3), DRAWN)
    assert g.verdict == "not_a_photo"
    assert not g.locatable and g.withhold
    assert "not a photograph of a place" in g.headline()
    assert g.reasons, "a refusal must say why"


def test_a_white_page_is_refused_by_the_page_route():
    g = assess(kinds(chart=0.7, document=0.2), PAGE)
    assert g.verdict == "not_a_photo"
    assert any("pure white" in r for r in g.reasons)


def test_a_real_photograph_that_reads_like_a_poster_is_not_refused():
    # Harare: a choir in a tent read 91% "not a photograph". Its pixels
    # are a camera's, and that is what keeps it.
    g = assess(kinds(document=0.46, poster=0.25, chart=0.2, outdoor=0.09),
               PHOTO)
    assert g.verdict != "not_a_photo"
    assert g.locatable


def test_a_black_night_sky_does_not_count_as_drawn():
    # The Moon on a black sky is 95% flat -- all of it pure black, which
    # the mid-tone measure ignores.
    moon = Pixels(flat_mid=0.01, flat=0.95, palette=0.99, white=0.0)
    g = assess(kinds(object=0.28, illustration=0.3, poster=0.29), moon)
    assert g.verdict != "not_a_photo"


def test_a_very_sure_reading_without_drawn_pixels_is_doubtful_not_refused():
    g = assess(kinds(poster=0.97), PHOTO)
    assert g.verdict == "doubtful"
    assert g.locatable, "candidates may still be listed"
    assert g.withhold, "but no location is given"


def test_the_most_poster_like_real_photograph_stays_clear_of_doubtful():
    # The benchmark's most poster-like photograph of a place reads 0.912.
    g = assess(kinds(slide=0.40, poster=0.30, document=0.212, outdoor=0.088),
               PHOTO)
    assert g.verdict == "place"
    assert not g.withhold


def test_a_camera_named_in_the_file_needs_a_surer_reading():
    g = assess(kinds(document=0.85, outdoor=0.15), DRAWN,
               exif=Exif("Apple", "iPhone 15"))
    assert g.verdict == "place", "0.85 is below the camera threshold"
    g = assess(kinds(document=0.97), DRAWN, exif=Exif("Apple", "iPhone 15"))
    assert g.verdict == "not_a_photo"
    assert any("camera" in r for r in g.reasons)


def test_an_object_close_up_withholds_its_location():
    g = assess(kinds(object=0.75, outdoor=0.1, indoor=0.05), PHOTO)
    assert g.verdict == "no_scenery"
    assert g.locatable and g.withhold


def test_an_ordinary_street_photograph_is_a_place():
    g = assess(kinds(outdoor=0.8, indoor=0.05), PHOTO)
    assert g.verdict == "place"
    assert g.locatable and not g.withhold


def test_no_reading_at_all_lets_the_image_through():
    """Withholding is a claim that needs evidence; a failed encoder is none."""
    g = assess({}, DRAWN)
    assert g.verdict == "place"


def test_the_shipped_thresholds_are_the_fitted_ones():
    # If these move, bench/fit-locatability.txt has to be regenerated and
    # the new numbers argued for; they are not tuning knobs.
    r = DEFAULT_RULE
    assert (r.weak_not_photo, r.drawn_flat, r.drawn_palette,
            r.drawn_white, r.drawn_white_flat, r.doubtful_not_photo) == \
        (0.80, 0.35, 0.60, 0.40, 0.35, 0.95)


# ------------------------------------------------------ pixel statistics

def _poster_bytes() -> Image.Image:
    img = Image.new("RGB", (900, 1200), (12, 30, 60))
    d = ImageDraw.Draw(img)
    d.rectangle([80, 80, 820, 260], fill=(0, 200, 255))
    d.ellipse([300, 400, 600, 700], fill=(255, 80, 60))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(buf.getvalue()))


def test_a_drawn_image_measures_as_flat_mid_tones():
    from mapify.forensics.graphic import graphic_stats
    s = graphic_stats(_poster_bytes())
    assert s.flat_mid_fraction > 0.8
    assert s.palette_share > 0.9


def test_noise_measures_as_not_flat():
    import numpy as np
    from mapify.forensics.graphic import graphic_stats
    rng = np.random.default_rng(0)
    # Strong enough to survive the half-size downscale the measurement
    # works on, which averages noise down by about half.
    arr = (rng.normal(128, 30, (600, 800, 3))).clip(0, 255).astype("uint8")
    s = graphic_stats(Image.fromarray(arr))
    assert s.flat_mid_fraction < 0.01
    assert s.texture_fraction > 0.5


def test_pure_black_is_flat_but_not_flat_mid():
    from mapify.forensics.graphic import graphic_stats
    s = graphic_stats(Image.new("RGB", (800, 600), (0, 0, 0)))
    assert s.flat_fraction > 0.95
    assert s.flat_mid_fraction < 0.01


def test_text_coverage_sums_box_areas_and_caps_at_one():
    from mapify.forensics.graphic import text_coverage

    class L:
        def __init__(self, box):
            self.box = box

    class O:
        lines = [L((0, 0, 0.5, 0.2)), L((0, 0.5, 1.0, 0.6)), L(None)]

    assert text_coverage(O()) == pytest.approx(0.1 + 0.1)

    class Full:
        lines = [L((0, 0, 1, 1)), L((0, 0, 1, 1))]

    assert text_coverage(Full()) == 1.0


# ------------------------------------------------------------ pipeline

def _engine():
    from mapify.config import load_config
    from mapify.pipeline import AnalysisEngine
    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.ocr_enabled = False
    cfg.models.geoclip_enabled = False
    cfg.models.clip_evidence_enabled = False
    return AnalysisEngine(cfg)


class StubLocator:
    name = "stub"
    load_error = None

    def __init__(self, points):
        self.points = points
        self.embedding = object()

    def load(self):
        return True

    @property
    def loaded(self):
        return True

    @property
    def gallery_size(self):
        return len(self.points)

    def shared_clip(self):
        return None, None

    def locate(self, image, top_k=12):
        from mapify.models.base import RawCandidate
        return [RawCandidate(a, b, s, self.name) for a, b, s in self.points]

    def locate_with_embedding(self, image, top_k=12):
        return self.locate(image, top_k), self.embedding


class StubEvidence:
    def __init__(self, kind_probs):
        self.kind_probs = kind_probs

    def score_embedding(self, embedding):
        from mapify.models.base import VisualEvidence
        return VisualEvidence(readings=[], model="stub")

    def group_probabilities(self, embedding, key):
        return self.kind_probs if key == "image_kind" else {}


def _poster_jpeg() -> bytes:
    img = Image.new("RGB", (900, 1200), (12, 30, 60))
    d = ImageDraw.Draw(img)
    d.rectangle([80, 80, 820, 260], fill=(0, 200, 255))
    d.ellipse([300, 400, 600, 700], fill=(255, 80, 60))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _photo_jpeg() -> bytes:
    """Camera-like pixels: sensor noise over a gradient, never flat."""
    import numpy as np
    rng = np.random.default_rng(1)
    y = np.linspace(60, 190, 480)[:, None, None]
    arr = (y + rng.normal(0, 14, (480, 640, 3))).clip(0, 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _confident(engine, probs):
    engine.geoclip = StubLocator([(48.8584, 2.2945, 0.60),
                                  (48.8600, 2.2950, 0.25)])
    engine.cfg.models.clip_evidence_enabled = True
    engine.evidence_extractor = StubEvidence(probs)
    engine.clip.load = lambda *a, **k: True
    return engine


def test_a_poster_gets_no_candidates_no_estimate_and_says_why():
    eng = _confident(_engine(), kinds(poster=0.7, screenshot=0.25))
    report = eng.analyse(_poster_jpeg(), "poster.png")
    assert report.ok
    assert report.candidates == [], "no candidates at all for a refusal"
    assert report.raw_candidates == [], "nothing a session could fuse"
    pred = report.prediction
    assert pred["locatable"] is False
    assert pred["estimated"] is None and pred["determinate"] is False
    assert "not a photograph of a place" in pred["confidence_reason"]
    assert report.as_dict()["locatability"]["verdict"] == "not_a_photo"


def test_the_same_locator_output_on_a_photograph_is_reported_as_usual():
    eng = _confident(_engine(), kinds(outdoor=0.85))
    report = eng.analyse(_photo_jpeg(), "x.jpg")
    assert report.ok
    assert report.candidates
    assert report.prediction["locatable"] is True
    assert report.prediction["image_kind"]["verdict"] == "place"


def test_a_doubtful_image_keeps_candidates_but_gives_no_location():
    eng = _confident(_engine(), kinds(poster=0.97))
    report = eng.analyse(_photo_jpeg(), "x.jpg")
    assert report.candidates, "listed as what the model considered"
    pred = report.prediction
    assert pred["determinate"] is False
    assert pred["confidence"] == "insufficient"
    assert pred["image_kind"]["verdict"] == "doubtful"


def test_a_session_does_not_count_a_poster():
    from mapify.session import Observation
    eng = _confident(_engine(), kinds(poster=0.7, screenshot=0.25))
    report = eng.analyse(_poster_jpeg(), "poster.png")
    obs = Observation(index=0, name="poster.png", report=report)
    assert not obs.locatable
    assert not obs.counted
