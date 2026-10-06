"""Techniques 111-131: regional intelligence, mostly read out of the text.

The best evidence left after phase 3 is text: phone numbers, web
domains, prices, units, postcodes, brand names, a state code on a
number plate. It is read off the image rather than inferred from it,
which makes it independent of the image encoder -- the only kind of
evidence Phase 22 showed actually improves the answer. Each is checked
against the public tables in mapify/data/regional.py, and a weak match
("$", a trunk code another country writes the same way) is reported as
weak. Every text clue carries the rectangle it was read from, so the
evidence map (151) can point at it.

The matchers are deliberately strict. A word is not a currency ("TRY
OUR NEW MENU"), a price is not a phone number ("1.00 44"), a US phone
number is not a Japanese postcode ("555-1234"), and an abbreviation is
not a web address ("Dr.In"). A clue that is missed costs nothing; a clue
that is invented names a country the photograph never showed.

The terrain checks (127, 128) ask Open-Meteo's elevation service for
the Copernicus DEM around the leading candidate and compare its relief
with the landform in the photograph -- a coordinate list goes out,
never the image, and the weather-history switch turns it off. Road
bearing (129) needs the camera's position and compass heading, and uses
the local street store.

Viewpoint matching and re-projection against candidate viewpoints (130,
131) need 3D reference imagery this project cannot license; registered,
with the reason.

Only driving side (112) feeds the fusion, as it has since Phase 9. The
text clues are the strongest candidates to join it, and each will be
measured before it is allowed to move an answer.
"""

from __future__ import annotations

import math
import re

from ..data.countries import get_table
from ..data.regional import INDIA_PLATE_STATES
from ..evidence.text_clues import (brand_clue, currency_clue, domain_clue, phone_clue,
                                    postcode_clue, units_clue)
from .base import Context, Finding, technique
from .common import (ORDINARY_SCENES, SRC_CLIP, SRC_EXIF, SRC_OCR, SRC_OSM_LOCAL,
                     box_region, describe_reading, leader, reading, spec, zero_shot)

PHASE = 8
SRC_TABLE = "hand-maintained table of public regional facts"
SRC_PIX = "measured from the pixels"
# What the measurement of the text clues as fusion inputs found, stated
# beside each one so the next person does not have to rediscover it.
_MEASURED = (" Measured as a fusion input on 2026-10-06 (PHASE-27): none of the "
             "163 benchmark photographs carries this kind of text, so its weight "
             "could not be fitted and stays zero -- it reports, and does not steer.")


def _lines(ctx: Context, min_conf: float = 0.35):
    return [l for l in (getattr(ctx.ocr, "lines", None) or []) if l.confidence >= min_conf]


def _leader_inside(ctx: Context, countries) -> bool | None:
    iso = getattr(leader(ctx), "iso_a2", None) if ctx.candidates else None
    return (iso in set(countries)) if iso else None


def _names(isos) -> list[str]:
    table = get_table()
    out = []
    for iso in isos:
        c = table.get(iso)
        out.append(c.name if c else iso)
    return out


def _text_finding(tid: int, ctx: Context, what: str, countries: list[str],
                  matches: list[str], narrow: bool, regions=()) -> Finding:
    """One text clue, its countries, and whether the leader agrees."""
    sp = spec(tid)
    isos = list(dict.fromkeys(countries))       # the caller's order: majors first
    names = _names(isos)
    detail = {"matches": matches, "countries": sorted(isos), "narrow": narrow}
    if narrow:
        summary = f"{what}: {', '.join(matches[:4])} — points to {', '.join(names[:6])}"
    else:
        more = "…" if len(names) > 5 else ""
        summary = (f"{what}: {', '.join(matches[:4])} — shared by "
                   f"{', '.join(names[:5])}{more}, so weak")
    inside = _leader_inside(ctx, isos)
    if inside is not None:
        detail["leader_inside"] = inside
        # Only a narrow clue can contradict: "$" outside the US proves nothing.
        detail["contradiction"] = bool(narrow and not inside)
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
    regions = [r for r in (f11.regions if f11 else []) if text in r.label]
    return Finding(technique=111, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.55,
                   summary=(f"The plate \"{text}\" is registered in {state} ({code}). "
                            f"Vehicles travel, so this says where the car is from, "
                            f"not strictly where it is."),
                   detail={"plates": [{"text": t, "code": c, "region": s}
                                      for t, c, s in found]},
                   regions=regions,
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
    im = ctx.image
    w, h = im.size
    band = im.crop((0, int(h * 0.25), w, int(h * 0.75))).convert("RGB")
    band.thumbnail((200, 200))
    a = np.asarray(band, dtype=np.float32).reshape(-1, 3)
    sat = (a.max(1) - a.min(1)) / (a.max(1) + 1e-6)
    vivid = a[(sat > 0.35) & (a.max(1) > 60)]
    if len(vivid) < 0.05 * len(a):
        return Finding(technique=115, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=False, confidence=0.0,
                       summary="The built band of the frame is mostly muted: no "
                               "strong paint colours.", sources=[SRC_PIX])
    names: dict[str, int] = {}
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
                           "colours": top}, sources=[SRC_PIX])


_CULTURE = {
    "hindu_temple": ["a Hindu temple with a carved gopuram tower"],
    "islamic": ["a mosque or an Islamic-style building with pointed arches, "
                "domes or minarets"],
    "church": ["a church with a steeple"],
    "pagoda": ["a Buddhist pagoda or stupa"],
    "market": ["a street market with stalls and vendors"],
    "kolam": ["a kolam or rangoli pattern drawn on the ground"],
    "prayer_flags": ["colourful Tibetan prayer flags"],
    "none": ORDINARY_SCENES,
}
# Styles and objects, not identifications: the Gateway of India's arches
# and jali screens resemble a mosque without being one.
_CULTURE_LABELS = {
    "hindu_temple": "Hindu temple architecture (a carved gopuram tower)",
    "islamic": "Islamic-style architecture (pointed arches, domes or minarets)",
    "church": "a church with a steeple",
    "pagoda": "a Buddhist pagoda or stupa",
    "market": "a street market with stalls",
    "kolam": "a kolam or rangoli drawn on the ground",
    "prayer_flags": "Tibetan prayer flags",
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
    if best[0] == "none" or best[1] < 0.6:
        return Finding.nothing_found(sp, "No distinctive religious or commercial "
                                         "feature.")
    return Finding(technique=116, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=best[1],
                   summary=(f"The scene resembles {_CULTURE_LABELS[best[0]]} "
                            f"({best[1]:.0%}, zero-shot) — a resemblance, not an "
                            f"identification; reported only, and nothing is "
                            f"inferred about any person."),
                   detail={"presence": {k: round(v, 3) for k, v in p.items()}},
                   sources=[SRC_CLIP])


# ---------------------------------------------------------- 117, 118

def _ocr_lines(ctx: Context) -> list:
    return getattr(ctx.ocr, "lines", None) or []


def _regions(clue) -> list:
    """The evidence-map rectangles of a text clue's matches."""
    return [r for h in clue.hits for r in box_region(h.line, h.label)]


def _brand_finding(tid: int, ctx: Context, kinds: set[str], what: str) -> Finding:
    clue = brand_clue(_ocr_lines(ctx), kinds)
    if clue is None:
        return Finding.nothing_found(spec(tid), f"No {what} appear in the text.")
    return _text_finding(tid, ctx, what.capitalize(), clue.countries, clue.matches,
                         narrow=clue.narrow, regions=_regions(clue))


@technique(id=117, key="retail_chains", name="Retail-chain geographic distribution matching",
           phase=PHASE, needs=("ocr",),
           fusion_note="Retail chains named in the text, against where they "
                       "trade." + _MEASURED)
def retail_chains(ctx: Context) -> Finding:
    return _brand_finding(117, ctx, {"retail"}, "retail chains")


@technique(id=118, key="brands", name="Logo / brand regional-distribution matching",
           phase=PHASE, needs=("ocr",),
           fusion_note="Fuel and banking brands named in the text. Logos "
                       "themselves are not recognised: no logo dataset has a "
                       "licence this project can use." + _MEASURED)
def brands(ctx: Context) -> Finding:
    return _brand_finding(118, ctx, {"fuel", "bank"}, "fuel and banking brands")


# ---------------------------------------------------------- 119-124
#
# The matchers live in mapify/evidence/text_clues.py, shared with the
# fusion, so "a calling code" means the same thing in a report as in a
# score. These techniques turn what it reads into findings.

@technique(id=119, key="phone_numbers", name="Telephone-number / area-code extraction",
           phase=PHASE, needs=("ocr",),
           fusion_note="Country calling codes and, for India, mobile number "
                       "shapes and trunk codes. Independent of the encoder."
                       + _MEASURED)
def phone_numbers(ctx: Context) -> Finding:
    sp = spec(119)
    clue = phone_clue(_ocr_lines(ctx))
    if clue is None:
        return Finding.nothing_found(sp, "No telephone number in the text.")
    regions = _regions(clue)
    cities = clue.extra.get("cities", [])
    if clue.extra.get("weak_only"):
        return Finding(technique=119, key=sp.key, name=sp.name, phase=PHASE,
                       ran=True, found=True, confidence=0.15,
                       summary=(f"Numbers written like Indian trunk-dialled numbers "
                                f"({', '.join(clue.matches[:3])}); other countries "
                                f"write numbers the same way, so this is weak."),
                       detail={"matches": clue.matches, "cities": cities,
                               "narrow": False},
                       regions=regions, sources=[SRC_OCR, SRC_TABLE])
    f = _text_finding(119, ctx, "Telephone numbers", clue.countries, clue.matches,
                      narrow=clue.narrow, regions=regions)
    if cities and "IN" in clue.countries:
        f.detail["cities"] = cities
        f.summary += f" The trunk code suggests {', '.join(cities)}."
    return f


@technique(id=120, key="domains", name="Domain-name / URL geographic clues",
           phase=PHASE, needs=("ocr",),
           fusion_note="Country-code top-level domains in web and email "
                       "addresses. Generic and resold codes (.com, .io, .co, "
                       ".tv, .me) are ignored." + _MEASURED)
def domains(ctx: Context) -> Finding:
    sp = spec(120)
    clue = domain_clue(_ocr_lines(ctx))
    if clue is None:
        return Finding.nothing_found(sp, "No country-code web or email address in "
                                         "the text.")
    return _text_finding(120, ctx, "Country-code domains", clue.countries,
                         clue.matches, narrow=clue.narrow, regions=_regions(clue))


@technique(id=121, key="currency", name="Currency / price-format identification",
           phase=PHASE, needs=("ocr",),
           fusion_note="Currency signs and codes in prices. Codes count only "
                       "beside a number; \"$\" alone is dozens of countries "
                       "and is reported as weak." + _MEASURED)
def currency(ctx: Context) -> Finding:
    sp = spec(121)
    clue = currency_clue(_ocr_lines(ctx))
    if clue is None:
        return Finding.nothing_found(sp, "No currency in the text.")
    return _text_finding(121, ctx, "Currency", clue.countries, clue.matches,
                         narrow=clue.narrow, regions=_regions(clue))


# Same separator twice: "25/12/2023", "2023-12-25". Not "1.2.3" (a
# version) or "192.168.1.1" (an address): the parts are checked.
_DATE = re.compile(r"(?<![\d.])(\d{1,4})([./-])(\d{1,2})\2(\d{1,4})(?![\d.])")


@technique(id=122, key="date_format", name="Date-format identification",
           phase=PHASE, needs=("ocr",),
           fusion_note="Day-first, month-first or year-first dates. Weak: most "
                       "of the world writes day-first.")
def date_format(ctx: Context) -> Finding:
    sp = spec(122)
    styles, regions = [], []
    for line in _lines(ctx):
        for a, _sep, b, c in _DATE.findall(line.text):
            ai, bi, ci = int(a), int(b), int(c)
            style = None
            if len(a) == 4 and 1900 <= ai <= 2099 and 1 <= bi <= 12 \
                    and len(c) <= 2 and 1 <= ci <= 31:
                style = "year-first (YYYY-MM-DD)"
            elif len(a) <= 2 and len(c) in (2, 4):
                if 13 <= ai <= 31 and 1 <= bi <= 12:
                    style = "day-first (DD/MM)"
                elif 1 <= ai <= 12 and 13 <= bi <= 31:
                    style = "month-first (MM/DD)"
            if style:
                styles.append(style)
                regions += box_region(line, style.split(" ")[0])
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
                   detail={"styles": styles}, regions=regions, sources=[SRC_OCR])


@technique(id=123, key="units", name="Measurement-unit identification",
           phase=PHASE, needs=("ocr",),
           fusion_note="Miles and degrees Fahrenheit narrow the world sharply; "
                       "kilometres and Celsius hardly at all." + _MEASURED)
def units(ctx: Context) -> Finding:
    sp = spec(123)
    clue = units_clue(_ocr_lines(ctx))
    if clue is None:
        return Finding.nothing_found(sp, "No measurement unit beside a number in "
                                         "the text.")
    if clue.extra.get("metric"):
        return Finding(technique=123, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=0.1,
                       summary=f"Metric units ({', '.join(clue.matches)}): used almost "
                               f"everywhere except the few mile countries, so this "
                               f"narrows little.",
                       detail={"units": clue.matches}, regions=_regions(clue),
                       sources=[SRC_OCR])
    return _text_finding(123, ctx, "Imperial units", clue.countries, clue.matches,
                         narrow=clue.narrow, regions=_regions(clue))


@technique(id=124, key="postcodes", name="Postal-code extraction",
           phase=PHASE, needs=("ocr",),
           fusion_note="Postcode shapes distinctive enough to name a country, "
                       "and only in the context they are written in. Bare "
                       "four-, five- and six-digit numbers are skipped." + _MEASURED)
def postcodes(ctx: Context) -> Finding:
    sp = spec(124)
    clue = postcode_clue(_ocr_lines(ctx))
    if clue is None:
        return Finding.nothing_found(sp, "No distinctive postcode in the text.")
    return _text_finding(124, ctx, "Postcodes", clue.countries, clue.matches,
                         narrow=clue.narrow, regions=_regions(clue))


# ---------------------------------------------------------- 125-126

# Stored kinds that are places or things rather than businesses -- OSM
# keys ("historic=") and OpenMapTiles classes ("park=") alike.
_NOT_BUSINESS = ("place=", "historic=", "man_made=", "leisure=", "railway=",
                 "bus=", "park=", "attraction=", "castle=", "cemetery=",
                 "harbor=", "golf=", "stadium=", "aerialway=", "entrance=",
                 "campsite=", "swimming=", "monument=", "place_of_worship=")


def _names_near(lat: float, lon: float, radius: float):
    try:
        from ..geodata.service import get_service
        from ..geodata.store import bbox_around
        held = get_service().store.query(bbox_around(lat, lon, radius), ("poi",))["poi"]
        return [(f[2], f[1]) for _s, f in held if f[2]]
    except Exception:
        return []


_GENERIC_WORDS = {"road", "street", "store", "shop", "hotel", "bank", "centre",
                  "center", "school", "hospital", "market", "station", "temple",
                  "church", "mosque", "college", "office", "nagar", "main",
                  "cross", "layout", "restaurant", "cafe", "metro", "plaza",
                  "mall", "tower", "park", "city", "india", "bazaar", "corner",
                  "house", "point", "express", "super", "royal", "golden",
                  "international", "national", "public", "general", "services"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in _GENERIC_WORDS}


def _match_names(ctx: Context, kinds_ok) -> list[dict]:
    """Stored names near each candidate that the text really contains.

    A one-word name must be at least six letters and appear as written
    ("Metro" on a station sign is not the shoe shop called Metro); a
    longer name needs two of its distinctive words in the text.
    """
    texts = [l.text.lower() for l in _lines(ctx, 0.45)]
    words: set[str] = set()
    for t in texts:
        words |= _tokens(t)
    if not words:
        return []
    joined = "\n".join(texts)
    out, seen = [], set()
    for i, c in enumerate(ctx.candidates[:5]):
        for name, kind in _names_near(c.latitude, c.longitude, 1500):
            if not kinds_ok(kind) or (i, name) in seen:
                continue
            t = _tokens(name)
            if not t:
                continue
            if len(t) == 1:
                (tok,) = t
                ok = len(tok) >= 6 and re.search(
                    r"(?<![a-z])" + re.escape(name.lower()) + r"(?![a-z])", joined)
            else:
                ok = len(t & words) >= 2
            if ok:
                seen.add((i, name))
                out.append({"rank": i + 1, "name": name, "kind": kind})
    return out


@technique(id=125, key="business_directory", name="Business-directory cross-reference",
           phase=PHASE, needs=("ocr", "candidates"),
           fusion_note="Business names in the text against businesses stored "
                       "near each candidate. The address reader (8) already "
                       "asks Nominatim; this uses the local store.")
def business_directory(ctx: Context) -> Finding:
    sp = spec(125)
    hits = _match_names(ctx, lambda k: not (k or "").startswith(_NOT_BUSINESS))
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
           fusion_note="Any named feature stored near a candidate whose name "
                       "the text contains -- landmarks, stations, institutions.")
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
    """Copernicus DEM on a 5x5 grid ~25 km across the leading candidate."""
    if "relief" in ctx.memo:
        return ctx.memo["relief"]
    from .phase5_temporal import _weather
    svc = _weather(ctx)
    top = leader(ctx)
    out = None
    if svc is not None and top is not None:
        step_lon = 0.06 / max(0.2, math.cos(math.radians(top.latitude)))
        pts = [(top.latitude + dy * 0.06, top.longitude + dx * step_lon)
               for dy in (-2, -1, 0, 1, 2) for dx in (-2, -1, 0, 1, 2)]
        el = svc.elevations(pts)
        good = sorted(e for e in (el or []) if e == e)
        if good:
            centre = el[12] if el[12] == el[12] else good[len(good) // 2]
            out = {"centre": centre, "min": good[0], "max": good[-1],
                   "relief": good[-1] - good[0]}
    ctx.memo["relief"] = out
    return out


@technique(id=127, key="dem", name="Digital-elevation-model (DEM) matching",
           phase=PHASE, needs=("evidence", "candidates"),
           fusion_note="Elevation of the leading candidate from the Copernicus "
                       "DEM, against what the photograph shows. Only "
                       "coordinates are sent.")
def dem(ctx: Context) -> Finding:
    from ..geodata.weather import DEM_ATTRIBUTION
    sp = spec(127)
    r = _relief(ctx)
    if r is None:
        return Finding.not_applicable(sp, "no elevation data (offline, or switched off)")
    snow = reading(ctx, "snow_cover")
    detail = {"elevation_m": round(r["centre"], 1)}
    summary = f"The leading candidate stands {r['centre']:.0f} m above sea level"
    # Lying snow in the tropics needs real altitude; below ~3500 m it is
    # implausible (the snow line there is far higher).
    if snow is not None and snow.option_key == "snow" and snow.confidence >= 0.6 \
            and abs(leader(ctx).latitude) < 23.5 and r["centre"] < 3500:
        detail["contradiction"] = True
        summary += ("; lying snow in the photograph at that height in the "
                    "tropics is implausible")
    return Finding(technique=127, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.6, sources=[DEM_ATTRIBUTION])


# What relief each landform needs across ~25 km. Generous on purpose: a
# reanalysis grid and a photograph disagree only when they really do.
_RELIEF_OK = {"mountains": lambda m: m >= 400, "flat": lambda m: m <= 300,
              "hills": lambda m: m >= 60, "valley": lambda m: m >= 150}


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
    detail = {k: round(v, 1) for k, v in r.items()}
    summary = (f"Relief of {r['relief']:.0f} m across ~25 km around the leading "
               f"candidate ({r['min']:.0f}–{r['max']:.0f} m)")
    check = _RELIEF_OK.get(rd.option_key) if rd is not None and rd.confidence >= 0.4 else None
    if check is not None:
        ok = check(r["relief"])
        detail["terrain"] = rd.option_key
        detail["contradiction"] = not ok
        summary += (f"; the photograph shows {rd.option_label.lower()} — "
                    + ("consistent" if ok else "these disagree"))
    return Finding(technique=128, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, summary=summary + ".", detail=detail,
                   confidence=0.55, sources=[DEM_ATTRIBUTION, SRC_CLIP])


def _segment_distance(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


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
    # Local metres around the camera.
    kx = 111_320.0 * math.cos(math.radians(fix.latitude))
    ky = 111_320.0
    best = None
    for _s, f in roads:
        path = [((lon - fix.longitude) * kx, (lat - fix.latitude) * ky)
                for lon, lat in decode_ring(f[4])]
        for (x0, y0), (x1, y1) in zip(path, path[1:]):
            dist = _segment_distance(0.0, 0.0, x0, y0, x1, y1)
            if best is None or dist < best[0]:
                bearing = (math.degrees(math.atan2(x1 - x0, y1 - y0)) + 360) % 180
                best = (dist, bearing, f[2] or f[1])
    dist, bearing, name = best
    diff = abs((heading % 180) - bearing)
    diff = min(diff, 180 - diff)
    relation = ("looking along it" if diff < 25 else
                "looking across it" if diff > 65 else "at an angle to it")
    return Finding(technique=129, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.5,
                   summary=(f"The camera faced {heading:.0f}° ({ref}); the nearest "
                            f"stored road, {name}, {dist:.0f} m away, runs at "
                            f"{bearing:.0f}°/{bearing + 180:.0f}° — the camera was "
                            f"{relation}."),
                   detail={"heading": round(heading, 1), "heading_ref": ref,
                           "road_bearing": round(bearing, 1), "road": name,
                           "distance_m": round(dist, 1), "relation": relation},
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
