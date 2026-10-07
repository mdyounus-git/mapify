"""Geotagged reference photographs near a point.

Wikimedia Commons, always: photographs freely licensed by their authors,
with the camera position the photographer recorded ({{Location}}), found
with the GeoData search. Mapillary, only when the user has configured
their own free access token: street-level sequences under CC BY-SA 4.0,
denser than Commons on ordinary streets.

Only a coordinate and a radius are sent; answers and thumbnails are
cached under the data folder, so asking again costs nothing. Requests
carry the project's User-Agent and keep to Wikimedia's robot policy
(wikitech.wikimedia.org/wiki/Robot_policy): Action API calls one at a
time and under five a second (here, one a second), thumbnail downloads
at most two at once and only at a standard thumbnail width.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Reference", "commons_listing", "commons_search_near", "commons_details",
           "commons_near", "touch", "mapillary_near", "fetch_thumb", "UA",
           "LISTING_CAP"]

UA = ("Mapify/0.1 (open-source image geolocation; "
      "https://github.com/mdyounus-git/mapify)")
_COMMONS = "https://commons.wikimedia.org/w/api.php"
_MAPILLARY = "https://graph.mapillary.com/images"
_ALLOWED = ("cc0", "cc by", "cc-by", "public domain", "pd-", "attribution")
_QUERY_TTL_S = 30 * 86400
LISTING_CAP = 500             # the most one geo search returns
_THUMB_WIDTH = 960            # one of Wikimedia's standard thumbnail widths
_LOCK = threading.Lock()
_last_api = [0.0]
_THUMB_SLOTS = threading.BoundedSemaphore(2)


@dataclass
class Reference:
    source: str               # "commons" | "mapillary"
    id: str
    lat: float
    lon: float
    thumb_url: str
    page_url: str
    licence: str
    author: str
    title: str = ""
    heading: float | None = None
    distance_m: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return hashlib.sha1(f"{self.source}:{self.id}".encode()).hexdigest()[:20]

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "id": self.id, "key": self.key,
                "lat": round(self.lat, 6), "lon": round(self.lon, 6),
                "page_url": self.page_url, "licence": self.licence,
                "author": self.author, "title": self.title, "heading": self.heading,
                "distance_m": round(self.distance_m, 1)}


def _get_json(url: str, timeout: float = 30.0) -> dict:
    """One API call at a time, a second apart. Told to slow down (429/503),
    it waits as long as the server asks -- up to a minute -- and tries
    again, twice at most; then the error goes to the caller."""
    with _LOCK:
        for attempt in range(3):
            wait = 1.0 - (time.time() - _last_api[0])
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 503) or attempt == 2:
                    raise
                try:
                    pause = float(exc.headers.get("Retry-After") or 0)
                except ValueError:
                    pause = 0.0
                time.sleep(min(60.0, max(5.0 * (attempt + 1), pause)))
            finally:
                _last_api[0] = time.time()
        raise RuntimeError("unreachable")


def _cached(cache: Path, key: str, fetch) -> Any:
    path = cache / "queries" / f"{hashlib.sha1(key.encode()).hexdigest()[:24]}.json"
    if path.is_file() and time.time() - path.stat().st_mtime < _QUERY_TTL_S:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _strip(html: str) -> str:
    out, depth = [], 0
    for ch in html or "":
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return " ".join("".join(out).split())[:160]


def touch(path: Path) -> None:
    """Mark a cached file as used: the cache is pruned oldest-used first."""
    try:
        os.utime(path)
    except OSError:
        pass


def _metres(lat1, lon1, lat2, lon2) -> float:
    from ..geo.geodesy import haversine_km
    return haversine_km(lat1, lon1, lat2, lon2) * 1000.0


def commons_listing(lat: float, lon: float, radius_m: int, cache: Path) -> list[Reference]:
    """Commons files with a camera position near a point: titles and positions.

    One geo search, nearest first, at most LISTING_CAP files -- in a
    well-photographed city centre that is a few hundred metres, not the
    whole radius, which the caller can tell from the farthest one. The
    references come back unresolved (no thumbnail, licence or author yet):
    ``commons_details`` fills those in for the ones actually compared.
    """
    radius_m = int(max(10, min(10000, radius_m)))

    def fetch() -> list[dict]:
        listed = _get_json(_COMMONS + "?" + urllib.parse.urlencode({
            "action": "query", "format": "json", "list": "geosearch",
            "gscoord": f"{lat:.6f}|{lon:.6f}", "gsradius": str(radius_m),
            "gslimit": str(LISTING_CAP), "gsnamespace": "6"}))
        return [{"title": h["title"], "lat": h["lat"], "lon": h["lon"]}
                for h in (listed.get("query") or {}).get("geosearch") or []]

    raw = _cached(cache, f"commons-list:{lat:.5f}:{lon:.5f}:{radius_m}", fetch)
    out = []
    for h in raw:
        r = Reference(source="commons", id=h["title"], lat=float(h["lat"]),
                      lon=float(h["lon"]), thumb_url="", page_url="", licence="",
                      author="", title=h["title"].removeprefix("File:"))
        r.distance_m = _metres(lat, lon, r.lat, r.lon)
        out.append(r)
    out.sort(key=lambda r: r.distance_m)
    return out


def commons_search_near(text: str, lat: float, lon: float, radius_m: int,
                        cache: Path) -> list[Reference]:
    """Commons files that mention ``text`` and were taken within the radius.

    A full-text search (CirrusSearch) narrowed by ``nearcoord:`` to the
    circle, at most LISTING_CAP files, most relevant first, with each
    file's camera position. Where a geo search drowns in a crowded spot
    -- 760 photographs at the foot of one tower -- this finds the ones of
    the named thing, wherever in the circle they were taken. Unresolved,
    like ``commons_listing``; files with no camera position are dropped.
    """
    radius_m = int(max(10, min(10000, radius_m)))
    words = " ".join(w for w in (text or "").replace('"', " ").split() if w)[:120]
    if not words:
        return []

    def fetch() -> list[dict]:
        data = _get_json(_COMMONS + "?" + urllib.parse.urlencode({
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"{words} nearcoord:{radius_m}m,{lat:.5f},{lon:.5f}",
            "gsrnamespace": "6", "gsrlimit": str(LISTING_CAP),
            "prop": "coordinates", "colimit": "max", "coprimary": "primary"}))
        out = []
        for page in ((data.get("query") or {}).get("pages") or {}).values():
            co = (page.get("coordinates") or [None])[0]
            if co:
                out.append({"title": page["title"], "lat": co["lat"], "lon": co["lon"],
                            "rank": page.get("index", 0)})
        return sorted(out, key=lambda h: h["rank"])

    raw = _cached(cache, f"commons-search:{words}:{lat:.5f}:{lon:.5f}:{radius_m}", fetch)
    out = []
    for h in raw:
        r = Reference(source="commons", id=h["title"], lat=float(h["lat"]),
                      lon=float(h["lon"]), thumb_url="", page_url="", licence="",
                      author="", title=h["title"].removeprefix("File:"))
        r.distance_m = _metres(lat, lon, r.lat, r.lon)
        if r.distance_m <= radius_m:
            out.append(r)
    return out


def _details_db(cache: Path) -> sqlite3.Connection:
    cache.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(cache / "commons-details.sqlite", timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS details "
               "(title TEXT PRIMARY KEY, page TEXT NOT NULL, at REAL NOT NULL)")
    return db


def commons_details(refs: list[Reference], cache: Path) -> list[Reference]:
    """The references that are freely licensed photographs, details filled in.

    Thumbnail, description page, licence and author, asked for 50 titles at
    a time and kept on disk for 30 days, so a file's details are fetched
    once however many searches list it. Files that are not raster images
    or whose licence is not a free one Mapify recognises are dropped.
    """
    todo = [r for r in refs if r.source == "commons" and not r.thumb_url]
    pages: dict[str, dict] = {}
    if todo:
        db = _details_db(cache)
        try:
            fresh = time.time() - _QUERY_TTL_S
            titles = list(dict.fromkeys(r.id for r in todo))
            for start in range(0, len(titles), 500):
                chunk = titles[start:start + 500]
                rows = db.execute(
                    f"SELECT title, page FROM details WHERE at > ? AND title IN "
                    f"({','.join('?' * len(chunk))})", [fresh, *chunk]).fetchall()
                pages.update((t, json.loads(pg)) for t, pg in rows)
            missing = [t for t in titles if t not in pages]
            for start in range(0, len(missing), 50):
                batch = missing[start:start + 50]
                data = _get_json(_COMMONS + "?" + urllib.parse.urlencode({
                    "action": "query", "format": "json", "titles": "|".join(batch),
                    "prop": "imageinfo", "iiprop": "url|extmetadata|mime",
                    "iiurlwidth": str(_THUMB_WIDTH)}))
                got = {pg.get("title", ""): pg
                       for pg in ((data.get("query") or {}).get("pages") or {}).values()}
                now = time.time()
                for t in batch:
                    pg = got.get(t) or {"title": t}        # missing: remembered as such
                    pages[t] = pg
                    db.execute("INSERT OR REPLACE INTO details VALUES (?, ?, ?)",
                               (t, json.dumps(pg), now))
                db.commit()
        finally:
            db.close()
    out = []
    for r in refs:
        if r.source != "commons" or r.thumb_url:
            out.append(r)
            continue
        page = pages.get(r.id) or {}
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        meta = info.get("extmetadata") or {}
        licence = _strip((meta.get("LicenseShortName") or {}).get("value", ""))
        if not any(a in licence.lower() for a in _ALLOWED):
            continue
        thumb = info.get("thumburl") or info.get("url")
        if not thumb:
            continue
        r.thumb_url = thumb
        r.page_url = info.get("descriptionurl", "")
        r.licence = licence
        r.author = _strip((meta.get("Artist") or {}).get("value", "")) or "unknown"
        out.append(r)
    return out


def commons_near(lat: float, lon: float, radius_m: int, cache: Path,
                 limit: int = 300) -> list[Reference]:
    """Freely licensed Commons photographs nearest a point, details filled in."""
    return commons_details(commons_listing(lat, lon, radius_m, cache), cache)[:limit]


def mapillary_near(lat: float, lon: float, radius_m: int, token: str, cache: Path,
                   limit: int = 150) -> list[Reference]:
    """Mapillary street-level images near a point (needs the user's token)."""
    if not token:
        return []
    dlat = radius_m / 111320.0
    from math import cos, radians
    dlon = radius_m / (111320.0 * max(0.2, cos(radians(lat))))
    bbox = f"{lon - dlon:.6f},{lat - dlat:.6f},{lon + dlon:.6f},{lat + dlat:.6f}"

    def fetch() -> list[dict]:
        url = _MAPILLARY + "?" + urllib.parse.urlencode({
            "access_token": token, "bbox": bbox, "limit": str(min(500, limit)),
            "fields": "id,thumb_1024_url,computed_geometry,geometry,compass_angle,"
                      "captured_at,creator"})
        return (_get_json(url) or {}).get("data") or []

    # The token is not part of the cache key and is never written to disk.
    raw = _cached(cache, f"mapillary:{bbox}:{limit}", fetch)
    out = []
    for img in raw:
        geom = img.get("computed_geometry") or img.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords or not img.get("thumb_1024_url"):
            continue
        creator = (img.get("creator") or {}).get("username") or "Mapillary contributor"
        r = Reference(source="mapillary", id=str(img["id"]), lat=float(coords[1]),
                      lon=float(coords[0]), thumb_url=img["thumb_1024_url"],
                      page_url=f"https://www.mapillary.com/app/?pKey={img['id']}",
                      licence="CC BY-SA 4.0", author=creator,
                      heading=img.get("compass_angle"))
        r.distance_m = _metres(lat, lon, r.lat, r.lon)
        if r.distance_m <= radius_m:
            out.append(r)
    out.sort(key=lambda r: r.distance_m)
    return out[:limit]


def fetch_thumb(ref: Reference, cache: Path, timeout: float = 30.0) -> Path | None:
    """The reference's thumbnail on disk, downloading it once.

    Thumbnails are asked for at a standard width (960 px), so one download
    serves both matching passes; at most two download at once.
    """
    path = cache / "thumbs" / f"{ref.key}.jpg"
    if path.is_file() and path.stat().st_size > 0:
        touch(path)
        return path
    url = ref.thumb_url
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with _THUMB_SLOTS, urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
        return None
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path
