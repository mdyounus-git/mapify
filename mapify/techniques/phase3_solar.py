"""Techniques 33-42: the sun, shadows, season, the Moon and the night.

The first block of techniques whose evidence is physics rather than an
opinion from the image encoder. The sun occupies exactly one point in
the sky for a given place and instant, so a photograph's light either
agrees with a candidate location and capture time or it does not.

What these do, concretely
-------------------------

  * compute the sun and Moon at the leading candidate for the capture
    instant (33, 36, 37, 41), and what shadows should look like (34, 35);
  * check the photograph's light against sunrise and sunset there (39,
    42) and its vegetation against the season (40);
  * measure the sun directly when its disc is in frame (36, 37) and the
    lens is known -- the rare case where a photograph gives an angle.

What they do not do
-------------------

Estimate a time of day from an arbitrary photograph. That needs a
measurable sun or shadow, a known camera direction and a known object
height; most photographs have none of these, and for them the output
says "not determinable". Every check that relies on the capture time
also says which basis that time rests on -- and when the time zone was
itself taken from the candidate location, that the check is partly
circular.

None of these feed the fusion yet. A contradiction is reported as a
contradiction, for the reader. Letting it move the answer needs the same
benchmark run every other evidence source had to pass.
"""

from __future__ import annotations

from datetime import timezone

from ..chrono.capture_time import camera_heading, resolve
from ..chrono.moon import moon_position
from ..chrono.solar import sun_position, sun_times
from ..chrono.sun_in_frame import field_of_view, find_sun, measured_angles
from .base import Context, Finding, Region, technique
from .common import SRC_CLIP, SRC_EXIF, leader, reading, spec

PHASE = 3
SRC_NOAA = "NOAA solar position algorithm (after Meeus)"
SRC_MEEUS = "Meeus, Astronomical Algorithms ch. 47 (ELP-2000/82 main terms)"
_COMPASS = ("north", "north-east", "east", "south-east", "south",
            "south-west", "west", "north-west")


def _compass(az: float) -> str:
    return _COMPASS[int(((az % 360) + 22.5) // 45) % 8]


def _chrono(ctx: Context) -> dict:
    """Capture time, sun and Moon at the leading candidate, computed once."""
    if "chrono" in ctx.memo:
        return ctx.memo["chrono"]
    top = leader(ctx)
    lat = getattr(top, "latitude", None)
    lon = getattr(top, "longitude", None)
    ct = resolve(ctx.raw_bytes, ctx.exif, lat, lon)
    out = {"time": ct, "lat": lat, "lon": lon, "sun": None, "moon": None,
           "times": None}
    if ct.known and lat is not None:
        out["sun"] = sun_position(lat, lon, ct.utc)
        out["moon"] = moon_position(lat, lon, ct.utc)
        out["times"] = sun_times(lat, lon, ct.utc)
    ctx.memo["chrono"] = out
    return out


def _basis_note(ct) -> str:
    if ct.basis == "gps":
        return "capture time from the GPS clock (UTC)"
    if ct.basis == "offset":
        return f"capture time with its recorded offset ({ct.zone})"
    if ct.basis == "zone":
        return (f"capture time assumed to be local time in {ct.zone} -- "
                f"taken from this same candidate, so the check is partly "
                f"circular")
    return "no capture time"


def _need_time(sp, c) -> Finding | None:
    if c["time"].known and c["sun"] is not None:
        return None
    why = "; ".join(c["time"].notes) or "no capture time and location"
    return Finding.not_applicable(sp, why)


# ---------------------------------------------------------------- 33

@technique(id=33, key="sun_position", name="Sun position", phase=PHASE,
           needs=("time", "candidates"),
           fusion_note="Computed, not observed: it is the reference the "
                       "other solar techniques test the photograph against.")
def sun_position_t(ctx: Context) -> Finding:
    sp = spec(33)
    c = _chrono(ctx)
    miss = _need_time(sp, c)
    if miss:
        return miss
    sun, ct = c["sun"], c["time"]
    summary = (f"At the leading candidate, at {ct.utc:%Y-%m-%d %H:%M} UTC, the "
               f"sun was {sun.elevation:.1f}° above the horizon in the "
               f"{_compass(sun.azimuth)} ({sun.azimuth:.0f}°): {sun.phase}")
    return Finding(
        technique=33, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=True, summary=summary + f". Basis: {_basis_note(ct)}.",
        detail={"elevation": round(sun.elevation, 2),
                "azimuth": round(sun.azimuth, 2),
                "declination": round(sun.declination, 3),
                "phase": sun.phase, "capture_time": ct.as_dict()},
        confidence=0.6 if ct.assumed else 0.95,
        sources=[SRC_NOAA, SRC_EXIF])


# ------------------------------------------------------------- 34, 35

@technique(id=34, key="shadow_direction", name="Shadow direction",
           phase=PHASE, needs=("time", "candidates"),
           fusion_note="A prediction to check by eye, not a measurement: "
                       "recovering shadow direction from pixels reliably "
                       "is beyond what this tool can honestly claim.")
def shadow_direction(ctx: Context) -> Finding:
    sp = spec(34)
    c = _chrono(ctx)
    miss = _need_time(sp, c)
    if miss:
        return miss
    sun = c["sun"]
    if sun.elevation <= 0.5:
        return Finding(
            technique=34, key=sp.key, name=sp.name, phase=PHASE, ran=True,
            found=False,
            summary="The sun was below the horizon: there should be no sun "
                    "shadows at all. Visible sharp shadows would contradict "
                    "this time or place.",
            detail={"sun_elevation": round(sun.elevation, 2)},
            confidence=0.5, sources=[SRC_NOAA])
    shadow_az = (sun.azimuth + 180.0) % 360.0
    heading, ref = camera_heading(ctx.raw_bytes)
    detail = {"shadow_azimuth": round(shadow_az, 1), "camera_heading": heading,
              "measured_from_pixels": False}
    summary = (f"Shadows should point {_compass(shadow_az)} ({shadow_az:.0f}°)")
    if heading is not None:
        rel = (shadow_az - heading + 540.0) % 360.0 - 180.0
        side = ("straight away from the camera" if abs(rel) < 20 else
                "toward the camera" if abs(rel) > 160 else
                ("to the right" if rel > 0 else "to the left")
                + (" and away" if abs(rel) < 90 else " and toward the camera"))
        detail["relative_to_view"] = round(rel, 1)
        summary += (f"; the camera faced {heading:.0f}° ({ref}), so in the "
                    f"frame they should fall {side}")
    summary += ". Check it against the photograph: a clear disagreement means "
    summary += "the time, the place or the file is wrong."
    return Finding(
        technique=34, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=True, summary=summary, detail=detail, confidence=0.5,
        sources=[SRC_NOAA, SRC_EXIF])


@technique(id=35, key="shadow_length", name="Shadow length", phase=PHASE,
           needs=("time", "candidates"),
           fusion_note="A prediction: measuring it needs an object of "
                       "known height, which the photograph rarely offers.")
def shadow_length(ctx: Context) -> Finding:
    sp = spec(35)
    c = _chrono(ctx)
    miss = _need_time(sp, c)
    if miss:
        return miss
    ratio = c["sun"].shadow_ratio()
    if ratio is None:
        return Finding.nothing_found(sp, "The sun was down or on the horizon: "
                                         "no measurable shadows.")
    return Finding(
        technique=35, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=True,
        summary=(f"Shadows should be {ratio:.2f} times the height of what "
                 f"casts them -- a 1.7 m person about {1.7 * ratio:.1f} m, a "
                 f"10 m building about {10 * ratio:.0f} m. Not measured from "
                 f"the pixels: that needs an object of known height."),
        detail={"shadow_to_height": round(ratio, 3),
                "sun_elevation": round(c["sun"].elevation, 2)},
        confidence=0.5, sources=[SRC_NOAA])


# ------------------------------------------------------------- 36, 37

def _disc(ctx: Context):
    if "sun_disc" not in ctx.memo:
        ctx.memo["sun_disc"] = find_sun(ctx.image) if ctx.image is not None else None
    return ctx.memo["sun_disc"]


def _measured(ctx: Context) -> dict | None:
    disc = _disc(ctx)
    if disc is None:
        return None
    other = getattr(ctx.exif, "other", {}) or {}
    fov = field_of_view(other, ctx.image.width, ctx.image.height)
    if fov is None:
        return {"disc": disc.as_dict(), "fov": None}
    heading, _ref = camera_heading(ctx.raw_bytes)
    return {"disc": disc.as_dict(), "fov": [round(fov[0], 1), round(fov[1], 1)],
            **measured_angles(disc, fov, heading)}


@technique(id=36, key="solar_elev_az", name="Solar elevation / azimuth estimation",
           phase=PHASE, needs=("pixels", "candidates"),
           fusion_note="Measured only when the sun's disc is in frame and the "
                       "lens is known (azimuth also needs the camera's compass "
                       "heading); always compared with the computed position.")
def solar_elev_az(ctx: Context) -> Finding:
    sp = spec(36)
    c = _chrono(ctx)
    m = _measured(ctx)
    detail, regions, parts = {}, [], []
    if m:
        d = m["disc"]
        r = d["radius"]
        regions.append(Region(max(0, d["x"] - 2 * r), max(0, d["y"] - 2 * r),
                              min(1, d["x"] + 2 * r), min(1, d["y"] + 2 * r),
                              "the sun"))
        detail["measured"] = m
    sun = c["sun"]
    if sun is not None:
        detail["computed"] = {"elevation": round(sun.elevation, 2),
                              "azimuth": round(sun.azimuth, 2)}
    contradiction = False
    if m and m.get("elevation") is not None and sun is not None:
        diff = m["elevation"] - sun.elevation
        ok = abs(diff) <= m["elevation_uncertainty"] + 2
        contradiction |= not ok
        detail["elevation_difference"] = round(diff, 1)
        parts.append(f"the sun is in frame at about {m['elevation']:.0f}° "
                     f"(±{m['elevation_uncertainty']:.0f}°, level camera "
                     f"assumed) against {sun.elevation:.0f}° computed — "
                     + ("consistent" if ok else f"{abs(diff):.0f}° apart"))
        if m.get("azimuth") is not None:
            daz = (m["azimuth"] - sun.azimuth + 540.0) % 360.0 - 180.0
            ok_az = abs(daz) <= m["azimuth_uncertainty"] + 3
            contradiction |= not ok_az
            detail["azimuth_difference"] = round(daz, 1)
            parts.append(f"azimuth {m['azimuth']:.0f}° against "
                         f"{sun.azimuth:.0f}° — "
                         + ("consistent" if ok_az else f"{abs(daz):.0f}° apart"))
        else:
            parts.append("azimuth not measured: the camera's compass heading "
                         "is not recorded")
        detail["contradiction"] = contradiction
        return Finding(technique=36, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=True,
                       summary=("; ".join(parts)).capitalize()
                       + (" — a contradiction worth checking" if contradiction
                          else ""),
                       detail=detail, confidence=0.6, regions=regions,
                       sources=["the image itself", SRC_NOAA, SRC_EXIF])
    if m and m.get("fov") is None:
        return Finding(technique=36, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=True, regions=regions, detail=detail,
                       summary="The sun's disc appears to be in frame, but the "
                               "lens's focal length is not recorded, so its "
                               "angles cannot be measured.",
                       confidence=0.3, sources=["the image itself"])
    if sun is not None:
        return Finding(technique=36, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=True, detail=detail, confidence=0.5,
                       summary=(f"Computed only: elevation {sun.elevation:.1f}°, "
                                f"azimuth {sun.azimuth:.0f}° "
                                f"({_compass(sun.azimuth)}) at the leading "
                                f"candidate. The sun is not in frame to measure."),
                       sources=[SRC_NOAA])
    return Finding.nothing_found(
        sp, "The sun is not in frame and there is no capture time: solar "
            "elevation and azimuth are not determinable.")


# ---------------------------------------------------------------- 37

@technique(id=37, key="local_time", name="Approximate local time estimation",
           phase=PHASE, needs=("time", "candidates"),
           fusion_note="Reports the capture time in the candidate's zone "
                       "and the local solar time; recovering a time from "
                       "light alone is not attempted.")
def local_time(ctx: Context) -> Finding:
    sp = spec(37)
    c = _chrono(ctx)
    ct = c["time"]
    if not ct.known:
        return Finding.not_applicable(sp, "; ".join(ct.notes) or "no capture time")
    detail = {"capture_time": ct.as_dict()}
    parts = [f"Captured {ct.utc:%Y-%m-%d %H:%M} UTC"]
    try:
        from zoneinfo import ZoneInfo
        from ..geo import timezone as tz
        info = tz.lookup(c["lat"], c["lon"])
        if info.iana_name:
            local = ct.utc.astimezone(ZoneInfo(info.iana_name))
            parts.append(f"{local:%H:%M} local time in {info.iana_name}")
            detail["local"] = local.isoformat()
    except Exception:
        pass
    if c["sun"] is not None:
        solar_minutes = (ct.utc.hour * 60 + ct.utc.minute + c["lon"] * 4
                         + c["sun"].equation_of_time_min) % 1440
        parts.append(f"{int(solar_minutes // 60):02d}:{int(solar_minutes % 60):02d} "
                     f"local solar time")
        detail["solar_time_minutes"] = round(solar_minutes, 1)
    return Finding(
        technique=37, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=True,
        summary="; ".join(parts) + f". Basis: {_basis_note(ct)}. "
                "Not estimated from the light: that needs a measurable sun.",
        detail=detail, confidence=0.4 if ct.assumed else 0.9,
        sources=[SRC_EXIF, SRC_NOAA])


# ---------------------------------------------------------------- 39

# Lighting readings, and the sun elevations each is compatible with.
_LIGHT_RANGE = {
    "night": (-90.0, -4.0),
    "twilight": (-12.0, 2.0),
    "golden": (-2.0, 18.0),
    "day": (8.0, 90.0),
    "diffuse": (0.0, 90.0),
}


@technique(id=38, key="sunrise_sunset", name="Sunrise / sunset constraints",
           phase=PHASE, needs=("time", "candidates", "evidence"),
           fusion_note="A consistency check between the photograph's light "
                       "and the sun's height there and then. Unmeasured as "
                       "a fusion input; reported.")
def sunrise_sunset(ctx: Context) -> Finding:
    sp = spec(38)
    c = _chrono(ctx)
    miss = _need_time(sp, c)
    if miss:
        return miss
    rd = reading(ctx, "lighting")
    sun, times = c["sun"], c["times"]
    detail = {"sun_elevation": round(sun.elevation, 2),
              "sun_times": times.as_dict() if times else None}
    rise_set = ""
    if times and times.sunrise and times.sunset:
        rise_set = (f" (sunrise {times.sunrise:%H:%M}, sunset "
                    f"{times.sunset:%H:%M} UTC)")
    elif times and times.polar:
        rise_set = f" (polar {times.polar})"
    if rd is None or rd.option_key == "indoor" or rd.confidence < 0.35:
        return Finding(
            technique=38, key=sp.key, name=sp.name, phase=PHASE, ran=True,
            found=False, detail=detail, confidence=0.0,
            summary=(f"The sun was at {sun.elevation:.0f}°{rise_set}; the "
                     f"photograph's light is too indoor or ambiguous to "
                     f"check against it."), sources=[SRC_NOAA, SRC_CLIP])
    lo, hi = _LIGHT_RANGE.get(rd.option_key, (-90.0, 90.0))
    agree = lo <= sun.elevation <= hi
    detail.update({"lighting": rd.option_key, "compatible_range": [lo, hi],
                   "contradiction": not agree})
    verdict = ("consistent" if agree else
               "a contradiction: either the capture time or the location is "
               "wrong, or the clock was never set")
    return Finding(
        technique=38, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=True, detail=detail, confidence=rd.confidence,
        summary=(f"The light reads as {rd.option_label.lower()} and the sun "
                 f"was {sun.elevation:.0f}° up there and then{rise_set} — "
                 f"{verdict}. Basis: {_basis_note(c['time'])}."),
        sources=[SRC_NOAA, SRC_CLIP, SRC_EXIF])


# ---------------------------------------------------------------- 39

@technique(id=39, key="season_solar", name="Season estimation from solar geometry",
           phase=PHASE, needs=("candidates",),
           fusion_note="Inverts the solar elevation formula for the sun's "
                       "declination, which names two dates a year. Needs a "
                       "measured sun at a known time of day, which few "
                       "photographs give; otherwise not determinable.")
def season_solar(ctx: Context) -> Finding:
    import math
    sp = spec(39)
    c = _chrono(ctx)
    m = _measured(ctx)
    ct = c["time"]
    if not (m and m.get("elevation") is not None and ct.known):
        detail = {}
        summary = ("Not determinable from solar geometry: that needs the sun "
                   "measured in frame and the time of day it was measured at")
        if c["sun"] is not None:
            noon = 90 - abs(c["lat"] - c["sun"].declination)
            detail["expected_noon_elevation"] = round(noon, 1)
            summary += (f". For the file's date the sun's noon height there "
                        f"would be {noon:.0f}°.")
        return Finding(technique=39, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=False, summary=summary, detail=detail,
                       confidence=0.0, sources=[SRC_NOAA])
    lat = math.radians(c["lat"])
    el = math.radians(m["elevation"])
    ha = math.radians(c["sun"].hour_angle)
    # sin(el) = sin(lat) sin(d) + cos(lat) cos(d) cos(ha): solve for d.
    a, b = math.sin(lat), math.cos(lat) * math.cos(ha)
    r = math.hypot(a, b)
    if r < 1e-9 or abs(math.sin(el) / r) > 1:
        return Finding.nothing_found(sp, "The measured elevation is not reachable "
                                         "at this latitude and hour.")
    phi = math.atan2(b, a)
    d = math.asin(math.sin(el) / r) - phi
    d = math.degrees(math.atan2(math.sin(d), math.cos(d)))
    if abs(d) > 23.6:
        return Finding.nothing_found(sp, f"The implied declination {d:.1f}° is "
                                         f"outside the sun's range.")
    import datetime as dt
    days = [n for n in range(1, 366)
            if abs(23.44 * math.sin(math.radians(360 / 365 * (n - 81))) - d) < 1.2]
    dates = sorted({(dt.date(2001, 1, 1) + dt.timedelta(days=n - 1)).strftime("%d %b")
                    for n in days[::max(1, len(days) // 4)]})
    summary = (f"The measured sun implies a declination of {d:+.1f}°, which the "
               f"sun reaches around {', '.join(dates)} — a date range of a few "
               f"days either side, twice a year")
    detail = {"declination": round(d, 2), "day_of_year_matches": days}
    if ct.known:
        month_day = ct.utc.strftime("%d %b")
        detail["capture_date"] = month_day
    return Finding(technique=39, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary, detail=detail, confidence=0.4,
                   sources=["the image itself", SRC_NOAA])


# ---------------------------------------------------------------- 40

@technique(id=40, key="moon", name="Moon / celestial-object analysis", phase=PHASE,
           needs=("time", "candidates"),
           fusion_note="Computed for the reader to compare with any Moon in "
                       "the photograph; detecting the Moon itself is not "
                       "attempted.")
def moon(ctx: Context) -> Finding:
    sp = spec(40)
    c = _chrono(ctx)
    miss = _need_time(sp, c)
    if miss:
        return miss
    m = c["moon"]
    where = (f"{m.altitude:.0f}° up in the {_compass(m.azimuth)}"
             if m.up else "below the horizon")
    return Finding(
        technique=40, key=sp.key, name=sp.name, phase=PHASE, ran=True,
        found=True,
        summary=(f"The Moon was {m.phase_name}, {m.illuminated:.0%} lit, "
                 f"{where}. A Moon in the photograph with a different phase, "
                 f"or visible when it was down, would contradict this time "
                 f"or place."),
        detail={"altitude": round(m.altitude, 2), "azimuth": round(m.azimuth, 2),
                "illuminated": round(m.illuminated, 4), "phase": m.phase_name,
                "waxing": m.waxing, "up": m.up},
        confidence=0.9 if not c["time"].assumed else 0.5,
        sources=[SRC_MEEUS, SRC_EXIF])


# ---------------------------------------------------------------- 41

@technique(id=41, key="night_sky", name="Star-field / night-sky analysis",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="Checks a night sky against the sun's depth below the "
                       "horizon. Star-pattern matching is not attempted: it "
                       "needs a sharp long exposure of the sky that ordinary "
                       "photographs are not.")
def night_sky(ctx: Context) -> Finding:
    sp = spec(41)
    sky = reading(ctx, "sky")
    if sky is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    detail = {"sky": sky.option_key, "star_pattern_matching": "not attempted"}
    if sky.option_key != "night" or sky.confidence < 0.35:
        return Finding(technique=41, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=False, detail=detail, confidence=0.0,
                       summary="No night sky in view.", sources=[SRC_CLIP])
    c = _chrono(ctx)
    summary = f"A night sky ({sky.confidence:.0%})"
    if c["sun"] is not None:
        dark = c["sun"].elevation < -6
        detail["sun_elevation"] = round(c["sun"].elevation, 2)
        detail["contradiction"] = not dark
        summary += (f"; the sun was {c['sun'].elevation:.0f}° there and then — "
                    + ("dark enough" if dark else "too light for a night sky, a "
                       "contradiction"))
        if c["moon"] is not None and c["moon"].up:
            summary += (f"; the Moon was up, {c['moon'].illuminated:.0%} lit, "
                        f"which washes out faint stars")
    else:
        summary += "; without a capture time, darkness cannot be checked"
    return Finding(technique=41, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=True, summary=summary + ".", detail=detail,
                   confidence=sky.confidence, sources=[SRC_CLIP, SRC_NOAA])


# ---------------------------------------------------------------- 42

@technique(id=42, key="lighting", name="Image lighting & artificial-light analysis",
           phase=PHASE, needs=("evidence",),
           fusion_note="Reads the light; checks artificial light against the "
                       "sun when a time and place are known.")
def lighting(ctx: Context) -> Finding:
    sp = spec(42)
    rd = reading(ctx, "lighting")
    if rd is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    detail = {"lighting": rd.option_key, "confidence": round(rd.confidence, 4)}
    summary = f"The light reads as {rd.option_label.lower()} ({rd.confidence:.0%})"
    night = rd.option_key == "night" and rd.confidence >= 0.35
    if ctx.candidates:
        c = _chrono(ctx)
        if night and c["sun"] is not None:
            dark = c["sun"].elevation < -4
            detail["sun_elevation"] = round(c["sun"].elevation, 2)
            detail["contradiction"] = not dark
            summary += (f", lit artificially; the sun was "
                        f"{c['sun'].elevation:.0f}° there and then — "
                        + ("consistent" if dark else "a contradiction"))
    return Finding(technique=42, key=sp.key, name=sp.name, phase=PHASE,
                   ran=True, found=rd.confidence >= 0.35, summary=summary + ".",
                   detail=detail, confidence=rd.confidence,
                   sources=[SRC_CLIP, SRC_NOAA])
