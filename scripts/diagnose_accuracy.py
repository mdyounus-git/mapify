#!/usr/bin/env python3
"""Where does the error actually come from?

"The mapping is inaccurate" is true -- the median error is a few hundred
kilometres -- but it is not yet a diagnosis, and the fixes for the
possible causes have nothing in common. So before changing anything,
measure four numbers per photograph:

    raw top-1     the locator's own best guess, untouched
    raw oracle    the best of the locator's whole shortlist, which is
                  the ceiling any re-ranking could ever reach
    fused         what Mapify actually reports
    centroid      the probability-weighted centre of the shortlist

and the three questions they answer:

  * **Is the fusion helping?** fused against raw top-1. If the evidence
    machinery is net negative, that is a bug in this project and fixable
    today.
  * **How much is left on the table?** oracle against fused. The gap is
    what better re-ranking could win. If it is small, no amount of
    cleverness here helps and the locator is the ceiling.
  * **Is the answer even in the shortlist?** oracle against the truth.
    When it is not, nothing downstream can recover, and the only fixes
    are a better locator or independent evidence.

Usage:

    python scripts/diagnose_accuracy.py [--limit N] [--ocr] [--online]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.config import load_config                    # noqa: E402
from mapify.geo.geodesy import haversine_km              # noqa: E402
from mapify.pipeline import AnalysisEngine               # noqa: E402

SAMPLES = ROOT / "samples"


def weighted_centroid(points):
    """Mass-weighted mean direction, so it works across the antimeridian."""
    import math

    x = y = z = 0.0
    total = 0.0
    for lat, lon, w in points:
        if w <= 0:
            continue
        la, lo = math.radians(lat), math.radians(lon)
        x += w * math.cos(la) * math.cos(lo)
        y += w * math.cos(la) * math.sin(lo)
        z += w * math.sin(la)
        total += w
    if total <= 0:
        return None
    x, y, z = x / total, y / total, z / total
    return (math.degrees(math.atan2(z, math.hypot(x, y))),
            math.degrees(math.atan2(y, x)))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):     # pragma: no cover
            pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--online", action="store_true",
                    help="allow Nominatim, which enables the read-address path")
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args()

    manifest = json.loads(
        (SAMPLES / "manifest.json").read_text(encoding="utf-8")
    )
    items = manifest if isinstance(manifest, list) else manifest.get("samples", [])
    if args.limit:
        items = items[: args.limit]

    cfg = load_config()
    cfg.network.offline_only = not args.online
    cfg.models.ocr_enabled = args.ocr
    engine = AnalysisEngine(cfg)
    print(f"loading models (ocr={args.ocr}, online={args.online})…")
    engine.warmup()

    rows = []
    for i, item in enumerate(items, 1):
        path = SAMPLES / item["file"]
        if not path.is_file():
            continue
        lat, lon = item["true_lat"], item["true_lon"]
        try:
            report = engine.analyse(path.read_bytes(), path.name)
        except Exception as exc:                  # pragma: no cover
            print(f"  {item['site']}: {type(exc).__name__}: {exc}")
            continue
        if not report.ok:
            continue

        raw = [c for c in (report.raw_candidates or [])
               if c.source not in ("read-address", "ocr-toponym")]
        if not raw:
            continue
        raw_sorted = sorted(raw, key=lambda c: -c.score)

        top1 = haversine_km(raw_sorted[0].latitude, raw_sorted[0].longitude,
                            lat, lon)
        oracle = min(haversine_km(c.latitude, c.longitude, lat, lon)
                     for c in raw)
        centre = weighted_centroid(
            [(c.latitude, c.longitude, max(c.score, 0.0)) for c in raw]
        )
        cen = haversine_km(*centre, lat, lon) if centre else float("nan")

        est = (report.prediction or {}).get("estimated")
        fused = (haversine_km(est["latitude"], est["longitude"], lat, lon)
                 if est else None)

        rows.append({
            "site": item["site"], "region": item.get("region"),
            "scene": item.get("scene"),
            "top1": top1, "oracle": oracle, "centroid": cen,
            "fused": fused,
            "mass": sum(c.score for c in raw),
            "confidence": (report.prediction or {}).get("confidence"),
        })
        if i % 25 == 0:
            print(f"  {i}/{len(items)}…", flush=True)

    if not rows:
        print("nothing scored")
        return 1

    placed = [r for r in rows if r["fused"] is not None]

    def med(key, source=None):
        return statistics.median([r[key] for r in (source or placed)])

    def within(key, km, source=None):
        src = source or placed
        return sum(1 for r in src if r[key] <= km)

    n = len(placed)
    print("\n" + "=" * 72)
    print(f"{n} photographs\n")
    print(f"{'':<26}{'median km':>11}{'<=1':>6}{'<=25':>7}{'<=200':>7}{'<=2500':>8}")
    print("-" * 72)
    for label, key in (("locator top-1", "top1"),
                       ("locator shortlist best", "oracle"),
                       ("locator centroid", "centroid"),
                       ("Mapify reports", "fused")):
        print(f"{label:<26}{med(key):>11,.1f}{within(key,1):>6}"
              f"{within(key,25):>7}{within(key,200):>7}{within(key,2500):>8}")

    better = sum(1 for r in placed if r["fused"] < r["top1"] - 1)
    worse = sum(1 for r in placed if r["fused"] > r["top1"] + 1)
    print(f"\nfusion vs the locator's own top-1:")
    print(f"  better {better:>4}    worse {worse:>4}    "
          f"unchanged {n - better - worse:>4}")

    gap = [r["fused"] - r["oracle"] for r in placed]
    print(f"\nleft on the table (fused - best in shortlist):")
    print(f"  median {statistics.median(gap):>9,.1f} km")
    print(f"  the shortlist already held a better answer in "
          f"{sum(1 for g in gap if g > 1)}/{n} cases")

    hopeless = sum(1 for r in placed if r["oracle"] > 2500)
    print(f"\nthe right answer was not in the shortlist at all "
          f"(>2500 km): {hopeless}/{n}")
    print("  nothing downstream can recover those; they need a better "
          "locator\n  or evidence from outside the image model.")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2),
                                   encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
