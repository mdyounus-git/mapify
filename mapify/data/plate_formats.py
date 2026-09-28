"""Vehicle registration formats, for reading a plate rather than ranking it.

The pipeline already scores the *look* of a plate -- colours, proportions,
the blue EU band -- as a zero-shot group. This is the other half of
technique 11: taking the characters OCR recovered and asking which
jurisdictions issue registrations in that shape.

These are public facts about registration schemes, written out by hand.
No dataset is copied.

Why matches are usually plural
------------------------------

Registration formats collide constantly. Two letters, three digits, two
letters is France, and also Italy, and also several others. A tool that
picked one and named it would be inventing precision. So every matching
jurisdiction is returned, the caller reports them all, and the only
strongly informative case is the one where exactly one scheme fits.

Scope: the schemes below are the current national formats for countries
where plates are widely photographed. Historic formats, regional and
specialist series, and the United States -- where every state runs its
own scheme and most are unconstrained -- are deliberately absent rather
than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["PlateFormat", "FORMATS", "looks_like_plate", "match_plate"]


@dataclass(frozen=True)
class PlateFormat:
    countries: frozenset[str]
    label: str          # how the scheme is written, e.g. "AB-123-CD"
    pattern: re.Pattern[str]
    note: str = ""


def _p(countries: str, label: str, pattern: str, note: str = "") -> PlateFormat:
    return PlateFormat(countries=frozenset(countries.split()), label=label,
                       pattern=re.compile(pattern), note=note)


# Matched against the plate text with spaces and hyphens removed and
# letters upper-cased, so a scheme is written here without separators.
FORMATS: tuple[PlateFormat, ...] = (
    _p("GB", "AB12 CDE", r"^[A-Z]{2}[0-9]{2}[A-Z]{3}$",
       "current UK scheme; the two digits are the registration period"),
    _p("GB", "A123 BCD", r"^[A-Z][0-9]{1,3}[A-Z]{3}$", "UK prefix scheme, 1983-2001"),
    _p("FR IT", "AB-123-CD", r"^[A-Z]{2}[0-9]{3}[A-Z]{2}$",
       "France since 2009 and Italy since 1994 use the same shape"),
    _p("NL", "12-ABC-3", r"^[0-9]{2}[A-Z]{3}[0-9]$"),
    _p("NL", "AB-12-34", r"^[A-Z]{2}[0-9]{2}[0-9]{2}$"),
    _p("ES", "1234 ABC", r"^[0-9]{4}[A-Z]{3}$"),
    _p("PT", "AB-12-CD", r"^[A-Z]{2}[0-9]{2}[A-Z]{2}$"),
    _p("DE", "B-AB 1234", r"^[A-ZÄÖÜ]{1,3}[A-Z]{1,2}[0-9]{1,4}$",
       "district code, letters, then digits"),
    _p("PL", "WX 12345", r"^[A-Z]{2,3}[A-Z0-9]{4,5}$"),
    _p("BE", "1-ABC-123", r"^[0-9][A-Z]{3}[0-9]{3}$"),
    _p("CH", "ZH 123456", r"^(AG|AI|AR|BE|BL|BS|FR|GE|GL|GR|JU|LU|NE|NW|OW"
                          r"|SG|SH|SO|SZ|TG|TI|UR|VD|VS|ZG|ZH)[0-9]{1,6}$",
       "the two letters are the canton"),
    _p("AT", "W 12345A", r"^[A-Z]{1,2}[0-9]{1,5}[A-Z]{0,2}$"),
    _p("SE", "ABC 12A", r"^[A-Z]{3}[0-9]{2}[A-Z0-9]$"),
    _p("NO", "AB 12345", r"^[A-Z]{2}[0-9]{5}$"),
    _p("DK", "AB 12 345", r"^[A-Z]{2}[0-9]{5}$"),
    _p("FI", "ABC-123", r"^[A-Z]{3}[0-9]{1,3}$"),
    _p("IE", "12-D-12345", r"^[0-9]{2,3}[A-Z]{1,2}[0-9]{1,6}$",
       "year, county letter, sequence"),
    _p("GR", "ABC-1234", r"^[A-Z]{3}[0-9]{4}$"),
    _p("TR", "34 ABC 123", r"^(0[1-9]|[1-7][0-9]|8[01])[A-Z]{1,3}[0-9]{2,4}$",
       "opens with the province number, 01 to 81"),
    _p("RU", "A123BC 45", r"^[ABEKMHOPCTYX][0-9]{3}[ABEKMHOPCTYX]{2}[0-9]{2,3}$",
       "only the twelve Cyrillic letters that look the same in Latin"),
    _p("UA", "AA1234BB", r"^[ABEIKMHOPCTX]{2}[0-9]{4}[ABEIKMHOPCTX]{2}$"),
    _p("IN", "MH 12 AB 1234", r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$",
       "state code, district, series, number"),
    _p("PK", "ABC-123", r"^[A-Z]{2,3}[0-9]{2,4}$"),
    _p("BD", "DHAKA METRO", r"^[A-Z]{2,3}[0-9]{2}[0-9]{4}$"),
    _p("LK", "ABC-1234", r"^[A-Z]{2,3}[0-9]{4}$"),
    _p("CN", "京A12345", r"^[一-鿿][A-Z][A-Z0-9]{5}$",
       "a province character, a city letter, then five characters"),
    _p("JP", "品川 300 あ 12-34", r"^[一-鿿]{2,4}[0-9]{2,3}"
                                  r"[぀-ゟ][0-9]{1,4}$",
       "region in kanji, class number, a hiragana, then the serial"),
    _p("KR", "12가3456", r"^[0-9]{2,3}[가-힯][0-9]{4}$"),
    _p("TH", "1กก 1234", r"^[0-9]{0,2}[฀-๿]{2,3}[0-9]{1,4}$"),
    _p("SG", "SBA1234A", r"^S[A-Z]{2}[0-9]{1,4}[A-Z]$"),
    _p("MY", "WXY 1234", r"^[A-Z]{1,3}[0-9]{1,4}[A-Z]?$"),
    _p("ID", "B 1234 ABC", r"^[A-Z]{1,2}[0-9]{1,4}[A-Z]{1,3}$"),
    _p("VN", "51A-123.45", r"^[0-9]{2}[A-Z][0-9]{4,5}$"),
    _p("AE", "A 12345", r"^[A-Z]?[0-9]{1,5}$",
       "emirate code and number; very unconstrained"),
    _p("SA", "ABC 1234", r"^[A-Z]{3}[0-9]{3,4}$"),
    _p("ZA", "AB 12 CD GP", r"^[A-Z]{2}[0-9]{2}[A-Z]{2}(GP|WP|EC|NW|ZN|MP|NC|FS|L)$",
       "ends in the province code"),
    _p("NG", "ABC 123 DE", r"^[A-Z]{3}[0-9]{3}[A-Z]{2}$"),
    _p("KE", "KAA 123A", r"^K[A-Z]{2}[0-9]{3}[A-Z]$"),
    _p("EG", "أ ب ج 123", r"^[؀-ۿ]{2,3}[0-9٠-٩]{3,4}$"),
    _p("MA", "12345-A-6", r"^[0-9]{1,5}[A-Z؀-ۿ][0-9]{1,2}$"),
    _p("BR", "ABC1D23", r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$",
       "Mercosur scheme; also Argentina, Paraguay and Uruguay"),
    _p("BR", "ABC-1234", r"^[A-Z]{3}[0-9]{4}$", "Brazilian scheme before 2018"),
    _p("AR", "AB 123 CD", r"^[A-Z]{2}[0-9]{3}[A-Z]{2}$", "Mercosur scheme"),
    _p("MX", "ABC-12-34", r"^[A-Z]{3}[0-9]{2}[0-9]{2}$"),
    _p("CA", "ABCD 123", r"^[A-Z]{4}[0-9]{3}$", "Ontario passenger series"),
    _p("AU", "ABC 12D", r"^[A-Z]{3}[0-9]{2}[A-Z]$", "New South Wales"),
    _p("AU", "1AB 2CD", r"^[0-9][A-Z]{2}[0-9][A-Z]{2}$", "Victoria"),
    _p("NZ", "ABC123", r"^[A-Z]{3}[0-9]{3}$"),
)

_CLEAN = re.compile(r"[\s\-·.·]+")
# A plate has to have both kinds of character, or nearly every short word
# in the image would be a candidate.
_HAS_DIGIT = re.compile(r"[0-9٠-٩]")
_HAS_LETTER = re.compile(r"[A-Za-zЀ-ӿ一-鿿"
                         r"぀-ヿ가-힯؀-ۿ฀-๿]")
_PLATE_CHARS = re.compile(r"^[A-Z0-9Ѐ-ӿ一-鿿぀-ヿ"
                          r"가-힯؀-ۿ٠-٩"
                          r"฀-๿]+$")


def normalise(text: str) -> str:
    """Strip separators and upper-case, the form the patterns expect."""
    return _CLEAN.sub("", text).upper()


def looks_like_plate(text: str, box: tuple[float, float, float, float] | None = None
                     ) -> bool:
    """Could this piece of recognised text be a registration plate?

    A deliberately loose gate whose job is to keep shop names and prices
    out, not to decide anything. What follows it -- matching against the
    schemes -- is where a claim is actually made.
    """
    cleaned = normalise(text)
    if not 4 <= len(cleaned) <= 10:
        return False
    if not _PLATE_CHARS.match(cleaned):
        return False
    if not (_HAS_DIGIT.search(cleaned) and _HAS_LETTER.search(cleaned)):
        return False
    # All digits but for a stray letter is a price, a phone number or a
    # house number far more often than a registration.
    letters = sum(1 for c in cleaned if _HAS_LETTER.match(c))
    if letters < 1 or letters == len(cleaned):
        return False
    if box is not None:
        x0, y0, x1, y1 = box
        w, h = abs(x1 - x0), abs(y1 - y0)
        if h <= 0:
            return False
        # Plate text sits in a wide, short box. Anything squarer is a
        # sign, and anything spanning the frame is a banner.
        if not 1.2 <= (w / h) <= 12.0:
            return False
        if w > 0.6:
            return False
    return True


def match_plate(text: str) -> list[PlateFormat]:
    """Every registration scheme whose format the text fits."""
    cleaned = normalise(text)
    if not cleaned:
        return []
    return [f for f in FORMATS if f.pattern.match(cleaned)]
