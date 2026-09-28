#!/usr/bin/env python3
"""Import an OpenStreetMap regional extract into the local feature store.

    python scripts/import_osm.py data/osm/southern-zone-latest.osm.pbf --region "India: Southern Zone"

Extracts come from Geofabrik (https://download.geofabrik.de), which cuts
the OpenStreetMap planet file into regions. The data is ODbL 1.0,
(c) OpenStreetMap contributors.

SQLite sorts its staging table in a temporary directory. That is pointed
at the project's own data folder, because on Windows the default is the
system drive and a regional import can stage several gigabytes.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pbf")
    ap.add_argument("--region", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from mapify.paths import geodata_dir, state_dir
    tmp = state_dir() / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = os.environ["TEMP"] = os.environ["SQLITE_TMPDIR"] = str(tmp)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from mapify.geodata.osm_import import import_pbf
    from mapify.geodata.store import GeoStore

    store = GeoStore(geodata_dir() / "store.sqlite")
    t0 = time.time()
    counts = import_pbf(args.pbf, store, args.region,
                        progress=lambda m: print(time.strftime("%H:%M:%S"), m,
                                                 flush=True))
    store.db.execute("VACUUM")
    store.close()
    size = (geodata_dir() / "store.sqlite").stat().st_size
    print(f"finished in {(time.time() - t0) / 60:.1f} min; store is "
          f"{size / 1e6:.0f} MB; {counts}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
