"""End-to-end pipeline behaviour, driven by stand-in models.

No weights are loaded here. The locator and evidence extractor are replaced
with deterministic stubs, which is the point of putting them behind protocols:
the decode, EXIF, fusion, enrichment and reporting path can be exercised in
milliseconds and asserted exactly.
"""

from __future__ import annotations

import io

import pytest

from conftest import gps_exif, make_jpeg, requires_geodata
from mapify.config import load_config
from mapify.models.base import GroupReading, RawCandidate, VisualEvidence
from mapify.pipeline import AnalysisEngine, ImageRejected


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

    def test_disagreement_is_called_out(self, engine):
        data = make_jpeg(exif=gps_exif(35.6762, 139.6503))
        with_locator(engine, [(48.8584, 2.2945, 0.1)])
        report = engine.analyse(data, "x.jpg")
        agree = report.prediction["exif_vs_estimate"]
        assert agree["consistent"] is False
        assert agree["distance_km"] > 9000
        assert "easier of the two to fake" in agree["note"]

    def test_agreement_is_called_out_too(self, engine):
        data = make_jpeg(exif=gps_exif(48.8584, 2.2945))
        with_locator(engine, [(48.8584, 2.2945, 0.1)])
        report = engine.analyse(data, "x.jpg")
        agree = report.prediction["exif_vs_estimate"]
        assert agree["consistent"] is True
        assert agree["distance_km"] < 1

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
