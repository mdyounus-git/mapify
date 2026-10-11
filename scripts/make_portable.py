#!/usr/bin/env python3
"""Make one portable ZIP of Mapify that works wherever it is unzipped.

    .venv\\Scripts\\python scripts/make_portable.py [--verify]

The ZIP holds one folder, ``Mapify``:

    Mapify\\Mapify.exe, _internal\\   the application (build_exe.py's output)
    Mapify\\models\\                  the model weights: CLIP and the OCR models
    Mapify\\data\\cache\\             the encoded GPS gallery (5-8 min of CPU saved)
    Mapify\\README-PORTABLE.txt       what it needs and what works offline

Unzipped anywhere writable, Mapify keeps its state and finds its weights
beside the executable (see mapify/paths.py), so nothing is downloaded and
analysis works with no internet at all.

Deliberately left out, because they are the person's own and not the
program's: their configuration (it can hold a Mapillary token), case
files, logs, cached place lookups, Pinpoint's reference photographs and
the local map store (an OpenStreetMap derivative regenerated per machine,
never redistributed -- see ATTRIBUTION.md).

``--verify`` unzips the result into bench/portable-check and runs the
packaged executable's self-test there, which must report the weights as
present in that folder.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "Mapify"
CLIP_DIR = "models--openai--clip-vit-large-patch14"
# Weights and arrays barely compress; storing them is minutes faster.
STORED = {".safetensors", ".bin", ".pth", ".npy", ".pt", ".onnx", ".msgpack", ".h5"}

README = """Mapify - portable
=================

Run:     double-click Mapify.exe in this folder. Nothing to install.
Needs:   Windows 10 or 11, 64-bit; the Microsoft Edge WebView2 runtime
         (part of Windows 11 and current Windows 10 - Mapify says so if
         it is missing); about 6 GB free where this folder is.
Keep:    the whole folder together. Mapify writes its settings, caches
         and logs into the "data" folder here, so put it somewhere you
         can write to (Desktop, Documents, a USB drive) - not Program Files.

Works with no internet:
  - analysing a photograph: every model is in the "models" folder
  - the globe, country and place lookup, reports, case files

Needs the internet:
  - Pinpoint (it compares with reference photographs from Wikimedia Commons)
  - 3D buildings and Walk for places not seen before (map tiles, imagery)
  - weather and air history, street-level place names

Mapify is not code-signed, so Windows SmartScreen may warn the first time
it runs; that warning is accurate - the publisher is unverified. Source,
licences and attributions: LICENSE and ATTRIBUTION.md in _internal, and
https://github.com/mdyounus-git/mapify
"""


def find_models() -> Path:
    for cand in (ROOT / "app" / "models", ROOT / "models"):
        if (any((cand / "hub" / CLIP_DIR / "snapshots").glob("*"))
                and any((cand / "easyocr" / "model").glob("*.pth"))):
            return cand
    raise SystemExit("No complete model folder (CLIP + OCR) found in app/models or "
                     "models/. Run the application once so it downloads them.")


def find_gallery() -> list[Path]:
    for cand in (ROOT / "app" / "data" / "cache", ROOT / "data" / "cache"):
        hits = sorted(cand.glob("geoclip_gallery_*.npy"))
        if hits:
            return hits
    return []


def entries(models: Path, gallery: list[Path]) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for p in sorted(DIST.rglob("*")):
        if p.is_file():
            rel = p.relative_to(DIST)
            if rel.parts[0] in ("data", "models") or p.name == "config.toml":
                continue                       # never a previous run's state
            if "_dev" in rel.parts:
                continue                       # test copies (web/vendor/_dev)
            out.append((p, f"Mapify/{rel.as_posix()}"))
    # CLIP and the OCR models only: the optional research parts (PLONK,
    # GlotLID, GeoNames, the street-photo memory) are off by default and
    # fetched by whoever turns them on (scripts/fetch_research_data.py).
    for sub in (f"hub/{CLIP_DIR}", "easyocr"):
        for p in sorted((models / sub).rglob("*")):
            if p.is_file() and not p.name.endswith((".lock", ".incomplete")):
                out.append((p, f"Mapify/models/{p.relative_to(models).as_posix()}"))
    for g in gallery:
        out.append((g, f"Mapify/data/cache/{g.name}"))
    return out


def build_zip(out: Path) -> Path:
    if not (DIST / "Mapify.exe").is_file():
        raise SystemExit(f"{DIST / 'Mapify.exe'} is missing: run build_exe.py first.")
    models = find_models()
    gallery = find_gallery()
    files = entries(models, gallery)
    total = sum(p.stat().st_size for p, _ in files)
    print(f"  {len(files)} files, {total / 1e9:.2f} GB, models from {models}")
    if not gallery:
        print("  note: no encoded GPS gallery found; the first run will make one")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    started, done = time.time(), 0
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as z:
        z.writestr("Mapify/README-PORTABLE.txt", README.replace("\n", "\r\n"),
                   compress_type=zipfile.ZIP_DEFLATED)
        for i, (p, arc) in enumerate(files, 1):
            kind = (zipfile.ZIP_STORED if p.suffix.lower() in STORED
                    else zipfile.ZIP_DEFLATED)
            z.write(p, arc, compress_type=kind, compresslevel=6)
            done += p.stat().st_size
            if i % 2000 == 0 or i == len(files):
                print(f"  {done / total:5.0%}  {done / 1e9:.2f} GB  "
                      f"{time.time() - started:.0f}s", flush=True)
    tmp.replace(out)
    print(f"  wrote {out} ({out.stat().st_size / 1e9:.2f} GB) in "
          f"{time.time() - started:.0f}s")
    return out


def verify(zip_path: Path) -> int:
    where = ROOT / "bench" / "portable-check"
    if where.exists():
        shutil.rmtree(where)
    print(f"  unzipping into {where}")
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()
        if bad:
            print(f"  CORRUPT member: {bad}")
            return 1
        z.extractall(where)
    exe = where / "Mapify" / "Mapify.exe"
    # Nothing from this machine may leak into the check: no overrides.
    env = {k: v for k, v in __import__("os").environ.items()
           if not k.startswith(("MAPIFY_", "HF_", "TRANSFORMERS_"))}
    probe = subprocess.run([str(exe), "--selftest"], cwd=exe.parent, env=env,
                           capture_output=True, text=True, timeout=900)
    text = (probe.stdout or "") + (probe.stderr or "")
    lines = [ln for ln in text.splitlines() if ln.startswith("  ") or "SELFTEST" in ln]
    print("\n".join(lines[-14:]))
    expected = str(where / "Mapify" / "models")
    ok = (probe.returncode == 0 and "SELFTEST OK" in text
          and "analysis works offline" in text and expected in text)
    print("  VERIFIED: self-contained" if ok
          else f"  NOT VERIFIED (weights must be reported present in {expected})")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    from mapify import __version__
    out = args.out or ROOT / "dist" / f"Mapify-{__version__}-portable.zip"
    print("Mapify - portable package\n")
    zip_path = build_zip(out)
    return verify(zip_path) if args.verify else 0


if __name__ == "__main__":
    raise SystemExit(main())
