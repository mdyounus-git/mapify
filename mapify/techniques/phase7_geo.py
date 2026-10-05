"""Techniques 91-110: astronomy, weather records and infrastructure, correlated.

The astronomical half builds on phase 3's ephemerides: the sun's whole
path for the day (91), whether anything measured in the frame matches
the ephemeris (92), whether the camera's own clock offset agrees with
the candidate's time zone (93) -- a quietly powerful check, because a
phone that last set its clock in UTC+1 was probably there -- and what
the sky narrows the date to (94).

The weather half compares the photograph with the ERA5 and CAMS records
for its hour (95-97), via Open-Meteo, under the same rules as 61: only a
coordinate and a date leave the machine, and a disagreement is a
question, not a refutation.

The infrastructure half checks what text and scene readings say against
the local map store and the regional tables: street names read off the
image against streets near each candidate (99), building density
(101), signing conventions (104), road markings (105), transit (109),
and telecom brands (110). Matching intersection geometry, facade
patterns, utility networks or "unique features" needs reference data no
free source provides, and 98, 100, 102, 103 say so.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from ..chrono.capture_time import resolve
from ..chrono.solar import sun_position, sun_times
from ..data.countries import get_table
from ..data.regional import BRANDS, SIGN_CONVENTION
from ..evidence import prompts as P
from ..paths import cache_dir
from .base import Context, Finding, Region, technique
from .common import (SRC_CLIP, SRC_EXIF, SRC_OCR, SRC_OSM_LOCAL, leader,
                     reading, spec, tiles, zero_shot)

PHASE = 7
SRC_NOAA = "NOAA solar position algorithm (after Meeus)"


def _time(ctx: Context):
    c = ctx.memo.get("chrono")
    if c is not None:
        return c["time"]
    top = leader(ctx)
    return resolve(ctx.raw_bytes, ctx.exif, getattr(top, "latitude", None),
                   getattr(top, "longitude", None))


def _weather(ctx: Context):
    from .phase5_temporal import _weather as w
    return w(ctx)


def _ocr_text(ctx: Context) -> str:
    return "\n".join(l.text for l in (getattr(ctx.ocr, "lines", None) or [])
                     if l.confidence >= 0.35)


def _na(sp, why: str) -> Finding:
    return Finding.not_applicable(sp, why)


# ------------------------------------------------------------ 91-94

@technique(id=91, key="solar_path", name="Solar-path simulation", phase=PHASE,
           needs=("time", "candidates"),
           fusion_note="The sun's path across the capture day at the leading "
                       "candidate, for the reader and the 3D view.")
def solar_path(ctx: Context) -> Finding:
    sp = spec(91)
    ct = _time(ctx)
    if not ct.known:
        return _na(sp, "no capture instant")
    top = leader(ctx)
    st = sun_times(top.latitude, top.longitude, ct.utc)
    base = datetime(ct.utc.year, ct.utc.month, ct.utc.day, tzinfo=timezone.utc)
    path = []
    for k in range(48):
        t = base + timedelta(minutes=30 * k)
        p = sun_position(top.latitude, top.longitude, t)
        if p.elevation > -1:
            path.append({"utc": t.strftime("%H:%M"), "elevation": round(p.elevation, 1),
                         "azimuth": round(p.azimuth, 1)})
    if st.polar:
        summary = f"Polar {st.polar} at the leading candidate on {ct.utc:%d %b}."
    else:
        length = (st.sunset - st.sunrise).total_seconds() / 3600
        noon = sun_position(top.latitude, top.longitude, st.solar_noon)
        summary = (f"On {ct.utc:%d %b %Y} the sun rose at {st.sunrise:%H:%M} and set "
                   f"at {st.sunset:%H:%M} UTC ({length:.1f} h of daylight), peaking "
                   f"at {noon.elevation:.0f}° at {st.solar_noon:%H:%M} UTC.")
    return Finding(technique=91, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary,
                   detail={"sun_times": st.as_dict(), "path": path},
                   confidence=0.9, sources=[SRC_NOAA])


@technique(id=92, key="ephemeris_match", name="Astronomical ephemeris matching",
           phase=PHASE, needs=("candidates",),
           fusion_note="Collects what was measured of the sky (36) against "
                       "the ephemeris.")
def ephemeris_match(ctx: Context) -> Finding:
    sp = spec(92)
    f36 = ctx.finding(36)
    if f36 is not None and f36.ran and "measured" in f36.detail and \
            f36.detail["measured"].get("elevation") is not None:
        bad = bool(f36.detail.get("contradiction"))
        return Finding(technique=92, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=True, confidence=0.6,
                       summary=("The sun measured in the frame "
                                + ("disagrees with" if bad else "matches")
                                + " the ephemeris for this place and time."),
                       detail={"contradiction": bad}, sources=[SRC_NOAA])
    return Finding.nothing_found(sp, "Nothing in the frame was measured against "
                                     "the ephemeris (no sun disc with a known "
                                     "lens); computed positions are in 33-41.")


@technique(id=93, key="utc_offset", name="Timezone / UTC-offset estimation",
           phase=PHASE, needs=("time", "candidates"),
           fusion_note="Compares the offset the camera's clock was running on "
                       "with the leading candidate's time zone at that "
                       "instant. A mismatch means the camera, or the candidate, "
                       "was somewhere else.")
def utc_offset(ctx: Context) -> Finding:
    from zoneinfo import ZoneInfo
    from ..geo import timezone as tz
    sp = spec(93)
    top = leader(ctx)
    ct = _time(ctx)
    local_text = getattr(ctx.exif, "captured_at", None)
    camera_offset = None
    basis = None
    if ct.basis == "offset" and ct.zone:
        m = re.match(r"([+-])(\d{2}):?(\d{2})", ct.zone)
        if m:
            camera_offset = (1 if m.group(1) == "+" else -1) * (
                int(m.group(2)) + int(m.group(3)) / 60)
            basis = "the file's recorded offset"
    elif ct.basis == "gps" and local_text:
        try:
            local = datetime.strptime(str(local_text)[:19], "%Y:%m:%d %H:%M:%S")
            diff = (local.replace(tzinfo=timezone.utc) - ct.utc).total_seconds() / 3600
            camera_offset = round(diff * 4) / 4        # zones are quarter-hours
            basis = "the gap between the camera clock and the GPS clock"
        except ValueError:
            pass
    info = tz.lookup(top.latitude, top.longitude)
    if not info.iana_name:
        return _na(sp, "no time zone for the leading candidate")
    ref = ct.utc or datetime.now(timezone.utc)
    # The offset in force at that instant, daylight saving included.
    zone_offset = ref.astimezone(ZoneInfo(info.iana_name)).utcoffset().total_seconds() / 3600
    detail = {"candidate_zone": info.iana_name, "candidate_offset": zone_offset,
              "camera_offset": camera_offset, "basis": basis}
    if camera_offset is None:
        return Finding(technique=93, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=False, detail=detail, confidence=0.0,
                       summary=(f"The leading candidate is on UTC{zone_offset:+g} "
                                f"({info.iana_name}); the file does not say which "
                                f"offset the camera's clock was on, so they cannot "
                                f"be compared."), sources=[SRC_EXIF])
    agree = abs(camera_offset - zone_offset) < 0.26
    detail["contradiction"] = not agree
    return Finding(technique=93, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, detail=detail, confidence=0.8,
                   summary=(f"The camera's clock ran on UTC{camera_offset:+g} (from "
                            f"{basis}); the leading candidate keeps UTC"
                            f"{zone_offset:+g} ({info.iana_name}) — "
                            + ("consistent" if agree else
                               "a mismatch: the camera was last set somewhere "
                               "else, or the candidate is wrong")),
                   sources=[SRC_EXIF, "timezone-boundary-builder (ODbL)"])


@technique(id=94, key="date_astro", name="Date-range narrowing from astronomical geometry",
           phase=PHASE,
           fusion_note="What the measured sky narrows the date to (39).")
def date_astro(ctx: Context) -> Finding:
    sp = spec(94)
    f39 = ctx.finding(39)
    if f39 is not None and f39.found:
        return Finding(technique=94, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=True, summary=f39.summary,
                       detail=f39.detail, confidence=f39.confidence,
                       sources=f39.sources)
    return Finding.nothing_found(sp, "Nothing measured in the sky narrows the date: "
                                     "that needs the sun measured at a known time "
                                     "of day.")


# ------------------------------------------------------------ 95-97

def _record(ctx: Context):
    if "era5" in ctx.memo:
        return ctx.memo["era5"]
    ct = _time(ctx)
    top = leader(ctx)
    svc = _weather(ctx)
    out = None
    if ct.known and svc is not None and top is not None and \
            (datetime.now(timezone.utc) - ct.utc) >= timedelta(days=6):
        out = svc.hour(top.latitude, top.longitude, ct.utc)
    ctx.memo["era5"] = out
    return out


@technique(id=95, key="weather_station", name="Weather-station correlation",
           phase=PHASE, needs=("time", "candidates", "evidence"),
           fusion_note="Temperature and humidity for the hour (ERA5, a ~25 km "
                       "grid -- the nearest thing to a station open to all) "
                       "against snow, fog or rain in the photograph.")
def weather_station(ctx: Context) -> Finding:
    from ..geodata.weather import ATTRIBUTION
    sp = spec(95)
    h = _record(ctx)
    if h is None:
        return _na(sp, "no weather record for the capture hour (no time, too "
                       "recent, offline, or switched off)")
    rd = reading(ctx, "weather")
    t, rh = h.get("temperature_2m"), h.get("relative_humidity_2m")
    detail = {"temperature_c": t, "relative_humidity": rh}
    summary = f"ERA5 for the hour: {t} °C, {rh}% relative humidity"
    if rd is not None and rd.confidence >= 0.35 and t is not None:
        if rd.option_key == "snow":
            ok = t <= 3
        elif rd.option_key == "fog":
            ok = (rh or 0) >= 85
        else:
            ok = None
        if ok is not None:
            detail["contradiction"] = not ok
            summary += (f"; the photograph shows {rd.option_label.lower()} — "
                        + ("plausible" if ok else "implausible at that temperature "
                           "or humidity"))
    return Finding(technique=95, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.5, sources=[ATTRIBUTION])


@technique(id=96, key="cloud_history", name="Historical cloud/weather-pattern correlation",
           phase=PHASE, needs=("time", "candidates", "evidence"),
           fusion_note="Cloud cover for the hour against the sky in the "
                       "photograph.")
def cloud_history(ctx: Context) -> Finding:
    from ..geodata.weather import ATTRIBUTION
    sp = spec(96)
    h = _record(ctx)
    if h is None:
        return _na(sp, "no weather record for the capture hour")
    sky = reading(ctx, "sky")
    cloud = h.get("cloud_cover")
    detail = {"cloud_cover": cloud, "sky": sky.option_key if sky else None}
    summary = f"ERA5 cloud cover for the hour: {cloud}%"
    if sky is not None and sky.confidence >= 0.35 and cloud is not None:
        expect = {"blue": cloud <= 40, "cumulus": 10 <= cloud <= 80,
                  "overcast": cloud >= 60, "haze": None, "night": None, "none": None}
        ok = expect.get(sky.option_key)
        if ok is not None:
            detail["contradiction"] = not ok
            summary += (f"; the sky reads as {sky.option_label.lower()} — "
                        + ("consistent" if ok else "they disagree"))
    return Finding(technique=96, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.45, sources=[ATTRIBUTION])


@technique(id=97, key="air_quality", name="Air-quality / haze correlation",
           phase=PHASE, needs=("time", "candidates", "evidence"),
           fusion_note="CAMS particulates for the hour (from 2013) against "
                       "haze in the photograph.")
def air_quality(ctx: Context) -> Finding:
    from ..geodata.weather import ATTRIBUTION
    sp = spec(97)
    ct = _time(ctx)
    svc = _weather(ctx)
    top = leader(ctx)
    if not ct.known or svc is None:
        return _na(sp, "no capture time, or weather history switched off")
    if ct.utc.year < 2013:
        return _na(sp, "air-quality records start in 2013")
    a = svc.air(top.latitude, top.longitude, ct.utc)
    if a is None:
        return _na(sp, "no air-quality record for the capture hour")
    pm = a.get("pm2_5")
    sky = reading(ctx, "sky")
    detail = {"record": a, "sky": sky.option_key if sky else None}
    summary = f"CAMS for the hour: PM2.5 {pm} µg/m³, aerosol depth {a.get('aerosol_optical_depth')}"
    if sky is not None and sky.confidence >= 0.35 and pm is not None:
        if sky.option_key == "haze":
            ok = pm >= 35
        elif sky.option_key == "blue":
            ok = pm < 75
        else:
            ok = None
        if ok is not None:
            detail["contradiction"] = not ok
            summary += (f"; the sky reads as {sky.option_label.lower()} — "
                        + ("consistent" if ok else "they disagree"))
    return Finding(technique=97, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.4, sources=[ATTRIBUTION])


# ----------------------------------------------------------- 98-103

@technique(id=98, key="unique_features", name="Unique environmental-feature matching",
           phase=PHASE,
           fusion_note="Needs a database of distinctive environmental features "
                       "to match against; none openly licensed exists.")
def unique_features(ctx: Context) -> Finding:
    return _na(spec(98), "no openly licensed database of unique environmental "
                         "features exists to match against")


def _street_names_near(lat: float, lon: float, radius: float = 1500) -> list[str]:
    try:
        from ..geodata.nearby import nearby
        from ..geodata.service import get_service
        return nearby(get_service(), lat, lon, radius, fetch=False).road_names
    except Exception:
        return []


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


@technique(id=99, key="street_network", name="Street-network geometry matching",
           phase=PHASE, needs=("ocr", "candidates"),
           fusion_note="Street names read off the image against the street "
                       "network stored near each candidate -- the checkable "
                       "part of network matching from a street-level photo.")
def street_network(ctx: Context) -> Finding:
    sp = spec(99)
    words = [_norm(l.text) for l in ctx.ocr.lines if l.confidence >= 0.4]
    words = [w for w in words if len(w) >= 5]
    if not words:
        return Finding.nothing_found(sp, "No legible text long enough to be a "
                                         "street name.")
    matches = []
    for i, c in enumerate(ctx.candidates[:5]):
        names = _street_names_near(c.latitude, c.longitude)
        for n in names:
            nn = _norm(n)
            core = nn.replace(" road", "").replace(" street", "").replace(" salai", "")
            if len(core) >= 5 and any(core in w or w in nn for w in words):
                matches.append({"rank": i + 1, "street": n})
    if not matches:
        return Finding.nothing_found(sp, "No street name in the text matches a street "
                                         "stored near the candidates (or none is "
                                         "stored there).")
    return Finding(technique=99, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True,
                   summary="Street names in the text match streets near "
                           + ", ".join(f"candidate #{m['rank']} ({m['street']})"
                                       for m in matches[:4]) + ".",
                   detail={"matches": matches}, confidence=0.7,
                   sources=[SRC_OCR, SRC_OSM_LOCAL])


@technique(id=100, key="intersection_geometry", name="Road-intersection geometry matching",
           phase=PHASE,
           fusion_note="Recovering an intersection's angles from one street-level "
                       "photograph is not reliable enough to match maps with.")
def intersection_geometry(ctx: Context) -> Finding:
    return _na(spec(100), "intersection angles cannot be recovered reliably from a "
                          "single street-level photograph")


@technique(id=101, key="footprint_density", name="Building-footprint matching",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="A street-level photo gives no footprints to match; what "
                       "can be checked is whether the scene's density agrees "
                       "with the buildings stored around the candidate.")
def footprint_density(ctx: Context) -> Finding:
    sp = spec(101)
    rd = reading(ctx, "setting")
    if rd is None:
        return _na(sp, "the zero-shot pass did not run")
    top = leader(ctx)
    try:
        from ..geodata.service import get_service
        from ..geodata.store import bbox_around
        held = get_service().store.query(bbox_around(top.latitude, top.longitude, 500),
                                         ("building",))["building"]
    except Exception:
        held = []
    if not held:
        return Finding.nothing_found(sp, "No buildings are stored around the "
                                         "leading candidate to compare with.")
    n = len(held)
    dense_scene = rd.option_key in ("cbd", "oldtown") and rd.confidence >= 0.4
    rural_scene = rd.option_key in ("village", "wilderness") and rd.confidence >= 0.4
    contradiction = (dense_scene and n < 30) or (rural_scene and n > 1500)
    return Finding(technique=101, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.4,
                   summary=(f"{n} buildings are stored within about 500 m of the "
                            f"leading candidate; the scene reads as "
                            f"{rd.option_label.lower()} — "
                            + ("these disagree" if contradiction else "consistent")),
                   detail={"stored_buildings_500m": n, "setting": rd.option_key,
                           "contradiction": contradiction,
                           "note": "true footprint matching is not possible from a "
                                   "street-level photograph"},
                   sources=[SRC_CLIP, SRC_OSM_LOCAL])


@technique(id=102, key="facade_matching", name="Facade/window-pattern matching",
           phase=PHASE,
           fusion_note="Needs a database of facades; none openly licensed. The "
                       "facade and openings readings are in 14 and 15.")
def facade_matching(ctx: Context) -> Finding:
    return _na(spec(102), "no openly licensed facade database exists; see "
                          "techniques 14 and 15 for what the facade reads as")


@technique(id=103, key="utility_database", name="Utility-infrastructure database matching",
           phase=PHASE,
           fusion_note="Power and telecom networks are not in the local store.")
def utility_database(ctx: Context) -> Finding:
    return _na(spec(103), "utility networks are not held locally; the pole and "
                          "cabling readings are techniques 18 and 19")


# ---------------------------------------------------------- 104-110

@technique(id=104, key="sign_standard", name="Traffic-sign regional-standard matching",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="The signing convention seen (10) against the leading "
                       "candidate's. Measured out of the fusion in Phase 22; "
                       "reported only.")
def sign_standard(ctx: Context) -> Finding:
    sp = spec(104)
    rd = reading(ctx, "sign_style")
    if rd is None or rd.option_key == "none" or rd.confidence < 0.4:
        return Finding.nothing_found(sp, "No signing convention read clearly.")
    iso = getattr(leader(ctx), "iso_a2", None)
    if not iso:
        return Finding.nothing_found(sp, "The leading candidate has no country.")
    theirs = next((k for k, v in SIGN_CONVENTION.items() if iso in v), "vienna")
    ok = theirs == rd.option_key
    return Finding(technique=104, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.4,
                   summary=(f"Signs read as {rd.option_label}; {iso} signs to the "
                            f"{theirs} convention — "
                            + ("consistent" if ok else "these disagree")
                            + " (reported only)"),
                   detail={"seen": rd.option_key, "candidate_convention": theirs,
                           "contradiction": not ok}, sources=[SRC_CLIP])


@technique(id=105, key="marking_standard", name="Road-marking standard matching",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="The road-marking reading against the countries its "
                       "constraining option names.")
def marking_standard(ctx: Context) -> Finding:
    from .phase5_temporal import _countries_for
    sp = spec(105)
    rd = reading(ctx, "road_markings")
    if rd is None or rd.confidence < 0.4:
        return Finding.nothing_found(sp, "No road-marking style read clearly.")
    group = P.GROUPS_BY_KEY["road_markings"]
    opt = next((o for o in group.options if o.key == rd.option_key), None)
    allowed = _countries_for(opt, get_table()) if opt and opt.constrains else None
    iso = getattr(leader(ctx), "iso_a2", None)
    if not allowed or not iso:
        return Finding(technique=105, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=True, confidence=0.3,
                       summary=f"Road markings read as {rd.option_label}; that "
                               f"style is not tied to particular countries.",
                       detail={"seen": rd.option_key}, sources=[SRC_CLIP])
    ok = iso in allowed
    return Finding(technique=105, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.5,
                   summary=(f"Road markings read as {rd.option_label}, used in "
                            f"{len(allowed)} countries; the leading candidate ({iso}) "
                            + ("is one" if ok else "is not")),
                   detail={"seen": rd.option_key, "contradiction": not ok},
                   sources=[SRC_CLIP])


@technique(id=106, key="curb_drainage", name="Curb / drainage design matching",
           phase=PHASE, needs=("evidence",),
           fusion_note="Reports the kerb reading (17); no open table of kerb "
                       "and drainage standards by country exists to match.")
def curb_drainage(ctx: Context) -> Finding:
    sp = spec(106)
    rd = reading(ctx, "pavement")
    if rd is None:
        return _na(sp, "the zero-shot pass did not run")
    return Finding(technique=106, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=rd.confidence >= 0.35, confidence=rd.confidence,
                   summary=(f"Kerb reads as {rd.option_label.lower()} "
                            f"({rd.confidence:.0%}); there is no open table of "
                            f"kerb standards to match it against."),
                   detail={"seen": rd.option_key}, sources=[SRC_CLIP])


@technique(id=107, key="streetlights", name="Streetlight design matching",
           phase=PHASE, needs=("embedding",),
           fusion_note="Reports streetlight styles seen; no open table of "
                       "streetlight standards exists to match.")
def streetlights(ctx: Context) -> Finding:
    sp = spec(107)
    p = zero_shot(ctx, {
        "cobra": ["a cobra-head street light on a curved arm"],
        "led": ["a modern flat LED street light"],
        "sodium": ["orange sodium street lights at night"],
        "ornate": ["an ornate decorative heritage lamp post"],
        "solar": ["a solar-powered street light with a panel"],
        "none": ["a scene with no street lights"]}, "streetlights")
    if p is None:
        return _na(sp, "the image encoder is not available")
    best = max(p.items(), key=lambda kv: kv[1])
    if best[0] == "none" or best[1] < 0.4:
        return Finding.nothing_found(sp, "No streetlight clearly visible.")
    return Finding(technique=107, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=best[1],
                   summary=f"Streetlights read as {best[0]} ({best[1]:.0%}); "
                           f"reported only, with no standards table to match.",
                   detail={"presence": {k: round(v, 3) for k, v in p.items()}},
                   sources=[SRC_CLIP])


@technique(id=108, key="manholes", name="Manhole / utility-cover identification",
           phase=PHASE, needs=("embedding", "pixels"),
           fusion_note="Finds covers in the lower half of the frame; reading a "
                       "city's name off one is left to OCR, which reports it "
                       "as text.")
def manholes(ctx: Context) -> Finding:
    sp = spec(108)
    hits = []
    for box, emb in tiles(ctx, 2):
        if box[1] < 0.5:
            continue
        p = zero_shot(ctx, {"cover": ["a round metal manhole cover on the road"],
                            "none": ["an ordinary road surface"]},
                      "manholes", embedding=emb)
        if p and p["cover"] >= 0.6:
            hits.append((box, p["cover"]))
    if not hits:
        return Finding.nothing_found(sp, "No utility cover visible.")
    return Finding(technique=108, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=max(v for _, v in hits),
                   summary=f"A utility cover appears in the lower frame "
                           f"({max(v for _, v in hits):.0%}); any lettering on it "
                           f"is read as text by technique 6.",
                   regions=[Region(*b, label="utility cover") for b, _ in hits],
                   detail={"tiles": [list(b) for b, _ in hits]}, sources=[SRC_CLIP])


@technique(id=109, key="transit_match", name="Bus-stop / transit-infrastructure matching",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="Transit seen (24) against stations stored near the "
                       "leading candidate.")
def transit_match(ctx: Context) -> Finding:
    sp = spec(109)
    rd = reading(ctx, "transit")
    top = leader(ctx)
    stations = []
    try:
        from ..geodata.nearby import nearby
        from ..geodata.service import get_service
        s = nearby(get_service(), top.latitude, top.longitude, 1500, fetch=False, limit=40)
        stations = [f for f in s.features if "station" in f.kind or "halt" in f.kind]
    except Exception:
        pass
    seen = rd.option_label if rd is not None and rd.option_key != "none" and \
        rd.confidence >= 0.35 else None
    if not seen and not stations:
        return Finding.nothing_found(sp, "No public transport seen, and no station "
                                         "stored nearby.")
    parts = []
    if seen:
        parts.append(f"the scene shows {seen.lower()}")
    if stations:
        parts.append("stations stored near the leading candidate: " + ", ".join(
            f"{f.name} ({f.distance_m} m)" for f in stations[:3]))
    return Finding(technique=109, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary="; ".join(parts).capitalize() + ".",
                   detail={"seen": rd.option_key if rd else None,
                           "stations": [f.as_dict() for f in stations[:5]]},
                   confidence=0.4, sources=[SRC_CLIP, SRC_OSM_LOCAL])


def _brand_hits(ctx: Context, kinds: set[str]):
    text = _ocr_text(ctx).lower()
    hits = []
    for brand, (kind, countries) in BRANDS.items():
        if kind not in kinds:
            continue
        if re.search(r"(?<![a-z])" + re.escape(brand) + r"(?![a-z])", text):
            hits.append((brand, kind, countries))
    return hits


@technique(id=110, key="telecom", name="Telecom / ISP infrastructure clues",
           phase=PHASE, needs=("ocr",),
           fusion_note="Telecom brands in the text, against the countries they "
                       "operate in. Not yet measured as a fusion input.")
def telecom(ctx: Context) -> Finding:
    sp = spec(110)
    hits = _brand_hits(ctx, {"telecom"})
    if not hits:
        return Finding.nothing_found(sp, "No telecom brand appears in the text.")
    countries = set.intersection(*(set(c) for _, _, c in hits)) or \
        set.union(*(set(c) for _, _, c in hits))
    return Finding(technique=110, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.6 if len(countries) <= 3 else 0.3,
                   summary=("Telecom brands in the text: "
                            + ", ".join(b for b, _, _ in hits)
                            + f" — operating in {', '.join(sorted(countries)[:10])}"),
                   detail={"brands": [b for b, _, _ in hits],
                           "countries": sorted(countries)},
                   sources=[SRC_OCR, "hand-maintained brand table"])
