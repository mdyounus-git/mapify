"""Turn an OpenStreetMap extract into the local feature store.

Reads a ``.osm.pbf`` -- a Geofabrik regional cut of the planet file --
with pyosmium (BSD-2-Clause, over libosmium, Boost licence), assembles
building and water polygons including multipolygon relations, and files
them into :class:`~mapify.geodata.store.GeoStore` tiles.

What is kept, and why only that
-------------------------------

Only what the 3D ground view and the landmark check use: buildings with
whatever height OSM records, streets by class, water, green space, and
named places. OSM holds far more; storing it would cost disk for
nothing the tool reads.

Licence
-------

The store built here is a derivative database of OpenStreetMap under
ODbL 1.0. It is built on the user's own machine and not redistributed by
this project. Anything exported from it -- a GeoPackage, a kepler.gl
file -- carries the ODbL notice with it, because that obligation travels
with the data rather than staying with the tool.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Callable

from .heights import resolve_height
from .store import GeoStore, encode_ring, ring_bbox

log = logging.getLogger("mapify.geodata")

__all__ = ["import_pbf", "OSM_LICENCE", "OSM_ATTRIBUTION"]

OSM_LICENCE = "ODbL 1.0 (Open Data Commons Open Database License)"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"

HEIGHT_SOURCE = {"assumed": 0, "levels": 1, "measured": 2}

ROAD_CLASSES = (
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "service", "living_street",
    "pedestrian", "track", "footway", "cycleway", "path", "steps",
    "motorway_link", "trunk_link", "primary_link", "secondary_link",
    "tertiary_link", "road",
)
_ROAD_SET = frozenset(ROAD_CLASSES)

_GREEN_LEISURE = frozenset({"park", "garden", "pitch", "golf_course",
                            "playground", "nature_reserve", "stadium"})
_GREEN_LANDUSE = frozenset({"grass", "forest", "meadow", "recreation_ground",
                            "village_green", "orchard", "cemetery",
                            "plant_nursery"})
_GREEN_NATURAL = frozenset({"wood", "scrub", "grassland", "wetland", "heath"})
_WATER_LANDUSE = frozenset({"reservoir", "basin"})
_WATERWAY_LINES = frozenset({"river", "stream", "canal", "drain", "ditch"})
_POI_KEYS = ("amenity", "shop", "tourism", "historic", "office", "leisure",
             "craft", "healthcare", "man_made", "place")


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _poi_kind(tags) -> str | None:
    for key in _POI_KEYS:
        value = tags.get(key)
        if value and value != "no":
            return f"{key}={value}"
    return None


def _rings_of_area(area) -> list[list[list[tuple[float, float]]]]:
    """Polygons as [outer, hole, hole...] lists of (lon, lat)."""
    polys = []
    for outer in area.outer_rings():
        rings = [[(n.lon, n.lat) for n in outer]]
        for inner in area.inner_rings(outer):
            rings.append([(n.lon, n.lat) for n in inner])
        if len(rings[0]) >= 4:
            polys.append(rings)
    return polys


def import_pbf(pbf: str | Path, store: GeoStore, region: str,
               progress: Callable[[str], None] | None = None) -> dict[str, int]:
    """Import one extract. Returns feature counts by layer."""
    import osmium

    pbf = Path(pbf)
    say = progress or (lambda msg: log.info(msg))
    counts = {k: 0 for k in ("building", "road", "water", "waterway",
                             "green", "poi")}
    heights = {"measured": 0, "levels": 0, "assumed": 0}

    header = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING)
    box = header.header().box()
    header.close()
    bbox = None
    if box.valid():
        bbox = (box.bottom_left.lon, box.bottom_left.lat,
                box.top_right.lon, box.top_right.lat)

    say(f"checksumming {pbf.name}")
    checksum = _md5(pbf)

    store.begin_stage()
    started = time.time()
    seen = 0

    keys = ("building", "building:part", "highway", "natural", "waterway",
            "landuse", "leisure", "water") + _POI_KEYS
    fp = (osmium.FileProcessor(str(pbf))
          .with_locations(osmium.index.create_map("flex_mem"))
          .with_areas()
          .with_filter(osmium.filter.KeyFilter(*keys)))

    for obj in fp:
        seen += 1
        if seen % 500_000 == 0:
            rate = seen / max(1.0, time.time() - started)
            say(f"{seen:,} objects, {counts['building']:,} buildings, "
                f"{counts['road']:,} roads ({rate:,.0f}/s)")
        tags = obj.tags
        try:
            if obj.is_area():
                _area(obj, tags, store, counts, heights)
            elif obj.is_way():
                _way(obj, tags, store, counts)
            elif obj.is_node():
                _node(obj, tags, store, counts)
        except Exception as exc:          # one bad geometry is not the region
            log.debug("skipped %s: %s", getattr(obj, "id", "?"), exc)

    say(f"read {seen:,} objects in {time.time() - started:.0f}s; "
        f"compressing into tiles")
    tiles = store.finish_stage(
        progress=lambda n: say(f"{n:,} tiles written"))
    counts["tiles"] = tiles
    counts.update({f"height_{k}": v for k, v in heights.items()})
    store.record_region(region, source=f"OpenStreetMap via {pbf.name}",
                        licence=OSM_LICENCE, attribution=OSM_ATTRIBUTION,
                        bbox=bbox, checksum=checksum, counts=counts)
    say(f"done: {counts}")
    return counts


def _area(obj, tags, store, counts, heights) -> None:
    building = tags.get("building")
    part = tags.get("building:part")
    if (building and building != "no") or (part and part != "no"):
        h = resolve_height(dict(tags))
        heights[h.source] += 1
        levels = tags.get("building:levels")
        for rings in _rings_of_area(obj):
            store.stage("building", "osm", ring_bbox(rings), [
                obj.id, tags.get("name"), building or part,
                int(round(h.metres * 10)), int(round(h.base_metres * 10)),
                HEIGHT_SOURCE[h.source], levels, 1 if part else 0,
                [encode_ring(r) for r in rings],
            ])
            counts["building"] += 1
        kind = _poi_kind(tags)
        name = tags.get("name")
        if name and kind:
            _stage_centroid_poi(obj, tags, store, counts, kind, name)
        return

    natural = tags.get("natural")
    if (natural == "water" or tags.get("waterway") == "riverbank"
            or tags.get("landuse") in _WATER_LANDUSE or tags.get("water")):
        for rings in _rings_of_area(obj):
            store.stage("water", "osm", ring_bbox(rings),
                        [obj.id, tags.get("name"),
                         [encode_ring(r) for r in rings]])
            counts["water"] += 1
        return

    kind = None
    if tags.get("leisure") in _GREEN_LEISURE:
        kind = tags.get("leisure")
    elif tags.get("landuse") in _GREEN_LANDUSE:
        kind = tags.get("landuse")
    elif natural in _GREEN_NATURAL:
        kind = natural
    if kind:
        for rings in _rings_of_area(obj):
            store.stage("green", "osm", ring_bbox(rings),
                        [obj.id, kind, tags.get("name"),
                         [encode_ring(r) for r in rings]])
            counts["green"] += 1

    poi = _poi_kind(tags)
    name = tags.get("name")
    if name and poi:
        _stage_centroid_poi(obj, tags, store, counts, poi, name)


def _stage_centroid_poi(obj, tags, store, counts, kind, name) -> None:
    rings = _rings_of_area(obj)
    if not rings:
        return
    outer = rings[0][0]
    lon = sum(p[0] for p in outer) / len(outer)
    lat = sum(p[1] for p in outer) / len(outer)
    store.stage("poi", "osm", (lon, lat, lon, lat),
                _poi_feature(obj.id, kind, name, tags, lon, lat))
    counts["poi"] += 1


def _poi_feature(oid, kind: str, name: str, tags, lon: float, lat: float) -> list:
    """A point of interest as stored: id, kind, name, position -- and its
    English name when it has one that differs (see vector_tiles)."""
    feature = [oid, kind, name, int(round(lon * 1e6)), int(round(lat * 1e6))]
    english = tags.get("name:en")
    if english and english != name:
        feature.append(english)
    return feature


def _way(obj, tags, store, counts) -> None:
    highway = tags.get("highway")
    if highway in _ROAD_SET and tags.get("area") != "yes":
        coords = [(n.lon, n.lat) for n in obj.nodes if n.location.valid()]
        if len(coords) >= 2:
            lanes = tags.get("lanes")
            store.stage("road", "osm", ring_bbox([coords]),
                        [obj.id, highway, tags.get("name"),
                         lanes if lanes and lanes.isdigit() else None,
                         encode_ring(coords)])
            counts["road"] += 1
        return
    waterway = tags.get("waterway")
    if waterway in _WATERWAY_LINES:
        coords = [(n.lon, n.lat) for n in obj.nodes if n.location.valid()]
        if len(coords) >= 2:
            store.stage("waterway", "osm", ring_bbox([coords]),
                        [obj.id, waterway, tags.get("name"),
                         encode_ring(coords)])
            counts["waterway"] += 1


def _node(obj, tags, store, counts) -> None:
    name = tags.get("name")
    if not name:
        return
    kind = _poi_kind(tags)
    if not kind:
        return
    loc = obj.location
    if not loc.valid():
        return
    store.stage("poi", "osm", (loc.lon, loc.lat, loc.lon, loc.lat),
                _poi_feature(obj.id, kind, name, tags, loc.lon, loc.lat))
    counts["poi"] += 1
