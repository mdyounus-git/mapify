"""Shared types for the model layer.

The pipeline talks to these shapes, never to torch, so the whole analysis
chain can be exercised with stand-in models in the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from PIL.Image import Image as PILImage


@dataclass
class RawCandidate:
    """A coordinate a locator proposed, before any evidence is applied."""

    latitude: float
    longitude: float
    score: float          # the locator's own probability mass
    source: str           # which locator produced it


@dataclass
class OcrLine:
    text: str
    confidence: float
    # Normalised [x0, y0, x1, y1] within the image, when the engine gives one.
    box: tuple[float, float, float, float] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "box": list(self.box) if self.box else None,
        }


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    engine: str | None = None
    languages: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "languages": self.languages,
            "error": self.error,
            "line_count": len(self.lines),
            "lines": [line.as_dict() for line in self.lines],
            "text": self.text,
        }


@dataclass
class GroupReading:
    """The winning option in one prompt group, with its rivals."""

    group_key: str
    group_title: str
    option_key: str
    option_label: str
    confidence: float
    margin: float
    called: bool               # did it clear the group's thresholds
    constrains: bool           # does the winning option restrict geography
    runners_up: list[tuple[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "group": self.group_key,
            "title": self.group_title,
            "option": self.option_key,
            "label": self.option_label,
            "confidence": round(self.confidence, 4),
            "margin": round(self.margin, 4),
            "called": self.called,
            "constrains": self.constrains,
            "runners_up": [(k, round(v, 4)) for k, v in self.runners_up],
        }


@dataclass
class VisualEvidence:
    readings: list[GroupReading] = field(default_factory=list)
    # ISO2 -> probability, from the CLIP country gazetteer, when it ran.
    country_prior: dict[str, float] = field(default_factory=dict)
    model: str | None = None
    error: str | None = None

    def called(self) -> list[GroupReading]:
        return [r for r in self.readings if r.called]

    def by_group(self, key: str) -> GroupReading | None:
        for r in self.readings:
            if r.group_key == key:
                return r
        return None

    def as_dict(self) -> dict[str, Any]:
        top = sorted(self.country_prior.items(), key=lambda kv: -kv[1])[:12]
        return {
            "model": self.model,
            "error": self.error,
            "readings": [r.as_dict() for r in self.readings],
            "country_prior_top": [(k, round(v, 4)) for k, v in top],
        }


@runtime_checkable
class Locator(Protocol):
    """Proposes coordinates for an image."""

    name: str

    def locate(self, image: PILImage, top_k: int) -> list[RawCandidate]: ...


@runtime_checkable
class EvidenceExtractor(Protocol):
    """Scores an image against the prompt bank."""

    name: str

    def extract(self, image: PILImage) -> VisualEvidence: ...


@runtime_checkable
class OcrEngine(Protocol):
    """Reads text out of an image."""

    name: str

    def read(self, image: PILImage) -> OcrResult: ...
