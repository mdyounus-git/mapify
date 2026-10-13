#!/usr/bin/env python3
"""Does the street-photo memory help, and how? Re-fuses cached inputs
with each photograph's memory neighbours (from its cached embedding):

  shipped             the fusion as it ships (PLONK judge on)
  +memory cands       the memory's places added as candidates (the pipeline's
                      _remembered, models.retrieval_enabled)
  memory judge R b    each candidate x (floor + memory weight within R km) ** b
  both judges         PLONK's judge, then the memory's

    python scripts/memory_variants.py india bench
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
from mapify.models.clusters import km_between         # noqa: E402
from mapify.models.retrieval import RetrievalMemory   # noqa: E402

SETS = {"bench": ROOT / "samples", "india": ROOT / "samples" / "india"}
BANDS = (1, 25, 200, 750, 2500)


def load(name):
    folder = SETS[name]
    out = []
    for s in json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["samples"]:
        c = ROOT / "bench" / "cache"
        f, g, e = (c / "fusion" / name / f"{s['site']}.pkl", c / "plonk" / name / f"{s['site']}.npy",
                   c / "embedding" / name / f"{s['site']}.npy")
        if f.exists() and g.exists() and e.exists():
            got = pickle.loads(f.read_bytes())
            if got:
                out.append((s, got, np.load(g), np.load(e)))
    return out


def judge(cands, lat, lon, weight, radius, power, floor=0.02):
    best, best_s = None, -1.0
    for c in cands:
        share = float(weight[km_between(c.latitude, c.longitude, lat, lon) <= radius].sum())
        s = c.score * (floor + share) ** power
        if s > best_s:
            best, best_s = c, s
    return best


def main():
    geo = get_geocoder()
    geo.use_geonames = True
    fe = FusionEngine(load_config().fusion, geo)
    mem = RetrievalMemory()
    print(f"memory: {mem.size:,} photographs")
    for name in sys.argv[1:] or ["india"]:
        items = load(name)
        rows: dict[str, dict] = {}
        for s, got, pts, emb in items:
            truth = (s["true_lat"], s["true_lon"])
            err = lambda c: haversine_km(c.latitude, c.longitude, *truth)   # noqa: E731
            kw = dict(evidence=got["evidence"], ocr=got["ocr"], script_reading=got["script"],
                      read_places=got["read_places"] or None)
            shipped = fe.fuse(got["raw"], second_opinion=pts, **kw)
            if not shipped.candidates:
                continue
            rows.setdefault("shipped", {})[s["site"]] = err(shipped.candidates[0])
            nb = mem.neighbours(emb, k=50)
            places = mem.candidates(emb)
            ceiling = max((c.score for c in got["raw"]), default=0.05)
            extra = [RawCandidate(c.latitude, c.longitude, c.score * ceiling * 0.5, c.source)
                     for c in places]
            plus = fe.fuse(got["raw"], extra_candidates=extra, second_opinion=pts, **kw)
            rows.setdefault("+memory cands", {})[s["site"]] = err(plus.candidates[0])
            if nb:
                arr = np.asarray(nb)
                w = np.exp((arr[:, 2] - arr[:, 2].max()) / 0.02)
                w /= w.sum()
                for r in (25, 100, 250):
                    for b in (0.5, 1.0):
                        rows.setdefault(f"memory judge {r} {b:g}", {})[s["site"]] = err(
                            judge(shipped.candidates, arr[:, 0], arr[:, 1], w, r, b))
                rows.setdefault("nearest photo alone", {})[s["site"]] = haversine_km(
                    nb[0][0], nb[0][1], *truth)
        base = rows["shipped"]
        print(f"\n=== {name}: {len(base)} photographs ===")
        for label, errs in rows.items():
            e = list(errs.values())
            cells = "  ".join(f"<={km} {sum(x <= km for x in e) / len(e):4.0%}" for km in BANDS)
            moved = ""
            if label != "shipped":
                better = sum(1 for k in errs if errs[k] < base[k] - 1)
                worse = sum(1 for k in errs if errs[k] > base[k] + 1)
                moved = f"  ({better} better, {worse} worse)"
            print(f"{label:22} {cells}  median {statistics.median(e):7.1f}{moved}")


if __name__ == "__main__":
    main()
