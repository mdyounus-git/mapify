"""Access to the bundled per-country attribute table.

Loaded once and indexed both ways, because fusion asks questions in both
directions: "what script does India sign in?" and "which countries sign in
Cyrillic?".
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_PATH = Path(__file__).resolve().parent / "country_attributes.json"


@dataclass(frozen=True)
class Country:
    iso_a2: str
    name: str
    name_long: str | None
    iso_a3: str | None
    continent: str | None
    subregion: str | None
    region_un: str | None
    label_lat: float
    label_lon: float
    drive_side: str
    scripts: tuple[str, ...]
    languages: tuple[str, ...]
    plate_class: str
    disputed_boundary_note: bool

    def signs_in(self, script: str) -> bool:
        return script in self.scripts

    def as_dict(self) -> dict[str, Any]:
        d = vars(self).copy()
        d["scripts"] = list(self.scripts)
        d["languages"] = list(self.languages)
        return d


class CountryTable:
    def __init__(self, payload: dict[str, Any]):
        self.meta: dict[str, Any] = payload.get("_meta", {})
        self._by_iso2: dict[str, Country] = {}
        self._by_name: dict[str, Country] = {}
        for iso2, row in (payload.get("countries") or {}).items():
            country = Country(
                iso_a2=iso2,
                name=row.get("name", iso2),
                name_long=row.get("name_long"),
                iso_a3=row.get("iso_a3"),
                continent=row.get("continent"),
                subregion=row.get("subregion"),
                region_un=row.get("region_un"),
                label_lat=float(row.get("label_lat", 0.0)),
                label_lon=float(row.get("label_lon", 0.0)),
                drive_side=row.get("drive_side", "right"),
                scripts=tuple(row.get("scripts") or ("Latn",)),
                languages=tuple(row.get("languages") or ()),
                plate_class=row.get("plate_class", "plain_white"),
                disputed_boundary_note=bool(row.get("disputed_boundary_note")),
            )
            self._by_iso2[iso2] = country
            self._by_name[country.name.casefold()] = country
            if country.name_long:
                self._by_name.setdefault(country.name_long.casefold(), country)

        self._by_script: dict[str, set[str]] = {}
        self._by_drive: dict[str, set[str]] = {"left": set(), "right": set()}
        self._by_plate: dict[str, set[str]] = {}
        self._by_subregion: dict[str, set[str]] = {}
        self._by_language: dict[str, set[str]] = {}
        for iso2, c in self._by_iso2.items():
            for s in c.scripts:
                self._by_script.setdefault(s, set()).add(iso2)
            self._by_drive.setdefault(c.drive_side, set()).add(iso2)
            self._by_plate.setdefault(c.plate_class, set()).add(iso2)
            if c.subregion:
                self._by_subregion.setdefault(c.subregion, set()).add(iso2)
            for lang in c.languages:
                self._by_language.setdefault(lang, set()).add(iso2)

    # --------------------------------------------------------------- lookups

    def __len__(self) -> int:
        return len(self._by_iso2)

    def __contains__(self, iso2: object) -> bool:
        return isinstance(iso2, str) and iso2.upper() in self._by_iso2

    def get(self, iso2: str | None) -> Country | None:
        if not iso2:
            return None
        return self._by_iso2.get(iso2.upper())

    def by_name(self, name: str | None) -> Country | None:
        if not name:
            return None
        return self._by_name.get(name.casefold())

    def resolve(self, iso2: str | None = None, name: str | None = None
                ) -> Country | None:
        """Best effort: ISO code first, then an exact-ish name match."""
        return self.get(iso2) or self.by_name(name)

    def all(self) -> Iterable[Country]:
        return self._by_iso2.values()

    # --------------------------------------------------------- reverse views

    def countries_using_script(self, script: str) -> set[str]:
        return set(self._by_script.get(script, ()))

    def countries_driving(self, side: str) -> set[str]:
        return set(self._by_drive.get(side, ()))

    def countries_with_plate(self, plate_class: str) -> set[str]:
        return set(self._by_plate.get(plate_class, ()))

    def countries_in_subregion(self, subregion: str) -> set[str]:
        return set(self._by_subregion.get(subregion, ()))

    def countries_speaking(self, language: str) -> set[str]:
        return set(self._by_language.get(language, ()))

    def scripts_in_use(self) -> list[str]:
        """Every script that appears on signage somewhere, commonest first."""
        return sorted(self._by_script, key=lambda s: -len(self._by_script[s]))

    def subregions(self) -> list[str]:
        return sorted(self._by_subregion)


@functools.lru_cache(maxsize=1)
def get_table() -> CountryTable:
    if not _PATH.exists():
        raise FileNotFoundError(
            f"{_PATH} is missing. Run: python scripts/build_country_data.py"
        )
    return CountryTable(json.loads(_PATH.read_text(encoding="utf-8")))
