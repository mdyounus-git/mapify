"""Helpers every technique phase uses.

Kept in one place so that "how a zero-shot reading is reported" and
"where a finding's evidence came from" mean the same thing in technique
13 as in technique 113.
"""

from __future__ import annotations

from .base import REGISTRY, Context, Finding, Region

__all__ = ["SRC_EXIF", "SRC_OCR", "SRC_CLIP", "SRC_NOMINATIM", "SRC_NE",
           "SRC_OSM_LOCAL", "box_region", "reading", "describe_reading",
           "spec", "leader"]

# Where evidence comes from, named once so provenance is never a guess.
SRC_EXIF = "the file's own Exif/XMP metadata"
SRC_OCR = "EasyOCR (Apache-2.0), run locally"
SRC_CLIP = "OpenAI CLIP ViT-L/14 (MIT), zero-shot, run locally"
SRC_NOMINATIM = "OpenStreetMap via Nominatim (ODbL)"
SRC_NE = "Natural Earth 1:10m (public domain)"
SRC_OSM_LOCAL = "OpenStreetMap, local store (ODbL)"


def spec(technique_id: int):
    """The registered Technique, for findings built by hand."""
    return REGISTRY[technique_id]


def box_region(line, label: str) -> list[Region]:
    """The evidence-map rectangle for one OCR line, when it has one."""
    box = getattr(line, "box", None)
    if not box:
        return []
    x0, y0, x1, y1 = box
    return [Region(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), label)]


def reading(ctx: Context, group_key: str):
    ev = ctx.evidence
    return ev.by_group(group_key) if ev is not None else None


def leader(ctx: Context):
    """The leading candidate, or None: what "the location" means here."""
    return ctx.candidates[0] if ctx.candidates else None


def describe_reading(sp, rd, *, steers: bool,
                     min_confidence: float = 0.30,
                     min_margin: float = 0.08) -> Finding:
    """Report one zero-shot group, with an honest note about its weight."""
    if rd is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    runners = ", ".join(f"{label} {p:.0%}" for label, p in rd.runners_up[:2])
    summary = f"{rd.option_label} ({rd.confidence:.0%})"
    if runners:
        summary += f"; then {runners}"
    if not steers:
        summary += " — reported only, moves no coordinate"
    # A group whose top two options are a coin-flip has seen nothing.
    decisive = rd.confidence >= min_confidence and rd.margin >= min_margin
    return Finding(
        technique=sp.id, key=sp.key, name=sp.name, phase=sp.phase,
        ran=True, found=decisive,
        summary=summary if decisive else
        f"No clear reading (best was {rd.option_label} at "
        f"{rd.confidence:.0%}, too close to call)",
        detail={"option": rd.option_key,
                "confidence": round(rd.confidence, 4),
                "margin": round(rd.margin, 4),
                "ranked": [(k, round(v, 4)) for k, v in rd.ranked],
                "feeds_location": bool(steers and rd.called)},
        confidence=rd.confidence if decisive else 0.0,
        sources=[SRC_CLIP],
    )
