#!/usr/bin/env python3
"""Measure the mirror test: how often a mirrored photograph is caught, and
how often one the right way round is flipped by mistake.

The pipeline calls a photograph mirrored when its text reads clearly better
flipped (pipeline.MIRROR_*). Each photograph here is read twice -- as it is,
through the pipeline's own first look (so with the reader the analysis would
choose), and flipped with that same reader -- and the two reading qualities
are kept. Fed the photograph as it is, the test flips it wrongly when the
flipped reading clears the thresholds. Fed its mirror image, the same two
readings swap places, and the test catches it when the unflipped one does.
So one pair of readings per photograph measures both errors.

Two sets:

  * ``bench`` -- the 163 benchmark photographs (samples/), nearly all with
    little or no text: how often an ordinary photograph pays for a second
    reading, and whether any is flipped wrongly;
  * ``text``  -- the text-bearing set (samples/text/, fetch_text_samples.py):
    signs that read well, where the test has to work.

    python scripts/measure_mirror.py --dump [--set bench|text]
    python scripts/measure_mirror.py --report

Readings go to bench/mirror-readings.jsonl after every photograph; an
interrupted dump resumes where it stopped. Nothing leaves the machine.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SETS = {
    "bench": ROOT / "samples",
    "text": ROOT / "samples" / "text",
}
DUMP = ROOT / "bench" / "mirror-readings.jsonl"
READABLE = 0.6          # a reading this good is text, not fragments


def _items(name: str) -> list[Path]:
    folder = SETS[name]
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        return []
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("samples", [])
    return [folder / r["file"] for r in rows if (folder / r["file"]).is_file()]


def _rows() -> list[dict]:
    if not DUMP.is_file():
        return []
    return [json.loads(l) for l in DUMP.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def dump(set_name: str) -> int:
    import logging
    logging.basicConfig(level=logging.ERROR)
    from PIL import ImageOps

    from mapify.config import load_config
    from mapify.paths import apply_model_cache_env
    apply_model_cache_env()
    from mapify.pipeline import AnalysisEngine, AnalysisReport, reading_quality

    done = {(r["set"], r["file"]) for r in _rows()}
    todo = [p for p in _items(set_name) if (set_name, p.name) not in done]
    print(f"{set_name}: {len(done)} readings already kept, {len(todo)} to go")
    if not todo:
        return 0

    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.ocr_enabled = True
    engine = AnalysisEngine(cfg)
    print("loading models…", flush=True)
    engine.warmup()
    if not engine.ocr or engine.ocr.name == "none":
        print("OCR is not installed; nothing to measure.")
        return 1

    DUMP.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with DUMP.open("a", encoding="utf-8") as out:
        for n, path in enumerate(todo, 1):
            try:
                source, _ = engine.decode(path.read_bytes(), path.name)
                image = engine._prepare(source, cfg.models.max_image_side)
                look = engine._look(image, AnalysisReport(ok=False, elapsed_s=0.0),
                                    lambda key, message: None)
                first = look.ocr_result
                try:
                    flipped = engine.ocr.read(ImageOps.mirror(image), look.extra_group)
                except TypeError:
                    flipped = engine.ocr.read(ImageOps.mirror(image))
            except Exception as exc:                      # pragma: no cover
                print(f"  {path.name}: {type(exc).__name__}: {exc}", flush=True)
                continue
            row = {
                "set": set_name, "file": path.name,
                "a": round(reading_quality(first), 4),
                "b": round(reading_quality(flipped), 4),
                "lines_a": len(first.lines), "lines_b": len(flipped.lines),
                "text_a": first.text.replace("\n", " | ")[:100],
                "text_b": flipped.text.replace("\n", " | ")[:100],
                "reader": "+".join(look.extra_group or ()) or "default",
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            rate = (time.time() - started) / n
            print(f"  {n}/{len(todo)}  a={row['a']:.2f} b={row['b']:.2f}  "
                  f"{path.name}  (~{rate * (len(todo) - n) / 60:.0f} min left)",
                  flush=True)
    return 0


def _flips(first: float, lines: int, flipped: float,
           try_below: float, min_quality: float, min_gain: float) -> tuple[bool, bool]:
    """(tried, called mirrored) -- the pipeline's rule, on stored readings."""
    tried = lines > 0 and first < try_below
    return tried, tried and flipped >= min_quality and flipped - first >= min_gain


def report() -> int:
    from mapify import pipeline

    rows = _rows()
    if not rows:
        print("Nothing dumped yet: run --dump first.")
        return 1
    current = (pipeline.MIRROR_TRY_BELOW, pipeline.MIRROR_MIN_QUALITY,
               pipeline.MIRROR_MIN_GAIN)
    grid = [(t, q, g) for t in (0.4, 0.5, 0.6, 0.7)
            for q in (0.5, 0.6, 0.7) for g in (0.1, 0.2, 0.3)]
    if current not in grid:
        grid.append(current)

    for set_name in ("bench", "text"):
        sub = [r for r in rows if r["set"] == set_name]
        if not sub:
            continue
        with_text = [r for r in sub if r["lines_a"] or r["lines_b"]]
        # Text that reads well the right way round: the photographs on which
        # the test can work at all. Fragments that read badly both ways
        # cannot tell anyone which way round a photograph is.
        readable = [r for r in sub if r["lines_a"] and r["a"] >= READABLE]
        print(f"\n{set_name}: {len(sub)} photographs, {len(with_text)} with any text "
              f"read, {len(readable)} whose text reads well (>= {READABLE})")
        print("  try<  min   gain | as is: 2nd read  wrongly flipped |"
              " mirrored: caught (of readable)")
        for t, q, g in grid:
            as_is = [_flips(r["a"], r["lines_a"], r["b"], t, q, g) for r in sub]
            mirrored = [_flips(r["b"], r["lines_b"], r["a"], t, q, g) for r in sub]
            caught = sum(_flips(r["b"], r["lines_b"], r["a"], t, q, g)[1] for r in readable)
            mark = "  <- current" if (t, q, g) == current else ""
            print(f"  {t:.1f}   {q:.1f}  {g:.1f}  |"
                  f" {sum(x[0] for x in as_is):6d}   {sum(x[1] for x in as_is):10d}       |"
                  f" {caught:4d} of {len(readable)}{mark}")
        wrong = [r for r in sub if _flips(r["a"], r["lines_a"], r["b"], *current)[1]]
        for r in wrong:
            print(f"  WRONGLY FLIPPED at current: {r['file']}  a={r['a']} b={r['b']}"
                  f"  '{r['text_a'][:40]}' / '{r['text_b'][:40]}'")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--set", choices=sorted(SETS), default="bench")
    args = ap.parse_args(argv)
    if args.dump:
        return dump(args.set)
    if args.report:
        return report()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
