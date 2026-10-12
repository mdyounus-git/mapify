"""Offline reverse geocoding from Natural Earth (public domain).

This is the privacy floor of the tool: with no network at all, a coordinate
still resolves to a country, a first-order administrative division, and the
nearest populated places. Nominatim, when enabled, refines this -- it does not
replace it.

Geometry is tested with pure-Python even-odd ray casting over the shapefile
rings, which handles multi-part countries and interior holes (enclaves) without
pulling in a geometry engine.
"""

from __future__ import annotations

import functools
import math
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .geodesy import bearing_deg, compass_point, haversine_km

from ..paths import read_root

DEFAULT_DATA_DIR = read_root() / "data" / "naturalearth"

COUNTRIES_LAYER = "ne_10m_admin_0_countries"
ADMIN1_LAYER = "ne_10m_admin_1_states_provinces"
PLACES_LAYER = "ne_10m_populated_places"


class GeodataMissing(RuntimeError):
    """Raised when the Natural Earth files have not been fetched yet."""


@dataclass
class CountryHit:
    name: str
    name_long: str | None
    iso_a2: str | None
    iso_a3: str | None
    continent: str | None
    subregion: str | None
    sovereign: str | None = None


@dataclass
class Admin1Hit:
    name: str
    iso_3166_2: str | None
    admin_type: str | None
    country_name: str | None


@dataclass
class PlaceHit:
    name: str
    admin0: str | None
    admin1: str | None
    latitude: float
    longitude: float
    population: int | None
    distance_km: float
    bearing_deg: float
    feature_class: str | None = None

    @property
    def direction(self) -> str:
        return compass_point(self.bearing_deg)


@dataclass
class GazetteerMatch:
    """A gazetteer entry whose name matched a string we read off a sign."""

    name: str
    matched_text: str
    # Which Natural Earth field matched, e.g. NAME_RU for the Russian
    # spelling.  This is what lets us say *why* we believe the match.
    matched_field: str
    latitude: float
    longitude: float
    admin0: str | None
    admin1: str | None
    population: int | None

    @property
    def language(self) -> str | None:
        if "_" in self.matched_field:
            suffix = self.matched_field.rsplit("_", 1)[1].lower()
            if len(suffix) == 2:
                return suffix
        return None


@dataclass
class OfflineLocation:
    country: CountryHit | None = None
    admin1: Admin1Hit | None = None
    nearest_places: list[PlaceHit] = field(default_factory=list)
    # True when the point fell outside every country polygon (at sea, or in
    # the gaps Natural Earth leaves along coastlines).
    offshore: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "country": vars(self.country) if self.country else None,
            "admin1": vars(self.admin1) if self.admin1 else None,
            "offshore": self.offshore,
            "nearest_places": [
                {**vars(p), "direction": p.direction} for p in self.nearest_places
            ],
        }


def _ring_contains(x: float, y: float, ring: Sequence[Sequence[float]]) -> bool:
    """Even-odd ray cast of the point against one closed ring."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            # x of the edge at height y; guard against a zero-height edge.
            denom = yj - yi
            if denom != 0.0 and x < (xj - xi) * (y - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def _shape_contains(x: float, y: float, points: Sequence[Sequence[float]],
                    parts: Sequence[int]) -> bool:
    """Even-odd over every ring, so interior holes subtract correctly."""
    inside = False
    starts = list(parts) or [0]
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(points)
        if _ring_contains(x, y, points[start:end]):
            inside = not inside
    return inside


def _has_latin(text: str) -> bool:
    """Whether any letter in the text is a Latin one."""
    return any(ch.isalpha() and "LATIN" in unicodedata.name(ch, "") for ch in text)


def _normalise(text: str) -> str:
    """Casefold and strip diacritics, for tolerant place-name matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold().strip()


class _Layer:
    """A lazily-loaded shapefile with a bounding-box index."""

    def __init__(self, path: Path):
        self.path = path
        self.fields: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.geometries: list[tuple[list[Any], list[int]]] = []
        self.bboxes: list[tuple[float, float, float, float]] = []
        self._loaded = False
        self._lock = threading.Lock()

    def load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                import shapefile  # pyshp
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise GeodataMissing(
                    "pyshp is required for offline geocoding: pip install pyshp"
                ) from exc
            if not self.path.with_suffix(".shp").exists():
                raise GeodataMissing(
                    f"{self.path.name}.shp not found in {self.path.parent}. "
                    "Run: python scripts/fetch_data.py"
                )
            reader = shapefile.Reader(str(self.path))
            self.fields = [f[0] for f in reader.fields[1:]]
            for sr in reader.iterShapeRecords():
                shape = sr.shape
                self.records.append(dict(zip(self.fields, list(sr.record))))
                pts = list(shape.points)
                self.geometries.append((pts, list(shape.parts)))
                # Point shapes carry no bbox attribute, so derive one.
                bbox = getattr(shape, "bbox", None)
                if bbox is not None:
                    box = (
                        float(bbox[0]), float(bbox[1]),
                        float(bbox[2]), float(bbox[3]),
                    )
                elif pts:
                    xs = [float(p[0]) for p in pts]
                    ys = [float(p[1]) for p in pts]
                    box = (min(xs), min(ys), max(xs), max(ys))
                else:
                    box = (0.0, 0.0, 0.0, 0.0)
                self.bboxes.append(box)
            reader.close()
            self._loaded = True

    def candidates(self, lon: float, lat: float) -> Iterable[int]:
        for i, (xmin, ymin, xmax, ymax) in enumerate(self.bboxes):
            if xmin <= lon <= xmax and ymin <= lat <= ymax:
                yield i

    def locate(self, lon: float, lat: float) -> dict[str, Any] | None:
        self.load()
        for i in self.candidates(lon, lat):
            pts, parts = self.geometries[i]
            if _shape_contains(lon, lat, pts, parts):
                return self.records[i]
        return None


class OfflineGeocoder:
    """Point-in-polygon lookups against the bundled Natural Earth layers."""

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self._countries = _Layer(self.data_dir / COUNTRIES_LAYER)
        self._country_names: dict[str, dict[str, Any]] | None = None
        self._admin1 = _Layer(self.data_dir / ADMIN1_LAYER)
        self._places = _Layer(self.data_dir / PLACES_LAYER)
        self._place_points: list[tuple[float, float, dict[str, Any]]] | None = None
        self._place_index: dict[str, list[int]] | None = None
        self._lock = threading.Lock()
        # Asked when Natural Earth knows no place by a name (geonames.py);
        # off until the engine turns it on from its configuration.
        self.use_geonames = False
        self._geonames = None

    # ---------------------------------------------------------------- status

    def available(self) -> bool:
        return (self.data_dir / (COUNTRIES_LAYER + ".shp")).exists()

    def status(self) -> dict[str, Any]:
        layers = {
            "countries": COUNTRIES_LAYER,
            "admin1": ADMIN1_LAYER,
            "places": PLACES_LAYER,
        }
        return {
            "data_dir": str(self.data_dir),
            "layers": {
                key: (self.data_dir / (name + ".shp")).exists()
                for key, name in layers.items()
            },
        }

    # ------------------------------------------------------------- geocoding

    @staticmethod
    def _country_from_record(rec: dict[str, Any]) -> CountryHit:
        def pick(*keys: str) -> str | None:
            for k in keys:
                v = rec.get(k)
                if isinstance(v, str):
                    v = v.strip()
                    if v and v not in {"-99", "-1", "" }:
                        return v
            return None

        return CountryHit(
            name=pick("NAME", "NAME_EN", "ADMIN", "SOVEREIGNT") or "Unknown",
            name_long=pick("NAME_LONG", "FORMAL_EN"),
            iso_a2=pick("ISO_A2_EH", "ISO_A2", "WB_A2", "FIPS_10"),
            iso_a3=pick("ISO_A3_EH", "ISO_A3", "ADM0_A3", "WB_A3"),
            continent=pick("CONTINENT"),
            subregion=pick("SUBREGION", "REGION_UN"),
            sovereign=pick("SOVEREIGNT"),
        )

    def country_at(self, lat: float, lon: float) -> CountryHit | None:
        rec = self._countries.locate(lon, lat)
        return self._country_from_record(rec) if rec else None

    def country_named(self, name: str | None) -> CountryHit | None:
        """The country a place record names (its ADM0NAME), with its codes."""
        if not name:
            return None
        if self._country_names is None:
            self._countries.load()
            names: dict[str, dict[str, Any]] = {}
            for rec in self._countries.records:
                for key in ("ADMIN", "NAME", "NAME_LONG", "GEOUNIT", "NAME_EN"):
                    v = rec.get(key)
                    if isinstance(v, str) and v.strip():
                        names.setdefault(v.strip().casefold(), rec)
            self._country_names = names
        rec = self._country_names.get(name.strip().casefold())
        return self._country_from_record(rec) if rec else None

    def admin1_at(self, lat: float, lon: float) -> Admin1Hit | None:
        try:
            rec = self._admin1.locate(lon, lat)
        except GeodataMissing:
            return None
        if not rec:
            return None

        def pick(*keys: str) -> str | None:
            for k in keys:
                v = rec.get(k)
                if isinstance(v, str) and v.strip() and v.strip() != "-99":
                    return v.strip()
            return None

        name = pick("name_en", "name", "gn_name", "woe_name")
        if not name:
            return None
        return Admin1Hit(
            name=name,
            iso_3166_2=pick("iso_3166_2"),
            admin_type=pick("type_en", "type"),
            country_name=pick("admin", "geonunit"),
        )

    # ---------------------------------------------------------------- places

    def _ensure_places(self) -> list[tuple[float, float, dict[str, Any]]]:
        """Load the gazetteer and index every name spelling it carries.

        Natural Earth ships each settlement's name in ~30 languages, so a sign
        photographed in Moscow reading a Cyrillic city name can be matched
        against NAME_RU directly -- no transliteration step, no online lookup.
        """
        if self._place_points is not None:
            return self._place_points
        with self._lock:
            if self._place_points is not None:
                return self._place_points
            self._places.load()
            name_fields = [
                f for f in self._places.fields
                if f.upper().startswith("NAME") and f.upper() != "NAME_LEN"
            ]
            pts: list[tuple[float, float, dict[str, Any]]] = []
            index: dict[str, list[tuple[int, str]]] = {}
            for i, rec in enumerate(self._places.records):
                geom = self._places.geometries[i][0]
                if geom:
                    lon, lat = float(geom[0][0]), float(geom[0][1])
                else:
                    try:
                        lat = float(rec.get("LATITUDE"))
                        lon = float(rec.get("LONGITUDE"))
                    except (TypeError, ValueError):
                        continue
                pts.append((lat, lon, rec))
                idx = len(pts) - 1
                for key in name_fields:
                    raw = rec.get(key)
                    if not isinstance(raw, str) or not raw.strip():
                        continue
                    for alt in raw.split("|"):
                        norm = _normalise(alt)
                        # Two-character names are ambiguous noise for OCR
                        # matching; CJK names are legitimately short, so the
                        # floor is lower once a name is non-Latin.
                        floor = 2 if any(ord(c) > 0x2E80 for c in norm) else 4
                        if len(norm) >= floor:
                            index.setdefault(norm, []).append((idx, key))
            self._place_points = pts
            self._place_index = index
            return pts

    @property
    def place_count(self) -> int:
        try:
            return len(self._ensure_places())
        except GeodataMissing:
            return 0

    def nearest_places(self, lat: float, lon: float, limit: int = 5,
                       max_km: float = 400.0) -> list[PlaceHit]:
        try:
            points = self._ensure_places()
        except GeodataMissing:
            return []
        scored: list[tuple[float, tuple[float, float, dict[str, Any]]]] = []
        # Cheap degree-box prefilter before the trigonometry.
        dlat = max_km / 111.0
        cos_lat = max(0.08, abs(math.cos(math.radians(lat))))
        dlon = max_km / (111.0 * cos_lat)
        for entry in points:
            if abs(entry[0] - lat) > dlat:
                continue
            dl = abs(entry[1] - lon)
            if dl > 180.0:
                dl = 360.0 - dl
            if dl > dlon:
                continue
            d = haversine_km(lat, lon, entry[0], entry[1])
            if d <= max_km:
                scored.append((d, entry))
        scored.sort(key=lambda kv: kv[0])
        out: list[PlaceHit] = []
        for d, (plat, plon, rec) in scored[:limit]:
            pop = rec.get("POP_MAX")
            try:
                pop_i = int(pop) if pop not in (None, "", -99) else None
            except (TypeError, ValueError):
                pop_i = None
            out.append(
                PlaceHit(
                    name=str(rec.get("NAME") or rec.get("NAMEASCII") or "?"),
                    admin0=(str(rec.get("ADM0NAME")) if rec.get("ADM0NAME") else None),
                    admin1=(str(rec.get("ADM1NAME")) if rec.get("ADM1NAME") else None),
                    latitude=round(plat, 6),
                    longitude=round(plon, 6),
                    population=pop_i,
                    distance_km=round(d, 2),
                    bearing_deg=round(bearing_deg(lat, lon, plat, plon), 1),
                    feature_class=(
                        str(rec.get("FEATURECLA")) if rec.get("FEATURECLA") else None
                    ),
                )
            )
        return out

    def find_place(self, name: str, limit: int = 8) -> list[GazetteerMatch]:
        """Gazetteer lookup by name, in any of the languages the data carries.

        Case- and diacritic-insensitive, so OCR that loses an accent still
        matches.  Results are ordered by population, because a sign reading
        "Springfield" is far more likely to be the large one. Where Natural
        Earth knows no such place, GeoNames is asked, when it is switched on
        and built.
        """
        try:
            self._ensure_places()
        except GeodataMissing:
            return self._from_geonames(name, limit)
        assert self._place_index is not None and self._place_points is not None
        hits = self._place_index.get(_normalise(name), [])
        out: list[GazetteerMatch] = []
        seen: set[int] = set()
        for idx, field_name in hits:
            if idx in seen:
                continue
            seen.add(idx)
            lat, lon, rec = self._place_points[idx]
            pop = rec.get("POP_MAX")
            try:
                pop_i = int(pop) if pop not in (None, "", -99) else None
            except (TypeError, ValueError):
                pop_i = None
            out.append(
                GazetteerMatch(
                    name=str(rec.get("NAME") or name),
                    matched_text=name,
                    matched_field=field_name,
                    latitude=round(lat, 6),
                    longitude=round(lon, 6),
                    admin0=str(rec.get("ADM0NAME")) if rec.get("ADM0NAME") else None,
                    admin1=str(rec.get("ADM1NAME")) if rec.get("ADM1NAME") else None,
                    population=pop_i,
                )
            )
        out.sort(key=lambda m: -(m.population or 0))
        return out[:limit] if out else self._from_geonames(name, limit)

    def _from_geonames(self, name: str, limit: int) -> list[GazetteerMatch]:
        """GeoNames, for names written in a script other than Latin only.

        Its 13 million names carry ordinary English words as places: on
        Indian photographs "ROAD" matched Rode, Somerset (spelled Road until
        1919), "Bamboo" a Jamaican village, "SINDH" on a sign in Leh the
        province of Pakistan. Where it adds what Natural Earth lacks is the
        local scripts -- "கோவை" is Coimbatore -- and there an ordinary word
        is far less likely to be a place somewhere else.
        """
        if not self.use_geonames or _has_latin(name):
            return []
        if self._geonames is None:
            from .geonames import GeoNames
            self._geonames = GeoNames()
        try:
            return self._geonames.find(name, limit)
        except Exception:                 # a damaged or half-built index
            return []

    def locate(self, lat: float, lon: float, place_limit: int = 5
               ) -> OfflineLocation:
        country = self.country_at(lat, lon)
        loc = OfflineLocation(
            country=country,
            admin1=self.admin1_at(lat, lon) if country else None,
            nearest_places=self.nearest_places(lat, lon, limit=place_limit),
            offshore=country is None,
        )
        if loc.offshore and loc.nearest_places:
            # Coastal points often miss the polygon by a few hundred metres;
            # attribute them to the nearest settlement's country instead.
            # The whole country, codes and all: with a name alone every check
            # that compares countries passed it by -- a Greenland village
            # escaped the Tamil script read on the same photograph.
            nearest = loc.nearest_places[0]
            if nearest.distance_km < 30.0 and nearest.admin0:
                loc.country = self.country_named(nearest.admin0) or CountryHit(
                    name=nearest.admin0, name_long=None, iso_a2=None,
                    iso_a3=None, continent=None, subregion=None,
                )
        return loc


@functools.lru_cache(maxsize=4)
def get_geocoder(data_dir: str | None = None) -> OfflineGeocoder:
    """Process-wide geocoder; the shapefiles are loaded once."""
    return OfflineGeocoder(data_dir)
