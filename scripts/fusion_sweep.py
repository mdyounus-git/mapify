#!/usr/bin/env python3
"""Re-fuse cached inputs (bench/cache/fusion/<set>) under variations of
FusionConfig, with the working tree's fusion and GeoNames on. No analysis is
re-run, so each variation over a set takes seconds.

    python scripts/fusion_sweep.py india [bench]
"""
from __future__ import annotations

import copy
import json
import pickle
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.config import load_config                          # noqa: E402
from mapify.evidence.fusion import FusionEngine                # noqa: E402
from mapify.geo.geodesy import haversine_km                    # noqa: E402
from mapify.geo.offline import get_geocoder                    # noqa: E402

SETS = {"bench": ROOT / "samples", "india": ROOT / "samples" / "india"}

VARIANTS = {"as is": {}}
for a in (0.25, 0.5, 0.75, 1.0):
    VARIANTS[f"density a={a}"] = {"_alpha": a}

_GALLERY = None


def gallery():
    global _GALLERY
    if _GALLERY is None:
        import numpy as np
        import pandas as pd
        f = ROOT / ".venv/Lib/site-packages/geoclip/model/gps_gallery/coordinates_100K.csv"
        g = pd.read_csv(f).to_numpy(dtype=float)
        _GALLERY = np.radians(g[:, 0]), np.radians(g[:, 1])
    return _GALLERY


def support(lat, lon, radius_km=40.0):
    """Gallery points within the mode radius of a coordinate."""
    import numpy as np
    glat, glon = gallery()
    la, lo = np.radians(lat), np.radians(lon)
    a = np.sin((glat - la) / 2) ** 2 + np.cos(la) * np.cos(glat) * np.sin((glon - lo) / 2) ** 2
    return max(1, int((2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0, 1))) <= radius_km).sum()))


def debias(raw, alpha):
    import dataclasses
    out = []
    for c in raw:
        if c.source == "geoclip" and alpha:
            n = support(c.latitude, c.longitude)
            c = dataclasses.replace(c, score=c.score / n ** alpha)
        out.append(c)
    return out


def load_set(name):
    folder = SETS[name]
    samples = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["samples"]
    out = []
    for s in samples:
        p = ROOT / "bench" / "cache" / "fusion" / name / f"{s['site']}.pkl"
        if p.exists():
            got = pickle.loads(p.read_bytes())
            if got:
                out.append((s, got))
    return out


def score(items, cfg, geo, alpha=0.0):
    fe = FusionEngine(cfg, geo)
    errs = {}
    for s, got in items:
        raw = debias(got["raw"], alpha) if alpha else got["raw"]
        fused = fe.fuse(raw, evidence=got["evidence"], ocr=got["ocr"],
                        script_reading=got["script"], read_places=got["read_places"] or None)
        if fused.candidates:
            t = fused.candidates[0]
            errs[s["site"]] = haversine_km(t.latitude, t.longitude, s["true_lat"], s["true_lon"])
    return errs


def main():
    base_cfg = load_config().fusion
    geo = get_geocoder()
    geo.use_geonames = True
    for name in sys.argv[1:] or ["india"]:
        items = load_set(name)
        print(f"\n=== {name}: {len(items)} cached ===")
        ref = None
        for label, change in VARIANTS.items():
            cfg = copy.deepcopy(base_cfg)
            alpha = change.get("_alpha", 0.0)
            for k, v in change.items():
                if not k.startswith("_"):
                    setattr(cfg, k, v)
            errs = score(items, cfg, geo, alpha)
            e = list(errs.values())
            line = "  ".join(f"<={km} {sum(x <= km for x in e) / len(e):4.0%}" for km in (1, 25, 200, 750, 2500))
            moved = ""
            if ref is None:
                ref = errs
            else:
                better = sum(1 for k in errs if errs[k] < ref[k] - 1)
                worse = sum(1 for k in errs if errs[k] > ref[k] + 1)
                moved = f"  ({better} better, {worse} worse)"
            print(f"{label:16} n={len(e)}  {line}  median {statistics.median(e):7.1f}{moved}")


if __name__ == "__main__":
    main()
