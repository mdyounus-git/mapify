"""Local geographic data: heights, the tile store, the readers, the service.

Nothing here touches the network. The OpenStreetMap importer runs on a
hand-written file, the vector-tile decoder on a tile encoded in the test,
and the FlatGeobuf reader on a file GDAL writes (skipped when GDAL's
Python binding is not installed) -- an independent writer is the point:
a reader tested only against its own assumptions proves little.
"""

from __future__ import annotations

import json
import math
import sqlite3
import struct

import pytest

from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


# ---------------------------------------------------------------- heights

class TestHeights:
    def test_lengths_parse_with_and_without_units(self):
        from mapify.geodata.heights import parse_length
        assert parse_length("12") == 12.0
        assert parse_length("12,5") == 12.5
        assert parse_length("12 m") == 12.0
        assert parse_length("40 ft") == pytest.approx(12.192)
        assert parse_length("12'6\"") == pytest.approx(3.81)

    def test_unreadable_lengths_are_refused_not_guessed(self):
        from mapify.geodata.heights import parse_length
        for bad in ("approx 10", "10-12", "tall", "", None, "-3", "5000"):
            assert parse_length(bad) is None, bad

    def test_every_height_says_how_it_is_known(self):
        from mapify.geodata.heights import resolve_height
        assert resolve_height({"building": "yes", "height": "21"}).source == "measured"
        h = resolve_height({"building": "apartments", "building:levels": "4"})
        assert (h.source, h.metres) == ("levels", 12.0)
        h = resolve_height({"building": "house"})
        assert h.source == "assumed"
        assert h.metres <= 6.0, "an assumed height must stay modest"

    def test_a_raised_part_keeps_its_base(self):
        from mapify.geodata.heights import resolve_height
        h = resolve_height({"building:part": "yes", "height": "30",
                            "min_height": "12"})
        assert (h.base_metres, h.metres, h.source) == (12.0, 30.0, "measured")


# ------------------------------------------------------------------ store

class TestStore:
    def test_rings_survive_encoding(self):
        from mapify.geodata.store import decode_ring, encode_ring
        ring = [(80.2210, 12.7810), (80.2215, 12.7810), (80.2215, 12.7815)]
        back = decode_ring(encode_ring(ring))
        for (x0, y0), (x1, y1) in zip(ring, back):
            assert abs(x0 - x1) < 1e-6 and abs(y0 - y1) < 1e-6

    def test_small_features_file_at_z14_and_large_ones_coarser(self):
        from mapify.geodata.store import _placement
        z, _ = _placement((80.2210, 12.7810, 80.2215, 12.7815))
        assert z == 14
        z, _ = _placement((79.0, 12.0, 80.5, 13.5))          # a long river
        assert z in (10, 6)

    def test_thousands_of_tiles_write_and_read_back(self, tmp_path):
        """More than one batch of tiles, so the mid-loop write runs."""
        from mapify.geodata.store import GeoStore, bbox_around, encode_ring
        st = GeoStore(tmp_path / "s.sqlite")
        st.begin_stage()
        n = 0
        for i in range(60):
            for j in range(60):
                lon, lat = 80.0 + i * 0.03, 12.0 + j * 0.03
                ring = [(lon, lat), (lon + 1e-4, lat), (lon + 1e-4, lat + 1e-4),
                        (lon, lat)]
                st.stage("building", "osm", (lon, lat, lon + 1e-4, lat + 1e-4),
                         [n, None, "yes", 40, 0, 0, None, 0, [encode_ring(ring)]])
                n += 1
        written = st.finish_stage()
        assert written > 2000, "must exceed one write batch"
        got = st.query(bbox_around(12.0 + 30 * 0.03, 80.0 + 30 * 0.03, 200))
        assert len(got["building"]) == 1
        tables = {r[0] for r in st.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert not any(t.startswith("stage") for t in tables), \
            "the staging table must be dropped"

    def test_a_feature_across_a_tile_edge_is_returned_once(self, tmp_path):
        from mapify.geodata.store import (GeoStore, _tile_bounds, bbox_around,
                                          encode_ring, tile_of)
        st = GeoStore(tmp_path / "s.sqlite")
        x, y = tile_of(80.22, 12.78, 14)
        west, south, east, north = _tile_bounds(14, x, y)
        ring = [(east - 1e-4, south + 1e-3), (east + 1e-4, south + 1e-3),
                (east + 1e-4, south + 2e-3), (east - 1e-4, south + 2e-3)]
        st.begin_stage()
        st.stage("building", "osm", (east - 1e-4, south + 1e-3,
                                     east + 1e-4, south + 2e-3),
                 [7, None, "yes", 40, 0, 0, None, 0, [encode_ring(ring)]])
        st.finish_stage()
        got = st.query(bbox_around(south + 1.5e-3, east, 500))
        assert [f[0] for _, f in got["building"]] == [7]

    def test_a_reimport_replaces_rather_than_duplicates(self, tmp_path):
        from mapify.geodata.store import GeoStore, bbox_around, encode_ring
        st = GeoStore(tmp_path / "s.sqlite")
        ring = [(80.221, 12.781), (80.2211, 12.781), (80.2211, 12.7811)]
        for height in (40, 90):
            st.begin_stage()
            st.stage("building", "osm", (80.221, 12.781, 80.2211, 12.7811),
                     [1, None, "yes", height, 0, 2, None, 0, [encode_ring(ring)]])
            st.finish_stage()
        got = st.query(bbox_around(12.781, 80.221, 200))["building"]
        assert len(got) == 1 and got[0][1][3] == 90, "the newer version wins"


# ------------------------------------------------------------ OSM import

class TestOsmImport:
    def test_the_tiny_file_imports_with_heights_holes_roads_and_places(self, tmp_path):
        pytest.importorskip("osmium")
        from mapify.geodata.osm_import import import_pbf
        from mapify.geodata.store import GeoStore, bbox_around
        st = GeoStore(tmp_path / "s.sqlite")
        counts = import_pbf(DATA / "tiny.osm", st, "tiny")
        assert counts["building"] == 2 and counts["road"] == 1
        assert counts["poi"] == 1
        assert counts["height_measured"] == 1 and counts["height_levels"] == 1
        got = st.query(bbox_around(12.784, 80.224, 1500))
        by_name = {f[1]: f for _, f in got["building"]}
        assert by_name["Test Towers"][3] == 120          # 4 levels, in dm
        assert len(by_name["Courtyard College"][8]) == 2, "the hole is kept"
        region = st.regions()[0]
        assert region["licence"].startswith("ODbL")
        assert region["bbox"] == [80.22, 12.78, 80.23, 12.79]


# ------------------------------------------------------------ vector tiles

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(num: int, wire: int, payload) -> bytes:
    key = _varint((num << 3) | wire)
    if wire == 0:
        return key + _varint(payload)
    return key + _varint(len(payload)) + payload


def _zz(n: int) -> int:
    return (n << 1) ^ (n >> 31)


def _geom(rings: list[list[tuple[int, int]]], close: bool) -> list[int]:
    out, cx, cy = [], 0, 0
    for ring in rings:
        x, y = ring[0]
        out += [(1 & 7) | (1 << 3), _zz(x - cx), _zz(y - cy)]
        cx, cy = x, y
        rest = ring[1:]
        out.append((2 & 7) | (len(rest) << 3))
        for x, y in rest:
            out += [_zz(x - cx), _zz(y - cy)]
            cx, cy = x, y
        if close:
            out.append((7 & 7) | (1 << 3))
    return out


def _tile(english: str | None = None, second_part: bool = False) -> bytes:
    """A vector tile with a clipped building, a hole, a road and a label
    (with an English name too, when ``english`` is given, and the building
    feature holding a second, separate footprint when ``second_part``)."""
    def packed(vals):
        return b"".join(_varint(v) for v in vals)

    def feature(gtype, tags, geom):
        return (_field(2, 2, packed(tags)) + _field(3, 0, gtype)
                + _field(4, 2, packed(geom)))

    def value_str(s):
        return _field(1, 2, s.encode())

    def value_int(i):
        return _field(4, 0, i)

    # A square straddling the tile's east edge (x runs past 4096), with a
    # hole wound the other way.
    outer = [(3900, 100), (4300, 100), (4300, 500), (3900, 500)]
    hole = [(3950, 150), (3950, 250), (4050, 250), (4050, 150)]
    rings = [outer, hole]
    if second_part:                      # wound like ``outer``: another exterior
        rings.append([(1000, 1000), (1400, 1000), (1400, 1400), (1000, 1400)])
    building = (_field(1, 2, b"building")
                + _field(2, 2, feature(3, [0, 0], _geom(rings, True)))
                + _field(3, 2, b"render_height") + _field(4, 2, value_int(24))
                + _field(5, 0, 4096) + _field(15, 0, 2))
    road = (_field(1, 2, b"transportation")
            + _field(2, 2, feature(2, [0, 0], _geom([[(0, 2000), (4096, 2100)]], False)))
            + _field(2, 2, feature(3, [0, 0], _geom([outer], True)))   # a plaza
            + _field(3, 2, b"class") + _field(4, 2, value_str("primary"))
            + _field(5, 0, 4096) + _field(15, 0, 2))
    tags = [0, 0, 1, 1] + ([2, 2] if english else [])
    poi = (_field(1, 2, b"poi")
           + _field(2, 2, feature(1, tags, _geom([[(2048, 2048)]], False)[:3]))
           + _field(3, 2, b"name") + _field(3, 2, b"class")
           + (_field(3, 2, b"name:en") if english else b"")
           + _field(4, 2, value_str("Test Market")) + _field(4, 2, value_str("shop"))
           + (_field(4, 2, value_str(english)) if english else b"")
           + _field(5, 0, 4096) + _field(15, 0, 2))
    return (_field(3, 2, building) + _field(3, 2, road) + _field(3, 2, poi))


class TestVectorTiles:
    Z, X, Y = 14, 11842, 7577

    def test_a_tile_decodes_into_longitude_and_latitude(self):
        from mapify.geodata.mvt import decode_tile, tile_bounds
        layers = decode_tile(_tile(), self.Z, self.X, self.Y)
        b = tile_bounds(self.Z, self.X, self.Y)
        (bld,) = layers["building"]
        assert bld["props"]["render_height"] == 24
        poly = bld["geometry"][0]
        assert len(poly) == 2, "the hole stays attached to its building"
        for ring in poly:
            for lon, lat in ring:
                assert b[0] - 1e-9 <= lon <= b[2] + 1e-9, "clipped to the tile"
                assert b[1] - 1e-9 <= lat <= b[3] + 1e-9
        (pt,) = layers["poi"]
        assert pt["props"] == {"name": "Test Market", "class": "shop"}

    def test_gzipped_tiles_decode_too(self):
        import gzip
        from mapify.geodata.mvt import decode_tile
        assert decode_tile(gzip.compress(_tile()), self.Z, self.X, self.Y)["building"]

    def test_filing_skips_geometry_a_layer_does_not_expect(self, tmp_path):
        """transportation carries plazas as polygons; only lines are streets."""
        from mapify.geodata.store import GeoStore, _tile_bounds
        from mapify.geodata.vector_tiles import OpenFreeMap
        st = GeoStore(tmp_path / "s.sqlite")
        ofm = OpenFreeMap(st, tmp_path)
        st.begin_stage()
        n = ofm._file(_tile(), self.X, self.Y)
        st.finish_stage()
        got = st.query(_tile_bounds(self.Z, self.X, self.Y))
        assert len(got["road"]) == 1
        assert len(got["building"]) == 1
        assert got["building"][0][1][5] == 3, "24 m is a published height"
        assert got["poi"][0][1][2] == "Test Market"
        assert n == 3

    def _filed_poi(self, tmp_path, english):
        from mapify.geodata.store import GeoStore, _tile_bounds
        from mapify.geodata.vector_tiles import OpenFreeMap
        st = GeoStore(tmp_path / "s.sqlite")
        st.begin_stage()
        OpenFreeMap(st, tmp_path)._file(_tile(english), self.X, self.Y)
        st.finish_stage()
        return st.query(_tile_bounds(self.Z, self.X, self.Y), ("poi",))["poi"][0][1]

    def test_a_points_english_name_is_kept_beside_its_own(self, tmp_path):
        assert self._filed_poi(tmp_path / "a", "Market Hall")[5] == "Market Hall"
        assert len(self._filed_poi(tmp_path / "b", None)) == 5
        assert len(self._filed_poi(tmp_path / "c", "Test Market")) == 5   # same: not kept

    def test_every_part_of_a_multi_part_building_is_kept(self, tmp_path):
        """Merged buildings come as one feature of many polygons; they all
        stay (they shared one id, and all but the first were dropped)."""
        from mapify.geodata.store import GeoStore, _tile_bounds
        from mapify.geodata.vector_tiles import OpenFreeMap
        st = GeoStore(tmp_path / "s.sqlite")
        st.begin_stage()
        OpenFreeMap(st, tmp_path)._file(_tile(second_part=True), self.X, self.Y)
        st.finish_stage()
        got = st.query(_tile_bounds(self.Z, self.X, self.Y), ("building",))["building"]
        assert len(got) == 2
        assert len({f[0] for _, f in got}) == 2

    def test_an_older_filing_is_discarded_once(self, tmp_path):
        from mapify.geodata.store import GeoStore
        from mapify.geodata.vector_tiles import FILING_VERSION, OpenFreeMap
        st = GeoStore(tmp_path / "s.sqlite")
        st.begin_stage()
        st.stage("poi", "ofm", (1, 1, 1, 1), [1, "shop=shop", "Old", 1000000, 1000000])
        st.stage("poi", "osm", (1, 1, 1, 1), [2, "shop=shop", "Imported", 1000000, 1000000])
        st.finish_stage()
        st.mark_fetched("ofm", 14, 1, 1)
        ofm = OpenFreeMap(st, tmp_path)
        ofm._check_filing_version()
        got = st.query((0.9, 0.9, 1.1, 1.1), ("poi",))["poi"]
        assert [s for s, _ in got] == ["osm"], "an imported region is never discarded"
        assert not st.was_fetched("ofm", 14, 1, 1)
        assert st.meta_get("ofm_filing_version") == str(FILING_VERSION)
        st.mark_fetched("ofm", 14, 1, 1)
        OpenFreeMap(st, tmp_path)._check_filing_version()   # current: nothing happens
        assert st.was_fetched("ofm", 14, 1, 1)

    def test_tiles_fetched_before_a_change_are_fetched_again(self, tmp_path):
        from mapify.geodata.store import GeoStore
        st = GeoStore(tmp_path / "s.sqlite")
        st.mark_fetched("ofm", 14, 1, 2)
        assert st.was_fetched("ofm", 14, 1, 2)
        assert st.was_fetched("ofm", 14, 1, 2, since="2000-01-01T00:00:00Z")
        assert not st.was_fetched("ofm", 14, 1, 2, since="2999-01-01T00:00:00Z")
        assert not st.was_fetched("ofm", 14, 9, 9)

    def test_a_tile_kept_past_a_month_is_fetched_again(self, tmp_path):
        import time as _time

        from mapify.geodata.store import GeoStore
        from mapify.geodata.vector_tiles import _fresh_since
        st = GeoStore(tmp_path / "s.sqlite")
        st.mark_fetched("ofm", 14, 1, 1)                       # today
        st.mark_fetched("ofm", 14, 2, 2)
        old = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_time.time() - 40 * 86400))
        st.db.execute("UPDATE fetched SET at=? WHERE x=2", (old,))
        st.db.commit()
        assert st.was_fetched("ofm", 14, 1, 1, since=_fresh_since())
        assert not st.was_fetched("ofm", 14, 2, 2, since=_fresh_since())

    def test_nearby_landmarks_carry_their_english_name(self, tmp_path):
        from types import SimpleNamespace

        from mapify.geodata.nearby import nearby
        from mapify.geodata.store import GeoStore
        st = GeoStore(tmp_path / "s.sqlite")
        st.begin_stage()
        st.stage("poi", "ofm", (37.6231, 55.7525, 37.6231, 55.7525),
                 [1, "place_of_worship=christian", "Собор Василия Блаженного",
                  37623100, 55752500, "Saint Basil's Cathedral"])
        st.finish_stage()
        svc = SimpleNamespace(store=st, online=False)
        (f,) = nearby(svc, 55.7525, 37.6231, 500, fetch=False).features
        assert f.name == "Собор Василия Блаженного"
        assert f.tags["name:en"] == "Saint Basil's Cathedral"


# ------------------------------------------------------------- FlatGeobuf

class TestFlatGeobuf:
    def test_a_bbox_search_matches_brute_force_on_a_gdal_written_file(self, tmp_path):
        pyogrio = pytest.importorskip("pyogrio")
        np = pytest.importorskip("numpy")
        from shapely import wkb as swkb
        from shapely.geometry import box
        from mapify.geodata.fgb import open_file

        polys, heights = [], []
        rng = np.random.default_rng(3)
        for _ in range(4000):
            x, y = rng.uniform(80.0, 80.4), rng.uniform(12.6, 13.0)
            polys.append(box(x, y, x + 2e-4, y + 2e-4).wkb)
            heights.append(float(rng.uniform(0.65, 1.0)))
        path = tmp_path / "b.fgb"
        pyogrio.raw.write(str(path), np.array(polys, dtype=object),
                          field_data=[np.array(heights)],
                          fields=["confidence"], driver="FlatGeobuf",
                          geometry_type="Polygon", crs="EPSG:4326",
                          layer_options={"SPATIAL_INDEX": "YES"})
        src = open_file(str(path))
        assert src.header.features_count == 4000
        q = (80.1, 12.7, 80.2, 12.8)
        hits = src.search(q)
        found = {round(p["confidence"], 9) for _, p, _ in src.features(hits)}
        expected = {round(h, 9) for w, h in zip(polys, heights)
                    if swkb.loads(w).intersects(box(*q))}
        assert found == expected


# ------------------------------------------------------------ imagery

class TestTiles:
    def test_imagery_sources_depend_on_where_you_are(self):
        from mapify.geodata.tiles import sources_for
        us = [s["key"] for s in sources_for(39.74, -104.99)]
        india = [s["key"] for s in sources_for(12.78, 80.22)]
        assert us[0] == "usgs"
        assert "usgs" not in india, "the USGS service has nothing outside the US"
        assert all(not s["shows_buildings"] for s in sources_for(12.78, 80.22))

    def test_cached_tiles_are_served_offline_and_missing_ones_remembered(self, tmp_path):
        from mapify.geodata.tiles import TileCache
        cache = TileCache(root=tmp_path, offline=True)
        p = tmp_path / "weld" / "5" / "22" / "14.jpg"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"\xff\xd8jpeg")
        data, media = cache.get("weld", 5, 22, 14)
        assert data == b"\xff\xd8jpeg" and media == "image/jpeg"
        (tmp_path / "weld" / "5" / "22" / "15.missing").write_text("404")
        assert cache.get("weld", 5, 22, 15)[0] is None
        assert cache.get("weld", 5, 22, 16) == (None, "offline, and not cached")

    def test_bad_requests_are_refused_before_any_fetch(self, tmp_path):
        from mapify.geodata.tiles import TileCache
        cache = TileCache(root=tmp_path, offline=False)
        assert cache.get("nope", 1, 0, 0)[1] == "unknown source"
        # Past the deepest zoom by more than OVERZOOM levels (16 + 4).
        assert cache.get("usgs", 21, 0, 0)[1] == "outside the source's range"
        assert cache.get("hls", 3, 0, 0, "../../etc")[1] == "bad date"

    def test_national_aerial_photographs_are_offered_only_at_home(self):
        from mapify.geo.offline import get_geocoder
        from mapify.geodata.tiles import sources_for
        if not get_geocoder().available():
            pytest.skip("Natural Earth not installed")
        first = lambda lat, lon: sources_for(lat, lon)[0]["key"]   # noqa: E731
        assert first(48.8584, 2.2945) == "fr"           # Paris
        assert first(52.3731, 4.8926) == "nl"           # Amsterdam
        assert first(46.9480, 7.4474) == "ch"           # Bern
        assert first(40.4168, -3.7038) == "es"          # Madrid
        assert first(35.6586, 139.7454) == "jp"         # Tokyo
        assert first(-21.1151, 55.5364) == "fr"         # Reunion, overseas France
        lisbon = [s["key"] for s in sources_for(38.7223, -9.1393)]
        assert not {"fr", "nl", "ch", "es", "jp"} & set(lisbon)
        assert first(39.74, -104.99) == "usgs"

    def test_a_flat_fill_is_not_a_photograph(self):
        """What IGN Espana sends over Portugal, PDOK over Belgium."""
        import io

        import numpy as np
        from PIL import Image

        from mapify.geodata.tiles import _is_fill

        def jpeg(im):
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=75)
            return buf.getvalue()

        assert _is_fill(jpeg(Image.new("RGB", (256, 256), (162, 134, 105))))
        assert _is_fill(jpeg(Image.new("RGB", (256, 256), (255, 255, 255))))
        rng = np.random.default_rng(0)
        streets = Image.fromarray(rng.integers(0, 255, (256, 256, 3), dtype=np.uint8))
        assert not _is_fill(jpeg(streets))
        assert not _is_fill(b"not an image")

    def test_a_fill_is_remembered_as_missing(self, tmp_path, monkeypatch):
        import io
        import urllib.request

        from PIL import Image

        from mapify.geodata.tiles import TileCache

        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (32, 26, 38)).save(buf, "JPEG")

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        calls = []

        def urlopen(req, timeout=30):
            calls.append(req.full_url)
            return Reply(buf.getvalue())

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        cache = TileCache(root=tmp_path, offline=False)
        assert cache.get("es", 15, 15000, 12000) == (None, "the source has no tile here")
        assert cache.get("es", 15, 15000, 12000)[0] is None
        assert len(calls) == 1, "remembered, not asked for again"


# ------------------------------------------------------------- service

class TestService:
    @pytest.fixture
    def svc(self, tmp_path, monkeypatch):
        pytest.importorskip("osmium")
        monkeypatch.setenv("MAPIFY_GEODATA_DIR", str(tmp_path))
        from mapify.geodata.osm_import import import_pbf
        from mapify.geodata.service import GeoService
        s = GeoService(online=False)
        import_pbf(DATA / "tiny.osm", s.store, "tiny")
        return s

    def test_around_returns_buildings_with_how_their_height_is_known(self, svc):
        a = svc.around(12.784, 80.224, 1500, fetch_open_buildings=False)
        by_name = {b["name"]: b for b in a["buildings"]}
        assert by_name["Test Towers"]["height_source"] == "levels"
        assert by_name["Courtyard College"]["height_source"] == "measured"
        assert len(by_name["Courtyard College"]["polygon"]) == 2
        assert a["roads"][0]["name"] == "Test Street"
        assert a["sources"][0]["attribution"].endswith("OpenStreetMap contributors")

    def test_a_detection_inside_a_mapped_building_is_dropped(self, svc):
        from mapify.geodata.store import encode_ring
        inside = [(80.2211, 12.7811), (80.2213, 12.7811), (80.2213, 12.7813)]
        outside = [(80.2280, 12.7880), (80.2282, 12.7880), (80.2282, 12.7882)]
        svc.store.begin_stage()
        for fid, ring in ((1, inside), (2, outside)):
            svc.store.stage("building", "gob",
                            (min(p[0] for p in ring), min(p[1] for p in ring),
                             max(p[0] for p in ring), max(p[1] for p in ring)),
                            [fid, None, "building", 40, 0, 0, None, 0,
                             [encode_ring(ring)], 0.9, 30.0])
        svc.store.finish_stage()
        a = svc.around(12.784, 80.224, 1500, fetch_open_buildings=False)
        gob = [b["id"] for b in a["buildings"] if b["source"] == "gob"]
        assert gob == ["gob:2"], "the mapped building wins where both exist"

    def test_offline_with_nothing_stored_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAPIFY_GEODATA_DIR", str(tmp_path / "empty"))
        from mapify.geodata.service import GeoService
        a = GeoService(online=False).around(40.758, -73.985, 500)
        assert a["buildings"] == []
        assert any("Offline" in n for n in a["notes"])


# ------------------------------------------------------------- exports

class TestExports:
    REPORT = {"prediction": {"determinate": True, "confidence": "medium",
                             "uncertainty_km": 12.5, "precision_km": 0.02,
                             "estimated": {"latitude": 12.781,
                                           "longitude": 80.2212,
                                           "provenance": "text"}},
              "candidates": [{"latitude": 12.781, "longitude": 80.2212,
                              "probability": 0.41, "provenance": "text"}]}
    AROUND = {"center": [80.2212, 12.781],
              "buildings": [{"polygon": [[[80.225, 12.785], [80.226, 12.785],
                                          [80.226, 12.786], [80.225, 12.786]]],
                             "height": 4.0, "base": 0.0,
                             "height_source": "assumed", "source": "gob",
                             "confidence": 0.81}],
              "roads": [{"path": [[80.222, 12.782], [80.223, 12.783]],
                         "name": "Test Street", "class": "residential"}],
              "water": [], "pois": [],
              "sources": [{"name": "Google Open Buildings v3",
                           "licence": "CC BY 4.0",
                           "attribution": "Google Open Buildings v3 (CC BY 4.0)"}]}

    def test_the_geopackage_is_a_valid_one(self, tmp_path):
        from mapify.exporters import build_gpkg
        path = build_gpkg(self.REPORT, self.AROUND, tmp_path / "c.gpkg")
        db = sqlite3.connect(path)
        assert db.execute("PRAGMA application_id").fetchone()[0] == 0x47504B47
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        blob = db.execute("SELECT geom FROM buildings").fetchone()[0]
        assert blob[:2] == b"GP" and struct.unpack_from("<i", blob, 4)[0] == 4326
        notices = [r[0] for r in db.execute("SELECT source FROM notice")]
        assert "Google Open Buildings v3" in notices, "licences travel with data"

    def test_gdal_reads_every_layer(self, tmp_path):
        pyogrio = pytest.importorskip("pyogrio")
        from mapify.exporters import build_gpkg
        path = build_gpkg(self.REPORT, self.AROUND, tmp_path / "c.gpkg")
        layers = dict(pyogrio.list_layers(str(path)))
        assert layers["buildings"] == "Polygon"
        assert layers["candidates"] == "Point"
        assert pyogrio.read_info(str(path), layer="buildings")["crs"] == "EPSG:4326"

    def test_the_kepler_file_has_datasets_layers_and_a_notice(self):
        from mapify.exporters import build_kepler
        k = build_kepler(self.REPORT, self.AROUND)
        ids = [d["data"]["id"] for d in k["datasets"]]
        assert ids == ["candidates", "buildings"]
        layer = k["config"]["config"]["visState"]["layers"][1]
        assert layer["config"]["visConfig"]["enable3d"] is True
        assert "CC BY 4.0" in k["info"]["description"]
        json.dumps(k)                                    # serialisable


# --------------------------------------------------------------- routes

class TestRoutes:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        pytest.importorskip("osmium")
        monkeypatch.setenv("MAPIFY_GEODATA_DIR", str(tmp_path / "geo"))
        monkeypatch.setenv("MAPIFY_STATE_DIR", str(tmp_path / "state"))
        import mapify.geodata.service as service
        monkeypatch.setattr(service, "_SHARED", {})
        from fastapi.testclient import TestClient
        from mapify.config import load_config
        from mapify.geodata.osm_import import import_pbf
        from mapify.server import create_app
        cfg = load_config()
        cfg.network.offline_only = True
        cfg.models.geoclip_enabled = False
        cfg.models.clip_evidence_enabled = False
        cfg.models.ocr_enabled = False
        import_pbf(DATA / "tiny.osm", service.get_service(online=False).store,
                   "tiny")
        return TestClient(create_app(cfg))

    def test_around_serves_the_local_store(self, client):
        r = client.get("/api/geodata/around?lat=12.784&lon=80.224&radius=1500")
        assert r.status_code == 200
        assert {b["name"] for b in r.json()["buildings"]} >= {"Test Towers"}

    def test_a_coordinate_out_of_range_is_refused(self, client):
        assert client.get("/api/geodata/around?lat=95&lon=0").status_code == 400

    def test_a_missing_tile_is_empty_ground_not_an_error(self, client):
        r = client.get("/api/tiles/weld/3/1/1")
        assert r.status_code == 204
        assert r.headers["X-Mapify-Reason"]

    def test_status_names_the_imagery_for_a_place(self, client):
        j = client.get("/api/geodata/status?lat=12.78&lon=80.22").json()
        assert j["regions"][0]["name"] == "tiny"
        assert "usgs" not in [s["key"] for s in j["imagery"]]


# ---------------------------------------------------------- nearby places

class TestNearby:
    def test_landmarks_and_streets_come_from_local_data(self, tmp_path, monkeypatch):
        pytest.importorskip("osmium")
        monkeypatch.setenv("MAPIFY_GEODATA_DIR", str(tmp_path))
        from mapify.geodata.nearby import nearby
        from mapify.geodata.osm_import import import_pbf
        from mapify.geodata.service import GeoService
        svc = GeoService(online=False)
        import_pbf(DATA / "tiny.osm", svc.store, "tiny")
        s = nearby(svc, 12.784, 80.224, 1500, fetch=False)
        assert [f.name for f in s.features] == ["Test Hospital"]
        assert s.features[0].distance_m > 0
        assert s.road_names == ["Test Street"]
        assert "OpenStreetMap" in s.attribution

    def test_no_local_data_is_said_rather_than_invented(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAPIFY_GEODATA_DIR", str(tmp_path))
        from mapify.geodata.nearby import nearby
        from mapify.geodata.service import GeoService
        s = nearby(GeoService(online=False), 40.758, -73.985, 1000, fetch=False)
        assert s.features == [] and s.error

    def test_public_overpass_is_off_by_default(self):
        from mapify.config import load_config
        assert load_config().network.overpass_enabled is False


# ---------------------------------------------------------------- terrain

def test_terrain_is_served_but_never_offered_as_imagery(tmp_path):
    from mapify.geodata.tiles import SOURCES, TileCache, sources_for
    assert "terrain" in SOURCES and "SRTM" in SOURCES["terrain"].attribution
    assert "terrain" not in [s["key"] for s in sources_for(37.75, -122.45)]
    cache = TileCache(tmp_path, offline=True)
    assert cache.get("terrain", 19, 0, 0) == (None, "outside the source's range")
    assert cache.get("terrain", 14, 2620, 6334) == (None, "offline, and not cached")


def test_imagery_past_its_deepest_zoom_is_cut_from_the_deepest_tile(tmp_path):
    """Landsat stops at zoom 12; zoom 13 is a quarter of a zoom-12 tile,
    enlarged -- no new detail, but small terrain tiles everywhere."""
    import io

    from PIL import Image

    from mapify.geodata.tiles import SOURCES, TileCache
    weld = SOURCES["weld"]
    cache = TileCache(tmp_path, offline=True)
    im = Image.new("RGB", (256, 256), (0, 0, 255))
    im.paste((255, 0, 0), (128, 0, 256, 128))            # north-east quarter red
    parent = cache._path(weld, 12, 100, 200, None)
    parent.parent.mkdir(parents=True, exist_ok=True)
    im.save(parent, "JPEG", quality=95)
    data, media = cache.get("weld", 13, 201, 400)          # the north-east child
    assert media == "image/jpeg"
    child = Image.open(io.BytesIO(data)).convert("RGB")
    assert child.size == (256, 256)
    r, g, b = child.getpixel((128, 128))
    assert r > 200 and b < 60, "the red quarter, enlarged"
    assert cache.get("weld", 17, 0, 0)[0] is None, "no more than four levels past"
    assert cache.get("terrain", 19, 0, 0) == (None, "outside the source's range")



def test_a_dropped_connection_is_tried_again(tmp_path, monkeypatch):
    """IGN Espana drops about one request in three; each succeeds when asked again."""
    import http.client
    import io
    import urllib.request

    from mapify.geodata import tiles
    from mapify.geodata.tiles import TileCache

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    calls = []

    def urlopen(req, timeout=30):
        calls.append(1)
        if len(calls) == 1:
            raise http.client.RemoteDisconnected("Remote end closed connection")
        return Reply(b"\xff\xd8 a real tile, " + b"x" * 4000)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(tiles.time, "sleep", lambda s: None)
    data, media = TileCache(root=tmp_path, offline=False).get("weld", 5, 22, 14)
    assert data and media == "image/jpeg" and len(calls) == 2


def test_a_timeout_is_not_waited_on_twice(tmp_path, monkeypatch):
    import urllib.request

    from mapify.geodata import tiles
    from mapify.geodata.tiles import TileCache
    calls = []

    def urlopen(req, timeout=30):
        calls.append(1)
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(tiles.time, "sleep", lambda s: None)
    data, why = TileCache(root=tmp_path, offline=False).get("weld", 5, 22, 14)
    assert data is None and why.startswith("unreachable") and len(calls) == 1


def _terrarium(heights):
    import numpy as np
    v = np.asarray(heights, dtype=np.float64) + 32768
    whole = np.floor(v)
    return np.stack([whole // 256, whole % 256, np.floor((v - whole) * 256)],
                    axis=-1).astype(np.uint8)


def _height_at(png: bytes, px: int, py: int) -> float:
    import io

    from PIL import Image
    r, g, b = Image.open(io.BytesIO(png)).convert("RGB").getpixel((px, py))
    return r * 256 + g + b / 256 - 32768


def test_terrain_is_cut_from_mapterhorn_with_its_heights_intact(tmp_path):
    """One 512 px Mapterhorn tile holds four 256 px terrain tiles exactly;
    two levels down it is enlarged as heights, never as packed colours."""
    import numpy as np
    from PIL import Image

    from mapify.geodata.tiles import TileCache
    h = np.zeros((512, 512))
    h[:256, :256], h[:256, 256:] = 100.0, 200.0
    h[256:, :256], h[256:, 256:] = -12.5, 3776.25
    parent = tmp_path / "mapterhorn" / "11" / "1000" / "700.webp"
    parent.parent.mkdir(parents=True)
    Image.fromarray(_terrarium(h), "RGB").save(parent, "WEBP", lossless=True)
    cache = TileCache(root=tmp_path, offline=True)

    ne, media = cache.get("terrain", 12, 2001, 1400)          # the top-right quarter
    assert media == "image/png"
    assert _height_at(ne, 10, 10) == 200.0 and _height_at(ne, 250, 250) == 200.0
    se, _ = cache.get("terrain", 12, 2001, 1401)
    assert _height_at(se, 128, 128) == 3776.25
    sw, _ = cache.get("terrain", 12, 2000, 1401)
    assert _height_at(sw, 128, 128) == -12.5
    deeper, _ = cache.get("terrain", 13, 4003, 2802)          # inside the SE quarter
    assert abs(_height_at(deeper, 128, 128) - 3776.25) < 0.01
    assert (tmp_path / "terrain-mapterhorn" / "12" / "2001" / "1400.png").is_file()


def test_where_mapterhorn_has_nothing_the_aws_terrain_answers(tmp_path):
    """The open ocean: no Mapterhorn tile at any level asked about."""
    import io

    import numpy as np
    from PIL import Image

    from mapify.geodata.tiles import MAPTERHORN_LEVELS_UP, TileCache
    z, x, y = 12, 1979, 1473
    for dz in range(1, MAPTERHORN_LEVELS_UP + 1):
        m = tmp_path / "mapterhorn" / str(z - dz) / str(x >> dz) / f"{y >> dz}.missing"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text("404")
    aws = tmp_path / "terrain" / str(z) / str(x) / f"{y}.png"
    aws.parent.mkdir(parents=True)
    buf = io.BytesIO()
    Image.fromarray(_terrarium(np.full((256, 256), -4500.0)), "RGB").save(buf, "PNG")
    aws.write_bytes(buf.getvalue())
    data, media = TileCache(root=tmp_path, offline=True).get("terrain", z, x, y)
    assert media == "image/png" and _height_at(data, 5, 5) == -4500.0


def test_past_the_aws_tiles_deepest_zoom_the_ground_does_not_vanish(tmp_path):
    """Walking needs zoom 16; offline, with no Mapterhorn kept, the AWS
    z15 tile is cut and enlarged, as heights."""
    import io

    import numpy as np
    from PIL import Image

    from mapify.geodata.tiles import TileCache
    h = np.zeros((256, 256))
    h[:128, 128:] = 41.5                                    # the NE quarter
    aws = tmp_path / "terrain" / "15" / "100" / "200.png"
    aws.parent.mkdir(parents=True)
    buf = io.BytesIO()
    Image.fromarray(_terrarium(h), "RGB").save(buf, "PNG")
    aws.write_bytes(buf.getvalue())
    cache = TileCache(root=tmp_path, offline=True)
    data, media = cache.get("terrain", 16, 201, 400)         # NE child of (100, 200)
    assert media == "image/png" and _height_at(data, 128, 128) == 41.5
    assert cache.get("terrain", 19, 0, 0)[1] == "outside the source's range"
