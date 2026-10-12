"""End-to-end pipeline behaviour, driven by stand-in models.

No weights are loaded here. The locator and evidence extractor are replaced
with deterministic stubs, which is the point of putting them behind protocols:
the decode, EXIF, fusion, enrichment and reporting path can be exercised in
milliseconds and asserted exactly.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from conftest import gps_exif, make_jpeg, requires_geodata
from mapify import heif
from mapify.config import load_config
from mapify.models.base import GroupReading, RawCandidate, VisualEvidence
from mapify.pipeline import AnalysisEngine, ImageRejected

# A 128 x 128 HEIF from pillow_heif's test images (BSD-3-Clause; see the
# README beside it).
HEIF_SAMPLE = Path(__file__).parent / "data" / "heif" / "RGB_8__128x128.heif"


class StubLocator:
    """Returns a fixed set of coordinates, ignoring the image."""

    name = "stub-locator"

    def __init__(self, points, embedding=None):
        self.points = points
        self.embedding = embedding
        self.load_error = None
        self.calls = 0

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
        self.calls += 1
        return [
            RawCandidate(lat, lon, score, self.name)
            for lat, lon, score in self.points[:top_k]
        ]

    def locate_with_embedding(self, image, top_k=12):
        return self.locate(image, top_k), self.embedding


class StubEvidence:
    def __init__(self, readings=None, prior=None):
        self.readings = readings or []
        self.prior = prior or {}

    def score_embedding(self, embedding):
        return VisualEvidence(readings=list(self.readings), model="stub")


@pytest.fixture
def engine(tmp_path):
    cfg = load_config()
    cfg.network.offline_only = True          # no outbound calls in tests
    cfg.models.ocr_enabled = False
    cfg.models.geoclip_enabled = False       # we inject a stub instead
    cfg.models.clip_evidence_enabled = False
    eng = AnalysisEngine(cfg)
    return eng


def with_locator(engine, points, readings=None):
    engine.geoclip = StubLocator(points, embedding=None)
    if readings is not None:
        engine.cfg.models.clip_evidence_enabled = True
        engine.evidence_extractor = StubEvidence(readings)
        engine.clip.load = lambda *a, **k: True
        engine.geoclip.embedding = object()
    return engine


class TestDecode:
    def test_accepts_a_real_jpeg(self, engine, plain_jpeg):
        img, meta = engine.decode(plain_jpeg, "x.jpg")
        assert meta["format"] == "JPEG"
        assert meta["width"] == 320 and meta["height"] == 240

    def test_accepts_png_and_webp(self, engine):
        from PIL import Image

        for fmt, ext in (("PNG", "png"), ("WEBP", "webp")):
            buf = io.BytesIO()
            Image.new("RGB", (200, 200), (10, 20, 30)).save(buf, fmt)
            _, meta = engine.decode(buf.getvalue(), f"x.{ext}")
            assert meta["format"] == fmt

    def test_rejects_empty_upload(self, engine):
        with pytest.raises(ImageRejected, match="Empty"):
            engine.decode(b"", "x.jpg")

    def test_rejects_a_file_that_is_not_an_image(self, engine):
        with pytest.raises(ImageRejected, match="Could not decode"):
            engine.decode(b"this is not an image, whatever it is named", "x.jpg")

    def test_a_renamed_text_file_is_still_refused(self, engine):
        """The declared extension is never trusted; the bytes decide."""
        with pytest.raises(ImageRejected):
            engine.decode(b"GIF89a" + b"\x00" * 64, "photo.jpg")

    def test_rejects_an_oversized_upload(self, engine):
        engine.cfg.server.max_upload_bytes = 1000
        with pytest.raises(ImageRejected, match="limit is"):
            engine.decode(make_jpeg(800, 800), "big.jpg")

    def test_rejects_a_thumbnail(self, engine):
        with pytest.raises(ImageRejected, match="Too small"):
            engine.decode(make_jpeg(32, 32), "tiny.jpg")

    def test_large_images_are_downscaled_for_the_model(self, engine):
        from PIL import Image

        big = Image.new("RGB", (4000, 3000), (1, 2, 3))
        small = engine._prepare(big, 1600)
        assert max(small.size) == 1600
        assert small.mode == "RGB"

    def test_heic_decodes_when_the_decoder_is_installed(self, engine):
        """The iPhone's default format, rejected outright until now."""
        pytest.importorskip("pi_heif")
        img, meta = engine.decode(HEIF_SAMPLE.read_bytes(), "photo.heic")
        assert meta["format"] == "HEIF"
        assert img.size == (128, 128)
        assert engine._prepare(img, 1600).mode == "RGB"

    def test_heic_without_the_decoder_says_how_to_add_it(self, engine,
                                                         monkeypatch):
        """Not "could not decode": the file is fine, a decoder is missing."""
        monkeypatch.setattr(heif, "enable", lambda: False)
        with pytest.raises(ImageRejected, match="pip install pi-heif"):
            engine.decode(HEIF_SAMPLE.read_bytes(), "photo.heic")

    def test_a_heif_header_is_recognised_whatever_the_file_is_called(self):
        assert heif.looks_like_heif(HEIF_SAMPLE.read_bytes())
        assert not heif.looks_like_heif(make_jpeg())
        assert not heif.looks_like_heif(b"")


@requires_geodata
class TestAnalysis:
    def test_reports_a_location_for_a_confident_locator(self, engine, plain_jpeg):
        with_locator(engine, [
            (48.8584, 2.2945, 0.12),
            (48.8590, 2.2950, 0.12),
            (48.8580, 2.2940, 0.10),
        ])
        report = engine.analyse(plain_jpeg, "paris.jpg")
        assert report.ok, report.error
        est = report.prediction["estimated"]
        assert est["place"]["country"] == "France"
        assert report.prediction["confidence"] == "high"
        assert report.prediction["determinate"] is True

    def test_says_so_when_it_cannot_tell(self, engine, plain_jpeg):
        with_locator(engine, [
            (48.85, 2.29, 0.004), (-33.87, 151.21, 0.004),
            (40.71, -74.01, 0.003), (-22.95, -43.21, 0.003),
        ])
        report = engine.analyse(plain_jpeg, "unknown.jpg")
        assert report.ok
        assert report.prediction["confidence"] == "insufficient"
        assert report.prediction["determinate"] is False
        assert report.prediction["notes"]

    def test_progress_callback_reports_every_stage(self, engine, plain_jpeg):
        with_locator(engine, [(48.8584, 2.2945, 0.1)])
        seen = []
        engine.analyse(plain_jpeg, "x.jpg", progress=lambda k, m: seen.append(k))
        assert seen[0] == "decode"
        assert seen[-1] == "done"
        for stage in ("exif", "locate", "fuse", "enrich"):
            assert stage in seen

    def test_a_broken_image_returns_an_error_not_an_exception(self, engine):
        report = engine.analyse(b"not an image", "bad.jpg")
        assert report.ok is False
        assert "Could not decode" in report.error

    def test_report_serialises_to_plain_json(self, engine, plain_jpeg):
        import json

        with_locator(engine, [(48.8584, 2.2945, 0.1)])
        report = engine.analyse(plain_jpeg, "x.jpg")
        json.dumps(report.as_dict())        # must not raise

    def test_attribution_is_always_present(self, engine, plain_jpeg):
        with_locator(engine, [(48.8584, 2.2945, 0.1)])
        report = engine.analyse(plain_jpeg, "x.jpg")
        assert any("Natural Earth" in a for a in report.attribution)


@requires_geodata
class TestExifIsNotTheEstimate:
    def test_embedded_gps_is_reported_separately(self, engine):
        data = make_jpeg(exif=gps_exif(35.6762, 139.6503))     # Tokyo
        with_locator(engine, [(48.8584, 2.2945, 0.1)])         # model says Paris
        report = engine.analyse(data, "tokyo-tagged.jpg")

        est = report.prediction["estimated"]
        gps = report.prediction["embedded_gps"]
        assert est["place"]["country"] == "France"
        assert gps["place"]["country"]["name"] == "Japan"
        # The tag must not have moved the estimate.
        assert est["latitude"] == pytest.approx(48.8584, abs=0.01)

    def test_a_weak_estimate_cannot_reject_the_tag(self, engine):
        """Disagreement is not adjudication.

        A locator holding a tenth of its distribution has not earned the
        right to call a GPS tag a lie. Three outcomes exist precisely so
        that "they disagree and I cannot tell which is wrong" can be said
        out loud instead of being rounded to one side or the other.
        """
        data = make_jpeg(exif=gps_exif(35.6762, 139.6503))     # Tokyo
        with_locator(engine, [(48.8584, 2.2945, 0.1)])         # weakly Paris
        report = engine.analyse(data, "x.jpg")
        agree = report.prediction["exif_vs_estimate"]
        assert agree["consistent"] is False
        assert agree["verdict"] == "unresolved"
        assert agree["distance_km"] > 9000
        assert "too weak to say which is wrong" in agree["note"]
        # And the tag must not have moved the estimate.
        assert report.prediction["estimated"]["latitude"] == pytest.approx(
            48.8584, abs=0.01
        )

    def test_a_strong_estimate_rejects_the_tag_in_those_words(self, engine):
        data = make_jpeg(exif=gps_exif(35.6762, 139.6503))     # Tokyo
        with_locator(engine, [(48.8584, 2.2945, 0.9)])         # firmly Paris
        report = engine.analyse(data, "x.jpg")
        agree = report.prediction["exif_vs_estimate"]
        assert agree["verdict"] == "rejected"
        assert "rejected" in agree["note"]
        # Rejected is not deleted: it is still a fact about the file.
        assert report.prediction["embedded_gps"] is not None
        assert report.prediction["estimated"]["latitude"] == pytest.approx(
            48.8584, abs=0.01
        )

    def test_a_corroborated_tag_becomes_the_coordinate(self, engine):
        """Two independent things pointing at one place.

        The tag is metres where the model is kilometres, so once the image
        has independently agreed with it, using it is strictly better than
        reporting a fuzzier number beside it. The provenance says so.
        """
        data = make_jpeg(exif=gps_exif(48.8584, 2.2945))
        with_locator(engine, [(48.86, 2.30, 0.9)])
        report = engine.analyse(data, "x.jpg")
        agree = report.prediction["exif_vs_estimate"]
        est = report.prediction["estimated"]

        assert agree["verdict"] == "corroborated"
        assert agree["consistent"] is True
        assert est["latitude"] == pytest.approx(48.8584, abs=1e-4)
        assert est["longitude"] == pytest.approx(2.2945, abs=1e-4)
        assert "exif" in est["provenance"]
        assert est["precision_km"] < 0.1

    def test_an_address_read_off_the_image_can_reject_the_tag(self, engine):
        """Where Phase 21 and Phase 22 meet, tested without a network.

        A weak locator cannot reject a tag, and should not. But a *read
        address* is different in kind: the place is printed in the
        photograph and OpenStreetMap resolved it. That is enough to say
        which of the two is wrong even when the visual model is vague,
        and it is the case a faked tag is most likely to be caught by.
        """
        from mapify.evidence.fusion import Candidate, FusionResult

        top = Candidate(
            latitude=12.7972, longitude=80.2176,
            prior_score=0.3, score=0.3,
            sources=["read-address"], provenance="text",
        )
        fused = FusionResult(
            candidates=[top], toponyms=[], confidence="medium",
            confidence_reason="", uncertainty_km=5.0, centroid=None,
            locator_mass=0.04,          # far too weak to adjudicate alone
        )

        class Tag:
            latitude, longitude = 47.4979, 19.0402      # Budapest
        class Exif:
            gps = Tag()

        verdict = engine._judge_exif(fused, Exif(), top)
        assert verdict["verdict"] == "rejected"
        assert verdict["distance_km"] > 6000
        assert "read from the image" in verdict["note"]

    def test_the_same_weak_locator_alone_would_not_have_rejected_it(self, engine):
        """The control for the test above: it is the read address doing it."""
        from mapify.evidence.fusion import Candidate, FusionResult

        top = Candidate(
            latitude=12.7972, longitude=80.2176,
            prior_score=0.3, score=0.3,
            sources=["geoclip"], provenance="model",
        )
        fused = FusionResult(
            candidates=[top], toponyms=[], confidence="medium",
            confidence_reason="", uncertainty_km=5.0, centroid=None,
            locator_mass=0.04,
        )

        class Tag:
            latitude, longitude = 47.4979, 19.0402
        class Exif:
            gps = Tag()

        assert engine._judge_exif(fused, Exif(), top)["verdict"] == "unresolved"

    def test_no_tag_produces_no_verdict_at_all(self, engine):
        """An absent GPS tag is not a finding and must not read as one."""
        data = make_jpeg()
        with_locator(engine, [(48.8584, 2.2945, 0.9)])
        report = engine.analyse(data, "x.jpg")
        assert report.prediction["embedded_gps"] is None
        assert report.prediction["exif_vs_estimate"] is None

    def test_no_gps_means_no_embedded_block(self, engine, plain_jpeg):
        with_locator(engine, [(48.8584, 2.2945, 0.1)])
        report = engine.analyse(plain_jpeg, "x.jpg")
        assert report.prediction["embedded_gps"] is None
        assert report.prediction["exif_vs_estimate"] is None


@requires_geodata
class TestEvidenceReachesTheReport:
    def test_called_evidence_reranks_and_is_explained(self, engine, plain_jpeg):
        readings = [
            GroupReading(
                group_key="traffic_side", group_title="Traffic direction",
                option_key="left", option_label="Left-hand traffic",
                confidence=0.9, margin=0.5, called=True, constrains=True,
            )
        ]
        with_locator(
            engine,
            [(12.9716, 77.5946, 0.05), (48.8584, 2.2945, 0.05)],
            readings=readings,
        )
        report = engine.analyse(plain_jpeg, "x.jpg")
        assert report.evidence["readings"][0]["called"] is True
        top = report.candidates[0]
        assert top["location"]["country"]["iso_a2"] == "IN"
        assert any(a["source"] == "traffic_side" for a in top["adjustments"])


class TestOfflineMode:
    def test_offline_engine_declares_itself(self, engine):
        status = engine.engine_status()
        assert status["offline_only"] is True
        assert status["nominatim_enabled"] is False
        assert status["overpass_enabled"] is False


def two_tone_jpeg() -> bytes:
    """Red on the left, blue on the right: which way round is visible."""
    from PIL import Image

    im = Image.new("RGB", (256, 256), (200, 0, 0))
    im.paste((0, 0, 200), (128, 0, 256, 256))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def blue_on_the_left(image) -> bool:
    return image.convert("RGB").getpixel((10, 128))[2] > 100


class MirrorOcr:
    """A sign that reads properly only when the blue half is on the left,
    as text on a mirrored photograph reads properly only when flipped."""

    name = "mirror-fake"

    def __init__(self, good=0.93, bad=0.12):
        self.good, self.bad = good, bad
        self.reads = 0

    def read(self, image, extra_group=None):
        from mapify.models.base import OcrLine, OcrResult

        self.reads += 1
        if blue_on_the_left(image):
            return OcrResult(lines=[OcrLine("MOSCOW", self.good)], engine=self.name)
        return OcrResult(lines=[OcrLine("WOCSOW", self.bad)], engine=self.name)


class RecordingLocator(StubLocator):
    def __init__(self, points):
        super().__init__(points)
        self.images = []

    def locate(self, image, top_k=12):
        self.images.append(image)
        return super().locate(image, top_k)


@requires_geodata
class TestMirroredPhotograph:
    POINTS = [(55.7558, 37.6173, 0.10), (55.7600, 37.6200, 0.08)]

    def test_text_that_reads_only_flipped_means_the_photo_is_mirrored(
            self, engine):
        from mapify.pipeline import MIRRORED_NOTE

        engine.geoclip = RecordingLocator(self.POINTS)
        engine.ocr = MirrorOcr()
        report = engine.analyse(two_tone_jpeg(), "selfie.jpg")
        assert report.ok, report.error
        assert report.image.get("mirrored") is True
        assert MIRRORED_NOTE in report.warnings
        assert report.ocr["text"] == "MOSCOW"
        # The locator looked again, at the flipped copy...
        assert [blue_on_the_left(i) for i in engine.geoclip.images] == [False, True]
        # ...and the flipped text was read once, not again.
        assert engine.ocr.reads == 2

    def test_text_that_reads_well_costs_no_second_reading(self, engine):
        engine.geoclip = RecordingLocator(self.POINTS)
        engine.ocr = MirrorOcr(good=0.93, bad=0.93)
        report = engine.analyse(two_tone_jpeg(), "street.jpg")
        assert "mirrored" not in report.image
        assert engine.ocr.reads == 1
        assert len(engine.geoclip.images) == 1

    def test_flipping_that_reads_no_better_changes_nothing(self, engine):
        engine.geoclip = RecordingLocator(self.POINTS)
        engine.ocr = MirrorOcr(good=0.2, bad=0.12)
        report = engine.analyse(two_tone_jpeg(), "blurred.jpg")
        assert "mirrored" not in report.image
        assert report.ocr["text"] == "WOCSOW"
        assert engine.ocr.reads == 2
        assert len(engine.geoclip.images) == 1


class TestReadingQuality:
    def test_fragments_cannot_outvote_a_sign(self):
        from mapify.models.base import OcrLine, OcrResult
        from mapify.pipeline import reading_quality

        ocr = OcrResult(lines=[OcrLine("a", 0.0), OcrLine("|", 0.0),
                               OcrLine("STATION", 0.9)])
        assert reading_quality(ocr) == pytest.approx(0.9)
        assert reading_quality(OcrResult()) == 0.0


@requires_geodata
def test_a_point_is_named_after_a_city_in_its_own_country(engine):
    # On the Indian side of the Wagah border Lahore is nearer than Amritsar;
    # "Lahore, India" names a city in the wrong country.
    from mapify.evidence.fusion import Candidate
    loc = engine.geocoder.locate(31.5948, 74.5664, place_limit=4)
    assert loc.country.name == "India"
    assert loc.nearest_places[0].admin0 == "Pakistan"
    cand = Candidate(latitude=31.5948, longitude=74.5664, prior_score=1.0, score=1.0,
                     location=loc)
    named = engine._describe(cand)
    assert named["country"] == "India" and named["city"] == "Amritsar"
