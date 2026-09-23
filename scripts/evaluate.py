#!/usr/bin/env python3
"""Run the pipeline over ``samples/`` and report how far off it was.

This exists to keep the tool honest about its own accuracy, so it is written to
make overconfidence visible rather than to produce a flattering headline.

Three things it does that a plain accuracy script would not:

  * **Refuses to report a figure for a band with too few samples.** A
    calibration table with n=1 cells is not a measurement, and printing
    "medium: median 190 km" from a single observation invites exactly the
    false confidence this tool is built to avoid.
  * **Reports a reliability table** — stated confidence against observed
    accuracy — and names a band OVERCONFIDENT when it breaks its own promise.
  * **Breaks results out by region and scene**, because the headline number
    hides GeoCLIP's known weakness on tropical and Sub-Saharan scenes, and
    because landmarks are the easy case.

    python scripts/evaluate.py [--offline] [--ocr] [--limit N] [--json PATH]
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

# Below this, a per-band figure is reported as unmeasured rather than printed.
MIN_BAND_N = 10

CONFIDENCE_ORDER = ["high", "medium", "low", "insufficient"]

# Only "high" makes a promise. Threshold fitting showed that "medium" tops
# out around 85% within 1500 km on this data, so promising a distance for it
# would be a claim the evidence does not support. It reports its measured rate
# instead, and the UI wording matches.
BAND_CONTRACT_KM: dict[str, float | None] = {
    "high": 200.0, "medium": None, "low": None, "insufficient": None,
}


def band_of(km: float) -> str:
    for name, limit in BANDS:
        if km <= limit:
            return name
    return "off-target"


def median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return float("nan")
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def bar(share: float, width: int = 26) -> str:
    return "#" * int(round(share * width))


def group_table(rows: list[dict], key: str, title: str) -> None:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get(key) or "?", []).append(r)
    if len(groups) <= 1:
        return
    print(f"\n{title}")
    print(f"  {'':<16} {'n':>4} {'<=25km':>8} {'<=200km':>9} {'median':>11}")
    for name, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        errs = [g["error_km"] for g in group]
        city = sum(1 for e in errs if e <= 25.0) / len(errs)
        region = sum(1 for e in errs if e <= 200.0) / len(errs)
        med = f"{median(errs):,.1f} km" if len(errs) >= 3 else "n<3"
        print(f"  {name:<16} {len(group):>4} {city:>7.0%} {region:>8.0%} "
              f"{med:>11}")


def reliability(rows: list[dict]) -> None:
    """Stated confidence against what actually happened.

    The claim this project makes is not "it is accurate". It is "when it says
    high, believe it; when it says insufficient, do not". This is the table
    that supports or refutes that, and it is allowed to say the measurement
    has not been made.
    """
    print("\nReliability — is the stated confidence earned?")
    print(f"  {'stated':<14} {'n':>4} {'median':>12} {'worst':>13} "
          f"{'<=200km':>8}  verdict")
    thin = False
    for level in CONFIDENCE_ORDER:
        group = [r["error_km"] for r in rows if r["confidence"] == level]
        if not group:
            continue
        n = len(group)
        worst = max(group)
        within = sum(1 for e in group if e <= 200.0) / n
        within_wide = sum(1 for e in group if e <= 1500.0) / n

        if n < MIN_BAND_N:
            verdict = f"unmeasured, n<{MIN_BAND_N}"
            thin = True
        else:
            contract = BAND_CONTRACT_KM.get(level)
            if contract is None:
                # No promise to break; report what it did so the wording in
                # the UI can be checked against it.
                verdict = f"no promise — {within_wide:.0%} within 1500 km"
            elif worst > contract:
                verdict = f"OVERCONFIDENT — worst {worst:,.0f} km"
            elif within >= 0.95:
                verdict = f"ok — {n} of {n} within {contract:,.0f} km"
            else:
                verdict = f"weak — only {within:.0%} within {contract:,.0f} km"

        med_s = f"{median(group):,.1f} km" if n >= 3 else "—"
        print(f"  {level:<14} {n:>4} {med_s:>12} {worst:>10,.0f} km "
              f"{within:>7.0%} {within_wide:>8.0%}  {verdict}")

    if thin:
        print(f"\n  Bands with fewer than {MIN_BAND_N} samples are reported as")
        print("  unmeasured. One observation is not a calibration.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="no outbound requests (faster, Natural Earth naming)")
    ap.add_argument("--ocr", action="store_true", help="enable OCR")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--quiet", action="store_true",
                    help="summary only, no per-sample lines")
    args = ap.parse_args()

    logging.basicConfig(level=logging.ERROR)

    manifest_path = SAMPLES / "manifest.json"
    if not manifest_path.exists():
        print("No samples. Run: python scripts/fetch_samples.py",
              file=sys.stderr)
        return 2
    samples = json.loads(manifest_path.read_text(encoding="utf-8"))["samples"]
    if args.limit:
        samples = samples[: args.limit]

    from mapify.config import load_config
    from mapify.geo.geodesy import haversine_km
    from mapify.pipeline import AnalysisEngine

    cfg = load_config()
    cfg.network.offline_only = args.offline
    cfg.models.ocr_enabled = args.ocr

    print(f"Loading models (offline={args.offline}, ocr={args.ocr})…",
          flush=True)
    t0 = time.time()
    engine = AnalysisEngine(cfg)
    engine.warmup()
    print(f"ready in {time.time() - t0:.0f}s, {len(samples)} samples\n")

    if not args.quiet:
        header = (f"{'sample':<18}{'error km':>10}  {'band':<11}"
                  f"{'confidence':<14}{'mass':>6}{'conc':>6}{'spread':>8}  "
                  f"predicted")
        print(header)
        print("-" * min(len(header), 118))

    rows: list[dict] = []
    failures: list[str] = []
    started = time.time()

    for i, s in enumerate(samples, 1):
        path = SAMPLES / s["file"]
        if not path.exists():
            continue
        try:
            report = engine.analyse(path.read_bytes(), s["file"])
        except Exception as exc:
            failures.append(f"{s['site']}: {type(exc).__name__}: {exc}")
            continue
        if not report.ok:
            failures.append(f"{s['site']}: {report.error}")
            continue

        pred = report.prediction
        est = pred.get("estimated") or {}
        diag = pred.get("diagnostics") or {}
        if not est:
            failures.append(f"{s['site']}: no estimate produced")
            continue

        km = haversine_km(s["true_lat"], s["true_lon"],
                          est["latitude"], est["longitude"])
        place = est.get("place") or {}
        name = ", ".join(x for x in (place.get("city"), place.get("country"))
                         if x) or "-"

        if not args.quiet:
            print(f"{s['site']:<18}{km:>10.1f}  {band_of(km):<11}"
                  f"{pred['confidence']:<14}"
                  f"{diag.get('locator_mass', 0):>6.3f}"
                  f"{diag.get('concentration', 0):>6.2f}"
                  f"{diag.get('dispersion_km', 0):>8.0f}  {name[:36]}",
                  flush=True)
        elif i % 25 == 0:
            print(f"  {i}/{len(samples)}…", flush=True)

        rows.append({
            "site": s["site"],
            "region": s.get("region"),
            "scene": s.get("scene"),
            "expected": s["expected_label"],
            "true": [s["true_lat"], s["true_lon"]],
            "predicted": [est["latitude"], est["longitude"]],
            "error_km": round(km, 2),
            "band": band_of(km),
            "confidence": pred["confidence"],
            "diagnostics": diag,
            "place": name,
            "ocr_lines": (report.ocr or {}).get("line_count", 0),
        })

    if not rows:
        print("\nNothing scored.")
        for f in failures[:10]:
            print("  ", f)
        return 1

    elapsed = time.time() - started
    n = len(rows)
    errs = [r["error_km"] for r in rows]

    print(f"\nAccuracy over {n} samples (cumulative), "
          f"{elapsed / n:.1f}s per image:")
    counts: dict[str, int] = {}
    for name, limit in BANDS:
        counts[name] = sum(1 for e in errs if e <= limit)
        share = counts[name] / n
        print(f"  {name:<10} <= {limit:6.0f} km  {counts[name]:3d}/{n}  "
              f"{share:5.0%} {bar(share)}")
    print(f"  median error {median(errs):,.1f} km")

    reliability(rows)
    group_table(rows, "region", "By region — the headline hides the weakness")
    group_table(rows, "scene", "By scene — landmarks are the easy case")

    if failures:
        print(f"\n{len(failures)} samples failed to analyse:")
        for f in failures[:8]:
            print("  ", f)

    if args.json:
        Path(args.json).write_text(json.dumps({
            "generated": time.strftime("%Y-%m-%d %H:%M"),
            "offline": args.offline,
            "ocr": args.ocr,
            "n": n,
            "median_error_km": round(median(errs), 2),
            "bands": counts,
            "seconds_per_image": round(elapsed / n, 2),
            "rows": rows,
            "failures": failures,
        }, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
