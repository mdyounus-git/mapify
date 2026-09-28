"""Techniques 1-15: the file, the text in it, and the buildings.

The first stage of the specification, in its order: metadata before
pixels, text before inference, and every one of them reporting what it
saw rather than what it would like to conclude.

Almost all of this wraps code that already works and is already
measured. That is the point of wrapping rather than rewriting: the
EXIF reader, the OCR routing and the address resolver were built,
benchmarked and corrected over several phases, and a fresh
implementation behind a nicer interface would throw that away. What is
new here is #5 (pixel inspection), #9 (legal forms in business names),
#11 (reading plates rather than scoring their appearance), and the
three descriptive prompt groups behind #10, #14 and #15.

Three of these techniques report and do not steer -- #10, #14, #15 --
because measurement said so. They are here in full because the
specification asked for them and because a human reader can use an
observation the fusion cannot.
"""

from __future__ import annotations

from ..data.business_forms import find_forms
from ..data.plate_formats import looks_like_plate, match_plate
from ..forensics import inspect_pixels
from .base import Constraint, Context, Finding, Region, technique

PHASE = 1

# Where each wrapped technique's evidence actually comes from, named once
# so the case file's provenance is not a guess.
_SRC_EXIF = "the file's own Exif/XMP metadata"
_SRC_OCR = "EasyOCR (Apache-2.0), run locally"
_SRC_CLIP = "OpenAI CLIP ViT-L/14 (MIT), zero-shot, run locally"
_SRC_NOMINATIM = "OpenStreetMap via Nominatim (ODbL)"


def _box_region(line, label: str) -> list[Region]:
    """The evidence-map rectangle for one OCR line, when it has one."""
    box = getattr(line, "box", None)
    if not box:
        return []
    x0, y0, x1, y1 = box
    return [Region(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), label)]


def _reading(ctx: Context, group_key: str):
    ev = ctx.evidence
    return ev.by_group(group_key) if ev is not None else None


def _describe_reading(spec, reading, *, steers: bool) -> Finding:
    """Report one zero-shot group, with an honest note about its weight."""
    if reading is None:
        return Finding.not_applicable(spec, "the zero-shot pass did not run")
    runners = ", ".join(f"{label} {p:.0%}" for label, p in reading.runners_up[:2])
    summary = f"{reading.option_label} ({reading.confidence:.0%})"
    if runners:
        summary += f"; then {runners}"
    if not steers:
        summary += " — reported only, moves no coordinate"
    # A group whose top two options are a coin-flip has seen nothing.
    decisive = (reading.confidence >= 0.30 and reading.margin >= 0.08)
    return Finding(
        technique=spec.id, key=spec.key, name=spec.name, phase=spec.phase,
        ran=True, found=decisive,
        summary=summary if decisive else
        f"No clear reading (best was {reading.option_label} at "
        f"{reading.confidence:.0%}, too close to call)",
        detail={"option": reading.option_key,
                "confidence": round(reading.confidence, 4),
                "margin": round(reading.margin, 4),
                "ranked": [(k, round(v, 4)) for k, v in reading.ranked],
                "feeds_location": bool(steers and reading.called)},
        confidence=reading.confidence if decisive else 0.0,
        sources=[_SRC_CLIP],
    )


# ---------------------------------------------------------------- 1

@technique(id=1, key="exif", name="EXIF / metadata extraction", phase=PHASE,
           needs=("exif",),
           feeds_fusion=False,
           fusion_note="The container itself constrains nothing; the GPS "
                       "tag inside it does, and that is technique 2.")
def exif_metadata(ctx: Context) -> Finding:
    spec = _spec(1)
    ex = ctx.exif
    parts = []
    if ex.camera_make or ex.camera_model:
        parts.append(" ".join(x for x in (ex.camera_make, ex.camera_model) if x))
    if ex.captured_at:
        parts.append(f"taken {ex.captured_at}")
    if ex.gps:
        parts.append("carries a GPS tag")
    if ex.software:
        parts.append(f"written by {ex.software}")
    extra = len(ex.other)
    summary = "; ".join(parts) if parts else "metadata present but sparse"
    if extra:
        summary += f" ({extra} further tags)"
    return Finding(
        technique=1, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True, summary=summary,
        detail=ex.as_dict(),
        confidence=1.0,          # it is a reading, not an inference
        sources=[_SRC_EXIF],
    )


# ---------------------------------------------------------------- 2

@technique(id=2, key="gps_tag", name="GPS coordinates", phase=PHASE,
           needs=("gps",),
           feeds_fusion=True,
           fusion_note="An embedded fix is the strongest single piece of "
                       "evidence a file can carry, and is used as such -- "
                       "but only after the image is asked whether it "
                       "agrees, because a tag can be edited in seconds.")
def gps_tag(ctx: Context) -> Finding:
    spec = _spec(2)
    fix = ctx.exif.gps
    warnings = list(ctx.exif.trust_warnings)
    summary = f"{fix.latitude:.6f}, {fix.longitude:.6f}"
    if fix.altitude_m is not None:
        summary += f" at {fix.altitude_m:.0f} m"
    if fix.processing_method:
        summary += f" ({fix.processing_method})"
    if warnings:
        summary += f" — {len(warnings)} reason(s) to doubt it"
    # Strength drops when the file itself looks edited. The verdict on
    # whether the picture agrees with the tag is the pipeline's, not
    # this technique's; this only reports how far the tag can be trusted
    # on its own terms.
    strength = 1.0 if not warnings else max(0.25, 1.0 - 0.25 * len(warnings))
    return Finding(
        technique=2, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True, summary=summary,
        detail={"latitude": fix.latitude, "longitude": fix.longitude,
                "altitude_m": fix.altitude_m,
                "timestamp_utc": fix.timestamp_utc,
                "hpositioning_error_m": fix.hpositioning_error_m,
                "processing_method": fix.processing_method,
                "datum": fix.datum,
                "trust_warnings": warnings},
        confidence=strength,
        constraint=Constraint(kind="point",
                              value=[fix.latitude, fix.longitude],
                              strength=strength,
                              note="embedded GPS tag"),
        sources=[_SRC_EXIF],
    )


# ---------------------------------------------------------------- 3

@technique(id=3, key="capture_time", name="Capture timestamp", phase=PHASE,
           needs=("time",),
           feeds_fusion=False,
           fusion_note="A timestamp says when, not where. It becomes "
                       "geographic only through the solar work in the "
                       "chronolocation phase.")
def capture_time(ctx: Context) -> Finding:
    spec = _spec(3)
    ex = ctx.exif
    when = ex.captured_at
    detail = {"captured_at": when}
    notes = []
    gps_time = getattr(ex.gps, "timestamp_utc", None) if ex.gps else None
    if gps_time:
        detail["gps_timestamp_utc"] = gps_time
        notes.append(f"the GPS tag records {gps_time} UTC")
    # Warnings about the timestamp were already raised by the reader;
    # repeat the relevant ones here so this finding stands alone.
    clock = [w for w in ex.trust_warnings
             if "time" in w.lower() or "date" in w.lower()]
    if clock:
        detail["warnings"] = clock
        notes.extend(clock)
    summary = f"{when} (camera clock, local time as the camera had it)"
    if notes:
        summary += " — " + "; ".join(notes)
    return Finding(
        technique=3, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True, summary=summary, detail=detail,
        confidence=0.6 if clock else 1.0,
        sources=[_SRC_EXIF],
    )


# ---------------------------------------------------------------- 4

@technique(id=4, key="device", name="Camera / device identification",
           phase=PHASE, needs=("exif",),
           feeds_fusion=False,
           fusion_note="Device models are sold worldwide. Knowing the "
                       "camera narrows nothing geographic, though it "
                       "matters for whether two photographs came from "
                       "one source.")
def device(ctx: Context) -> Finding:
    spec = _spec(4)
    ex = ctx.exif
    bits = {k: v for k, v in (("make", ex.camera_make),
                              ("model", ex.camera_model),
                              ("lens", ex.lens),
                              ("software", ex.software)) if v}
    if not bits:
        return Finding.nothing_found(
            spec, "The metadata names no camera, lens or software.")
    summary = ", ".join(f"{k}: {v}" for k, v in bits.items())
    return Finding(
        technique=4, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True, summary=summary, detail=bits,
        confidence=1.0, sources=[_SRC_EXIF],
    )


# ---------------------------------------------------------------- 5

@technique(id=5, key="pixels", name="Resolution & pixel inspection",
           phase=PHASE, needs=("pixels",),
           feeds_fusion=False,
           fusion_note="Nothing here locates anything. It says how much "
                       "the rest of the analysis should be trusted, "
                       "which is a different and prior question.")
def pixel_inspection(ctx: Context) -> Finding:
    spec = _spec(5)
    rep = inspect_pixels(ctx.image, ctx.raw_bytes, ctx.exif)
    summary = "; ".join(rep.notes)
    if rep.signals:
        summary += f" — {len(rep.signals)} thing(s) worth knowing about "
        summary += "this file's history"
    return Finding(
        technique=5, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True, summary=summary,
        detail=rep.as_dict(),
        # Measurements, not guesses; the uncertainty lives in what each
        # signal is said to mean, and that is spelled out per signal.
        confidence=1.0,
        sources=["the image file itself"],
    )


# ---------------------------------------------------------------- 6

@technique(id=6, key="ocr", name="OCR / text extraction", phase=PHASE,
           needs=("ocr",),
           feeds_fusion=False,
           fusion_note="The raw text constrains nothing by itself. What "
                       "is done with it -- addresses (8), script (12), "
                       "legal forms (9), plates (11) -- does.")
def ocr_text(ctx: Context) -> Finding:
    spec = _spec(6)
    lines = ctx.ocr.lines
    strong = [l for l in lines if l.confidence >= 0.5]
    preview = " | ".join(l.text for l in lines[:6])
    regions = []
    for line in lines[:40]:
        regions.extend(_box_region(line, line.text[:40]))
    return Finding(
        technique=6, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True,
        summary=f"{len(lines)} line(s) of text, {len(strong)} read "
                f"confidently: {preview}"[:400],
        detail={"engine": ctx.ocr.engine,
                "languages": ctx.ocr.languages,
                "line_count": len(lines),
                "lines": [l.as_dict() for l in lines]},
        confidence=(sum(l.confidence for l in lines) / len(lines)) if lines else 0.0,
        regions=regions,
        sources=[_SRC_OCR],
    )


# ---------------------------------------------------------------- 7

@technique(id=7, key="signboards", name="Signboards & billboards", phase=PHASE,
           needs=("ocr",),
           feeds_fusion=False,
           fusion_note="Identifying a sign as a sign proves nothing. "
                       "Resolving what it says to a place is technique 8, "
                       "and that one does move the answer.")
def signboards(ctx: Context) -> Finding:
    spec = _spec(7)
    lines = ctx.ocr.lines
    if not lines:
        return Finding.nothing_found(spec)
    # The largest text in the frame is the sign a photographer was
    # pointing at, far more often than not. Height of the box is the
    # measure; a wide thin banner is not "big text".
    sized = []
    for line in lines:
        if not line.box:
            continue
        x0, y0, x1, y1 = line.box
        sized.append((abs(y1 - y0), line))
    if not sized:
        return Finding(
            technique=7, key=spec.key, name=spec.name, phase=PHASE,
            ran=True, found=bool(lines),
            summary="Text was read, but the engine gave no positions, so "
                    "which of it is signage cannot be told apart.",
            detail={"line_count": len(lines)},
            confidence=0.2, sources=[_SRC_OCR],
        )
    sized.sort(key=lambda p: -p[0])
    tallest = sized[0][0]
    prominent = [line for h, line in sized if h >= tallest * 0.55][:6]
    regions = []
    for line in prominent:
        regions.extend(_box_region(line, "prominent sign text"))
    return Finding(
        technique=7, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True,
        summary="Largest text in the frame: "
                + " | ".join(l.text for l in prominent),
        detail={"prominent": [l.as_dict() for l in prominent],
                "tallest_fraction_of_frame": round(tallest, 4)},
        confidence=min(1.0, tallest * 6),   # a bigger sign is a surer read
        regions=regions,
        sources=[_SRC_OCR],
    )


# ---------------------------------------------------------------- 8

@technique(id=8, key="addresses", name="Street names & addresses", phase=PHASE,
           needs=("ocr",),
           feeds_fusion=True,
           fusion_note="Measured in Phase 21: a legible address board took "
                       "the error to 0.00 km. This is the strongest "
                       "evidence in the pipeline after an embedded GPS "
                       "tag, and the only one that is genuinely "
                       "independent of the image encoder.")
def addresses(ctx: Context) -> Finding:
    spec = _spec(8)
    places = ctx.read_places or []
    if not places:
        # Distinguish "looked it up and nothing matched" from "never
        # looked it up", which offline mode makes a real possibility.
        if not ctx.online:
            return Finding.not_applicable(
                spec, "address lookup needs the gazetteer service and this "
                      "run was offline")
        return Finding.nothing_found(
            spec, "No line of the text resolved to a place.")
    best = places[0]
    others = [p.result.display_name for p in places[1:4]]
    summary = f"{best.result.display_name} (read from \"{best.query}\")"
    if best.corroborated:
        summary += ", and the picture agrees with it"
    if others:
        summary += f"; also matched {len(others)} other place(s)"
    return Finding(
        technique=8, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True, summary=summary,
        detail={"places": [{"display_name": p.result.display_name,
                            "latitude": p.result.latitude,
                            "longitude": p.result.longitude,
                            "query": p.query,
                            "weight": round(p.weight, 4),
                            "overlap": round(p.overlap, 4),
                            "corroborated": p.corroborated,
                            "why": p.why, "note": p.note}
                           for p in places[:8]]},
        confidence=min(1.0, best.weight),
        constraint=Constraint(
            kind="point",
            value=[best.result.latitude, best.result.longitude],
            strength=min(1.0, best.weight),
            note=f"address read from the image: {best.query}"),
        sources=[_SRC_OCR, _SRC_NOMINATIM],
    )


# ---------------------------------------------------------------- 9

@technique(id=9, key="business_forms", name="Business names & legal forms",
           phase=PHASE, needs=("ocr",),
           feeds_fusion=False,
           fusion_note="Not yet measured. A legal form is genuinely "
                       "independent evidence -- it is statute, not another "
                       "opinion from the image encoder -- so it is a real "
                       "candidate for the fusion. It stays out until it "
                       "has been run against the benchmark, because that "
                       "is the rule that killed the last four additions.")
def business_forms(ctx: Context) -> Finding:
    spec = _spec(9)
    hits: list[tuple[object, object]] = []
    for line in ctx.ocr.lines:
        if line.confidence < 0.3:
            continue
        for form in find_forms(line.text):
            hits.append((form, line))
    if not hits:
        return Finding.nothing_found(
            spec, "No company legal form appears in the recognised text.")

    # A form that fits four countries or fewer says something; "Ltd" does
    # not, and must not be presented as though it did.
    sharp = [(f, l) for f, l in hits if f.discriminating]
    shown = sharp or hits
    countries: set[str] = set()
    for form, _ in sharp:
        countries |= set(form.countries)

    lines_out = []
    regions = []
    for form, line in shown[:6]:
        lines_out.append({"text": line.text, "form": form.label,
                          "countries": sorted(form.countries),
                          "discriminating": form.discriminating,
                          "note": form.note})
        regions.extend(_box_region(line, form.label))

    if sharp:
        summary = "; ".join(f"\"{l.text}\" — {f.label}" for f, l in sharp[:3])
        if countries:
            summary += (f" → registered under the law of "
                        f"{', '.join(sorted(countries))}")
    else:
        summary = ("; ".join(f"\"{l.text}\" — {f.label}" for f, l in hits[:3])
                   + " — used in too many countries to narrow anything")
    return Finding(
        technique=9, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True, summary=summary,
        detail={"matches": lines_out,
                "countries": sorted(countries),
                "logo_recognition": "not attempted: no logo dataset with a "
                                    "licence this project can use"},
        confidence=0.8 if sharp else 0.25,
        regions=regions,
        sources=[_SRC_OCR, "hand-maintained table of company legal forms"],
    )


# ---------------------------------------------------------------- 10

@technique(id=10, key="road_signs", name="Road signs & traffic signs",
           phase=PHASE, needs=("evidence",),
           feeds_fusion=False,
           fusion_note="Measured in Phase 22 with real country sets "
                       "attached: it never once cleared its threshold. "
                       "Zero of 163 benchmark samples moved. Kept as an "
                       "observation for the reader; permanently barred "
                       "from the score.")
def road_signs(ctx: Context) -> Finding:
    return _describe_reading(_spec(10), _reading(ctx, "sign_style"),
                             steers=False)


# ---------------------------------------------------------------- 11

@technique(id=11, key="plates", name="License plate reading", phase=PHASE,
           needs=("ocr",),
           feeds_fusion=False,
           fusion_note="Plate formats collide across jurisdictions, so a "
                       "match is usually plural and weak. The appearance "
                       "of plates is already scored separately, by the "
                       "PLATE prompt group, which is measured and does "
                       "feed the fusion.")
def plates(ctx: Context) -> Finding:
    spec = _spec(11)
    found = []
    for line in ctx.ocr.lines:
        if line.confidence < 0.25:
            continue
        if not looks_like_plate(line.text, line.box):
            continue
        schemes = match_plate(line.text)
        found.append((line, schemes))
    if not found:
        return Finding.nothing_found(
            spec, "No recognised text has the shape of a registration plate.")

    matched = [(l, s) for l, s in found if s]
    regions = []
    detail = []
    for line, schemes in found[:8]:
        regions.extend(_box_region(line, f"plate? {line.text}"))
        detail.append({
            "text": line.text,
            "ocr_confidence": round(line.confidence, 3),
            "schemes": [{"countries": sorted(s.countries),
                         "format": s.label, "note": s.note}
                        for s in schemes],
        })

    if not matched:
        return Finding(
            technique=11, key=spec.key, name=spec.name, phase=PHASE,
            ran=True, found=True,
            summary=f"{len(found)} plate-shaped reading(s) "
                    f"({', '.join(l.text for l, _ in found[:4])}), none "
                    f"matching a registration format this tool knows",
            detail={"candidates": detail},
            confidence=0.15, regions=regions, sources=[_SRC_OCR],
        )

    line, schemes = matched[0]
    countries = sorted({c for s in schemes for c in s.countries})
    unique = len(countries) == 1
    summary = f"\"{line.text}\" fits " + ", ".join(
        f"{s.label} ({'/'.join(sorted(s.countries))})" for s in schemes[:3])
    if unique:
        summary += " — one jurisdiction only"
    else:
        summary += f" — {len(countries)} jurisdictions share this shape"
    return Finding(
        technique=11, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True, summary=summary,
        detail={"candidates": detail, "countries": countries,
                "single_jurisdiction": unique},
        # OCR on a plate at street distance is not reliable, and the
        # format space is crowded. Even the unique case is capped well
        # short of certainty.
        confidence=(0.55 if unique else 0.25) * min(1.0, line.confidence * 1.4),
        regions=regions,
        sources=[_SRC_OCR, "hand-written table of national registration formats"],
    )


# ---------------------------------------------------------------- 12

@technique(id=12, key="script", name="Language & script identification",
           phase=PHASE, needs=("ocr",),
           feeds_fusion=True,
           fusion_note="Writing system maps to the countries that sign in "
                       "it, and has been in the fusion since Phase 10. It "
                       "is read from the text rather than guessed from "
                       "the embedding, which is why it survives where the "
                       "prompt-group clues did not.")
def script(ctx: Context) -> Finding:
    spec = _spec(12)
    reading = ctx.script_reading
    if reading is None or not reading.total_letters:
        return Finding.nothing_found(
            spec, "Not enough recognised letters to identify a script.")
    dominant = reading.dominant()
    if not dominant:
        return Finding.nothing_found(
            spec, f"{reading.total_letters} letters read, none of any one "
                  f"script often enough to call it.")
    langs = sorted(reading.language_hints.items(), key=lambda kv: -kv[1])[:4]
    summary = ", ".join(f"{code} {share:.0%}" for code, share in dominant)
    if langs:
        summary += " — wording suggests " + ", ".join(c for c, _ in langs)
    top_script, top_share = dominant[0]
    return Finding(
        technique=12, key=spec.key, name=spec.name, phase=PHASE,
        ran=True, found=True, summary=summary,
        detail=reading.as_dict(),
        confidence=top_share,
        constraint=Constraint(kind="script", value=top_script,
                              strength=top_share,
                              note="countries that sign in this script"),
        sources=[_SRC_OCR, "Unicode script ranges (ISO 15924)"],
    )


# ---------------------------------------------------------------- 13

@technique(id=13, key="architecture", name="Architecture analysis", phase=PHASE,
           needs=("evidence",),
           feeds_fusion=True,
           fusion_note="In the fusion since Phase 9, with subregion "
                       "constraints. Coarse, but it survived measurement.")
def architecture(ctx: Context) -> Finding:
    return _describe_reading(_spec(13), _reading(ctx, "architecture"),
                             steers=True)


# ---------------------------------------------------------------- 14

@technique(id=14, key="facade", name="Building shapes & facades", phase=PHASE,
           needs=("evidence",),
           feeds_fusion=False,
           fusion_note="Facade material is close kin to the architecture "
                       "group and comes from the same embedding, so it "
                       "would add correlated weight rather than new "
                       "information. Reported; not scored.")
def facade(ctx: Context) -> Finding:
    return _describe_reading(_spec(14), _reading(ctx, "facade"), steers=False)


# ---------------------------------------------------------------- 15

@technique(id=15, key="openings", name="Windows, doors and structure",
           phase=PHASE, needs=("evidence",),
           feeds_fusion=False,
           fusion_note="The roof-form version of this was measured in "
                       "Phase 22 and pushed the median error from 342 km "
                       "to 368. Reported; not scored.")
def openings(ctx: Context) -> Finding:
    return _describe_reading(_spec(15), _reading(ctx, "openings"), steers=False)


# --------------------------------------------------------------------------

def _spec(technique_id: int):
    """The registered Technique, for findings built by hand.

    Imported lazily from the registry rather than passed in, so a
    technique body never has to know how it was registered.
    """
    from .base import REGISTRY
    return REGISTRY[technique_id]
