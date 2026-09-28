"""Ground imagery, fetched once per tile and kept in the Mapify folder.

The 3D view lays buildings over imagery, and imagery comes from public
tile services. Rather than widen the page's content policy to each of
them, every tile is fetched here, by the application, and written to
disk: a place visited once can be revisited offline, a tile is never
asked for twice, and the page keeps talking only to its own origin.

Sources, and what each honestly shows
-------------------------------------

  usgs     USGS The National Map orthoimagery (USDA NAIP). Public
           domain. The only free source here that resolves individual
           houses -- about 2 m a pixel at zoom 16 -- and it covers the
           United States only. Outside it the service returns nothing.
  hls      NASA's Harmonized Landsat Sentinel-2 (Landsat 8/9, "L30")
           via GIBS: dated, recent, 30 m a pixel. A house is a fraction
           of one pixel. Good for land cover and season, not buildings.
  weld     Landsat WELD annual composite via GIBS: cloud-free, 30 m,
           around the year 2000.
  builtup  The Landsat-derived Human Built-up and Settlement Extent: an
           overlay marking where settlements are, at the same 30 m.

A tile the source does not have is remembered as missing, so a view
over the Bay of Bengal does not ask the USGS for Chennai on every frame.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..paths import state_dir

log = logging.getLogger("mapify.tiles")

__all__ = ["TileSource", "SOURCES", "TileCache", "sources_for"]

_UA = ("Mapify/0.1 (open-source image geolocation; "
       "https://github.com/mdyounus-git/mapify)")
_GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"


@dataclass(frozen=True)
class TileSource:
    key: str
    name: str
    url: str                     # {z} {x} {y} and optionally {date}
    max_zoom: int
    ext: str
    metres_per_pixel_equator: float
    attribution: str
    licence: str
    shows_buildings: bool
    overlay: bool = False
    dated: bool = False
    note: str = ""


SOURCES: dict[str, TileSource] = {s.key: s for s in (
    TileSource(
        key="usgs", name="USGS National Map orthoimagery",
        url=("https://basemap.nationalmap.gov/arcgis/rest/services/"
             "USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"),
        max_zoom=16, ext="jpg", metres_per_pixel_equator=2.39,
        attribution=("USGS The National Map: Orthoimagery (USDA NAIP). Map "
                     "services and data available from U.S. Geological "
                     "Survey, National Geospatial Program."),
        licence="Public domain (U.S. Government work)",
        shows_buildings=True,
        note="United States only."),
    TileSource(
        key="hls", name="Landsat 8/9 (NASA HLS L30)",
        url=(f"{_GIBS}/HLS_L30_Nadir_BRDF_Adjusted_Reflectance/default/"
             "{date}/GoogleMapsCompatible_Level12/{z}/{y}/{x}.png"),
        max_zoom=12, ext="png", metres_per_pixel_equator=38.2,
        attribution=("NASA Harmonized Landsat Sentinel-2 (HLS) via NASA "
                     "GIBS; Landsat data courtesy of the U.S. Geological "
                     "Survey."),
        licence="Public domain; GIBS: Fees none, AccessConstraints none",
        shows_buildings=False, dated=True,
        note="30 m per pixel: individual buildings are not visible."),
    TileSource(
        key="weld", name="Landsat WELD annual composite",
        url=(f"{_GIBS}/Landsat_WELD_CorrectedReflectance_TrueColor_Global_"
             "Annual/default/2000-12-01/GoogleMapsCompatible_Level12/"
             "{z}/{y}/{x}.jpg"),
        max_zoom=12, ext="jpg", metres_per_pixel_equator=38.2,
        attribution=("Landsat WELD via NASA GIBS; Landsat data courtesy of "
                     "the U.S. Geological Survey."),
        licence="Public domain; GIBS: Fees none, AccessConstraints none",
        shows_buildings=False,
        note="Cloud-free composite from about 2000, 30 m per pixel."),
    TileSource(
        key="builtup", name="Landsat built-up settlement extent",
        url=(f"{_GIBS}/Landsat_Human_Built-up_And_Settlement_Extent/default/"
             "default/GoogleMapsCompatible_Level12/{z}/{y}/{x}.png"),
        max_zoom=12, ext="png", metres_per_pixel_equator=38.2,
        attribution=("Global Human Built-up and Settlement Extent (HBASE), "
                     "from Landsat, via NASA GIBS / SEDAC."),
        licence="Public domain; GIBS: Fees none, AccessConstraints none",
        shows_buildings=False, overlay=True,
        note="Marks where settlements are; not individual buildings."),
)}

# Bounding boxes the USGS imagery actually covers. Checked against the
# service: a tile outside these returns 404 at every zoom.
_US_BOXES = (
    (-125.0, 24.3, -66.9, 49.5),       # conterminous United States
    (-170.0, 51.0, -129.0, 71.6),      # Alaska
    (-160.6, 18.8, -154.7, 22.4),      # Hawaii
    (-67.4, 17.8, -65.2, 18.6),        # Puerto Rico
)


def sources_for(lat: float, lon: float) -> list[dict]:
    """Which imagery is worth showing here, best first, and why."""
    in_us = any(b[0] <= lon <= b[2] and b[1] <= lat <= b[3] for b in _US_BOXES)
    # The cloud-free composite first outside the US: it covers every tile.
    # The dated Landsat layer is patchier -- most days a given spot was not
    # imaged -- so it is offered, and searched for, rather than defaulted.
    order = (["usgs"] if in_us else []) + ["weld", "hls", "builtup"]
    out = []
    for key in order:
        s = SOURCES[key]
        out.append({"key": s.key, "name": s.name, "max_zoom": s.max_zoom,
                    "metres_per_pixel_equator": s.metres_per_pixel_equator,
                    "shows_buildings": s.shows_buildings, "overlay": s.overlay,
                    "dated": s.dated, "attribution": s.attribution,
                    "licence": s.licence, "note": s.note})
    return out


class TileCache:
    """Fetch-once tile storage under the state directory."""

    def __init__(self, root: Path | None = None, offline: bool = False):
        self.root = Path(root) if root else state_dir() / "tiles"
        self.offline = offline
        self._slots = threading.Semaphore(4)     # polite to the upstreams

    def _path(self, source: TileSource, z: int, x: int, y: int,
              date: str | None) -> Path:
        sub = self.root / source.key
        if source.dated:
            sub = sub / (date or "latest")
        return sub / str(z) / str(x) / f"{y}.{source.ext}"

    def get(self, key: str, z: int, x: int, y: int,
            date: str | None = None) -> tuple[bytes | None, str]:
        """(bytes, media type), or (None, reason) when there is no tile."""
        source = SOURCES.get(key)
        if source is None:
            return None, "unknown source"
        if not (0 <= z <= source.max_zoom) or not (0 <= x < 2 ** z) \
                or not (0 <= y < 2 ** z):
            return None, "outside the source's range"
        if source.dated and date is not None:
            # A date is a path component; only a plain ISO date gets in.
            if len(date) != 10 or date[4] != "-" or date[7] != "-" or \
                    not (date[:4] + date[5:7] + date[8:]).isdigit():
                return None, "bad date"
        path = self._path(source, z, x, y, date)
        missing = path.with_suffix(".missing")
        media = "image/png" if source.ext == "png" else "image/jpeg"
        if path.is_file():
            return path.read_bytes(), media
        if missing.is_file():
            return None, "the source has no tile here"
        if self.offline:
            return None, "offline, and not cached"

        url = (source.url.replace("{z}", str(z)).replace("{x}", str(x))
               .replace("{y}", str(y))
               .replace("{date}", date or time.strftime("%Y-%m-%d")))
        with self._slots:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (400, 404):
                    missing.parent.mkdir(parents=True, exist_ok=True)
                    missing.write_text(str(exc.code))
                    return None, "the source has no tile here"
                return None, f"upstream HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                return None, f"unreachable: {exc}"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data, media
