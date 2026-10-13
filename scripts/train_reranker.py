#!/usr/bin/env python3
"""Train the re-ranker (mapify/evidence/rerank.py) and measure it.

    python scripts/train_reranker.py [--memory] [--save]

Training material: bench/cache/rerank/osv/*.pkl (scripts/rerank_data.py),
photographs no benchmark holds. Each is fused as the application fuses it --
GeoNames on, the twelve leading candidates kept -- and the model learns, by
a listwise softmax over each photograph's candidates, to put the one nearest
the truth first.

Measured three ways, always against the fusion as it ships (with PLONK as a
judge, models.plonk_enabled):

  * five-fold cross-validation on the training photographs, folds by country
    so no country is both learnt and tested;
  * the 46 India photographs and the 161 world benchmark photographs, from
    their cached inputs (bench/fusion_variants.py, bench/plonk_cache.py),
    which the model never sees.

``--memory`` adds the street-photo memory's features: every photograph must
then carry its CLIP embedding, and the memory must be built. ``--save``
writes the weights to the models directory (models/reranker/weights.json)
with the measurements beside them.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pickle
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.config import load_config                          # noqa: E402
from mapify.evidence.fusion import FusionEngine                # noqa: E402
from mapify.evidence.rerank import FEATURES, candidate_features, default_path  # noqa: E402
from mapify.geo.geodesy import haversine_km                    # noqa: E402
from mapify.geo.offline import get_geocoder                    # noqa: E402

KEEP = 12
BANDS = (1, 25, 200, 750, 2500)


def _fuse(fe, inputs, plonk=None):
    return fe.fuse(inputs["raw"], evidence=inputs["evidence"], ocr=inputs["ocr"],
                   script_reading=inputs["script"], read_places=inputs["read_places"] or None,
                   second_opinion=plonk)


def build_rows(items, fe, memory=None):
    """(features, errors_km, shipped_error_km, group) per photograph."""
    out = []
    for item in items:
        inputs, truth, plonk = item["inputs"], item["truth"], item["plonk"]
        fused = _fuse(fe, inputs)                     # the candidates, before the judge
        if not fused.candidates:
            continue
        shipped = _fuse(fe, inputs, plonk)            # as the application answers today
        nb = None
        if memory is not None and item.get("embedding") is not None:
            nb = memory.neighbours(np.asarray(item["embedding"], dtype=np.float32), k=50)
        feats = candidate_features(fused.candidates, plonk, nb)
        errs = np.array([haversine_km(c.latitude, c.longitude, *truth) for c in fused.candidates])
        ship = haversine_km(shipped.candidates[0].latitude, shipped.candidates[0].longitude, *truth)
        out.append({"x": feats, "err": errs, "shipped": ship, "group": item["group"],
                    "name": item["name"]})
    return out


def train(rows, l2=0.05, steps=3000, lr=0.05):
    x_all = np.concatenate([r["x"] for r in rows])
    mean = x_all.mean(axis=0)
    std = x_all.std(axis=0)
    std[std < 1e-9] = 1.0
    zs = [(r["x"] - mean) / std for r in rows]
    ys = [int(np.argmin(r["err"])) for r in rows]
    w = np.zeros(len(FEATURES))
    m = np.zeros_like(w)
    v = np.zeros_like(w)
    for t in range(1, steps + 1):
        g = l2 * w
        for z, y in zip(zs, ys):
            s = z @ w
            p = np.exp(s - s.max())
            p /= p.sum()
            g += (p @ z - z[y]) / len(zs)
        m = 0.9 * m + 0.1 * g
        v = 0.999 * v + 0.001 * g * g
        w -= lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
    return {"features": list(FEATURES), "mean": mean.tolist(), "std": std.tolist(),
            "weights": w.tolist(), "bias": 0.0}


def pick(model, x):
    z = (x - np.asarray(model["mean"])) / np.asarray(model["std"])
    return int(np.argmax(z @ np.asarray(model["weights"])))


def bands(errs):
    e = list(errs)
    return {f"<={km}": sum(x <= km for x in e) / len(e) for km in BANDS} | \
        {"median_km": statistics.median(e), "n": len(e)}


def line(label, b):
    cells = "  ".join(f"{k} {v:4.0%}" for k, v in b.items() if k.startswith("<="))
    return f"{label:28} n={b['n']:4}  {cells}  median {b['median_km']:7.1f}"


def compare(model, rows, label):
    chosen = [r["err"][pick(model, r["x"])] for r in rows]
    shipped = [r["shipped"] for r in rows]
    first = [r["err"][0] for r in rows]
    better = sum(1 for c, s in zip(chosen, shipped) if c < s - 1)
    worse = sum(1 for c, s in zip(chosen, shipped) if c > s + 1)
    print(line(f"{label}: fusion alone", bands(first)))
    print(line(f"{label}: as shipped (judge)", bands(shipped)))
    print(line(f"{label}: re-ranked", bands(chosen)) + f"  ({better} better, {worse} worse)")
    return {"fusion": bands(first), "shipped": bands(shipped), "reranked": bands(chosen),
            "better": better, "worse": worse}


def load_training():
    items = []
    for f in sorted((ROOT / "bench" / "cache" / "rerank" / "osv").glob("*.pkl")):
        r = pickle.loads(f.read_bytes())
        if r.get("inputs"):
            items.append({"inputs": r["inputs"], "truth": tuple(r["truth"]), "plonk": r["plonk"],
                          "embedding": r.get("embedding"), "group": r["country"],
                          "name": r["id"]})
    return items


def load_benchmark(name, folder):
    items = []
    for s in json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["samples"]:
        f = ROOT / "bench" / "cache" / "fusion" / name / f"{s['site']}.pkl"
        g = ROOT / "bench" / "cache" / "plonk" / name / f"{s['site']}.npy"
        e = ROOT / "bench" / "cache" / "embedding" / name / f"{s['site']}.npy"
        if not (f.exists() and g.exists()):
            continue
        got = pickle.loads(f.read_bytes())
        if not got:
            continue
        items.append({"inputs": got, "truth": (s["true_lat"], s["true_lon"]), "plonk": np.load(g),
                      "embedding": np.load(e) if e.exists() else None, "group": s["site"],
                      "name": s["site"]})
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--memory", action="store_true", help="use the street-photo memory")
    ap.add_argument("--save", action="store_true", help="write the weights")
    ap.add_argument("--l2", type=float, default=0.05)
    args = ap.parse_args()

    geo = get_geocoder()
    geo.use_geonames = True
    cfg = copy.deepcopy(load_config().fusion)
    cfg.candidates_returned = KEEP
    fe = FusionEngine(cfg, geo)
    memory = None
    if args.memory:
        from mapify.models.retrieval import RetrievalMemory
        memory = RetrievalMemory()
        if not memory.available():
            print("the street-photo memory is not built")
            return 1

    t0 = time.time()
    rows = build_rows(load_training(), fe, memory)
    print(f"{len(rows)} training photographs ({time.time() - t0:.0f} s)")
    if len(rows) < 50:
        print("too few to train on")
        return 1

    # Cross-validation, folds by country.
    folds = [[] for _ in range(5)]
    for r in rows:
        folds[int(hashlib.sha1(r["group"].encode()).hexdigest()[:6], 16) % 5].append(r)
    cv_chosen, cv_shipped = [], []
    for k in range(5):
        model = train([r for j, f in enumerate(folds) if j != k for r in f], l2=args.l2)
        for r in folds[k]:
            cv_chosen.append(r["err"][pick(model, r["x"])])
            cv_shipped.append(r["shipped"])
    print(line("training CV: as shipped", bands(cv_shipped)))
    print(line("training CV: re-ranked", bands(cv_chosen)))

    model = train(rows, l2=args.l2)
    report = {"trained_on": len(rows), "memory": bool(args.memory),
              "cv": {"shipped": bands(cv_shipped), "reranked": bands(cv_chosen)}}
    for name, folder in (("india", ROOT / "samples" / "india"), ("bench", ROOT / "samples")):
        items = load_benchmark(name, folder)
        if args.memory and any(i["embedding"] is None for i in items):
            print(f"{name}: embeddings missing (bench/embedding_cache.py); skipped")
            continue
        report[name] = compare(model, build_rows(items, fe, memory), name)
    print("weights:", {f: round(w, 3) for f, w in zip(FEATURES, model["weights"])})
    if args.save:
        path = default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**model, "measured": report}, indent=1), encoding="utf-8")
        print("saved", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
