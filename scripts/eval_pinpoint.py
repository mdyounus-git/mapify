#!/usr/bin/env python3
"""How often Pinpoint finds the spot, and how close it gets.

For each benchmark photograph of a chosen scene type, Pinpoint searches
the photographs taken near its *true* location -- excluding its own copy
on Commons, which would be a trivial match -- and the result is scored:
confirmed or not, and for confirmed ones, how far the pinpointed spot is
from where the photograph was really taken.

By default this measures the matching, not the search: in use, Pinpoint
searches around the analysis's estimate, which is sometimes far from the
truth. ``--offset`` moves the search centre that far from the truth (a
different bearing per photograph) to measure the search as well.
``--names`` also searches Commons by the names of the landmarks the
photograph fits, weighed with CLIP as the application does.

    python scripts/eval_pinpoint.py [--scene landmark] [--radius 800] [--offset 0]
                                    [--names] [--panoramax]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.geo.geodesy import haversine_km             # noqa: E402
from mapify.paths import state_dir                       # noqa: E402
from mapify.pinpoint.engine import Area, PinpointEngine  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):     # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", default="landmark")
    ap.add_argument("--radius", type=int, default=800)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=float, default=0.0,
                    help="metres between the search centre and the truth")
    ap.add_argument("--budget", type=int, default=0,
                    help="photographs to compare at most (default: the engine's)")
    ap.add_argument("--names", action="store_true",
                    help="also search by the names of landmarks the photograph fits "
                         "(loads CLIP from the local model cache)")
    ap.add_argument("--panoramax", action="store_true",
                    help="also ask Panoramax for street pictures, as the app does")
    ap.add_argument("--sites", default="",
                    help="comma-separated sample sites to run (default: all of the scene)")
    args = ap.parse_args()

    items = [i for i in json.loads((ROOT / "samples" / "manifest.json")
                                   .read_text(encoding="utf-8"))["samples"]
             if args.scene in ("all", i.get("scene"))]
    for n, it in enumerate(items):
        it["_n"] = n                  # a stable bearing, whatever is filtered out
    if args.sites:
        keep = set(args.sites.split(","))
        items = [i for i in items if i["site"] in keep]
    if args.limit:
        items = items[:args.limit]
    engine = PinpointEngine(state_dir() / "pinpoint", panoramax=args.panoramax)
    clip = None
    if args.names:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")      # the weights already here
        from mapify.paths import apply_model_cache_env
        apply_model_cache_env()
        from mapify.models.clip_backend import ClipBackend
        from mapify.pinpoint.engine import clip_namer
        clip = ClipBackend()
        if not clip.load():
            print("CLIP could not be loaded:", clip.load_error)
            return 1
    rows = []
    for it in items:
        t0 = time.time()
        bearing = math.radians(it["_n"] * 137.508)           # the golden angle
        lat = it["true_lat"] + args.offset * math.cos(bearing) / 111320.0
        lon = it["true_lon"] + args.offset * math.sin(bearing) / (
            111320.0 * max(0.2, math.cos(math.radians(it["true_lat"]))))
        with Image.open(ROOT / "samples" / it["file"]) as img:
            img.load()
            res = engine.run(img, [Area(lat, lon, args.radius, it["site"])],
                             exclude={it.get("commons_title", "")},
                             namer=clip_namer(clip, img) if clip is not None else None,
                             **({"budget": args.budget} if args.budget else {}))
        best = res.get("best") or {}
        err = None
        if res["location"]:
            err = haversine_km(res["location"]["lat"], res["location"]["lon"],
                               it["true_lat"], it["true_lon"]) * 1000
        rows.append((it["site"], res["verdict"], (best.get("match") or {}).get("inliers", 0),
                     err, best.get("title", "")))
        print(f"{it['site']:<16} {res['verdict']:<10} "
              f"{(best.get('match') or {}).get('inliers', 0):>4} inliers  "
              f"{'' if err is None else f'{err:>6.0f} m off'}  "
              f"{res['searched']['references']:>3} refs  {time.time() - t0:>4.0f}s  "
              f"{res['searched'].get('listed', 0):>4} listed  "
              f"{best.get('title', '')[:50]}", flush=True)
        for note in res.get("notes") or []:
            print(f"{'':<16} note: {note}", flush=True)
        for area in res["searched"].get("areas") or []:
            for named in area.get("named") or []:
                print(f"{'':<16} named: {named['name']} -- {named['found']} found, "
                      f"{named['not_listed_otherwise']} not listed by distance", flush=True)
    hits = [r for r in rows if r[1] == "pinpointed"]
    print(f"\n{len(hits)} of {len(rows)} pinpointed"
          + (f"; median error {sorted(r[3] for r in hits)[len(hits) // 2]:.0f} m"
             if hits else "")
          + f"; {sum(r[1] == 'possible' for r in rows)} possible, "
            f"{sum(r[1] == 'none' for r in rows)} none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
