"""Run real photographs through the analysis and report every technique.

For checking the technique pass end to end: which techniques ran, which
found something, which were skipped and why, which failed, and what each
cost. A technique that raises shows up here as "FAILED" with its error,
which is the point -- run_all contains failures, so nothing else would
show them.

    python scripts/run_techniques.py samples/bengaluru.jpg samples/london-bridge.jpg
    python scripts/run_techniques.py --offline --phase 8 samples/tokyo-shibuya.jpg
    python scripts/run_techniques.py --found-only samples/mumbai.jpg
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--offline", action="store_true",
                    help="no network: skips geocoding, weather and elevation")
    ap.add_argument("--phase", type=int, action="append",
                    help="only list techniques of this phase (repeatable)")
    ap.add_argument("--found-only", action="store_true",
                    help="only list techniques that found something")
    args = ap.parse_args()

    logging.basicConfig(level=logging.ERROR)
    from mapify.config import load_config
    from mapify.pipeline import AnalysisEngine

    cfg = load_config()
    cfg.network.offline_only = args.offline
    t0 = time.time()
    engine = AnalysisEngine(cfg)
    engine.warmup()
    print(f"models ready in {time.time() - t0:.0f}s\n")

    failures = 0
    for path in args.images:
        started = time.time()
        report = engine.analyse(path.read_bytes(), path.name,
                                online_enrichment=not args.offline)
        total = time.time() - started
        if not report.ok:
            print(f"== {path.name}: analysis failed: {report.error}")
            failures += 1
            continue
        found = [f for f in report.techniques if f["found"]]
        failed = [f for f in report.techniques
                  if f["skipped_because"] == "the technique raised"]
        failures += len(failed)
        by_phase: dict[int, float] = defaultdict(float)
        for f in report.techniques:
            by_phase[f["phase"]] += f["elapsed_ms"]
        print(f"== {path.name}: {total:.1f}s total, "
              f"{sum(by_phase.values()) / 1000:.1f}s in techniques; "
              f"{len(report.techniques)} techniques, {len(found)} found something, "
              f"{len(failed)} failed")
        print("   per phase (s): " + "  ".join(
            f"{p}:{ms / 1000:.2f}" for p, ms in sorted(by_phase.items())))
        for f in report.techniques:
            if args.phase and f["phase"] not in args.phase:
                continue
            if args.found_only and not f["found"]:
                continue
            state = ("FAILED" if f in failed else "found " if f["found"] else
                     "ran   " if f["ran"] else "n/a   ")
            print(f"   {f['technique']:>3} {state} {f['elapsed_ms']:>7.0f}ms  "
                  f"{f['name'][:44]:<44} {f['summary'][:110]}")
        print()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
