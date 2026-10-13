"""Satellite and aerial imagery for the 2D map, fetched by the application.

The map shows real imagery under the streets and building footprints. The
page's content policy lets it talk to Mapify's own server only, so every
tile is fetched here and handed on; where the source allows it, the tile is
also written to disk, so a place visited once can be revisited offline and
no tile is asked for twice.

Sources, and what each honestly shows
-------------------------------------

With a key the owner creates himself (free tiers; see ATTRIBUTION.md), one
of two worldwide sources of sub-metre imagery -- the only way to see
buildings from above in most of the world, India, Africa and South-East
Asia included:

  esri     Esri World Imagery, through the ArcGIS Location Platform: about
           0.3-0.5 m a pixel in most cities, coarser in the countryside.
           Free tier 2,000,000 basemap tiles a month (location.arcgis.com).
  mapbox   Mapbox Satellite, through the Raster Tiles API, with a token.

Neither documents permission to store tiles, so these are passed through
and never written to disk; the key stays in the local config file and is
never sent to the page.

Without a key, the open sources -- national aerial photographs, each
offered only inside its own country, in which houses, cars and road
markings are visible:

  fr       IGN BD ORTHO through the Geoplateforme, about 20 cm a pixel
           (Etalab Open Licence 2.0). Metropolitan France and the overseas
           departments.
  nl       Beeldmateriaal Nederland "Actueel" through PDOK, 8 cm (CC BY 4.0).
  ch       swisstopo SWISSIMAGE, 10 cm (Swiss open government data).
  es       PNOA through IGN Espana, 25 cm (CC BY 4.0).
  jp       GSI seamless aerial photographs (GSI tile terms, with credit).
  usgs     USGS The National Map orthoimagery (USDA NAIP), public domain,
           about 1 m: the United States only.

and everywhere else

  weld     Landsat WELD annual composite via NASA GIBS: cloud-free, 30 m a
           pixel, around the year 2000. Districts and fields, not buildings.

Outside its coverage a national service may answer with a flat fill rather
than an error (white over Belgium, beige over Portugal); such a tile is
recognised and remembered as missing like a 404. A tile the source does not
have is remembered as missing, so a view over the Bay of Bengal does not ask
the USGS for Chennai on every frame.

Imagery can be asked for up to OVERZOOM levels past its deepest zoom: the
deepest real tile is cut and enlarged. That adds no detail -- 30 m Landsat
stays 30 m -- and the map says so.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..paths import state_dir

log = logging.getLogger("mapify.tiles")

__all__ = ["TileSource", "SOURCES", "TileCache", "sources_for", "KEYED"]

_UA = ("Mapify/0.1 (open-source image geolocation; "
       "https://github.com/mdyounus-git/mapify)")
_GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
OVERZOOM = 4


@dataclass(frozen=True)
class TileSource:
    key: str
    name: str
    url: str                     # {z} {x} {y}, and {key} for the keyed ones
    max_zoom: int
    ext: str
    metres_per_pixel_equator: float
    attribution: str
    licence: str
    shows_buildings: bool
    note: str = ""
    overzoom: bool = True        # may be cut and enlarged past max_zoom
    countries: tuple[str, ...] = ()   # national imagery: only offered in these
    keyed: bool = False          # needs the owner's key; never stored on disk


SOURCES: dict[str, TileSource] = {s.key: s for s in (
    TileSource(
        key="esri", name="Esri World Imagery",
        url=("https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/"
             "MapServer/tile/{z}/{y}/{x}?token={key}"),
        max_zoom=19, ext="jpg", metres_per_pixel_equator=0.30,
        attribution="Esri, Maxar, Earthstar Geographics, and the GIS User Community",
        licence="ArcGIS Location Platform terms; your own API key",
        shows_buildings=True, keyed=True, overzoom=False,
        note="Worldwide, about 0.3-0.5 m a pixel in most cities; coarser and "
             "sometimes years old in the countryside."),
    TileSource(
        key="mapbox", name="Mapbox Satellite",
        url="https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}.jpg90?access_token={key}",
        max_zoom=19, ext="jpg", metres_per_pixel_equator=0.30,
        attribution="(c) Mapbox (c) Maxar",
        licence="Mapbox terms of service; your own access token",
        shows_buildings=True, keyed=True, overzoom=False,
        note="Worldwide; resolution varies by place."),
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
        key="weld", name="Landsat WELD annual composite",
        url=(f"{_GIBS}/Landsat_WELD_CorrectedReflectance_TrueColor_Global_"
             "Annual/default/2000-12-01/GoogleMapsCompatible_Level12/"
             "{z}/{y}/{x}.jpg"),
        max_zoom=12, ext="jpg", metres_per_pixel_equator=38.2,
        attribution=("Landsat WELD via NASA GIBS; Landsat data courtesy of "
                     "the U.S. Geological Survey."),
        licence="Public domain; GIBS: Fees none, AccessConstraints none",
        shows_buildings=False,
        note="Cloud-free composite from about 2000, 30 m a pixel: districts "
             "and fields, not buildings."),
)}

KEYED = tuple(k for k, s in SOURCES.items() if s.keyed)

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


def describe(s: TileSource) -> dict:
    return {"key": s.key, "name": s.name, "max_zoom": s.max_zoom,
            "metres_per_pixel_equator": s.metres_per_pixel_equator,
            "shows_buildings": s.shows_buildings, "attribution": s.attribution,
            "licence": s.licence, "note": s.note, "keyed": s.keyed}


def sources_for(lat: float, lon: float, keys: dict[str, str] | None = None) -> list[dict]:
    """Which imagery is worth showing here, best first: the owner's keyed
    source when he has one, then the open sources that cover this place."""
    keys = keys or {}
    in_us = any(b[0] <= lon <= b[2] and b[1] <= lat <= b[3] for b in _US_BOXES)
    country = _country(lat, lon)
    keyed = [k for k in KEYED if keys.get(k)]
    national = [s.key for s in SOURCES.values() if country and country in s.countries]
    order = keyed + national + (["usgs"] if in_us else []) + ["weld"]
    return [describe(SOURCES[k]) for k in order]


class TileCache:
    """Fetch-once tile storage under the state directory; keyed sources are
    passed through without being stored."""

    def __init__(self, root: Path | None = None, offline: bool = False,
                 keys: dict[str, str] | None = None):
        self.root = Path(root) if root else state_dir() / "tiles"
        self.offline = offline
        self.keys = dict(keys or {})
        self._slots = threading.Semaphore(6)     # polite to the upstreams

    def _path(self, source: TileSource, z: int, x: int, y: int) -> Path:
        return self.root / source.key / str(z) / str(x) / f"{y}.{source.ext}"

    def get(self, key: str, z: int, x: int, y: int,
            date: str | None = None) -> tuple[bytes | None, str]:
        """(bytes, media type), or (None, reason) when there is no tile."""
        source = SOURCES.get(key)
        if source is None:
            return None, "unknown source"
        if source.keyed:
            return self._keyed(source, z, x, y)
        if source.overzoom and source.max_zoom < z <= source.max_zoom + OVERZOOM \
                and 0 <= x < 2 ** z and 0 <= y < 2 ** z:
            return self._overzoomed(source, z, x, y)
        if not (0 <= z <= source.max_zoom) or not (0 <= x < 2 ** z) \
                or not (0 <= y < 2 ** z):
            return None, "outside the source's range"
        path = self._path(source, z, x, y)
        missing = path.with_suffix(".missing")
        media = {"png": "image/png", "webp": "image/webp"}.get(source.ext, "image/jpeg")
        if path.is_file():
            return path.read_bytes(), media
        if missing.is_file():
            return None, "the source has no tile here"
        if self.offline:
            return None, "offline, and not cached"
        url = (source.url.replace("{z}", str(z)).replace("{x}", str(x))
               .replace("{y}", str(y)))
        data, why = self._fetch(url)
        if data is None:
            if why == "the source has no tile here":
                missing.parent.mkdir(parents=True, exist_ok=True)
                missing.write_text("404")
            return None, why
        if source.countries and _is_fill(data):
            missing.parent.mkdir(parents=True, exist_ok=True)
            missing.write_text("fill")
            return None, "the source has no tile here"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data, media

    def _keyed(self, source: TileSource, z: int, x: int, y: int) -> tuple[bytes | None, str]:
        key = self.keys.get(source.key, "")
        if not key:
            return None, "no key for this source"
        if self.offline:
            return None, "offline"
        if not (0 <= z <= source.max_zoom) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
            return None, "outside the source's range"
        url = (source.url.replace("{z}", str(z)).replace("{x}", str(x))
               .replace("{y}", str(y)).replace("{key}", urllib.parse.quote(key, safe="")))
        data, why = self._fetch(url)
        if data is None:
            return None, why
        return data, "image/jpeg"

    def _fetch(self, url: str) -> tuple[bytes | None, str]:
        with self._slots:
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": _UA})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        return resp.read(), "ok"
                except urllib.error.HTTPError as exc:
                    if exc.code in (400, 404):
                        return None, "the source has no tile here"
                    if exc.code in (401, 403, 498, 499):
                        # Never echo the URL: it carries the key.
                        return None, "the imagery key was refused"
                    return None, f"upstream HTTP {exc.code}"
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    # A dropped connection is worth one more try or two (IGN
                    # Espana dropped 4 requests in 12, each fine when asked
                    # again); a timeout has already cost 30 s and is not.
                    timed_out = isinstance(exc, TimeoutError) or isinstance(
                        getattr(exc, "reason", None), TimeoutError)
                    if attempt == 2 or timed_out:
                        return None, "unreachable"
                    time.sleep(1.0)
        return None, "unreachable"

    def _overzoomed(self, source: TileSource, z: int, x: int,
                    y: int) -> tuple[bytes | None, str]:
        """A tile past the source's deepest zoom: its quarter (or smaller
        part) of the deepest real tile, enlarged to 256 px and kept."""
        path = self._path(source, z, x, y)
        media = "image/png" if source.ext == "png" else "image/jpeg"
        if path.is_file():
            return path.read_bytes(), media
        dz = z - source.max_zoom
        px, py = x >> dz, y >> dz
        parent, why = self.get(source.key, source.max_zoom, px, py)
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
