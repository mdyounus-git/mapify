"""Choosing an OCR reader from the detected script, and using what it reads.

An English-only recogniser does not return nothing on a Cyrillic sign — it
returns Latin lookalikes. "МОСКВА" comes back as "MOCKBA", which script
detection then scores as 100% Latin, and fusion uses that to push *away* from
Russia. Reading the wrong thing is worse than reading nothing, which is why
the reader has to match the script.
"""

from __future__ import annotations

import pytest

from conftest import requires_geodata
from mapify.config import FusionConfig
from mapify.evidence.fusion import FusionEngine
from mapify.evidence.script_detect import analyse
from mapify.models.base import GroupReading, OcrLine, OcrResult, RawCandidate
from mapify.models.ocr import SCRIPT_TO_GROUP, group_for_script


class TestScriptToReader:
    @pytest.mark.parametrize(
        "script, expect",
        [
            ("Cyrl", ("ru", "en")),
            ("Arab", ("ar", "en")),
            ("Deva", ("hi", "en")),
            ("Jpan", ("ja", "en")),
            ("Hang", ("ko", "en")),
            ("Hani", ("ch_sim", "en")),
            ("Knda", ("kn", "en")),
            ("Thai", ("th", "en")),
        ],
    )
    def test_non_latin_scripts_get_their_own_reader(self, script, expect):
        assert group_for_script(script) == expect

    def test_latin_needs_no_extra_reader(self):
        assert group_for_script("Latn") is None

    def test_scripts_with_no_recogniser_ask_for_nothing(self):
        """EasyOCR has no Greek or Hebrew model; do not load a useless one."""
        assert group_for_script("Grek") is None
        assert group_for_script("Hebr") is None

    def test_an_unknown_script_is_not_an_error(self):
        assert group_for_script("Xxxx") is None

    def test_every_mapped_group_ends_with_english(self):
        """EasyOCR needs a Latin model alongside to read mixed signage."""
        for script, group in SCRIPT_TO_GROUP.items():
            assert "en" in group, f"{script} group {group} has no Latin model"


class FakeOcr:
    """Returns different text depending on which reader it was asked for."""

    name = "fake"

    def __init__(self):
        self.calls: list[tuple[str, ...] | None] = []

    def read(self, image, extra_group=None):
        self.calls.append(extra_group)
        if extra_group and "ru" in extra_group:
            return OcrResult(
                lines=[OcrLine(text="МОСКВА", confidence=0.9)],
                engine=self.name, languages=["ru", "en"],
            )
        # What an English-only reader actually does to Cyrillic.
        return OcrResult(
            lines=[OcrLine(text="MOCKBA", confidence=0.6)],
            engine=self.name, languages=["en"],
        )


def script_reading(option: str, called: bool = True) -> GroupReading:
    return GroupReading(
        group_key="signage_script", group_title="Signage script",
        option_key=option, option_label=option, confidence=0.8, margin=0.4,
        called=called, constrains=True,
    )


class TestMisreadingIsWorseThanNotReading:
    def test_latin_lookalikes_are_scored_as_latin(self):
        """This is the failure the routing exists to prevent."""
        reading = analyse("MOCKBA")
        assert [s for s, _ in reading.dominant()] == ["Latn"]

    def test_the_right_reader_gives_cyrillic(self):
        reading = analyse("МОСКВА")
        assert [s for s, _ in reading.dominant()] == ["Cyrl"]


@requires_geodata
class TestRoutingThroughThePipeline:
    @pytest.fixture
    def engine(self):
        from mapify.config import load_config
        from mapify.pipeline import AnalysisEngine

        cfg = load_config()
        cfg.network.offline_only = True
        cfg.models.geoclip_enabled = False
        cfg.models.clip_evidence_enabled = False
        eng = AnalysisEngine(cfg)
        eng.ocr = FakeOcr()
        return eng

    def test_a_called_cyrillic_reading_asks_for_the_russian_reader(
        self, engine, plain_jpeg
    ):
        from mapify.models.base import RawCandidate as RC
        from mapify.models.base import VisualEvidence

        class Stub:
            name = "stub"
            load_error = None
            loaded = True
            gallery_size = 1

            def load(self):
                return True

            def shared_clip(self):
                return None, None

            def locate_with_embedding(self, image, top_k=12):
                return [RC(55.7558, 37.6173, 0.1, "stub")], object()

        engine.geoclip = Stub()
        engine.cfg.models.clip_evidence_enabled = True
        engine.clip.load = lambda *a, **k: True
        engine.evidence_extractor = type(
            "E", (), {
                "score_embedding": lambda self, emb: VisualEvidence(
                    readings=[script_reading("Cyrl")], model="stub"
                )
            }
        )()

        report = engine.analyse(plain_jpeg, "sign.jpg")
        assert report.ok, report.error
        assert engine.ocr.calls == [("ru", "en")], (
            "the pipeline should have asked for the Cyrillic reader"
        )
        assert "МОСКВА" in report.ocr["text"]

    def test_no_script_call_means_no_extra_reader(self, engine, plain_jpeg):
        from mapify.models.base import RawCandidate as RC

        class Stub:
            name = "stub"
            load_error = None
            loaded = True
            gallery_size = 1

            def load(self):
                return True

            def shared_clip(self):
                return None, None

            def locate_with_embedding(self, image, top_k=12):
                return [RC(48.8584, 2.2945, 0.1, "stub")], None

        engine.geoclip = Stub()
        report = engine.analyse(plain_jpeg, "x.jpg")
        assert report.ok
        assert engine.ocr.calls == [None]


@requires_geodata
class TestLanguageHints:
    @pytest.fixture
    def fusion(self, geocoder, countries):
        return FusionEngine(FusionConfig(), geocoder, countries)

    def test_turkish_markers_favour_turkey(self, fusion):
        """MECBURI ISTIKAMET OTOBUSLERI is unmistakably Turkish."""
        reading = analyse("MECBURİ İSTİKAMET OTOBÜSLERİ ğ ş ı")
        assert "tr" in reading.language_hints

        result = fusion.fuse(
            [
                RawCandidate(41.0082, 28.9784, 0.02, "stub"),   # Istanbul
                RawCandidate(48.8584, 2.2945, 0.02, "stub"),    # Paris
            ],
            script_reading=reading,
        )
        assert result.candidates[0].iso_a2 == "TR"
        turkish = [
            a for a in result.candidates[0].adjustments
            if a.source == "ocr_language"
        ]
        assert turkish and turkish[0].delta > 0

    def test_plain_ascii_produces_no_language_adjustment(self, fusion):
        reading = analyse("MAIN STREET")
        result = fusion.fuse(
            [RawCandidate(40.7580, -73.9855, 0.05, "stub")],
            script_reading=reading,
        )
        assert not [
            a for a in result.candidates[0].adjustments
            if a.source == "ocr_language"
        ]

    def test_every_adjustment_states_its_reason(self, fusion):
        reading = analyse("Straße")
        result = fusion.fuse(
            [RawCandidate(52.52, 13.405, 0.05, "stub")],
            script_reading=reading,
        )
        for a in result.candidates[0].adjustments:
            assert a.detail
