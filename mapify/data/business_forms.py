"""Company legal forms, and the countries whose law defines them.

A shop sign reading "Müller GmbH" carries a fact the image encoder
cannot supply: GmbH is a form of company that exists in German,
Austrian and Swiss law and essentially nowhere else. Unlike a roof
shape or a bollard, this is not another opinion about the same
embedding -- it is text read off the image, checked against statute.

These are public legal facts, hand-maintained. No dataset is copied.

Scope and its limits
--------------------

The table covers the forms that appear on shopfronts and vehicles
often enough to be worth matching, not every form in every companies
act. A suffix maps to the countries whose law provides it, which is
usually more than one: "Ltd" alone is nearly the whole Commonwealth
and says little, while "Sdn Bhd" is Malaysia and Brunei and says a
great deal. ``discriminating`` records which is which, so a reader is
never shown a weak hint dressed as a strong one.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LegalForm", "FORMS", "find_forms"]


@dataclass(frozen=True)
class LegalForm:
    suffix: str                 # as written, for matching
    label: str                  # as shown to a reader
    countries: frozenset[str]   # ISO-3166 alpha-2
    note: str = ""

    # Above this many countries, a match stops being worth acting on.
    # Eight of roughly two hundred is a real narrowing; "Ltd" at
    # nineteen, spread over five continents, is not. The line is drawn
    # by how much the world shrinks, not by how exotic the suffix looks:
    # GmbH covers five countries and SARL seven, and both are genuinely
    # informative, which is why the cut is not lower.
    NARROWS_TO = 8

    @property
    def discriminating(self) -> bool:
        """Does matching this actually narrow the world usefully?"""
        return len(self.countries) <= self.NARROWS_TO


def _f(suffix: str, label: str, countries: str, note: str = "") -> LegalForm:
    return LegalForm(suffix=suffix, label=label,
                     countries=frozenset(countries.split()), note=note)


FORMS: tuple[LegalForm, ...] = (
    # ---- German-speaking -------------------------------------------
    _f("gmbh", "GmbH — limited company (German law)", "DE AT CH LI LU"),
    _f("mbh", "mbH — limited company (German law)", "DE AT CH"),
    _f("ag", "AG — stock corporation (German law)", "DE AT CH LI"),
    _f("kg", "KG — limited partnership (German law)", "DE AT CH"),
    _f("ohg", "OHG — general partnership (German law)", "DE AT"),
    _f("gbr", "GbR — civil-law partnership (Germany)", "DE"),
    _f("ug", "UG — entrepreneurial company (Germany)", "DE"),
    _f("eg", "eG — registered cooperative (Germany)", "DE"),
    # ---- Nordic ----------------------------------------------------
    _f("oy", "Oy — limited company (Finland)", "FI"),
    _f("oyj", "Oyj — public company (Finland)", "FI"),
    _f("ab", "AB — limited company (Sweden)", "SE FI",
       "also Finland, where Swedish is official"),
    _f("as", "AS — limited company (Norway/Denmark)", "NO DK"),
    _f("asa", "ASA — public company (Norway)", "NO"),
    _f("aps", "ApS — limited company (Denmark)", "DK"),
    _f("ehf", "ehf — limited company (Iceland)", "IS"),
    _f("hf", "hf — public company (Iceland)", "IS"),
    # ---- Low Countries --------------------------------------------
    _f("bv", "BV — limited company (Netherlands)", "NL"),
    _f("nv", "NV — public company (Netherlands/Belgium)", "NL BE"),
    _f("vof", "VOF — general partnership (Netherlands)", "NL BE"),
    _f("bvba", "BVBA — limited company (Belgium)", "BE"),
    _f("sprl", "SPRL — limited company (Belgium, French)", "BE"),
    # ---- Romance ---------------------------------------------------
    _f("sarl", "SARL — limited company (French law)", "FR LU MA DZ TN SN CI"),
    _f("sas", "SAS — simplified joint stock (France)", "FR"),
    _f("eurl", "EURL — single-member limited (France)", "FR"),
    _f("srl", "S.r.l. — limited company (Italy/Romania)", "IT RO"),
    _f("spa", "S.p.A. — public company (Italy)", "IT"),
    _f("snc", "S.n.c. — general partnership (Italy/France)", "IT FR"),
    _f("sl", "S.L. — limited company (Spain)", "ES"),
    _f("sa de cv", "S.A. de C.V. — variable capital (Mexico)", "MX"),
    _f("sapi de cv", "S.A.P.I. de C.V. (Mexico)", "MX"),
    _f("lda", "Lda — limited company (Portugal)", "PT AO MZ"),
    _f("ltda", "Ltda — limited company (Brazil/Chile)", "BR CL CO"),
    _f("eireli", "EIRELI — single-member company (Brazil)", "BR"),
    _f("srl de cv", "S.R.L. de C.V.", "MX"),
    # ---- Central and Eastern Europe --------------------------------
    _f("sp z oo", "Sp. z o.o. — limited company (Poland)", "PL"),
    _f("sa", "S.A. — public company (civil-law countries)",
       "FR ES PT PL BE CH LU BR AR MX CL CO PE GR RO",
       "very widespread; weak on its own"),
    _f("sro", "s.r.o. — limited company (Czechia/Slovakia)", "CZ SK"),
    _f("kft", "Kft. — limited company (Hungary)", "HU"),
    _f("zrt", "Zrt. — private company (Hungary)", "HU"),
    _f("dood", "OOD — limited company (Bulgaria)", "BG"),
    _f("doo", "d.o.o. — limited company (former Yugoslavia)",
       "HR RS SI BA ME MK"),
    _f("dd", "d.d. — public company (former Yugoslavia)", "HR SI BA"),
    _f("ooo", "OOO — limited company (Russia)", "RU"),
    _f("zao", "ZAO — closed company (Russia)", "RU"),
    _f("pao", "PAO — public company (Russia)", "RU"),
    _f("tov", "TOV — limited company (Ukraine)", "UA"),
    # ---- Anglophone ------------------------------------------------
    _f("pty ltd", "Pty Ltd — proprietary company", "AU ZA NA BW ZW"),
    _f("pty", "Pty — proprietary company", "AU ZA NA"),
    _f("pvt ltd", "Pvt Ltd — private limited", "IN PK BD LK NP"),
    _f("private limited", "Private Limited", "IN PK BD LK SG NP"),
    _f("pte ltd", "Pte Ltd — private limited (Singapore)", "SG"),
    _f("sdn bhd", "Sdn Bhd — private limited (Malaysia/Brunei)", "MY BN"),
    _f("bhd", "Bhd — public limited (Malaysia/Brunei)", "MY BN"),
    _f("llc", "LLC — limited liability company", "US AE SA OM QA BH KW"),
    _f("inc", "Inc. — incorporated", "US CA PH"),
    _f("corp", "Corp. — corporation", "US CA PH"),
    _f("plc", "PLC — public limited company", "GB IE NG GH KE"),
    _f("llp", "LLP — limited liability partnership", "GB US IN SG"),
    _f("ulc", "ULC — unlimited liability company", "CA"),
    _f("ltd", "Ltd — limited company",
       "GB IE IN PK BD LK NG KE GH ZA AU NZ CA SG MY HK TZ UG ZM",
       "used across the Commonwealth; weak on its own"),
    # ---- East and South-East Asia ----------------------------------
    _f("kk", "K.K. — stock company (Japan)", "JP"),
    _f("kabushiki kaisha", "Kabushiki Kaisha (Japan)", "JP"),
    _f("yugen kaisha", "Yugen Kaisha (Japan)", "JP"),
    _f("godo kaisha", "Godo Kaisha (Japan)", "JP"),
    _f("co ltd", "Co., Ltd. — company limited",
       "JP KR CN TW TH VN HK SG", "common across East Asia"),
    _f("pt", "PT — limited company (Indonesia)", "ID"),
    _f("cv", "CV — partnership (Indonesia)", "ID NL"),
    _f("tbk", "Tbk — publicly listed (Indonesia)", "ID"),
    _f("bhd sdn", "Sdn Bhd (Malaysia)", "MY"),
    # ---- Middle East and elsewhere ---------------------------------
    _f("wll", "WLL — with limited liability (Gulf)", "BH KW QA"),
    _f("fzco", "FZCO — free-zone company (UAE)", "AE"),
    _f("fze", "FZE — free-zone establishment (UAE)", "AE"),
    _f("jsc", "JSC — joint stock company", "RU KZ AZ AM GE UA VN"),
    _f("ae", "A.E. — public company (Greece)", "GR"),
    _f("epe", "E.P.E. — limited company (Greece)", "GR"),
    _f("ike", "I.K.E. — private company (Greece)", "GR"),
    _f("as sirketi", "A.Ş. — joint stock company (Turkey)", "TR"),
    _f("ltd sti", "Ltd. Şti. — limited company (Turkey)", "TR"),
    _f("sti", "Şti. — company (Turkey)", "TR"),
)

# Longest suffixes first, so "pty ltd" is not swallowed by "ltd".
_ORDERED = tuple(sorted(FORMS, key=lambda f: -len(f.suffix)))

_STRIP = str.maketrans({".": "", ",": "", "(": " ", ")": " ",
                        "-": " ", "/": " ", "'": "", "’": ""})


def _normalise(text: str) -> str:
    return " ".join(text.translate(_STRIP).lower().split())


def find_forms(text: str) -> list[LegalForm]:
    """Legal forms appearing in a line of text, longest match first.

    Matching is on whole words after punctuation is dropped, so "Ltd."
    and "LTD" both hit and "Altdorf" does not.
    """
    cleaned = _normalise(text)
    if not cleaned:
        return []
    padded = f" {cleaned} "
    out: list[LegalForm] = []
    seen: set[str] = set()
    consumed: list[tuple[int, int]] = []
    for form in _ORDERED:
        needle = f" {form.suffix} "
        start = padded.find(needle)
        if start < 0:
            continue
        end = start + len(needle)
        # A longer form already claimed these words: "pty ltd" beats "ltd".
        if any(start < ce and cs < end for cs, ce in consumed):
            continue
        if form.label in seen:
            continue
        consumed.append((start, end))
        seen.add(form.label)
        out.append(form)
    return out
