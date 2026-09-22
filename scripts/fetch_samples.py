#!/usr/bin/env python3
"""Build a small benchmark set of freely-licensed, geotagged photographs.

Wikimedia Commons hosts geotagged images under CC or public-domain terms, and
its API returns both the file and the coordinate the photographer recorded.
That gives us test inputs with ground truth that we are allowed to use.

The images are written to ``samples/`` with a ``manifest.json`` recording each
file's title, licence, author, source page and true coordinate, so attribution
travels with the data. ``samples/`` is git-ignored: this fetches inputs for
local evaluation, it does not redistribute anyone's photographs.

    python scripts/fetch_samples.py [--per-site 1]

Wikimedia asks for a descriptive User-Agent and reasonable request rates; both
are honoured here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"

API = "https://commons.wikimedia.org/w/api.php"
UA = (
    "Mapify/0.1 (open-source image geolocation benchmark; "
    "https://github.com/mdyounus-git/mapify)"
)

# Spread across continents, traffic sides, scripts and biomes, so the sample
# actually exercises the evidence machinery rather than four European cities.
SITES = [
    ("paris",      48.8584,   2.2945, "Paris, France"),
    ("tokyo",      35.6595, 139.7005, "Shibuya, Tokyo, Japan"),
    ("bengaluru",  12.9794,  77.5912, "Vidhana Soudha, Bengaluru, India"),
    ("moscow",     55.7525,  37.6231, "Red Square, Moscow, Russia"),
    ("cairo",      29.9792,  31.1342, "Giza, Egypt"),
    ("nyc",        40.7580, -73.9855, "Times Square, New York, USA"),
    ("sydney",    -33.8568, 151.2153, "Sydney Opera House, Australia"),
    ("rio",       -22.9519, -43.2105, "Corcovado, Rio de Janeiro, Brazil"),
    ("reykjavik",  64.1466, -21.9426, "Reykjavik, Iceland"),
    ("bangkok",    13.7500, 100.4913, "Grand Palace, Bangkok, Thailand"),
    ("nairobi",    -1.2921,  36.8219, "Nairobi, Kenya"),
    ("istanbul",   41.0082,  28.9784, "Istanbul, Turkiye"),
]

ALLOWED_LICENCE_MARKERS = (
    "cc0", "cc by", "cc-by", "public domain", "pd-", "attribution",
)


def api_get(params: dict[str, str]) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)


def geosearch(lat: float, lon: float, radius_m: int, limit: int) -> list[dict]:
    payload = api_get({
        "action": "query",
        "generator": "geosearch",
        "ggscoord": f"{lat}|{lon}",
        "ggsradius": str(radius_m),
        "ggslimit": str(limit),
        "ggsnamespace": "6",           # File:
        "prop": "imageinfo|coordinates",
        "iiprop": "url|extmetadata|size|mime",
        "iiurlwidth": "1280",
    })
    pages = (payload.get("query") or {}).get("pages") or {}
    return list(pages.values())


def usable(page: dict) -> tuple[bool, str]:
    info = (page.get("imageinfo") or [{}])[0]
    mime = info.get("mime", "")
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        return False, f"mime {mime}"
    meta = info.get("extmetadata") or {}
    licence = (meta.get("LicenseShortName", {}).get("value") or "").lower()
    if not any(m in licence for m in ALLOWED_LICENCE_MARKERS):
        return False, f"licence {licence or 'unknown'}"
    if (info.get("width") or 0) < 640:
        return False, "too small"
    return True, licence


def download(url: str, dest: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return len(data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-site", type=int, default=1)
    ap.add_argument("--radius", type=int, default=600)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    for slug, lat, lon, label in SITES:
        print(f"{slug:11s} {label}")
        try:
            pages = geosearch(lat, lon, args.radius, 25)
        except urllib.error.URLError as exc:
            print(f"            geosearch failed: {exc}")
            continue

        taken = 0
        for page in pages:
            if taken >= args.per_site:
                break
            ok, reason = usable(page)
            if not ok:
                continue
            info = page["imageinfo"][0]
            coords = (page.get("coordinates") or [{}])[0]
            true_lat = coords.get("lat", lat)
            true_lon = coords.get("lon", lon)
            meta = info.get("extmetadata") or {}

            ext = Path(urllib.parse.urlparse(info["url"]).path).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                ext = ".jpg"
            dest = OUT / f"{slug}{ext}"
            try:
                size = download(info.get("thumburl") or info["url"], dest)
            except urllib.error.URLError as exc:
                print(f"            download failed: {exc}")
                continue

            def clean(key: str) -> str:
                raw = meta.get(key, {}).get("value", "") or ""
                # extmetadata values are HTML fragments.
                out, depth = [], 0
                for ch in raw:
                    if ch == "<":
                        depth += 1
                    elif ch == ">":
                        depth = max(0, depth - 1)
                    elif depth == 0:
                        out.append(ch)
                return " ".join("".join(out).split())[:180]

            entry = {
                "file": dest.name,
                "site": slug,
                "expected_label": label,
                "true_lat": true_lat,
                "true_lon": true_lon,
                "commons_title": page.get("title"),
                "descriptionurl": info.get("descriptionurl"),
                "licence": clean("LicenseShortName") or reason,
                "artist": clean("Artist"),
                "credit": clean("Credit"),
                "bytes": size,
            }
            manifest.append(entry)
            taken += 1
            print(f"            {dest.name}  {size:,} B  {entry['licence']}  "
                  f"({true_lat:.4f}, {true_lon:.4f})")
            time.sleep(0.6)   # be polite to the API

        if taken == 0:
            print("            no usable freely-licensed image found")

    (OUT / "manifest.json").write_text(
        json.dumps({
            "_note": (
                "Test inputs fetched from Wikimedia Commons for local "
                "evaluation. Each entry records its licence and author; "
                "these files are not redistributed by this repository."
            ),
            "samples": manifest,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n{len(manifest)} samples -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
