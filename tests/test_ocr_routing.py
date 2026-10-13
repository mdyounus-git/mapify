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


def script_reading(option: str, called: bool = True,
                   confidence: float = 0.8,
                   ranked: list[tuple[str, float]] | None = None
                   ) -> GroupReading:
    return GroupReading(
        group_key="signage_script", group_title="Signage script",
        option_key=option, option_label=option, confidence=confidence,
        margin=0.4, called=called, constrains=True,
        ranked=ranked if ranked is not None else [(option, confidence)],
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
        cfg.models.plonk_enabled = False     # a stand-in locator, alone
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


class TestReaderSelectionThresholds:
    """Which reader to load is a cheaper decision than whether to believe it.

    Being wrong costs one model load and reads nothing extra, so the bar is
    deliberately far lower than the bar for moving a candidate's score.
    """

    @pytest.fixture
    def engine(self):
        from mapify.config import load_config
        from mapify.pipeline import AnalysisEngine

        cfg = load_config()
        cfg.network.offline_only = True
        cfg.models.geoclip_enabled = False
        cfg.models.clip_evidence_enabled = False
        cfg.models.plonk_enabled = False     # a stand-in locator, alone
        return AnalysisEngine(cfg)

    def choose(self, engine, readings=(), prior=None):
        from mapify.models.base import VisualEvidence

        return engine._choose_ocr_group(
            VisualEvidence(readings=list(readings), country_prior=prior or {})
        )

    def test_a_runner_up_script_still_selects_its_reader(self, engine):
        """Latin wins almost any mixed sign; the runner-up is the useful one."""
        reading = script_reading(
            "Latn", called=True, confidence=0.72,
            ranked=[("Latn", 0.72), ("Knda", 0.18), ("Deva", 0.05)],
        )
        assert self.choose(engine, [reading]) == ("kn", "en")

    def test_an_uncalled_script_still_selects_its_reader(self, engine):
        reading = script_reading(
            "Cyrl", called=False, confidence=0.22,
            ranked=[("Cyrl", 0.22), ("Latn", 0.20)],
        )
        assert self.choose(engine, [reading]) == ("ru", "en")

    def test_noise_below_the_floor_selects_nothing(self, engine):
        reading = script_reading(
            "Latn", called=True, confidence=0.94,
            ranked=[("Latn", 0.94), ("Thai", 0.03)],
        )
        assert self.choose(engine, [reading]) is None

    def test_a_peaked_country_prior_selects_a_latin_language_reader(self, engine):
        """EasyOCR's English model flattens Turkish diacritics away."""
        prior = {"TR": 0.55, "GR": 0.05, "BG": 0.04, "FR": 0.03, "DE": 0.02}
        assert self.choose(engine, [], prior) == ("tr", "en")

    def test_a_flat_country_prior_selects_nothing(self, engine):
        prior = {"TR": 0.05, "GR": 0.05, "BG": 0.05, "FR": 0.05, "DE": 0.05}
        assert self.choose(engine, [], prior) is None

    def test_script_beats_language(self, engine):
        """A non-Latin script is the stronger signal; do not override it."""
        reading = script_reading(
            "Cyrl", called=True, confidence=0.6, ranked=[("Cyrl", 0.6)],
        )
        prior = {"TR": 0.55, "GR": 0.05, "BG": 0.04, "FR": 0.03}
        assert self.choose(engine, [reading], prior) == ("ru", "en")

    def test_a_country_with_no_supported_language_selects_nothing(self, engine):
        # Greek is peaked, but EasyOCR has no Greek recogniser.
        prior = {"GR": 0.60, "TR": 0.04, "BG": 0.03, "IT": 0.02}
        assert self.choose(engine, [], prior) is None


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


class TestTamilAlphabet:
    """EasyOCR 1.7.2 ships a Tamil alphabet cut short after zha, so its own
    Tamil model would not load and no Tamil sign was ever read."""

    def test_the_truncated_alphabet_is_completed_once(self, monkeypatch):
        easyocr_config = pytest.importorskip("easyocr.config")
        from mapify.models import ocr
        model = easyocr_config.recognition_models["gen1"]["tamil_g1"]
        cut = model["characters"].split("வ")[0]       # as 1.7.2 ships it
        monkeypatch.setitem(model, "characters", cut)
        assert ocr._repair_easyocr_alphabets() is True
        assert len(model["characters"]) == len(cut) + 16
        assert model["characters"].endswith("்")      # the virama last
        assert ocr._repair_easyocr_alphabets() is False      # never twice

    def test_a_complete_alphabet_is_left_alone(self, monkeypatch):
        easyocr_config = pytest.importorskip("easyocr.config")
        from mapify.models import ocr
        model = easyocr_config.recognition_models["gen1"]["tamil_g1"]
        whole = model["characters"].split("வ")[0] + ocr._TAMIL_TAIL
        monkeypatch.setitem(model, "characters", whole)
        assert ocr._repair_easyocr_alphabets() is False
        assert model["characters"] == whole
