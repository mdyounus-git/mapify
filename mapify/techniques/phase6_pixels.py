"""Techniques 73-90: the image, region by region and in depth.

The whole-image encoder sees a photograph shrunk to 224 pixels. That is
enough for "what kind of place", and it is exactly how a distant sign,
a single vehicle or a line of script disappears. The region-of-interest
pass (73) gives the most detailed half of the frame its own look at
twice the resolution -- one extra encoder pass, not a grid of them,
because on a CPU each pass costs seconds -- and 75 and 76 build on it:
a coarse "what is there", honestly labelled as zero-shot presence rather
than detection, because no detector model is shipped.

The rest measure the pixels directly: colour and texture (74), light
level and near-infrared capture (78), mirrored scenes in water (81),
distance and height from the horizon and an assumed camera height (85,
86), and whether a building stood between the camera and the sun (90)
-- the one shadow question local map data can answer, when the camera's
position is known to metres.

Some of these the specification asks for need machinery this project
does not have: scene reconstruction from reflections (80), depth
networks (84), shadow detection (87, 88), occlusion reasoning (89),
rolling-shutter measurement (83). They are registered and say so.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from .base import Context, Finding, Region, technique
from .common import (ABSENT, ORDINARY_SCENES, ROI_MIN_SIDE, SRC_CLIP, SRC_EXIF, leader,
                     reading, roi, spec, window_name, zero_shot)

PHASE = 6
SRC_PIX = "measured from the pixels"


def _unavailable(sp, why: str) -> Finding:
    return Finding.not_applicable(sp, why)


# ---------------------------------------------------------------- 73

@technique(id=73, key="tiling_roi", name="Image tiling / region-of-interest (ROI) analysis",
           phase=PHASE, needs=("embedding", "pixels"),
           fusion_note="Gives the most detailed half of the frame its own look "
                       "at twice the resolution and reports what it shows that "
                       "the whole-image view did not. One extra encoder pass, "
                       "not a grid: on a CPU each costs seconds. Unmeasured as "
                       "a fusion input.")
def tiling_roi(ctx: Context) -> Finding:
    sp = spec(73)
    side = max(ctx.image.size)
    if side < ROI_MIN_SIDE:
        return Finding.nothing_found(
            sp, f"The frame is small ({side} px): the whole-image view already "
                f"sees it at close to full detail.")
    r = roi(ctx)
    ext = getattr(ctx.engine, "evidence_extractor", None)
    if r is None or ext is None:
        return _unavailable(sp, "the image encoder is not available")
    box, emb = r
    ev = ext.score_embedding(emb)
    global_ev = ctx.evidence
    notes = []
    for rd in ev.readings:
        if rd.confidence < 0.5 or rd.margin < 0.15 or rd.option_key in ABSENT:
            continue
        g = global_ev.by_group(rd.group_key) if global_ev is not None else None
        if g is not None and g.option_key == rd.option_key:
            continue                           # the whole image saw it too
        notes.append(f"{rd.group_title.lower()}: {rd.option_label} "
                     f"({rd.confidence:.0%})")
    where = window_name(box)
    detail = {"box": list(box), "window": where, "notes": notes}
    region = Region(*box, label="region of interest")
    if not notes:
        return Finding(technique=73, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=False, detail=detail, regions=[region],
                       summary=(f"The most detailed part of the frame ({where}), "
                                f"examined at twice the resolution, shows nothing "
                                f"the whole-image reading missed."),
                       sources=[SRC_CLIP, SRC_PIX])
    return Finding(technique=73, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, detail=detail, confidence=0.5, regions=[region],
                   summary=(f"Up close, {where} of the frame shows "
                            + "; ".join(notes[:4])
                            + " — which the whole-image view did not."),
                   sources=[SRC_CLIP, SRC_PIX])


# ---------------------------------------------------------------- 74

_NAMED = {"white": (240, 240, 240), "black": (15, 15, 15), "grey": (128, 128, 128),
          "red": (200, 40, 40), "orange": (230, 130, 40), "yellow": (230, 210, 60),
          "green": (60, 140, 60), "teal": (40, 140, 140), "blue": (50, 90, 200),
          "sky blue": (130, 180, 230), "purple": (120, 60, 160), "pink": (230, 140, 180),
          "brown": (120, 80, 50), "beige": (210, 190, 150)}


def _name(rgb) -> str:
    return min(_NAMED, key=lambda n: sum((a - b) ** 2 for a, b in zip(rgb, _NAMED[n])))


@technique(id=74, key="color_texture", name="Pixel-level color & texture analysis",
           phase=PHASE, needs=("pixels",),
           fusion_note="Describes the palette and texture; locates nothing.")
def color_texture(ctx: Context) -> Finding:
    from ..forensics.graphic import graphic_stats
    sp = spec(74)
    im = ctx.image.convert("RGB")
    im.thumbnail((256, 256), Image.Resampling.BILINEAR)
    a = np.asarray(im, dtype=np.float32).reshape(-1, 3)
    rg = a[:, 0] - a[:, 1]
    yb = 0.5 * (a[:, 0] + a[:, 1]) - a[:, 2]
    colourful = float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))
    rng = np.random.default_rng(0)
    sample = a[rng.choice(len(a), min(3000, len(a)), replace=False)]
    cent = sample[rng.choice(len(sample), 5, replace=False)]
    for _ in range(8):
        lab = np.argmin(((sample[:, None, :] - cent[None]) ** 2).sum(2), 1)
        cent = np.array([sample[lab == k].mean(0) if (lab == k).any() else cent[k]
                         for k in range(5)])
    share = np.bincount(lab, minlength=5) / len(lab)
    # Two clusters can carry the same colour name; report each name once.
    named: dict[str, list] = {}
    for k in np.argsort(-share):
        n = _name(cent[k])
        if n in named:
            named[n][1] += float(share[k])
        else:
            named[n] = [n, float(share[k]), [int(v) for v in cent[k]]]
    palette = sorted(((n, round(s, 3), rgb) for n, s, rgb in named.values()),
                     key=lambda t: -t[1])
    g = graphic_stats(ctx.image)
    word = ("muted" if colourful < 25 else "moderately colourful" if colourful < 55
            else "highly colourful")
    summary = (f"{word.capitalize()} (colourfulness {colourful:.0f}); dominant "
               f"colours " + ", ".join(f"{n} {s:.0%}" for n, s, _ in palette[:3])
               + f"; natural texture over {g.texture_fraction:.0%} of the frame")
    return Finding(technique=74, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary,
                   detail={"colourfulness": round(colourful, 1), "palette": palette,
                           "graphic": g.as_dict()},
                   confidence=1.0, sources=[SRC_PIX])


# ------------------------------------------------------------- 75, 76

_OBJECTS = {
    "car": ["a car on a road"], "motorcycle": ["a motorcycle or scooter"],
    "bus": ["a bus"], "truck": ["a truck or lorry"],
    "rickshaw": ["an auto rickshaw or tuk-tuk"], "bicycle": ["a bicycle"],
    "person": ["people walking on a street"], "sign": ["a road sign or shop sign"],
    "tree": ["trees"], "building": ["a building facade"],
    "water": ["water, a river or the sea"], "boat": ["a boat"],
    "animal": ["an animal such as a cow, dog or horse"],
    "nothing": ["an empty patch of sky or ground"],
}


def _object_views(ctx: Context):
    """[(label, box or None, embedding, presence)] for the whole frame and
    the region of interest, computed once per run."""
    if "objects" in ctx.memo:
        return ctx.memo["objects"]
    out = []
    p = zero_shot(ctx, _OBJECTS, "objects")
    if p:
        out.append(("the whole frame", None, ctx.embedding, p))
    r = roi(ctx)
    if r is not None:
        box, emb = r
        q = zero_shot(ctx, _OBJECTS, "objects", embedding=emb)
        if q:
            out.append((f"{window_name(box)}, up close", box, emb, q))
    ctx.memo["objects"] = out
    return out


@technique(id=75, key="objects", name="Object detection & segmentation",
           phase=PHASE, needs=("embedding", "pixels"),
           fusion_note="Coarse zero-shot presence in the whole frame and in "
                       "its most detailed region -- not bounding-box detection "
                       "or segmentation, which need a detector model this "
                       "project does not ship.")
def objects(ctx: Context) -> Finding:
    sp = spec(75)
    views = _object_views(ctx)
    if not views:
        return _unavailable(sp, "the image encoder is not available")
    rows, regions = [], []
    for label, box, _emb, p in views:
        top = sorted(((k, v) for k, v in p.items() if k != "nothing"),
                     key=lambda kv: -kv[1])[:3]
        shown = [(k, v) for k, v in top if v >= 0.15]
        if shown and shown[0][1] >= 0.30:
            rows.append(f"{label}: " + ", ".join(f"{k} {v:.0%}" for k, v in shown))
            if box is not None:
                regions.append(Region(*box, label=shown[0][0]))
    if not rows:
        return Finding.nothing_found(sp, "No object stands out, in the whole frame "
                                         "or up close.")
    return Finding(technique=75, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True,
                   summary="What is there (coarse, zero-shot presence, not "
                           "detection): " + "; ".join(rows) + ".",
                   detail={"views": [{"view": label, "box": list(box) if box else None,
                                      "presence": {k: round(v, 3) for k, v in p.items()}}
                                     for label, box, _e, p in views]},
                   confidence=0.4, regions=regions, sources=[SRC_CLIP])


_VEHICLES = {
    "auto-rickshaw": ["a yellow and green three-wheeled auto rickshaw"],
    "tuk-tuk": ["a colourful Thai tuk-tuk"], "kei car": ["a small boxy Japanese kei car"],
    "pickup": ["a large American pickup truck"], "sedan": ["a sedan car"],
    "hatchback": ["a small hatchback car"], "suv": ["an SUV"],
    "minibus": ["a shared minibus or matatu"], "double-decker": ["a double-decker bus"],
    "tram": ["a tram on rails"], "motorbike": ["a motorbike or scooter"],
    "lorry": ["a painted goods lorry"],
}


@technique(id=76, key="fine_grained", name="Fine-grained object recognition",
           phase=PHASE, needs=("embedding", "pixels"),
           fusion_note="Vehicle type, in whichever view shows vehicles most. "
                       "Region-typical vehicles are already scored by "
                       "technique 22; this reports, and does not add weight.")
def fine_grained(ctx: Context) -> Finding:
    sp = spec(76)
    views = _object_views(ctx)
    if not views:
        return _unavailable(sp, "the image encoder is not available")
    veh = ("car", "motorcycle", "bus", "truck", "rickshaw")
    label, box, emb, presence = max(views, key=lambda v: sum(v[3].get(k, 0) for k in veh))
    if sum(presence.get(k, 0) for k in veh) < 0.35:
        return Finding.nothing_found(sp, "No vehicles are visible clearly enough to "
                                         "identify their type.")
    p = zero_shot(ctx, _VEHICLES, "vehicle-types", embedding=emb) or {}
    ranked = sorted(p.items(), key=lambda kv: -kv[1])
    if not ranked:
        return _unavailable(sp, "the encoder could not score vehicle types")
    decisive = ranked[0][1] >= 0.3
    return Finding(technique=76, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=decisive,
                   summary=(f"In {label} the vehicles read as {ranked[0][0]} "
                            f"({ranked[0][1]:.0%}), then "
                            + ", ".join(f"{k} {v:.0%}" for k, v in ranked[1:3])
                            + " — reported only"),
                   detail={"view": label, "box": list(box) if box else None,
                           "ranked": [(k, round(v, 3)) for k, v in ranked[:6]]},
                   confidence=ranked[0][1] if decisive else 0.0,
                   regions=[Region(*box, label=ranked[0][0])] if box else [],
                   sources=[SRC_CLIP])


# ------------------------------------------------------------- 77, 78

def _luma(ctx: Context) -> np.ndarray:
    im = ctx.image.convert("L")
    im.thumbnail((512, 512), Image.Resampling.BILINEAR)
    return np.asarray(im, dtype=np.float32)


@technique(id=77, key="enhancement", name="Image enhancement / deblurring / denoising",
           phase=PHASE, needs=("pixels",),
           fusion_note="Only when the frame is dark or flat: equalises it and "
                       "reads the text again, reporting anything the first "
                       "reading missed.")
def enhancement(ctx: Context) -> Finding:
    sp = spec(77)
    y = _luma(ctx)
    dark, flat = float(y.mean()) < 70, float(y.std()) < 35
    detail = {"mean_luma": round(float(y.mean()), 1), "contrast": round(float(y.std()), 1)}
    if not (dark or flat):
        return Finding(technique=77, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail=detail, confidence=0.0,
                       summary="Well exposed; no enhancement needed.",
                       sources=[SRC_PIX])
    # A re-read is an OCR pass -- seconds on a CPU for a whole frame -- so
    # it is aimed. Fragments the first reading could not make out are
    # re-read in a crop around them; the whole frame is re-read only when
    # the signage reading sees script and the first reading found nothing.
    first = getattr(ctx.ocr, "lines", None) or []
    weak = [l for l in first if l.confidence < 0.5 and l.box]
    script = reading(ctx, "signage_script")
    script_seen = (script is not None and script.option_key not in ABSENT
                   and script.confidence >= 0.35)
    state = "dark" if dark else "flat"
    if weak:
        x0, y0 = min(l.box[0] for l in weak), min(l.box[1] for l in weak)
        x1, y1 = max(l.box[2] for l in weak), max(l.box[3] for l in weak)
        px, py = max(0.05, (x1 - x0) * 0.5), max(0.05, (y1 - y0) * 0.5)
        box = (max(0.0, x0 - px), max(0.0, y0 - py), min(1.0, x1 + px), min(1.0, y1 + py))
        target = "around the text the first reading could not make out"
    elif script_seen and not first:
        box = (0.0, 0.0, 1.0, 1.0)
        target = "across the frame, where the signage reading sees script"
    else:
        reason = ("the first reading was already confident" if first else
                  "neither the text reader nor the signage reading found any "
                  "sign of text")
        return Finding(technique=77, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail=detail, confidence=0.0,
                       summary=f"The frame is {state}, but {reason}, so it was not re-read.",
                       sources=[SRC_PIX])
    detail["reread_box"] = [round(v, 3) for v in box]
    ocr_engine = getattr(ctx.engine, "ocr", None)
    if ocr_engine is None:
        return Finding(technique=77, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail=detail, confidence=0.0,
                       summary=(f"The frame is {state}; text could be re-read after "
                                f"equalising, but OCR is not running."),
                       sources=[SRC_PIX])
    import cv2
    w, h = ctx.image.size
    crop = ctx.image.crop((int(box[0] * w), int(box[1] * h),
                           int(box[2] * w), int(box[3] * h))).convert("RGB")
    lab = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    enhanced = Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
    if max(enhanced.size) < 800:               # small crops read better larger
        s = 800 / max(enhanced.size)
        enhanced = enhanced.resize((max(1, int(enhanced.width * s)),
                                    max(1, int(enhanced.height * s))),
                                   Image.Resampling.LANCZOS)
    try:
        again = ocr_engine.read(enhanced)
    except Exception as exc:
        return _unavailable(sp, f"re-reading failed ({type(exc).__name__})")
    # A fragment re-read with confidence counts as recovered; only text the
    # first reading already read confidently is not new.
    before = {l.text.strip().lower() for l in first if l.confidence >= 0.5}
    new = [l for l in again.lines if l.confidence >= 0.4
           and l.text.strip().lower() not in before]

    def to_frame(b):
        return (box[0] + b[0] * (box[2] - box[0]), box[1] + b[1] * (box[3] - box[1]),
                box[0] + b[2] * (box[2] - box[0]), box[1] + b[3] * (box[3] - box[1]))

    detail["new_lines"] = [{"text": l.text, "confidence": round(l.confidence, 3),
                            "box": [round(v, 4) for v in to_frame(l.box)] if l.box else None}
                           for l in new[:20]]
    if not new:
        return Finding(technique=77, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail=detail, confidence=0.0,
                       summary=f"Equalised and re-read {target}: nothing the first "
                               f"reading missed.",
                       sources=[SRC_PIX, "EasyOCR (Apache-2.0)"])
    return Finding(technique=77, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, detail=detail, confidence=0.5,
                   summary=(f"Equalising the {state} frame and re-reading {target} "
                            f"recovered {len(new)} line(s) of text: "
                            + " | ".join(l.text for l in new[:5])),
                   regions=[Region(*to_frame(l.box), label="text after enhancement")
                            for l in new[:8] if l.box],
                   sources=[SRC_PIX, "EasyOCR (Apache-2.0)"])


@technique(id=78, key="low_light", name="Low-light / infrared analysis",
           phase=PHASE, needs=("pixels",),
           fusion_note="Light level, noise, and the near-monochrome signature "
                       "of infrared or night-vision capture.")
def low_light(ctx: Context) -> Finding:
    sp = spec(78)
    y = _luma(ctx)
    im = ctx.image.convert("HSV")
    im.thumbnail((256, 256))
    sat = float(np.asarray(im, dtype=np.float32)[:, :, 1].mean() / 255.0)
    mean = float(y.mean())
    noise = float(np.median(np.abs(np.diff(y, axis=1))))
    ir = sat < 0.06 and mean < 110 and ctx.image.mode not in ("L", "1")
    detail = {"mean_luma": round(mean, 1), "saturation": round(sat, 3),
              "noise": round(noise, 2), "infrared_like": ir}
    if ir:
        summary = ("Near-monochrome and dark: the signature of infrared or "
                   "night-vision capture (or a black-and-white conversion)")
    elif mean < 50:
        summary = f"A low-light frame (mean brightness {mean:.0f}/255)"
    else:
        return Finding(technique=78, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail=detail, confidence=0.0,
                       summary=f"Not low-light (mean brightness {mean:.0f}/255).",
                       sources=[SRC_PIX])
    return Finding(technique=78, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.6, sources=[SRC_PIX])


# ------------------------------------------------------------- 79-81

_REFLECT = {"window": ["a scene reflected in a shop window or glass facade"],
            "mirror": ["a reflection in a mirror"],
            "water": ["buildings reflected in calm water"],
            "wet_road": ["reflections on a wet road at night"],
            "none": ORDINARY_SCENES}


@technique(id=79, key="reflections", name="Reflections & mirror-surface analysis",
           phase=PHASE, needs=("embedding",),
           fusion_note="Flags reflections, which can show what is behind the "
                       "camera. Reported only.")
def reflections(ctx: Context) -> Finding:
    sp = spec(79)
    p = zero_shot(ctx, _REFLECT, "reflections")
    if p is None:
        return _unavailable(sp, "the image encoder is not available")
    best = max(p.items(), key=lambda kv: kv[1])
    if best[0] == "none" or best[1] < 0.6:
        return Finding.nothing_found(sp, "No clear reflective surface.")
    return Finding(technique=79, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True,
                   summary=(f"A reflection reads as present: {best[0].replace('_', ' ')} "
                            f"({best[1]:.0%}). A reflection can show what stood "
                            f"behind the camera -- worth a close look by eye."),
                   detail={"presence": {k: round(v, 3) for k, v in p.items()}},
                   confidence=best[1], sources=[SRC_CLIP])


@technique(id=80, key="glass_reconstruction", name="Glass/reflection scene reconstruction",
           phase=PHASE,
           fusion_note="Separating a reflected scene from a transmitted one "
                       "needs specialised layer-separation models this "
                       "project does not have.")
def glass_reconstruction(ctx: Context) -> Finding:
    return _unavailable(spec(80), "reconstructing a reflected scene needs a "
                                  "layer-separation model, which is not installed")


@technique(id=81, key="water_reflection", name="Water-reflection analysis",
           phase=PHASE, needs=("pixels",),
           fusion_note="A mirrored scene below a waterline is measured by "
                       "flipping and comparing; a symmetric skyline can then "
                       "be read twice.")
def water_reflection(ctx: Context) -> Finding:
    sp = spec(81)
    y = _luma(ctx)
    h = y.shape[0]
    best = (0.0, None)
    for frac in np.linspace(0.35, 0.75, 9):
        cut = int(h * frac)
        n = min(cut, h - cut, int(h * 0.3))
        if n < 16:
            continue
        up = y[cut - n:cut][::-1]
        down = y[cut:cut + n]
        a, b = up - up.mean(), down - down.mean()
        denom = math.sqrt(float((a * a).sum() * (b * b).sum())) + 1e-9
        corr = float((a * b).sum() / denom)
        if corr > best[0]:
            best = (corr, frac)
    p = zero_shot(ctx, _REFLECT, "reflections")
    water = (p or {}).get("water", 0.0)
    found = best[0] > 0.65 and (water > 0.25 or p is None)
    detail = {"mirror_correlation": round(best[0], 3), "waterline": best[1],
              "water_reading": round(water, 3)}
    if not found:
        return Finding(technique=81, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail=detail, confidence=0.0,
                       summary="No mirrored scene in water.", sources=[SRC_PIX])
    return Finding(technique=81, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, detail=detail, confidence=min(1.0, best[0]),
                   summary=(f"A mirrored scene about {best[1]:.0%} down the frame "
                            f"(correlation {best[0]:.2f}): a waterline, with the "
                            f"skyline reflected below it."),
                   regions=[Region(0, best[1], 1, min(1, best[1] + 0.3),
                                   label="reflection")],
                   sources=[SRC_PIX, SRC_CLIP])


# ------------------------------------------------------------- 82-84

@technique(id=82, key="lens_calibration", name="Lens distortion / camera calibration estimation",
           phase=PHASE, needs=("pixels",),
           fusion_note="The intrinsic parameters the file supports, and the "
                       "distortion its focal length makes likely. Correction "
                       "is not applied.")
def lens_calibration(ctx: Context) -> Finding:
    from ..chrono.sun_in_frame import field_of_view
    sp = spec(82)
    other = getattr(ctx.exif, "other", {}) or {}
    w, h = ctx.image.size
    fov = field_of_view(other, w, h)
    detail = {"principal_point": [w / 2, h / 2], "principal_point_assumed": True}
    if fov is None:
        return Finding(technique=82, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail=detail, confidence=0.0,
                       summary="No 35 mm-equivalent focal length in the file, so "
                               "the camera cannot be calibrated from metadata.",
                       sources=[SRC_EXIF])
    fx = (w / 2) / math.tan(math.radians(fov[0]) / 2)
    f35 = float(other.get("FocalLengthIn35mmFilm"))
    distortion = ("strong barrel distortion likely (ultra-wide)" if f35 < 18 else
                  "mild barrel distortion likely (wide)" if f35 < 28 else
                  "little distortion expected")
    detail.update({"fx_px": round(fx, 1), "fy_px": round(fx, 1),
                   "fov_deg": [round(fov[0], 1), round(fov[1], 1)],
                   "distortion_expectation": distortion})
    return Finding(technique=82, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True,
                   summary=(f"Focal length about {fx:.0f} px ({f35:.0f} mm "
                            f"equivalent, {fov[0]:.0f}° across); principal point "
                            f"assumed at the centre; {distortion}. Not corrected."),
                   detail=detail, confidence=0.7, sources=[SRC_EXIF])


@technique(id=83, key="rolling_shutter", name="Rolling-shutter artifact analysis",
           phase=PHASE,
           fusion_note="Skew from a rolling shutter shows only under fast "
                       "motion across the frame; a still of a static scene "
                       "carries none to measure.")
def rolling_shutter(ctx: Context) -> Finding:
    return _unavailable(spec(83), "rolling-shutter skew needs fast motion across "
                                  "the frame, and is not measured in stills")


@technique(id=84, key="depth", name="Depth / 3D scene reconstruction",
           phase=PHASE,
           fusion_note="Needs a monocular depth network. None is installed; "
                       "85 and 86 use single-view geometry instead.")
def depth(ctx: Context) -> Finding:
    return _unavailable(spec(84), "no depth-estimation model is installed; "
                                  "techniques 85 and 86 use single-view geometry")


# ------------------------------------------------------------- 85, 86

CAMERA_HEIGHT_M = 1.6


def _horizon(ctx: Context):
    from .phase4_vision import _geometry
    g = _geometry(ctx)
    return g if g.horizon_y is not None and g.focal_px else None


# Single-view metrology assumes a roughly level camera and a horizon in
# view. Outside that it returns numbers -- a horizon "139% above the
# frame", the tallest thing in Shibuya "0.2 m high" -- that are artefacts
# of the method, not measurements.
_MAX_TILT_DEG = 20.0


def _level_view(ctx: Context):
    """(geometry, copy height, pitch-down radians), or (None, why not)."""
    g = _horizon(ctx)
    if g is None:
        return None, "the horizon could not be located"
    if not 0.1 <= g.horizon_y <= 0.9:
        return None, (f"the horizon estimate ({g.horizon_y:.0%} down the frame) "
                      f"is outside the frame or at its edge")
    # The geometry ran on a copy scaled to at most 1000 px; its focal
    # length is in that copy's pixels, so the height must be too.
    w, h = ctx.image.size
    h_copy = h * min(1.0, 1000.0 / max(w, h))
    if g.pitch_deg is not None:
        # Two independent estimates of the same thing: the horizon drawn
        # through the horizontal vanishing points, and the one the
        # verticals' tilt implies. When they disagree, neither is trusted.
        implied = 0.5 + math.tan(math.radians(g.pitch_deg)) * g.focal_px / h_copy
        if abs(implied - g.horizon_y) > 0.15:
            return None, (f"the horizon line ({g.horizon_y:.0%} down the frame) and "
                          f"the camera tilt from the verticals ({implied:.0%}) disagree")
    pitch = math.atan((0.5 - g.horizon_y) * h_copy / g.focal_px)
    if abs(math.degrees(pitch)) > _MAX_TILT_DEG:
        return None, (f"the camera is tilted about {abs(math.degrees(pitch)):.0f}°, "
                      f"too far for level-camera geometry")
    return (g, h_copy, pitch), None


@technique(id=85, key="relative_distance", name="Relative-distance estimation",
           phase=PHASE, needs=("pixels",),
           fusion_note="Single-view metrology from the horizon and an assumed "
                       "1.6 m camera height. Rough by construction; stated so, "
                       "and only attempted with the horizon in view and the "
                       "camera near level.")
def relative_distance(ctx: Context) -> Finding:
    sp = spec(85)
    view, why = _level_view(ctx)
    if view is None:
        return Finding.nothing_found(sp, f"Distances are not estimated: {why}.")
    g, h_copy, pitch = view
    f = g.focal_px
    # The bottom row's angle below the horizon: its angle below the optical
    # axis plus the axis's own pitch below the horizon.
    angle = math.atan((h_copy / 2) / f) + pitch
    if angle <= 1e-3:
        return Finding.nothing_found(sp, "The frame does not reach below the "
                                         "horizon: no ground to measure.")
    d = CAMERA_HEIGHT_M / math.tan(angle)
    return Finding(technique=85, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True,
                   summary=(f"The bottom edge of the frame is roughly {d:.0f} m "
                            f"from the camera, the horizon {g.horizon_y:.0%} down "
                            f"the frame -- assuming a camera 1.6 m up on level "
                            f"ground"),
                   detail={"bottom_distance_m": round(d, 1),
                           "horizon_y": round(g.horizon_y, 3),
                           "camera_height_assumed_m": CAMERA_HEIGHT_M,
                           "focal_assumed": g.focal_assumed},
                   confidence=0.3, sources=[SRC_PIX])


@technique(id=86, key="object_height", name="Object-height / scale estimation",
           phase=PHASE, needs=("pixels",),
           fusion_note="Height of the tallest standing vertical edge, from the "
                       "horizon and an assumed camera height. Rough; stated so. "
                       "Only edges that rise past eye level are reported: a "
                       "shorter one is usually a fragment of a taller edge.")
def object_height(ctx: Context) -> Finding:
    from ..forensics.geometry import _segments
    sp = spec(86)
    view, why = _level_view(ctx)
    if view is None:
        return Finding.nothing_found(sp, f"Heights are not estimated: {why}.")
    g = view[0]
    segs, w, h = _segments(ctx.image)
    yh = g.horizon_y * h
    best = None
    for x1, y1, x2, y2 in segs:
        ang = abs(math.degrees(math.atan2(y2 - y1, x2 - x1))) % 180
        if abs(ang - 90) > 8:
            continue
        yb, yt = max(y1, y2), min(y1, y2)
        if yb <= yh + 5 or yb < 0.6 * h:
            continue                    # must stand on the ground below the horizon
        if yt >= yh:
            continue                    # stops below eye level: likely a fragment
        height = CAMERA_HEIGHT_M * (yb - yt) / (yb - yh)
        if best is None or height > best[0]:
            best = (height, (min(x1, x2) / w, yt / h, max(x1, x2) / w + 0.01, yb / h))
    if best is None:
        return Finding.nothing_found(sp, "No vertical edge stands on visible ground "
                                         "and rises past eye level.")
    return Finding(technique=86, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True,
                   summary=(f"The tallest vertical edge standing on the ground rises "
                            f"about {best[0]:.1f} m (camera assumed 1.6 m up; "
                            f"±30% at best)"),
                   detail={"height_m": round(best[0], 2),
                           "camera_height_assumed_m": CAMERA_HEIGHT_M},
                   confidence=0.25, regions=[Region(*best[1], label="measured edge")],
                   sources=[SRC_PIX])


# ------------------------------------------------------------- 87-89

@technique(id=87, key="shadow_object", name="Shadow-object matching",
           phase=PHASE,
           fusion_note="Pairing each shadow with its caster needs shadow "
                       "detection, which is not attempted.")
def shadow_object(ctx: Context) -> Finding:
    return _unavailable(spec(87), "shadow detection is not attempted; techniques "
                                  "34 and 35 predict what shadows should look like")


@technique(id=88, key="multi_shadow", name="Multiple-shadow-source detection",
           phase=PHASE,
           fusion_note="Needs shadow detection, as 87.")
def multi_shadow(ctx: Context) -> Finding:
    return _unavailable(spec(88), "shadow detection is not attempted")


@technique(id=89, key="occlusion", name="Occlusion analysis", phase=PHASE,
           fusion_note="Needs scene segmentation and depth ordering, which "
                       "are not available.")
def occlusion(ctx: Context) -> Finding:
    return _unavailable(spec(89), "occlusion reasoning needs segmentation and "
                                  "depth, neither of which is installed")


# ---------------------------------------------------------------- 90

@technique(id=90, key="sun_obstruction", name="Sun obstruction / horizon analysis",
           phase=PHASE, needs=("gps", "time"),
           fusion_note="With the camera's position known to metres, asks the "
                       "local building store whether anything stood between "
                       "the camera and the sun at the capture instant.")
def sun_obstruction(ctx: Context) -> Finding:
    from ..chrono.capture_time import resolve
    from ..chrono.solar import sun_position
    sp = spec(90)
    fix = ctx.exif.gps
    ct = resolve(ctx.raw_bytes, ctx.exif, fix.latitude, fix.longitude)
    if not ct.known:
        return _unavailable(sp, "no capture instant")
    sun = sun_position(fix.latitude, fix.longitude, ct.utc)
    if sun.elevation <= 0:
        return Finding.nothing_found(sp, "The sun was below the horizon.")
    try:
        from ..geodata.service import get_service
        from ..geodata.store import bbox_around, decode_ring
        store = get_service().store
        raw = store.query(bbox_around(fix.latitude, fix.longitude, 200), ("building",))
    except Exception as exc:
        return _unavailable(sp, f"no local building data ({type(exc).__name__})")
    blockers = []
    for src, f in raw["building"]:
        ring = decode_ring(f[8][0])
        cx = sum(p[0] for p in ring) / len(ring)
        cy = sum(p[1] for p in ring) / len(ring)
        dx = (cx - fix.longitude) * 111320 * math.cos(math.radians(fix.latitude))
        dy = (cy - fix.latitude) * 111320
        dist = math.hypot(dx, dy)
        if dist < 3:
            continue
        az = (math.degrees(math.atan2(dx, dy)) + 360) % 360
        if abs((az - sun.azimuth + 540) % 360 - 180) > 12:
            continue
        height = f[3] / 10.0
        angle = math.degrees(math.atan2(height, dist))
        if angle > sun.elevation:
            blockers.append({"distance_m": round(dist), "height_m": height,
                             "angle": round(angle, 1), "source": src,
                             "height_source": {0: "assumed", 1: "levels",
                                               2: "measured", 3: "published"}.get(f[5])})
    if not raw["building"]:
        return Finding.nothing_found(sp, "No buildings are stored around the "
                                         "camera position to test.")
    if not blockers:
        return Finding(technique=90, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=0.5,
                       summary=(f"The sun ({sun.elevation:.0f}° up in the "
                                f"{sun.azimuth:.0f}° direction) had a clear line "
                                f"to the camera: no stored building in that "
                                f"direction rises above it. Expect direct sun and "
                                f"hard shadows at the camera."),
                       detail={"sun_elevation": round(sun.elevation, 2)},
                       sources=["OpenStreetMap / Open Buildings (local store)"])
    b = min(blockers, key=lambda x: x["distance_m"])
    return Finding(technique=90, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.5,
                   summary=(f"A building {b['distance_m']} m toward the sun "
                            f"({b['height_m']:.0f} m tall, height "
                            f"{b['height_source']}) rose {b['angle']:.0f}° -- above "
                            f"the sun's {sun.elevation:.0f}°. The camera stood in "
                            f"its shadow, if the heights are right."),
                   detail={"blockers": blockers[:5],
                           "sun_elevation": round(sun.elevation, 2)},
                   sources=["OpenStreetMap / Open Buildings (local store)"])
