"""Techniques 143-153: putting the evidence together, and saying how sure.

These run last and read everything before them. None of them looks at
the image; they weigh, cross-check and bound what the other 142 found,
and record how the answer was reached.

Two rules run through all of them.

**Uncertainty is measured, not asserted.** The error intervals (147,
148) come from the 163-photograph benchmark: for each confidence band,
how far from the truth the answers in that band actually were. A
"high" answer was within 0.8 km half the time and within 23.5 km nine
times in ten; a "low" one had a median error of 728 km. Those are the
numbers reported, with their sample sizes, rather than a radius anybody
chose.

**Agreement only counts between independent lines of evidence.** The
image model agreeing with itself through twelve prompt groups is one
line. Text read off a sign, the file's own metadata, the sun's position
and the local map are others. Cross-validation (144) counts lines, not
findings.

The evidence map (151) is data -- every marked region with the technique
that marked it -- for the interface to draw over the photograph; no
annotated copy of anyone's picture is written to disk. The provenance
record (152) goes into the case file the analysis writes.
"""

from __future__ import annotations

import hashlib
import json
import platform
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .base import REGISTRY, Context, Finding, technique
from .common import leader, spec
from .spec import TITLES

PHASE = 10
SRC_ALL = "every earlier technique's finding"

# Measured, not chosen: bench/final.json (2026-09-27), the 163-sample
# benchmark run offline without OCR, error from the true location by the
# band the answer was given. Small samples; the quantiles are approximate,
# and the app with OCR and online naming does at least this well.
BAND_ERROR_KM = {
    "high": {"n": 26, "p50": 0.8, "p75": 5.1, "p90": 23.5},
    "medium": {"n": 24, "p50": 16.1, "p75": 302.6, "p90": 8507.8},
    "low": {"n": 78, "p50": 727.9, "p75": 2314.1, "p90": 6773.7},
    "insufficient": {"n": 35, "p50": 4448.5, "p75": 11229.5, "p90": 14479.7},
}
BENCH_SOURCE = ("Mapify's own benchmark: 163 photographs with known locations "
                "(bench/final.json, 2026-09-27)")


def _all(ctx: Context) -> dict[int, Finding]:
    return dict(sorted(ctx.memo.get("findings", {}).items()))


def _km(v: float) -> str:
    return f"{v * 1000:.0f} m" if v < 1 else f"{v:,.0f} km" if v >= 10 else f"{v:.1f} km"


def _family(f: Finding) -> str:
    """Which independent line of evidence a finding belongs to.

    Classified by what it read from the image first, and by the data it
    checked against second: a street name matched against the map is text.
    """
    s = " ".join(f.sources).lower()
    if "easyocr" in s or "nominatim" in s:
        return "text read from the image"
    if "exif" in s or "file name" in s or "metadata" in s:
        return "the file's metadata"
    if "forensic" in s:
        return "forensics"
    if "clip" in s:
        return "the image model"
    if "noaa" in s or "meeus" in s or "ephemeris" in s:
        return "astronomy"
    if "open-meteo" in s or "era5" in s or "copernicus" in s:
        return "weather and terrain records"
    if any(k in s for k in ("openstreetmap", "landsat", "natural earth", "case store",
                            "open buildings", "openfreemap")):
        return "map data"
    return "other"


# --------------------------------------------------------------- 143

@technique(id=143, key="reliability", name="Clue reliability weighting",
           phase=PHASE,
           fusion_note="States what each clue weighs: the fusion's measured "
                       "weight for the few that steer the location, zero for "
                       "everything reported only.")
def reliability(ctx: Context) -> Finding:
    sp = spec(143)
    found = [f for f in _all(ctx).values() if f.ran and f.found]
    if not found:
        return Finding.nothing_found(sp, "No finding to weigh.")
    fusion = getattr(ctx.cfg, "fusion", None)
    weights = {k: getattr(fusion, k) for k in dir(fusion) if k.startswith("w_")} \
        if fusion is not None else {}
    steering, by_family = [], Counter()
    for f in found:
        by_family[_family(f)] += 1
        t = REGISTRY.get(f.technique)
        if t is not None and t.feeds_fusion:
            steering.append(f.technique)
    rows = [{"technique": f.technique, "family": _family(f),
             "own_confidence": round(f.confidence, 3),
             "steers_location": f.technique in steering} for f in found]
    return Finding(technique=143, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.8,
                   summary=(f"{len(found)} findings weighed. {len(steering)} steer the "
                            f"location, with weights measured on the benchmark "
                            f"(techniques {', '.join(map(str, steering)) or 'none'}); "
                            f"the rest are reported with weight zero. By line of "
                            f"evidence: " + ", ".join(f"{k} {v}" for k, v in
                                                      by_family.most_common()) + "."),
                   detail={"rows": rows, "steering": steering,
                           "fusion_weights": {k: round(v, 3) for k, v in weights.items()},
                           "by_family": dict(by_family)},
                   sources=[SRC_ALL, BENCH_SOURCE])


# --------------------------------------------------------------- 144

_ADJUSTMENT_FAMILY = {"ocr_script": "text read from the image",
                      "toponym": "text read from the image",
                      "read_place": "text read from the image"}


@technique(id=144, key="cross_validation", name="Independent-evidence cross-validation",
           phase=PHASE,
           fusion_note="Counts independent lines of evidence for and against "
                       "the leading candidate -- lines, not findings.")
def cross_validation(ctx: Context) -> Finding:
    sp = spec(144)
    top = leader(ctx)
    if top is None:
        return Finding.nothing_found(sp, "No leading candidate to cross-validate.")
    support: dict[str, list] = defaultdict(list)
    oppose: dict[str, list] = defaultdict(list)
    support["the image model"].append("locator")
    for adj in getattr(top, "adjustments", []) or []:
        fam = _ADJUSTMENT_FAMILY.get(getattr(adj, "source", ""), "the image model")
        if getattr(adj, "delta", 0) > 0:
            support[fam].append(adj.source)
        elif getattr(adj, "delta", 0) < 0:
            oppose[fam].append(adj.source)
    agreement = (ctx.prediction or {}).get("exif_vs_estimate") or {}
    if agreement.get("verdict") == "corroborated":
        support["the file's metadata"].append(2)
    elif agreement.get("verdict") == "rejected":
        oppose["the file's metadata"].append(2)
    for tid, f in _all(ctx).items():
        if tid >= 143 or not f.ran:
            continue
        d = f.detail or {}
        fam = _family(f)
        if d.get("contradiction") is True:
            oppose[fam].append(tid)
        elif d.get("leader_inside") is True:
            support[fam].append(tid)
    for_lines = sorted(support)
    against = sorted(oppose)
    summary = ("The leading candidate is supported by "
               + f"{len(for_lines)} independent line(s) of evidence ("
               + "; ".join(f"{k}: {', '.join(map(str, v[:5]))}" for k, v in
                           sorted(support.items())) + ")")
    if against:
        summary += (f" and opposed by {len(against)} ("
                    + "; ".join(f"{k}: {', '.join(map(str, v[:5]))}" for k, v in
                                sorted(oppose.items())) + ")")
    summary += (". Only the image model" if for_lines == ["the image model"] else
                ". Agreement between lines that cannot share an error is what makes "
                "a location credible")
    if for_lines == ["the image model"]:
        summary += " supports it: nothing independent confirms the location yet"
    return Finding(technique=144, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.7,
                   summary=summary + ".",
                   # Not a "contradiction" key: 145 lists the underlying ones,
                   # and counting this summary too would count them twice.
                   detail={"supporting_lines": {k: v for k, v in support.items()},
                           "opposing_lines": {k: v for k, v in oppose.items()},
                           "independent_support": len(for_lines),
                           "opposed": bool(against)},
                   sources=[SRC_ALL])


# --------------------------------------------------------------- 145

@technique(id=145, key="contradictions", name="Contradiction detection between clues",
           phase=PHASE,
           fusion_note="Every finding that contradicts another clue or the "
                       "answer, in one list. A contradiction means one side is "
                       "wrong, not which.")
def contradictions(ctx: Context) -> Finding:
    sp = spec(145)
    hits = [(tid, f) for tid, f in _all(ctx).items()
            if tid < 145 and f.ran and (f.detail or {}).get("contradiction") is True]
    if not hits:
        checked = sum(1 for f in _all(ctx).values()
                      if f.ran and "contradiction" in (f.detail or {}))
        return Finding(technique=145, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, confidence=0.0,
                       summary=(f"No contradictions: {checked} finding(s) were checked "
                                f"against the other evidence and none disagreed."),
                       detail={"checked": checked}, sources=[SRC_ALL])
    return Finding(technique=145, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.8,
                   summary=(f"{len(hits)} contradiction(s): "
                            + " | ".join(f"{tid} {f.name}: {f.summary[:120]}"
                                         for tid, f in hits[:6])
                            + ". Each means one of the two sides is wrong; the reader "
                              "decides which."),
                   detail={"contradictions": [{"technique": tid, "name": f.name,
                                               "summary": f.summary}
                                              for tid, f in hits]},
                   sources=[SRC_ALL])


# --------------------------------------------------------------- 146

@technique(id=146, key="evidence_ranking", name="Candidate ranking by evidence strength",
           phase=PHASE, needs=("candidates",),
           fusion_note="Each candidate's fused probability beside the narrow "
                       "text clues for and against it. Reported for the reader; "
                       "it does not reorder the answer.")
def evidence_ranking(ctx: Context) -> Finding:
    sp = spec(146)
    narrow = [(tid, f.detail.get("countries", [])) for tid, f in _all(ctx).items()
              if f.ran and f.found and f.detail.get("narrow") and f.detail.get("countries")]
    rows = []
    for rank, c in enumerate(ctx.candidates[:5], start=1):
        iso = getattr(c, "iso_a2", None)
        pro = [tid for tid, cs in narrow if iso and iso in cs]
        con = [tid for tid, cs in narrow if iso and iso not in cs]
        rows.append({"rank": rank, "iso": iso,
                     "probability": round(float(getattr(c, "probability", 0.0)), 4),
                     "text_for": pro, "text_against": con})
    lead = rows[0]
    better = [r for r in rows[1:] if len(r["text_for"]) > len(lead["text_for"])
              and len(r["text_against"]) < len(lead["text_against"])]
    summary = "Ranked by the fusion: " + "; ".join(
        f"#{r['rank']} {r['iso'] or '?'} {r['probability']:.0%}"
        + (f" (text for: {', '.join(map(str, r['text_for']))})" if r["text_for"] else "")
        + (f" (text against: {', '.join(map(str, r['text_against']))})"
           if r["text_against"] else "") for r in rows)
    if better:
        summary += (f". Candidate #{better[0]['rank']} has stronger text support than "
                    f"the leader — worth a look")
    return Finding(technique=146, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.6, summary=summary + ".",
                   detail={"rows": rows, "text_favours_another": bool(better)},
                   sources=[SRC_ALL])


# ------------------------------------------------------------ 147, 148

def _band(ctx: Context):
    pred = ctx.prediction or {}
    return pred.get("confidence"), pred.get("estimated") or {}, pred


@technique(id=147, key="error_radius", name="Uncertainty / error-radius estimation",
           phase=PHASE,
           fusion_note="The typical error of answers given this confidence "
                       "band on the benchmark, and the spread of the shortlist.")
def error_radius(ctx: Context) -> Finding:
    sp = spec(147)
    band, est, pred = _band(ctx)
    if not pred.get("determinate") or est.get("latitude") is None:
        return Finding.nothing_found(sp, "The location is not determined, so there "
                                         "is no error radius to give.")
    detail = {"band": band, "shortlist_spread_km": pred.get("uncertainty_km")}
    if est.get("precision_km"):
        detail["radius_km"] = est["precision_km"]
        return Finding(technique=147, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=0.9, detail=detail,
                       summary=(f"About {_km(est['precision_km'])}: the coordinate is "
                                f"the file's GPS tag, which the picture corroborates."),
                       sources=[SRC_ALL])
    stats = BAND_ERROR_KM.get(band)
    if stats is None:
        return Finding.nothing_found(sp, f"No benchmark figures for a '{band}' answer.")
    detail.update(radius_km=stats["p50"], benchmark=stats)
    spread = pred.get("uncertainty_km")
    return Finding(technique=147, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.7, detail=detail,
                   summary=(f"Typical error {_km(stats['p50'])}: half of the benchmark's "
                            f"{band}-confidence answers were closer than that "
                            f"(n={stats['n']})"
                            + (f"; the shortlist itself spans {_km(spread)}"
                               if spread else "") + "."),
                   sources=[BENCH_SOURCE])


@technique(id=148, key="coordinate_interval", name="Coordinate confidence interval",
           phase=PHASE,
           fusion_note="50%, 75% and 90% radii around the estimate, measured on "
                       "the benchmark for the band the answer was given.")
def coordinate_interval(ctx: Context) -> Finding:
    sp = spec(148)
    band, est, pred = _band(ctx)
    if not pred.get("determinate") or est.get("latitude") is None:
        return Finding.nothing_found(sp, "No interval: the location is not "
                                         "determined.")
    lat, lon = est["latitude"], est["longitude"]
    if est.get("precision_km"):
        r = est["precision_km"]
        return Finding(technique=148, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=0.9,
                       summary=(f"{lat:.5f}, {lon:.5f}, within about {_km(r)} (a "
                                f"corroborated GPS tag; a phone's fix is good to "
                                f"roughly that)."),
                       detail={"centre": [lat, lon], "radii_km": {"gps": r}},
                       sources=[SRC_ALL])
    stats = BAND_ERROR_KM.get(band)
    if stats is None:
        return Finding.nothing_found(sp, f"No benchmark figures for a '{band}' answer.")
    radii = {"50%": stats["p50"], "75%": stats["p75"], "90%": stats["p90"]}
    return Finding(technique=148, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.7,
                   summary=(f"Around {lat:.4f}, {lon:.4f}: 50% within {_km(radii['50%'])}, "
                            f"75% within {_km(radii['75%'])}, 90% within "
                            f"{_km(radii['90%'])} — how far the benchmark's "
                            f"{band}-confidence answers were from the truth "
                            f"(n={stats['n']}, so approximate)."),
                   detail={"centre": [lat, lon], "radii_km": radii, "band": band,
                           "n": stats["n"]},
                   sources=[BENCH_SOURCE])


# ------------------------------------------------------------ 149, 150

_SOLAR_WINDOWS = {"day": (9, 15), "night": (19, 5), "diffuse": (8, 17)}


@technique(id=149, key="time_interval", name="Time uncertainty interval",
           phase=PHASE,
           fusion_note="The capture time with its uncertainty, from the file "
                       "when it has one, from the light when it does not.")
def time_interval(ctx: Context) -> Finding:
    from .phase5_temporal import _chrono_time
    sp = spec(149)
    ct = _chrono_time(ctx)
    f68 = ctx.finding(68)
    against = (f68.detail.get("contradicted_by") or []) if f68 is not None else []
    if ct.known:
        unc = ct.uncertainty_min or 1
        a, b = ct.utc - timedelta(minutes=unc), ct.utc + timedelta(minutes=unc)
        basis = {"gps": "the GPS clock", "offset": "the recorded time-zone offset",
                 "zone": "a time zone assumed from the location"}.get(ct.basis, ct.basis)
        summary = (f"Between {a:%Y-%m-%d %H:%M} and {b:%H:%M} UTC (± {unc:.0f} min, "
                   f"from {basis})")
        if against:
            summary += (f"; techniques {', '.join(map(str, against))} contradict it, "
                        f"so the clock or the place is suspect")
        return Finding(technique=149, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=0.4 if against else 0.8,
                       summary=summary + ".",
                       detail={"from_utc": a.isoformat(), "to_utc": b.isoformat(),
                               "basis": ct.basis, "contradicted_by": against},
                       sources=["the file's own Exif/XMP metadata"])
    light = (f68.detail or {}).get("lighting") if f68 is not None and f68.found else None
    if light in _SOLAR_WINDOWS:
        a, b = _SOLAR_WINDOWS[light]
        return Finding(technique=149, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=0.25,
                       summary=(f"Roughly {a:02d}:00 to {b:02d}:00 local solar time, from "
                                f"the light alone — a wide interval, not a measurement."),
                       detail={"from_solar": a, "to_solar": b, "basis": "lighting"},
                       sources=["OpenAI CLIP ViT-L/14 (MIT), zero-shot, run locally"])
    if light in ("golden", "twilight"):
        return Finding(technique=149, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=0.25,
                       summary=("Near sunrise or sunset, from the light alone; which of "
                                "the two, and what clock time, the picture cannot say."),
                       detail={"basis": "lighting", "lighting": light},
                       sources=["OpenAI CLIP ViT-L/14 (MIT), zero-shot, run locally"])
    return Finding.nothing_found(sp, "No capture time and no decisive light: the time "
                                     "cannot be bounded.")


@technique(id=150, key="date_interval", name="Date uncertainty interval",
           phase=PHASE,
           fusion_note="The narrowest date range the evidence supports, each "
                       "bound named with where it came from.")
def date_interval(ctx: Context) -> Finding:
    sp = spec(150)
    lo, hi, why = None, None, []
    f69 = ctx.finding(69)
    bounds = (f69.detail or {}).get("bounds", {}) if f69 is not None and f69.found else {}
    if bounds.get("file"):
        d = datetime.fromisoformat(bounds["file"]).date()
        lo, hi = d - timedelta(days=1), d + timedelta(days=1)
        why.append(f"the file's date ({d:%d %b %Y}, ± a day for time zones)")
    if bounds.get("not_before_year"):
        y = datetime(int(bounds["not_before_year"]), 1, 1).date()
        if lo is None or y > lo:
            lo = y
        why.append(f"text in the picture showing {bounds['not_before_year']} (not earlier)")
    f136 = ctx.finding(136)
    if f136 is not None and f136.found and f136.detail.get("meaning") in (
            "received or saved", "saved"):
        t = datetime.fromisoformat(f136.detail["name_time"]).date()
        if hi is None or t < hi:
            hi = t
        why.append(f"the file name's {f136.detail['meaning']} date ({t:%d %b %Y}, "
                   f"not later)")
    months = bounds.get("months")
    if months:
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                 "Nov", "Dec"]
        why.append("the vegetation, which suits " + ", ".join(names[m - 1] for m in months))
    if not why:
        return Finding.nothing_found(sp, "No evidence bounds the date.")
    contradicted = (f69.detail or {}).get("contradicted_by", []) if f69 is not None else []
    if lo and hi and lo > hi:
        span = "no consistent range: the bounds cross, so one of them is wrong"
    elif lo and hi:
        span = f"between {lo:%d %b %Y} and {hi:%d %b %Y}"
    elif lo:
        span = f"on or after {lo:%d %b %Y}"
    elif hi:
        span = f"on or before {hi:%d %b %Y}"
    else:
        span = "no calendar range, only a season"
    summary = f"Date: {span}; from " + "; ".join(why)
    if contradicted:
        summary += f" — techniques {', '.join(map(str, contradicted))} contradict the file's date"
    return Finding(technique=150, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.7 if bounds.get("file") and not contradicted
                   else 0.3, summary=summary + ".",
                   detail={"from": lo.isoformat() if lo else None,
                           "to": hi.isoformat() if hi else None, "months": months,
                           "basis": why, "contradicted_by": contradicted,
                           "contradiction": bool(lo and hi and lo > hi)},
                   sources=[SRC_ALL])


# --------------------------------------------------------------- 151

# Techniques that measure the file as stored rather than the upright
# analysis copy: forensics has to see the uploaded bytes. Their boxes are
# in the stored orientation and are turned upright before drawing.
_STORED_ORIENTATION = {54, 55, 138, 139, 140}


def _upright(box, orientation: int | None):
    """A normalised box in a stored image, in the upright (displayed) one."""
    x0, y0, x1, y1 = box
    return {
        2: (1 - x1, y0, 1 - x0, y1),            # mirrored
        3: (1 - x1, 1 - y1, 1 - x0, 1 - y0),    # upside down
        4: (x0, 1 - y1, x1, 1 - y0),
        5: (y0, x0, y1, x1),
        6: (1 - y1, x0, 1 - y0, x1),            # stored on its side (phones)
        7: (1 - y1, 1 - x1, 1 - y0, 1 - x0),
        8: (y0, 1 - x1, y1, 1 - x0),
    }.get(orientation or 1, (x0, y0, x1, y1))


@technique(id=151, key="evidence_map", name="Final evidence map / annotated clue regions",
           phase=PHASE,
           fusion_note="Every region any technique marked, with the technique "
                       "that marked it, for the interface to draw over the "
                       "photograph. No annotated copy is written to disk.")
def evidence_map(ctx: Context) -> Finding:
    sp = spec(151)
    orientation = getattr(ctx.exif, "orientation", None)
    regions = []
    for tid, f in _all(ctx).items():
        if tid >= 151 or not f.ran:
            continue
        for r in f.regions:
            d = r.as_dict()
            if tid in _STORED_ORIENTATION and orientation not in (None, 1):
                d["box"] = [round(v, 4) for v in _upright(d["box"], orientation)]
            regions.append({"technique": tid, "name": f.name, "phase": f.phase,
                            "found": f.found, **d})
    if not regions:
        return Finding.nothing_found(sp, "No technique marked a region of the "
                                         "photograph.")
    by_tech = Counter(r["technique"] for r in regions)
    return Finding(technique=151, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=1.0,
                   summary=(f"{len(regions)} marked region(s) from {len(by_tech)} "
                            f"technique(s) ({', '.join(map(str, sorted(by_tech)))}), "
                            f"drawn over the photograph in the Techniques panel."),
                   detail={"regions": regions}, sources=[SRC_ALL])


# --------------------------------------------------------------- 152

def _versions() -> dict[str, str]:
    from importlib import metadata
    out = {"python": platform.python_version()}
    for pkg in ("torch", "transformers", "easyocr", "numpy", "Pillow", "opencv-python",
                "opencv-python-headless", "pyosmium", "fastapi"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            continue
    return out


@technique(id=152, key="provenance", name="Reproducibility / evidence provenance tracking",
           phase=PHASE,
           fusion_note="What produced this answer: the input's hash, the "
                       "models, data sources, versions and settings, and a hash "
                       "of the technique registry. Written into the case file.")
def provenance(ctx: Context) -> Finding:
    from .. import __version__
    sp = spec(152)
    sources = sorted({s for f in _all(ctx).values() for s in f.sources})
    cfg = ctx.cfg
    try:
        cfg_hash = hashlib.sha256(json.dumps(cfg.to_dict(), sort_keys=True,
                                             default=str).encode()).hexdigest()[:16]
    except Exception:
        cfg_hash = None
    registry = "|".join(f"{t.id}:{t.name}:{t.phase}:{int(t.enabled)}"
                        for t in sorted(REGISTRY.values(), key=lambda t: t.id))
    engine = {}
    if ctx.engine is not None and hasattr(ctx.engine, "engine_status"):
        try:
            engine = ctx.engine.engine_status()
        except Exception:
            engine = {}
    models = getattr(cfg, "models", None)
    record = {
        "mapify_version": __version__,
        "analysed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input_sha256": hashlib.sha256(ctx.raw_bytes).hexdigest() if ctx.raw_bytes else None,
        "filename": ctx.filename,
        "analysed_size": list(ctx.image.size) if ctx.image is not None else None,
        "models": {"image_encoder": getattr(models, "clip_model_id", None),
                   "device": getattr(models, "device", None),
                   "ocr": engine.get("ocr_engine"),
                   "geoclip_loaded": engine.get("geoclip_loaded")},
        "online": bool(ctx.online),
        "config_sha256": cfg_hash,
        "techniques_registry_sha256": hashlib.sha256(registry.encode()).hexdigest()[:16],
        "techniques_registered": len(REGISTRY),
        "sources": sources,
        "versions": _versions(),
    }
    return Finding(technique=152, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=1.0,
                   summary=(f"Recorded for reproducibility: the input's SHA-256, Mapify "
                            f"{__version__}, the models, {len(sources)} data sources and "
                            f"their licences, library versions, and hashes of the "
                            f"settings and of the {len(REGISTRY)}-technique registry. "
                            f"The same file and the same record give the same answer."),
                   detail=record, sources=[SRC_ALL])


# --------------------------------------------------------------- 153

@technique(id=153, key="orchestration", name="Integrated multi-stage geolocation/chronolocation orchestration",
           phase=PHASE,
           fusion_note="The run as a whole: which techniques could run on this "
                       "image and why the rest could not, phase by phase, and "
                       "what the answer rests on.")
def orchestration(ctx: Context) -> Finding:
    sp = spec(153)
    fs = _all(ctx)
    per = defaultdict(lambda: {"total": 0, "ran": 0, "found": 0, "skipped": 0,
                               "failed": 0, "ms": 0.0})
    reasons = Counter()
    for f in fs.values():
        p = per[f.phase]
        p["total"] += 1
        p["ms"] += f.elapsed_ms
        if f.skipped_because == "the technique raised":
            p["failed"] += 1
        elif f.ran:
            p["ran"] += 1
            p["found"] += int(f.found)
        else:
            p["skipped"] += 1
            reasons[f.skipped_because] += 1
    total = sum(p["total"] for p in per.values()) + 1        # and this one,
    ran = sum(p["ran"] for p in per.values()) + 1            # which runs
    found = sum(p["found"] for p in per.values()) + 1        # and reports
    failed = sum(p["failed"] for p in per.values())
    band, est, pred = _band(ctx)
    answer = (f"{est['latitude']:.4f}, {est['longitude']:.4f} ({band} confidence)"
              if pred.get("determinate") and est.get("latitude") is not None
              else "no location determined")
    f148 = fs.get(148)
    if f148 is not None and f148.found:
        answer += "; " + f148.summary.split(":", 1)[-1].split("—")[0].strip()
    def short(why: str) -> str:
        # The first clause is the reason; the rest is explanation the
        # technique's own row already carries.
        clause = why.split(";")[0].split(" — ")[0].split(" (")[0].strip()
        return clause if len(clause) <= 70 else clause[:67].rstrip() + "…"

    top_reasons = "; ".join(f"{short(why)} ({n})" for why, n in reasons.most_common(3))
    return Finding(technique=153, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=1.0,
                   summary=(f"{ran} of {total} techniques could run on this image and "
                            f"{found} found something"
                            + (f"; {failed} failed and are recorded as failures"
                               if failed else "")
                            + f". Answer: {answer}."
                            + (f" The rest were skipped automatically because "
                               f"{top_reasons}." if top_reasons else "")),
                   detail={"phases": {str(k): {**v, "title": TITLES.get(k),
                                               "ms": round(v["ms"], 1)}
                                      for k, v in sorted(per.items())},
                           "skip_reasons": dict(reasons), "answer": answer,
                           "techniques_total": total, "techniques_ran": ran,
                           "techniques_found": found, "techniques_failed": failed},
                   sources=[SRC_ALL])
