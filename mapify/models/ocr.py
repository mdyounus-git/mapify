"""Optional OCR.

Text on signs is the strongest single clue in a photograph, so it is worth
reading when we can. EasyOCR (Apache-2.0) is the default because it covers
about eighty languages including Cyrillic, Arabic, Devanagari, Thai, CJK and
Hangul, and because it runs on the torch install we already have.

EasyOCR loads one recogniser per compatible language group, so a full
multi-script sweep is slow. The default is a single Latin pass; extra groups
are opt-in through ``models.ocr_extra_groups`` in config.toml.

OCR is never required: with no engine installed the pipeline still runs, and
the report says which clue types were unavailable rather than pretending the
image had no text.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PIL.Image import Image as PILImage

from .base import OcrLine, OcrResult

log = logging.getLogger("mapify.ocr")

# Languages EasyOCR can combine in one reader. Anything outside a group needs
# its own pass, which is why a full sweep is opt-in.
SUGGESTED_GROUPS: tuple[tuple[str, ...], ...] = (
    ("en",),
    ("ru", "en"),
    ("ar", "en"),
    ("hi", "en"),
    ("ch_sim", "en"),
    ("ja", "en"),
    ("ko", "en"),
    ("th", "en"),
)

# ISO 15924 writing system -> the EasyOCR group that can read it.
#
# An English-only reader returns nothing at all on a Cyrillic or Japanese
# sign, which is precisely the case where signage text would have been most
# useful. The pipeline uses this to load the right reader on demand, based on
# the script the zero-shot pass identified, rather than loading eight
# recognisers on every image or missing the text entirely.
SCRIPT_TO_GROUP: dict[str, tuple[str, ...]] = {
    "Cyrl": ("ru", "en"),
    "Arab": ("ar", "en"),
    "Deva": ("hi", "en"),
    "Beng": ("bn", "en"),
    "Taml": ("ta", "en"),
    "Telu": ("te", "en"),
    "Knda": ("kn", "en"),
    "Thai": ("th", "en"),
    "Hani": ("ch_sim", "en"),
    "Jpan": ("ja", "en"),
    "Hang": ("ko", "en"),
    "Grek": ("en",),      # EasyOCR has no Greek recogniser
    "Hebr": ("en",),      # nor Hebrew
    "Latn": ("en",),
}


def group_for_script(script: str) -> tuple[str, ...] | None:
    """The reader group for a writing system, if one exists and differs."""
    group = SCRIPT_TO_GROUP.get(script)
    if not group or group == ("en",):
        return None
    return group


class EasyOcrEngine:
    """EasyOCR adapter, loaded lazily and only when first used."""

    name = "easyocr"

    def __init__(self, languages: list[str] | None = None,
                 extra_groups: list[list[str]] | None = None,
                 device: str = "cpu"):
        self.languages = list(languages or ["en"])
        self.extra_groups = [list(g) for g in (extra_groups or [])]
        self.device = device
        self._readers: dict[tuple[str, ...], Any] = {}
        self._lock = threading.Lock()
        self.load_error: str | None = None

    @property
    def available(self) -> bool:
        try:
            import easyocr  # noqa: F401
            return True
        except Exception:
            return False

    def _reader(self, langs: tuple[str, ...]) -> Any | None:
        if langs in self._readers:
            return self._readers[langs]
        with self._lock:
            if langs in self._readers:
                return self._readers[langs]
            try:
                import easyocr

                reader = easyocr.Reader(
                    list(langs),
                    gpu=(self.device != "cpu"),
                    verbose=False,
                )
                self._readers[langs] = reader
                log.info("EasyOCR reader ready for %s", "+".join(langs))
                return reader
            except Exception as exc:
                self.load_error = f"{type(exc).__name__}: {exc}"
                log.warning("EasyOCR reader %s failed: %s", langs, self.load_error)
                self._readers[langs] = None
                return None

    def read(self, image: PILImage,
             extra_group: tuple[str, ...] | None = None) -> OcrResult:
        import numpy as np

        groups: list[tuple[str, ...]] = [tuple(self.languages)]
        for extra in self.extra_groups:
            t = tuple(extra)
            if t not in groups:
                groups.append(t)
        # A group chosen for this image, from the script the zero-shot pass
        # identified.
        if extra_group and tuple(extra_group) not in groups:
            groups.append(tuple(extra_group))

        array = np.array(image.convert("RGB"))
        width, height = image.size
        lines: list[OcrLine] = []
        seen: set[str] = set()
        used: list[str] = []
        errors: list[str] = []

        for langs in groups:
            reader = self._reader(langs)
            if reader is None:
                errors.append(f"{'+'.join(langs)}: {self.load_error}")
                continue
            try:
                raw = reader.readtext(array, detail=1, paragraph=False)
            except Exception as exc:
                errors.append(f"{'+'.join(langs)}: {type(exc).__name__}: {exc}")
                continue
            used.extend(langs)
            for item in raw:
                try:
                    box, text, conf = item[0], item[1], float(item[2])
                except (IndexError, TypeError, ValueError):
                    continue
                text = (text or "").strip()
                if not text or text.casefold() in seen:
                    continue
                seen.add(text.casefold())
                norm_box = None
                try:
                    xs = [float(p[0]) for p in box]
                    ys = [float(p[1]) for p in box]
                    norm_box = (
                        round(min(xs) / width, 4), round(min(ys) / height, 4),
                        round(max(xs) / width, 4), round(max(ys) / height, 4),
                    )
                except Exception:
                    norm_box = None
                lines.append(OcrLine(text=text, confidence=conf, box=norm_box))

        lines.sort(key=lambda l: -l.confidence)
        return OcrResult(
            lines=lines,
            engine=self.name,
            languages=sorted(set(used)),
            error="; ".join(errors) if errors and not lines else None,
        )


class NullOcrEngine:
    """Stand-in when no OCR engine is installed."""

    name = "none"

    def read(self, image: PILImage,
             extra_group: tuple[str, ...] | None = None) -> OcrResult:  # noqa: ARG002
        return OcrResult(
            engine=None,
            error=(
                "No OCR engine installed. Install easyocr to read signage "
                "text: pip install easyocr"
            ),
        )


def build_ocr_engine(languages: list[str] | None, extra_groups: list[list[str]] | None,
                     device: str = "cpu"):
    engine = EasyOcrEngine(languages, extra_groups, device)
    if engine.available:
        return engine
    return NullOcrEngine()
