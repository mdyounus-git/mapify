#!/usr/bin/env python3
"""Download the Earth textures for the photoreal globe mode.

All imagery here comes from NASA's Earth Observatory / Visible Earth. NASA
content is generally not copyrighted and may be used for any purpose; the
agency asks for a credit line, which the application displays on the globe and
which is recorded in ATTRIBUTION.md.

    https://www.nasa.gov/nasa-brand-center/images-and-media/

Textures are large, so this is a separate, optional step: the tool runs
perfectly well without it and falls back to the vector globe built from
Natural Earth. Run it when you want the satellite look.

    python scripts/fetch_textures.py [--size 4k|2k] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEX_DIR = ROOT / "mapify" / "web" / "textures"

UA = "Mapify/0.1 (open-source image geolocation; texture bootstrap)"

CREDIT = "NASA Earth Observatory"

# (url, local name, target width, credit detail)
TEXTURES = [
    (
        "https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73909/"
        "world.topo.bathy.200412.3x5400x2700.jpg",
        "earth_day.jpg",
        "NASA Blue Marble Next Generation (December 2004), topography and "
        "bathymetry, by Reto Stockli, NASA Earth Observatory",
    ),
    (
        "https://eoimages.gsfc.nasa.gov/images/imagerecords/79000/79765/"
        "dnb_land_ocean_ice.2012.3600x1800.jpg",
        "earth_night.jpg",
        "NASA Earth at Night 2012 (Black Marble), NASA Earth Observatory / "
        "NOAA NGDC",
    ),
    (
        "https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57747/"
        "cloud_combined_2048.jpg",
        "earth_clouds.jpg",
        "NASA Blue Marble cloud composite, NASA Earth Observatory",
    ),
    (
        "https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73934/"
        "gebco_08_rev_elev_21600x10800.png",
        "earth_elevation.jpg",
        "GEBCO_08 elevation and bathymetry, rendered by NASA Earth "
        "Observatory; GEBCO grid (c) GEBCO / BODC",
    ),
]

SIZES = {"2k": 2048, "4k": 4096, "8k": 8192}


def fetch(url: str, dest: Path, force: bool = False) -> bool:
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"  skip   {dest.name} ({dest.stat().st_size:,} B)")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  get    {dest.name} <- {url.rsplit('/', 1)[-1]}", flush=True)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, tmp.open("wb") as out:
            total = 0
            while chunk := resp.read(1 << 18):
                out.write(chunk)
                total += len(chunk)
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        print(f"  FAILED {url}: {exc}", file=sys.stderr)
        return False
    tmp.replace(dest)
    print(f"         {total:,} B")
    return True


def resample(path: Path, width: int, grayscale: bool = False) -> None:
    """Downscale to the requested width, so the browser is not asked to
    upload a 21600px texture to the GPU."""
    try:
        from PIL import Image
    except ImportError:
        print("  !! Pillow not installed; leaving textures at source size")
        return
    Image.MAX_IMAGE_PIXELS = None  # these are legitimately huge NASA rasters
    with Image.open(path) as img:
        if img.width <= width and not grayscale:
            return
        height = max(1, round(img.height * width / img.width))
        out = img.convert("L" if grayscale else "RGB").resize(
            (width, height), Image.LANCZOS
        )
        out.save(path, "JPEG", quality=88, optimize=True, progressive=True)
    print(f"         resampled -> {width}px ({path.stat().st_size:,} B)")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--size", choices=sorted(SIZES), default="4k",
                    help="target texture width (default: 4k)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    width = SIZES[args.size]

    print(f"NASA Earth textures -> {TEX_DIR}  (target {width}px wide)")
    manifest: dict[str, dict[str, object]] = {}
    credits: list[str] = []
    ok = 0
    for url, name, credit in TEXTURES:
        dest = TEX_DIR / name
        downloaded = fetch(url, dest, args.force)
        if not dest.exists():
            continue
        if downloaded:
            # The elevation map is only ever read as a height/bump channel.
            resample(dest, width, grayscale=(name == "earth_elevation.jpg"))
        ok += 1
        credits.append(credit)
        manifest[name] = {
            "bytes": dest.stat().st_size,
            "sha256": sha256(dest),
            "source": url,
            "credit": credit,
            "license": "NASA imagery -- generally not copyrighted; credit requested",
        }

    (TEX_DIR / "CREDITS.txt").write_text(
        "Earth textures used by the Mapify globe\n"
        "=======================================\n\n"
        + "\n\n".join(f"* {c}" for c in credits)
        + "\n\nNASA imagery is generally not copyrighted and may be used for "
          "any purpose.\nNASA requests a credit line, which this application "
          "displays on the globe.\nSee https://www.nasa.gov/nasa-brand-center/"
          "images-and-media/\n",
        encoding="utf-8",
    )

    mpath = ROOT / "data" / "MANIFEST.json"
    existing: dict = {}
    if mpath.exists():
        existing = json.loads(mpath.read_text(encoding="utf-8"))
    existing.update(manifest)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")

    print(f"\n{ok}/{len(TEXTURES)} textures ready. Credits: {TEX_DIR / 'CREDITS.txt'}")
    if ok < len(TEXTURES):
        print("Missing textures just mean the satellite globe mode degrades; "
              "the vector globe always works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
