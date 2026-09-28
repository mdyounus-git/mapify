"""Techniques 16-32: roads, infrastructure, vehicles, landform and sky.

Mostly readings of the scene by the zero-shot encoder, and the Phase 22
result governs all of them. Six of these groups were measured as fusion
inputs and earned their place (road markings, vehicles, plates,
vegetation, and the two that were always descriptive); street furniture
was measured and made the median worse. Every new group here -- pavement,
traffic lights, transit, ground, weather, sky -- reports and does not
steer, because each is another opinion from the same encoder about the
same embedding, and that is the kind of evidence that was shown to
amplify rather than correct.

Three techniques are different in kind, because they check the scene
against something that is not the encoder:

  25  landmark identification -- the locator's own concentration, and
      named landmarks near the leading candidate from local map data.
  26  terrain -- "is the ground here mountainous?" asked of the landform
      reading and, where local map data exists, of what surrounds the
      candidate.
  27  coastline -- the photograph shows a shore; is the candidate within
      reach of one? Answered from Natural Earth, offline.

A contradiction from 26 or 27 is reported as a contradiction. It does not
move the answer: that would need a benchmark run, and has not had one.
"""

from __future__ import annotations

from ..geo.coast import coast_available, distance_to_coast_km
from .base import Context, Finding, technique
from .common import (SRC_CLIP, SRC_NE, SRC_OSM_LOCAL, describe_reading,
                     leader, reading, spec)

PHASE = 2


def _group(tid: int, ctx: Context, key: str, steers: bool) -> Finding:
    return describe_reading(spec(tid), reading(ctx, key), steers=steers)


# ------------------------------------------------------------- 16-24

@technique(id=16, key="road_markings", name="Road design & markings",
           phase=PHASE, needs=("evidence",), feeds_fusion=True,
           fusion_note="In the fusion since Phase 9; its one constraining "
                       "option survived the Phase 13 benchmark.")
def road_markings(ctx: Context) -> Finding:
    return _group(16, ctx, "road_markings", True)


@technique(id=17, key="pavement", name="Sidewalk / pavement", phase=PHASE,
           needs=("evidence",),
           fusion_note="New and unmeasured; another reading of the same "
                       "embedding. Reported only.")
def pavement(ctx: Context) -> Finding:
    return _group(17, ctx, "pavement", False)


@technique(id=18, key="utility_poles", name="Utility poles & electrical",
           phase=PHASE, needs=("evidence",),
           fusion_note="Always descriptive: no option constrains, because "
                       "pole styles cross borders too freely.")
def utility_poles(ctx: Context) -> Finding:
    return _group(18, ctx, "utility", False)


@technique(id=19, key="power_lines", name="Power-line configuration",
           phase=PHASE, needs=("evidence",),
           fusion_note="Read from the utility group's runners-up; "
                       "descriptive for the same reason as 18.")
def power_lines(ctx: Context) -> Finding:
    sp = spec(19)
    rd = reading(ctx, "utility")
    if rd is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    ranked = dict(rd.ranked)
    overhead = sum(p for k, p in ranked.items() if k != "buried" and k != "none")
    return Finding(
        technique=19, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=bool(ranked),
        summary=(f"Overhead cabling reads at {overhead:.0%} across the "
                 f"utility options (best: {rd.option_label})"
                 " — reported only, moves no coordinate"),
        detail={"ranked": [(k, round(v, 4)) for k, v in rd.ranked]},
        confidence=min(1.0, overhead), sources=[SRC_CLIP])


@technique(id=20, key="street_furniture", name="Street furniture",
           phase=PHASE, needs=("evidence",),
           fusion_note="Measured in Phase 22 as a fusion input: median "
                       "error 342 -> 368 km. Permanently reported only.")
def street_furniture(ctx: Context) -> Finding:
    return _group(20, ctx, "street_furniture", False)


@technique(id=21, key="traffic_lights", name="Traffic-light design",
           phase=PHASE, needs=("evidence",),
           fusion_note="New and unmeasured. Reported only.")
def traffic_lights(ctx: Context) -> Finding:
    return _group(21, ctx, "traffic_lights", False)


@technique(id=22, key="vehicles", name="Vehicle models", phase=PHASE,
           needs=("evidence",), feeds_fusion=True,
           fusion_note="In the fusion since Phase 9 with country and "
                       "subregion constraints.")
def vehicles(ctx: Context) -> Finding:
    return _group(22, ctx, "vehicles", True)


@technique(id=23, key="plate_format", name="Plate-format analysis",
           phase=PHASE, needs=("evidence",), feeds_fusion=True,
           fusion_note="Plate class against a 239-country table; in the "
                       "fusion since Phase 9. Reading the characters is "
                       "technique 11.")
def plate_format(ctx: Context) -> Finding:
    return _group(23, ctx, "plate_style", True)


@technique(id=24, key="transit", name="Public transport", phase=PHASE,
           needs=("evidence",),
           fusion_note="New and unmeasured. Reported only.")
def transit(ctx: Context) -> Finding:
    return _group(24, ctx, "transit", False)


# ------------------------------------------------------------------ 25

@technique(id=25, key="landmark", name="Landmark identification",
           phase=PHASE, needs=("candidates",),
           fusion_note="The locator already sees landmarks -- 82% of the "
                       "benchmark's land within 25 km. This reports how "
                       "concentrated it was, and what named landmarks lie "
                       "near its answer, without adding weight twice.")
def landmark(ctx: Context) -> Finding:
    sp = spec(25)
    top = leader(ctx)
    mass = sum(getattr(c, "probability", 0.0) for c in ctx.candidates[:1])
    detail = {"leading_probability": round(mass, 4)}
    parts = []
    if mass >= 0.5:
        parts.append(f"the locator put {mass:.0%} on one place, the way it "
                     f"does for recognisable landmarks")
    named = []
    try:
        from ..geodata.nearby import nearby
        from ..geodata.service import get_service
        s = nearby(get_service(), top.latitude, top.longitude,
                   800, fetch=False, limit=8)
        named = [{"name": f.name, "kind": f.kind, "distance_m": f.distance_m,
                  "direction": f.direction} for f in s.features]
    except Exception:
        named = []
    if named:
        detail["nearby_landmarks"] = named
        parts.append("named landmarks near it: " + ", ".join(
            f"{n['name']} ({n['distance_m']} m {n['direction']})"
            for n in named[:3]))
    if not parts:
        return Finding.nothing_found(
            sp, "No sign of a recognisable landmark: the locator's answer is "
                "spread out, and no named landmark is stored near it.")
    return Finding(
        technique=25, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=True, summary="; ".join(parts).capitalize(), detail=detail,
        confidence=min(1.0, mass),
        sources=["GeoCLIP gallery (MIT)"] + ([SRC_OSM_LOCAL] if named else []))


# ------------------------------------------------------------- 26, 27

@technique(id=26, key="skyline", name="Mountain / skyline consistency",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="A consistency check, not a locator: true horizon "
                       "matching needs a DEM renderer this project does "
                       "not have. Contradictions are reported, unmeasured "
                       "as a fusion input.")
def skyline(ctx: Context) -> Finding:
    sp = spec(26)
    rd = reading(ctx, "terrain")
    if rd is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    top = leader(ctx)
    ranked = dict(rd.ranked)
    mountains = ranked.get("mountains", 0.0) + 0.5 * ranked.get("valley", 0.0)
    detail = {"mountains_reading": round(mountains, 4),
              "horizon_matching": "not attempted: needs a DEM horizon "
                                  "renderer, which this project does not have"}
    place = getattr(top, "location", None) or {}
    if mountains < 0.35:
        return Finding(
            technique=26, key=sp.key, name=sp.name, phase=PHASE, ran=True,
            found=False,
            summary=f"No mountainous skyline in view ({rd.option_label}); "
                    f"nothing to check against the terrain",
            detail=detail, confidence=0.0, sources=[SRC_CLIP])
    return Finding(
        technique=26, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=True,
        summary=(f"The skyline reads as mountainous ({mountains:.0%}). A "
                 f"horizon match against elevation data is not available, "
                 f"so this is recorded for the reader to check against the "
                 f"leading candidate"
                 + (f" ({place.get('country', {}).get('name')})"
                    if isinstance(place, dict) and place.get("country") else "")),
        detail=detail, confidence=mountains, sources=[SRC_CLIP])


@technique(id=27, key="coastline", name="Coastline / river / lake",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="Checks a shoreline in the photograph against the "
                       "leading candidate's distance to the sea. Reported "
                       "as a contradiction when they disagree; unmeasured "
                       "as a fusion input.")
def coastline(ctx: Context) -> Finding:
    sp = spec(27)
    rd = reading(ctx, "terrain")
    if rd is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    if not coast_available():
        return Finding.not_applicable(sp, "the Natural Earth data is not installed")
    top = leader(ctx)
    coast = dict(rd.ranked).get("coast", 0.0)
    dist = distance_to_coast_km(top.latitude, top.longitude, max_km=2000)
    detail = {"coast_reading": round(coast, 4),
              "leader_distance_to_coast_km": (round(dist, 1)
                                              if dist is not None else None)}
    where = (f"{dist:.0f} km from the sea" if dist is not None
             else "more than 2,000 km from the sea")
    if coast < 0.40:
        return Finding(
            technique=27, key=sp.key, name=sp.name, phase=PHASE, ran=True,
            found=False,
            summary=f"No shoreline in view; the leading candidate is {where}",
            detail=detail, confidence=0.0, sources=[SRC_CLIP, SRC_NE])
    contradiction = dist is None or dist > 60
    detail["contradiction"] = contradiction
    summary = (f"The photograph shows a shoreline ({coast:.0%}) and the "
               f"leading candidate is {where}")
    summary += (" — these disagree: either this is a lake or river shore, "
                "or the candidate is wrong" if contradiction
                else " — consistent")
    return Finding(
        technique=27, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=True, summary=summary, detail=detail,
        confidence=coast, sources=[SRC_CLIP, SRC_NE])


# ------------------------------------------------------------- 28-32

@technique(id=28, key="vegetation", name="Vegetation & tree species",
           phase=PHASE, needs=("evidence",), feeds_fusion=True,
           fusion_note="The biome group, with latitude-band constraints; in "
                       "the fusion since Phase 9.")
def vegetation(ctx: Context) -> Finding:
    return _group(28, ctx, "biome", True)


@technique(id=29, key="terrain", name="Terrain / geology", phase=PHASE,
           needs=("evidence",),
           fusion_note="Always descriptive: landforms repeat across every "
                       "continent.")
def terrain(ctx: Context) -> Finding:
    return _group(29, ctx, "terrain", False)


@technique(id=30, key="soil", name="Soil & ground", phase=PHASE,
           needs=("evidence",),
           fusion_note="New and unmeasured. Reported only.")
def soil(ctx: Context) -> Finding:
    return _group(30, ctx, "ground", False)


@technique(id=31, key="weather", name="Weather / atmospheric", phase=PHASE,
           needs=("evidence",),
           fusion_note="Weather says little about where; it is compared "
                       "against weather records by the chronolocation "
                       "techniques when a date is known.")
def weather(ctx: Context) -> Finding:
    return _group(31, ctx, "weather", False)


@technique(id=32, key="sky", name="Cloud & visibility", phase=PHASE,
           needs=("evidence",),
           fusion_note="Reported only; an input to the chronolocation "
                       "checks.")
def sky(ctx: Context) -> Finding:
    return _group(32, ctx, "sky", False)
