#!/usr/bin/env python3
"""Download the bundled open geodata and the vendored front-end library.

Everything fetched here is public domain (Natural Earth) or MIT
(three.js).  Nothing requires an account, a key, or a payment.  Run once
after install; re-running is idempotent and skips files already present.

    python scripts/fetch_data.py [--force] [--skip-vendor]
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
GEO_DIR = ROOT / "data" / "naturalearth"
VENDOR_DIR = ROOT / "mapify" / "web" / "vendor"

UA = "Mapify/0.1 (open-source image geolocation; dataset bootstrap)"

NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master"
NE_LAYERS = [
    ("10m_cultural", "ne_10m_admin_0_countries"),
    ("10m_cultural", "ne_10m_admin_1_states_provinces"),
    ("10m_cultural", "ne_10m_populated_places"),
    ("110m_cultural", "ne_110m_admin_0_countries"),
]
NE_EXTS = (".shp", ".dbf", ".shx")

THREE_VERSION = "0.186.0"
# Three.js ships ES modules, so the front end imports them through an import
# map: no bundler, no build step, nothing to trust but these files.
VENDOR_FILES = [
    (
        f"https://unpkg.com/three@{THREE_VERSION}/build/three.module.js",
        "three.module.js",
    ),
    (
        f"https://unpkg.com/three@{THREE_VERSION}/build/three.core.js",
        "three.core.js",
    ),
    (
        f"https://unpkg.com/three@{THREE_VERSION}/examples/jsm/controls/"
        "OrbitControls.js",
        "OrbitControls.js",
    ),
    (
        f"https://unpkg.com/three@{THREE_VERSION}/LICENSE",
        "three.LICENSE.txt",
    ),
]


def fetch(url: str, dest: Path, force: bool = False) -> bool:
    """Download ``url`` to ``dest``.  Returns True if a download happened."""
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"  skip   {dest.name} ({dest.stat().st_size:,} B)")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
            total = 0
            while chunk := resp.read(1 << 16):
                out.write(chunk)
                total += len(chunk)
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"  FAILED {url}\n         {exc}") from exc
    tmp.replace(dest)
    print(f"  got    {dest.name} ({total:,} B)")
    return True


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def build_globe_geojson() -> None:
    """Convert the 110m country outlines to GeoJSON for the offline globe."""
    try:
        import shapefile  # pyshp
    except ImportError:
        print("  !! pyshp not installed; skipping globe GeoJSON build")
        return

    src = GEO_DIR / "ne_110m_admin_0_countries.shp"
    dest = VENDOR_DIR.parent / "data" / "ne_110m_countries.geojson"
    dest.parent.mkdir(parents=True, exist_ok=True)
    reader = shapefile.Reader(str(src))
    fields = [f[0] for f in reader.fields[1:]]
    keep = {"NAME", "ISO_A2_EH", "ISO_A3_EH", "CONTINENT"}
    features = []
    for sr in reader.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        props = {k: rec.get(k) for k in keep if k in rec}
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": sr.shape.__geo_interface__,
            }
        )
    reader.close()
    payload = {
        "type": "FeatureCollection",
        "_source": "Natural Earth 110m admin-0 countries (public domain)",
        "features": features,
    }
    # separators keep the file small; the globe only needs outlines.
    dest.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"  built  {dest.name} ({dest.stat().st_size:,} B, {len(features)} features)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download everything")
    ap.add_argument("--skip-vendor", action="store_true", help="skip three.js")
    ap.add_argument("--skip-geo", action="store_true", help="skip Natural Earth")
    args = ap.parse_args()

    manifest: dict[str, dict[str, object]] = {}

    if not args.skip_geo:
        print("Natural Earth (public domain) ->", GEO_DIR)
        for folder, layer in NE_LAYERS:
            for ext in NE_EXTS:
                name = layer + ext
                fetch(f"{NE_BASE}/{folder}/{name}", GEO_DIR / name, args.force)
        for folder, layer in NE_LAYERS:
            for ext in NE_EXTS:
                p = GEO_DIR / (layer + ext)
                manifest[p.name] = {
                    "bytes": p.stat().st_size,
                    "sha256": sha256(p),
                    "source": f"{NE_BASE}/{folder}/{p.name}",
                    "license": "Public domain (Natural Earth)",
                }
        print("Building offline globe geometry")
        build_globe_geojson()

    if not args.skip_vendor:
        print(f"Three.js {THREE_VERSION} (MIT) ->", VENDOR_DIR)
        for url, name in VENDOR_FILES:
            fetch(url, VENDOR_DIR / name, args.force)
            p = VENDOR_DIR / name
            manifest[p.name] = {
                "bytes": p.stat().st_size,
                "sha256": sha256(p),
                "source": url,
                "license": "MIT (three.js)",
            }

    mpath = ROOT / "data" / "MANIFEST.json"
    existing: dict = {}
    if mpath.exists():
        existing = json.loads(mpath.read_text(encoding="utf-8"))
    existing.update(manifest)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nManifest written: {mpath}")
    print("All assets are public domain or MIT. See ATTRIBUTION.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
