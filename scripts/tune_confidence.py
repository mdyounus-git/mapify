#!/usr/bin/env python3
"""Refit the confidence thresholds, and measure the fit on data it never saw.

The 163-sample benchmark showed `high` confidence landing 15,689 km out. That
breaks the one property this project actually claims, so the thresholds have to
change.

Tuning thresholds on the same data used to report accuracy is how a tool ends
up with numbers that only describe its own test set. So this splits the samples
in two, searches on one half, and reports the chosen thresholds against the
other half, which the search never saw. The held-out number is the one worth
quoting.

    python scripts/tune_confidence.py [--json data/eval_wide.json]

It prints a configuration block; it does not write one. Changing what the tool
claims about itself should be a deliberate edit.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# What "high" promises. A high-confidence result further out than this is the
# failure the whole exercise exists to prevent.
HIGH_CONTRACT_KM = 200.0
MEDIUM_CONTRACT_KM = 1500.0


def split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Deterministic interleaved split, so a re-run gives the same answer."""
    ordered = sorted(rows, key=lambda r: r["site"])
    return ordered[0::2], ordered[1::2]


def band(row: dict, t: dict) -> str:
    """Apply a candidate threshold set to one result."""
    d = row["diagnostics"]
    mass = d.get("locator_mass", 0.0)
    conc = d.get("concentration", 0.0)
    disp = d.get("dispersion_km", 0.0)
    eviD = d.get("evidence_support", 0.0)

    if mass < t["min_mass"]:
        return "insufficient"
    if (mass >= t["high_mass"] and disp <= t["high_disp"]
            and conc >= t["high_conc"] and eviD >= t["high_evidence"]):
        return "high"
    if mass >= t["medium_mass"] and disp <= t["medium_disp"]:
        return "medium"
    if disp <= t["low_disp"]:
        return "low"
    return "insufficient"


def score(rows: list[dict], t: dict) -> dict:
    """How good is this threshold set?

    A high-confidence miss is the thing being minimised. Coverage matters too:
    a rule that never says "high" keeps its promise and is useless.
    """
    highs = [r for r in rows if band(r, t) == "high"]
    meds = [r for r in rows if band(r, t) == "medium"]

    high_bad = [r for r in highs if r["error_km"] > HIGH_CONTRACT_KM]
    med_bad = [r for r in meds if r["error_km"] > MEDIUM_CONTRACT_KM]

    # Results that were actually accurate but got no usable band: the cost of
    # being too cautious.
    accurate = [r for r in rows if r["error_km"] <= 25.0]
    missed = [r for r in accurate if band(r, t) in ("low", "insufficient")]

    return {
        "high_n": len(highs),
        "high_bad": len(high_bad),
        "high_worst": max((r["error_km"] for r in highs), default=0.0),
        "high_precision": (len(highs) - len(high_bad)) / len(highs) if highs else 0.0,
        "medium_n": len(meds),
        "medium_bad": len(med_bad),
        "missed_accurate": len(missed),
        "coverage": len(highs) / len(rows) if rows else 0.0,
    }


# The minimum share of high-confidence calls that must land within the
# contract for the band to be worth having at all.
TARGET_PRECISION = 0.95

# And the minimum number of calls, below which the band is too rare to use.
MIN_HIGH_CALLS = 12


def acceptable(s: dict) -> bool:
    """Worth considering: precise enough, and used often enough to matter."""
    return (s["high_precision"] >= TARGET_PRECISION
            and s["high_n"] >= MIN_HIGH_CALLS
            and s["medium_bad"] <= max(1, s["medium_n"] // 10))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):   # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default=str(ROOT / "data" / "eval_wide.json"))
    args = ap.parse_args()

    path = Path(args.json)
    if not path.exists():
        print(f"no results at {path}; run scripts/evaluate.py --json first",
              file=sys.stderr)
        return 2
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    tune, held = split(rows)
    print(f"{len(rows)} samples -> {len(tune)} to search on, "
          f"{len(held)} held out\n")

    grid = {
        "min_mass":       [0.02, 0.03, 0.05],
        "high_mass":      [0.15, 0.20, 0.25, 0.30],
        "high_disp":      [0.5, 5.0, 20.0, 40.0],
        "high_conc":      [0.80, 0.90, 0.999],
        "high_evidence":  [-1.0, 0.0, 0.3],
        "medium_mass":    [0.08, 0.12],
        "medium_disp":    [200.0, 400.0],
        "low_disp":       [2500.0],
    }
    keys = list(grid)
    best: tuple[tuple, dict, dict] | None = None
    considered = 0
    viable = 0

    for combo in itertools.product(*(grid[k] for k in keys)):
        t = dict(zip(keys, combo))
        considered += 1
        s = score(tune, t)
        if not acceptable(s):
            continue
        viable += 1
        # Among rules that keep the promise, prefer the one that calls "high"
        # most often and strands the fewest accurate results.
        rank = (s["high_n"], -s["missed_accurate"])
        if best is None or rank > best[0]:
            best = (rank, t, s)

    print(f"searched {considered} threshold sets, {viable} kept the promise "
          f"on the tuning half\n")

    if best is None:
        print(f"No threshold set reached {TARGET_PRECISION:.0%} precision on "
              f"at least {MIN_HIGH_CALLS} high-confidence calls.")
        print("\nThat is a finding, not a failure of the search. Here is the")
        print("best that is actually achievable, so the promise can be set to")
        print("something the data supports:\n")
        frontier: list[tuple[float, int, dict, dict]] = []
        for combo in itertools.product(*(grid[k] for k in keys)):
            t2 = dict(zip(keys, combo))
            s2 = score(tune, t2)
            if s2["high_n"] >= MIN_HIGH_CALLS:
                frontier.append((s2["high_precision"], s2["high_n"], t2, s2))
        frontier.sort(key=lambda x: (-x[0], -x[1]))
        print(f"  {'precision':>10}{'calls':>7}{'worst km':>11}   thresholds")
        seen: set[float] = set()
        for prec, n_calls, t2, s2 in frontier:
            key = round(prec, 3)
            if key in seen:
                continue
            seen.add(key)
            held2 = score(held, t2)
            print(f"  {prec:>10.0%}{n_calls:>7}{s2['high_worst']:>11,.0f}   "
                  f"mass>={t2['high_mass']} disp<={t2['high_disp']} "
                  f"conc>={t2['high_conc']} evid>={t2['high_evidence']}"
                  f"   [held out: {held2['high_precision']:.0%} of "
                  f"{held2['high_n']}]")
            if len(seen) >= 8:
                break
        return 1

    _, t, s_tune = best
    s_held = score(held, t)

    print("chosen thresholds:")
    for k in keys:
        print(f"  {k:<16} {t[k]}")

    print(f"\n{'':<20}{'tuning half':>16}{'HELD OUT':>16}")
    for label, key, fmt in [
        ("high calls", "high_n", "d"),
        ("high misses", "high_bad", "d"),
        ("high worst km", "high_worst", ",.0f"),
        ("high precision", "high_precision", ".0%"),
        ("medium calls", "medium_n", "d"),
        ("medium misses", "medium_bad", "d"),
        ("accurate but unranked", "missed_accurate", "d"),
        ("coverage", "coverage", ".0%"),
    ]:
        a = format(s_tune[key], fmt)
        b = format(s_held[key], fmt)
        print(f"  {label:<20}{a:>14}{b:>16}")

    print("\nThe held-out column is the one to believe. If high misses are")
    print("zero there and nonzero on the tuning half, the search overfitted.")

    print("\nconfig.toml:")
    print(f"  min_locator_mass     = {t['min_mass']}")
    print(f"  high_locator_mass    = {t['high_mass']}")
    print(f"  high_spread_km       = {t['high_disp']}")
    print(f"  high_concentration   = {t['high_conc']}")
    print(f"  high_min_evidence    = {t['high_evidence']}")
    print(f"  medium_locator_mass  = {t['medium_mass']}")
    print(f"  medium_spread_km     = {t['medium_disp']}")
    print(f"  insufficient_spread_km = {t['low_disp']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
