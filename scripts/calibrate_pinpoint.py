#!/usr/bin/env python3
"""Where to draw the line between "same scene" and "lookalike".

Pinpoint adopts a reference photograph's location only when the
geometric match clears a threshold. That threshold is measured, not
chosen:

  * **negatives** -- benchmark photographs from different places (more
    than 50 km apart) cannot show the same scene, so the best inlier
    count any of those pairs reaches is the ceiling that chance and
    repeating texture achieve. The threshold must sit well above it.
  * **positives** -- each benchmark photograph against a warped,
    recompressed, relit copy of itself: a floor for what a true match
    looks like (real second photographs of a scene score lower than
    this, and well above the negatives when they match at all).

    python scripts/calibrate_pinpoint.py [--pairs 400] [--matcher lightglue]

``--matcher lightglue`` measures the second matcher (DISK + LightGlue,
mapify/pinpoint/deep_match.py) the same way; its thresholds are its own.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.geo.geodesy import haversine_km                   # noqa: E402
from mapify.pinpoint.features import (QUERY_PIXELS, QUERY_SIDE,  # noqa: E402
                                      REFERENCE_SIDE, REFINE_SIDE, extract, match)


def _warp(img: Image.Image, rng: random.Random) -> Image.Image:
    """A second look at the same scene: perspective, scale, light, JPEG."""
    import cv2
    a = np.asarray(img.convert("RGB"))
    h, w = a.shape[:2]
    d = 0.12
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[rng.uniform(0, d) * w, rng.uniform(0, d) * h],
                      [w - rng.uniform(0, d) * w, rng.uniform(0, d) * h],
                      [w - rng.uniform(0, d) * w, h - rng.uniform(0, d) * h],
                      [rng.uniform(0, d) * w, h - rng.uniform(0, d) * h]])
    warped = cv2.warpPerspective(a, cv2.getPerspectiveTransform(src, dst), (w, h))
    out = ImageEnhance.Brightness(Image.fromarray(warped)).enhance(rng.uniform(0.75, 1.25))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=70)
    return Image.open(io.BytesIO(buf.getvalue()))


def _pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", type=int, default=400, help="negative pairs to try")
    ap.add_argument("--positives", type=int, default=60)
    ap.add_argument("--matcher", choices=("sift", "lightglue"), default="sift")
    args = ap.parse_args()
    if args.matcher == "lightglue":
        return _lightglue(args)

    items = _items()
    rng = random.Random(7)
    print(f"{len(items)} benchmark photographs; extracting features…", flush=True)
    # Exactly the engine's settings: the query by area with 6000 points,
    # references at both passes' sizes, the better of the two counts.
    q_feats, r_feats, r2_feats = {}, {}, {}
    for it in items:
        img = Image.open(ROOT / "samples" / it["file"])
        q_feats[it["site"]] = extract(img, QUERY_SIDE, QUERY_PIXELS, nfeatures=6000)
        r_feats[it["site"]] = extract(img, REFERENCE_SIDE)
        r2_feats[it["site"]] = extract(img, REFINE_SIDE)

    negatives = []
    while len(negatives) < args.pairs:
        a, b = rng.sample(items, 2)
        if haversine_km(a["true_lat"], a["true_lon"], b["true_lat"], b["true_lon"]) < 50:
            continue
        m = max(match(q_feats[a["site"]], r_feats[b["site"]]),
                match(q_feats[a["site"]], r2_feats[b["site"]]), key=lambda x: x.inliers)
        negatives.append((m.inliers, m.homography_inliers, a["site"], b["site"]))

    positives = []
    for it in rng.sample(items, min(args.positives, len(items))):
        img = Image.open(ROOT / "samples" / it["file"])
        ref = extract(_warp(img, rng).resize(
            (REFERENCE_SIDE, int(REFERENCE_SIDE * img.height / img.width))), REFERENCE_SIDE)
        m = match(q_feats[it["site"]], ref)
        positives.append((m.inliers, m.homography_inliers, it["site"]))

    _report(negatives, positives)
    return 0


def _items():
    manifest = json.loads((ROOT / "samples" / "manifest.json").read_text(encoding="utf-8"))
    return [i for i in manifest["samples"] if (ROOT / "samples" / i["file"]).is_file()]


def _lightglue(args) -> int:
    """The same negatives and positives through DISK + LightGlue: one
    feature set per photograph, each pair matched once."""
    from mapify.paths import apply_model_cache_env
    apply_model_cache_env()
    from mapify.pinpoint.deep_match import DeepMatcher
    dm = DeepMatcher()
    items = _items()
    rng = random.Random(7)
    print(f"{len(items)} benchmark photographs; DISK features…", flush=True)
    feats = {it["site"]: dm.features(Image.open(ROOT / "samples" / it["file"]))
             for it in items}
    negatives = []
    while len(negatives) < args.pairs:
        a, b = rng.sample(items, 2)
        if haversine_km(a["true_lat"], a["true_lon"], b["true_lat"], b["true_lon"]) < 50:
            continue
        m = dm.match_features(feats[a["site"]], feats[b["site"]])
        negatives.append((m.inliers, m.homography_inliers, a["site"], b["site"]))
        if len(negatives) % 50 == 0:
            print(f"  {len(negatives)} pairs", flush=True)
    positives = []
    for it in rng.sample(items, min(args.positives, len(items))):
        img = Image.open(ROOT / "samples" / it["file"])
        m = dm.match_features(feats[it["site"]], dm.features(_warp(img, rng)))
        positives.append((m.inliers, m.homography_inliers, it["site"]))
    _report(negatives, positives)
    return 0


def _report(negatives, positives) -> None:
    ni = [n[0] for n in negatives]
    pi = [p[0] for p in positives]
    print(f"\nNEGATIVES ({len(ni)} pairs of different places)")
    print(f"  inliers: median {statistics.median(ni):.0f}, p90 {_pct(ni, .9)}, "
          f"p99 {_pct(ni, .99)}, max {max(ni)}")
    worst = sorted(negatives, reverse=True)[:5]
    print("  worst:", ", ".join(f"{a}~{b} {i}" for i, _h, a, b in worst))
    print(f"\nPOSITIVES ({len(pi)} photographs against a warped copy)")
    print(f"  inliers: min {min(pi)}, p10 {_pct(pi, .1)}, median {statistics.median(pi):.0f}")
    print(f"  homography inliers: min {min(p[1] for p in positives)}")


if __name__ == "__main__":
    raise SystemExit(main())
