"""GeoNames: place names read off a sign, matched offline against far more places.

The bundled Natural Earth gazetteer knows about 7,300 settlements. GeoNames
(CC BY 4.0, "GeoNames geographical database, geonames.org") knows 13 million
features in every language; built once into a SQLite index from
``allCountries.zip`` (``scripts/fetch_research_data.py geonames``), it answers
a name in milliseconds with no network.

Not all of it is kept for matching: almost any word is the name of a hamlet
somewhere ("Station", "Centre", "Hotel"), and a match is a claim about where
a photograph was taken. So the index holds settlements of at least
``MIN_POPULATION`` people and countries and first- and second-level regions,
every spelling of each, and is asked only after Natural Earth. Matches come
ordered by population, as Natural Earth's do.
"""

from __future__ import annotations

import io
import sqlite3
import threading
import zipfile
from pathlib import Path
from typing import Callable

from .offline import GazetteerMatch, _normalise

__all__ = ["GeoNames", "build", "default_path", "ATTRIBUTION", "MIN_POPULATION"]

ATTRIBUTION = "Place names: GeoNames geographical database, geonames.org (CC BY 4.0)"
MIN_POPULATION = 1000
_ADMIN = {"PCLI", "PCLD", "PCLF", "PCLS", "PCL", "ADM1", "ADM2"}
# Words a sign says for other reasons, which are also the names of regions:
# "CENTRE" on a shop front found Burkina Faso's and Cameroon's Centre
# regions. A region keeps its other spellings; these alone do not find it.
_GENERIC = {
    "centre", "center", "central", "centro", "zentrum", "centrale", "centrum",
    "north", "south", "east", "west", "northern", "southern", "eastern", "western",
    "nord", "sud", "est", "ouest", "norte", "sur", "este", "oeste", "nordeste",
    "noreste", "sudeste", "sureste", "noroeste", "sudoeste", "suroeste", "centre-est",
    "centre-nord", "centre-ouest", "centre-sud", "capital", "capitale", "coast",
    "littoral", "upper", "lower", "middle", "islands", "island", "plateau",
    "plateaux", "lakes", "mountains", "valley", "river", "region", "district",
    "province", "metropolitan", "national", "federal", "maritime", "savanes",
}


def default_path() -> Path:
    from ..paths import models_dir
    return models_dir() / "geonames" / "geonames.sqlite"


def _keep(fclass: str, fcode: str, population: int) -> bool:
    return (fclass == "P" and population >= MIN_POPULATION) or fcode in _ADMIN


def _floor(norm: str) -> int:
    # The same rule as the Natural Earth index: two characters are noise in
    # Latin script, but CJK names are legitimately short.
    return 2 if any(ord(c) > 0x2E80 for c in norm) else 4


def build(zip_path: Path, db_path: Path,
          progress: Callable[[int, int], None] | None = None) -> int:
    """Build the index from ``allCountries.zip``. Returns the places kept."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript("""
        PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;
        CREATE TABLE places (id INTEGER PRIMARY KEY, name TEXT, lat REAL, lon REAL,
                             fclass TEXT, fcode TEXT, cc TEXT, admin1 TEXT, pop INTEGER);
        CREATE TABLE names (norm TEXT, id INTEGER);
    """)
    kept = seen = 0
    places, names = [], []
    with zipfile.ZipFile(zip_path) as z:
        member = next(n for n in z.namelist() if n.endswith(".txt"))
        with io.TextIOWrapper(z.open(member), encoding="utf-8") as fh:
            for line in fh:
                seen += 1
                f = line.rstrip("\n").split("\t")
                if len(f) < 15:
                    continue
                try:
                    pop = int(f[14] or 0)
                except ValueError:
                    pop = 0
                if not _keep(f[6], f[7], pop):
                    continue
                gid = int(f[0])
                places.append((gid, f[1], float(f[4]), float(f[5]), f[6], f[7], f[8],
                               f[10], pop))
                spellings = {f[1], f[2], *(f[3].split(",") if f[3] else ())}
                norms = {_normalise(s) for s in spellings if s}
                if f[6] == "A":
                    norms -= _GENERIC
                names.extend((n, gid) for n in norms if len(n) >= _floor(n))
                kept += 1
                if len(places) >= 50000:
                    con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?)", places)
                    con.executemany("INSERT INTO names VALUES (?,?)", names)
                    places, names = [], []
                    if progress:
                        progress(seen, kept)
    con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?)", places)
    con.executemany("INSERT INTO names VALUES (?,?)", names)
    con.execute("CREATE INDEX names_norm ON names (norm)")
    con.commit()
    con.close()
    tmp.replace(db_path)
    return kept


class GeoNames:
    """Read-only lookups in the built index; unavailable until it is built."""

    def __init__(self, db_path: Path | None = None):
        self.path = Path(db_path) if db_path else default_path()
        self._local = threading.local()

    def available(self) -> bool:
        return self.path.is_file()

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True,
                                  check_same_thread=False)
            self._local.con = con
        return con

    def find(self, name: str, limit: int = 8) -> list[GazetteerMatch]:
        """Places with this name in any language, most populous first."""
        norm = _normalise(name or "")
        if len(norm) < _floor(norm) or not self.available():
            return []
        rows = self._con().execute(
            "SELECT p.name, p.lat, p.lon, p.cc, p.admin1, p.pop FROM names n "
            "JOIN places p ON p.id = n.id WHERE n.norm = ? "
            "ORDER BY p.pop DESC LIMIT ?", (norm, int(limit))).fetchall()
        from ..data.countries import get_table
        table = get_table()
        out = []
        for pname, lat, lon, cc, admin1, pop in rows:
            country = table.get(cc)
            out.append(GazetteerMatch(
                name=pname, matched_text=name, matched_field="GEONAMES",
                latitude=round(lat, 6), longitude=round(lon, 6),
                admin0=country.name if country else cc, admin1=None,
                population=pop or None))
        return out


if __name__ == "__main__":                          # pragma: no cover
    import sys
    import time
    from ..paths import models_dir
    src = models_dir() / "geonames" / "allCountries.zip"
    dst = default_path()
    t0 = time.time()
    n = build(src, dst, lambda seen, kept: print(f"  {seen:,} read, {kept:,} kept",
                                                 flush=True))
    print(f"{n:,} places indexed into {dst} in {time.time() - t0:.0f} s")
    sys.exit(0)
