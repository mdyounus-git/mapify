"""Techniques 57-72: time, and the correlation of everything so far.

Two halves.

57-63 bring time into the evidence: dated imagery that exists for the
place, whether the scene is under construction, vegetation against the
calendar, years written in the text (a photograph cannot predate a date
printed in it), the weather record for the hour, and the sun checked
against every candidate rather than only the leader -- the check that
can actually eliminate a place, because at one instant the sun is up in
some candidates' skies and down in others'.

64-72 are the correlation stage the specification describes. Most of it
the fusion engine already does, measured on the benchmark; these
techniques make its reasoning legible: which independent clues agree,
which candidates the evidence ruled against, which countries survive
every constraint, the final coordinate and its uncertainty, a time
window and a date range with how each was bounded, and the chain of
evidence that produced the answer.

As everywhere, a contradiction is reported and does not move the
answer; making it do so needs a benchmark run.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..chrono.capture_time import resolve
from ..chrono.solar import sun_position
from ..data.countries import get_table
from ..evidence import prompts as P
from ..paths import cache_dir
from .base import Context, Finding, technique
from .common import ABSENT, SRC_CLIP, SRC_EXIF, SRC_OCR, leader, reading, spec

PHASE = 5
SRC_NOAA = "NOAA solar position algorithm (after Meeus)"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec"]


def _chrono_time(ctx: Context):
    """The capture time as phase 3 resolved it, or resolve it now."""
    c = ctx.memo.get("chrono")
    if c is not None:
        return c["time"]
    top = leader(ctx)
    return resolve(ctx.raw_bytes, ctx.exif, getattr(top, "latitude", None),
                   getattr(top, "longitude", None))


def _weather(ctx: Context):
    if "weather" in ctx.memo:
        return ctx.memo["weather"]
    svc = None
    net = getattr(ctx.cfg, "network", None) if ctx.cfg is not None else None
    enabled = getattr(net, "weather_history_enabled", True) if net else True
    if enabled:
        from ..geodata.weather import WeatherHistory
        svc = WeatherHistory(cache_dir(), online=ctx.online)
    ctx.memo["weather"] = svc
    return svc


# ---------------------------------------------------------------- 57

@technique(id=57, key="historical_imagery", name="Historical imagery comparison",
           phase=PHASE, needs=("candidates",),
           fusion_note="Names the dated imagery that exists for the place, to "
                       "compare in the 3D view. Automated change detection "
                       "would need building detection in 30 m imagery, which "
                       "cannot see buildings.")
def historical_imagery(ctx: Context) -> Finding:
    sp = spec(57)
    top = leader(ctx)
    from ..geodata.tiles import sources_for
    srcs = sources_for(top.latitude, top.longitude)
    listed = []
    for s in srcs:
        if s["key"] == "weld":
            listed.append("Landsat WELD annual composite (about 2000)")
        elif s["key"] == "hls":
            listed.append("Landsat 8/9 HLS, dated passes since 2013")
        elif s["key"] == "usgs":
            listed.append("USGS NAIP orthoimagery (current cache)")
    return Finding(
        technique=57, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=bool(listed),
        summary=("Dated imagery to compare against at the leading candidate: "
                 + "; ".join(listed) + ". All 30 m except NAIP, so it shows "
                 "land cover and settlement growth, not individual buildings. "
                 "Open the 3D view to look."),
        detail={"sources": srcs}, confidence=0.3,
        sources=["NASA GIBS (public domain)", "USGS The National Map"])


# ---------------------------------------------------------------- 58

@technique(id=58, key="construction_state", name="Construction-state comparison",
           phase=PHASE, needs=("evidence",),
           fusion_note="Reads the scene's construction state. Comparing it "
                       "against dated building records needs data this "
                       "project does not hold (Open Buildings' temporal "
                       "layer is not integrated).")
def construction_state(ctx: Context) -> Finding:
    sp = spec(58)
    rd = reading(ctx, "construction")
    if rd is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    decisive = rd.confidence >= 0.35 and rd.margin >= 0.08
    summary = (f"The scene reads as {rd.option_label.lower()} "
               f"({rd.confidence:.0%})" if decisive else
               "No clear construction state")
    summary += (". No dated building record is held locally, so when it was "
                "built cannot be compared.")
    return Finding(technique=58, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=decisive, summary=summary,
                   detail={"reading": rd.option_key,
                           "ranked": [(k, round(v, 4)) for k, v in rd.ranked]},
                   confidence=rd.confidence if decisive else 0.0,
                   sources=[SRC_CLIP])


# ---------------------------------------------------------------- 59

# Season readings and the months they suit, in the northern hemisphere.
_SEASON_MONTHS = {
    "bare": {11, 12, 1, 2, 3},
    "snow": {11, 12, 1, 2, 3, 4},
    "autumn": {9, 10, 11},
    "blossom": {3, 4, 5},
    "lush": {5, 6, 7, 8, 9},
}


def _green_fraction(ctx: Context) -> float | None:
    """Share of pixels that are vegetation-green: a pixel measurement,
    independent of the encoder's reading."""
    if ctx.image is None:
        return None
    import numpy as np
    from PIL import Image
    im = ctx.image.convert("RGB")
    im.thumbnail((320, 320), Image.Resampling.BILINEAR)
    a = np.asarray(im, dtype=np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    green = (g > r * 1.08) & (g > b * 1.08) & (g > 40)
    return float(green.mean())


@technique(id=59, key="season_vegetation", name="Seasonal vegetation comparison",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="Vegetation against the calendar, allowing for the "
                       "hemisphere, with a pixel greenness measurement beside "
                       "the reading. The tropics are wet and dry, not "
                       "temperate, and infer no month.")
def season_vegetation(ctx: Context) -> Finding:
    sp = spec(59)
    rd = reading(ctx, "season")
    top = leader(ctx)
    if rd is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    lat = top.latitude
    green = _green_fraction(ctx)
    detail = {"reading": rd.option_key, "confidence": round(rd.confidence, 4),
              "latitude": round(lat, 3),
              "green_pixel_fraction": round(green, 4) if green is not None else None}
    greenness = f"; {green:.0%} of the pixels are vegetation-green" if green is not None else ""
    if abs(lat) < 23.5:
        return Finding(
            technique=59, key=sp.key, name=sp.name, phase=PHASE, ran=True,
            found=rd.confidence >= 0.35 and rd.option_key not in ABSENT,
            detail=detail, confidence=0.0,
            summary=(f"Vegetation reads as {rd.option_label.lower()} "
                     f"({rd.confidence:.0%}){greenness}. The leading candidate "
                     f"is in the tropics, where seasons are wet and dry rather "
                     f"than temperate, so no month is inferred."),
            sources=[SRC_CLIP])
    months = _SEASON_MONTHS.get(rd.option_key)
    if not months or rd.confidence < 0.35:
        return Finding(
            technique=59, key=sp.key, name=sp.name, phase=PHASE, ran=True,
            found=False, detail=detail, confidence=0.0,
            summary=f"No seasonal cue strong enough to use (best: "
                    f"{rd.option_label}, {rd.confidence:.0%}){greenness}.",
            sources=[SRC_CLIP])
    if lat < 0:
        months = {((m + 5) % 12) + 1 for m in months}
    detail["compatible_months"] = sorted(months)
    span = ", ".join(_MONTHS[m - 1] for m in sorted(months))
    summary = (f"Vegetation reads as {rd.option_label.lower()} "
               f"({rd.confidence:.0%}){greenness}, which in the "
               f"{'southern' if lat < 0 else 'northern'} hemisphere suits {span}")
    ct = _chrono_time(ctx)
    if ct.known:
        month = ct.utc.month
        agree = month in months
        detail["capture_month"] = month
        detail["contradiction"] = not agree
        summary += (f"; the file says {_MONTHS[month - 1]} — "
                    + ("consistent" if agree else "a contradiction"))
    return Finding(technique=59, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=True, summary=summary, detail=detail,
                   confidence=rd.confidence, sources=[SRC_CLIP, SRC_EXIF])


# ---------------------------------------------------------------- 60

_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
_EST = re.compile(r"(?i)\b(est\.?|estd\.?|since|founded|©|\(c\))\s*(19\d\d|20\d\d)")


@technique(id=60, key="dated_signage", name="Historical billboard/sign analysis",
           phase=PHASE, needs=("ocr",),
           fusion_note="Years printed in the scene bound the date from below: "
                       "a photograph cannot be older than a date it shows. "
                       "\"Est. 1998\" says nothing about the photograph and is "
                       "set apart.")
def dated_signage(ctx: Context) -> Finding:
    sp = spec(60)
    now_year = datetime.now(timezone.utc).year
    years, founding = set(), set()
    for line in ctx.ocr.lines:
        if line.confidence < 0.4:
            continue
        for m in _EST.finditer(line.text):
            founding.add(int(m.group(2)))
        for m in _YEAR.finditer(line.text):
            y = int(m.group(1))
            if y <= now_year:
                years.add(y)
    usable = sorted(years - founding)
    if not usable and not founding:
        return Finding.nothing_found(sp, "No year appears in the text.")
    detail = {"years": usable, "founding_years": sorted(founding)}
    if usable:
        lo = max(usable)
        detail["not_before"] = lo
        summary = (f"The text shows the year {lo}" + (
            f" (and {', '.join(map(str, usable[:-1]))})" if len(usable) > 1 else "")
            + f": the photograph was taken in {lo} or later")
    else:
        summary = ("The only years in the text are founding dates ("
                   + ", ".join(map(str, sorted(founding)))
                   + "), which say when a business began, not when this was "
                     "taken")
    ct = _chrono_time(ctx)
    if usable and ct.known:
        detail["contradiction"] = ct.utc.year < max(usable)
        summary += (f"; the file says {ct.utc.year} — "
                    + ("a contradiction" if detail["contradiction"] else "consistent"))
    return Finding(technique=60, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=bool(usable), summary=summary + ".", detail=detail,
                   confidence=0.7 if usable else 0.2, sources=[SRC_OCR])


# ---------------------------------------------------------------- 61

@technique(id=61, key="weather_history", name="Weather-history correlation",
           phase=PHASE, needs=("time", "candidates", "evidence"),
           fusion_note="ERA5 reanalysis for the capture hour at the leading "
                       "candidate. A ~25 km grid average, so a disagreement "
                       "is a question, not a refutation.")
def weather_history(ctx: Context) -> Finding:
    sp = spec(61)
    ct = _chrono_time(ctx)
    if not ct.known:
        return Finding.not_applicable(sp, "; ".join(ct.notes) or "no capture time")
    svc = _weather(ctx)
    if svc is None:
        return Finding.not_applicable(sp, "weather history is switched off")
    top = leader(ctx)
    if (datetime.now(timezone.utc) - ct.utc) < timedelta(days=6):
        return Finding.not_applicable(sp, "the reanalysis runs about five days "
                                          "behind; this capture is too recent")
    h = svc.hour(top.latitude, top.longitude, ct.utc)
    if h is None:
        return Finding.not_applicable(sp, "no weather record could be retrieved "
                                          "(offline, or before the record)")
    from ..geodata.weather import ATTRIBUTION, describe_code
    rd = reading(ctx, "weather")
    code = h.get("weather_code")
    cloud = h.get("cloud_cover")
    rain = h.get("precipitation") or 0.0
    snow = h.get("snowfall") or 0.0
    rec = (f"{describe_code(code)}, {cloud}% cloud, {rain:.1f} mm precipitation"
           + (f", {snow:.1f} cm snow" if snow else ""))
    detail = {"record": h, "reading": rd.option_key if rd else None,
              "capture_time": ct.as_dict()}
    summary = (f"The record for {ct.utc:%Y-%m-%d %H}:00 UTC at the leading "
               f"candidate: {rec}")
    if rd is not None and rd.confidence >= 0.35:
        k = rd.option_key
        agree = {"rain": rain > 0.1 or (code or 0) in range(51, 83) or (code or 0) >= 95,
                 "snow": snow > 0 or (code or 0) in (71, 73, 75, 77, 85, 86),
                 "clear": (cloud or 0) < 60 and rain < 0.2,
                 "overcast": (cloud or 0) >= 50,
                 "fog": (code or 0) in (45, 48)}.get(k)
        if agree is not None:
            detail["contradiction"] = not agree
            summary += (f"; the photograph reads as {rd.option_label.lower()} — "
                        + ("consistent" if agree else
                           "they disagree (a local shower or a wrong time, place "
                           "or date)"))
    return Finding(technique=61, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.5, sources=[ATTRIBUTION, SRC_CLIP])


# ---------------------------------------------------------------- 62

_LIGHT_RANGE = {"night": (-90.0, -4.0), "twilight": (-12.0, 2.0),
                "golden": (-2.0, 18.0), "day": (8.0, 90.0),
                "diffuse": (0.0, 90.0)}


@technique(id=62, key="sun_vs_candidates", name="Sun-position verification against candidate locations",
           phase=PHASE, needs=("time", "candidates", "evidence"),
           fusion_note="The one solar check that can eliminate places: at one "
                       "UTC instant the sun is up over some candidates and "
                       "down over others. Reported; unmeasured as a fusion "
                       "input.")
def sun_vs_candidates(ctx: Context) -> Finding:
    sp = spec(62)
    rd = reading(ctx, "lighting")
    if rd is None or rd.option_key not in _LIGHT_RANGE or rd.confidence < 0.35:
        return Finding.nothing_found(
            sp, "The light is indoor or ambiguous, so it cannot be checked "
                "against the sun.")
    lo, hi = _LIGHT_RANGE[rd.option_key]
    rows = []
    for i, cand in enumerate(ctx.candidates[:8]):
        ct = resolve(ctx.raw_bytes, ctx.exif, cand.latitude, cand.longitude)
        if not ct.known:
            continue
        el = sun_position(cand.latitude, cand.longitude, ct.utc).elevation
        rows.append({"rank": i + 1, "lat": round(cand.latitude, 3),
                     "lon": round(cand.longitude, 3), "sun_elevation": round(el, 1),
                     "consistent": lo <= el <= hi, "time_basis": ct.basis})
    if not rows:
        return Finding.not_applicable(sp, "no capture time")
    ok = [r for r in rows if r["consistent"]]
    bad = [r for r in rows if not r["consistent"]]
    summary = (f"The light reads as {rd.option_label.lower()}: consistent with "
               f"the sun at {len(ok)} of {len(rows)} candidates")
    if bad:
        summary += ("; the sun was wrong for candidate(s) "
                    + ", ".join(f"#{r['rank']} ({r['sun_elevation']:.0f}°)"
                                for r in bad[:5]))
    if rows[0]["time_basis"] == "zone":
        summary += (". The capture time has no zone, so each candidate is "
                    "checked in its own local time")
    return Finding(technique=62, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".",
                   detail={"lighting": rd.option_key, "candidates": rows},
                   confidence=rd.confidence, sources=[SRC_NOAA, SRC_CLIP, SRC_EXIF])


# ---------------------------------------------------------------- 63

@technique(id=63, key="shadow_verification", name="Shadow-geometry verification",
           phase=PHASE, needs=("candidates",),
           fusion_note="Collects the shadow predictions (34, 35) and any "
                       "measured sun (36) into one verdict for the reader.")
def shadow_verification(ctx: Context) -> Finding:
    sp = spec(63)
    parts, contradiction, any_ran = [], False, False
    for tid in (34, 35, 36):
        f = ctx.finding(tid)
        if f is None or not f.ran:
            continue
        any_ran = True
        if f.detail.get("contradiction"):
            contradiction = True
        parts.append(f"{tid}: {f.summary.split('.')[0]}")
    if not any_ran:
        return Finding.not_applicable(sp, "no shadow predictions were possible "
                                          "(no capture time)")
    return Finding(technique=63, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True,
                   summary=("Shadow geometry: " + ("a contradiction in the "
                            "measured sun" if contradiction else
                            "predictions made; no measured disagreement")
                            + ". " + " | ".join(parts)),
                   detail={"contradiction": contradiction},
                   confidence=0.4, sources=[SRC_NOAA])


# ------------------------------------------------------------ 64-67

def _adjustments(ctx: Context) -> list:
    top = leader(ctx)
    return list(getattr(top, "adjustments", []) or [])


@technique(id=64, key="clue_correlation", name="Multiple-independent-clue correlation",
           phase=PHASE, needs=("candidates",),
           fusion_note="The fusion engine does this; this lists which kinds of "
                       "evidence agreed with the leading candidate.")
def clue_correlation(ctx: Context) -> Finding:
    sp = spec(64)
    adj = _adjustments(ctx)
    by_source: dict[str, float] = {}
    for a in adj:
        by_source[a.source] = by_source.get(a.source, 0.0) + a.delta
    top = leader(ctx)
    prov = getattr(top, "provenance", "model")
    agreeing = sorted(k for k, v in by_source.items() if v > 0)
    against = sorted(k for k, v in by_source.items() if v < 0)
    summary = (f"The leading candidate came from {prov}; "
               + (f"supported by {', '.join(agreeing)}" if agreeing else
                  "no other kind of evidence supported it")
               + (f"; opposed by {', '.join(against)}" if against else ""))
    return Finding(technique=64, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=bool(agreeing), summary=summary + ".",
                   detail={"by_source": {k: round(v, 3) for k, v in by_source.items()},
                           "provenance": prov},
                   confidence=min(1.0, 0.3 * len(agreeing)),
                   sources=["the fusion engine's adjustments"])


@technique(id=65, key="elimination", name="Candidate-location elimination",
           phase=PHASE, needs=("candidates",),
           fusion_note="Which candidates the evidence argued against, from the "
                       "fusion and from the solar check (62).")
def elimination(ctx: Context) -> Finding:
    sp = spec(65)
    against = []
    for i, c in enumerate(ctx.candidates[:12]):
        total = sum(a.delta for a in (getattr(c, "adjustments", []) or []))
        if total < -0.5:
            against.append((i + 1, round(total, 2)))
    solar = ctx.finding(62)
    solar_bad = [r["rank"] for r in (solar.detail.get("candidates", [])
                                     if solar and solar.ran else [])
                 if not r["consistent"]]
    if not against and not solar_bad:
        return Finding.nothing_found(sp, "No candidate was argued against strongly "
                                         "enough to set aside.")
    parts = []
    if against:
        parts.append("the evidence weighed against " + ", ".join(
            f"#{r} ({d:+.2f})" for r, d in against[:6]))
    if solar_bad:
        parts.append("the sun was wrong for " + ", ".join(f"#{r}" for r in solar_bad[:6]))
    return Finding(technique=65, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary="; ".join(parts).capitalize() + ".",
                   detail={"fusion_against": against, "solar_against": solar_bad},
                   confidence=0.5, sources=["the fusion engine", SRC_NOAA])


def _countries_for(option, table) -> set[str] | None:
    allc = list(table.all())
    if option.countries:
        return set(option.countries)
    if option.drive_side:
        return {c.iso_a2 for c in allc if c.drive_side == option.drive_side}
    if option.script:
        return {c.iso_a2 for c in allc if option.script in c.scripts}
    if option.plate_class:
        return {c.iso_a2 for c in allc if c.plate_class == option.plate_class}
    if option.subregions or option.continents:
        return {c.iso_a2 for c in allc if c.subregion in option.subregions
                or c.continent in option.continents}
    return None


@technique(id=66, key="constraint_intersection", name="Geospatial constraint intersection / candidate-location convergence",
           phase=PHASE, needs=("evidence",),
           fusion_note="Intersects the country sets of every clue that "
                       "constrains geography and was called. The fusion "
                       "weighs these softly; the hard intersection is shown "
                       "for the reader.")
def constraint_intersection(ctx: Context) -> Finding:
    sp = spec(66)
    table = get_table()
    sets, used = [], []
    for rd in ctx.evidence.readings:
        if not rd.called:
            continue
        group = P.GROUPS_BY_KEY.get(rd.group_key)
        opt = next((o for o in group.options if o.key == rd.option_key), None) if group else None
        s = _countries_for(opt, table) if opt else None
        if s:
            sets.append(s)
            used.append(f"{group.title}: {rd.option_label}")
    script = ctx.finding(12)
    if script is not None and script.found and script.constraint is not None:
        sc = script.constraint.value
        s = {c.iso_a2 for c in table.all() if sc in c.scripts}
        if s:
            sets.append(s)
            used.append(f"script read from text: {sc}")
    if not sets:
        return Finding.nothing_found(sp, "No clue constraining geography was "
                                         "strong enough to intersect.")
    inter = set.intersection(*sets)
    names = sorted(table.get(i).name for i in inter if table.get(i))
    detail = {"clues": used, "countries": sorted(inter)}
    if not inter:
        summary = ("The constraining clues contradict each other: no country "
                   "satisfies all of " + "; ".join(used))
        detail["contradiction"] = True
    else:
        summary = (f"{len(inter)} countr{'y' if len(inter) == 1 else 'ies'} "
                   f"satisfy every constraining clue ({'; '.join(used)})"
                   + (f": {', '.join(names[:12])}" if len(names) <= 12 else ""))
        top = leader(ctx)
        iso = getattr(top, "iso_a2", None) if top else None
        if iso:
            detail["leader_inside"] = iso in inter
            summary += ("; the leading candidate is inside" if iso in inter
                        else "; the leading candidate is not among them")
    return Finding(technique=66, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.6, sources=[SRC_CLIP, "the country table"])


@technique(id=67, key="final_coordinate", name="Final latitude/longitude estimation",
           phase=PHASE,
           fusion_note="The fusion engine's answer, as reported, with what it "
                       "rests on.")
def final_coordinate(ctx: Context) -> Finding:
    sp = spec(67)
    pred = ctx.prediction or {}
    est = pred.get("estimated")
    if pred.get("locatable") is False:
        return Finding.not_applicable(sp, "the image is not a photograph of a place")
    if not (pred.get("determinate") and est):
        return Finding.nothing_found(
            sp, "No location was determined: " + (pred.get("confidence_reason")
                                                  or "the evidence was insufficient"))
    prec = pred.get("precision_km")
    summary = (f"{est['latitude']:.5f}, {est['longitude']:.5f} "
               f"({pred.get('confidence')} confidence); the candidates span "
               f"{pred.get('uncertainty_km', 0):.0f} km"
               + (f", the matched feature is about {prec * 2:.2f} km across"
                  if prec is not None else ""))
    return Finding(technique=67, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + f"; from {est.get('provenance')}.",
                   detail={"estimated": est, "confidence": pred.get("confidence")},
                   confidence={"high": 0.9, "medium": 0.6, "low": 0.3}.get(
                       pred.get("confidence"), 0.1),
                   sources=["the fusion engine"])


# ------------------------------------------------------------ 68, 69

_SOLAR_WINDOWS = {"day": (9, 15), "golden": None, "twilight": None,
                  "night": (19, 5), "diffuse": (8, 17)}


@technique(id=68, key="time_window", name="Time-window estimation",
           phase=PHASE,
           fusion_note="The capture time when the file gives one; otherwise a "
                       "wide window from the light, in local solar time.")
def time_window(ctx: Context) -> Finding:
    sp = spec(68)
    ct = _chrono_time(ctx)
    contradictions = [t for t in (38, 41, 42, 62)
                      if (f := ctx.finding(t)) is not None and f.ran
                      and f.detail.get("contradiction")]
    if ct.known:
        unc = ct.uncertainty_min or 1
        summary = (f"{ct.utc:%Y-%m-%d %H:%M} UTC ± {unc:.0f} min "
                   f"({'GPS clock' if ct.basis == 'gps' else 'recorded offset' if ct.basis == 'offset' else 'zone assumed from the candidate'})")
        if contradictions:
            summary += (f"; the light contradicts it (techniques "
                        f"{', '.join(map(str, contradictions))}), so the clock or "
                        f"the place is suspect")
        return Finding(technique=68, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=True, summary=summary + ".",
                       detail={"capture_time": ct.as_dict(),
                               "contradicted_by": contradictions},
                       confidence=0.3 if contradictions else (0.5 if ct.assumed else 0.9),
                       sources=[SRC_EXIF])
    rd = reading(ctx, "lighting")
    if rd is None or rd.confidence < 0.4:
        return Finding.nothing_found(sp, "No capture time and no decisive light: "
                                         "the time of day is not determinable.")
    k = rd.option_key
    if k == "golden":
        text = "within about 90 minutes of sunrise or sunset"
    elif k == "twilight":
        text = "in the half hour or so around dawn or dusk"
    elif k in ("day", "diffuse", "night"):
        a, b = _SOLAR_WINDOWS[k]
        text = f"roughly {a:02d}:00 to {b:02d}:00 local solar time"
    else:
        return Finding.nothing_found(sp, "Indoor light gives no time of day.")
    return Finding(technique=68, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True,
                   summary=f"From the light alone ({rd.option_label.lower()}): "
                           f"{text}. A wide window, not a measurement.",
                   detail={"lighting": k}, confidence=0.25, sources=[SRC_CLIP])


@technique(id=69, key="date_estimate", name="Date estimation where evidence permits",
           phase=PHASE,
           fusion_note="Combines the file's date, years in the text (60), the "
                       "vegetation season (59) and solar season (39) into one "
                       "range, saying which bound came from what.")
def date_estimate(ctx: Context) -> Finding:
    sp = spec(69)
    ct = _chrono_time(ctx)
    bounds, notes = {}, []
    if ct.known:
        bounds["file"] = ct.utc.date().isoformat()
        notes.append(f"the file says {ct.utc:%d %b %Y}")
    f60 = ctx.finding(60)
    if f60 is not None and f60.found and f60.detail.get("not_before"):
        bounds["not_before_year"] = f60.detail["not_before"]
        notes.append(f"the text shows {f60.detail['not_before']}, so not earlier")
    f59 = ctx.finding(59)
    if f59 is not None and f59.found and f59.detail.get("compatible_months"):
        months = f59.detail["compatible_months"]
        bounds["months"] = months
        notes.append("the vegetation suits " + ", ".join(_MONTHS[m - 1] for m in months))
    f39 = ctx.finding(39)
    if f39 is not None and f39.found and f39.detail.get("declination") is not None:
        bounds["declination"] = f39.detail["declination"]
        notes.append("the measured sun names two dates a year")
    if not notes:
        return Finding.nothing_found(sp, "No evidence bears on the date.")
    contradictions = [t for t in (59, 60) if (f := ctx.finding(t)) is not None
                      and f.ran and f.detail.get("contradiction")]
    summary = "; ".join(notes).capitalize()
    if contradictions:
        summary += (f" — but techniques {', '.join(map(str, contradictions))} "
                    f"contradict the file's date")
    return Finding(technique=69, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".",
                   detail={"bounds": bounds, "contradicted_by": contradictions},
                   confidence=0.8 if ct.known and not contradictions else 0.3,
                   sources=[SRC_EXIF, SRC_OCR, SRC_CLIP])


# ------------------------------------------------------------ 70-72

@technique(id=70, key="confidence_scoring", name="Confidence scoring for each finding",
           phase=PHASE,
           fusion_note="Every finding carries its own confidence; the location's "
                       "band was fitted on a held-out split of the benchmark.")
def confidence_scoring(ctx: Context) -> Finding:
    sp = spec(70)
    fs = [f for f in ctx.memo.get("findings", {}).values() if f.ran and f.found]
    if not fs:
        return Finding.nothing_found(sp, "No finding to score yet.")
    hi = sum(1 for f in fs if f.confidence >= 0.7)
    mid = sum(1 for f in fs if 0.4 <= f.confidence < 0.7)
    lo = len(fs) - hi - mid
    band = (ctx.prediction or {}).get("confidence")
    return Finding(technique=70, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True,
                   summary=(f"{len(fs)} findings so far: {hi} strong, {mid} "
                            f"moderate, {lo} weak. The location itself is "
                            f"{band or 'not determined'} confidence, a band "
                            f"fitted on a held-out split of the benchmark."),
                   detail={"strong": hi, "moderate": mid, "weak": lo,
                           "location_band": band},
                   confidence=0.8, sources=["every technique's own confidence"])


@technique(id=71, key="alternatives", name="Alternative-location analysis",
           phase=PHASE, needs=("candidates",),
           fusion_note="The ranked alternatives, as the fusion scored them.")
def alternatives(ctx: Context) -> Finding:
    sp = spec(71)
    alts = ctx.candidates[1:6]
    if not alts:
        return Finding.nothing_found(sp, "There is only one candidate.")
    parts = []
    for i, c in enumerate(alts, start=2):
        loc = getattr(c, "location", None) or {}
        name = (loc.get("country") or {}).get("name") if isinstance(loc, dict) else None
        parts.append(f"#{i} {c.latitude:.2f}, {c.longitude:.2f}"
                     + (f" ({name})" if name else "")
                     + f" {getattr(c, 'probability', 0):.0%}")
    return Finding(technique=71, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary="Alternatives: " + "; ".join(parts) + ".",
                   detail={"count": len(alts)}, confidence=0.5,
                   sources=["the fusion engine"])


@technique(id=72, key="evidence_chain", name="Evidence-chain documentation",
           phase=PHASE,
           fusion_note="The chain from evidence to answer: which techniques "
                       "steered, which only reported, and what contradicted.")
def evidence_chain(ctx: Context) -> Finding:
    from .base import REGISTRY
    sp = spec(72)
    fs = ctx.memo.get("findings", {})
    steered = [t for t, f in fs.items() if f.ran and f.found
               and REGISTRY.get(t) and REGISTRY[t].feeds_fusion]
    contradictions = [t for t, f in fs.items() if f.ran
                      and f.detail.get("contradiction")]
    reported = [t for t, f in fs.items() if f.ran and f.found and t not in steered]
    skipped = [t for t, f in fs.items() if not f.ran]
    summary = (f"Steered the location: {', '.join(map(str, sorted(steered))) or 'none'}. "
               f"Reported without steering: {len(reported)}. "
               f"Contradictions: {', '.join(map(str, sorted(contradictions))) or 'none'}. "
               f"Could not run: {len(skipped)}, each with its reason in the case file.")
    return Finding(technique=72, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary,
                   detail={"steered": sorted(steered), "reported": sorted(reported),
                           "contradictions": sorted(contradictions),
                           "skipped": sorted(skipped)},
                   confidence=1.0, sources=["the technique registry"])
