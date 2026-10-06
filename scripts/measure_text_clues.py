#!/usr/bin/env python3
"""Measure whether clues read off the image as text should steer the answer.

A calling code, a ".in" web address, a rupee sign, a postcode, miles on
a speed sign: techniques 110-124 report them. Whether they should also
*move the coordinate* is a different question with an answer that only
measurement can give -- Phase 22 built four plausible clues, measured
them, and found them inert or harmful. This is the same test for text.

Method (tune_weights.py's, which is Phase 13's):

  * run every benchmark photograph once, with OCR, and record for each
    candidate its prior, the sum of its other adjustments and its
    country, and for the photograph the text clues read off it;
  * re-score offline: adding w x strength x agreement per narrow clue,
    capped as the fusion caps it, costs nothing per weight;
  * choose w on the even-numbered samples, report on the odd ones -- the
    held-out half is the only number that means anything.

    python scripts/measure_text_clues.py --dump      # ~1.5-2 h with OCR on a CPU
    python scripts/measure_text_clues.py --search

The dump is written after every photograph and resumes where it stopped,
so an interrupted run loses nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLES = ROOT / "samples"
DUMP = ROOT / "bench" / "text-clue-features.jsonl"


def _manifest() -> list[dict]:
    raw = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else raw.get("samples", [])


def _done() -> set[str]:
    if not DUMP.is_file():
        return set()
    return {json.loads(l)["site"] for l in DUMP.read_text(encoding="utf-8").splitlines()
            if l.strip()}


def dump(limit: int = 0) -> int:
    import logging
    logging.basicConfig(level=logging.ERROR)
    from mapify.config import load_config
    from mapify.evidence.text_clues import read_clues
    from mapify.pipeline import AnalysisEngine

    items = _manifest()[: limit or None]
    done = _done()
    todo = [i for i in items if i["site"] not in done and (SAMPLES / i["file"]).is_file()]
    print(f"{len(items)} samples, {len(done)} already dumped, {len(todo)} to go")
    if not todo:
        return 0

    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.ocr_enabled = True
    cfg.records.case_files = False
    cfg.fusion.w_text_clue = 0.0          # measure the current answer, not a new one
    engine = AnalysisEngine(cfg)
    # The numbered techniques describe; they do not decide. Skipping them
    # here saves seconds per photograph and changes no candidate.
    engine._run_techniques = lambda **kw: []
    print("loading models…", flush=True)
    engine.warmup()

    DUMP.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with DUMP.open("a", encoding="utf-8") as out:
        for n, item in enumerate(todo, 1):
            path = SAMPLES / item["file"]
            try:
                report = engine.analyse(path.read_bytes(), path.name).as_dict()
            except Exception as exc:                      # pragma: no cover
                print(f"  {item['site']}: {type(exc).__name__}: {exc}", flush=True)
                continue
            lines = [SimpleNamespace(**l) for l in (report.get("ocr") or {}).get("lines", [])]
            clues = read_clues(lines)
            cands = []
            for c in report.get("candidates") or []:
                loc = c.get("location") or {}
                country = loc.get("country") or {}
                cands.append({
                    "lat": c["latitude"], "lon": c["longitude"],
                    "prior": max(c.get("prior_score", 0.0), 1e-12),
                    "adj": sum(a["delta"] for a in c.get("adjustments", [])),
                    "iso": country.get("iso_a2"),
                })
            row = {"site": item["site"], "true": [item["true_lat"], item["true_lon"]],
                   "ocr_lines": len(lines), "clues": [c.as_dict() for c in clues],
                   "candidates": cands,
                   "locatable": (report.get("prediction") or {}).get("locatable", True)}
            out.write(json.dumps(row) + "\n")
            out.flush()
            rate = (time.time() - started) / n
            print(f"  {n}/{len(todo)} {item['site']:<22} {len(lines):>3} lines, "
                  f"{len(clues)} clue(s)   ~{rate * (len(todo) - n) / 60:.0f} min left",
                  flush=True)
    print(f"\nwrote {DUMP}")
    return 0


# ------------------------------------------------------------------ search

def _text_unit(clues: list[dict], iso: str | None) -> float:
    """Sum of strength x agreement over the narrow clues, as the fusion adds it."""
    total = 0.0
    for c in clues:
        if not c["narrow"] or not iso:
            continue
        total += c["strength"] * (1.0 if iso in c["countries"] else -0.6)
    return total


def _top(sample: dict, w: float, cap: float) -> dict | None:
    best, best_score = None, -1.0
    for c in sample["candidates"]:
        total = max(-cap, min(cap, c["adj"] + w * _text_unit(sample["clues"], c["iso"])))
        value = c["prior"] * math.exp(total)
        if value > best_score:
            best, best_score = c, value
    return best


def _errors(samples: list[dict], w: float, cap: float) -> list[float]:
    from mapify.geo.geodesy import haversine_km
    out = []
    for s in samples:
        top = _top(s, w, cap)
        if top is not None:
            out.append(haversine_km(top["lat"], top["lon"], s["true"][0], s["true"][1]))
    return out


def _summary(errors: list[float]) -> tuple[float, int, int]:
    if not errors:
        return float("inf"), 0, 0
    return (statistics.median(errors), sum(e <= 25 for e in errors),
            sum(e <= 200 for e in errors))


def search(_args) -> int:
    from mapify.config import load_config
    from mapify.geo.geodesy import haversine_km
    if not DUMP.is_file():
        print(f"no {DUMP}; run --dump first")
        return 2
    rows = [json.loads(l) for l in DUMP.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r["candidates"] and r.get("locatable", True)]
    order = {it["site"]: i for i, it in enumerate(_manifest())}
    rows.sort(key=lambda r: order.get(r["site"], 10**6))
    fit = [r for i, r in enumerate(rows) if i % 2 == 0]
    held = [r for i, r in enumerate(rows) if i % 2 == 1]
    cap = load_config().fusion.max_total_adjustment

    with_text = [r for r in rows if r["ocr_lines"]]
    with_clue = [r for r in rows if any(c["narrow"] for c in r["clues"])]
    print(f"{len(rows)} samples ({len(fit)} fit, {len(held)} held out); "
          f"{len(with_text)} have any OCR text, {len(with_clue)} a narrow text clue\n")
    for r in with_clue:
        kinds = ", ".join(f"{c['kind']}:{'/'.join(c['countries'][:3])}"
                          for c in r["clues"] if c["narrow"])
        print(f"  {r['site']:<24} {kinds}")
    print()

    grid = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5)
    best = None
    for w in grid:
        m, a, b = _summary(_errors(fit, w, cap))
        print(f"  fit  w={w:<4g} median {m:>8,.1f} km   <=25km {a:>3}/{len(fit)}   "
              f"<=200km {b:>3}/{len(fit)}")
        key = (-a, -b, m)
        if best is None or key < best[0]:
            best = (key, w)
    w = best[1]

    base = _summary(_errors(held, 0.0, cap))
    new = _summary(_errors(held, w, cap))
    print(f"\nHELD OUT -- the only numbers that mean anything (w fitted = {w:g})")
    print(f"  without text clues  median {base[0]:>8,.1f} km   <=25km {base[1]:>3}/{len(held)}"
          f"   <=200km {base[2]:>3}/{len(held)}")
    print(f"  with text clues     median {new[0]:>8,.1f} km   <=25km {new[1]:>3}/{len(held)}"
          f"   <=200km {new[2]:>3}/{len(held)}")

    print("\nevery sample whose answer changes at that weight:")
    moved = 0
    for half, subset in (("fit", fit), ("held", held)):
        for r in subset:
            a, b = _top(r, 0.0, cap), _top(r, w, cap)
            if a is None or b is None or (a["lat"], a["lon"]) == (b["lat"], b["lon"]):
                continue
            moved += 1
            ea = haversine_km(a["lat"], a["lon"], *r["true"])
            eb = haversine_km(b["lat"], b["lon"], *r["true"])
            print(f"  [{half}] {r['site']:<24} {ea:>8,.0f} km -> {eb:>8,.0f} km  "
                  f"({a['iso']} -> {b['iso']})")
    if not moved:
        print("  none")

    better = (new[1], new[2], -new[0]) > (base[1], base[2], -base[0])
    worse = (new[1], new[2], -new[0]) < (base[1], base[2], -base[0])
    verdict = ("KEEP -- it holds up out of sample" if better and w > 0 else
               "REJECT -- it does not survive the held-out half" if worse else
               "NO EFFECT out of sample -- leave the weight at zero")
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
    ap.add_argument("--set", choices=("benchmark", "text"), default="benchmark",
                    help="the 163-photograph benchmark, or the text-bearing set "
                         "built by fetch_text_samples.py")
    args = ap.parse_args()
    if args.set == "text":
        global SAMPLES, DUMP
        SAMPLES = ROOT / "samples" / "text"
        DUMP = ROOT / "bench" / "text-clue-features-textset.jsonl"
    if args.dump:
        return dump(args.limit)
    if args.search:
        return search(args)
    ap.error("choose --dump or --search")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
