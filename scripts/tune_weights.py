#!/usr/bin/env python3
"""Fit the evidence weights to the benchmark, on a held-out split.

The diagnosis that produced this script: across 163 photographs the
locator's own top guess is 594 km out at the median, Mapify's answer is
342, and **the best candidate already in the locator's shortlist is 50**.
The right answer is usually in the list. We are choosing the wrong one,
in 102 of 163 cases.

So the ranking is the bottleneck, not the locator, and the ranking is
six numbers: how much each kind of evidence may move a candidate. Those
six were set by judgement and never measured. Phase 13 did exactly this
for the confidence thresholds and found the judgement had been badly
wrong; there is no reason to expect better here.

Method, which is Phase 13's:

  * dump every candidate of every sample once, with each piece of
    evidence recorded as a *unit* contribution -- the agreement term
    with the weight divided back out -- so the search can re-weight
    offline without re-running a single model;
  * search the weights on one half of the samples;
  * report the result on the other half, which is the only number that
    means anything.

Splitting by index parity rather than at random, so a re-run compares
against the same halves.

    python scripts/tune_weights.py --dump          # once, ~4 minutes
    python scripts/tune_weights.py --search
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.config import load_config                    # noqa: E402
from mapify.geo.geodesy import haversine_km              # noqa: E402
from mapify.pipeline import AnalysisEngine               # noqa: E402

SAMPLES = ROOT / "samples"
DUMP = ROOT / "bench" / "weight-features.json"

# Which weight each adjustment source was scaled by, so the unit
# contribution can be recovered and re-scaled.
SOURCE_WEIGHT = {
    "country_prior": "w_region_hint",
    "toponym": "w_toponym",
    "script": "w_script",
    "language": "w_script",
}

TUNABLE = ("w_driving_side", "w_script", "w_toponym", "w_climate",
           "w_plate", "w_region_hint")


def dump(limit: int = 0, ocr: bool = False) -> int:
    """Run every sample once and record what the fusion had to work with."""
    manifest = json.loads(
        (SAMPLES / "manifest.json").read_text(encoding="utf-8")
    )
    items = manifest if isinstance(manifest, list) else manifest.get("samples", [])
    if limit:
        items = items[:limit]

    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.ocr_enabled = ocr
    engine = AnalysisEngine(cfg)
    print("loading models…")
    engine.warmup()

    weights = {k: getattr(cfg.fusion, k) for k in TUNABLE}
    out = []
    for i, item in enumerate(items, 1):
        path = SAMPLES / item["file"]
        if not path.is_file():
            continue
        try:
            report = engine.analyse(path.read_bytes(), path.name)
        except Exception as exc:                  # pragma: no cover
            print(f"  {item['site']}: {type(exc).__name__}: {exc}")
            continue
        if not report.ok or not report.candidates:
            continue

        cands = []
        for c in report.candidates:
            units: dict[str, float] = {}
            for adj in c.get("adjustments", []):
                key = SOURCE_WEIGHT.get(adj["source"])
                if key is None:
                    # A visual group; recover which weight it used by
                    # looking the group up in the prompt bank.
                    from mapify.evidence import prompts as P
                    group = P.GROUPS_BY_KEY.get(adj["source"])
                    key = group.weight_key if group else None
                if key is None or weights.get(key, 0) <= 0:
                    continue
                units[key] = units.get(key, 0.0) + adj["delta"] / weights[key]
            cands.append({
                "lat": c["latitude"], "lon": c["longitude"],
                "prior": max(c["prior_score"], 1e-12),
                "units": units,
            })

        out.append({
            "site": item["site"],
            "true": [item["true_lat"], item["true_lon"]],
            "candidates": cands,
        })
        if i % 25 == 0:
            print(f"  {i}/{len(items)}…", flush=True)

    DUMP.parent.mkdir(parents=True, exist_ok=True)
    DUMP.write_text(json.dumps(out), encoding="utf-8")
    print(f"\nwrote {DUMP}  ({len(out)} samples)")
    return 0


def score(samples, weights, cap: float) -> tuple[float, int, int]:
    """Median error, and counts within 25 km and 200 km, for one weighting."""
    errors = []
    for s in samples:
        best = None
        best_score = -1.0
        for c in s["candidates"]:
            total = sum(weights.get(k, 0.0) * v for k, v in c["units"].items())
            total = max(-cap, min(cap, total))
            value = c["prior"] * math.exp(total)
            if value > best_score:
                best_score, best = value, c
        if best is None:
            continue
        errors.append(
            haversine_km(best["lat"], best["lon"], s["true"][0], s["true"][1])
        )
    if not errors:
        return float("inf"), 0, 0
    return (statistics.median(errors),
            sum(1 for e in errors if e <= 25),
            sum(1 for e in errors if e <= 200))


def search(args) -> int:
    if not DUMP.is_file():
        print(f"no {DUMP}; run --dump first")
        return 2
    samples = json.loads(DUMP.read_text(encoding="utf-8"))
    fit = [s for i, s in enumerate(samples) if i % 2 == 0]
    held = [s for i, s in enumerate(samples) if i % 2 == 1]
    print(f"{len(samples)} samples: {len(fit)} to fit on, {len(held)} held out\n")

    cfg = load_config().fusion
    current = {k: getattr(cfg, k) for k in TUNABLE}
    cap = cfg.max_total_adjustment

    for label, subset in (("fit", fit), ("held out", held)):
        m, a, b = score(subset, current, cap)
        print(f"current weights, {label:<9} median {m:>8,.1f} km   "
              f"<=25km {a:>3}/{len(subset)}   <=200km {b:>3}/{len(subset)}")

    # A coarse grid. The point is not a perfect optimum -- it is to find
    # out whether the hand-chosen numbers were anywhere near right, and
    # six dimensions of fine grid would be a fitting exercise on 81
    # samples, which is how you get a number that does not survive.
    grid = {
        "w_driving_side": (0.0, 0.9, 1.8),
        "w_script": (0.0, 1.1, 2.2),
        "w_toponym": (0.8, 1.6, 2.4),
        "w_climate": (0.0, 0.55, 1.1),
        "w_plate": (0.0, 0.45, 0.9),
        "w_region_hint": (0.0, 0.5, 1.5, 3.0),
    }
    keys = list(grid)
    best = None
    total = 1
    for k in keys:
        total *= len(grid[k])
    print(f"\nsearching {total:,} combinations on the fit half…")

    for i, combo in enumerate(itertools.product(*(grid[k] for k in keys))):
        w = dict(zip(keys, combo))
        m, a, b = score(fit, w, cap)
        # Rank on within-25km first: the median is dominated by the
        # samples nothing can save, and moving one of those from 9,000
        # to 8,000 km is not an improvement anybody can use.
        key = (-a, -b, m)
        if best is None or key < best[0]:
            best = (key, w, m, a, b)
        if (i + 1) % 200 == 0:
            print(f"  {i + 1:,}/{total:,}…", flush=True)

    _, w, m, a, b = best
    print("\nbest on the fit half")
    for k in keys:
        arrow = "" if abs(w[k] - current[k]) < 1e-9 else \
            f"   (was {current[k]:g})"
    for k in keys:
        mark = " " if abs(w[k] - current[k]) < 1e-9 else "*"
        print(f"  {mark} {k:<18} {w[k]:>5g}    was {current[k]:g}")
    print(f"    median {m:,.1f} km   <=25km {a}/{len(fit)}   "
          f"<=200km {b}/{len(fit)}")

    hm, ha, hb = score(held, w, cap)
    cm, ca, cb = score(held, current, cap)
    print("\nHELD OUT -- the only numbers that mean anything")
    print(f"  current  median {cm:>8,.1f} km   <=25km {ca:>3}/{len(held)}   "
          f"<=200km {cb:>3}/{len(held)}")
    print(f"  fitted   median {hm:>8,.1f} km   <=25km {ha:>3}/{len(held)}   "
          f"<=200km {hb:>3}/{len(held)}")

    verdict = ("KEEP -- it holds up out of sample" if ha > ca
               else "REJECT -- it does not survive the held-out half"
               if ha < ca else "no better out of sample; keep what exists")
    print(f"\n  {verdict}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):     # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--search", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ocr", action="store_true")
    args = ap.parse_args()
    if args.dump:
        return dump(args.limit, args.ocr)
    if args.search:
        return search(args)
    ap.error("choose --dump or --search")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
