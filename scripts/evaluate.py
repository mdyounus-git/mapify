#!/usr/bin/env python3
"""Run the pipeline over ``samples/`` and report how far off it was.

This exists to keep the tool honest about its own accuracy. It prints the
great-circle error for every sample, the standard geolocation accuracy bands
(street / city / region / country / continent), and -- the part that actually
matters -- whether the confidence the tool reported was justified.

A "high" confidence result that is 3,000 km out is a worse failure than an
"insufficient" one, and this harness is what makes that visible.

    python scripts/evaluate.py [--offline] [--ocr] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLES = ROOT / "samples"

# The bands the geolocation literature reports against.
BANDS = [
    ("street", 1.0),
    ("city", 25.0),
    ("region", 200.0),
    ("country", 750.0),
    ("continent", 2500.0),
]


def band_of(km: float) -> str:
    for name, limit in BANDS:
        if km <= limit:
            return name
    return "off-target"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="no outbound requests (faster, Natural Earth naming)")
    ap.add_argument("--ocr", action="store_true", help="enable OCR")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", type=str, default=None, help="write results here")
    args = ap.parse_args()

    logging.basicConfig(level=logging.ERROR)

    manifest_path = SAMPLES / "manifest.json"
    if not manifest_path.exists():
        print("No samples. Run: python scripts/fetch_samples.py", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples = manifest["samples"]
    if args.limit:
        samples = samples[: args.limit]

    from mapify.config import load_config
    from mapify.geo.geodesy import haversine_km
    from mapify.pipeline import AnalysisEngine

    cfg = load_config()
    cfg.network.offline_only = args.offline
    cfg.models.ocr_enabled = args.ocr

    print("Loading models...", flush=True)
    t0 = time.time()
    engine = AnalysisEngine(cfg)
    engine.warmup()
    print(f"ready in {time.time() - t0:.0f}s\n")

    header = (
        f"{'sample':<11} {'error km':>9}  {'band':<10} {'confidence':<13} "
        f"{'mass':>6} {'conc':>5} {'spread':>7}  predicted"
    )
    print(header)
    print("-" * len(header))

    rows = []
    for s in samples:
        path = SAMPLES / s["file"]
        if not path.exists():
            continue
        data = path.read_bytes()
        t = time.time()
        report = engine.analyse(data, s["file"])
        elapsed = time.time() - t

        if not report.ok:
            print(f"{s['site']:<11} {'ERROR':>9}  {report.error}")
            rows.append({**s, "error": report.error})
            continue

        pred = report.prediction
        est = pred.get("estimated") or {}
        diag = pred.get("diagnostics") or {}
        if est:
            km = haversine_km(
                s["true_lat"], s["true_lon"], est["latitude"], est["longitude"]
            )
        else:
            km = float("inf")

        place = est.get("place") or {}
        name = ", ".join(
            x for x in (place.get("city"), place.get("region"), place.get("country"))
            if x
        ) or "-"

        print(
            f"{s['site']:<11} {km:9.1f}  {band_of(km):<10} "
            f"{pred['confidence']:<13} "
            f"{diag.get('locator_mass', 0):6.3f} "
            f"{diag.get('concentration', 0):5.2f} "
            f"{diag.get('dispersion_km', 0):7.0f}  {name[:46]}"
        )
        rows.append({
            "site": s["site"],
            "expected": s["expected_label"],
            "true": [s["true_lat"], s["true_lon"]],
            "predicted": [est.get("latitude"), est.get("longitude")],
            "error_km": round(km, 2),
            "band": band_of(km),
            "confidence": pred["confidence"],
            "confidence_reason": pred["confidence_reason"],
            "diagnostics": diag,
            "place": name,
            "elapsed_s": round(elapsed, 1),
            "called_evidence": [
                f"{r['title']}={r['label']} ({r['confidence']:.0%})"
                for r in report.evidence.get("readings", []) if r["called"]
            ],
        })

    # ---- summary ---------------------------------------------------------
    scored = [r for r in rows if r.get("error_km") is not None and "error" not in r]
    if not scored:
        print("\nNothing scored.")
        return 1

    print()
    counts: dict[str, int] = {}
    for name, limit in BANDS:
        counts[name] = sum(1 for r in scored if r["error_km"] <= limit)
    n = len(scored)
    print(f"Accuracy over {n} samples (cumulative):")
    for name, limit in BANDS:
        share = counts[name] / n
        bar = "#" * int(round(share * 28))
        print(f"  {name:<10} <= {limit:6.0f} km  {counts[name]:2d}/{n}  "
              f"{share:5.0%} {bar}")

    errors = sorted(r["error_km"] for r in scored)
    median = errors[len(errors) // 2]
    print(f"  median error {median:,.1f} km")

    # The honesty check: was the stated confidence earned?
    print("\nCalibration -- median error by stated confidence:")
    for level in ("high", "medium", "low", "insufficient"):
        group = [r["error_km"] for r in scored if r["confidence"] == level]
        if not group:
            continue
        group.sort()
        med = group[len(group) // 2]
        worst = max(group)
        flag = ""
        if level == "high" and worst > 200:
            flag = "  <-- OVERCONFIDENT"
        elif level == "medium" and worst > 1500:
            flag = "  <-- overconfident"
        print(f"  {level:<13} n={len(group):2d}  median {med:8.1f} km  "
              f"worst {worst:8.1f} km{flag}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"rows": rows, "bands": counts, "n": n,
                        "median_error_km": median}, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
