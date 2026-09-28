#!/usr/bin/env python3
"""Fetch freely-licensed images that are *not* photographs of places.

The 163-sample benchmark measures how far off the tool is on photographs
of places. It cannot measure the opposite failure -- being handed a
poster, a chart or a screenshot and answering with coordinates anyway --
because every image in it is a photograph of a place. A gate that
refuses non-places has to be tested on non-places, and on the benchmark
at the same time, because the number that matters most is how many real
photographs it wrongly swallows.

Images come from Wikimedia Commons categories, as thumbnails (Wikimedia
prefers thumbnails to originals for this kind of use), with each file's
licence, author and source page written to ``samples/nonplace/manifest.json``.
``samples/`` is git-ignored: this is local test input, not redistribution.

Each category carries the class it is *expected* to fall in. Categories
are not pure -- "Posters" contains a few photographs of posters on real
walls -- so a disagreement between the gate and the label is a thing to
look at, not automatically a gate error.

    python scripts/fetch_nonplace.py [--per-category 8]
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples" / "nonplace"

API = "https://commons.wikimedia.org/w/api.php"
UA = (
    "Mapify/0.1 (open-source image geolocation benchmark; "
    "https://github.com/mdyounus-git/mapify)"
)

ALLOWED_LICENCE_MARKERS = (
    "cc0", "cc by", "cc-by", "public domain", "pd-", "attribution",
)

# (category, expected class). "not_a_photo" must be refused outright;
# "photo_no_scenery" is a real photograph with nothing in it that places
# it, which should end up unlocated but through the ordinary confidence
# path rather than the gate.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Computer security", "not_a_photo"),
    ("Information security", "not_a_photo"),
    ("Posters", "not_a_photo"),
    ("Pie charts", "not_a_photo"),
    ("Bar charts", "not_a_photo"),
    ("Computer network diagrams", "not_a_photo"),
    ("Text logos", "not_a_photo"),
    ("Presentation slides", "not_a_photo"),
    ("Book covers", "not_a_photo"),
    ("Scanned documents", "not_a_photo"),
    ("Food photography", "photo_no_scenery"),
    ("Computer keyboards", "photo_no_scenery"),
    ("Coffee cups", "photo_no_scenery"),
    ("Living rooms", "photo_no_scenery"),
)

_SKIP_MIME = ("video/", "audio/", "application/ogg")

# Corrections made after looking at every image. Category membership is
# not the same as content: "Computer security" holds photographs of
# people at a security exhibition, "Book covers" holds photographs of
# physical books. The gate must refuse none of those, so labelling them
# "not_a_photo" would reward a gate for refusing real photographs.
#
#   either      a photograph whose whole frame is a document; refusing it
#               or not are both defensible, so it is not scored
#   duplicate   the same artwork as another file (a TIFF original and
#               its JPEG); counted once
RELABEL: dict[str, str] = {
    "computer-security-01.jpg": "photo_no_scenery",   # event, indoors
    "computer-security-06.jpg": "photo_no_scenery",   # appliance product shot
    "information-security-01.jpg": "either",          # photo of a cover sheet
    "information-security-02.jpg": "either",          # photo of stamped papers
    "information-security-03.jpg": "either",          # photo of a cover sheet
    "information-security-05.jpg": "photo_no_scenery",  # exhibition booth
    "information-security-06.jpg": "photo_no_scenery",  # exhibition hall
    "posters-03.jpg": "duplicate",
    "posters-05.jpg": "duplicate",
    "posters-07.jpg": "duplicate",
    "posters-08.jpg": "either",                       # photo of a paper flyer
    "pie-charts-02.jpg": "photo_no_scenery",          # photo of a laptop
    "computer-keyboards-05.png": "not_a_photo",       # screenshot of a layout
    **{f"book-covers-{i:02d}.jpg": "photo_no_scenery" for i in range(1, 9)},
}


def api_get(params: dict[str, str], attempts: int = 3) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    last: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.load(resp)
        except (urllib.error.URLError, http.client.HTTPException,
                ConnectionError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"giving up after {attempts} attempts: {last}")


def members(category: str, limit: int) -> list[dict]:
    payload = api_get({
        "action": "query",
        "generator": "categorymembers",
        "gcmtitle": f"Category:{category}",
        "gcmtype": "file",
        "gcmlimit": str(min(50, limit * 4)),
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata|size",
        "iiurlwidth": "800",
    })
    pages = payload.get("query", {}).get("pages", {})
    return sorted(pages.values(), key=lambda p: p.get("title", ""))


def _meta(info: dict, key: str) -> str:
    value = info.get("extmetadata", {}).get(key, {}).get("value", "")
    return re.sub(r"<[^>]+>", "", str(value)).strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fetch(url: str, dest: Path, attempts: int = 3) -> bool:
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                dest.write_bytes(resp.read())
            return True
        except (urllib.error.URLError, http.client.HTTPException,
                ConnectionError, TimeoutError):
            if attempt < attempts - 1:
                time.sleep(2.0 * (attempt + 1))
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--per-category", type=int, default=8)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else [])
    have = {m["title"] for m in manifest}

    total_bytes = 0
    for category, expected in CATEGORIES:
        taken = sum(1 for m in manifest if m["category"] == category)
        if taken >= args.per_category:
            continue
        try:
            pages = members(category, args.per_category)
        except RuntimeError as exc:
            print(f"  {category}: {exc}")
            continue
        time.sleep(1.0)
        for page in pages:
            if taken >= args.per_category:
                break
            title = page.get("title", "")
            if title in have:
                continue
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            mime = info.get("mime", "")
            if any(mime.startswith(m) for m in _SKIP_MIME):
                continue
            licence = _meta(info, "LicenseShortName")
            if not any(m in licence.lower() for m in ALLOWED_LICENCE_MARKERS):
                continue
            thumb = info.get("thumburl") or info.get("url")
            if not thumb:
                continue
            ext = Path(urllib.parse.urlparse(thumb).path).suffix.lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            name = f"{slug(category)}-{taken + 1:02d}{ext}"
            dest = OUT / name
            if not fetch(thumb, dest):
                continue
            total_bytes += dest.stat().st_size
            manifest.append({
                "file": name,
                "title": title,
                "category": category,
                "expected": RELABEL.get(name, expected),
                "licence": licence,
                "author": _meta(info, "Artist")[:200],
                "source": info.get("descriptionurl", ""),
                "mime": mime,
            })
            have.add(title)
            taken += 1
            print(f"  {name:40s} {licence}")
            time.sleep(0.8)
        manifest_path.write_text(json.dumps(manifest, indent=2,
                                            ensure_ascii=False),
                                 encoding="utf-8")

    print(f"\n{len(manifest)} images in {OUT} "
          f"(+{total_bytes / 1e6:.1f} MB this run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
