#!/usr/bin/env python3
"""Measure the read-address path, so its confidence cap can stop being a guess.

Phase 21 put a hard ceiling of `medium` on any location that came from text
read in the image. That ceiling is not a statement about how good the path
is -- on the one board it was built against it was exact -- it is a statement
about the absence of evidence. Phase 13 established that this project does
not hand out the `high` band on reasoning, and a read address had no numbers
behind it.

This produces the numbers. Two runs, and they measure different things:

  --synthetic   boards rendered from real OpenStreetMap places, whose true
                coordinates are therefore known exactly. Clean type on a
                flat background, so OCR is far easier than reality: this is
                a *ceiling*, not an estimate. If the path fails here it is
                broken; passing here proves only that it is not broken.

  --photos DIR  real photographs with known coordinates, one per line in
                DIR/truth.csv as `filename,lat,lon`. This is the number
                that would actually justify changing the band, and it needs
                photographs nobody has taken yet.

Usage:

    .venv\\Scripts\\python scripts/bench_readplace.py --synthetic
    .venv\\Scripts\\python scripts/bench_readplace.py --photos samples/boards

Network: one Nominatim search per image at worst, cached, rate limited by
the same machinery the application uses. Do not point this at a thousand
images; the service is donated and the usage policy is a condition of
access, not a suggestion.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mapify.config import load_config                     # noqa: E402
from mapify.geo.geodesy import haversine_km               # noqa: E402
from mapify.pipeline import AnalysisEngine                # noqa: E402

log = logging.getLogger("bench.readplace")


# Real places, with coordinates taken from OpenStreetMap, spread across
# scripts, naming conventions and address formats. The point is not that
# these are famous -- it is that their addresses are written the way local
# addresses are written, which is what the query builder has to cope with.
@dataclass(frozen=True)
class Board:
    name: str
    lines: tuple[str, ...]
    lat: float
    lon: float
    note: str = ""


BOARDS: tuple[Board, ...] = (
    Board("chettinad",
          ("CHETTINAD", "HOSPITAL AND RESEARCH INSTITUTE",
           "Rajiv Gandhi Salai (OMR)", "Kelambakkam - 603 103",
           "Chengalpattu District, Tamil Nadu"),
          12.7972, 80.2176,
          "the board this whole path was built from"),
    Board("charite",
          ("CHARITE", "Universitatsmedizin Berlin",
           "Charitepl. 1", "10117 Berlin", "Mitte"),
          52.5245, 13.3768),
    Board("trinity",
          ("TRINITY COLLEGE", "DUBLIN", "College Green", "Dublin 2",
           "Ireland"),
          53.3438, -6.2546,
          "a one-digit postal district, which the postcode patterns skip"),
    Board("sorbonne",
          ("SORBONNE UNIVERSITE", "Campus Pierre et Marie Curie",
           "4 Place Jussieu", "75005 Paris", "France"),
          48.8467, 2.3557),
    Board("wits",
          ("UNIVERSITY OF THE WITWATERSRAND", "JOHANNESBURG",
           "1 Jan Smuts Avenue", "Braamfontein 2000", "Gauteng"),
          -26.1929, 28.0305),
    Board("unam",
          ("UNIVERSIDAD NACIONAL", "AUTONOMA DE MEXICO",
           "Av. Universidad 3000", "Ciudad Universitaria",
           "04510 Coyoacan, CDMX"),
          19.3320, -99.1846),
    Board("iisc",
          ("INDIAN INSTITUTE OF SCIENCE", "BANGALORE",
           "CV Raman Road", "Bengaluru - 560 012", "Karnataka"),
          13.0219, 77.5671),
    Board("uoft",
          ("UNIVERSITY OF TORONTO", "27 King's College Circle",
           "Toronto, ON M5S 1A1", "Canada"),
          43.6629, -79.3957,
          "a Canadian postcode, a shape the patterns do not know"),
)


def render(board: Board, out_dir: Path) -> Path:
    """Draw a signboard. Deliberately clean: this measures a ceiling."""
    from PIL import Image, ImageDraw, ImageFont

    def font(size: int):
        for name in ("arialbd.ttf", "arial.ttf", "segoeui.ttf",
                     "DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    img = Image.new("RGB", (1200, 800), (104, 118, 98))
    draw = ImageDraw.Draw(img)
    draw.rectangle([90, 150, 1110, 640], fill=(238, 236, 228))
    draw.rectangle([90, 150, 1110, 640], outline=(40, 62, 110), width=10)
    draw.rectangle([90, 150, 1110, 240], fill=(24, 54, 108))

    sizes = [58] + [30] * (len(board.lines) - 1)
    y = 170
    for text, size in zip(board.lines, sizes):
        f = font(size)
        width = draw.textbbox((0, 0), text, font=f)[2]
        colour = (255, 255, 255) if y < 240 else (22, 32, 52)
        draw.text(((1200 - width) // 2, y), text, font=f, fill=colour)
        y += size + 18

    path = out_dir / f"board-{board.name}.jpg"
    img.save(path, "JPEG", quality=92)
    return path


@dataclass
class Outcome:
    name: str
    error_km: float | None
    confidence: str
    was_read: bool
    matched: str
    note: str


def run_one(engine: AnalysisEngine, path: Path, lat: float, lon: float,
            name: str, note: str = "") -> Outcome:
    report = engine.analyse(path.read_bytes(), path.name).as_dict()
    pred = report["prediction"]
    est = pred.get("estimated")
    leader = (report.get("candidates") or [{}])[0]
    read_places = report.get("read_places") or []

    return Outcome(
        name=name,
        error_km=(haversine_km(est["latitude"], est["longitude"], lat, lon)
                  if est else None),
        confidence=pred.get("confidence", "?"),
        was_read="text" in str(leader.get("provenance", "")),
        matched=(read_places[0]["display_name"].split(",")[0]
                 if read_places else "-"),
        note=note,
    )


def report(outcomes: list[Outcome], title: str, optimistic: bool) -> int:
    print(f"\n{title}")
    print("=" * len(title))
    print(f"{'image':<14} {'error':>10}  {'band':<13} {'read':<5} matched")
    print("-" * 78)
    for o in outcomes:
        err = f"{o.error_km:,.2f} km" if o.error_km is not None else "no result"
        print(f"{o.name:<14} {err:>10}  {o.confidence:<13} "
              f"{'yes' if o.was_read else 'no':<5} {o.matched[:34]}")
        if o.note:
            print(f"{'':<14} {o.note}")

    placed = [o for o in outcomes if o.error_km is not None]
    read = [o for o in placed if o.was_read]
    print("-" * 78)
    if not placed:
        print("nothing was placed at all")
        return 1

    errors = sorted(o.error_km for o in placed)
    print(f"placed        : {len(placed)}/{len(outcomes)}")
    print(f"read from text: {len(read)}/{len(outcomes)}")
    print(f"median error  : {statistics.median(errors):,.2f} km")
    print(f"within 1 km   : {sum(e <= 1 for e in errors)}/{len(errors)}")
    print(f"within 25 km  : {sum(e <= 25 for e in errors)}/{len(errors)}")
    print(f"worst         : {errors[-1]:,.2f} km")

    if read:
        read_err = sorted(o.error_km for o in read)
        print(f"\nof the ones read from text:")
        print(f"  median      : {statistics.median(read_err):,.2f} km")
        print(f"  worst       : {read_err[-1]:,.2f} km")
        print(f"  bands used  : "
              f"{', '.join(sorted({o.confidence for o in read}))}")

    if optimistic:
        print(
            "\nThese are rendered boards: clean type, flat background, no "
            "\nperspective, no glare, no motion blur. Treat this as a ceiling "
            "\non the path's accuracy, never as an estimate of it. It cannot "
            "\njustify raising the confidence cap; only real photographs can."
        )
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):   # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true",
                    help="render boards from known OSM places and measure")
    ap.add_argument("--photos", type=Path, default=None,
                    help="a directory with truth.csv: filename,lat,lon")
    ap.add_argument("--keep", action="store_true",
                    help="keep the rendered boards for inspection")
    args = ap.parse_args()

    if not args.synthetic and not args.photos:
        ap.error("choose --synthetic or --photos")

    logging.basicConfig(level=logging.ERROR)
    cfg = load_config()
    cfg.models.ocr_enabled = True
    engine = AnalysisEngine(cfg)
    print("loading models...")
    engine.warmup()

    if args.photos:
        truth = args.photos / "truth.csv"
        if not truth.is_file():
            print(f"no truth.csv in {args.photos}")
            return 2
        outcomes = []
        with truth.open(encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if not row or row[0].startswith("#"):
                    continue
                name, lat, lon = row[0].strip(), float(row[1]), float(row[2])
                image = args.photos / name
                if not image.is_file():
                    print(f"  missing {name}")
                    continue
                print(f"  {name}")
                outcomes.append(
                    run_one(engine, image, lat, lon, Path(name).stem)
                )
        return report(outcomes, "Real photographs", optimistic=False)

    out_dir = ROOT / "samples" / "private" / "bench-boards"
    out_dir.mkdir(parents=True, exist_ok=True)
    outcomes = []
    for board in BOARDS:
        path = render(board, out_dir)
        print(f"  {board.name}")
        outcomes.append(
            run_one(engine, path, board.lat, board.lon, board.name, board.note)
        )
        if not args.keep:
            path.unlink(missing_ok=True)
    return report(outcomes, "Rendered signboards (a ceiling, not an estimate)",
                  optimistic=True)


if __name__ == "__main__":
    raise SystemExit(main())
