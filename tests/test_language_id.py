"""The language of recognised text, and the countries that speak it.

A tiny fastText model is trained here from a few labelled sentences, and a
tiny CLDR file written, so nothing needs the real 1.7 GB model or a network.
"""

from __future__ import annotations

import pytest

fasttext = pytest.importorskip("fasttext")

from mapify.evidence.language_id import LanguageIdentifier, countries_speaking  # noqa: E402

CLDR_DATA = """<supplementalData>
  <codeMappings><languageCodes type="tr" alpha3="tur"/></codeMappings>
  <territoryInfo>
    <territory type="NG" population="230000000">
      <languagePopulation type="en" populationPercent="53"/>
      <languagePopulation type="ha" populationPercent="13"/>
      <languagePopulation type="ha_Arab" populationPercent="1"/>
    </territory>
    <territory type="NE" population="26000000">
      <languagePopulation type="ha" populationPercent="49"/>
    </territory>
    <territory type="TZ" population="65000000">
      <languagePopulation type="sw" populationPercent="88"/>
    </territory>
    <territory type="TR" population="85000000">
      <languagePopulation type="tr" populationPercent="88"/>
    </territory>
  </territoryInfo>
</supplementalData>"""
CLDR_META = """<supplementalData><metadata><alias>
  <languageAlias type="hau" replacement="ha"/>
  <languageAlias type="swh" replacement="sw" reason="macrolanguage"/>
  <languageAlias type="swc" replacement="sw_CD"/>
</alias></metadata></supplementalData>"""

TRAINING = {
    "swh_Latn": ["karibu sana duka la dawa hapa", "habari za asubuhi rafiki",
                 "duka la vitabu karibu na soko", "asante sana kwa chakula"],
    "tur_Latn": ["mecburi istikamet otobusleri disinda", "eczane her gun acik",
                 "otobus duragi sol tarafta", "hos geldiniz efendim"],
}


@pytest.fixture
def setup(tmp_path):
    cldr = tmp_path / "cldr"
    cldr.mkdir()
    (cldr / "supplementalData.xml").write_text(CLDR_DATA, encoding="utf-8")
    (cldr / "supplementalMetadata.xml").write_text(CLDR_META, encoding="utf-8")
    train = tmp_path / "train.txt"
    train.write_text("".join(f"__label__{lab} {s}\n" for lab, ss in TRAINING.items()
                             for s in ss * 20), encoding="utf-8")
    model = fasttext.train_supervised(str(train), epoch=30, lr=1.0, minn=2, maxn=4,
                                      verbose=0)
    path = tmp_path / "model.bin"
    model.save_model(str(path))
    return LanguageIdentifier(path, cldr), cldr


def test_a_language_and_the_countries_that_speak_it(setup):
    lid, _ = setup
    got = lid.identify("eczane her gun acik, otobus duragi sol tarafta")
    assert got["language"] == "tur" and got["model"] == "GlotLID"
    assert got["countries"][0]["country"] == "TR"


def test_capitals_are_read_as_ordinary_text(setup):
    """In capitals the real model named Swahili as Hausa, with confidence."""
    lid, _ = setup
    got = lid.identify("KARIBU SANA DUKA LA DAWA HAPA")
    assert got and got["language"] == "swh"
    assert got["countries"][0]["country"] == "TZ"           # swh is CLDR's sw


def test_too_little_text_is_not_guessed_at(setup):
    lid, _ = setup
    assert lid.identify("OK 24") is None
    assert lid.identify("") is None


def test_one_language_in_two_scripts_is_added_up(setup):
    """Nigeria lists Hausa and Hausa in Arabic script as separate rows."""
    _, cldr = setup
    shares = dict(countries_speaking("hau", cldr_dir=cldr))
    ng, ne = 230e6 * 0.14, 26e6 * 0.49
    assert shares["NG"] == pytest.approx(ng / (ng + ne))
    assert list(dict(countries_speaking("swc", cldr_dir=cldr))) == ["TZ"]   # sw_CD -> sw


def test_without_the_model_nothing_is_claimed(tmp_path):
    assert LanguageIdentifier(tmp_path / "missing.bin").identify("karibu sana duka") is None
