#!/usr/bin/env python3
"""Does PLONK help, and how should it be used? Re-fuses cached
inputs (bench/cache/fusion/<set>) with PLONK's cached samples
(bench/cache/plonk/<set>) several ways:

  as is           the current fusion (working tree), GeoNames on
  +plonk cands    PLONK's places added as candidates, as the pipeline does
                  with models.plonk_enabled
  judge R b       every fused candidate's score multiplied by
                  (eps + share of PLONK samples within R km) ** b -- two
                  independent models agreeing, a product of experts
  plonk alone     PLONK's own densest place

    python scripts/plonk_variants.py india bench
"""
from __future__ import annotations

import json
import math
import pickle
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.config import load_config                 # noqa: E402
from mapify.evidence.fusion import FusionEngine       # noqa: E402
from mapify.geo.geodesy import haversine_km           # noqa: E402
from mapify.geo.offline import get_geocoder           # noqa: E402
from mapify.models.base import RawCandidate           # noqa: E402
from mapify.models.clusters import km_between, places  # noqa: E402

SETS = {"bench": ROOT / "samples", "india": ROOT / "samples" / "india"}
BANDS = (1, 25, 200, 750, 2500)


def load(name):
    folder = SETS[name]
    out = []
    for s in json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["samples"]:
        f = ROOT / "bench" / "cache" / "fusion" / name / f"{s['site']}.pkl"
        g = ROOT / "bench" / "cache" / "plonk" / name / f"{s['site']}.npy"
        if f.exists() and g.exists():
            got = pickle.loads(f.read_bytes())
            if got:
                out.append((s, got, np.load(g)))
    return out


def fuse(fe, got, extra=None):
    return fe.fuse(got["raw"], evidence=got["evidence"], ocr=got["ocr"],
                   script_reading=got["script"], extra_candidates=extra,
                   read_places=got["read_places"] or None)


def judged(cands, pts, radius_km, power, eps=0.02):
    best, best_score = None, -1.0
    for c in cands:
        share = float((km_between(c.latitude, c.longitude, pts[:, 0], pts[:, 1]) <= radius_km).mean())
        score = c.score * (eps + share) ** power
        if score > best_score:
            best, best_score = c, score
    return best


def report(name, rows):
    base = rows["as is"]
    print(f"\n=== {name}: {len(base)} photographs ===")
    for label, errs in rows.items():
        e = list(errs.values())
        cells = "  ".join(f"<={km} {sum(x <= km for x in e) / len(e):4.0%}" for km in BANDS)
        moved = ""
        if label != "as is":
            better = sum(1 for k in errs if errs[k] < base[k] - 1)
            worse = sum(1 for k in errs if errs[k] > base[k] + 1)
            moved = f"  ({better} better, {worse} worse)"
        print(f"{label:18} {cells}  median {statistics.median(e):7.1f}{moved}")


def main():
    geo = get_geocoder()
    geo.use_geonames = True
    fe = FusionEngine(load_config().fusion, geo)
    for name in sys.argv[1:] or ["india"]:
        items = load(name)
        if not items:
            print(f"{name}: nothing cached yet")
            continue
        rows = {k: {} for k in ("as is", "+plonk cands", "judge 100 0.5", "judge 100 0.75",
                                "judge 250 0.75", "judge 100 1",
                                "judge 250 0.5", "judge 250 1", "judge 100 2",
                                "union judge 100 1", "union judge 250 1",
                                "plonk + evidence", "mix 1:1", "mix 1:1 judge 100 1",
                                "plonk alone")}
        for s, got, pts in items:
            truth = (s["true_lat"], s["true_lon"])
            err = lambda c: haversine_km(c.latitude, c.longitude, *truth)   # noqa: E731
            f0 = fuse(fe, got)
            if not f0.candidates:
                continue
            rows["as is"][s["site"]] = err(f0.candidates[0])
            ceiling = max((c.score for c in got["raw"]), default=0.05)
            pcs = places(pts[:, 0], pts[:, 1], np.ones(len(pts)), "plonk", top=5)
            extra = [RawCandidate(c.latitude, c.longitude, c.score * ceiling, "plonk") for c in pcs]
            f1 = fuse(fe, got, extra)
            rows["+plonk cands"][s["site"]] = err(f1.candidates[0])
            for r in (100, 250):
                for b in (0.5, 0.75, 1):
                    rows[f"judge {r} {b:g}"][s["site"]] = err(judged(f0.candidates, pts, r, b))
            rows["judge 100 2"][s["site"]] = err(judged(f0.candidates, pts, 100, 2))
            for r in (100, 250):
                rows[f"union judge {r} 1"][s["site"]] = err(judged(f1.candidates, pts, r, 1))
            plonk_raw = [RawCandidate(c.latitude, c.longitude, c.score, "plonk") for c in pcs]
            f2 = fe.fuse(plonk_raw, evidence=got["evidence"], ocr=got["ocr"],
                         script_reading=got["script"], read_places=got["read_places"] or None)
            rows["plonk + evidence"][s["site"]] = err(f2.candidates[0]) if f2.candidates else math.inf
            # Equal footing: each model's places normalised to its own total.
            g_total = sum(c.score for c in got["raw"] if c.source == "geoclip") or 1.0
            mixed = [RawCandidate(c.latitude, c.longitude, c.score / g_total, c.source)
                     for c in got["raw"]] + [RawCandidate(c.latitude, c.longitude, c.score, "plonk")
                                             for c in pcs]
            f3 = fe.fuse(mixed, evidence=got["evidence"], ocr=got["ocr"],
                         script_reading=got["script"], read_places=got["read_places"] or None)
            rows["mix 1:1"][s["site"]] = err(f3.candidates[0]) if f3.candidates else math.inf
            rows["mix 1:1 judge 100 1"][s["site"]] = err(judged(f3.candidates, pts, 100, 1))
            rows["plonk alone"][s["site"]] = err(pcs[0]) if pcs else math.inf
        report(name, rows)


if __name__ == "__main__":
    main()
