#!/usr/bin/env python3
"""GeoCLIP's CLIP embedding for every photograph of a set, cached,
so the street-photo memory's neighbours can be looked up for photographs
whose fusion inputs are already cached.

    python scripts/embedding_cache.py india bench
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SETS = {"bench": ROOT / "samples", "india": ROOT / "samples" / "india",
        "text": ROOT / "samples" / "text"}


def main():
    from mapify.paths import apply_model_cache_env
    apply_model_cache_env()
    from mapify.config import load_config
    from mapify.models.geoclip_locator import GeoClipLocator
    from mapify.pipeline import AnalysisEngine
    side = load_config().models.max_image_side
    loc = GeoClipLocator()
    if not loc.load():
        raise SystemExit(f"GeoCLIP did not load: {loc.load_error}")
    for name in sys.argv[1:] or ["india"]:
        folder = SETS[name]
        out = ROOT / "bench" / "cache" / "embedding" / name
        out.mkdir(parents=True, exist_ok=True)
        for s in json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["samples"]:
            dest = out / f"{s['site']}.npy"
            src = folder / s["file"]
            if dest.exists() or not src.is_file():
                continue
            with Image.open(src) as im:
                image = AnalysisEngine._prepare(im, side)
            _cands, emb = loc.locate_modes(image)
            np.save(dest, np.asarray(emb, dtype=np.float32).ravel())
        print(name, "done", flush=True)


if __name__ == "__main__":
    main()
