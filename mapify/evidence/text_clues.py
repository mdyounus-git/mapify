"""Clues read off the image as text, checked against public regional facts.

A calling code on a shop sign, a ".in" web address, a rupee sign, a UK
postcode, a speed limit in miles: each is read rather than inferred, so
it is independent of the image encoder -- which is exactly the kind of
evidence Phase 22 showed can improve an answer, where more readings from
the same encoder could not.

One reader serves two uses. The techniques (110, 117-121, 123, 124)
report everything it finds, weak matches included and marked as weak.
The fusion may weigh the *narrow* ones -- clues that name a handful of
countries -- but only at the weight the benchmark measured, and that
weight is zero until it has been measured.

The matchers are deliberately strict, and every guard is there because
something real tripped it: a word is not a currency ("TRY OUR NEW
MENU"), a price is not a phone number ("1.00 44"), a US phone number is
not a Japanese postcode ("555-1234"), an abbreviation is not a web
address ("Dr.In"). A missed clue costs nothing; an invented one names a
country the photograph never showed. The tables behind them live in
mapify/data/regional.py, with their own caveats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..data.countries import get_table
from ..data.regional import (BRANDS, CALLING_CODES, CURRENCIES, GENERIC_TLDS,
                             INDIA_STD, MPH_COUNTRIES, POSTCODES)

__all__ = ["Hit", "TextClue", "phone_clue", "domain_clue", "currency_clue",
           "units_clue", "postcode_clue", "brand_clue", "brand_hits", "read_clues",
           "host_of", "MIN_CONFIDENCE"]

# OCR lines below this are noise more often than text.
MIN_CONFIDENCE = 0.35


@dataclass
class Hit:
    """One match, in the OCR line it was read from."""
    line: Any                       # .text, .confidence, .box
    label: str                      # what the evidence map calls it


@dataclass
class TextClue:
    """What one kind of text says about where the photograph is."""
    kind: str                       # phone, domain, currency, units, postcode, brand
    matches: list[str]
    countries: list[str]            # the most telling first
    narrow: bool                    # names a handful of countries
    # How hard it may argue if it is allowed to steer, before the fitted
    # weight scales it. Set by judgement and stated as such: a calling
    # code is harder to misread than a brand name that trades in four
    # countries. The benchmark fits one weight for all of them.
    strength: float = 1.0
    hits: list[Hit] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def agreement(self, iso: str | None) -> float:
        """+1 if a candidate in ``iso`` fits a narrow clue, -0.6 if not.

        The penalty is smaller than the reward, as for a place name read
        off a sign: a clue can be about somewhere other than where the
        photograph was taken (a foreign brand's sign, a tourist's price
        list), and absence of fit is weaker evidence than fit.
        """
        if not self.narrow or not iso:
            return 0.0
        return 1.0 if iso in self.countries else -0.6

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "matches": self.matches,
                "countries": sorted(set(self.countries)), "narrow": self.narrow,
                "strength": self.strength}


def _lines(lines: Iterable[Any] | None, minimum: float = MIN_CONFIDENCE) -> list[Any]:
    return [l for l in (lines or []) if getattr(l, "confidence", 0.0) >= minimum]


# ---------------------------------------------------------------- phones

# "+44 20 7946 0958", "0044 ...": a country code, then at least six more
# digits. Not "1.00 44" -- a price -- and not "00" inside a word.
_INTL = re.compile(r"(?:\+|(?<![\w.,])00)\s?(\d{1,3})(?:[\s\-.()]*\d){6,}")
# Indian mobile numbers as Indians write them: "98450 12345".
_IN_MOBILE = re.compile(r"(?<![\d+])[6-9]\d{4}[\s\-]\d{5}(?!\d)")
# Trunk-dialled "080-23456789". Weak alone: Germany writes "040 1234567".
_IN_STD = re.compile(r"(?<![\d+])0(\d{2,4})[\s\-]\d{6,8}(?!\d)")


def phone_clue(lines) -> TextClue | None:
    """Calling codes; for India, mobile number shapes and trunk codes."""
    strong, weak, countries, cities, hits = [], [], [], [], []
    for line in _lines(lines):
        t = line.text
        intl = False
        for m in _INTL.finditer(t):
            digits = m.group(1)
            for n in (3, 2, 1):
                if digits[:n] in CALLING_CODES:
                    strong.append(f"+{digits[:n]}")
                    countries += CALLING_CODES[digits[:n]]
                    hits.append(Hit(line, f"+{digits[:n]}"))
                    intl = True
                    break
        if not intl and _IN_MOBILE.search(t):
            strong.append("an Indian-style mobile number")
            countries.append("IN")
            hits.append(Hit(line, "mobile number"))
        for m in _IN_STD.finditer(t):
            code = m.group(1)
            for n in (4, 3, 2):
                if code[:n] in INDIA_STD:
                    weak.append(f"0{code[:n]} ({INDIA_STD[code[:n]]}?)")
                    cities.append(INDIA_STD[code[:n]])
                    hits.append(Hit(line, f"0{code[:n]}"))
                    break
    if not strong and not weak:
        return None
    extra = {"cities": sorted(set(cities))} if cities else {}
    if not strong:
        # Trunk codes alone: other countries write numbers the same way.
        return TextClue("phone", weak, ["IN"], narrow=False, strength=0.15,
                        hits=hits, extra={**extra, "weak_only": True})
    return TextClue("phone", strong + weak, countries,
                    narrow=len(set(countries)) <= 3, strength=1.0, hits=hits,
                    extra=extra)


# --------------------------------------------------------------- domains

def host_of(token: str) -> str | None:
    """The host name in a web or email address, or None.

    Without "www.", "http" or "@", only an all-lowercase token counts:
    "Dr.In" and "St.Mary" are abbreviations, not addresses.
    """
    tok = token.strip("()[]{}<>,;:!?\"'")
    if "@" in tok:
        host = tok.rsplit("@", 1)[1]
    else:
        explicit = tok.lower().startswith(("http://", "https://", "www."))
        host = re.sub(r"(?i)^https?://", "", tok).split("/", 1)[0]
        if not explicit and (host != host.lower() or len(host) < 6):
            return None
    host = host.lower().rstrip(".")
    if not re.fullmatch(r"(?:[a-z0-9][a-z0-9\-]*\.)+[a-z]{2,6}", host):
        return None
    labels = host.split(".")
    if labels[0] == "www":
        labels = labels[1:]
    if len(labels) < 2 or len(labels[-2]) < 2:
        return None
    return ".".join(labels)


def domain_clue(lines) -> TextClue | None:
    """Country-code top-level domains; generic and resold codes ignored."""
    table = get_table()
    hosts, countries, hits = [], [], []
    for line in _lines(lines):
        for tok in line.text.split():
            host = host_of(tok)
            if host is None:
                continue
            tld = host.rsplit(".", 1)[1]
            iso = "GB" if tld == "uk" else tld.upper()
            if tld in GENERIC_TLDS or len(tld) != 2 or table.get(iso) is None:
                continue
            hosts.append(host)
            countries.append(iso)
            hits.append(Hit(line, host))
    if not hosts:
        return None
    return TextClue("domain", hosts, countries, narrow=len(set(countries)) <= 2,
                    strength=0.8, hits=hits)


# -------------------------------------------------------------- currency

def currency_clue(lines) -> TextClue | None:
    """Currency signs and codes in prices; a bare "$" is reported as weak."""
    found, hits = [], []
    for line in _lines(lines):
        for pat, cs, narrow in CURRENCIES:
            m = re.search(pat, line.text)
            if m:
                found.append((m.group(0).strip(), cs, narrow))
                hits.append(Hit(line, m.group(0).strip()))
    if not found:
        return None
    narrow_hits = [h for h in found if h[2]]
    use = narrow_hits or found
    countries = [c for _, cs, _ in use for c in cs]
    seen = list(dict.fromkeys(s for s, _, _ in use))
    return TextClue("currency", seen, countries,
                    narrow=bool(narrow_hits) and len(set(countries)) <= 3,
                    strength=1.0, hits=hits)


# ----------------------------------------------------------------- units

# Units only beside a number: "Miles" alone is a surname.
_UNITS = {"miles": re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s?(?:mph|miles?)\b"),
          "kilometres": re.compile(r"(?i)\b\d+(?:[.,]\d+)?\s?(?:km/?h|kmph|kms?)\b"),
          "fahrenheit": re.compile(r"\d\s?°\s?F\b"),
          "celsius": re.compile(r"\d\s?°\s?C\b")}
_FAHRENHEIT = ["US", "BS", "BZ", "KY", "LR", "PW", "PR", "GU", "VI", "AS", "MP"]


def units_clue(lines) -> TextClue | None:
    """Miles and degrees Fahrenheit narrow the world; metric hardly at all."""
    seen, hits = [], []
    for line in _lines(lines):
        for k, rx in _UNITS.items():
            m = rx.search(line.text)
            if m:
                seen.append(k)
                hits.append(Hit(line, m.group(0)))
    if not seen:
        return None
    seen = list(dict.fromkeys(seen))
    if "miles" in seen or "fahrenheit" in seen:
        # A long list, but of small territories: miles are the US, the UK,
        # Liberia and Myanmar, which is narrow.
        majors = ["US", "GB", "LR", "MM"]
        countries = (majors + sorted(MPH_COUNTRIES - set(majors)) if "miles" in seen
                     else _FAHRENHEIT)
        return TextClue("units", seen, countries, narrow=True, strength=0.8, hits=hits)
    return TextClue("units", seen, [], narrow=False, strength=0.0, hits=hits,
                    extra={"metric": True})


# -------------------------------------------------------------- postcodes

def postcode_clue(lines) -> TextClue | None:
    """Postcode shapes distinctive enough to name a country, in context."""
    found, countries, hits = [], [], []
    for line in _lines(lines, 0.4):
        for iso, label, rx in POSTCODES:
            m = rx.search(line.text)
            if m:
                found.append(f"{m.group(0).strip()} ({label})")
                countries.append(iso)
                hits.append(Hit(line, label))
    if not found:
        return None
    return TextClue("postcode", found, countries, narrow=len(set(countries)) <= 2,
                    strength=1.0, hits=hits)


# ----------------------------------------------------------------- brands

def brand_hits(text: str, kinds: set[str]) -> list[tuple[str, str, list[str]]]:
    """(brand, kind, countries) for every listed brand named in ``text``."""
    low = text.lower()
    out = []
    for brand, (kind, countries) in BRANDS.items():
        if kind in kinds and re.search(r"(?<![a-z])" + re.escape(brand) + r"(?![a-z])",
                                       low):
            out.append((brand, kind, countries))
    return out


def brand_clue(lines, kinds: set[str]) -> TextClue | None:
    """Brands named in the text, against the countries they operate in."""
    usable = _lines(lines)
    found = brand_hits("\n".join(l.text for l in usable), kinds)
    if not found:
        return None
    sets = [set(c) for _, _, c in found]
    countries = set.intersection(*sets) or set.union(*sets)
    hits = []
    for line in usable:
        low = line.text.lower()
        for b, _, _ in found:
            if re.search(r"(?<![a-z])" + re.escape(b) + r"(?![a-z])", low):
                hits.append(Hit(line, b))
    return TextClue("brand", [b for b, _, _ in found], sorted(countries),
                    narrow=len(countries) <= 4, strength=0.6, hits=hits,
                    extra={"kinds": sorted({k for _, k, _ in found})})


# ------------------------------------------------------------------- all

_BRAND_KINDS = {"telecom", "retail", "fuel", "bank"}


def read_clues(lines) -> list[TextClue]:
    """Every clue in the text that names countries at all."""
    clues = [phone_clue(lines), domain_clue(lines), currency_clue(lines),
             units_clue(lines), postcode_clue(lines), brand_clue(lines, _BRAND_KINDS)]
    return [c for c in clues if c is not None and c.countries]
