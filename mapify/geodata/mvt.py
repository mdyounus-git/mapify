"""Decode Mapbox Vector Tiles, with nothing but the standard library.

A vector tile is a protocol buffer: layers of features, each feature a
list of integer drawing commands on a 4096-unit grid plus tags that
index into the layer's shared key and value tables. The format is
specified in https://github.com/mapbox/vector-tile-spec (version 2.1),
and it is small enough to read directly -- which keeps a dependency out
of the application for a few dozen lines.

Geometry comes back in longitude/latitude, clipped to the tile's own
bounds. Tiles carry a buffer of their neighbours' edges so renderers can
draw lines across seams; for extruded buildings that buffer means two
overlapping walls at every tile boundary, so polygons are cut exactly at
the edge and each tile keeps only its own share.
"""

from __future__ import annotations

import gzip
import math
import struct
from typing import Any, Iterator

__all__ = ["decode_tile", "tile_bounds"]

_POINT, _LINE, _POLYGON = 1, 2, 3


# ------------------------------------------------------------- protobuf

def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, pos
        shift += 7


def _fields(buf: bytes) -> Iterator[tuple[int, int, Any]]:
    """(field number, wire type, value) for one message."""
    pos, end = 0, len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 2:
            n, pos = _varint(buf, pos)
            value = buf[pos:pos + n]
            pos += n
        elif wire == 5:
            value = buf[pos:pos + 4]
            pos += 4
        elif wire == 1:
            value = buf[pos:pos + 8]
            pos += 8
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield field, wire, value


def _packed(buf: bytes) -> list[int]:
    out, pos = [], 0
    while pos < len(buf):
        v, pos = _varint(buf, pos)
        out.append(v)
    return out


def _zigzag(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _value(buf: bytes) -> Any:
    for field, _wire, v in _fields(buf):
        if field == 1:
            return v.decode("utf-8", "replace")
        if field == 2:
            return struct.unpack("<f", v)[0]
        if field == 3:
            return struct.unpack("<d", v)[0]
        if field in (4, 5):
            return v
        if field == 6:
            return _zigzag(v)
        if field == 7:
            return bool(v)
    return None


# ------------------------------------------------------------- geometry

def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2 ** z

    def lat(yy: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    return (x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0,
            lat(y))


def _commands(geom: list[int]) -> list[list[tuple[int, int]]]:
    """Decode drawing commands into rings or lines of tile coordinates."""
    parts: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    x = y = 0
    i = 0
    while i < len(geom):
        cmd = geom[i] & 7
        count = geom[i] >> 3
        i += 1
        if cmd == 1:                                    # MoveTo
            for _ in range(count):
                x += _zigzag(geom[i])
                y += _zigzag(geom[i + 1])
                i += 2
                if cur:
                    parts.append(cur)
                cur = [(x, y)]
        elif cmd == 2:                                  # LineTo
            for _ in range(count):
                x += _zigzag(geom[i])
                y += _zigzag(geom[i + 1])
                i += 2
                cur.append((x, y))
        elif cmd == 7:                                  # ClosePath
            if cur:
                cur.append(cur[0])
        else:
            break
    if cur:
        parts.append(cur)
    return parts


def _area(ring: list[tuple[float, float]]) -> float:
    s = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        s += x0 * y1 - x1 * y0
    return s / 2.0


def _clip_ring(ring: list[tuple[float, float]], extent: float
               ) -> list[tuple[float, float]]:
    """Sutherland-Hodgman against the tile square [0, extent]^2."""

    def clip(points, inside, cross):
        out = []
        if not points:
            return out
        prev = points[-1]
        for cur in points:
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
            prev = cur
        return out

    def at_x(xv):
        return lambda a, b: (xv, a[1] + (b[1] - a[1]) * (xv - a[0]) / (b[0] - a[0]))

    def at_y(yv):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (yv - a[1]) / (b[1] - a[1]), yv)

    pts = ring[:-1] if ring and ring[0] == ring[-1] else list(ring)
    pts = clip(pts, lambda p: p[0] >= 0, at_x(0.0))
    pts = clip(pts, lambda p: p[0] <= extent, at_x(extent))
    pts = clip(pts, lambda p: p[1] >= 0, at_y(0.0))
    pts = clip(pts, lambda p: p[1] <= extent, at_y(extent))
    if len(pts) >= 3:
        pts.append(pts[0])
    return pts


def _polygons(parts: list[list[tuple[int, int]]], extent: float
              ) -> list[list[list[tuple[float, float]]]]:
    """Group rings into polygons by winding, clipped to the tile."""
    polys: list[list[list[tuple[float, float]]]] = []
    for ring in parts:
        if len(ring) < 4:
            continue
        a = _area(ring)
        if a == 0:
            continue
        clipped = _clip_ring(ring, extent)
        if len(clipped) < 4:
            continue
        # Spec 2.1: in tile space (y down) exterior rings have positive
        # area by the surveyor's formula and interior rings negative.
        if a > 0 or not polys:
            polys.append([clipped])
        else:
            polys[-1].append(clipped)
    return polys


# --------------------------------------------------------------- decode

def decode_tile(data: bytes, z: int, x: int, y: int,
                layers: set[str] | None = None) -> dict[str, list[dict]]:
    """Features per layer, geometry in (lon, lat).

    Each feature: {"type": "point"|"line"|"polygon", "props": {...},
    "id": int|None, "geometry": [...]} where geometry is a point, a list
    of lines, or a list of polygons (each a list of rings).
    """
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    n = 2 ** z
    out: dict[str, list[dict]] = {}

    for field, _wire, layer_buf in _fields(data):
        if field != 3:
            continue
        name = None
        extent = 4096
        keys: list[str] = []
        values: list[Any] = []
        raw_features: list[bytes] = []
        for f, _w, v in _fields(layer_buf):
            if f == 1:
                name = v.decode("utf-8", "replace")
            elif f == 2:
                raw_features.append(v)
            elif f == 3:
                keys.append(v.decode("utf-8", "replace"))
            elif f == 4:
                values.append(_value(v))
            elif f == 5:
                extent = v
        if name is None or (layers is not None and name not in layers):
            continue

        def to_ll(px: float, py: float) -> tuple[float, float]:
            gx = (x + px / extent) / n
            gy = (y + py / extent) / n
            lon = gx * 360.0 - 180.0
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy))))
            return (lon, lat)

        feats = []
        for fb in raw_features:
            fid, gtype, tags, geom = None, 0, [], []
            for f, _w, v in _fields(fb):
                if f == 1:
                    fid = v
                elif f == 2:
                    tags = _packed(v)
                elif f == 3:
                    gtype = v
                elif f == 4:
                    geom = _packed(v)
            props = {}
            for k in range(0, len(tags) - 1, 2):
                ki, vi = tags[k], tags[k + 1]
                if ki < len(keys) and vi < len(values):
                    props[keys[ki]] = values[vi]
            parts = _commands(geom)
            if gtype == _POINT:
                pts = [p for part in parts for p in part]
                inside = [p for p in pts if 0 <= p[0] <= extent and 0 <= p[1] <= extent]
                if not inside:
                    continue
                feats.append({"type": "point", "id": fid, "props": props,
                              "geometry": to_ll(*inside[0])})
            elif gtype == _LINE:
                lines = [[to_ll(*p) for p in part] for part in parts if len(part) >= 2]
                if lines:
                    feats.append({"type": "line", "id": fid, "props": props,
                                  "geometry": lines})
            elif gtype == _POLYGON:
                polys = _polygons(parts, float(extent))
                if polys:
                    feats.append({"type": "polygon", "id": fid, "props": props,
                                  "geometry": [[[to_ll(*p) for p in ring]
                                                for ring in poly]
                                               for poly in polys]})
        out[name] = feats
    return out
