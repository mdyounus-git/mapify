#!/usr/bin/env python3
"""PLONK's samples for every photograph of a set, cached.

    python scripts/plonk_cache.py india bench

Writes bench/cache/plonk/<set>/<site>.npy (N x 2 lat, lon), skipping what is
there, so it resumes. The analysis's own image preparation is used (EXIF
orientation, the long-side cap), so PLONK sees what GeoCLIP sees.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.config import load_config                 # noqa: E402
from mapify.models.plonk_locator import PlonkLocator  # noqa: E402
from mapify.pipeline import AnalysisEngine            # noqa: E402

SETS = {"bench": ROOT / "samples", "india": ROOT / "samples" / "india",
        "text": ROOT / "samples" / "text"}


def main():
    from mapify.paths import apply_model_cache_env
    apply_model_cache_env()                 # the weights live in the models directory
    cfg = load_config()
    plonk = PlonkLocator(samples=64, steps=16)
    side = cfg.models.max_image_side
    for name in sys.argv[1:] or ["india"]:
        folder = SETS[name]
        out = ROOT / "bench" / "cache" / "plonk" / name
        out.mkdir(parents=True, exist_ok=True)
        samples = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["samples"]
        t0 = time.time()
        for i, s in enumerate(samples, 1):
            dest = out / f"{s['site']}.npy"
            src = folder / s["file"]
            if dest.exists() or not src.is_file():
                continue
            try:
                with Image.open(src) as im:
                    image = AnalysisEngine._prepare(im, side)
                pts = plonk.sample(image)
            except Exception as exc:
                print(f"  {s['site']}: {type(exc).__name__}: {exc}", flush=True)
                continue
            tmp = dest.with_suffix(".tmp.npy")
            np.save(tmp, pts.astype(np.float32))
            tmp.replace(dest)
            if i % 10 == 0:
                print(f"  {name}: {i}/{len(samples)} ({time.time() - t0:.0f} s)", flush=True)
        print(f"{name}: done in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
