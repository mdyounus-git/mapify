#!/usr/bin/env python3
"""Download the open data and models the research upgrade uses, once, onto E:.

Everything goes under Mapify's models directory (``paths.models_dir()``,
``E:\\mapify\\app\\models`` here), which a source checkout and the installed
application share. One file at a time, resumable (an interrupted download
continues where it stopped), with the project's User-Agent.

    python scripts/fetch_research_data.py geonames glotlid
    python scripts/fetch_research_data.py osv5m-csv
    python scripts/fetch_research_data.py osv5m-shards 00 01 02 03
    python scripts/fetch_research_data.py --list

What each is, and its licence (see ATTRIBUTION.md):

  geonames      GeoNames allCountries + alternateNamesV2 (CC BY 4.0), 0.6 GB:
                13 million places and their names in every language, for
                reading place names off signs without asking anyone.
  glotlid       GlotLID language identification (Apache-2.0 plus a notice
                on its training data), 1.7 GB.
  osv5m-csv     OpenStreetView-5M train.csv (CC BY-SA 4.0), 2.9 GB: where
                each of 4.9 million street-level photographs was taken.
  osv5m-shards  OpenStreetView-5M image shards (CC BY-SA 4.0), 2.5 GB each
                (50,000 photographs); embedded once, then deletable.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = "Mapify/0.1 (open-source image geolocation; https://github.com/mdyounus-git/mapify)"
_HF = "https://huggingface.co"

SOURCES = {
    "geonames": [
        ("https://download.geonames.org/export/dump/allCountries.zip", "geonames/allCountries.zip"),
        ("https://download.geonames.org/export/dump/alternateNamesV2.zip",
         "geonames/alternateNamesV2.zip"),
        ("https://download.geonames.org/export/dump/readme.txt", "geonames/readme.txt"),
    ],
    "glotlid": [
        (f"{_HF}/cis-lmu/glotlid/resolve/main/model.bin", "glotlid/model.bin"),
        (f"{_HF}/cis-lmu/glotlid/resolve/main/LICENSE", "glotlid/LICENSE"),
    ],
    # Unicode CLDR (Unicode License v3): which languages each country speaks,
    # with shares, and the mapping from three-letter language codes to two.
    "cldr": [
        ("https://raw.githubusercontent.com/unicode-org/cldr/main/common/supplemental/"
         "supplementalData.xml", "cldr/supplementalData.xml"),
        ("https://raw.githubusercontent.com/unicode-org/cldr/main/common/supplemental/"
         "supplementalMetadata.xml", "cldr/supplementalMetadata.xml"),
        ("https://raw.githubusercontent.com/unicode-org/cldr/main/LICENSE", "cldr/LICENSE"),
    ],
    "osv5m-csv": [
        (f"{_HF}/datasets/osv5m/osv5m/resolve/main/train.csv", "osv5m/train.csv"),
        (f"{_HF}/datasets/osv5m/osv5m/resolve/main/README.md", "osv5m/README.md"),
    ],
}


def _shard(n: str) -> tuple[str, str]:
    n = f"{int(n):02d}"
    return (f"{_HF}/datasets/osv5m/osv5m/resolve/main/images/train/{n}.zip",
            f"osv5m/shards/{n}.zip")


def fetch(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest``, resuming a ``.part`` file if there is one."""
    if dest.is_file():
        print(f"  have {dest.name} ({dest.stat().st_size / 1e6:,.0f} MB)", flush=True)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(6):
        done = part.stat().st_size if part.is_file() else 0
        headers = {"User-Agent": UA}
        if done:
            headers["Range"] = f"bytes={done}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                if done and resp.status != 206:          # the server ignored the range
                    done = 0
                total = done + int(resp.headers.get("Content-Length") or 0)
                last = 0.0
                with part.open("ab" if done else "wb") as out:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 30:
                            last = time.time()
                            print(f"  {dest.name}: {done / 1e6:,.0f} of {total / 1e6:,.0f} MB",
                                  flush=True)
            if total and done < total:
                raise ConnectionError(f"stopped at {done} of {total} bytes")
            part.replace(dest)
            print(f"  got {dest.name} ({done / 1e6:,.0f} MB)", flush=True)
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and 400 <= exc.code < 500:
                # Not there, or not ours to fetch: asking again will not help.
                raise SystemExit(f"{url}: HTTP {exc.code}") from exc
            wait = min(300, 15 * 2 ** attempt)
            print(f"  {dest.name}: HTTP {exc.code}; retrying in {wait} s", flush=True)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            wait = min(300, 15 * 2 ** attempt)
            print(f"  {dest.name}: {type(exc).__name__}: {exc}; retrying in {wait} s", flush=True)
            time.sleep(wait)
    raise SystemExit(f"{url}: gave up after repeated failures")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", nargs="*")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    from mapify.paths import models_dir
    base = models_dir()
    if args.list or not args.what:
        print(f"into {base}")
        for name, files in SOURCES.items():
            print(f"  {name}: " + ", ".join(rel for _, rel in files))
        print("  osv5m-shards NN [NN ...]: osv5m/shards/NN.zip")
        return 0
    jobs: list[tuple[str, str]] = []
    words = list(args.what)
    while words:
        w = words.pop(0)
        if w == "osv5m-shards":
            while words and words[0].isdigit():
                jobs.append(_shard(words.pop(0)))
        elif w in SOURCES:
            jobs.extend(SOURCES[w])
        else:
            raise SystemExit(f"unknown: {w}")
    print(f"into {base}", flush=True)
    for url, rel in jobs:
        fetch(url, base / rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
