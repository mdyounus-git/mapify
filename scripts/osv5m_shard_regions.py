#!/usr/bin/env python3
"""Which countries each OpenStreetView-5M image shard holds -- without
downloading 98 x 2.5 GB.

A zip's directory sits at its end. Each shard's directory (a few MB) is read
with HTTP range requests, its image ids are looked up in train.csv, and the
countries counted. The result decides which shards are worth embedding first:
the benchmark is weakest in Africa and South America.

    python scripts/osv5m_shard_regions.py [--shards 0-97] [--top 10]

Writes osv5m/shard_countries.json under the models directory; resumable.
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = "Mapify/0.1 (open-source image geolocation; https://github.com/mdyounus-git/mapify)"
URL = "https://huggingface.co/datasets/osv5m/osv5m/resolve/main/images/train/{:02d}.zip"
AFRICA_SA = set("DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW "
                "KE LS LR LY MG MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN "
                "UG ZM ZW AR BO BR CL CO EC GY PY PE SR UY VE".split())


def _range(url: str, start: int, end: int | None = None) -> tuple[bytes, int]:
    rng = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": rng})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int((resp.headers.get("Content-Range") or "/0").rsplit("/", 1)[1])
        return resp.read(), total


def shard_ids(n: int) -> list[str]:
    url = URL.format(n)
    _, total = _range(url, 0, 0)                     # one byte, for the size
    tail, _ = _range(url, max(0, total - 65536))
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("no end-of-directory record")
    cd_size, cd_offset = struct.unpack("<II", tail[eocd + 12:eocd + 20])
    if cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:        # zip64
        loc = tail.rfind(b"PK\x06\x07")
        z64_offset = struct.unpack("<Q", tail[loc + 8:loc + 16])[0]
        rec, _ = _range(url, z64_offset, z64_offset + 55)
        cd_size, cd_offset = struct.unpack("<QQ", rec[40:56])
    cd, _ = _range(url, cd_offset, cd_offset + cd_size - 1)
    ids, p = [], 0
    while p + 46 <= len(cd) and cd[p:p + 4] == b"PK\x01\x02":
        nlen, xlen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")
        if name.lower().endswith((".jpg", ".jpeg")):
            ids.append(Path(name).stem)
        p += 46 + nlen + xlen + clen
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shards", default="0-97")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args(argv)
    from mapify.paths import models_dir
    base = models_dir() / "osv5m"
    out_path = base / "shard_countries.json"
    done = json.loads(out_path.read_text(encoding="utf-8")) if out_path.is_file() else {}
    lo, hi = (int(x) for x in args.shards.split("-"))
    todo = [n for n in range(lo, hi + 1) if f"{n:02d}" not in done]
    if todo:
        print("reading train.csv ...", flush=True)
        country = {}
        with (base / "train.csv").open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                country[row["id"]] = row["country"]
        for n in todo:
            ids = shard_ids(n)
            counts = Counter(country.get(i, "?") for i in ids)
            done[f"{n:02d}"] = {"images": len(ids), "countries": dict(counts.most_common())}
            share = sum(v for k, v in counts.items() if k in AFRICA_SA) / max(1, len(ids))
            print(f"  {n:02d}: {len(ids):,} images, {share:.1%} Africa + South America, "
                  f"top {counts.most_common(3)}", flush=True)
            out_path.write_text(json.dumps(done), encoding="utf-8")
    ranked = sorted(done.items(), key=lambda kv: -sum(
        v for k, v in kv[1]["countries"].items() if k in AFRICA_SA) / max(1, kv[1]["images"]))
    print(f"\nshards richest in Africa + South America:")
    for k, v in ranked[:args.top]:
        n = sum(c for cc, c in v["countries"].items() if cc in AFRICA_SA)
        print(f"  {k}: {n:,} of {v['images']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
