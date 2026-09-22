"""Writing-system detection from OCR text."""

from __future__ import annotations

import pytest

from mapify.evidence.script_detect import (
    SCRIPT_NAMES,
    analyse,
    script_of,
    tokens,
)


class TestScriptOf:
    @pytest.mark.parametrize(
        "char, code",
        [
            ("A", "Latn"), ("z", "Latn"), ("é", "Latn"),
            ("Д", "Cyrl"), ("ж", "Cyrl"),
            ("Ω", "Grek"), ("π", "Grek"),
            ("ع", "Arab"),
            ("א", "Hebr"),
            ("क", "Deva"),
            ("ক", "Beng"),
            ("த", "Taml"),
            ("ก", "Thai"),
            ("漢", "Hani"),
            ("ひ", "Hira"),
            ("カ", "Kana"),
            ("한", "Hang"),
            ("ა", "Geor"),
            ("Ա", "Armn"),
            ("አ", "Ethi"),
        ],
    )
    def test_classifies_letters(self, char, code):
        assert script_of(char) == code

    @pytest.mark.parametrize("char", ["1", " ", ".", "!", "\n", "€"])
    def test_non_letters_are_none(self, char):
        assert script_of(char) is None

    def test_every_code_has_a_display_name(self):
        seen = set()
        for ch in "AДΩعאकকதกひカ한აԱአ":
            code = script_of(ch)
            if code:
                seen.add(code)
        missing = seen - set(SCRIPT_NAMES)
        assert not missing, f"no display name for {missing}"


class TestAnalyse:
    def test_pure_latin(self):
        r = analyse("Rue de Rivoli")
        assert r.proportions["Latn"] == pytest.approx(1.0)
        assert r.total_letters == 11

    def test_pure_cyrillic(self):
        r = analyse("Красная площадь")
        assert r.proportions["Cyrl"] == pytest.approx(1.0)
        assert [s for s, _ in r.dominant()] == ["Cyrl"]

    def test_mixed_scripts_are_proportional(self):
        r = analyse("Москва Moscow")
        props = r.proportions
        assert props["Cyrl"] == pytest.approx(6 / 12)
        assert props["Latn"] == pytest.approx(6 / 12)

    def test_kana_promotes_han_to_japanese(self):
        """Kana beside Han means Japanese, not Chinese."""
        r = analyse("東京の駅です")
        assert "Jpan" in r.counts
        assert r.counts["Jpan"] >= r.counts.get("Hani", 0)

    def test_han_alone_stays_han(self):
        r = analyse("北京站")
        assert "Jpan" not in r.counts
        assert r.counts["Hani"] == 3

    def test_digits_and_punctuation_are_ignored(self):
        r = analyse("A1 B2 - C3!")
        assert r.total_letters == 3

    def test_empty_text(self):
        r = analyse("")
        assert r.total_letters == 0
        assert r.proportions == {}
        assert r.dominant() == []

    def test_dominant_respects_the_threshold(self):
        # One Cyrillic character among twenty Latin is below 15%.
        r = analyse("abcdefghijklmnopqrst" + "Д")
        assert [s for s, _ in r.dominant(minimum=0.15)] == ["Latn"]


class TestLanguageHints:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Łódź żółw", "pl"),
            ("Señor niño", "es"),
            ("Straße", "de"),
            ("İstanbul Şişli ğ", "tr"),
            ("Reykjavík Þingvellir", "is"),
        ],
    )
    def test_marker_characters_suggest_a_language(self, text, expected):
        hints = analyse(text).language_hints
        assert expected in hints
        assert hints[expected] == pytest.approx(max(hints.values()))

    def test_plain_ascii_gives_no_hint(self):
        assert analyse("Main Street").language_hints == {}

    def test_hints_are_normalised_to_one(self):
        hints = analyse("Łódź").language_hints
        assert max(hints.values()) == pytest.approx(1.0)


class TestTokens:
    def test_splits_on_punctuation_and_digits(self):
        assert tokens("Bengaluru 560001, Karnataka") == ["Bengaluru", "Karnataka"]

    def test_keeps_internal_hyphens(self):
        assert "Saint-Denis" in tokens("Saint-Denis 93200")

    def test_strips_edge_punctuation(self):
        assert tokens("'Paris'.") == ["Paris"]

    def test_respects_min_length(self):
        assert tokens("a bc def ghij", min_len=4) == ["ghij"]

    def test_handles_non_latin(self):
        assert "Москва" in tokens("Москва, 101000")
