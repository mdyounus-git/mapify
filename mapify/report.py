"""Export an analysis as a document someone else can read.

JSON export already exists and is the right thing for a machine. It is the
wrong thing for the actual output of this tool, which is an argument: *here is
where I think this was taken, here is how sure I am, and here is why.* That
argument has to survive leaving the application.

So: a single self-contained HTML file. Everything inlined — the stylesheet,
the source image, the locator map — so it opens correctly on a machine with no
network, forever, with no dependency on Mapify still existing.

Two things this deliberately does not do.

It does not dress the output as an official record. No letterhead, no case
number, no CONFIDENTIAL banner. This is a tool output, and making it look like
a document of record would be the single most harmful thing it could do with a
result that might be wrong.

And it does not let the confidence quietly recede into a footnote. An
``insufficient`` result says so at the top, in the same weight that a ``high``
one states its location.
"""

from __future__ import annotations

import base64
import html
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

ROOT = Path(__file__).resolve().parent.parent
NE_110M = ROOT / "data" / "naturalearth" / "ne_110m_admin_0_countries"

DEG = math.pi / 180.0

# Colour by meaning, the same as the application, and legible on paper.
BAND_COLOUR = {
    "high": "#1f7a44",
    "medium": "#9a6b12",
    "low": "#a2542a",
    "insufficient": "#a3352c",
}
BAND_WORD = {
    "high": "High confidence",
    "medium": "Medium confidence",
    "low": "Low confidence",
    "insufficient": "Insufficient evidence",
}
# What the band was measured to mean. Stated in the document, because the word
# on its own invites the reader to supply their own definition.
BAND_MEASURED = {
    "high": (
        "Measured over a 163-sample benchmark: all 26 high-confidence calls "
        "landed within 47 km of the true location."
    ),
    "medium": (
        "Measured over a 163-sample benchmark: 88% of medium-confidence calls "
        "landed within 1500 km, median 16 km. A region to check, not a "
        "location."
    ),
    "low": (
        "Measured over a 163-sample benchmark: 63% within 1500 km, median "
        "728 km. A lead, nothing more."
    ),
    "insufficient": (
        "Measured over a 163-sample benchmark: only 31% within 1500 km. The "
        "evidence does not support naming a location."
    ),
}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _dms(value: float, pos: str, neg: str) -> str:
    hemi = pos if value >= 0 else neg
    a = abs(value)
    d = int(a)
    m = int((a - d) * 60)
    s = ((a - d) * 60 - m) * 60
    return f"{d}°{m:02d}′{s:04.1f}″{hemi}"


def _km(value: float | None) -> str:
    if value is None or not isinstance(value, (int, float)):
        return "—"
    if value < 1:
        return f"{round(value * 1000)} m"
    if value < 10:
        return f"{value:.1f} km"
    return f"{round(value):,} km"


# --------------------------------------------------------------------------
# locator map
# --------------------------------------------------------------------------

def locator_svg(lat: float, lon: float, size: int = 260) -> str:
    """An orthographic globe centred on the coordinate, as inline SVG.

    SVG rather than a rendered image: it is a few kilobytes, stays sharp when
    the document is printed, and needs no base64 round trip.
    """
    r = size / 2 - 6
    cx = cy = size / 2
    p0, l0 = lat * DEG, lon * DEG
    sin_p0, cos_p0 = math.sin(p0), math.cos(p0)

    def project(plon: float, plat: float):
        p, dl = plat * DEG, (plon - lon) * DEG
        cosc = sin_p0 * math.sin(p) + cos_p0 * math.cos(p) * math.cos(dl)
        if cosc < 0:
            return None                       # on the far side
        x = math.cos(p) * math.sin(dl)
        y = cos_p0 * math.sin(p) - sin_p0 * math.cos(p) * math.cos(dl)
        return cx + x * r, cy - y * r

    paths: list[str] = []
    try:
        import shapefile  # pyshp

        reader = shapefile.Reader(str(NE_110M))
        for shape in reader.iterShapes():
            pts = shape.points
            if not pts:
                continue
            parts = list(shape.parts) or [0]
            for i, start in enumerate(parts):
                end = parts[i + 1] if i + 1 < len(parts) else len(pts)
                ring = pts[start:end]
                if len(ring) < 3:
                    continue
                d: list[str] = []
                pen_down = False
                for plon, plat in ring:
                    pt = project(plon, plat)
                    if pt is None:
                        pen_down = False
                        continue
                    cmd = "L" if pen_down else "M"
                    d.append(f"{cmd}{pt[0]:.1f},{pt[1]:.1f}")
                    pen_down = True
                if len(d) > 2:
                    paths.append("".join(d))
        reader.close()
    except Exception:
        # No shapefile: an empty globe with a marker is still a locator.
        pass

    land = "".join(
        f'<path d="{p}" fill="#d9e2ec" stroke="#8fa3b8" stroke-width="0.5"/>'
        for p in paths
    )
    return (
        f'<svg class="locator" viewBox="0 0 {size} {size}" width="{size}" '
        f'height="{size}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Locator map">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#eef4fa" '
        f'stroke="#b6c6d6" stroke-width="1"/>'
        f"{land}"
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="#8fa3b8" stroke-width="1"/>'
        f'<circle cx="{cx}" cy="{cy}" r="10" fill="none" stroke="#c0392b" '
        f'stroke-width="1.5" opacity="0.8"/>'
        f'<circle cx="{cx}" cy="{cy}" r="3.5" fill="#c0392b"/>'
        f"</svg>"
    )


# --------------------------------------------------------------------------
# document shell
# --------------------------------------------------------------------------

CSS = """
:root { --ink:#16202b; --ink2:#4a5a6b; --ink3:#7b8b9c; --line:#dbe3ea;
        --bg:#ffffff; --panel:#f6f9fc; }
* { box-sizing:border-box; }
body { margin:0; padding:32px 28px 56px; background:var(--bg); color:var(--ink);
  font:14px/1.55 "Segoe UI",-apple-system,system-ui,sans-serif;
  max-width:980px; margin-inline:auto; }
h1 { font-size:20px; margin:0 0 2px; letter-spacing:.14em; font-weight:650; }
h2 { font-size:11px; letter-spacing:.12em; text-transform:uppercase;
  color:var(--ink3); margin:30px 0 10px; display:flex; align-items:center;
  gap:10px; font-weight:650; }
h2::after { content:""; flex:1; height:1px; background:var(--line); }
.sub { color:var(--ink3); font-size:11px; letter-spacing:.06em;
  text-transform:uppercase; margin:0 0 22px; }
header { border-bottom:2px solid var(--ink); padding-bottom:14px;
  margin-bottom:6px; }
.mono { font-family:"Cascadia Mono",Consolas,ui-monospace,monospace;
  font-variant-numeric:tabular-nums; }

.verdict { display:grid; grid-template-columns:1fr auto; gap:24px;
  padding:18px 20px; border:1px solid var(--line); border-left-width:5px;
  border-radius:8px; background:var(--panel); margin-bottom:6px; }
.place { font-size:26px; font-weight:650; line-height:1.2; margin:0 0 6px;
  letter-spacing:-.01em; }
.band { display:inline-block; font-size:12px; font-weight:650; padding:2px 9px;
  border-radius:99px; border:1.5px solid currentColor; }
.measured { margin:10px 0 0; font-size:12px; color:var(--ink2);
  border-left:3px solid var(--line); padding-left:10px; }
.reason { margin:12px 0 0; font-size:13px; color:var(--ink2); }
.locator { flex:none; }

table { border-collapse:collapse; width:100%; font-size:13px; }
th,td { text-align:left; padding:6px 10px 6px 0; vertical-align:top;
  border-bottom:1px solid var(--line); }
th { width:150px; color:var(--ink3); font-weight:500; font-size:12px; }
td.num { font-family:"Cascadia Mono",Consolas,monospace;
  font-variant-numeric:tabular-nums; }
tr:last-child th, tr:last-child td { border-bottom:0; }

.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:0 28px; }
.note { padding:10px 12px; margin:8px 0; border:1px solid var(--line);
  border-left-width:4px; border-radius:6px; background:var(--panel);
  font-size:12.5px; color:var(--ink2); }
.note strong { color:var(--ink); }
.note.warn { border-left-color:#c98a1e; }
.note.risk { border-left-color:#c0392b; }
.note.info { border-left-color:#2c6fae; }
.note.exif { border-left-color:#7a56c2; }

.source { max-width:100%; max-height:330px; border:1px solid var(--line);
  border-radius:6px; display:block; }
.ev { display:grid; grid-template-columns:1fr auto; gap:1px 12px;
  padding:7px 0; border-bottom:1px solid var(--line); }
.ev:last-child { border-bottom:0; }
.ev .d { grid-column:1/-1; font-size:11.5px; color:var(--ink3); }
.ev .v { font-family:"Cascadia Mono",Consolas,monospace; font-size:12px; }
.pos { color:#1f7a44; } .neg { color:#c0392b; }
pre.ocr { background:var(--panel); border:1px solid var(--line);
  border-radius:6px; padding:10px 12px; font-size:12px; white-space:pre-wrap;
  overflow-wrap:anywhere; margin:0; }
/* evidence map: the regions techniques marked, over the photograph */
.evframe { position:relative; display:inline-block; line-height:0; }
.evframe .source { max-height:420px; }
.evbox { position:absolute; border:1.5px solid var(--c,#2c6fae); border-radius:2px;
  box-shadow:0 0 0 1px rgba(255,255,255,.7); }
.evbox span { position:absolute; left:-1.5px; top:-1.5px; transform:translateY(-100%);
  padding:0 3px; font:600 9px/1.5 "Cascadia Mono",Consolas,monospace; color:#fff;
  background:var(--c,#2c6fae); border-radius:2px 2px 0 0; }
.legend { font-size:11px; color:var(--ink3); margin:6px 0 0; line-height:1.8; }
.legend i { display:inline-block; width:8px; height:8px; border-radius:2px;
  background:var(--c); margin:0 4px 0 10px; }
.ph-1 { --c:#7a56c2; } .ph-2 { --c:#2c6fae; } .ph-3 { --c:#b07a12; }
.ph-4 { --c:#1f7a44; } .ph-5 { --c:#c2622a; } .ph-6 { --c:#1d8fb3; }
.ph-7 { --c:#8a6fd1; } .ph-8 { --c:#b04aa6; } .ph-9 { --c:#c0392b; }
.ph-10 { --c:#16202b; }
/* techniques */
table.tq th { width:34px; font-family:"Cascadia Mono",Consolas,monospace; }
table.tq td.n { width:230px; color:var(--ink); }
table.tq td.s { color:var(--ink2); font-size:12px; }
table.tq tr.na td, table.tq tr.ran td.s { color:var(--ink3); }
.st { font-size:10px; letter-spacing:.06em; text-transform:uppercase; white-space:nowrap; }
.st.found { color:#1f7a44; } .st.ran { color:var(--ink3); } .st.na { color:var(--ink3); }
.st.failed { color:#c0392b; }
h3 { font-size:12px; margin:18px 0 4px; color:var(--ink2); font-weight:650; }
footer { margin-top:38px; padding-top:14px; border-top:1px solid var(--line);
  font-size:11px; color:var(--ink3); line-height:1.65; }
footer strong { color:var(--ink2); }
@media print {
  body { padding:0; max-width:none; }
  h2 { break-after:avoid; }
  .verdict, table, .note { break-inside:avoid; }
}
"""


def _shell(title: str, body: str, attribution: list[str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    credits = "".join(f"<div>{esc(a)}</div>" for a in attribution)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style></head><body>
<header>
  <h1>MAPIFY</h1>
  <p class="sub">Image geolocation reconnaissance &mdash; {esc(stamp)}
     &mdash; v{esc(__version__)}</p>
</header>
{body}
<footer>
  <p><strong>This is an estimate produced by a model, not a record of fact.</strong>
  Mapify infers a location from the content of a photograph. It is wrong
  sometimes, and the confidence band above states how often, measured. Read it
  before relying on this.</p>
  <p><strong>Attribution.</strong></p>
  {credits}
  <p>Place names, nearby features and time-zone boundaries derived from
  OpenStreetMap are licensed under the Open Database Licence (ODbL) 1.0 and
  must be attributed wherever this report is reproduced. Building a database
  from reports of this kind makes that database subject to ODbL.</p>
  <p>Generated locally by Mapify v{esc(__version__)}. The image was analysed on
  the machine that produced this document and was not uploaded anywhere.</p>
</footer>
</body></html>"""


# --------------------------------------------------------------------------
# single-image report
# --------------------------------------------------------------------------

def _rows(pairs: list[tuple[str, Any]], numeric: set[str] | None = None) -> str:
    numeric = numeric or set()
    out = []
    for label, value in pairs:
        if value in (None, "", "—"):
            continue
        cls = ' class="num"' if label in numeric else ""
        out.append(f"<tr><th>{esc(label)}</th><td{cls}>{esc(value)}</td></tr>")
    return f"<table>{''.join(out)}</table>" if out else ""


_PHASE_TITLES = {
    1: "Metadata & image forensics", 2: "Scene & infrastructure",
    3: "Sun, shadow & sky", 4: "Vision & geometry", 5: "History & time",
    6: "The pixels in depth", 7: "Places, sky & weather records",
    8: "Regional clues", 9: "Provenance & other images", 10: "Putting it together",
}
_STATE_WORD = {"found": "found", "ran": "nothing", "na": "not run", "failed": "failed"}


def _technique_state(t: dict[str, Any]) -> str:
    if t.get("skipped_because") == "the technique raised":
        return "failed"
    if not t.get("ran"):
        return "na"
    return "found" if t.get("found") else "ran"


def _evidence_regions(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The regions technique 151 collected, if it ran."""
    for t in report.get("techniques") or []:
        if t.get("technique") == 151:
            return [r for r in (t.get("detail") or {}).get("regions") or []
                    if len(r.get("box") or []) == 4]
    return []


def _techniques_section(report: dict[str, Any]) -> list[str]:
    """Every technique, found or not, and why the rest could not run.

    A report listing only the hits would read as though everything else
    was looked for and absent; most of it could not be attempted on this
    image, and the reason is printed beside each.
    """
    ts = [t for t in report.get("techniques") or [] if t.get("technique", 999) <= 153]
    if not ts:
        return []
    by = {t["technique"]: t for t in ts}
    ran = sum(1 for t in ts if t.get("ran"))
    found = sum(1 for t in ts if t.get("found"))
    out = [f"<h2>Techniques &mdash; {ran} of {len(ts)} ran, {found} found something</h2>"]
    if by.get(153, {}).get("summary"):
        out.append(f'<p class="reason">{esc(by[153]["summary"])}</p>')
    for tid, kind in ((145, "warn"), (144, "info"), (148, "info"), (149, "info"),
                      (150, "info")):
        t = by.get(tid)
        if t and t.get("found"):
            out.append(f'<div class="note {kind}">{esc(t.get("summary"))}</div>')
    for phase in sorted({t.get("phase", 0) for t in ts}):
        rows = []
        for t in sorted((t for t in ts if t.get("phase") == phase),
                        key=lambda t: t["technique"]):
            state = _technique_state(t)
            text = t.get("summary") or t.get("skipped_because") or ""
            rows.append(f'<tr class="{state}"><th>{t["technique"]}</th>'
                        f'<td class="n">{esc(t.get("name"))}</td>'
                        f'<td class="st {state}">{_STATE_WORD[state]}</td>'
                        f'<td class="s">{esc(text)}</td></tr>')
        out.append(f"<h3>{phase}. {esc(_PHASE_TITLES.get(phase, f'Phase {phase}'))}</h3>")
        out.append(f'<table class="tq">{"".join(rows)}</table>')
    return out


def build_single(report: dict[str, Any], image_bytes: bytes | None = None,
                 image_mime: str = "image/jpeg") -> str:
    """One analysis, as a standalone document."""
    pred = report.get("prediction") or {}
    est = pred.get("estimated") or {}
    place = est.get("place") or {}
    band = pred.get("confidence") or "insufficient"
    colour = BAND_COLOUR.get(band, "#555")
    determinate = bool(pred.get("determinate") and est)

    parts: list[str] = []

    # ---- the verdict, whichever way it went --------------------------------
    if determinate:
        name = ", ".join(
            x for x in (place.get("city"), place.get("region"),
                        place.get("country")) if x
        ) or place.get("display_name") or "Unnamed location"
        locator = locator_svg(est["latitude"], est["longitude"])
        headline = f'<p class="place">{esc(name)}</p>'
    else:
        name = "No location determined"
        locator = ""
        headline = f'<p class="place">{esc(name)}</p>'

    parts.append(f"""
<div class="verdict" style="border-left-color:{colour}">
  <div>
    {headline}
    <span class="band" style="color:{colour}">{esc(BAND_WORD.get(band, band))}</span>
    {f'<span style="margin-left:10px;color:var(--ink2)">&plusmn;{esc(_km(pred.get("uncertainty_km")))}</span>'
     if determinate else ''}
    <p class="reason">{esc(pred.get("confidence_reason") or "")}</p>
    <p class="measured">{esc(BAND_MEASURED.get(band, ""))}</p>
  </div>
  <div>{locator}</div>
</div>""")

    for note in pred.get("notes") or []:
        parts.append(f'<div class="note warn">{esc(note)}</div>')

    # ---- coordinates and place --------------------------------------------
    if determinate:
        tz = ((report.get("candidates") or [{}])[0] or {}).get("timezone") or {}
        tz_text = None
        if tz.get("iana_name"):
            off = tz.get("utc_offset_hours")
            tz_text = tz["iana_name"] + (
                f" (UTC{off:+g})" if isinstance(off, (int, float)) else ""
            )
        parts.append("<h2>Location</h2>")
        parts.append('<div class="grid2">')
        parts.append(_rows([
            ("Latitude", f"{est['latitude']:.6f}"),
            ("Longitude", f"{est['longitude']:.6f}"),
            ("DMS", f"{_dms(est['latitude'], 'N', 'S')}  "
                    f"{_dms(est['longitude'], 'E', 'W')}"),
            ("Uncertainty", f"±{_km(pred.get('uncertainty_km'))}"),
        ], numeric={"Latitude", "Longitude", "DMS", "Uncertainty"}))
        parts.append(_rows([
            ("Road", place.get("road")),
            ("Suburb", place.get("suburb")),
            ("City", place.get("city")),
            ("Region", place.get("region")),
            ("Country", place.get("country")),
            ("Postcode", place.get("postcode")),
            ("Timezone", tz_text),
            ("Named by", place.get("source")),
        ]))
        parts.append("</div>")
        if place.get("boundary_note"):
            parts.append(f'<div class="note warn">{esc(place["boundary_note"])}</div>')

    # ---- the image ---------------------------------------------------------
    img = report.get("image") or {}
    if image_bytes:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        regions = _evidence_regions(report)
        parts.append("<h2>Source image" + (" and evidence map" if regions else "")
                     + "</h2>")
        boxes = "".join(
            f'<div class="evbox ph-{int(r.get("phase", 0))}" title="{esc(r.get("technique"))} '
            f'{esc(r.get("name"))}: {esc(r.get("label"))}" style="left:{r["box"][0] * 100:.2f}%;'
            f'top:{r["box"][1] * 100:.2f}%;width:{(r["box"][2] - r["box"][0]) * 100:.2f}%;'
            f'height:{(r["box"][3] - r["box"][1]) * 100:.2f}%"><span>{esc(r.get("technique"))}'
            f'</span></div>' for r in regions)
        parts.append(
            f'<div class="evframe"><img class="source" alt="The photograph analysed" '
            f'src="data:{esc(image_mime)};base64,{encoded}">{boxes}</div>'
        )
        if regions:
            seen: dict[int, dict] = {}
            for r in regions:
                seen.setdefault(int(r.get("technique", 0)), r)
            parts.append('<p class="legend">Marked by technique:' + "".join(
                f'<span class="ph-{int(r.get("phase", 0))}"><i></i>{t} '
                f'{esc(r.get("name"))}</span>' for t, r in sorted(seen.items())) + "</p>")

    # ---- embedded GPS, held apart -----------------------------------------
    gps = pred.get("embedded_gps")
    if gps:
        agree = pred.get("exif_vs_estimate") or {}
        gplace = gps.get("place") or {}
        resolved = ", ".join(x for x in (
            (gplace.get("nearest_places") or [{}])[0].get("name"),
            (gplace.get("admin1") or {}).get("name"),
            (gplace.get("country") or {}).get("name"),
        ) if x)
        verdict = agree.get("verdict")
        headline = {
            "corroborated": "Embedded GPS &mdash; corroborated by the image",
            "rejected": "Embedded GPS &mdash; rejected",
            "unresolved": "Embedded GPS &mdash; unresolved",
        }.get(verdict, "Embedded GPS &mdash; a claim, not proof")
        lede = {
            "corroborated":
                "<strong>The file carries a GPS tag, and the picture agrees "
                "with it.</strong> Two independent things pointing at one "
                "place is the strongest result this tool produces. The tag "
                "is the more precise of the two and is the coordinate "
                "reported above.",
            "rejected":
                "<strong>The file carries a GPS tag and it was rejected.</strong> "
                "The picture points somewhere else, and the image evidence is "
                "strong enough to say which is wrong. A tag is the easiest "
                "part of a file to edit. It is reported here because it is a "
                "fact about the file even when it is not a fact about the "
                "place.",
            "unresolved":
                "<strong>The file carries a GPS tag that disagrees with the "
                "picture, and neither can be trusted.</strong> The image "
                "evidence is too weak to adjudicate. If the tag matters it "
                "needs corroborating from outside this tool.",
        }.get(
            verdict,
            "<strong>The file carries a GPS tag.</strong> That is what the "
            "writer of the file recorded, not what the picture shows, and it "
            "is trivially editable. It is reported here separately and was "
            "kept out of the estimate above.",
        )
        parts.append(f"<h2>{headline}</h2>")
        parts.append(f'<div class="note exif">{lede}</div>')
        parts.append(_rows([
            ("Latitude", f"{gps['latitude']:.6f}"),
            ("Longitude", f"{gps['longitude']:.6f}"),
            ("Altitude", f"{gps['altitude_m']} m" if gps.get("altitude_m") else None),
            ("GPS time", gps.get("timestamp_utc")),
            ("Method", gps.get("processing_method")),
            ("Resolves to", resolved),
        ], numeric={"Latitude", "Longitude", "GPS time"}))
        if agree:
            cls = "info" if agree.get("consistent") else "risk"
            word = "Consistent" if agree.get("consistent") else "Disagreement"
            parts.append(
                f'<div class="note {cls}"><strong>{word}</strong> &mdash; '
                f'{esc(_km(agree.get("distance_km")))} from the image estimate. '
                f'{esc(agree.get("note") or "")}</div>'
            )
        for w in gps.get("trust_warnings") or []:
            parts.append(f'<div class="note warn">{esc(w)}</div>')

    # ---- evidence ----------------------------------------------------------
    used = [r for r in (report.get("evidence") or {}).get("readings", [])
            if r.get("called")]
    if used:
        parts.append(f"<h2>Detected evidence &mdash; {len(used)} used</h2>")
        rows = "".join(
            f'<div class="ev"><span>{esc(r["label"])}</span>'
            f'<span class="v">{round(r["confidence"] * 100)}%</span>'
            f'<span class="d">{esc(r["title"])}</span></div>'
            for r in used
        )
        parts.append(rows)

    top = (report.get("candidates") or [{}])[0] or {}
    adjustments = top.get("adjustments") or []
    if adjustments:
        parts.append("<h2>How the leading candidate was scored</h2>")
        rows = "".join(
            f'<div class="ev"><span>{esc(a["source"].replace("_", " "))}</span>'
            f'<span class="v {"pos" if a["delta"] >= 0 else "neg"}">'
            f'{a["delta"]:+.2f}</span>'
            f'<span class="d">{esc(a["detail"])}</span></div>'
            for a in sorted(adjustments, key=lambda a: -abs(a["delta"]))[:10]
        )
        parts.append(rows)
        d = pred.get("diagnostics") or {}
        parts.append(_rows([
            ("Locator mass", f"{d.get('locator_mass', 0) * 100:.1f}%"),
            ("Concentration", f"{d.get('concentration', 0) * 100:.0f}%"),
            ("Dispersion", _km(d.get("dispersion_km"))),
            ("Evidence support", f"{d.get('evidence_support', 0):+.2f}"),
        ], numeric={"Locator mass", "Concentration", "Dispersion",
                    "Evidence support"}))

    # ---- text read off the image ------------------------------------------
    ocr = report.get("ocr") or {}
    if ocr.get("line_count"):
        parts.append(f"<h2>Text read from the image &mdash; {ocr['line_count']} lines</h2>")
        parts.append(f'<pre class="ocr">{esc(ocr.get("text") or "")}</pre>')
    for t in report.get("toponyms") or []:
        parts.append(
            f'<div class="note info">Sign text <strong>{esc(t["text"])}</strong> '
            f'matches <strong>{esc(t["matched_name"])}</strong> '
            f'({esc(t["matched_field"])})'
            + (f', {esc(_km(t["corroborated_km"]))} from the estimate'
               if t.get("corroborated_km") is not None else "")
            + ".</div>"
        )

    # ---- alternatives ------------------------------------------------------
    candidates = report.get("candidates") or []
    if len(candidates) > 1:
        parts.append("<h2>Alternative candidates</h2>")
        body = "".join(
            f"<tr><th>{i + 1}</th><td>"
            f"{esc(_candidate_name(c))}</td>"
            f'<td class="num">{c["latitude"]:.5f}, {c["longitude"]:.5f}</td>'
            f'<td class="num">{round((c.get("probability") or 0) * 100)}%</td>'
            f"</tr>"
            for i, c in enumerate(candidates)
        )
        parts.append(f"<table>{body}</table>")

    # ---- the 153 techniques -------------------------------------------------
    parts.extend(_techniques_section(report))

    # ---- file --------------------------------------------------------------
    exif = report.get("exif") or {}
    parts.append("<h2>File</h2>")
    parts.append(_rows([
        ("Name", img.get("filename")),
        ("Format", f"{img.get('format')} · {img.get('width')}×{img.get('height')}"),
        ("Size", f"{(img.get('bytes') or 0) / 1024:.0f} KB"),
        ("Camera", " ".join(x for x in (exif.get("camera_make"),
                                        exif.get("camera_model")) if x) or None),
        ("Lens", exif.get("lens")),
        ("Captured", exif.get("captured_at")),
        ("Software", exif.get("software")),
        ("EXIF", "present" if exif.get("has_exif") else "absent"),
        ("Analysis time", f"{report.get('elapsed_s')} s"),
    ], numeric={"Captured", "Analysis time"}))

    if not gps:
        for w in exif.get("trust_warnings") or []:
            parts.append(f'<div class="note warn">{esc(w)}</div>')
    for w in report.get("warnings") or []:
        parts.append(f'<div class="note warn">{esc(w)}</div>')

    title = f"Mapify — {name}"
    return _shell(title, "\n".join(parts), report.get("attribution") or [])


def _candidate_name(c: dict[str, Any]) -> str:
    loc = c.get("location") or {}
    nom = c.get("nominatim") or {}
    nearest = (loc.get("nearest_places") or [{}])[0]
    return (
        nom.get("city") or nearest.get("name") or nom.get("state")
        or (loc.get("admin1") or {}).get("name")
        or (loc.get("country") or {}).get("name") or "Unnamed"
    )


# --------------------------------------------------------------------------
# batch report
# --------------------------------------------------------------------------

def build_batch(summary: dict[str, Any],
                attribution: list[str] | None = None) -> str:
    """A whole folder, as one document."""
    clusters = summary.get("clusters") or []
    items = summary.get("items") or []
    parts: list[str] = []

    parts.append(f"""
<div class="verdict" style="border-left-color:#2c6fae">
  <div>
    <p class="place">{len(clusters)} distinct place{'' if len(clusters) == 1 else 's'}</p>
    <p class="reason">{summary.get('analysed', 0)} of {summary.get('total', 0)}
      images analysed{' before stopping' if summary.get('cancelled') else ''},
      in {summary.get('elapsed_s', 0)} s.</p>
    <p class="measured">Each place was geocoded once rather than each image:
      {summary.get('geocode_requests', 0)} request{'' if summary.get('geocode_requests') == 1 else 's'}
      for {summary.get('analysed', 0)} images.</p>
  </div>
  <div>{locator_svg(clusters[0]['latitude'], clusters[0]['longitude'])
        if clusters else ''}</div>
</div>""")

    if summary.get("cancelled"):
        parts.append(
            '<div class="note warn"><strong>This batch was stopped before it '
            "finished.</strong> The images below are the ones that completed; "
            "the rest were not analysed.</div>"
        )

    parts.append("<h2>Places found</h2>")
    rows = "".join(
        f"<tr><th>{i + 1}</th><td>{esc(c.get('place'))}</td>"
        f'<td class="num">{c["latitude"]:.5f}, {c["longitude"]:.5f}</td>'
        f'<td class="num">{c.get("size")} img</td>'
        f'<td class="num">&plusmn;{esc(_km(c.get("radius_km")))}</td></tr>'
        for i, c in enumerate(clusters)
    )
    parts.append(f"<table>{rows}</table>")

    parts.append("<h2>Every image</h2>")
    body = []
    for item in items:
        if item.get("state") == "done" and item.get("latitude") is not None:
            coord = f'{item["latitude"]:.5f}, {item["longitude"]:.5f}'
            band = item.get("confidence") or ""
            colour = BAND_COLOUR.get(band, "#555")
            state = f'<span style="color:{colour}">{esc(band)}</span>'
        else:
            coord = "—"
            state = esc(item.get("error") or item.get("state"))
        body.append(
            f"<tr><th>{esc(item.get('name'))}</th>"
            f"<td>{esc(item.get('place') or '')}</td>"
            f'<td class="num">{coord}</td><td>{state}</td></tr>'
        )
    parts.append(f"<table>{''.join(body)}</table>")

    counts = _rows([
        ("Analysed", summary.get("analysed")),
        ("Failed", summary.get("failed") or None),
        ("Skipped", summary.get("skipped") or None),
        ("Seconds per image", summary.get("seconds_per_image")),
        ("Geocoding requests", summary.get("geocode_requests")),
    ], numeric={"Analysed", "Failed", "Skipped", "Seconds per image",
                "Geocoding requests"})
    if counts:
        parts.append("<h2>Run</h2>")
        parts.append(counts)

    default_attr = [
        "Country, region and gazetteer data: Natural Earth (public domain)",
        "Geolocation model: GeoCLIP (MIT) on OpenAI CLIP ViT-L/14 (MIT)",
    ]
    return _shell(
        f"Mapify — batch of {summary.get('total', 0)} images",
        "\n".join(parts),
        attribution or default_attr,
    )
