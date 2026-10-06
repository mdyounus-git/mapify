#!/usr/bin/env python3
"""Build a text-bearing benchmark: geotagged photographs that carry a clue.

The 163-photograph benchmark could not measure whether text clues should
steer the answer (PHASE-27): it is landmarks and scenes, and not one of
them shows a calling code, a country-code web address, a currency sign,
a postcode or miles. This builds the set that can.

**The selection rule, which is the whole point.** A photograph is kept if
and only if local OCR reads a *narrow* text clue in it
(evidence/text_clues.py). Nothing in the selection looks at where the
photograph was taken, so clues that point to the wrong country -- a
foreign brand's sign, a tourist's price list -- are kept as readily as
the right ones, and the measurement sees both. Selecting *for* a clue is
the right condition rather than a bias: the weight being measured acts
only when a clue is present, and the question is what it does then.

Candidates come from Wikimedia Commons searches for photographs likely to
show such text (shopfronts, estate agents' boards, price lists, speed
signs), restricted to files with a camera location and a free licence.
No more than three are kept per photographer and per one-degree cell, so
one street or one uploader cannot become the benchmark.

Files go to ``samples/text/`` (git-ignored, like ``samples/``) with a
manifest recording each one's title, licence, author, source page, true
coordinate and the clue that qualified it. Reduced-size copies (1600 px)
are fetched, at a polite pace, with the project's User-Agent; resumable.

    python scripts/fetch_text_samples.py [--target 70]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_samples import api_get, download, usable   # noqa: E402

OUT = ROOT / "samples" / "text"
MANIFEST = OUT / "manifest.json"
SEEN = OUT / "seen.json"

# Where readable clue text is likely: Commons categories built around it
# (expanded one level into subcategories), then a few description
# searches. Every candidate still needs a camera location and a free
# licence, and the OCR still decides.
SOURCES = [
    ("category", "Telephone numbers on signs"),       # 17 country subcategories
    ("category", "Price signs by country"),
    ("category", "Price boards"),
    ("category", "Pricelists"),
    ("category", "Fuel price signs"),
    ("category", "Price tags"),
    ("category", "Mobile phone shops in India"),
    ("category", "Real estate agents - shop display"),
    ("search", '"phone number" hastemplate:Location'),
    ("search", '"price list" hastemplate:Location'),
    ("search", '"to let" sign hastemplate:Location'),
    ("search", 'shop sign India hastemplate:Location'),
]
PER_CELL = 3
PER_ARTIST = 3
PER_SOURCE = 12                 # no one category becomes the benchmark


def subcategories(category: str) -> list[str]:
    payload = api_get({"action": "query", "list": "categorymembers",
                       "cmtitle": f"Category:{category}", "cmtype": "subcat",
                       "cmlimit": "100"})
    return [m["title"].removeprefix("Category:")
            for m in (payload.get("query") or {}).get("categorymembers") or []]


def category_pages(category: str, limit: int) -> list[dict]:
    """File pages in a category, with imageinfo and coordinates."""
    pages: list[dict] = []
    cont: dict[str, str] = {}
    while len(pages) < limit:
        payload = api_get({"action": "query", "generator": "categorymembers",
                           "gcmtitle": f"Category:{category}", "gcmtype": "file",
                           "gcmlimit": "50", "prop": "imageinfo|coordinates",
                           "iiprop": "url|extmetadata|size|mime", "iiurlwidth": "1600",
                           **cont})
        pages += list(((payload.get("query") or {}).get("pages") or {}).values())
        cont = payload.get("continue") or {}
        if not cont:
            break
        cont = {k: v for k, v in cont.items() if k != "continue"}
        time.sleep(1.0)
    return pages[:limit]


def search_titles(query: str, limit: int) -> list[str]:
    titles: list[str] = []
    offset = 0
    while len(titles) < limit:
        payload = api_get({"action": "query", "list": "search", "srnamespace": "6",
                           "srsearch": query, "srlimit": "50",
                           "sroffset": str(offset)})
        hits = (payload.get("query") or {}).get("search") or []
        titles += [h["title"] for h in hits]
        if len(hits) < 50:
            break
        offset += 50
        time.sleep(1.0)
    return titles[:limit]


def page_info(titles: list[str]) -> list[dict]:
    """imageinfo and the camera coordinate for up to 50 titles."""
    payload = api_get({"action": "query", "titles": "|".join(titles),
                       "prop": "imageinfo|coordinates",
                       "iiprop": "url|extmetadata|size|mime", "iiurlwidth": "1600"})
    return list(((payload.get("query") or {}).get("pages") or {}).values())


def _clean(meta: dict, key: str) -> str:
    raw = meta.get(key, {}).get("value", "") or ""
    out, depth = [], 0
    for ch in raw:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return " ".join("".join(out).split())[:180]


def _slug(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower().removeprefix("file:"))
    base = re.sub(r"-(jpe?g|png|webp)$", "", base).strip("-")[:48] or "photo"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):     # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=int, default=70, help="photographs to keep")
    ap.add_argument("--per-source", type=int, default=60,
                    help="candidates to look at per category or search")
    args = ap.parse_args()

    import logging
    logging.basicConfig(level=logging.ERROR)
    from PIL import Image, ImageOps
    from mapify.config import load_config
    from mapify.evidence.text_clues import read_clues
    from mapify.pipeline import AnalysisEngine

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = (json.loads(MANIFEST.read_text(encoding="utf-8"))
                if MANIFEST.is_file() else {"samples": []})
    samples = [s for s in manifest["samples"] if (OUT / s["file"]).is_file()]
    seen = set(json.loads(SEEN.read_text(encoding="utf-8"))) if SEEN.is_file() else set()
    cells: dict[tuple[int, int], int] = {}
    artists: dict[str, int] = {}
    per_source: dict[str, int] = {}
    for s in samples:
        k = (int(s["true_lat"] // 1), int(s["true_lon"] // 1))
        cells[k] = cells.get(k, 0) + 1
        artists[s["artist"]] = artists.get(s["artist"], 0) + 1
        per_source[s["query"]] = per_source.get(s["query"], 0) + 1
    print(f"{len(samples)} kept so far, {len(seen)} candidates already looked at")

    cfg = load_config()
    cfg.models.ocr_enabled = True
    ocr = AnalysisEngine(cfg).ocr

    def save() -> None:
        manifest["samples"] = samples
        manifest["selection"] = ("kept if and only if local OCR read a narrow text "
                                 "clue; capped at 3 per photographer, 3 per "
                                 "one-degree cell and 12 per source")
        MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False),
                            encoding="utf-8")
        SEEN.write_text(json.dumps(sorted(seen)), encoding="utf-8")

    def consider(page: dict, source: str) -> None:
        title = page.get("title", "")
        if title in seen:
            return
        seen.add(title)
        coords = (page.get("coordinates") or [None])[0]
        ok, licence = usable(page)
        if not ok or not coords:
            return
        info = page["imageinfo"][0]
        meta = info.get("extmetadata") or {}
        artist = _clean(meta, "Artist") or "unknown"
        lat, lon = float(coords["lat"]), float(coords["lon"])
        cell = (int(lat // 1), int(lon // 1))
        if cells.get(cell, 0) >= PER_CELL or artists.get(artist, 0) >= PER_ARTIST:
            return
        slug = _slug(title, {s["site"] for s in samples})
        dest = OUT / f"{slug}.jpg"
        try:
            size = download(info.get("thumburl") or info["url"], dest)
        except Exception as exc:
            print(f"  download failed: {type(exc).__name__}")
            return
        time.sleep(1.0)
        try:
            img = ImageOps.exif_transpose(Image.open(dest)).convert("RGB")
            img.thumbnail((1600, 1600))
            lines = ocr.read(img).lines
        except Exception as exc:
            print(f"  unreadable {slug}: {type(exc).__name__}")
            dest.unlink(missing_ok=True)
            return
        narrow = [c for c in read_clues(lines) if c.narrow]
        if not narrow:
            dest.unlink(missing_ok=True)       # keep nothing that is not used
            return
        cells[cell] = cells.get(cell, 0) + 1
        artists[artist] = artists.get(artist, 0) + 1
        per_source[source] = per_source.get(source, 0) + 1
        samples.append({
            "file": dest.name, "site": slug, "true_lat": lat, "true_lon": lon,
            "commons_title": title, "descriptionurl": info.get("descriptionurl"),
            "licence": _clean(meta, "LicenseShortName") or licence,
            "artist": artist, "credit": _clean(meta, "Credit"), "bytes": size,
            "query": source, "qualifying_clues": [c.as_dict() for c in narrow],
        })
        save()
        kinds = ", ".join(f"{c.kind}:{'/'.join(sorted(set(c.countries))[:3])}"
                          for c in narrow)
        print(f"  kept {len(samples):>3}  {slug:<40} {kinds}", flush=True)

    # Expand each category into itself and its subcategories, each its own
    # source with its own cap, so one country's signs cannot fill the set.
    expanded: list[tuple[str, str]] = []
    for kind, name in SOURCES:
        if kind == "category":
            expanded.append(("category", name))
            try:
                expanded += [("category", sub) for sub in subcategories(name)]
            except Exception as exc:
                print(f"subcategories of {name} failed: {type(exc).__name__}")
            time.sleep(1.0)
        else:
            expanded.append((kind, name))

    for kind, name in expanded:
        if len(samples) >= args.target:
            break
        if per_source.get(name, 0) >= PER_SOURCE:
            continue
        try:
            if kind == "category":
                pages = category_pages(name, args.per_source)
            else:
                pages = []
                titles = [t for t in search_titles(name, args.per_source) if t not in seen]
                for start in range(0, len(titles), 50):
                    pages += page_info(titles[start:start + 50])
                    time.sleep(1.0)
        except Exception as exc:
            print(f"{name}: failed ({type(exc).__name__})")
            continue
        fresh = [p for p in pages if p.get("title") not in seen]
        print()
        print(f"{name}: {len(fresh)} new candidates", flush=True)
        for page in fresh:
            if len(samples) >= args.target or per_source.get(name, 0) >= PER_SOURCE:
                break
            consider(page, name)
        save()
    save()
    print()
    print(f"{len(samples)} photographs kept in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
