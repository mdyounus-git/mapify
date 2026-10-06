"""The shared reader of text clues, and the fusion's use of it.

The reader is tested directly here; the techniques that report its
findings are tested in test_techniques_p678.py. The fusion hook is
tested both ways: at weight zero -- where it stays until the benchmark
says otherwise -- nothing may change, and above zero a narrow clue must
favour the candidates it fits and only those.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import requires_geodata
from mapify.config import FusionConfig
from mapify.evidence.fusion import FusionEngine
from mapify.evidence.text_clues import (TextClue, currency_clue, phone_clue,
                                        read_clues, units_clue)
from mapify.models.base import OcrLine, OcrResult, RawCandidate


def _lines(*texts, confidence=0.9):
    return [SimpleNamespace(text=t, confidence=confidence, box=None) for t in texts]


def raw(lat, lon, score):
    return RawCandidate(latitude=lat, longitude=lon, score=score, source="geoclip")


# ------------------------------------------------------------- the reader

def test_read_clues_collects_every_kind_that_names_countries():
    clues = read_clues(_lines("Call +44 20 7946 0958", "Tea £2", "30 mph",
                              "London SW1A 1AA", "www.shop.co.uk"))
    kinds = {c.kind for c in clues}
    assert kinds == {"phone", "currency", "units", "postcode", "domain"}
    assert all(c.narrow for c in clues)
    assert all("GB" in c.countries for c in clues)


def test_metric_units_name_no_country_and_are_left_out():
    assert units_clue(_lines("Chennai 42 km")).extra["metric"]
    assert read_clues(_lines("Chennai 42 km")) == []


def test_low_confidence_lines_are_ignored():
    assert read_clues(_lines("Call +44 20 7946 0958", confidence=0.2)) == []


def test_a_weak_clue_never_argues():
    trunk = phone_clue(_lines("Ph: 040 1234567"))        # Hyderabad, or Hamburg
    assert not trunk.narrow and trunk.agreement("DE") == 0.0
    dollar = currency_clue(_lines("Coffee $5"))
    assert not dollar.narrow and dollar.agreement("IN") == 0.0


def test_a_narrow_clue_rewards_fit_more_than_it_punishes_misfit():
    rupee = currency_clue(_lines("Chai ₹ 20"))
    assert rupee.agreement("IN") == 1.0
    assert rupee.agreement("GB") == -0.6
    assert rupee.agreement(None) == 0.0


@pytest.mark.parametrize("text, countries", [
    ("Call (212) 555-0123", {"US", "CA"}),
    ("212-555-0123", {"US", "CA"}),
    ("+1 212 555 0123", {"US", "CA"}),
    ("01 23 45 67 89", {"FR", "MA", "CI"}),
    ("(11) 91234-5678", {"BR"}),
    ("(11) 2345-6789", {"BR", "AR"}),
    ("(02) 9876 5432", {"AU"}),
    ("0412 345 678", {"AU"}),
    ("03-1234-5678", {"JP"}),
    ("090-1234-5678", {"JP"}),
    ("010-1234-5678", {"KR"}),
    ("138 0013 8000", {"CN"}),
    ("Tel. 612 345 678", {"ES", "PT", "PL"}),
])
def test_distinctive_national_phone_formats_name_their_countries(text, countries):
    clue = phone_clue(_lines(text))
    assert clue is not None and clue.narrow
    assert countries <= set(clue.countries)


@pytest.mark.parametrize("text", [
    "Tel 312 555 0123",            # spaces: Italian and Colombian mobiles look so
    "333 123 4567",
    "Rp 750 000 000",              # a grouped price, not a phone number
    "Price 523 456 789",
    "ISBN 978-3-16-148410-0",
    "2023-12-25",
])
def test_lookalikes_are_not_phone_numbers(text):
    assert phone_clue(_lines(text)) is None


def test_the_strengths_rank_hard_clues_above_brands():
    clue = lambda kind: next(c for c in read_clues(_lines(
        "Call +91 98450 12345", "Chai ₹ 20", "airtel")) if c.kind == kind)
    assert clue("phone").strength >= clue("brand").strength


# ------------------------------------------------------------- the fusion

@requires_geodata
class TestFusionHook:
    def _engine(self, geocoder, countries, w):
        cfg = FusionConfig()
        cfg.w_text_clue = w
        return FusionEngine(cfg, geocoder, countries)

    def _ocr(self, *texts):
        return OcrResult(lines=[OcrLine(text=t, confidence=0.9) for t in texts])

    def test_at_weight_zero_nothing_changes(self, geocoder, countries):
        engine = self._engine(geocoder, countries, 0.0)
        result = engine.fuse([raw(51.5074, -0.1278, 0.02), raw(12.9716, 77.5946, 0.01)],
                             ocr=self._ocr("Chai ₹ 20", "Call +91 98450 12345"))
        assert not [a for c in result.candidates for a in c.adjustments
                    if a.source.startswith("text_")]
        assert result.candidates[0].iso_a2 == "GB"        # the prior decides

    def test_a_narrow_clue_can_turn_the_ranking(self, geocoder, countries):
        engine = self._engine(geocoder, countries, 1.0)
        result = engine.fuse([raw(51.5074, -0.1278, 0.02), raw(12.9716, 77.5946, 0.01)],
                             ocr=self._ocr("Chai ₹ 20", "Call +91 98450 12345"))
        assert result.candidates[0].iso_a2 == "IN"
        india = result.candidates[0]
        kinds = {a.source for a in india.adjustments if a.source.startswith("text_")}
        assert kinds == {"text_currency", "text_phone"}
        uk = next(c for c in result.candidates if c.iso_a2 == "GB")
        assert all(a.delta < 0 for a in uk.adjustments if a.source.startswith("text_"))

    def test_a_weak_clue_moves_nothing_even_when_switched_on(self, geocoder, countries):
        engine = self._engine(geocoder, countries, 2.0)
        result = engine.fuse([raw(51.5074, -0.1278, 0.02), raw(12.9716, 77.5946, 0.01)],
                             ocr=self._ocr("Coffee $5", "Ph: 040 1234567"))
        assert not [a for c in result.candidates for a in c.adjustments
                    if a.source.startswith("text_")]
