"""Techniques 111-131: regional intelligence, mostly read out of the text.

The best evidence left after phase 3 is text: phone numbers, web
domains, prices, units, postcodes, brand names, a state code on a
number plate. It is read off the image rather than inferred from it,
which makes it independent of the image encoder -- the only kind of
evidence Phase 22 showed actually improves the answer. Each is checked
against the public tables in mapify/data/regional.py, and a weak match
("$", a five-digit number) is reported as weak.

The terrain checks (127, 128) ask Open-Meteo's elevation service for
the Copernicus DEM around the leading candidate and compare its relief
with the landform in the photograph -- a coordinate list goes out,
never the image. Road bearing (129) needs the camera's position and
compass heading, and uses the local street store.

Viewpoint matching and re-projection against candidate viewpoints (130,
131) need 3D reference imagery this project cannot license; registered,
with the reason.

None of these feed the fusion yet: the text clues are the strongest
candidates for it, and each will be measured before it is allowed to
move an answer.
"""

from __future__ import annotations

import math
import re

from ..data.countries import get_table
from ..data.regional import (CALLING_CODES, CURRENCIES, GENERIC_TLDS, INDIA_PLATE_STATES,
                             INDIA_STD, MPH_COUNTRIES, POSTCODES)
from .base import Context, Finding, Region, technique
from .common import (SRC_CLIP, SRC_EXIF, SRC_OCR, SRC_OSM_LOCAL, box_region,
                     describe_reading, leader, reading, spec, zero_shot)

PHASE = 8
SRC_TABLE = "hand-maintained table of public regional facts"


def _lines(ctx: Context, min_conf: float = 0.35):
    return [l for l in (getattr(ctx.ocr, "lines", None) or []) if l.confidence >= min_conf]


def _text(ctx: Context) -> str:
    return "\n".join(l.text for l in _lines(ctx))


def _leader_inside(ctx: Context, countries) -> bool | None:
    iso = getattr(leader(ctx), "iso_a2", None) if ctx.candidates else None
    return (iso in set(countries)) if iso else None


def _text_finding(tid: int, ctx: Context, what: str, countries: list[str],
                  matches: list[str], narrow: bool, regions=()) -> Finding:
    sp = spec(tid)
    inside = _leader_inside(ctx, countries)
    detail = {"matches": matches, "countries": sorted(set(countries)),
              "narrow": narrow}
    summary = f"{what}: {', '.join(matches[:4])} — "
    names = []
    table = get_table()
    for iso in sorted(set(countries))[:8]:
        c = table.get(iso)
        names.append(c.name if c else iso)
    summary += ("points to " + ", ".join(names) if narrow else
                "shared by many countries (" + ", ".join(names[:5]) + "...), weak")
    if inside is not None:
        detail["leader_inside"] = inside
        detail["contradiction"] = narrow and not inside
        summary += ("; the leading candidate agrees" if inside else
                    "; the leading candidate is elsewhere")
    return Finding(technique=tid, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.7 if narrow else 0.2, regions=list(regions),
                   sources=[SRC_OCR, SRC_TABLE])


# ---------------------------------------------------------- 111-116

@technique(id=111, key="plate_region", name="Vehicle registration-region analysis",
           phase=PHASE, needs=("ocr",),
           fusion_note="Reads the region code on a plate that 11 read -- for "
                       "India, the state. Unmeasured as a fusion input.")
def plate_region(ctx: Context) -> Finding:
    sp = spec(111)
    f11 = ctx.finding(11)
    cands = (f11.detail.get("candidates", []) if f11 is not None and f11.found else [])
    found = []
    for c in cands:
        text = re.sub(r"[\s\-.]", "", c.get("text", "")).upper()
        in_scheme = any("IN" in s.get("countries", []) for s in c.get("schemes", []))
        code = text[:2]
        if in_scheme and code in INDIA_PLATE_STATES:
            found.append((c["text"], code, INDIA_PLATE_STATES[code]))
    if not found:
        return Finding.nothing_found(sp, "No plate with a readable region code.")
    text, code, state = found[0]
    return Finding(technique=111, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.55,
                   summary=(f"The plate \"{text}\" is registered in {state} ({code}). "
                            f"Vehicles travel, so this says where the car is from, "
                            f"not strictly where it is."),
                   detail={"plates": [{"text": t, "code": c, "region": s}
                                      for t, c, s in found]},
                   sources=[SRC_OCR, "Indian registration state codes"])


@technique(id=112, key="driving_side", name="Driving-side determination",
           phase=PHASE, needs=("evidence",), feeds_fusion=True,
           fusion_note="The strongest coarse discriminator; in the fusion since "
                       "Phase 9 and measured.")
def driving_side(ctx: Context) -> Finding:
    return describe_reading(spec(112), reading(ctx, "traffic_side"), steers=True)


@technique(id=113, key="lane_orientation", name="Road-lane orientation analysis",
           phase=PHASE, needs=("evidence",),
           fusion_note="Lane orientation relative to the camera is not measured; "
                       "the regional signal it carries is the driving side (112).")
def lane_orientation(ctx: Context) -> Finding:
    sp = spec(113)
    rd = reading(ctx, "traffic_side")
    if rd is None:
        return Finding.not_applicable(sp, "the zero-shot pass did not run")
    return Finding(technique=113, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=False, confidence=0.0,
                   summary=("Lane geometry relative to the camera is not measured; "
                            f"the traffic reads as {rd.option_label.lower()}, which "
                            "technique 112 carries."),
                   detail={"traffic": rd.option_key}, sources=[SRC_CLIP])


@technique(id=114, key="materials", name="Local construction-material analysis",
           phase=PHASE, needs=("evidence",),
           fusion_note="The facade material reading (14). Reported only.")
def materials(ctx: Context) -> Finding:
    return describe_reading(spec(114), reading(ctx, "facade"), steers=False)


@technique(id=115, key="facade_paint", name="Regional paint / facade-style analysis",
           phase=PHASE, needs=("pixels",),
           fusion_note="The colours of the built band of the frame. Paint "
                       "traditions exist, but no measured table ties colours "
                       "to places; reported only.")
def facade_paint(ctx: Context) -> Finding:
    import numpy as np
    from .phase6_pixels import _name
    sp = spec(115)
    im = ctx.image.convert("RGB")
    w, h = im.size
    band = im.crop((0, int(h * 0.25), w, int(h * 0.75)))
    band.thumbnail((200, 200))
    a = np.asarray(band, dtype=np.float32).reshape(-1, 3)
    sat = (a.max(1) - a.min(1)) / (a.max(1) + 1e-6)
    vivid = a[sat > 0.35]
    if len(vivid) < 0.05 * len(a):
        return Finding(technique=115, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=False, confidence=0.0,
                       summary="The built band of the frame is mostly muted: no "
                               "strong paint colours.", sources=["measured from the pixels"])
    names = {}
    for px in vivid[:: max(1, len(vivid) // 800)]:
        n = _name(px)
        names[n] = names.get(n, 0) + 1
    top = sorted(names.items(), key=lambda kv: -kv[1])[:3]
    total = sum(names.values())
    return Finding(technique=115, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.3,
                   summary=("Strong colours in the built band: "
                            + ", ".join(f"{n} {c / total:.0%}" for n, c in top)
                            + f" ({len(vivid) / len(a):.0%} of it vivid). "
                              "Reported only."),
                   detail={"vivid_fraction": round(len(vivid) / len(a), 3),
                           "colours": top}, sources=["measured from the pixels"])


_CULTURE = {
    "temple": ["a Hindu temple with a carved gopuram tower"],
    "mosque": ["a mosque with minarets and a dome"],
    "church": ["a church with a steeple"],
    "pagoda": ["a Buddhist pagoda or stupa"],
    "market": ["a street market with stalls and vendors"],
    "kolam": ["a kolam or rangoli pattern drawn on the ground"],
    "prayer_flags": ["colourful Tibetan prayer flags"],
    "none": ["an ordinary street with no religious or market features"],
}


@technique(id=116, key="cultural_clues", name="Cultural / commercial visual clues",
           phase=PHASE, needs=("embedding",),
           fusion_note="Places, buildings and objects only -- never people. "
                       "Guessing where someone is from by looking at them is "
                       "unreliable and is the inference behind the worst uses "
                       "of tools like this; the project will not do it.")
def cultural_clues(ctx: Context) -> Finding:
    sp = spec(116)
    p = zero_shot(ctx, _CULTURE, "culture")
    if p is None:
        return Finding.not_applicable(sp, "the image encoder is not available")
    best = max(p.items(), key=lambda kv: kv[1])
    if best[0] == "none" or best[1] < 0.4:
        return Finding.nothing_found(sp, "No distinctive religious or commercial "
                                         "feature.")
    return Finding(technique=116, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=best[1],
                   summary=(f"The scene includes {_CULTURE[best[0]][0][2:]} "
                            f"({best[1]:.0%}) — reported only; no inference is "
                            f"made about any person."),
                   detail={"presence": {k: round(v, 3) for k, v in p.items()}},
                   sources=[SRC_CLIP])


# ---------------------------------------------------------- 117, 118

def _brands(ctx: Context, kinds: set[str]):
    from .phase7_geo import _brand_hits
    return _brand_hits(ctx, kinds)


def _brand_finding(tid: int, ctx: Context, kinds: set[str], what: str) -> Finding:
    hits = _brands(ctx, kinds)
    if not hits:
        return Finding.nothing_found(spec(tid), f"No {what} appears in the text.")
    sets = [set(c) for _, _, c in hits]
    both = set.intersection(*sets)
    countries = both or set.union(*sets)
    return _text_finding(tid, ctx, what.capitalize(),
                         sorted(countries), [b for b, _, _ in hits],
                         narrow=len(countries) <= 4)


@technique(id=117, key="retail_chains", name="Retail-chain geographic distribution matching",
           phase=PHASE, needs=("ocr",),
           fusion_note="Retail chains named in the text, against where they "
                       "trade. Unmeasured as a fusion input.")
def retail_chains(ctx: Context) -> Finding:
    return _brand_finding(117, ctx, {"retail"}, "retail chains")


@technique(id=118, key="brands", name="Logo / brand regional-distribution matching",
           phase=PHASE, needs=("ocr",),
           fusion_note="Fuel and banking brands named in the text. Logos "
                       "themselves are not recognised: no logo dataset has a "
                       "licence this project can use.")
def brands(ctx: Context) -> Finding:
    return _brand_finding(118, ctx, {"fuel", "bank"}, "fuel and banking brands")


# ---------------------------------------------------------- 119-124

_INTL = re.compile(r"(?:\+|\b00)\s?(\d{1,3})[\s\-.)]*\d")
_IN_MOBILE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)")
_IN_STD = re.compile(r"(?<!\d)0(\d{2,4})[\s\-]\d{6,8}(?!\d)")


@technique(id=119, key="phone_numbers", name="Telephone-number / area-code extraction",
           phase=PHASE, needs=("ocr",),
           fusion_note="Country calling codes and, for India, trunk codes and "
                       "mobile number shapes. Independent of the encoder; "
                       "unmeasured as a fusion input.")
def phone_numbers(ctx: Context) -> Finding:
    sp = spec(119)
    text = _text(ctx)
    matches, countries, cities = [], [], []
    for m in _INTL.finditer(text):
        digits = m.group(1)
        for n in (3, 2, 1):
            code = digits[:n]
            if code in CALLING_CODES:
                matches.append(f"+{code}")
                countries += CALLING_CODES[code]
                break
    for m in _IN_STD.finditer(text):
        code = m.group(1)
        for n in (4, 3, 2):
            if code[:n] in INDIA_STD:
                cities.append(INDIA_STD[code[:n]])
                matches.append(f"0{code[:n]} ({INDIA_STD[code[:n]]})")
                countries.append("IN")
                break
    if _IN_MOBILE.search(text):
        matches.append("an Indian mobile number")
        countries.append("IN")
    if not matches:
        return Finding.nothing_found(sp, "No telephone number in the text.")
    f = _text_finding(119, ctx, "Telephone numbers", countries, matches,
                      narrow=len(set(countries)) <= 3)
    if cities:
        f.detail["cities"] = sorted(set(cities))
        f.summary += f" The trunk code names {', '.join(sorted(set(cities)))}."
    return f


_URL = re.compile(r"(?i)\b(?:https?://)?(?:www\.)?[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.([a-z]{2,6})\b"
                  r"|@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.([a-z]{2,6})\b")


@technique(id=120, key="domains", name="Domain-name / URL geographic clues",
           phase=PHASE, needs=("ocr",),
           fusion_note="Country-code top-level domains in web and email "
                       "addresses. Generic and resold codes (.com, .io, .co, "
                       ".tv) are ignored.")
def domains(ctx: Context) -> Finding:
    sp = spec(120)
    table = get_table()
    tlds, countries = [], []
    for m in _URL.finditer(_text(ctx)):
        tld = (m.group(1) or m.group(2) or "").lower()
        if not tld or tld in GENERIC_TLDS:
            continue
        iso = "GB" if tld == "uk" else tld.upper()
        if len(tld) == 2 and table.get(iso):
            tlds.append("." + tld)
            countries.append(iso)
    if not tlds:
        return Finding.nothing_found(sp, "No country-code web or email address in "
                                         "the text.")
    return _text_finding(120, ctx, "Country-code domains", countries, tlds,
                         narrow=len(set(countries)) <= 2)


@technique(id=121, key="currency", name="Currency / price-format identification",
           phase=PHASE, needs=("ocr",),
           fusion_note="Currency signs and codes in prices. \"$\" alone is "
                       "dozens of countries and is reported as weak.")
def currency(ctx: Context) -> Finding:
    sp = spec(121)
    text = _text(ctx)
    hits = [(pat, cs, narrow) for pat, cs, narrow in CURRENCIES if re.search(pat, text)]
    if not hits:
        return Finding.nothing_found(sp, "No currency in the text.")
    narrow_hits = [h for h in hits if h[2]]
    use = narrow_hits or hits
    countries = [c for _, cs, _ in use for c in cs]
    labels = [re.sub(r"\\b|\\s\?|\\d|\\.\?|\(|\)|\?", "", p).split("|")[0] or p
              for p, _, _ in use]
    return _text_finding(121, ctx, "Currency", countries, labels,
                         narrow=bool(narrow_hits) and len(set(countries)) <= 3)


_DATE = re.compile(r"(?<!\d)(\d{1,4})[./-](\d{1,2})[./-](\d{2,4})(?!\d)")


@technique(id=122, key="date_format", name="Date-format identification",
           phase=PHASE, needs=("ocr",),
           fusion_note="Day-first, month-first or year-first dates. Weak: most "
                       "of the world writes day-first.")
def date_format(ctx: Context) -> Finding:
    sp = spec(122)
    styles = []
    for a, b, c in _DATE.findall(_text(ctx)):
        a_, b_ = int(a), int(b)
        if len(a) == 4:
            styles.append("year-first (YYYY-MM-DD)")
        elif a_ > 12 and b_ <= 12:
            styles.append("day-first (DD/MM)")
        elif b_ > 12 and a_ <= 12:
            styles.append("month-first (MM/DD)")
    if not styles:
        return Finding.nothing_found(sp, "No unambiguous date in the text.")
    top = max(set(styles), key=styles.count)
    note = {"month-first (MM/DD)": "month-first dates are mostly the United States "
                                   "and the Philippines",
            "day-first (DD/MM)": "day-first is most of the world, so this narrows "
                                 "little",
            "year-first (YYYY-MM-DD)": "year-first is typical of East Asia, "
                                       "Scandinavia and machine-printed labels"}[top]
    return Finding(technique=122, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.3 if "month" in top else 0.15,
                   summary=f"Dates are written {top}; {note}.",
                   detail={"styles": styles}, sources=[SRC_OCR])


_UNITS = {"mph": re.compile(r"(?i)\b\d+\s?mph\b|\bmiles?\b"),
          "kmh": re.compile(r"(?i)\b\d+\s?km/?h\b|\bkm\b"),
          "fahrenheit": re.compile(r"\d\s?°\s?F\b"),
          "celsius": re.compile(r"\d\s?°\s?C\b")}


@technique(id=123, key="units", name="Measurement-unit identification",
           phase=PHASE, needs=("ocr",),
           fusion_note="Miles and degrees Fahrenheit narrow the world sharply; "
                       "kilometres and Celsius hardly at all.")
def units(ctx: Context) -> Finding:
    sp = spec(123)
    text = _text(ctx)
    seen = [k for k, rx in _UNITS.items() if rx.search(text)]
    if not seen:
        return Finding.nothing_found(sp, "No measurement unit in the text.")
    if "mph" in seen or "fahrenheit" in seen:
        countries = sorted(MPH_COUNTRIES if "mph" in seen else {"US", "BS", "BZ", "KY", "LR", "PW"})
        return _text_finding(123, ctx, "Imperial units", countries, seen, narrow=True)
    return Finding(technique=123, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.1,
                   summary=f"Metric units ({', '.join(seen)}): used almost everywhere "
                           f"except the few mile countries, so this narrows little.",
                   detail={"units": seen}, sources=[SRC_OCR])


@technique(id=124, key="postcodes", name="Postal-code extraction",
           phase=PHASE, needs=("ocr",),
           fusion_note="Postcode shapes distinctive enough to name a country. "
                       "Five- and four-digit codes are skipped as too common.")
def postcodes(ctx: Context) -> Finding:
    sp = spec(124)
    hits, countries, regions = [], [], []
    for line in _lines(ctx, 0.4):
        for iso, label, rx in POSTCODES:
            m = rx.search(line.text.upper() if iso != "JP" else line.text)
            if m:
                hits.append(f"{m.group(0)} ({label})")
                countries.append(iso)
                regions += box_region(line, label)
    if not hits:
        return Finding.nothing_found(sp, "No distinctive postcode in the text.")
    return _text_finding(124, ctx, "Postcodes", countries, hits,
                         narrow=len(set(countries)) <= 2, regions=regions)


# ---------------------------------------------------------- 125-126

def _names_near(ctx: Context, lat: float, lon: float, radius: float):
    try:
        from ..geodata.service import get_service
        from ..geodata.store import bbox_around
        held = get_service().store.query(bbox_around(lat, lon, radius), ("poi",))["poi"]
        return [(f[2], f[1]) for _s, f in held if f[2]]
    except Exception:
        return []


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", s.lower())
            if w not in {"road", "street", "store", "shop", "hotel", "bank",
                         "centre", "center", "school", "hospital", "market"}}


def _match_names(ctx: Context, kinds_ok) -> list[dict]:
    words = set()
    for l in _lines(ctx, 0.45):
        words |= _tokens(l.text)
    if not words:
        return []
    out = []
    for i, c in enumerate(ctx.candidates[:5]):
        for name, kind in _names_near(ctx, c.latitude, c.longitude, 1500):
            if not kinds_ok(kind):
                continue
            t = _tokens(name)
            if t and len(t & words) >= max(1, min(2, len(t))):
                out.append({"rank": i + 1, "name": name, "kind": kind})
    return out


@technique(id=125, key="business_directory", name="Business-directory cross-reference",
           phase=PHASE, needs=("ocr", "candidates"),
           fusion_note="Business names in the text against businesses stored "
                       "near each candidate. The address reader (8) already "
                       "asks Nominatim; this uses the local store.")
def business_directory(ctx: Context) -> Finding:
    sp = spec(125)
    hits = _match_names(ctx, lambda k: k.startswith(("shop", "amenity", "office",
                                                     "craft", "healthcare")))
    if not hits:
        return Finding.nothing_found(sp, "No business named in the text is stored "
                                         "near the candidates.")
    return Finding(technique=125, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.7,
                   summary="Businesses named in the text are stored near "
                           + ", ".join(f"candidate #{h['rank']} ({h['name']})"
                                       for h in hits[:4]) + ".",
                   detail={"matches": hits}, sources=[SRC_OCR, SRC_OSM_LOCAL])


@technique(id=126, key="osm_features", name="OpenStreetMap feature matching",
           phase=PHASE, needs=("ocr", "candidates"),
           fusion_note="Any named OSM feature near a candidate whose name the "
                       "text contains -- landmarks, stations, institutions.")
def osm_features(ctx: Context) -> Finding:
    sp = spec(126)
    hits = _match_names(ctx, lambda k: True)
    if not hits:
        return Finding.nothing_found(sp, "No named feature near the candidates "
                                         "appears in the text.")
    return Finding(technique=126, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.7,
                   summary="The text names features stored near "
                           + ", ".join(f"candidate #{h['rank']} ({h['name']}, "
                                       f"{h['kind']})" for h in hits[:4]) + ".",
                   detail={"matches": hits}, sources=[SRC_OCR, SRC_OSM_LOCAL])


# ---------------------------------------------------------- 127-129

def _relief(ctx: Context):
    if "relief" in ctx.memo:
        return ctx.memo["relief"]
    from .phase5_temporal import _weather
    svc = _weather(ctx)
    top = leader(ctx)
    out = None
    if svc is not None and top is not None:
        pts = []
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                pts.append((top.latitude + dy * 0.06,
                            top.longitude + dx * 0.06 / max(0.2, math.cos(math.radians(top.latitude)))))
        el = svc.elevations(pts)
        if el:
            good = [e for e in el if e == e]
            if good:
                out = {"centre": el[12], "min": min(good), "max": max(good),
                       "relief": max(good) - min(good)}
    ctx.memo["relief"] = out
    return out


@technique(id=127, key="dem", name="Digital-elevation-model (DEM) matching",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="Elevation of the leading candidate from the Copernicus "
                       "DEM, against the landform in the photograph. Only "
                       "coordinates are sent.")
def dem(ctx: Context) -> Finding:
    from ..geodata.weather import DEM_ATTRIBUTION
    sp = spec(127)
    r = _relief(ctx)
    if r is None:
        return Finding.not_applicable(sp, "no elevation data (offline, or switched off)")
    rd = reading(ctx, "terrain")
    snow = reading(ctx, "snow_cover")
    detail = {"elevation_m": r["centre"]}
    summary = f"The leading candidate stands {r['centre']:.0f} m above sea level"
    if snow is not None and snow.option_key != "none" and snow.confidence >= 0.6 \
            and abs(leader(ctx).latitude) < 30 and r["centre"] < 1500:
        detail["contradiction"] = True
        summary += ("; snow in the photograph at that height in the tropics is "
                    "implausible")
    return Finding(technique=127, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.6, sources=[DEM_ATTRIBUTION])


@technique(id=128, key="elevation_profile", name="Terrain/elevation-profile matching",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="Relief across about 25 km around the leading candidate, "
                       "against mountains or flat ground in the photograph.")
def elevation_profile(ctx: Context) -> Finding:
    from ..geodata.weather import DEM_ATTRIBUTION
    sp = spec(128)
    r = _relief(ctx)
    if r is None:
        return Finding.not_applicable(sp, "no elevation data (offline, or switched off)")
    rd = reading(ctx, "terrain")
    detail = dict(r)
    summary = (f"Relief of {r['relief']:.0f} m across ~25 km around the leading "
               f"candidate ({r['min']:.0f}–{r['max']:.0f} m)")
    if rd is not None and rd.confidence >= 0.4:
        if rd.option_key == "mountains":
            ok = r["relief"] >= 400
        elif rd.option_key == "flat":
            ok = r["relief"] <= 300
        elif rd.option_key == "hills":
            ok = r["relief"] >= 60
        else:
            ok = None
        if ok is not None:
            detail["contradiction"] = not ok
            summary += (f"; the photograph shows {rd.option_label.lower()} — "
                        + ("consistent" if ok else "these disagree"))
    return Finding(technique=128, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.55, sources=[DEM_ATTRIBUTION, SRC_CLIP])


@technique(id=129, key="road_bearing", name="Road-bearing / azimuth matching",
           phase=PHASE, needs=("gps",),
           fusion_note="With the camera's position and compass heading, which "
                       "stored road it was facing along. Needs both.")
def road_bearing(ctx: Context) -> Finding:
    from ..chrono.capture_time import camera_heading
    sp = spec(129)
    heading, ref = camera_heading(ctx.raw_bytes)
    if heading is None:
        return Finding.not_applicable(sp, "the file records no compass heading")
    fix = ctx.exif.gps
    try:
        from ..geodata.service import get_service
        from ..geodata.store import bbox_around, decode_ring
        roads = get_service().store.query(bbox_around(fix.latitude, fix.longitude, 60),
                                          ("road",))["road"]
    except Exception:
        roads = []
    if not roads:
        return Finding.nothing_found(sp, "No road is stored within 60 m of the "
                                         "camera position.")
    best = None
    for _s, f in roads:
        path = decode_ring(f[4])
        for (x0, y0), (x1, y1) in zip(path, path[1:]):
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            dist = math.hypot((mx - fix.longitude) * 111320 * math.cos(math.radians(fix.latitude)),
                              (my - fix.latitude) * 111320)
            bearing = (math.degrees(math.atan2((x1 - x0) * math.cos(math.radians(fix.latitude)),
                                               y1 - y0)) + 360) % 180
            if best is None or dist < best[0]:
                best = (dist, bearing, f[2] or f[1])
    dist, bearing, name = best
    diff = min(abs((heading % 180) - bearing), 180 - abs((heading % 180) - bearing))
    along = diff < 25
    return Finding(technique=129, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.5,
                   summary=(f"The camera faced {heading:.0f}° ({ref}); the nearest "
                            f"stored road, {name}, {dist:.0f} m away, runs at "
                            f"{bearing:.0f}°/{bearing + 180:.0f}° — the camera was "
                            + ("looking along it" if along else
                               "looking across it" if diff > 65 else "at an angle to it")),
                   detail={"heading": heading, "road_bearing": round(bearing, 1),
                           "road": name, "distance_m": round(dist, 1)},
                   sources=[SRC_EXIF, SRC_OSM_LOCAL])


# ---------------------------------------------------------- 130-131

@technique(id=130, key="viewpoint", name="Camera-location / viewpoint matching",
           phase=PHASE,
           fusion_note="Needs reference imagery or 3D models of candidate "
                       "viewpoints that can be legally compared against.")
def viewpoint(ctx: Context) -> Finding:
    return Finding.not_applicable(spec(130), "no licensable reference imagery or 3D "
                                             "model of candidate viewpoints to compare with")


@technique(id=131, key="reprojection", name="Geometric re-projection against candidate viewpoints",
           phase=PHASE,
           fusion_note="Re-projecting the scene into candidate viewpoints needs "
                       "the same reference models as 130.")
def reprojection(ctx: Context) -> Finding:
    return Finding.not_applicable(spec(131), "re-projection needs 3D reference "
                                             "models of the candidate viewpoints")
