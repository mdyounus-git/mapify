"""The language of the text read off a photograph, and where it is spoken.

Script says the writing system; Latin covers half the world. The language
says far more: Swahili, Hausa, Portuguese and French all sign in Latin
letters. GlotLID (Apache-2.0 plus a notice on its training data; a fastText
model covering about 2,000 languages, ``models/glotlid/model.bin``) names
the language of the recognised text, and Unicode CLDR's territory data
(Unicode License v3, ``models/cldr/``) says which countries speak it and how
many of their people do.

Reported, not yet steering: a language finding goes into technique 12 and
the report, and moves nothing until the text-bearing benchmark says it
helps. The model is 1.7 GB in memory, so it is loaded only when OCR has read
enough letters to identify a language from.
"""

from __future__ import annotations

import json
import re
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

__all__ = ["LanguageIdentifier", "countries_speaking", "MIN_LETTERS"]

MIN_LETTERS = 12          # fewer than this and any language guess is noise
MIN_PROBABILITY = 0.4
_LABEL = re.compile(r"__label__([a-z]{2,3})_([A-Z][a-z]{3})")
_tables: dict[str, Any] | None = None
_tables_lock = threading.Lock()


def _models_dir() -> Path:
    from ..paths import models_dir
    return models_dir()


def _cldr(cldr_dir: Path | None = None) -> dict[str, Any]:
    """CLDR's language-by-country shares and code mappings, parsed once and
    kept beside the XML as JSON."""
    global _tables
    if _tables is not None and cldr_dir is None:
        return _tables
    base = cldr_dir or (_models_dir() / "cldr")
    cache = base / "languages.json"
    with _tables_lock:
        if cache.is_file():
            tables = json.loads(cache.read_text(encoding="utf-8"))
        else:
            data = ET.parse(base / "supplementalData.xml").getroot()
            meta = ET.parse(base / "supplementalMetadata.xml").getroot()
            speakers: dict[str, dict[str, float]] = {}
            for terr in data.iter("territory"):
                cc = terr.get("type")
                pop = float(terr.get("population") or 0)
                if not cc or len(cc) != 2 or not cc.isalpha():
                    continue
                for lp in terr.findall("languagePopulation"):
                    code = (lp.get("type") or "").split("_")[0]
                    pct = float(lp.get("populationPercent") or 0)
                    if code and pct:
                        # Added, not replaced: Nigeria lists Hausa (13%) and
                        # Hausa in Arabic script (1%) as two rows.
                        here = speakers.setdefault(code, {})
                        here[cc] = here.get(cc, 0.0) + pop * pct / 100.0
            alpha3 = {c.get("alpha3"): c.get("type")
                      for c in data.iter("languageCodes") if c.get("alpha3")}
            alias = {a.get("type"): a.get("replacement").split()[0].split("_")[0]
                     for a in meta.iter("languageAlias")
                     if a.get("type") and a.get("replacement")
                     and "_" not in a.get("type") and "-" not in a.get("type")}
            tables = {"speakers": speakers, "alpha3": alpha3, "alias": alias}
            cache.write_text(json.dumps(tables), encoding="utf-8")
        if cldr_dir is None:
            _tables = tables
        return tables


def _code(lang3: str, tables: dict[str, Any]) -> str:
    """GlotLID's ISO 639-3 code as CLDR spells it (swh -> sw, fra -> fr)."""
    speakers = tables["speakers"]
    for candidate in (lang3, tables["alias"].get(lang3), tables["alpha3"].get(lang3)):
        if candidate and candidate in speakers:
            return candidate
    return tables["alias"].get(lang3) or tables["alpha3"].get(lang3) or lang3


def countries_speaking(lang3: str, top: int = 8, cldr_dir: Path | None = None
                       ) -> list[tuple[str, float]]:
    """(ISO country, share of the language's speakers), most speakers first."""
    tables = _cldr(cldr_dir)
    by_country = tables["speakers"].get(_code(lang3, tables), {})
    total = sum(by_country.values())
    if not total:
        return []
    ranked = sorted(by_country.items(), key=lambda kv: -kv[1])[:top]
    return [(cc, n / total) for cc, n in ranked]


class LanguageIdentifier:
    def __init__(self, model_path: Path | None = None, cldr_dir: Path | None = None):
        self.path = Path(model_path) if model_path else _models_dir() / "glotlid" / "model.bin"
        self.cldr_dir = cldr_dir
        self._model = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            import fasttext  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    import fasttext
                    self._model = fasttext.load_model(str(self.path))
        return self._model

    def identify(self, text: str) -> dict[str, Any] | None:
        """The language of ``text``, its script, the model's probability and
        the countries that speak it; None when there is too little text or
        the model is unsure."""
        letters = sum(ch.isalpha() for ch in text or "")
        if letters < MIN_LETTERS or not self.available():
            return None
        clean = " ".join((text or "").split())
        upper = sum(ch.isupper() for ch in clean)
        if upper > 0.7 * letters:
            # Signs are often in capitals, and in capitals the model guesses
            # wrong with confidence: "KARIBU SANA DUKA LA DAWA" read as Hausa
            # (0.92), lowercased as Swahili.
            clean = clean.lower()
        labels, probs = self._predict(clean)
        if not labels:
            return None
        m = _LABEL.match(labels[0])
        if not m or float(probs[0]) < MIN_PROBABILITY:
            return None
        lang3, script = m.group(1), m.group(2)
        return {
            "language": lang3, "script": script,
            "probability": round(float(probs[0]), 3),
            "alternatives": [
                {"language": mm.group(1), "probability": round(float(p), 3)}
                for lab, p in zip(labels[1:], probs[1:])
                if (mm := _LABEL.match(lab))],
            "countries": [{"country": cc, "share": round(sh, 3)}
                          for cc, sh in countries_speaking(lang3, cldr_dir=self.cldr_dir)],
            "letters": letters, "model": "GlotLID",
        }

    def _predict(self, text: str):
        model = self._load()
        try:
            labels, probs = model.predict(text, k=3)
        except ValueError:
            # fastText's predict meets NumPy 2's stricter copy rules; ask for
            # the raw pairs instead.
            pairs = model.f.predict(text + "\n", 3, 0.0, "strict")
            probs = [p for p, _ in pairs]
            labels = [lab for _, lab in pairs]
        return list(labels), list(probs)
