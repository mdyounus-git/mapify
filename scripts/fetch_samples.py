#!/usr/bin/env python3
"""Build a small benchmark set of freely-licensed, geotagged photographs.

Wikimedia Commons hosts geotagged images under CC or public-domain terms, and
its API returns both the file and the coordinate the photographer recorded.
That gives us test inputs with ground truth that we are allowed to use.

The images are written to ``samples/`` with a ``manifest.json`` recording each
file's title, licence, author, source page and true coordinate, so attribution
travels with the data. ``samples/`` is git-ignored: this fetches inputs for
local evaluation, it does not redistribute anyone's photographs.

    python scripts/fetch_samples.py [--per-site 1] [--frame india]

``--frame india`` fetches the India frame (sample_sites.INDIA_SITES) into
``samples/india/`` instead, with a manifest of its own.

Wikimedia asks for a descriptive User-Agent and reasonable request rates; both
are honoured here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import http.client
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
OUT = ROOT / "samples"

API = "https://commons.wikimedia.org/w/api.php"
UA = (
    "Mapify/0.1 (open-source image geolocation benchmark; "
    "https://github.com/mdyounus-git/mapify)"
)

# The sampling frame lives in sample_sites.py so it can be inspected and
# argued with on its own. 165 sites, with Africa and South America
# deliberately over-sampled relative to their share of photo corpora, because
# that is where GeoCLIP is known to fail.
from sample_sites import FRAMES

ALLOWED_LICENCE_MARKERS = (
    "cc0", "cc by", "cc-by", "public domain", "pd-", "attribution",
)


def api_get(params: dict[str, str], attempts: int = 3) -> dict:
    """Query the API, retrying transient failures.

    Over 165 sites the connection will be dropped at least once; that must
    cost one site a retry, not end the run.
    """
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


def download(url: str, dest: Path, attempts: int = 3) -> int:
    last: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
            dest.write_bytes(data)
            return len(data)
        except (urllib.error.URLError, http.client.HTTPException,
                ConnectionError, TimeoutError) as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"download failed after {attempts} attempts: {last}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-site", type=int, default=1)
    ap.add_argument("--radius", type=int, default=600)
    ap.add_argument("--frame", choices=sorted(FRAMES), default="world",
                    help="which sampling frame to fetch (india: samples/india/)")
    ap.add_argument("--refetch", action="store_true",
                    help="ignore what is already downloaded")
    args = ap.parse_args()
    global OUT
    if args.frame != "world":
        OUT = OUT / args.frame
    sites = FRAMES[args.frame]

    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    failures: list[str] = []

    # Resume: keep whatever a previous interrupted run already fetched.
    existing_manifest = OUT / "manifest.json"
    have: dict[str, dict] = {}
    if existing_manifest.exists() and not args.refetch:
        try:
            for entry in json.loads(
                existing_manifest.read_text(encoding="utf-8")
            ).get("samples", []):
                if (OUT / entry["file"]).exists():
                    have[entry["site"]] = entry
        except (json.JSONDecodeError, KeyError):
            have = {}
        if have:
            print(f"resuming: {len(have)} sites already fetched\n")
            manifest.extend(have.values())

    for slug, lat, lon, region, scene, label in sites:
        if slug in have:
            continue
        print(f"{slug:16s} {region:<14} {scene:<9} {label}")
        try:
            pages = geosearch(lat, lon, args.radius, 25)
        except Exception as exc:
            print(f"                 geosearch failed, skipping: "
                  f"{type(exc).__name__}")
            failures.append(slug)
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
            except Exception as exc:
                print(f"                 download failed: "
                      f"{type(exc).__name__}")
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
                "region": region,
                "scene": scene,
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
            print(f"                 {dest.name}  {size / 1024:.0f} KB  "
                  f"{entry['licence']}  ({true_lat:.4f}, {true_lon:.4f})")
            time.sleep(0.6)   # be polite to the API

        if taken == 0:
            print("                 no usable freely-licensed image found")

    regions: dict[str, int] = {}
    scenes: dict[str, int] = {}
    for e in manifest:
        regions[e["region"]] = regions.get(e["region"], 0) + 1
        scenes[e["scene"]] = scenes.get(e["scene"], 0) + 1

    (OUT / "manifest.json").write_text(
        json.dumps({
            "_note": (
                "Test inputs fetched from Wikimedia Commons for local "
                "evaluation. Each entry records its licence and author; "
                "these files are not redistributed by this repository."
            ),
            "counts": {"total": len(manifest), "by_region": regions,
                       "by_scene": scenes},
            "samples": manifest,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n{len(manifest)} samples -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
