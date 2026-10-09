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

National aerial photographs, each offered only inside its own country, in
which houses, cars and road markings are visible:

  fr       IGN BD ORTHO through the Geoplateforme, about 20 cm a pixel
           (Etalab Open Licence 2.0). Metropolitan France and the overseas
           departments.
  nl       Beeldmateriaal Nederland "Actueel" through PDOK, 8 cm (CC BY 4.0).
  ch       swisstopo SWISSIMAGE, 10 cm (Swiss open government data).
  es       PNOA through IGN Espana, 25 cm (CC BY 4.0).
  jp       GSI seamless aerial photographs (GSI tile terms, with credit).

Outside its coverage a national service may answer with a flat fill
rather than an error (white over Belgium, beige over Portugal); such a
tile is recognised and remembered as missing like a 404.

And one that is not imagery: the ground's height ("terrain"), as 256 px
Terrarium PNGs (height = R*256 + G + B/256 - 32768 m), from two sources:

  mapterhorn  Mapterhorn (mapterhorn.com): Copernicus GLO-30 everywhere and
           bare-earth national models at 0.25-2 m in about 30 countries,
           as 512 px lossless WebP. Bare earth matters in a city: at Dam
           Square, Amsterdam, it reads 1.8 m where the AWS tiles read 13.8 m
           -- roofs, not ground -- and Bern's old town 540 m against 550.
           Each terrain tile is cut from the deepest Mapterhorn tile above
           it (at most four levels up), resampled as heights, not colours.
  terrain  Terrain Tiles on AWS (Mapzen / Tilezen), merged from SRTM, 3DEP,
           GMTED2010, ETOPO1 and national models: used where Mapterhorn has
           no tile (the open ocean, where it carries the sea floor), when it
           cannot be reached, and offline from what was kept before.

Both are free to use with attribution (see ATTRIBUTION.md); the 3D view
lifts the ground, buildings and streets onto them. Never offered as imagery.

A tile the source does not have is remembered as missing, so a view
over the Bay of Bengal does not ask the USGS for Chennai on every frame.

Imagery can be asked for up to OVERZOOM levels past its deepest zoom: the
deepest real tile is cut and enlarged. That adds no detail -- 30 m Landsat
stays 30 m -- but it lets the terrain be drawn in small tiles everywhere,
which keeps the streets and parks laid over it sharp.
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
OVERZOOM = 4
MAPTERHORN_ATTRIBUTION = (
    "Terrain: Mapterhorn (mapterhorn.com), from open elevation data listed at "
    "mapterhorn.com/attribution -- national terrain models and Copernicus "
    "WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH "
    "2014-2018, provided under COPERNICUS by the European Union and ESA")
MAPTERHORN_LEVELS_UP = 4        # how far up to look for a Mapterhorn tile
TERRAIN_ATTRIBUTION = (
    "Terrain: Terrain Tiles on AWS (Mapzen/Tilezen) -- SRTM, USGS 3DEP, "
    "GMTED2010, ETOPO1 (public domain); EU-DEM (Copernicus); Geoscience "
    "Australia (CC BY 4.0); LINZ (CC BY 3.0); Kartverket (CC BY 4.0); "
    "Austria DGM (CC BY 3.0); Canada CDEM (OGL-Canada); UK EA (OGL v3); "
    "INEGI; ArcticDEM (DigitalGlobe imagery, NSF awards 1043681, 1559691, "
    "1542736)")


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
    overzoom: bool = True        # imagery: may be cut and enlarged past max_zoom
    countries: tuple[str, ...] = ()   # national imagery: only offered in these


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
    TileSource(
        key="fr", name="IGN BD ORTHO (France)",
        url=("https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
             "&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM"
             "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/jpeg"),
        max_zoom=19, ext="jpg", metres_per_pixel_equator=0.30,
        attribution="IGN - BD ORTHO, through the Geoplateforme (data.geopf.fr)",
        licence="Etalab Open Licence 2.0",
        shows_buildings=True, countries=("FR",),
        note="Aerial photographs of France, about 20 cm a pixel."),
    TileSource(
        key="nl", name="Beeldmateriaal Nederland (Netherlands)",
        url=("https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_orthoHR/"
             "EPSG:3857/{z}/{x}/{y}.jpeg"),
        max_zoom=20, ext="jpg", metres_per_pixel_equator=0.15,
        attribution="Beeldmateriaal Nederland (Kadaster), through PDOK",
        licence="CC BY 4.0",
        shows_buildings=True, countries=("NL",),
        note="Aerial photographs of the Netherlands, 8 cm a pixel."),
    TileSource(
        key="ch", name="swisstopo SWISSIMAGE (Switzerland)",
        url=("https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/"
             "current/3857/{z}/{x}/{y}.jpeg"),
        max_zoom=20, ext="jpg", metres_per_pixel_equator=0.15,
        attribution="(c) swisstopo - SWISSIMAGE",
        licence="Swiss open government data: free use, source credited",
        shows_buildings=True, countries=("CH", "LI"),
        note="Aerial photographs of Switzerland, 10 cm a pixel."),
    TileSource(
        key="es", name="PNOA (Spain)",
        url=("https://www.ign.es/wmts/pnoa-ma?request=GetTile&service=WMTS"
             "&VERSION=1.0.0&Layer=OI.OrthoimageCoverage&Style=default"
             "&Format=image/jpeg&TileMatrixSet=GoogleMapsCompatible"
             "&TileMatrix={z}&TileRow={y}&TileCol={x}"),
        max_zoom=20, ext="jpg", metres_per_pixel_equator=0.15,
        attribution="PNOA cedido por (c) Instituto Geografico Nacional de Espana",
        licence="CC BY 4.0 (scne.es)",
        shows_buildings=True, countries=("ES",),
        note="Aerial photographs of Spain, 25 cm a pixel."),
    TileSource(
        key="jp", name="GSI seamless aerial photographs (Japan)",
        url="https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg",
        max_zoom=18, ext="jpg", metres_per_pixel_equator=0.60,
        attribution="Chiriin tiles (GSI tiles), Geospatial Information Authority of Japan",
        licence="GSI content terms of use (compatible with CC BY 4.0), credited",
        shows_buildings=True, countries=("JP",),
        note="Aerial photographs of Japan."),
    TileSource(
        key="mapterhorn", name="Mapterhorn (elevation)",
        url="https://tiles.mapterhorn.com/{z}/{x}/{y}.webp",
        max_zoom=17, ext="webp", metres_per_pixel_equator=2.4,
        attribution=MAPTERHORN_ATTRIBUTION,
        licence="Open licences per source: CC BY 4.0, public domain, Etalab 2.0, "
                "DL-DE BY 2.0, GSI terms (mapterhorn.com/attribution)",
        shows_buildings=False, overzoom=False,
        note="Ground height (512 px tiles), not imagery."),
    TileSource(
        key="terrain", name="Terrain Tiles (elevation)",
        url="https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
        max_zoom=15, ext="png", metres_per_pixel_equator=4.8,
        attribution=TERRAIN_ATTRIBUTION,
        licence="Public domain and open licences per source (see ATTRIBUTION.md)",
        shows_buildings=False, overzoom=False,
        note="Ground height, not imagery."),
)}

# Bounding boxes the USGS imagery actually covers. Checked against the
# service: a tile outside these returns 404 at every zoom.
_US_BOXES = (
    (-125.0, 24.3, -66.9, 49.5),       # conterminous United States
    (-170.0, 51.0, -129.0, 71.6),      # Alaska
    (-160.6, 18.8, -154.7, 22.4),      # Hawaii
    (-67.4, 17.8, -65.2, 18.6),        # Puerto Rico
)


def _country(lat: float, lon: float) -> str | None:
    """ISO code of the country here, from the bundled Natural Earth, or None."""
    try:
        from ..geo.offline import get_geocoder
        hit = get_geocoder().country_at(lat, lon)
    except Exception:                       # geodata not installed
        return None
    return hit.iso_a2 if hit else None


def sources_for(lat: float, lon: float) -> list[dict]:
    """Which imagery is worth showing here, best first, and why."""
    in_us = any(b[0] <= lon <= b[2] and b[1] <= lat <= b[3] for b in _US_BOXES)
    country = _country(lat, lon)
    national = [s.key for s in SOURCES.values() if country and country in s.countries]
    # The cloud-free composite first outside the US: it covers every tile.
    # The dated Landsat layer is patchier -- most days a given spot was not
    # imaged -- so it is offered, and searched for, rather than defaulted.
    order = national + (["usgs"] if in_us else []) + ["weld", "hls", "builtup"]
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
        if key == "terrain" and 0 <= z <= source.max_zoom + 3 \
                and 0 <= x < 2 ** z and 0 <= y < 2 ** z:
            data, _why = self._from_mapterhorn(z, x, y)
            if data is not None:
                return data, "image/png"
            if z > source.max_zoom:
                return None, "outside the source's range"
        if source.overzoom and source.max_zoom < z <= source.max_zoom + OVERZOOM \
                and 0 <= x < 2 ** z and 0 <= y < 2 ** z:
            return self._overzoomed(source, z, x, y, date)
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
        media = {"png": "image/png", "webp": "image/webp"}.get(source.ext, "image/jpeg")
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
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": _UA})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = resp.read()
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code in (400, 404):
                        missing.parent.mkdir(parents=True, exist_ok=True)
                        missing.write_text(str(exc.code))
                        return None, "the source has no tile here"
                    return None, f"upstream HTTP {exc.code}"
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    # A dropped connection is worth one more try or two (IGN
                    # Espana dropped 4 requests in 12, each fine when asked
                    # again); a timeout has already cost 30 s and is not.
                    timed_out = isinstance(exc, TimeoutError) or isinstance(
                        getattr(exc, "reason", None), TimeoutError)
                    if attempt == 2 or timed_out:
                        return None, f"unreachable: {exc}"
                    time.sleep(1.0)
        if source.countries and _is_fill(data):
            missing.parent.mkdir(parents=True, exist_ok=True)
            missing.write_text("fill")
            return None, "the source has no tile here"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data, media

    def _from_mapterhorn(self, z: int, x: int, y: int) -> tuple[bytes | None, str]:
        """A 256 px Terrarium tile cut from the deepest Mapterhorn tile above it.

        Mapterhorn's tiles are 512 px, so its tile at z-1 holds this one
        exactly; where its finest data stops, a tile further up is cut and
        enlarged. The heights are decoded first and resampled as numbers --
        interpolating Terrarium's packed colours would mix a metre's high
        byte with another's low one.
        """
        path = self.root / "terrain-mapterhorn" / str(z) / str(x) / f"{y}.png"
        if path.is_file():
            return path.read_bytes(), "image/png"
        top = SOURCES["mapterhorn"].max_zoom
        for pz in range(min(z - 1, top), max(-1, z - 1 - MAPTERHORN_LEVELS_UP), -1):
            dz = z - pz
            parent, why = self.get("mapterhorn", pz, x >> dz, y >> dz)
            if parent is not None:
                break
            if why not in ("the source has no tile here", "offline, and not cached"):
                return None, why            # unreachable: let the AWS tiles try
            # Offline, a coarser tile kept from an earlier visit still serves.
        else:
            return None, "no Mapterhorn tile here"
        from io import BytesIO

        import numpy as np
        from PIL import Image
        with Image.open(BytesIO(parent)) as im:
            rgb = np.asarray(im.convert("RGB"), dtype=np.float64)
        heights = rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768
        part = heights.shape[0] >> dz            # this tile's share: 256 px one level up
        ox, oy = (x - ((x >> dz) << dz)) * part, (y - ((y >> dz) << dz)) * part
        cut = heights[oy:oy + part, ox:ox + part].astype(np.float32)
        if part != 256:
            cut = np.asarray(Image.fromarray(cut).resize((256, 256), Image.BILINEAR))
        v = np.clip(cut.astype(np.float64) + 32768, 0, 65535.996)
        whole = np.floor(v)
        out = np.stack([whole // 256, whole % 256, np.floor((v - whole) * 256)], axis=-1)
        buf = BytesIO()
        Image.fromarray(out.astype(np.uint8), "RGB").save(buf, "PNG")
        data = buf.getvalue()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data, "image/png"

    def _overzoomed(self, source: TileSource, z: int, x: int, y: int,
                    date: str | None) -> tuple[bytes | None, str]:
        """A tile past the source's deepest zoom: its quarter (or smaller
        part) of the deepest real tile, enlarged to 256 px and kept."""
        path = self._path(source, z, x, y, date)
        media = "image/png" if source.ext == "png" else "image/jpeg"
        if path.is_file():
            return path.read_bytes(), media
        dz = z - source.max_zoom
        px, py = x >> dz, y >> dz
        parent, why = self.get(source.key, source.max_zoom, px, py, date)
        if parent is None:
            return None, why
        from io import BytesIO

        from PIL import Image
        part = 256 >> dz
        ox, oy = (x - (px << dz)) * part, (y - (py << dz)) * part
        with Image.open(BytesIO(parent)) as im:
            im = im.convert("RGBA" if source.ext == "png" else "RGB")
            cut = im.crop((ox, oy, ox + part, oy + part)).resize((256, 256), Image.BILINEAR)
            buf = BytesIO()
            if source.ext == "png":
                cut.save(buf, "PNG")
            else:
                cut.save(buf, "JPEG", quality=90)
        data = buf.getvalue()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data, media


def _is_fill(data: bytes) -> bool:
    """A national service's "nothing here": one flat colour in a tiny file.

    Measured: IGN Espana answers over Portugal with 1.1 KB of beige
    (deviation under 5) and over the Atlantic with 0.9 KB of flat dark, PDOK
    over Belgium with 1 KB of white; a real aerial tile here was 13-30 KB.
    Both tests together, so a real calm sea, smooth but larger, survives.
    """
    if len(data) > 2500:
        return False
    try:
        from io import BytesIO

        from PIL import Image, ImageStat
        with Image.open(BytesIO(data)) as im:
            spread = ImageStat.Stat(im.convert("RGB")).stddev
    except Exception:
        return False
    return max(spread) < 6.0
