#!/usr/bin/env python3
"""Record every signal the locatability gate could use, for every image.

Runs the real pipeline -- offline, so nothing is geocoded and the run is
repeatable -- over three sets:

  place       the 163 geotagged benchmark photographs. The gate must
              refuse none of these. This is the number that matters.
  nonplace    real images from Wikimedia Commons that are not photographs
              of places: posters, charts, screenshots, documents, and
              photographs of objects and interiors.
  synthetic   drawn reproductions of the image that exposed the bug: a
              cyber-security poster, and its relatives.

For each image it writes the image-kind probabilities, the pixel
statistics, text coverage, the locator's own diagnostics and the
confidence band the pipeline reached, to ``bench/locatability.json``.
Fitting the gate is then a separate, fast step over that file, so the
thresholds can be argued with without re-running the models.

The file is written after every image and the run resumes where it
stopped, because 300 analyses on a CPU is most of an hour.

    python scripts/bench_locatability.py [--limit N] [--only place|nonplace|synthetic]
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

OUT = ROOT / "bench" / "locatability.json"


def image_sets() -> list[tuple[str, Path, str]]:
    """(set, path, expected) for every image to measure."""
    items: list[tuple[str, Path, str]] = []
    samples = ROOT / "samples"
    manifest = json.loads((samples / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["samples"]:
        path = samples / entry["file"]
        if path.exists():
            items.append(("place", path, "place"))
    for private in sorted((samples / "private").glob("*.jp*g")):
        items.append(("place", private, "place"))
    for sub in ("nonplace", "synthetic"):
        man = samples / sub / "manifest.json"
        if not man.exists():
            continue
        for entry in json.loads(man.read_text(encoding="utf-8")):
            path = samples / sub / entry["file"]
            if path.exists():
                items.append((sub, path, entry["expected"]))
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", choices=("place", "nonplace", "synthetic"))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING)

    from mapify.config import load_config
    from mapify.forensics.graphic import graphic_stats, text_coverage
    from mapify.pipeline import AnalysisEngine
    from PIL import Image

    done: dict[str, dict] = {}
    if OUT.exists():
        for row in json.loads(OUT.read_text(encoding="utf-8")):
            done[row["key"]] = row

    items = image_sets()
    if args.only:
        items = [i for i in items if i[0] == args.only]
    if args.limit:
        items = items[: args.limit]
    todo = [i for i in items if f"{i[0]}/{i[1].name}" not in done]
    print(f"{len(items)} images, {len(todo)} still to measure", flush=True)
    if not todo:
        return 0

    engine = AnalysisEngine(load_config())
    t_start = time.time()
    for n, (group, path, expected) in enumerate(todo, 1):
        key = f"{group}/{path.name}"
        data = path.read_bytes()
        t0 = time.time()
        report = engine.analyse(data, path.name, online_enrichment=False)
        row: dict = {"key": key, "set": group, "file": path.name,
                     "expected": expected, "ok": report.ok,
                     "error": report.error}
        if report.ok:
            kinds = {}
            if report.embedding is not None:
                kinds = engine.evidence_extractor.group_probabilities(
                    report.embedding, "image_kind")
            img = Image.open(path)
            pred = report.prediction or {}
            exif = report.exif or {}
            row.update({
                "kinds": {k: round(v, 5) for k, v in kinds.items()},
                "graphic": graphic_stats(img).as_dict(),
                "text_coverage": round(text_coverage(report.ocr_result), 4),
                "ocr_lines": len(getattr(report.ocr_result, "lines", []) or []),
                "diagnostics": pred.get("diagnostics", {}),
                "confidence": pred.get("confidence"),
                "uncertainty_km": pred.get("uncertainty_km"),
                "has_exif": bool(exif.get("has_exif")),
                "camera": " ".join(x for x in (exif.get("camera_make"),
                                               exif.get("camera_model")) if x),
                "format": (report.image or {}).get("format"),
                "size": [img.width, img.height],
            })
        row["elapsed_s"] = round(time.time() - t0, 2)
        done[key] = row
        OUT.write_text(json.dumps(list(done.values()), indent=1,
                                  ensure_ascii=False), encoding="utf-8")
        rate = (time.time() - t_start) / n
        top = max(row.get("kinds", {"-": 0}).items(), key=lambda kv: kv[1])
        print(f"[{n:3d}/{len(todo)}] {key:45s} {row.get('confidence')!s:12s} "
              f"kind={top[0]}:{top[1]:.2f} "
              f"eta {rate * (len(todo) - n) / 60:.0f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
