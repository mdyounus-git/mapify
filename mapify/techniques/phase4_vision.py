"""Techniques 43-56: geometry, cross-references, and whether the file is honest.

Three kinds of thing live here.

Geometry (43-45): vanishing points, how the camera was held, what lens
it had. None of it locates anything; all of it conditions what else can
be measured -- a sun elevation read from the frame assumes a level
camera, and 44 is what says whether it was.

Cross-references (46-53): comparing the photograph with something
outside it. Some are genuinely possible offline or with sources whose
terms allow it (47 against the Landsat built-up layer, 49 against the
gazetteers already consulted). Several are not, and are registered so
that the report says so rather than being silent: reverse image search
and repost tracing (50, 51) need services that charge or forbid
automated use; street-level comparison (48) needs imagery whose licence
forbids this, or a Mapillary token the user has not given; landmark
geometry matching (46) needs a reference database no free licence
provides at scale; video techniques (52, 53) need a video, and this is a
still.

Forensics (54-56): error level, noise, compression history, and what
the file declares about AI generation. Signals with their innocent
explanations, never verdicts -- see mapify/forensics/tamper.py.
"""

from __future__ import annotations

import io

from PIL import Image

from ..forensics.geometry import analyse_geometry
from ..forensics.provenance import read_provenance
from ..forensics.tamper import error_levels, jpeg_ghost, noise_consistency
from ..chrono.sun_in_frame import field_of_view
from .base import Context, Finding, Region, technique
from .common import SRC_EXIF, SRC_NE, SRC_NOMINATIM, leader, reading, spec

PHASE = 4
SRC_CV = "OpenCV line-segment detector (Apache-2.0), run locally"
SRC_FORENSIC = "classical image forensics, computed locally"


def _geometry(ctx: Context):
    if "geometry" not in ctx.memo:
        other = getattr(ctx.exif, "other", {}) or {}
        fov = (field_of_view(other, ctx.image.width, ctx.image.height)
               if ctx.image is not None else None)
        ctx.memo["geometry"] = analyse_geometry(ctx.image, fov)
    return ctx.memo["geometry"]


def _original(ctx: Context) -> Image.Image:
    """The image as the file holds it -- not the resized analysis copy.

    Forensics has to look at the bytes that were uploaded: resizing for
    the models is itself a resampling and would bury what it measures.
    """
    if "original" not in ctx.memo:
        try:
            im = Image.open(io.BytesIO(ctx.raw_bytes))
            im.load()
        except Exception:
            im = ctx.image
        ctx.memo["original"] = im
    return ctx.memo["original"]


def _blocked(sp, why: str) -> Finding:
    return Finding.not_applicable(sp, why)


# ---------------------------------------------------------------- 43-45

@technique(id=43, key="vanishing_points", name="Vanishing points",
           phase=PHASE, needs=("pixels",),
           fusion_note="Geometry of the frame; locates nothing by itself.")
def vanishing_points(ctx: Context) -> Finding:
    sp = spec(43)
    g = _geometry(ctx)
    if g.segments < 12:
        return Finding.nothing_found(
            sp, f"Only {g.segments} straight line(s): not enough structure to "
                f"find vanishing points (open country, foliage or a close-up).")
    parts = [f"{g.segments} straight segments, {g.vertical_segments} near-vertical"]
    if g.vertical_vp:
        parts.append("the verticals converge "
                     + ("above" if g.vertical_vp[1] < 0 else "below")
                     + " the frame")
    if g.horizontal_vps:
        parts.append(f"{len(g.horizontal_vps)} horizontal vanishing point(s)")
    return Finding(technique=43, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=True, summary="; ".join(parts),
                   detail=g.as_dict(), confidence=min(1.0, g.segments / 150),
                   sources=[SRC_CV])


@technique(id=44, key="camera_orientation", name="Camera orientation",
           phase=PHASE, needs=("pixels",),
           fusion_note="Roll from leaning verticals, tilt from where they "
                       "converge. Feeds the sun-elevation measurement's "
                       "assumptions, not the location.")
def camera_orientation(ctx: Context) -> Finding:
    sp = spec(44)
    g = _geometry(ctx)
    if g.roll_deg is None and g.pitch_deg is None:
        return Finding.nothing_found(
            sp, "Not enough vertical structure to say how the camera was held.")
    bits = []
    if g.roll_deg is not None:
        bits.append(f"rolled {abs(g.roll_deg):.1f}° "
                    + ("clockwise" if g.roll_deg > 0 else "anticlockwise")
                    if abs(g.roll_deg) >= 0.5 else "not rolled")
    if g.pitch_deg is not None:
        bits.append("held level" if abs(g.pitch_deg) < 3 else
                    f"tilted {abs(g.pitch_deg):.0f}° "
                    + ("up" if g.pitch_deg > 0 else "down"))
    summary = "The camera was " + " and ".join(bits)
    if g.focal_assumed:
        summary += " (tilt assumes a typical phone lens; the file records none)"
    orient = getattr(ctx.exif, "orientation", None)
    detail = g.as_dict()
    detail["exif_orientation"] = orient
    return Finding(technique=44, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=True, summary=summary, detail=detail,
                   confidence=0.4 if g.focal_assumed else 0.7,
                   sources=[SRC_CV] + ([SRC_EXIF] if orient else []))


@technique(id=45, key="lens", name="Focal length / lens", phase=PHASE,
           needs=("exif",),
           fusion_note="The lens says what the frame covers; it is what "
                       "turns a position in the frame into an angle.")
def lens(ctx: Context) -> Finding:
    sp = spec(45)
    other = getattr(ctx.exif, "other", {}) or {}
    f = other.get("FocalLength")
    f35 = other.get("FocalLengthIn35mmFilm")
    lens_name = getattr(ctx.exif, "lens", None)
    if f is None and f35 is None and not lens_name:
        return Finding.nothing_found(sp, "The file records no lens or focal length.")
    parts, detail = [], {"focal_length": f, "focal_35mm": f35, "lens": lens_name}
    if lens_name:
        parts.append(str(lens_name))
    if f is not None:
        parts.append(f"{f} mm")
    fov = (field_of_view(other, ctx.image.width, ctx.image.height)
           if ctx.image is not None else None)
    if fov:
        detail["fov_deg"] = [round(fov[0], 1), round(fov[1], 1)]
        parts.append(f"{f35} mm equivalent: a {fov[0]:.0f}° × {fov[1]:.0f}° field "
                     f"of view")
    else:
        parts.append("no 35 mm-equivalent focal length, so the field of view "
                     "is not known")
    return Finding(technique=45, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=True, summary="; ".join(parts), detail=detail,
                   confidence=1.0, sources=[SRC_EXIF])


# ---------------------------------------------------------------- 46-53

@technique(id=46, key="landmark_geometry", name="Geometric landmark matching",
           phase=PHASE,
           fusion_note="Needs a reference database of landmark geometry. No "
                       "free licence provides one at scale; the locator's "
                       "gallery is the implicit form (technique 25).")
def landmark_geometry(ctx: Context) -> Finding:
    return _blocked(spec(46), "no openly licensed database of landmark "
                              "geometry exists to match against; see "
                              "technique 25 for what the locator recognises")


@technique(id=47, key="satellite_compare", name="Satellite-image comparison",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="Checks a built-up scene against Landsat's built-up "
                       "settlement layer at the leading candidate, from the "
                       "local tile cache. Reported, unmeasured as a fusion "
                       "input.")
def satellite_compare(ctx: Context) -> Finding:
    sp = spec(47)
    rd = reading(ctx, "setting")
    if rd is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    top = leader(ctx)
    ranked = dict(rd.ranked)
    urban = ranked.get("cbd", 0) + ranked.get("oldtown", 0) + \
        0.6 * ranked.get("suburb", 0) + 0.5 * ranked.get("industrial", 0)
    try:
        from ..geodata.tiles import TileCache
        import math
        z = 12
        n = 2 ** z
        x = int((top.longitude + 180) / 360 * n)
        lat_r = math.radians(top.latitude)
        y = int((1 - math.asinh(math.tan(lat_r)) / math.pi) / 2 * n)
        cache = TileCache(offline=not ctx.online)
        data, why = cache.get("builtup", z, x, y)
    except Exception as exc:
        data, why = None, f"tile unavailable ({type(exc).__name__})"
    if data is None:
        return Finding.not_applicable(sp, f"no Landsat built-up tile for the "
                                          f"leading candidate: {why}")
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    import numpy as np
    a = np.asarray(im)
    built = float((a[:, :, 3] > 40).mean())            # coloured pixels are built-up
    detail = {"urban_reading": round(urban, 3), "builtup_fraction_z12": round(built, 4)}
    contradiction = urban >= 0.6 and built < 0.01
    detail["contradiction"] = contradiction
    summary = (f"The scene reads as built-up ({urban:.0%}); Landsat maps "
               f"{built:.0%} of the ~10 km tile around the leading candidate as "
               f"settlement")
    summary += (" — a contradiction: no settlement there" if contradiction
                else " — consistent" if urban >= 0.6 else "")
    return Finding(technique=47, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=True, summary=summary, detail=detail,
                   confidence=0.4,
                   sources=["Landsat Human Built-up and Settlement Extent via "
                            "NASA GIBS (public domain)"])


@technique(id=48, key="street_view", name="Street-view comparison",
           phase=PHASE,
           fusion_note="Google Street View's terms forbid this use. Mapillary "
                       "(CC BY-SA 4.0) is the honest route and needs an access "
                       "token from a free account, which only the user can "
                       "create.")
def street_view(ctx: Context) -> Finding:
    return _blocked(spec(48), "Street View is not licensed for this; Mapillary "
                              "would be, but needs an access token from the "
                              "user's own free account, and none is configured")


@technique(id=49, key="gis_crossref", name="Map / GIS cross-reference",
           phase=PHASE, needs=("candidates",),
           fusion_note="The gazetteers the analysis already consulted, "
                       "listed as provenance for the location.")
def gis_crossref(ctx: Context) -> Finding:
    sp = spec(49)
    top = leader(ctx)
    loc = getattr(top, "location", None) or {}
    nom = getattr(top, "nominatim", None) or {}
    names = []
    sources = [SRC_NE]
    if isinstance(loc, dict) and loc.get("country"):
        names.append(f"Natural Earth: {loc['country'].get('name')}")
    if isinstance(nom, dict) and nom.get("display_name"):
        names.append(f"OpenStreetMap: {nom['display_name'][:80]}")
        sources.append(SRC_NOMINATIM)
    if ctx.read_places:
        names.append(f"{len(ctx.read_places)} place(s) resolved from text")
    if not names:
        return Finding.nothing_found(sp, "No gazetteer described the leading "
                                         "candidate.")
    return Finding(technique=49, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=True, summary="; ".join(names),
                   detail={"natural_earth": loc if isinstance(loc, dict) else None,
                           "nominatim": nom if isinstance(nom, dict) else None},
                   confidence=0.8, sources=sources)


@technique(id=50, key="reverse_image", name="Reverse-image search",
           phase=PHASE,
           fusion_note="Every engine with an API charges or forbids automated "
                       "use; uploading the image would also send it to a "
                       "third party, which this project will not do.")
def reverse_image(ctx: Context) -> Finding:
    return _blocked(spec(50), "every reverse-image service either charges or "
                              "forbids automated use, and it would mean sending "
                              "the photograph to a third party")


@technique(id=51, key="repost_tracing", name="Source / repost tracing",
           phase=PHASE,
           fusion_note="Same as 50: needs services that forbid it.")
def repost_tracing(ctx: Context) -> Finding:
    return _blocked(spec(51), "tracing reposts needs the same prohibited "
                              "services as reverse-image search")


@technique(id=52, key="video_frames", name="Video frame extraction",
           phase=PHASE,
           fusion_note="Applies to video input. Several stills of one place "
                       "can be combined today through a session (132).")
def video_frames(ctx: Context) -> Finding:
    return _blocked(spec(52), "the input is a single still image; for several "
                              "frames of one place, add them to a session")


@technique(id=53, key="adjacent_frames", name="Adjacent-frame comparison",
           phase=PHASE,
           fusion_note="Applies to video input; see 52.")
def adjacent_frames(ctx: Context) -> Finding:
    return _blocked(spec(53), "the input is a single still image")


# ---------------------------------------------------------------- 54-56

def _signal_finding(tid: int, sig, extra_sources=()) -> Finding:
    sp = spec(tid)
    regions = [Region(*box, label=sig.name.replace("_", " "))
               for box in sig.regions[:6]]
    summary = sig.summary
    if sig.flagged and sig.innocent:
        summary += f". Innocent explanation: {sig.innocent}."
    return Finding(technique=tid, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=sig.flagged, summary=summary,
                   detail={**sig.detail, "innocent_explanation": sig.innocent},
                   confidence=0.35 if sig.flagged else 0.0, regions=regions,
                   sources=[SRC_FORENSIC, *extra_sources])


@technique(id=54, key="manipulation", name="Manipulation / compositing detection",
           phase=PHASE, needs=("pixels",),
           fusion_note="Error-level and noise-consistency signals, reported "
                       "with their innocent explanations. Never a verdict.")
def manipulation(ctx: Context) -> Finding:
    im = _original(ctx)
    ela = error_levels(im)
    noise = noise_consistency(im)
    f = _signal_finding(54, ela)
    f.detail = {"error_level": {**ela.detail, "flagged": ela.flagged,
                                "summary": ela.summary},
                "noise": {**noise.detail, "flagged": noise.flagged,
                          "summary": noise.summary},
                "innocent_explanation": ela.innocent + "; " + noise.innocent}
    f.found = ela.flagged or noise.flagged
    f.regions += [Region(*b, label="noise") for b in noise.regions[:6]]
    f.summary = (f"Error level: {ela.summary} Noise: {noise.summary}")
    if f.found:
        f.summary += (" These are pointers for a closer look; each has "
                      "ordinary causes, listed in the detail.")
        f.confidence = 0.35
    return f


@technique(id=55, key="compression", name="Compression & editing artifacts",
           phase=PHASE, needs=("pixels",),
           fusion_note="Compression history from the JPEG ghost test: was "
                       "the file saved more than once?")
def compression(ctx: Context) -> Finding:
    im = _original(ctx)
    return _signal_finding(55, jpeg_ghost(im, getattr(im, "format", None)))


@technique(id=56, key="ai_generation", name="AI-generation detection",
           phase=PHASE, needs=("pixels",),
           fusion_note="Reads what the file declares -- C2PA, IPTC source "
                       "type, generator settings. No pixel-level detector: "
                       "they are unreliable and a false 'real' is worse than "
                       "no answer.")
def ai_generation(ctx: Context) -> Finding:
    sp = spec(56)
    p = read_provenance(ctx.raw_bytes, ctx.exif)
    if p.ai_declared or p.c2pa:
        summary = "; ".join(p.evidence)
        if p.ai_declared:
            summary = "The file declares AI involvement: " + summary
        return Finding(technique=56, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=True, summary=summary,
                       detail=p.as_dict(), confidence=0.9 if p.ai_declared else 0.5,
                       sources=["the file's own metadata (C2PA / IPTC / XMP / PNG)"])
    return Finding(
        technique=56, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=False,
        summary=("No AI-generation declaration in the file. That proves "
                 "nothing: generators that do not declare themselves, and "
                 "files whose metadata was stripped, look exactly like this."),
        detail=p.as_dict(), confidence=0.0,
        sources=["the file's own metadata (C2PA / IPTC / XMP / PNG)"])
