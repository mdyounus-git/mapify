"""Writing-system detection from recognised text.

Pure standard library: we classify codepoints into ISO 15924 script codes and
look for language-marking characters.  This runs on OCR output, so it only
tells us what script the *recognised* text was in -- the CLIP script probe in
:mod:`mapify.evidence.prompts` covers signage the OCR could not read.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

# (first, last, ISO 15924 code).  Ordered; first match wins.
_RANGES: list[tuple[int, int, str]] = [
    (0x0041, 0x005A, "Latn"),
    (0x0061, 0x007A, "Latn"),
    (0x00C0, 0x024F, "Latn"),
    (0x1E00, 0x1EFF, "Latn"),
    (0x0370, 0x03FF, "Grek"),
    (0x1F00, 0x1FFF, "Grek"),
    (0x0400, 0x052F, "Cyrl"),
    (0x2DE0, 0x2DFF, "Cyrl"),
    (0xA640, 0xA69F, "Cyrl"),
    (0x0530, 0x058F, "Armn"),
    (0x0590, 0x05FF, "Hebr"),
    (0x0600, 0x06FF, "Arab"),
    (0x0750, 0x077F, "Arab"),
    (0x08A0, 0x08FF, "Arab"),
    (0xFB50, 0xFDFF, "Arab"),
    (0xFE70, 0xFEFF, "Arab"),
    (0x0700, 0x074F, "Syrc"),
    (0x0780, 0x07BF, "Thaa"),
    (0x07C0, 0x07FF, "Nkoo"),
    (0x0900, 0x097F, "Deva"),
    (0x0980, 0x09FF, "Beng"),
    (0x0A00, 0x0A7F, "Guru"),
    (0x0A80, 0x0AFF, "Gujr"),
    (0x0B00, 0x0B7F, "Orya"),
    (0x0B80, 0x0BFF, "Taml"),
    (0x0C00, 0x0C7F, "Telu"),
    (0x0C80, 0x0CFF, "Knda"),
    (0x0D00, 0x0D7F, "Mlym"),
    (0x0D80, 0x0DFF, "Sinh"),
    (0x0E00, 0x0E7F, "Thai"),
    (0x0E80, 0x0EFF, "Laoo"),
    (0x0F00, 0x0FFF, "Tibt"),
    (0x1000, 0x109F, "Mymr"),
    (0x10A0, 0x10FF, "Geor"),
    (0x2D00, 0x2D2F, "Geor"),
    (0x1200, 0x137F, "Ethi"),
    (0x13A0, 0x13FF, "Cher"),
    (0x1400, 0x167F, "Cans"),
    (0x1780, 0x17FF, "Khmr"),
    (0x1800, 0x18AF, "Mong"),
    (0x2D30, 0x2D7F, "Tfng"),
    (0x3040, 0x309F, "Hira"),
    (0x30A0, 0x30FF, "Kana"),
    (0x31F0, 0x31FF, "Kana"),
    (0x3400, 0x4DBF, "Hani"),
    (0x4E00, 0x9FFF, "Hani"),
    (0xF900, 0xFAFF, "Hani"),
    (0x1100, 0x11FF, "Hang"),
    (0x3130, 0x318F, "Hang"),
    (0xA960, 0xA97F, "Hang"),
    (0xAC00, 0xD7AF, "Hang"),
]

# Human-readable names for the report.
SCRIPT_NAMES: dict[str, str] = {
    "Latn": "Latin", "Cyrl": "Cyrillic", "Grek": "Greek", "Arab": "Arabic",
    "Hebr": "Hebrew", "Deva": "Devanagari", "Beng": "Bengali", "Guru": "Gurmukhi",
    "Gujr": "Gujarati", "Orya": "Odia", "Taml": "Tamil", "Telu": "Telugu",
    "Knda": "Kannada", "Mlym": "Malayalam", "Sinh": "Sinhala", "Thai": "Thai",
    "Laoo": "Lao", "Tibt": "Tibetan", "Mymr": "Myanmar", "Geor": "Georgian",
    "Armn": "Armenian", "Ethi": "Ethiopic", "Khmr": "Khmer", "Hani": "Han",
    "Hira": "Hiragana", "Kana": "Katakana", "Hang": "Hangul", "Jpan": "Japanese",
    "Mong": "Mongolian", "Tfng": "Tifinagh", "Nkoo": "N'Ko", "Syrc": "Syriac",
    "Thaa": "Thaana", "Cher": "Cherokee", "Cans": "Canadian Aboriginal",
}

# Characters that narrow Latin text down to particular languages.  Each entry
# maps a character to the ISO 639-1 codes that commonly use it.  This is a
# hint generator, not a language identifier.
_LATIN_MARKERS: dict[str, tuple[str, ...]] = {
    "ł": ("pl",), "ż": ("pl",), "ę": ("pl",), "ą": ("pl",), "ś": ("pl",),
    "ñ": ("es",), "¿": ("es",), "¡": ("es",),
    "ß": ("de",), "ü": ("de", "tr", "hu", "et"), "ö": ("de", "sv", "tr", "hu", "is", "et"),
    "ä": ("de", "sv", "fi", "et"),
    "ã": ("pt",), "õ": ("pt", "et"), "ç": ("pt", "fr", "tr", "ca", "az"),
    "à": ("fr", "it", "pt", "ca"), "è": ("fr", "it", "ca"), "ù": ("fr", "it"),
    "œ": ("fr",), "ê": ("fr", "pt", "vi"),
    "å": ("sv", "no", "da"), "ø": ("no", "da"), "æ": ("no", "da", "is"),
    "ð": ("is",), "þ": ("is",),
    "ğ": ("tr",), "ş": ("tr", "ro", "az"), "ı": ("tr", "az"),
    "ă": ("ro",), "ț": ("ro",),
    "č": ("cs", "sk", "sl", "hr", "lt", "lv", "sr"), "š": ("cs", "sk", "sl", "hr", "lt", "lv", "et"),
    "ž": ("cs", "sk", "sl", "hr", "lt", "lv", "et"), "ř": ("cs",), "ů": ("cs",),
    "ľ": ("sk",), "ĺ": ("sk",), "ő": ("hu",), "ű": ("hu",),
    "ė": ("lt",), "ų": ("lt",), "ā": ("lv",), "ģ": ("lv",),
    "ij": ("nl",),
    "ạ": ("vi",), "ế": ("vi",), "ơ": ("vi",), "ư": ("vi",),
    "ǎ": ("zh",),
}


@dataclass
class ScriptReading:
    """What writing systems the recognised text used."""

    counts: dict[str, int] = field(default_factory=dict)
    total_letters: int = 0
    language_hints: dict[str, float] = field(default_factory=dict)

    @property
    def proportions(self) -> dict[str, float]:
        if not self.total_letters:
            return {}
        return {k: v / self.total_letters for k, v in self.counts.items()}

    def dominant(self, minimum: float = 0.15) -> list[tuple[str, float]]:
        """Scripts holding at least ``minimum`` of the recognised letters."""
        out = [(s, p) for s, p in self.proportions.items() if p >= minimum]
        return sorted(out, key=lambda kv: -kv[1])

    def as_dict(self) -> dict[str, object]:
        return {
            "counts": dict(sorted(self.counts.items(), key=lambda kv: -kv[1])),
            "proportions": {k: round(v, 4) for k, v in self.proportions.items()},
            "total_letters": self.total_letters,
            "language_hints": {
                k: round(v, 4)
                for k, v in sorted(self.language_hints.items(), key=lambda kv: -kv[1])
            },
        }


def script_of(ch: str) -> str | None:
    """ISO 15924 code for a single character, or None if it is not a letter."""
    cp = ord(ch)
    if not unicodedata.category(ch).startswith("L"):
        return None
    for lo, hi, code in _RANGES:
        if lo <= cp <= hi:
            return code
    return None


def analyse(text: str) -> ScriptReading:
    """Classify the writing systems present in ``text``."""
    reading = ScriptReading()
    lowered = text.lower()
    for ch in text:
        code = script_of(ch)
        if code is None:
            continue
        reading.counts[code] = reading.counts.get(code, 0) + 1
        reading.total_letters += 1

    # Hiragana/Katakana next to Han means Japanese rather than Chinese.
    kana = reading.counts.get("Hira", 0) + reading.counts.get("Kana", 0)
    if kana:
        reading.counts["Jpan"] = kana + reading.counts.get("Hani", 0)

    hits: dict[str, float] = {}
    for marker, langs in _LATIN_MARKERS.items():
        n = lowered.count(marker)
        if not n:
            continue
        # A marker shared by many languages is weaker evidence than a unique one.
        weight = n / len(langs)
        for lang in langs:
            hits[lang] = hits.get(lang, 0.0) + weight
    if hits:
        peak = max(hits.values())
        reading.language_hints = {k: v / peak for k, v in hits.items()}
    return reading


def tokens(text: str, min_len: int = 3) -> list[str]:
    """Split OCR output into candidate toponym tokens.

    Keeps runs of letters (any script) plus internal hyphens/apostrophes, which
    is what place names look like on signage.
    """
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        if unicodedata.category(ch).startswith("L") or ch in "-'’.":
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    cleaned = []
    for tok in out:
        tok = tok.strip("-'’.")
        if len(tok) >= min_len:
            cleaned.append(tok)
    return cleaned
