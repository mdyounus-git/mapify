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


# ------------------------------------------------------- asking the encoder

def zero_shot(ctx: Context, options: dict, tag: str, embedding=None,
              temperature: float = 0.02) -> dict | None:
    """Probabilities over ``options`` ({key: [prompts]}) for one embedding.

    Lets a technique ask its own narrow question -- is there a water
    reflection, a manhole cover -- without adding a group to the shared
    prompt bank that every analysis would pay to score. The text side is
    cached on disk under ``tag``; the image side reuses the embedding the
    analysis already computed. Returns None when the encoder is not there.
    """
    import numpy as np
    eng = ctx.engine
    emb = ctx.embedding if embedding is None else embedding
    clip = getattr(eng, "clip", None) if eng is not None else None
    if clip is None or emb is None:
        return None
    keys = list(options)
    texts = [t for k in keys for t in options[k]]
    try:
        bank = clip.encode_texts_cached(texts, f"technique-{tag}")
    except Exception:
        return None
    sims = bank @ np.asarray(emb)
    scores, i = [], 0
    for k in keys:
        n = len(options[k])
        scores.append(float(np.max(sims[i:i + n])))
        i += n
    z = np.asarray(scores) / temperature
    z = np.exp(z - z.max())
    p = z / z.sum()
    return {k: float(v) for k, v in zip(keys, p)}


def tiles(ctx: Context, grid: int = 2):
    """[(box, embedding)] for a grid of tiles, computed once per run.

    Each tile is encoded at the encoder's full input size, so detail the
    whole-image view shrank away -- a distant sign, a vehicle, a script --
    gets its own look. Costs one encoder pass per tile, which is why the
    grid is small and the result is shared by every technique asking.
    """
    key = f"tiles{grid}"
    if key in ctx.memo:
        return ctx.memo[key]
    eng = ctx.engine
    clip = getattr(eng, "clip", None) if eng is not None else None
    out = []
    if clip is not None and ctx.image is not None:
        w, h = ctx.image.size
        for gy in range(grid):
            for gx in range(grid):
                box = (gx / grid, gy / grid, (gx + 1) / grid, (gy + 1) / grid)
                crop = ctx.image.crop((int(box[0] * w), int(box[1] * h),
                                       int(box[2] * w), int(box[3] * h)))
                try:
                    out.append((box, clip.encode_image(crop)))
                except Exception:
                    out = []
                    break
    ctx.memo[key] = out
    return out
