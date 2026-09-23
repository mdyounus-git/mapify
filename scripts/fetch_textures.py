#!/usr/bin/env python3
"""Download and prepare the Earth textures for the photoreal globe.

All imagery comes from NASA's Earth Observatory / Visible Earth. NASA content
is generally not copyrighted and may be used for any purpose; the agency asks
for a credit line, which the application displays on the globe and which is
recorded in ATTRIBUTION.md.

    https://www.nasa.gov/nasa-brand-center/images-and-media/

The sources are large -- the Blue Marble master is 21600x10800 -- so they are
downloaded once and resampled to the working size. 8k is the default because
the globe fills most of the window and 4k visibly softens at that size.

    python scripts/fetch_textures.py [--size 2k|4k|8k] [--force] [--keep-source]

One texture is not from NASA: the ocean mask is generated from the Natural
Earth coastlines already in data/, because deriving "is this water" from image
luminance mistakes dark forest for ocean and sea ice for cloud.
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
CACHE_DIR = ROOT / "data" / "texture-cache"

UA = "Mapify/0.1 (open-source image geolocation; texture bootstrap)"

SIZES = {"2k": 2048, "4k": 4096, "8k": 8192}

E = "https://eoimages.gsfc.nasa.gov/images/imagerecords"

# name -> (url, mode, credit)
#   mode "rgb"  : colour texture
#   mode "gray" : single channel, stored as a greyscale JPEG
TEXTURES = {
    "earth_day.jpg": (
        f"{E}/73000/73909/world.topo.bathy.200412.3x21600x10800.jpg",
        "rgb",
        "NASA Blue Marble Next Generation (December 2004), topography and "
        "bathymetry, by Reto Stockli, NASA Earth Observatory",
    ),
    "earth_night.jpg": (
        f"{E}/79000/79765/dnb_land_ocean_ice.2012.13500x6750.jpg",
        "rgb",
        "NASA Earth at Night 2012 (Black Marble), NASA Earth Observatory / "
        "NOAA National Geophysical Data Center",
    ),
    "earth_clouds.jpg": (
        f"{E}/57000/57747/cloud_combined_8192.tif",
        "gray",
        "NASA Blue Marble cloud composite, NASA Earth Observatory",
    ),
    "earth_elevation.jpg": (
        f"{E}/73000/73934/gebco_08_rev_elev_21600x10800.png",
        "gray",
        "GEBCO_08 elevation and bathymetry, rendered by NASA Earth "
        "Observatory; the underlying GEBCO grid is (c) GEBCO / BODC",
    ),
}


def fetch(url: str, dest: Path, force: bool = False) -> bool:
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"    cached  {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            step = max(1, total // 8) if total else (1 << 24)
            next_mark = step
            with tmp.open("wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
                    got += len(chunk)
                    if got >= next_mark:
                        pct = f"{100 * got / total:3.0f}%" if total else ""
                        print(f"      {got / 1e6:6.1f} MB {pct}", flush=True)
                        next_mark += step
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        print(f"    FAILED  {url}", file=sys.stderr)
        print(f"            {exc}", file=sys.stderr)
        return False
    tmp.replace(dest)
    print(f"    got     {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return True


def resample(src: Path, dest: Path, width: int, mode: str) -> bool:
    try:
        from PIL import Image
    except ImportError:
        print("    !! Pillow not installed; cannot resample", file=sys.stderr)
        return False

    # These are legitimately enormous scientific rasters, not decompression
    # bombs; Pillow's default guard would refuse them.
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(src) as img:
        w = min(width, img.width)
        h = max(1, round(img.height * w / img.width))
        out = img.convert("L" if mode == "gray" else "RGB")
        if (w, h) != out.size:
            out = out.resize((w, h), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.save(dest, "JPEG", quality=90, optimize=True, progressive=True,
                 subsampling=0 if mode == "rgb" else 2)
    print(f"    built   {dest.name}  {w}x{h}  "
          f"({dest.stat().st_size / 1e6:.1f} MB)")
    return True


def build_ocean_mask(width: int) -> bool:
    """Render a land/water mask from the Natural Earth coastlines.

    White is water. The globe shader uses this to decide where sunlight can
    glint, which is far more reliable than guessing from the colour of the
    Blue Marble image: deserts are bright, sea ice is white, and dense forest
    is as dark as deep ocean.
    """
    try:
        import shapefile  # pyshp
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        print("    !! pyshp and Pillow needed for the ocean mask")
        return False

    src = ROOT / "data" / "naturalearth" / "ne_10m_admin_0_countries"
    if not src.with_suffix(".shp").exists():
        print("    !! Natural Earth data missing; run scripts/fetch_data.py")
        return False

    height = width // 2
    img = Image.new("L", (width, height), 255)      # start all water
    draw = ImageDraw.Draw(img)

    reader = shapefile.Reader(str(src))
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
            draw.polygon(
                [(((lon + 180.0) / 360.0) * width,
                  ((90.0 - lat) / 180.0) * height) for lon, lat in ring],
                fill=0,                              # land
            )
    reader.close()

    # A one-pixel coastline would alias into a shimmering rim at grazing
    # angles, so soften it very slightly.
    img = img.filter(ImageFilter.GaussianBlur(radius=max(0.6, width / 4096)))
    dest = TEX_DIR / "earth_ocean_mask.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=88, optimize=True)
    print(f"    built   {dest.name}  {width}x{height}  "
          f"({dest.stat().st_size / 1e6:.1f} MB)  [Natural Earth, public domain]")
    return True


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--size", choices=sorted(SIZES), default="8k",
                    help="working texture width (default: 8k)")
    ap.add_argument("--force", action="store_true",
                    help="re-download the sources")
    ap.add_argument("--keep-source", action="store_true",
                    help="keep the multi-hundred-megabyte originals")
    args = ap.parse_args()
    width = SIZES[args.size]

    print(f"Earth textures -> {TEX_DIR}   (working size {width}px)")
    print(f"Sources cached in {CACHE_DIR}")
    print()

    manifest: dict[str, dict[str, object]] = {}
    credits: list[str] = []
    built = 0

    for name, (url, mode, credit) in TEXTURES.items():
        print(f"  {name}")
        src = CACHE_DIR / url.rsplit("/", 1)[-1]
        if not fetch(url, src, args.force):
            print("    skipping; the globe falls back for this layer")
            print()
            continue
        dest = TEX_DIR / name
        if resample(src, dest, width, mode):
            built += 1
            credits.append(credit)
            manifest[name] = {
                "bytes": dest.stat().st_size,
                "sha256": sha256(dest),
                "source": url,
                "credit": credit,
                "license": (
                    "NASA imagery -- generally not copyrighted; credit requested"
                ),
            }
        print()

    print("  earth_ocean_mask.jpg")
    if build_ocean_mask(min(width, 8192)):
        p = TEX_DIR / "earth_ocean_mask.jpg"
        credits.append(
            "Ocean mask generated from Natural Earth 10m coastlines "
            "(public domain)"
        )
        manifest[p.name] = {
            "bytes": p.stat().st_size,
            "sha256": sha256(p),
            "source": "generated from data/naturalearth/ne_10m_admin_0_countries",
            "license": "Public domain (Natural Earth)",
        }

    (TEX_DIR / "CREDITS.txt").write_text(
        "Earth textures used by the Mapify globe\n"
        "=======================================\n\n"
        + "\n\n".join(f"* {c}" for c in credits)
        + "\n\nNASA imagery is generally not copyrighted and may be used for "
          "any purpose.\nNASA requests a credit line, which this application "
          "displays on the globe.\nSee https://www.nasa.gov/nasa-brand-center/"
          "images-and-media/\n\nNASA does not endorse this project.\n",
        encoding="utf-8",
    )

    mpath = ROOT / "data" / "MANIFEST.json"
    existing: dict = {}
    if mpath.exists():
        existing = json.loads(mpath.read_text(encoding="utf-8"))
    existing.update(manifest)
    mpath.write_text(json.dumps(existing, indent=2, sort_keys=True),
                     encoding="utf-8")

    if not args.keep_source and CACHE_DIR.exists():
        freed = sum(p.stat().st_size for p in CACHE_DIR.glob("*"))
        for p in CACHE_DIR.glob("*"):
            p.unlink()
        try:
            CACHE_DIR.rmdir()
        except OSError:
            pass
        print(f"\nRemoved {freed / 1e6:.0f} MB of source rasters "
              f"(--keep-source to retain them).")

    total = sum(p.stat().st_size for p in TEX_DIR.glob("*.jpg"))
    print(f"\n{built}/{len(TEXTURES)} NASA textures + ocean mask ready "
          f"({total / 1e6:.1f} MB).")
    print(f"Credits: {TEX_DIR / 'CREDITS.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
