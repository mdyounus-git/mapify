#!/usr/bin/env python3
"""Training material for a learned re-ranker: photographs no benchmark holds,
analysed the way the application analyses them, with their true positions.

    python scripts/rerank_data.py osv --per-country 6

For each photograph it keeps what the re-ranker's features are computed
from -- the inputs to fusion (GeoCLIP's candidates, the visual readings, the
text read and the script), the CLIP embedding (so the street-photo memory's
neighbours can be looked up once the memory is complete), PLONK's samples --
and the truth. One pickle per photograph under
bench/cache/rerank/<source>/, so a stopped run resumes where it was.

Sources:

  osv   OpenStreetView-5M shard 03 (CC BY-SA 4.0), kept out of the street-
        photo memory for exactly this: up to --per-country photographs from
        each of its 192 countries, chosen by a hash of the id so the choice
        is stable, so the material is spread over the world rather than
        a quarter American.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import pickle
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "bench" / "cache" / "rerank"
SHARD = "03"


def _rank(image_id: str) -> int:
    return int(hashlib.sha1(image_id.encode()).hexdigest()[:8], 16)


def choose_osv(per_country: int) -> list[dict]:
    from mapify.paths import models_dir
    base = models_dir() / "osv5m"
    with zipfile.ZipFile(base / "shards" / f"{SHARD}.zip") as z:
        members = {Path(n).stem: n for n in z.namelist()
                   if n.lower().endswith((".jpg", ".jpeg"))}
    by_country: dict[str, list[dict]] = defaultdict(list)
    with (base / "train.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["id"] in members:
                by_country[row["country"]].append({
                    "id": row["id"], "member": members[row["id"]],
                    "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                    "country": row["country"]})
    chosen = []
    for country, rows in sorted(by_country.items()):
        rows.sort(key=lambda r: _rank(r["id"]))
        chosen.extend(rows[:per_country])
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=("osv",))
    ap.add_argument("--per-country", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from mapify.paths import apply_model_cache_env, models_dir
    apply_model_cache_env()
    from PIL import Image

    from mapify.config import load_config
    from mapify.models.plonk_locator import PlonkLocator
    from mapify.pipeline import AnalysisEngine

    chosen = choose_osv(args.per_country)
    if args.limit:
        chosen = chosen[: args.limit]
    out = OUT / args.source
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(chosen)} photographs from {len({c['country'] for c in chosen})} countries",
          flush=True)

    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.ocr_enabled = True
    cfg.models.plonk_enabled = False            # its samples are kept separately
    cfg.models.retrieval_enabled = False        # neighbours are looked up later
    engine = AnalysisEngine(cfg)
    engine.warmup()
    plonk = PlonkLocator(samples=64, steps=16)

    t0, done = time.time(), 0
    with zipfile.ZipFile(models_dir() / "osv5m" / "shards" / f"{SHARD}.zip") as z:
        for i, c in enumerate(chosen, 1):
            dest = out / f"{c['id']}.pkl"
            if dest.exists():
                continue
            data = z.read(c["member"])
            try:
                rep = engine.analyse(data, filename=f"{c['id']}.jpg")
                with Image.open(io.BytesIO(data)) as im:
                    image = AnalysisEngine._prepare(im, cfg.models.max_image_side)
                pts = plonk.sample(image).astype(np.float32)
            except Exception as exc:
                print(f"  {c['id']}: {type(exc).__name__}: {exc}", flush=True)
                continue
            record = {"truth": (c["lat"], c["lon"]), "country": c["country"],
                      "source": args.source, "id": c["id"], "locatable": rep.locatable,
                      "inputs": None, "embedding": None, "plonk": pts}
            if rep.ok and rep.locatable and rep.raw_candidates:
                record["inputs"] = {
                    "raw": list(rep.raw_candidates), "evidence": rep.visual_evidence,
                    "ocr": rep.ocr_result, "script": getattr(rep, "script_reading", None),
                    "read_places": list(getattr(rep, "read_place_objs", []) or [])}
                if rep.embedding is not None:
                    record["embedding"] = np.asarray(rep.embedding, dtype=np.float16).ravel()
            tmp = dest.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps(record))
            tmp.replace(dest)
            done += 1
            if done % 10 == 0:
                rate = (time.time() - t0) / done
                print(f"  {i}/{len(chosen)}: {rate:.1f} s each, "
                      f"{rate * (len(chosen) - i) / 3600:.1f} h left", flush=True)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
