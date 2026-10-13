"""The 2D map's streets, names and fonts, served from Mapify's own origin.

OpenStreetMap through OpenFreeMap (openfreemap.org: no key, no limits on
requests, ODbL data, OpenMapTiles schema) gives the map its roads, place
names, water and boundaries as vector tiles; the label fonts (Noto Sans,
SIL Open Font License) come from the same host. The page may talk to
Mapify's server only, so both are fetched here and kept on disk:
OpenFreeMap invites that, and a place seen once is then legible offline.

Tiles are kept under the planet build that served them -- OpenFreeMap
publishes a new one each week and names it in the tile URL -- so a new
build is fetched fresh and the old one can be deleted whole.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..paths import state_dir

__all__ = ["BasemapProxy", "ATTRIBUTION"]

TILEJSON = "https://tiles.openfreemap.org/planet"
GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf"
ATTRIBUTION = ('<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> '
               '<a href="https://www.openmaptiles.org/" target="_blank">&copy; OpenMapTiles</a> '
               'Data from <a href="https://www.openstreetmap.org/copyright" '
               'target="_blank">OpenStreetMap</a>')
MAX_ZOOM = 14
_UA = ("Mapify/0.1 (open-source image geolocation; "
       "https://github.com/mdyounus-git/mapify)")
_FONTSTACK = re.compile(r"^[A-Za-z0-9 ,\-]{1,96}$")
_RANGE = re.compile(r"^\d{1,5}-\d{1,5}$")


class BasemapProxy:
    def __init__(self, root: Path | None = None, offline: bool = False):
        self.root = Path(root) if root else state_dir() / "basemap"
        self.offline = offline
        self._tilejson: dict | None = None
        self._tj_at = 0.0
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(6)

    # ------------------------------------------------------------ tiles

    def _template(self) -> str | None:
        """The current planet build's tile URL, refreshed daily."""
        with self._lock:
            path = self.root / "tilejson.json"
            if self._tilejson is None and path.is_file():
                try:
                    self._tilejson = json.loads(path.read_text(encoding="utf-8"))
                    self._tj_at = path.stat().st_mtime
                except (OSError, ValueError):
                    self._tilejson = None
            stale = time.time() - self._tj_at > 86_400
            if (self._tilejson is None or stale) and not self.offline:
                fresh = self._get(TILEJSON)
                if fresh:
                    try:
                        self._tilejson = json.loads(fresh)
                        self._tj_at = time.time()
                        self.root.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(fresh)
                    except ValueError:
                        pass
            if not self._tilejson:
                return None
            tiles = self._tilejson.get("tiles") or []
            return tiles[0] if tiles else None

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        """One vector tile (gzip-compressed protobuf, as served), or b""
        where the planet has nothing, or None when it cannot be had."""
        if not (0 <= z <= MAX_ZOOM and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
            return b""
        template = self._template()
        if not template:
            return None
        build = re.sub(r"[^A-Za-z0-9_.-]", "_", template.split("/{z}")[0].rsplit("/", 1)[-1])
        path = self.root / "tiles" / build / str(z) / str(x) / f"{y}.pbf"
        if path.is_file():
            return path.read_bytes()
        if self.offline:
            return None
        url = template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
        data = self._get(url, empty_ok=True)
        if data is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data

    # ------------------------------------------------------------ glyphs

    def glyphs(self, fontstack: str, glyph_range: str) -> bytes | None:
        """A font's glyphs for one Unicode range, as MapLibre asks for them."""
        if not (_FONTSTACK.match(fontstack) and _RANGE.match(glyph_range)):
            return None
        path = self.root / "glyphs" / fontstack / f"{glyph_range}.pbf"
        if path.is_file():
            return path.read_bytes()
        if self.offline:
            return None
        url = GLYPHS.replace("{fontstack}", urllib.request.quote(fontstack)) \
                    .replace("{range}", glyph_range)
        data = self._get(url, empty_ok=True)
        if data is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data

    # ------------------------------------------------------------ fetch

    def _get(self, url: str, empty_ok: bool = False) -> bytes | None:
        with self._slots:
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": _UA})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        return resp.read()
                except urllib.error.HTTPError as exc:
                    if exc.code in (204, 404) and empty_ok:
                        return b""
                    return None
                except (urllib.error.URLError, TimeoutError, OSError):
                    if attempt == 2:
                        return None
                    time.sleep(1.0)
        return None
