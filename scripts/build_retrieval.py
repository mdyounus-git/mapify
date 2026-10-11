#!/usr/bin/env python3
"""Build the retrieval memory: CLIP embeddings of OpenStreetView-5M photographs.

Each kept photograph becomes a row: its CLIP ViT-L/14 embedding (the same one
the analysis computes, through GeoCLIP's own encoder and preprocessing), and
where it was taken. At analysis time the photograph's embedding is compared
with every row, and the nearest neighbours say where scenes like it were
photographed (mapify/models/retrieval.py).

The shards are random samples of the world (each about 10% Africa and South
America, scripts/osv5m_shard_regions.py), and the benchmark is weakest there,
so every African and South American photograph is kept and a fixed share of
the rest. Embedding costs about half a second a photograph on this CPU.

    python scripts/build_retrieval.py --shards 00 01 02 03 [--others 0.2]

Rows go to osv5m/index/NN-PPP.npz under the models directory, a part every
2,000 photographs, so a stopped build resumes where it was. OpenStreetView-5M
is CC BY-SA 4.0 (imagery by Mapillary contributors); the index stays local.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
csv.field_size_limit(1 << 24)

AFRICA_SA = set("DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW "
                "KE LS LR LY MG MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN "
                "UG ZM ZW AR BO BR CL CO EC GY PY PE SR UY VE".split())
PART = 2000


def _keep(image_id: str, country: str, others: float) -> bool:
    if country in AFRICA_SA:
        return True
    h = int(hashlib.sha1(image_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < others


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shards", nargs="+", required=True)
    ap.add_argument("--others", type=float, default=0.2,
                    help="share of photographs outside Africa and South America kept")
    ap.add_argument("--batch", type=int, default=16)
    args = ap.parse_args(argv)

    from mapify.paths import apply_model_cache_env, models_dir
    apply_model_cache_env()
    base = models_dir() / "osv5m"
    out_dir = base / "index"
    out_dir.mkdir(parents=True, exist_ok=True)

    shards = [f"{int(s):02d}" for s in args.shards]
    members: dict[str, dict[str, str]] = {}
    for s in shards:
        path = base / "shards" / f"{s}.zip"
        if not path.is_file():
            raise SystemExit(f"{path} is missing: scripts/fetch_research_data.py osv5m-shards {s}")
        with zipfile.ZipFile(path) as z:
            members[s] = {Path(n).stem: n for n in z.namelist()
                          if n.lower().endswith((".jpg", ".jpeg"))}
    wanted = {i for m in members.values() for i in m}
    print(f"{len(wanted):,} photographs in {len(shards)} shards; reading train.csv ...",
          flush=True)
    where: dict[str, tuple[float, float, str]] = {}
    with (base / "train.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["id"] in wanted:
                where[row["id"]] = (float(row["latitude"]), float(row["longitude"]),
                                    row["country"])

    from PIL import Image

    from mapify.models.geoclip_locator import GeoClipLocator, _as_tensor
    locator = GeoClipLocator()
    if not locator.load():
        raise SystemExit(f"GeoCLIP did not load: {locator.load_error}")
    clip, processor = locator.shared_clip()
    import torch

    for s in shards:
        ids = sorted(i for i in members[s]
                     if i in where and _keep(i, where[i][2], args.others))
        parts = [ids[k:k + PART] for k in range(0, len(ids), PART)]
        print(f"shard {s}: keeping {len(ids):,} of {len(members[s]):,} in {len(parts)} parts",
              flush=True)
        with zipfile.ZipFile(base / "shards" / f"{s}.zip") as z:
            for p, part_ids in enumerate(parts):
                dest = out_dir / f"{s}-{p:03d}.npz"
                if dest.is_file():
                    continue
                started = time.time()
                embs = []
                for b in range(0, len(part_ids), args.batch):
                    batch_ids = part_ids[b:b + args.batch]
                    images = [Image.open(io.BytesIO(z.read(members[s][i]))).convert("RGB")
                              for i in batch_ids]
                    inputs = processor(images=images, return_tensors="pt")
                    with torch.no_grad():
                        feats = _as_tensor(clip.get_image_features(
                            pixel_values=inputs["pixel_values"]))
                        feats = feats / feats.norm(dim=-1, keepdim=True)
                    embs.append(feats.cpu().numpy().astype(np.float16))
                lat = np.array([where[i][0] for i in part_ids], dtype=np.float32)
                lon = np.array([where[i][1] for i in part_ids], dtype=np.float32)
                cc = np.array([where[i][2] for i in part_ids])
                tmp = dest.with_suffix(".part.npz")
                np.savez(tmp, emb=np.concatenate(embs), lat=lat, lon=lon, cc=cc,
                         id=np.array(part_ids))
                tmp.replace(dest)
                rate = (time.time() - started) / len(part_ids)
                left = sum(len(x) for x in parts[p + 1:])
                print(f"  {dest.name}: {len(part_ids):,} in {time.time() - started:.0f} s "
                      f"({rate:.2f} s each; shard {s} ~{left * rate / 3600:.1f} h left)",
                      flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
