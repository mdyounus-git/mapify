"""Techniques 132-142: where the file came from, and what happened to it.

Two kinds of evidence live here.

**Other images.** A photograph is rarely analysed alone, and the case
store (casefile.py) remembers every earlier one: its estimate, a 64-bit
difference hash and the encoder's embedding -- never the image. So an
analysis can say that this is the same file as one seen last week (134),
a resized copy of it (134), a different photograph of what looks like
the same scene (133), or one of several photographs placed in the same
few kilometres (132). Nothing is searched outside this machine: reverse
image search (50) needs services whose terms do not allow it.

**The file's own history.** Messaging apps and phones name files in
recognisable ways ("IMG-20230514-WA0007.jpg" is WhatsApp on Android),
and some of those names carry a date (135, 136). The metadata can show
an edit -- a modification time after the capture time, an editor's name,
an XMP history, an embedded thumbnail that no longer matches the picture
(137). And the pixels carry compression and editing traces that phase 4
does not already read: the quantisation table (138), interpolation from
resizing (139), and regions copied from elsewhere in the frame (140).

Every forensic signal is reported with its innocent explanation and is
never a verdict: almost every shared photograph has been resized and
re-saved somewhere, and that is not tampering. Pixel-level AI detectors
are not used (141): they are unreliable, and a false "real" is worse
than no answer; what a file declares about AI is read instead.
"""

from __future__ import annotations

import io
import math
import re
import struct
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

from .base import Context, Finding, Region, technique
from .common import SRC_CLIP, SRC_EXIF, leader, spec

PHASE = 9
SRC_CASES = "this machine's own case store (earlier analyses; never the images)"
SRC_FORENSIC = "image forensics on the uploaded bytes (local)"
SRC_NAME = "the file name"


def _original(ctx: Context) -> Image.Image:
    from .phase4_vision import _original as orig
    return orig(ctx)


# ------------------------------------------------------------ the case store

def _index(ctx: Context) -> dict:
    if "case_index" not in ctx.memo:
        try:
            from .casefile import load_index
            ctx.memo["case_index"] = load_index()
        except Exception:
            ctx.memo["case_index"] = {}
    return ctx.memo["case_index"]


def _case_id(ctx: Context) -> str:
    from .casefile import _case_id as cid
    return cid(ctx.raw_bytes)


def _dhash(ctx: Context) -> str:
    if "dhash" not in ctx.memo:
        from .casefile import dhash
        ctx.memo["dhash"] = dhash(ctx.image)
    return ctx.memo["dhash"]


def _empty_store(sp) -> Finding:
    return Finding.nothing_found(sp, "No earlier analyses to compare with: the "
                                     "case store is empty or switched off.")


def _when(entry: dict) -> str:
    return (entry.get("at") or "")[:10] or "an earlier date"


# How far apart two estimates may be and still be "the same place" here.
_SAME_PLACE_KM = 25.0


@technique(id=132, key="multi_image", name="Multi-image geolocation correlation",
           phase=PHASE, needs=("candidates",),
           fusion_note="Earlier analyses placed near this one. Reported for "
                       "the reader to compare; it moves nothing, because the "
                       "earlier estimates are estimates too.")
def multi_image(ctx: Context) -> Finding:
    from ..geo.geodesy import haversine_km
    sp = spec(132)
    est = (ctx.prediction or {}).get("estimated") or {}
    if not (ctx.prediction or {}).get("determinate") or est.get("latitude") is None:
        return Finding.nothing_found(sp, "This photograph's location is not "
                                         "determined, so there is nothing to "
                                         "correlate with earlier ones.")
    index = _index(ctx)
    if not index:
        return _empty_store(sp)
    me = _case_id(ctx)
    lat, lon = est["latitude"], est["longitude"]
    near = []
    for cid, e in index.items():
        if cid == me or not e.get("determinate") or e.get("lat") is None:
            continue
        d = haversine_km(lat, lon, e["lat"], e["lon"])
        if d <= _SAME_PLACE_KM:
            near.append({"case_id": cid, "filename": e.get("filename"),
                         "distance_km": round(d, 1), "at": e.get("at"),
                         "confidence": e.get("confidence")})
    near.sort(key=lambda m: m["distance_km"])
    if not near:
        return Finding.nothing_found(sp, f"None of the {len(index)} earlier "
                                         f"analyses was placed within "
                                         f"{_SAME_PLACE_KM:.0f} km of this one.")
    shown = ", ".join(f"'{m['filename'] or m['case_id'][:8]}' ({m['distance_km']} km, "
                      f"{(m['at'] or '')[:10]})" for m in near[:4])
    return Finding(technique=132, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.5,
                   summary=(f"{len(near)} earlier photograph(s) were placed within "
                            f"{_SAME_PLACE_KM:.0f} km of this one: {shown}. Each is "
                            f"an estimate in its own right; agreement between "
                            f"them is not proof."),
                   detail={"nearby_cases": near[:20]}, sources=[SRC_CASES])


@technique(id=133, key="cross_source", name="Cross-source image matching",
           phase=PHASE, needs=("embedding",),
           fusion_note="Earlier photographs that look like the same scene but "
                       "are not copies of this file. Local only.")
def cross_source(ctx: Context) -> Finding:
    from .casefile import decode_embedding, hamming
    sp = spec(133)
    index = _index(ctx)
    if not index:
        return _empty_store(sp)
    me, mine = _case_id(ctx), _dhash(ctx)
    emb = np.asarray(ctx.embedding, dtype=np.float32)
    emb = emb / (np.linalg.norm(emb) + 1e-9)
    hits = []
    for cid, e in index.items():
        if cid == me:
            continue
        if e.get("dhash") and hamming(mine, e["dhash"]) <= 10:
            continue                            # a copy, which is 134's
        other = decode_embedding(e.get("embedding"))
        if other is None or other.shape != emb.shape:
            continue
        sim = float(emb @ (other / (np.linalg.norm(other) + 1e-9)))
        if sim >= 0.90:
            hits.append({"case_id": cid, "filename": e.get("filename"),
                         "similarity": round(sim, 3), "at": e.get("at"),
                         "lat": e.get("lat"), "lon": e.get("lon")})
    hits.sort(key=lambda h: -h["similarity"])
    if not hits:
        return Finding.nothing_found(sp, "No earlier photograph looks like the same "
                                         "scene.")
    top = hits[0]
    where = (f", placed at {top['lat']:.4f}, {top['lon']:.4f}"
             if top.get("lat") is not None else "")
    return Finding(technique=133, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.4,
                   summary=(f"Looks like the same scene as '{top['filename'] or top['case_id'][:8]}' "
                            f"(similarity {top['similarity']:.2f}, analysed "
                            f"{_when(top)}{where})"
                            + (f", and {len(hits) - 1} more" if len(hits) > 1 else "")
                            + ". Similar-looking scenes are not necessarily the "
                              "same place."),
                   detail={"matches": hits[:10]}, sources=[SRC_CASES, SRC_CLIP])


@technique(id=134, key="near_duplicate", name="Near-duplicate image detection",
           phase=PHASE, needs=("pixels",),
           fusion_note="The same file analysed before, or a resized or "
                       "recompressed copy of one, by 64-bit difference hash.")
def near_duplicate(ctx: Context) -> Finding:
    from .casefile import hamming
    sp = spec(134)
    index = _index(ctx)
    mine = _dhash(ctx)
    if not index:
        return _empty_store(sp)
    me = _case_id(ctx)
    if me in index:
        e = index[me]
        return Finding(technique=134, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=1.0,
                       summary=(f"This exact file was analysed before, on "
                                f"{_when(e)}" + (f", as '{e['filename']}'"
                                                  if e.get("filename") else "") + "."),
                       detail={"same_file": True, "case_id": me, "dhash": mine,
                               "previous": {k: e.get(k) for k in ("filename", "at",
                                                                  "lat", "lon",
                                                                  "confidence")}},
                       sources=[SRC_CASES])
    copies = []
    for cid, e in index.items():
        if e.get("dhash"):
            d = hamming(mine, e["dhash"])
            if d <= 6:
                copies.append({"case_id": cid, "filename": e.get("filename"),
                               "bits_different": d, "at": e.get("at")})
    copies.sort(key=lambda c: c["bits_different"])
    if not copies:
        return Finding(technique=134, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail={"dhash": mine},
                       summary=f"Not a copy of any of the {len(index)} earlier "
                               f"analyses.", sources=[SRC_CASES])
    c = copies[0]
    return Finding(technique=134, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.8,
                   summary=(f"A near-duplicate of '{c['filename'] or c['case_id'][:8]}', "
                            f"analysed {_when(c)} ({c['bits_different']} of 64 hash "
                            f"bits differ): the same picture resized, recompressed "
                            f"or lightly edited."),
                   detail={"dhash": mine, "copies": copies[:10]}, sources=[SRC_CASES])


# --------------------------------------------------------- the file's origin

# How apps and cameras name files. Order matters: the specific before the
# general. A name is easily changed, so a match is a hint about origin,
# never proof of it.
_ORIGINS = [
    (r"^WhatsApp Image (\d{4})-(\d{2})-(\d{2}) at (\d{1,2})\.(\d{2})\.(\d{2})",
     "WhatsApp (saved from desktop or web)", "messaging", "received"),
    (r"^IMG-(\d{4})(\d{2})(\d{2})-WA\d+", "WhatsApp on Android", "messaging", "received"),
    (r"^signal-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})", "Signal", "messaging",
     "received"),
    (r"^photo_(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})",
     "Telegram (saved from desktop)", "messaging", "received"),
    (r"^FB_IMG_(\d{13})", "Facebook (saved from the app)", "social", "saved"),
    (r"^received_\d+", "Facebook Messenger", "messaging", None),
    (r"^\d+_\d+_\d+_[no]\.(?:jpe?g|png|webp)$", "Facebook or Instagram (downloaded)",
     "social", None),
    (r"^Snapchat-\d+", "Snapchat", "social", None),
    (r"^Screenshot[_ ](\d{4})-?(\d{2})-?(\d{2})[-_ ](\d{2})-?(\d{2})-?(\d{2})",
     "a screenshot", "screenshot", "taken"),
    (r"^Screen ?[Ss]hot (\d{4})-(\d{2})-(\d{2}) at (\d{1,2})\.(\d{2})\.(\d{2})",
     "a screenshot (macOS)", "screenshot", "taken"),
    (r"^Screenshot", "a screenshot", "screenshot", None),
    (r"^PXL_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", "a Google Pixel camera file",
     "camera", "captured_utc"),
    (r"^IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", "an Android camera file",
     "camera", "captured"),
    (r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", "a Samsung camera file", "camera",
     "captured"),
    (r"^IMG_\d{4}\.(?:jpe?g|heic|png)$", "an iPhone or camera file", "camera", None),
    (r"^(?:DSC|DSCN|DSCF|_DSC|PANA|GOPR|DJI_|P\d{7})", "a camera or drone file",
     "camera", None),
    (r"^unnamed", "a Google download (Gmail, Drive or Photos)", "download", None),
    (r"^(?:image|images|download)(?:\s?\(\d+\))?\.\w+$",
     "a browser download or a pasted image", "download", None),
]
# Long sides platforms resize to. Common, not unique: a hint only.
_PLATFORM_SIDES = {1600: "WhatsApp", 2048: "Facebook or Twitter", 1080: "Instagram",
                   1280: "Telegram or Signal", 960: "Facebook (older)"}


def _name_match(filename: str | None):
    if not filename:
        return None
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    for pattern, label, kind, stamp in _ORIGINS:
        m = re.match(pattern, base, flags=re.IGNORECASE)
        if m:
            return base, m, label, kind, stamp
    return base, None, None, None, None


def _name_time(m, stamp: str | None):
    """The datetime a matched file name records, or None."""
    if m is None or stamp is None:
        return None
    g = m.groups()
    try:
        if stamp == "saved" and len(g) == 1:          # epoch milliseconds
            return datetime.fromtimestamp(int(g[0]) / 1000, tz=timezone.utc)
        parts = [int(x) for x in g[:6]] + [0] * (6 - len(g[:6]))
        when = datetime(*parts)
        return when.replace(tzinfo=timezone.utc) if stamp == "captured_utc" else when
    except (ValueError, OverflowError, OSError):
        return None


@technique(id=135, key="original_source", name="Original-source identification",
           phase=PHASE,
           fusion_note="Whether this looks like a camera original or a copy "
                       "passed through an app, from the file name, the "
                       "metadata and the size. Hints, each stated as such.")
def original_source(ctx: Context) -> Finding:
    sp = spec(135)
    exif = ctx.exif
    camera = " ".join(x for x in (getattr(exif, "camera_make", None),
                                  getattr(exif, "camera_model", None)) if x)
    orig = _original(ctx)
    side = max(orig.size)
    hints, verdict = [], None
    nm = _name_match(ctx.filename)
    if nm and nm[2]:
        hints.append(f"the name '{nm[0]}' is how {nm[2]} names files")
        verdict = nm[3]
    if camera:
        hints.append(f"the metadata names a camera ({camera})")
        verdict = verdict or "camera"
    elif not getattr(exif, "has_exif", False):
        hints.append("there is no metadata at all, which is what messaging apps "
                     "and social platforms leave after stripping it")
        verdict = verdict or "stripped"
    if side in _PLATFORM_SIDES:
        hints.append(f"the long side is exactly {side} px, a size {_PLATFORM_SIDES[side]} "
                     f"resizes to")
        if verdict in (None, "stripped"):
            verdict = "platform"
    if not hints:
        return Finding.nothing_found(sp, "Nothing in the name, metadata or size "
                                         "points to where the file came from.")
    headline = {
        "camera": "Probably a camera original, or close to one",
        "messaging": "Probably a copy passed through a messaging app; the original "
                     "is on the sender's device",
        "social": "Probably a copy saved from a social platform",
        "screenshot": "A screenshot: a picture of a screen, not of the place",
        "download": "Probably a downloaded copy",
        "stripped": "A copy with its metadata removed -- origin unknown",
        "platform": "Possibly resized by an app or platform",
    }.get(verdict, "Origin unclear")
    return Finding(technique=135, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.5,
                   summary=f"{headline}: " + "; ".join(hints) + ". Names and sizes "
                           "are easily changed, so these are hints, not proof.",
                   detail={"verdict": verdict, "hints": hints, "long_side": side,
                           "camera": camera or None,
                           "name_pattern": nm[2] if nm else None},
                   sources=[SRC_NAME, SRC_EXIF, SRC_FORENSIC])


def _exif_time(text: str | None):
    try:
        return datetime.strptime(str(text).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


@technique(id=136, key="upload_timestamp", name="Image-upload timestamp tracing where available",
           phase=PHASE,
           fusion_note="Dates written into file names by messaging apps, "
                       "screenshots and cameras: when the file was received, "
                       "saved or taken. Upload times held by platforms are "
                       "not available without their services.")
def upload_timestamp(ctx: Context) -> Finding:
    sp = spec(136)
    nm = _name_match(ctx.filename)
    if nm is None:
        return Finding.not_applicable(sp, "the file has no name to read")
    base, m, label, kind, stamp = nm
    when = _name_time(m, stamp)
    if when is None:
        return Finding.nothing_found(sp, f"The name '{base}' records no date. "
                                         f"Platform upload times are not available "
                                         f"without their services.")
    meaning = {"received": "received or saved", "saved": "saved",
               "taken": "taken (the screenshot)", "captured": "taken",
               "captured_utc": "taken"}[stamp]
    zone = "UTC" if when.tzinfo else "local time"
    shown = when.strftime("%Y-%m-%d %H:%M:%S")
    detail = {"name_time": when.isoformat(), "meaning": meaning, "source": label}
    summary = f"The name '{base}' records {shown} ({zone}): when the file was {meaning}"
    captured = _exif_time(getattr(ctx.exif, "captured_at", None))
    if captured is not None:
        detail["exif_capture"] = captured.isoformat()
        naive = when.replace(tzinfo=None)
        if stamp in ("received", "saved", "taken"):
            # A file cannot be taken after it was received.
            late = captured - naive
            detail["contradiction"] = late > timedelta(days=1)
            summary += (f"; the metadata says it was taken {captured:%Y-%m-%d %H:%M} — "
                        + ("after it was received, which cannot be right: one of "
                           "the two clocks or the name is wrong"
                           if detail["contradiction"] else "before that, consistent"))
        else:
            # Both are capture times; a time-zone's worth apart is normal.
            gap = abs(captured - naive)
            detail["contradiction"] = gap > timedelta(hours=14)
            summary += (f"; the metadata's capture time is {captured:%Y-%m-%d %H:%M} — "
                        + ("these disagree by more than any time-zone difference"
                           if detail["contradiction"] else "consistent"))
    else:
        summary += ". So the photograph existed by then" if stamp != "captured_utc" \
            and stamp != "captured" else ""
    return Finding(technique=136, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.5, summary=summary + ".", detail=detail,
                   sources=[SRC_NAME, SRC_EXIF])


# ---------------------------------------------------------------- edit history

_EDITORS = ("photoshop", "lightroom", "gimp", "snapseed", "picsart", "canva", "vsco",
            "affinity", "pixelmator", "paint.net", "luminar", "facetune", "meitu",
            "capture one", "darktable", "rawtherapee", "acdsee", "photoscape",
            "fotor", "lensa", "remini", "photos", "preview")


def _raw_ifds(raw: bytes) -> tuple[dict, dict]:
    try:
        exif = Image.open(io.BytesIO(raw)).getexif()
        return dict(exif), dict(exif.get_ifd(0x8769) or {})
    except Exception:
        return {}, {}


def _xmp(raw: bytes) -> str:
    i = raw.find(b"<x:xmpmeta")
    if i < 0:
        return ""
    j = raw.find(b"</x:xmpmeta>", i)
    return raw[i:j + 12].decode("utf-8", "replace") if j > i else ""


def _exif_thumbnail(raw: bytes) -> bytes | None:
    """The JPEG thumbnail held in EXIF IFD1, or None."""
    i = raw.find(b"Exif\x00\x00")
    if i < 0 or i > 1 << 17:
        return None
    tiff = raw[i + 6:]
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None:
        return None
    try:
        (ifd0,) = struct.unpack(order + "I", tiff[4:8])
        (n0,) = struct.unpack(order + "H", tiff[ifd0:ifd0 + 2])
        at = ifd0 + 2 + 12 * n0
        (ifd1,) = struct.unpack(order + "I", tiff[at:at + 4])
        if not ifd1:
            return None
        (n1,) = struct.unpack(order + "H", tiff[ifd1:ifd1 + 2])
        offset = length = None
        for k in range(n1):
            e = ifd1 + 2 + 12 * k
            tag, kind, _count = struct.unpack(order + "HHI", tiff[e:e + 8])
            value = (struct.unpack(order + "I", tiff[e + 8:e + 12])[0] if kind == 4
                     else struct.unpack(order + "H", tiff[e + 8:e + 10])[0])
            if tag == 0x0201:
                offset = value
            elif tag == 0x0202:
                length = value
        if offset and length and offset + length <= len(tiff):
            data = tiff[offset:offset + length]
            return data if data[:2] == b"\xff\xd8" else None
    except struct.error:
        return None
    return None


def _thumbnail_check(raw: bytes, image: Image.Image) -> dict | None:
    """Compare the embedded thumbnail with the picture it should show."""
    data = _exif_thumbnail(raw)
    if not data:
        return None
    try:
        thumb = Image.open(io.BytesIO(data)).convert("L")
        thumb.load()
    except Exception:
        return None
    t = np.asarray(thumb, dtype=np.float32)
    if t.std() < 3:
        return {"usable": False}                # blank thumbnails are written too
    tw, th = thumb.size
    iw, ih = image.size
    out = {"usable": True, "thumbnail_size": [tw, th]}
    ta, ia = tw / th, iw / ih
    if abs(ta - ia) / ia > 0.05 and abs(ta - 1 / ia) / ia > 0.05:
        out["aspect_differs"] = True            # cropped after the thumbnail
        return out
    main = image.convert("L").resize((64, 48), Image.Resampling.BILINEAR)
    small = thumb.resize((64, 48), Image.Resampling.BILINEAR)
    a, b = np.asarray(main, np.float32), np.asarray(small, np.float32)
    a, b = a - a.mean(), b - b.mean()
    out["difference"] = round(float(np.abs(a - b).mean()), 1)
    out["differs"] = out["difference"] > 14
    return out


@technique(id=137, key="edit_history", name="Image version / edit-history analysis",
           phase=PHASE, needs=("pixels",),
           fusion_note="Modification time against capture time, editing "
                       "software, XMP history, and whether the embedded "
                       "thumbnail still shows the same picture.")
def edit_history(ctx: Context) -> Finding:
    sp = spec(137)
    ifd0, exif_ifd = _raw_ifds(ctx.raw_bytes)
    signs, detail = [], {}
    taken = _exif_time(exif_ifd.get(0x9003))
    changed = _exif_time(ifd0.get(0x0132))
    if taken and changed:
        detail["captured"], detail["modified"] = taken.isoformat(), changed.isoformat()
        if changed - taken > timedelta(minutes=2):
            signs.append(f"the file was modified {changed:%Y-%m-%d %H:%M}, "
                         f"after it was taken ({taken:%Y-%m-%d %H:%M})")
    software = str(ifd0.get(0x0131) or getattr(ctx.exif, "software", "") or "").strip()
    xmp = _xmp(ctx.raw_bytes)
    tool = re.search(r'(?:xmp:CreatorTool="|<xmp:CreatorTool>)([^"<]+)', xmp)
    tools = [t for t in (software, tool.group(1) if tool else "") if t]
    detail["software"] = tools
    editors = sorted({e for t in tools for e in _EDITORS if e in t.lower()})
    if editors:
        signs.append("editing software is named: " + ", ".join(tools))
    actions = re.findall(r'stEvt:action="([^"]+)"|<stEvt:action>([^<]+)<', xmp)
    agents = re.findall(r'stEvt:softwareAgent="([^"]+)"|<stEvt:softwareAgent>([^<]+)<', xmp)
    history = [a or b for a, b in actions]
    if history:
        detail["xmp_history"] = history[:20]
        detail["xmp_agents"] = sorted({a or b for a, b in agents})[:10]
        signs.append(f"an XMP edit history of {len(history)} step(s) "
                     f"({', '.join(dict.fromkeys(history))})")
    if re.search(r"xmpMM:DerivedFrom|stRef:documentID", xmp):
        signs.append("XMP records that it was derived from another document")
    thumb = _thumbnail_check(ctx.raw_bytes, _original(ctx))
    if thumb:
        detail["thumbnail"] = thumb
        if thumb.get("aspect_differs"):
            signs.append("the embedded thumbnail has a different shape: the "
                         "picture was cropped after it was written")
        elif thumb.get("differs"):
            signs.append("the embedded thumbnail no longer matches the picture: "
                         "it was changed after the thumbnail was written")
    if not signs:
        return Finding(technique=137, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail=detail, confidence=0.0,
                       summary=("No sign of editing in the metadata"
                                + (", and the embedded thumbnail matches the picture"
                                   if thumb and thumb.get("usable") else "")
                                + ". Stripped or rewritten metadata would look the "
                                  "same, so this is not proof of an original."),
                       sources=[SRC_EXIF, SRC_FORENSIC])
    return Finding(technique=137, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, detail=detail, confidence=0.6,
                   summary=("Edited after capture: " + "; ".join(signs) + ". Editing "
                            "includes ordinary cropping, straightening and colour "
                            "work; it says the file is not the camera's original, "
                            "not that its content is false."),
                   sources=[SRC_EXIF, SRC_FORENSIC])


# ------------------------------------------------------------- 138, 139

def _signal(tid: int, sig) -> Finding:
    sp = spec(tid)
    summary = sig.summary
    if sig.flagged and sig.innocent:
        summary += f". Innocent explanation: {sig.innocent}."
    elif not summary.endswith("."):
        summary += "."
    return Finding(technique=tid, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=sig.flagged, summary=summary,
                   detail={**sig.detail, "innocent_explanation": sig.innocent},
                   confidence=0.35 if sig.flagged else 0.0,
                   regions=[Region(*b, label=sig.name) for b in sig.regions[:6]],
                   sources=[SRC_FORENSIC])


@technique(id=138, key="quantisation", name="JPEG quantization / compression-signature analysis",
           phase=PHASE, needs=("pixels",),
           fusion_note="The JPEG quantisation table: a camera's own encoder, "
                       "or libjpeg's standard table at a given quality -- the "
                       "signature of a software re-save when the file also "
                       "names a camera.")
def quantisation(ctx: Context) -> Finding:
    from ..forensics.tamper import quant_signature
    exif = ctx.exif
    camera = " ".join(x for x in (getattr(exif, "camera_make", None),
                                  getattr(exif, "camera_model", None)) if x) or None
    return _signal(138, quant_signature(_original(ctx), camera))


@technique(id=139, key="resampling", name="Resampling / scaling artifact detection",
           phase=PHASE, needs=("pixels",),
           fusion_note="Periodic interpolation traces that resizing or "
                       "rotating leaves in the pixels.")
def resampling(ctx: Context) -> Finding:
    from ..forensics.tamper import resampling as measure
    return _signal(139, measure(_original(ctx)))


# ---------------------------------------------------------------- 140

def copy_move(image: Image.Image, work: int = 512, block: int = 16, step: int = 4):
    """Regions of the frame that match block for block, or None.

    Block matching on small mean-pooled blocks (Fridrich et al., 2003): a
    patch cloned elsewhere in the frame gives many block pairs with one
    and the same offset. Flat blocks are skipped -- sky matches sky -- and
    an offset whose double also matches strongly is a repeating pattern
    (windows, tiles, brick), not a clone.
    """
    g = image.convert("L")
    g.thumbnail((work, work), Image.Resampling.BILINEAR)
    a = np.asarray(g, dtype=np.float32)
    h, w = a.shape
    if min(h, w) < 4 * block:
        return None
    win = np.lib.stride_tricks.sliding_window_view(a, (block, block))[::step, ::step]
    ny, nx = win.shape[:2]
    p = block // 4
    feats = win.reshape(ny, nx, 4, p, 4, p).mean(axis=(3, 5)).reshape(ny * nx, 16)
    textured = win.reshape(ny * nx, block * block).std(axis=1) >= 6.0
    idx = np.flatnonzero(textured)
    if len(idx) < 50:
        return None
    q = np.round(feats[idx] / 3.0).astype(np.int32)
    order = np.lexsort(q.T[::-1])
    ys, xs = (idx // nx) * step, (idx % nx) * step
    shifts: Counter = Counter()
    pairs: dict = {}
    for k in range(len(order) - 1):
        i = order[k]
        for j in order[k + 1:k + 4]:
            if np.abs(q[i] - q[j]).max() > 1:
                break
            dx, dy = int(xs[j] - xs[i]), int(ys[j] - ys[i])
            if math.hypot(dx, dy) < 2 * block:
                continue
            src, dst = (i, j)
            if dx < 0 or (dx == 0 and dy < 0):
                dx, dy, src, dst = -dx, -dy, j, i
            key = (dx, dy)
            shifts[key] += 1
            pairs.setdefault(key, []).append((src, dst))
    if not shifts:
        return None
    need = max(12, int(0.004 * len(idx)))
    ranked = shifts.most_common()
    if ranked[0][1] < need:
        return None
    # A clone gives one dominant offset (and its near neighbours, from
    # recompression noise); a repeating texture gives a lattice of them.
    # Count the distinct strong offsets, merging neighbours within two steps.
    strong: list[tuple[int, int]] = []
    for (sx, sy), n in ranked:
        if n < max(need, 0.5 * ranked[0][1]):
            break
        if all(abs(sx - tx) > 2 * step or abs(sy - ty) > 2 * step for tx, ty in strong):
            strong.append((sx, sy))
    if len(strong) > 2:
        return None                             # periodic: a pattern, not a clone
    (dx, dy), n = ranked[0]
    src = np.array([s for s, _d in pairs[(dx, dy)]])
    dst = np.array([d for _s, d in pairs[(dx, dy)]])

    def box(sel):
        x0, y0 = xs[sel].min(), ys[sel].min()
        x1, y1 = xs[sel].max() + block, ys[sel].max() + block
        return (float(x0) / w, float(y0) / h, float(x1) / w, float(y1) / h)

    sb, db = box(src), box(dst)
    if (sb[2] - sb[0]) * (sb[3] - sb[1]) > 0.12:
        return None                             # spread across the frame: texture
    return {"offset_px": [dx, dy], "pairs": int(n), "blocks": int(len(idx)),
            "region_a": [round(v, 4) for v in sb],
            "region_b": [round(v, 4) for v in db], "work_size": [w, h]}


@technique(id=140, key="copy_move", name="Copy-paste / splice detection",
           phase=PHASE, needs=("pixels",),
           fusion_note="Copy-move by block matching; splicing from another "
                       "picture shows as the noise inconsistency technique 54 "
                       "reads. Pointers for a closer look, never verdicts.")
def splice(ctx: Context) -> Finding:
    sp = spec(140)
    found = copy_move(_original(ctx))
    f54 = ctx.finding(54)
    noise = bool(f54 is not None and f54.found)
    detail = {"copy_move": found, "noise_inconsistency_54": noise}
    if not found:
        return Finding(technique=140, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=False, detail=detail, confidence=0.0,
                       summary=("No region matches another part of the frame block "
                                "for block" + ("; technique 54 did flag uneven noise, "
                                               "which splicing from another picture "
                                               "can cause" if noise else "")
                                + "."),
                       sources=[SRC_FORENSIC])
    a, b = found["region_a"], found["region_b"]
    return Finding(technique=140, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, detail=detail, confidence=0.35,
                   regions=[Region(*a, label="matching region A"),
                            Region(*b, label="matching region B")],
                   summary=(f"Two regions match block for block ({found['pairs']} block "
                            f"pairs, offset {found['offset_px'][0]}, "
                            f"{found['offset_px'][1]} px at the working size) — a "
                            f"pointer to copy-paste editing. Innocent explanation: "
                            f"some repeating textures and smooth gradients match like "
                            f"this; look at the two marked regions."),
                   sources=[SRC_FORENSIC])


# ---------------------------------------------------------------- 141

@technique(id=141, key="ai_region", name="AI-generated region detection",
           phase=PHASE, needs=("pixels",),
           fusion_note="What the file declares about generated content -- a "
                       "composite with AI material, generative-fill actions in "
                       "Content Credentials. No pixel detector: they are "
                       "unreliable, and a false 'real' is worse than none.")
def ai_region(ctx: Context) -> Finding:
    from ..forensics.provenance import read_provenance
    sp = spec(141)
    p = read_provenance(ctx.raw_bytes, ctx.exif)
    low = ctx.raw_bytes.lower()
    generative_fill = p.c2pa and any(k in low for k in (b"generative fill", b"generativefill",
                                                         b"firefly", b"c2pa.ai"))
    composite = (p.source_type or "").lower().endswith("compositewithtrainedalgorithmicmedia")
    pointers = []
    for tid in (54, 140):
        f = ctx.finding(tid)
        if f is not None and f.found and f.regions:
            pointers.append(tid)
    detail = {**p.as_dict(), "generative_fill_declared": bool(generative_fill),
              "composite_declared": composite, "regions_worth_a_look_from": pointers}
    if composite or generative_fill:
        return Finding(technique=141, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=0.8, detail=detail,
                       summary=("The file declares that part of it was generated: "
                                + ("a composite containing AI-generated material"
                                   if composite else "a generative-fill edit in its "
                                                     "Content Credentials")
                                + ". Which region is not recorded"
                                + (f"; techniques {', '.join(map(str, pointers))} mark "
                                   f"regions worth a close look" if pointers else "")
                                + "."),
                       sources=["the file's own metadata (C2PA / IPTC / XMP)"])
    if p.ai_declared:
        return Finding(technique=141, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                       found=True, confidence=0.9, detail=detail,
                       regions=[Region(0.0, 0.0, 1.0, 1.0, "declared AI-generated")],
                       summary="The file declares the whole image AI-generated "
                               "(technique 56); every region is.",
                       sources=["the file's own metadata (C2PA / IPTC / XMP / PNG)"])
    return Finding(technique=141, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=False, confidence=0.0, detail=detail,
                   summary=("No declaration of generated regions. Region-level AI "
                            "detection is not attempted -- pixel detectors are "
                            "unreliable -- so this proves nothing either way"
                            + (f"; techniques {', '.join(map(str, pointers))} mark "
                               f"regions whose statistics differ" if pointers else "")
                            + "."),
                   sources=["the file's own metadata (C2PA / IPTC / XMP / PNG)"])


# ---------------------------------------------------------------- 142

@technique(id=142, key="metadata_visual", name="Metadata–visual-evidence consistency check",
           phase=PHASE,
           fusion_note="Collects every check of what the metadata claims "
                       "against what the picture shows: place, time of day, "
                       "season, dated text, file name, editing.")
def metadata_visual(ctx: Context) -> Finding:
    sp = spec(142)
    checks = []

    def from_finding(tid: int, what: str):
        f = ctx.finding(tid)
        if f is not None and f.ran and "contradiction" in f.detail:
            checks.append({"check": what, "technique": tid,
                           "contradiction": bool(f.detail["contradiction"])})

    agreement = (ctx.prediction or {}).get("exif_vs_estimate")
    if agreement and agreement.get("verdict"):
        verdict = agreement["verdict"]
        checks.append({"check": f"the GPS tag against the picture ({verdict}, "
                                f"{agreement.get('distance_km', '?')} km apart)",
                       "technique": 2, "contradiction": verdict == "rejected"})
    from_finding(59, "the capture month against the vegetation")
    from_finding(60, "the capture date against dated text in the picture")
    from_finding(38, "the capture time against the light")
    from_finding(62, "the capture time against the sun at the candidates")
    from_finding(127, "snow in the picture against the place's altitude")
    from_finding(136, "the file name's date against the metadata's")
    f137, f138 = ctx.finding(137), ctx.finding(138)
    if f137 is not None and f137.ran and f137.found:
        checks.append({"check": "the metadata against an edit history (edited "
                                "after capture)", "technique": 137,
                       "contradiction": False, "note": "edited, not contradicted"})
    if f138 is not None and f138.ran and f138.found:
        # Many phones use libjpeg's table too, so this is a re-encode hint,
        # not a contradiction.
        checks.append({"check": "a named camera against a software encoder's "
                                "quantisation table", "technique": 138,
                       "contradiction": False,
                       "note": "possibly re-encoded by software"})
    if not checks:
        return Finding.nothing_found(sp, "No metadata claim could be checked against "
                                         "the picture (no GPS tag, capture time or "
                                         "camera to test).")
    bad = [c for c in checks if c["contradiction"]]
    summary = (f"{len(checks)} check(s) of the metadata against the picture: "
               + ("all consistent" if not bad else
                  f"{len(bad)} disagree — " + "; ".join(c["check"] for c in bad)))
    return Finding(technique=142, key=sp.key, name=sp.name, phase=PHASE, ran=True,
                   found=True, confidence=0.6,
                   summary=summary + ". A disagreement means one of the two is "
                                     "wrong, not which.",
                   detail={"checks": checks, "contradiction": bool(bad)},
                   sources=[SRC_EXIF, "the earlier techniques named in each check"])
