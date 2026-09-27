#!/usr/bin/env python3
"""Compare two benchmark runs, and say whether a change earned its place.

Phase 22 set a rule for adding evidence: every new group is measured on
the benchmark and kept or deleted on that basis. That rule needs an
instrument, because "the median went down a bit" is not a decision and
two runs printed side by side invite reading whichever number flatters
the change.

So this reports the three things that can actually settle it:

  **Did it move the errors?** Median and the per-band counts, plus how
  many individual samples got better or worse and by how much. A change
  that improves the median by moving two samples and damaging thirty is
  not an improvement, and only the paired view shows that.

  **Did it break the promise?** The high band is the one commitment this
  tool makes. Any run where a high-confidence call lands outside its
  contract is a regression regardless of what the median did, and this
  says so in those words.

  **Where did it act?** Per region and per scene, because the headline
  hides the thing most worth knowing: whether a change helped everywhere
  a little or helped landmarks and hurt everything else.

Usage:

    python scripts/compare_bench.py bench/baseline.json bench/after.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# Matches evaluate.py. Only "high" makes a distance promise.
HIGH_CONTRACT_KM = 200.0
BANDS = [("street", 1.0), ("city", 25.0), ("region", 200.0),
         ("country", 750.0), ("continent", 2500.0)]

# Below this a per-cell figure is noise dressed as a measurement.
MIN_CELL = 8
# A sample has to move by more than this to count as having moved at all;
# below it the difference is scoring jitter, not an effect.
NOISE_KM = 1.0


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def by_site(run: dict) -> dict[str, dict]:
    return {r["site"]: r for r in run.get("rows", [])}


def band_of(km: float) -> str:
    for name, limit in BANDS:
        if km <= limit:
            return name
    return "off"


def summarise(label: str, rows: list[dict]) -> None:
    errs = sorted(r["error_km"] for r in rows)
    if not errs:
        print(f"{label}: nothing scored")
        return
    within = lambda k: sum(e <= k for e in errs)          # noqa: E731
    print(f"{label:<10} n={len(errs):<4} "
          f"median={statistics.median(errs):>9,.1f} km   "
          f"<=1km {within(1):>3}   <=25km {within(25):>3}   "
          f"<=200km {within(200):>3}   <=2500km {within(2500):>3}")


def promise_check(label: str, rows: list[dict]) -> int:
    """How many high-confidence calls broke their contract."""
    high = [r for r in rows if r["confidence"] == "high"]
    broken = [r for r in high if r["error_km"] > HIGH_CONTRACT_KM]
    state = "kept" if not broken else f"BROKEN by {len(broken)}"
    print(f"  {label:<10} high calls: {len(high):>3}   contract {state}")
    for r in broken[:5]:
        print(f"      {r['site']:<22} {r['error_km']:>10,.0f} km  "
              f"({r['expected']})")
    return len(broken)


def paired(before: dict[str, dict], after: dict[str, dict]) -> None:
    shared = sorted(set(before) & set(after))
    if not shared:
        print("\nNo sites in common; these runs cannot be paired.")
        return

    deltas = [(s, after[s]["error_km"] - before[s]["error_km"]) for s in shared]
    better = [(s, d) for s, d in deltas if d < -NOISE_KM]
    worse = [(s, d) for s, d in deltas if d > NOISE_KM]
    same = len(deltas) - len(better) - len(worse)

    print(f"\nPaired over {len(shared)} shared samples")
    print(f"  better {len(better):>3}    worse {len(worse):>3}    "
          f"unchanged {same:>3}   (beyond +/-{NOISE_KM:g} km)")

    if better:
        print("  biggest improvements")
        for s, d in sorted(better, key=lambda x: x[1])[:6]:
            print(f"      {s:<22} {d:>12,.0f} km   "
                  f"{before[s]['error_km']:,.0f} -> {after[s]['error_km']:,.0f}")
    if worse:
        print("  biggest regressions")
        for s, d in sorted(worse, key=lambda x: -x[1])[:6]:
            print(f"      {s:<22} {d:>+12,.0f} km   "
                  f"{before[s]['error_km']:,.0f} -> {after[s]['error_km']:,.0f}")

    # The sign test: how surprising is this split if the change did nothing?
    n = len(better) + len(worse)
    if n >= 10:
        k = min(len(better), len(worse))
        # Two-sided sign test, exact.
        from math import comb
        p = 2 * sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
        verdict = ("not distinguishable from chance" if p > 0.05
                   else "unlikely to be chance")
        print(f"  sign test over {n} moved samples: p={min(1.0, p):.3f} "
              f"-- {verdict}")
    else:
        print(f"  only {n} samples moved: too few to test")


def breakdown(key: str, before: dict[str, dict], after: dict[str, dict]) -> None:
    groups: dict[str, list[float]] = defaultdict(list)
    for site in set(before) & set(after):
        g = before[site].get(key) or "unknown"
        groups[g].append(after[site]["error_km"] - before[site]["error_km"])

    rows = [(g, d) for g, d in groups.items() if len(d) >= MIN_CELL]
    if not rows:
        print(f"\nBy {key}: every cell below n={MIN_CELL}, not reported")
        return
    print(f"\nBy {key} (median change, cells of at least {MIN_CELL})")
    for g, ds in sorted(rows, key=lambda x: statistics.median(x[1])):
        med = statistics.median(ds)
        arrow = "better" if med < -NOISE_KM else ("worse" if med > NOISE_KM
                                                  else "flat")
        print(f"  {g:<18} n={len(ds):<4} {med:>+10,.1f} km   {arrow}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):      # pragma: no cover
            pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    args = ap.parse_args()

    for p in (args.before, args.after):
        if not p.is_file():
            print(f"missing: {p}")
            return 2

    a, b = load(args.before), load(args.after)
    ra, rb = a.get("rows", []), b.get("rows", [])

    print("=" * 78)
    print(f"before : {args.before}   ({a.get('generated')})")
    print(f"after  : {args.after}   ({b.get('generated')})")
    print("=" * 78)
    summarise("before", ra)
    summarise("after", rb)

    print("\nThe one promise")
    broken_before = promise_check("before", ra)
    broken_after = promise_check("after", rb)

    paired(by_site(a), by_site(b))
    breakdown("region", by_site(a), by_site(b))
    breakdown("scene", by_site(a), by_site(b))

    print("\n" + "-" * 78)
    if broken_after > broken_before:
        print("VERDICT: reject. The high band broke its contract more often "
              "than before, and no median improvement buys that back.")
        return 1
    print("VERDICT: read the paired counts and the sign test above. A median "
          "that improved while more samples got worse than better is not an "
          "improvement; it is one lucky sample.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
