#!/usr/bin/env python3
"""Package Mapify as a Windows application folder with Mapify.exe in it.

    .venv\\Scripts\\python build_exe.py [--clean] [--console]

This is optional. ``Mapify.bat`` runs the same application from the source
tree and is the normal way to use it during development; this exists so you
can hand someone a folder they can double-click without installing Python.

Notes on what is and is not bundled:

  * PyTorch is large and PyInstaller cannot make it small, so this builds a
    *folder* distribution rather than a single file. A one-file build of a
    torch application is a two-gigabyte self-extracting archive that unpacks
    to a temporary directory on every launch, which is worse in every way.

  * Model weights are NOT bundled. They live in the Hugging Face cache under
    the user profile and are fetched on first run. Bundling them would make
    a 4 GB download to ship something that then downloads nothing.

  * The Natural Earth data and the Earth textures ARE bundled, because
    without them the application cannot name a location or draw the globe.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"

REQUIRED = [
    (ROOT / "mapify" / "web" / "vendor" / "three.module.js",
     "three.js — run: python scripts/fetch_data.py"),
    (ROOT / "data" / "naturalearth" / "ne_10m_admin_0_countries.shp",
     "Natural Earth — run: python scripts/fetch_data.py"),
    (ROOT / "mapify" / "data" / "country_attributes.json",
     "country table — run: python scripts/build_country_data.py"),
]


def preflight() -> bool:
    ok = True
    for path, hint in REQUIRED:
        if not path.exists():
            print(f"  MISSING  {path.relative_to(ROOT)}\n           {hint}")
            ok = False
    if not (ROOT / "mapify" / "web" / "textures" / "earth_day.jpg").exists():
        print("  note: Earth textures absent; the globe will fall back to "
              "vectors.\n        Run: python scripts/fetch_textures.py")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("  MISSING  PyInstaller\n           Run: pip install pyinstaller")
        ok = False
    return ok


def build(console: bool, clean: bool) -> int:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--name", "Mapify",
        "--noconfirm",
        "--windowed" if not console else "--console",
        "--icon", str(ROOT / "assets" / "mapify.ico"),

        # The interface, the vendored libraries and the textures.
        "--add-data", f"{ROOT / 'mapify' / 'web'}{os_sep()}mapify/web",
        # The country attribute table the fusion step needs.
        "--add-data", f"{ROOT / 'mapify' / 'data'}{os_sep()}mapify/data",
        # Natural Earth: without it there is no offline geocoding at all.
        "--add-data", f"{ROOT / 'data' / 'naturalearth'}{os_sep()}data/naturalearth",
        # Documents the user is entitled to have alongside the binary.
        "--add-data", f"{ROOT / 'ATTRIBUTION.md'}{os_sep()}.",
        "--add-data", f"{ROOT / 'LICENSE'}{os_sep()}.",
        "--add-data", f"{ROOT / 'config.example.toml'}{os_sep()}.",

        # geoclip ships its GPS gallery and weights as package data.
        "--collect-data", "geoclip",
        "--collect-binaries", "geoclip",

        # The whole model stack is imported lazily -- deliberately, so the
        # window opens before a gigabyte of PyTorch does -- which means
        # PyInstaller's static analysis never sees any of it. Collect it
        # explicitly or the build produces an executable that fails on the
        # first analysis.
        "--collect-all", "torch",
        "--collect-all", "torchvision",
        "--collect-all", "transformers",
        "--collect-all", "tokenizers",
        "--collect-all", "geoclip",
        "--collect-all", "timezonefinder",
        "--collect-data", "safetensors",
        "--collect-data", "huggingface_hub",
        "--hidden-import", "torch",
        "--hidden-import", "torchvision",
        "--hidden-import", "transformers",
        "--hidden-import", "geoclip",
        "--hidden-import", "shapefile",
        "--hidden-import", "httpx",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "PIL.ExifTags",

        # Imported dynamically, so PyInstaller cannot see them.
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "clr_loader",
        "--hidden-import", "timezonefinder",

        # Nothing here needs a GUI toolkit of its own; WebView2 draws the
        # window. Excluding these saves a few hundred megabytes.
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "torch.test",
        "--exclude-module", "torch.testing",
        "--exclude-module", "IPython",
        "--exclude-module", "notebook",
        "--exclude-module", "pytest",
        # The HEIC decoder is optional and not shipped: its wheels bundle
        # LGPL libraries and HEVC is covered by patents in some countries,
        # so whether it goes into a package someone else receives is the
        # owner's decision (see mapify/heif.py). Without it the built app
        # explains how to add it when given a HEIC photograph.
        "--exclude-module", "pi_heif",
        # PLONK, the second locator, judges the candidates (measured; on by
        # default). Its geoopt ships its source (build_hooks/hook-geoopt.py);
        # DINOv2's code is read by torch.hub from the models directory.
        "--hidden-import", "plonk",
        "--hidden-import", "plonk.pipe",
        *c2pa_library(),
        # Our own PyInstaller hooks (build_hooks/): kornia ships its source.
        "--additional-hooks-dir", str(ROOT / "build_hooks"),

        # Not mapify/__main__.py: PyInstaller runs the entry script as a
        # top-level module with no parent package, so its relative imports
        # cannot resolve. run_mapify.py imports absolutely.
        str(ROOT / "run_mapify.py"),
    ]
    if clean:
        args.insert(3, "--clean")

    print("  " + " ".join(args[2:8]) + " …\n")
    started = time.time()
    result = subprocess.run(args, cwd=ROOT)
    print(f"\n  PyInstaller finished in {time.time() - started:.0f}s "
          f"(exit {result.returncode})")
    return result.returncode


def c2pa_library() -> list[str]:
    """c2pa-python loads its native library from c2pa/libs at import, which
    PyInstaller cannot see. Only the DLL: the folder also holds a 283 MB
    static library and debug symbols that nothing loads."""
    import importlib.util
    spec = importlib.util.find_spec("c2pa")
    if spec is None or not spec.origin:
        return []
    dll = Path(spec.origin).parent / "libs" / "c2pa_c.dll"
    return ["--add-binary", f"{dll}{os_sep()}c2pa/libs"] if dll.is_file() else []


def os_sep() -> str:
    # PyInstaller's --add-data separator: ';' on Windows, ':' elsewhere.
    return ";" if sys.platform == "win32" else ":"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clean", action="store_true",
                    help="discard PyInstaller's cache first")
    ap.add_argument("--console", action="store_true",
                    help="build with a console window, for debugging")
    args = ap.parse_args()

    print("Mapify — building a Windows application folder\n")
    print("Preflight:")
    if not preflight():
        print("\nFix the above and re-run.")
        return 2
    print("  all present\n")

    if args.clean:
        for d in (BUILD, DIST / "Mapify"):
            if d.exists():
                print(f"  removing {d.relative_to(ROOT)}")
                shutil.rmtree(d, ignore_errors=True)

    rc = build(args.console, args.clean)
    if rc != 0:
        return rc

    out = DIST / "Mapify"
    exe = out / "Mapify.exe"
    if not exe.exists():
        print("  build reported success but Mapify.exe is not there.")
        return 1
    # --add-data copies mapify/web whole, including any test copies a
    # developer left in web/vendor/_dev (git-ignored). They are not ours to
    # ship.
    for stray in out.rglob("_dev"):
        if stray.is_dir():
            print(f"  removing test copies: {stray.relative_to(out)}")
            shutil.rmtree(stray, ignore_errors=True)

    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"\n  {exe}")
    print(f"  {size / 1e6:.0f} MB in {out.name}/")
    # A build that produces an executable which cannot import its own model
    # stack is worse than no build, and PyInstaller reports success either
    # way. Ask the binary to prove it.
    print("\n  Smoke test: importing the analysis stack inside the build…")
    try:
        probe = subprocess.run(
            [str(exe), "--selftest"], cwd=out,
            capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        print("  SMOKE TEST TIMED OUT after 15 minutes.")
        return 1
    output = (probe.stdout or "") + (probe.stderr or "")
    if probe.returncode == 0 and "SELFTEST OK" in output:
        for line in output.splitlines():
            if line.strip():
                print("    " + line.rstrip())
    else:
        print(f"  SMOKE TEST FAILED (exit {probe.returncode})")
        for line in output.strip().splitlines()[-20:]:
            print("    " + line.rstrip())
        return 1

    print("\n  Ship the whole folder, not just the .exe.")
    print("  First run downloads ~1.7 GB of model weights from Hugging Face,")
    print("  then encodes the GPS gallery once. Both are cached afterwards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
