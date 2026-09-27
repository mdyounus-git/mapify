#!/usr/bin/env python3
"""Sweep the mode-finding parameters in one pass over the samples.

Each full benchmark run costs six minutes, and there are two parameters
worth varying, so measuring them one run at a time is an afternoon. The
expensive part -- the vision tower and the 100,000-point softmax -- does
not depend on either parameter, so it can be done once per photograph
and every combination evaluated against the same distribution.

This measures the *locator* only: the error of its best proposal, before
any evidence re-ranking. That is the right thing to tune here, because
the fusion sits downstream of it and is unchanged by these parameters.

    radius   how far apart two places must be to count as different,
             and how wide the mass for a place is gathered
    focus    how far from the peak the reported coordinate is averaged,
             which trades precision against sampling noise

    python scripts/sweep_modes.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.config import load_config                    # noqa: E402
from mapify.geo.geodesy import haversine_km              # noqa: E402
from mapify.pipeline import AnalysisEngine               # noqa: E402

SAMPLES = ROOT / "samples"

RADII = (40.0, 75.0, 150.0)
FOCI = (0.0, 5.0, 15.0, 40.0)


def modes_from(probs, coords, lat, lon, cos_lat, radius_km, focus_km,
               top_k=3, seeds=300):
    """The same algorithm as GeoClipLocator.locate_modes, offline."""
    order = np.argsort(probs)[::-1][:seeds]
    remaining = probs.copy()
    out = []
    for _ in range(top_k):
        best_sum, best_mask = 0.0, None
        for idx in order:
            if remaining[idx] <= 0:
                continue
            sin_dlat = np.sin((lat - lat[idx]) / 2.0)
            sin_dlon = np.sin((lon - lon[idx]) / 2.0)
            a = sin_dlat ** 2 + cos_lat[idx] * cos_lat * sin_dlon ** 2
            dist = 2.0 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
            mask = dist <= radius_km
            total = float(remaining[mask].sum())
            if total > best_sum:
                best_sum, best_mask = total, mask
        if best_mask is None or best_sum <= 0:
            break

        peak = int(np.argmax(np.where(best_mask, remaining, -1.0)))
        if focus_km <= 0:
            out.append((float(coords[peak][0]), float(coords[peak][1]),
                        best_sum))
        else:
            sin_dlat = np.sin((lat - lat[peak]) / 2.0)
            sin_dlon = np.sin((lon - lon[peak]) / 2.0)
            a = sin_dlat ** 2 + cos_lat[peak] * cos_lat * sin_dlon ** 2
            near = 2.0 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
            focus = best_mask & (near <= focus_km)
            if not focus.any():
                focus = best_mask
            w = remaining[focus]
            pts = coords[focus]
            rlat, rlon = np.radians(pts[:, 0]), np.radians(pts[:, 1])
            x = float((w * np.cos(rlat) * np.cos(rlon)).sum())
            y = float((w * np.cos(rlat) * np.sin(rlon)).sum())
            z = float((w * np.sin(rlat)).sum())
            n = math.sqrt(x * x + y * y + z * z)
            if n <= 0:
                break
            x, y, z = x / n, y / n, z / n
            out.append((math.degrees(math.atan2(z, math.hypot(x, y))),
                        math.degrees(math.atan2(y, x)), best_sum))
        remaining[best_mask] = 0.0
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):     # pragma: no cover
            pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    manifest = json.loads(
        (SAMPLES / "manifest.json").read_text(encoding="utf-8")
    )
    items = manifest if isinstance(manifest, list) else manifest.get("samples", [])
    if args.limit:
        items = items[: args.limit]

    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.ocr_enabled = False
    engine = AnalysisEngine(cfg)
    print("loading models…")
    engine.warmup()
    loc = engine.geoclip

    coords = loc.gallery_coords()
    lat = np.radians(coords[:, 0])
    lon = np.radians(coords[:, 1])
    cos_lat = np.cos(lat)

    # errors[(radius, focus)] -> list, plus the point-wise baseline
    errors: dict[tuple, list[float]] = {(r, f): [] for r in RADII for f in FOCI}
    errors[("top-1 point", "")] = []

    from PIL import Image

    for i, item in enumerate(items, 1):
        path = SAMPLES / item["file"]
        if not path.is_file():
            continue
        tlat, tlon = item["true_lat"], item["true_lon"]
        try:
            img = Image.open(path).convert("RGB")
            probs = loc.distribution(img)
        except Exception as exc:                 # pragma: no cover
            print(f"  {item['site']}: {type(exc).__name__}: {exc}")
            continue
        if probs is None:
            continue
        probs = probs.astype(np.float64)

        peak = int(np.argmax(probs))
        errors[("top-1 point", "")].append(
            haversine_km(float(coords[peak][0]), float(coords[peak][1]),
                         tlat, tlon)
        )
        for r in RADII:
            for f in FOCI:
                got = modes_from(probs, coords, lat, lon, cos_lat, r, f,
                                 top_k=1)
                if got:
                    errors[(r, f)].append(
                        haversine_km(got[0][0], got[0][1], tlat, tlon)
                    )
        if i % 20 == 0:
            print(f"  {i}/{len(items)}…", flush=True)

    print("\n" + "=" * 68)
    print("locator's best proposal, before any evidence re-ranking\n")
    print(f"{'radius':>8}{'focus':>8}{'median km':>12}{'<=1':>6}{'<=25':>7}"
          f"{'<=200':>7}{'<=2500':>8}")
    print("-" * 68)

    def row(label_a, label_b, errs):
        if not errs:
            return
        print(f"{label_a:>8}{label_b:>8}{statistics.median(errs):>12,.1f}"
              f"{sum(1 for e in errs if e <= 1):>6}"
              f"{sum(1 for e in errs if e <= 25):>7}"
              f"{sum(1 for e in errs if e <= 200):>7}"
              f"{sum(1 for e in errs if e <= 2500):>8}")

    row("point", "-", errors[("top-1 point", "")])
    for r in RADII:
        for f in FOCI:
            row(f"{r:g}", f"{f:g}", errors[(r, f)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
