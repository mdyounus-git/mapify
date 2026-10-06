"""Helpers every technique phase uses.

Kept in one place so that "how a zero-shot reading is reported" and
"where a finding's evidence came from" mean the same thing in technique
13 as in technique 113.
"""

from __future__ import annotations

from .base import REGISTRY, Context, Finding, Region

__all__ = ["SRC_EXIF", "SRC_OCR", "SRC_CLIP", "SRC_NOMINATIM", "SRC_NE",
           "SRC_OSM_LOCAL", "box_region", "reading", "describe_reading",
           "spec", "leader", "zero_shot", "roi", "detail_window", "window_name",
           "ABSENT", "ORDINARY_SCENES"]

# Options that say "nothing of this kind is visible". Reading one is not
# a finding: a technique that sees no road has found no road marking.
ABSENT = frozenset({"none", "indeterminate", "urban_flat"})

# "None of these", for a technique's own presence question. It has to be
# as broad as the scenes people photograph: the encoder does not do
# negation, so "a scene with no street lights" sits right beside "street
# lights", and with nothing else to choose a river at night becomes a
# mosque. A thing is reported present only when it beats all of these.
ORDINARY_SCENES = ["an ordinary city street", "a skyline of modern buildings",
                   "a river or a bridge in a city",
                   "a landscape of fields, hills or trees",
                   "a residential street with houses",
                   "a shopping street with shop signs", "a road or highway with cars",
                   "the interior of a room", "a building facade", "a city at night"]

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
    if decisive and rd.option_key in ABSENT:
        # "No road visible" is a reading, not a road marking.
        return Finding(
            technique=sp.id, key=sp.key, name=sp.name, phase=sp.phase,
            ran=True, found=False,
            summary=f"Nothing of this kind to read: {rd.option_label.lower()} "
                    f"({rd.confidence:.0%})",
            detail={"option": rd.option_key,
                    "confidence": round(rd.confidence, 4),
                    "margin": round(rd.margin, 4),
                    "ranked": [(k, round(v, 4)) for k, v in rd.ranked],
                    "feeds_location": False},
            confidence=0.0, sources=[SRC_CLIP],
        )
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


# Below this the whole-image view has lost little, and a second look at
# part of the frame is not worth an encoder pass.
ROI_MIN_SIDE = 640


def detail_window(image) -> tuple[float, float, float, float]:
    """The half-size window of the frame with the most fine detail.

    Nine overlapping windows, each half the width and height of the frame,
    scored by mean gradient on a small greyscale copy -- cheap, and it
    lands on signs, shopfronts and traffic rather than on sky or a blank
    wall. Returned as a normalised (x0, y0, x1, y1) box.
    """
    import numpy as np
    from PIL import Image
    small = image.convert("L")
    small.thumbnail((256, 256), Image.Resampling.BILINEAR)
    y = np.asarray(small, dtype=np.float32)
    grad = np.zeros_like(y)
    grad[:, 1:] += np.abs(np.diff(y, axis=1))
    grad[1:, :] += np.abs(np.diff(y, axis=0))
    h, w = grad.shape
    best, best_box = -1.0, (0.25, 0.25, 0.75, 0.75)
    for fy in (0.0, 0.25, 0.5):
        for fx in (0.0, 0.25, 0.5):
            win = grad[int(fy * h):int((fy + 0.5) * h), int(fx * w):int((fx + 0.5) * w)]
            score = float(win.mean()) if win.size else 0.0
            if score > best:
                best, best_box = score, (fx, fy, fx + 0.5, fy + 0.5)
    return best_box


def roi(ctx: Context):
    """(box, embedding) for the frame's most detailed region, or None.

    The whole-image encoder sees the photograph shrunk to 224 pixels,
    which is how a distant sign or a single vehicle disappears. This gives
    the most detailed half of the frame its own look at twice the
    resolution. One extra encoder pass rather than a grid of them: on a
    CPU each pass costs seconds, so the look goes where the detail is.
    Computed once per run and shared by every technique that asks.
    """
    if "roi" in ctx.memo:
        return ctx.memo["roi"]
    eng = ctx.engine
    clip = getattr(eng, "clip", None) if eng is not None else None
    out = None
    if clip is not None and ctx.image is not None and max(ctx.image.size) >= ROI_MIN_SIDE:
        box = detail_window(ctx.image)
        w, h = ctx.image.size
        crop = ctx.image.crop((int(box[0] * w), int(box[1] * h),
                               int(box[2] * w), int(box[3] * h)))
        try:
            out = (box, clip.encode_image(crop))
        except Exception:
            out = None
    ctx.memo["roi"] = out
    return out


def window_name(box) -> str:
    """"the top-left of the frame" for a half-size detail window."""
    x0, y0 = round(box[0], 2), round(box[1], 2)
    row = {0.0: "top", 0.25: "middle", 0.5: "bottom"}.get(y0, "middle")
    col = {0.0: "left", 0.25: "centre", 0.5: "right"}.get(x0, "centre")
    return "the centre" if (row, col) == ("middle", "centre") else f"the {row}-{col}"
