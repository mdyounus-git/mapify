"""A small FlatGeobuf reader that fetches only what a bounding box needs.

FlatGeobuf is built for exactly this: a fixed header, then a packed
Hilbert R-tree over every feature's bounding box, then the features in
the same Hilbert order. A reader that wants one neighbourhood out of a
1.8 GB file walks the tree from the root, fetching only the index nodes
whose boxes overlap, and then only the features those leaves point to --
which, because of the Hilbert ordering, sit close together on disk.

That turns "download this country's buildings" into "download the few
hundred kilobytes around this coordinate", over plain HTTP range
requests, which is the access pattern the format and the open-data
hosts serving it are designed for.

No dependency is needed: FlatBuffers tables are simple enough to read
with ``struct``, and this reads only the handful of fields it uses.

Format reference: https://flatgeobuf.org (BSD-2-Clause specification).
"""

from __future__ import annotations

import math
import struct
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

__all__ = ["FgbSource", "FgbHeader", "open_url", "open_file"]

_MAGIC = b"fgb\x03fgb"
_NODE = 40                          # four doubles and a uint64
_UA = ("Mapify/0.1 (open-source image geolocation; "
       "https://github.com/mdyounus-git/mapify)")

# ColumnType values from the FlatGeobuf schema.
_COL_SIZES = {0: 1, 1: 1, 2: 1, 3: 2, 4: 2, 5: 4, 6: 4, 7: 8, 8: 8, 9: 4,
              10: 8}
_COL_FMT = {0: "<b", 1: "<B", 2: "<?", 3: "<h", 4: "<H", 5: "<i", 6: "<I",
            7: "<q", 8: "<Q", 9: "<f", 10: "<d"}


# ------------------------------------------------------------- flatbuffers

def _u32(buf: bytes, pos: int) -> int:
    return struct.unpack_from("<I", buf, pos)[0]


class _Table:
    """Just enough of a FlatBuffers table to read scalars, vectors, strings."""

    __slots__ = ("buf", "pos", "offsets")

    def __init__(self, buf: bytes, pos: int):
        self.buf = buf
        self.pos = pos
        vt = pos - struct.unpack_from("<i", buf, pos)[0]
        vsize = struct.unpack_from("<H", buf, vt)[0]
        count = (vsize - 4) // 2
        self.offsets = struct.unpack_from(f"<{count}H", buf, vt + 4)

    def _field(self, i: int) -> int | None:
        if i < len(self.offsets) and self.offsets[i]:
            return self.pos + self.offsets[i]
        return None

    def scalar(self, i: int, fmt: str, default: Any) -> Any:
        p = self._field(i)
        return default if p is None else struct.unpack_from(fmt, self.buf, p)[0]

    def _vector(self, i: int) -> tuple[int, int] | None:
        p = self._field(i)
        if p is None:
            return None
        start = p + _u32(self.buf, p)
        return start + 4, _u32(self.buf, start)

    def doubles(self, i: int) -> tuple[float, ...]:
        v = self._vector(i)
        return () if v is None else struct.unpack_from(f"<{v[1]}d", self.buf, v[0])

    def uints(self, i: int) -> tuple[int, ...]:
        v = self._vector(i)
        return () if v is None else struct.unpack_from(f"<{v[1]}I", self.buf, v[0])

    def bytes_(self, i: int) -> bytes:
        v = self._vector(i)
        return b"" if v is None else self.buf[v[0]:v[0] + v[1]]

    def string(self, i: int) -> str | None:
        v = self._vector(i)
        return None if v is None else self.buf[v[0]:v[0] + v[1]].decode("utf-8")

    def tables(self, i: int) -> list["_Table"]:
        v = self._vector(i)
        if v is None:
            return []
        out = []
        for k in range(v[1]):
            p = v[0] + 4 * k
            out.append(_Table(self.buf, p + _u32(self.buf, p)))
        return out

    def table(self, i: int) -> "_Table | None":
        p = self._field(i)
        return None if p is None else _Table(self.buf, p + _u32(self.buf, p))


# ------------------------------------------------------------------ header

@dataclass
class FgbHeader:
    geometry_type: int
    features_count: int
    index_node_size: int
    envelope: tuple[float, ...]
    columns: list[tuple[str, int]]
    header_end: int                     # byte offset where the index starts
    index_bytes: int = 0
    level_bounds: list[tuple[int, int]] = field(default_factory=list)
    num_nodes: int = 0

    @property
    def features_start(self) -> int:
        return self.header_end + self.index_bytes


def _level_bounds(num_items: int, node_size: int
                  ) -> tuple[list[tuple[int, int]], int]:
    """Node index ranges per level, leaves first, as the format lays them out."""
    n = num_items
    counts = [n]
    total = n
    while True:
        n = math.ceil(n / node_size)
        total += n
        counts.append(n)
        if n == 1:
            break
    bounds = []
    end = total
    for size in counts:
        bounds.append((end - size, end))
        end -= size
    return bounds, total


def parse_header(first: bytes) -> FgbHeader:
    if not first.startswith(_MAGIC):
        raise ValueError("not a FlatGeobuf file")
    size = _u32(first, 8)
    if len(first) < 12 + size:
        raise ValueError(f"need {12 + size} bytes for the header")
    buf = first[12:12 + size]
    t = _Table(buf, _u32(buf, 0))
    cols = []
    for c in t.tables(7):
        cols.append((c.string(0) or "", c.scalar(1, "<B", 0)))
    hdr = FgbHeader(
        geometry_type=t.scalar(2, "<B", 0),
        features_count=t.scalar(8, "<Q", 0),
        index_node_size=t.scalar(9, "<H", 16),
        envelope=t.doubles(1),
        columns=cols,
        header_end=12 + size,
    )
    if hdr.index_node_size >= 2 and hdr.features_count > 0:
        hdr.level_bounds, hdr.num_nodes = _level_bounds(
            hdr.features_count, hdr.index_node_size)
        hdr.index_bytes = hdr.num_nodes * _NODE
    return hdr


# ----------------------------------------------------------------- reading

Reader = Callable[[int, int], bytes]        # (offset, length) -> bytes


def _coalesce(spans: list[tuple[int, int]], gap: int
              ) -> list[tuple[int, int]]:
    """Merge (start, end) byte spans closer than ``gap`` into one request."""
    out: list[list[int]] = []
    for s, e in sorted(spans):
        if out and s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


class FgbSource:
    """One FlatGeobuf file behind a byte-range reader."""

    def __init__(self, read: Reader, name: str = ""):
        self.read = read
        self.name = name
        first = read(0, 16384)
        size = _u32(first, 8) if first.startswith(_MAGIC) else 0
        if len(first) < 12 + size:
            first = read(0, 12 + size)
        self.header = parse_header(first)
        self.requests = 0
        self.bytes_read = len(first)

    def _get(self, offset: int, length: int) -> bytes:
        self.requests += 1
        data = self.read(offset, length)
        self.bytes_read += len(data)
        return data

    def _get_many(self, spans: list[tuple[int, int]]) -> dict[tuple[int, int], bytes]:
        """Fetch several (start, end) spans at once.

        Over a long network path each range request is mostly waiting, so
        the ones a tree level needs go out together rather than in turn.
        """
        if len(spans) <= 1:
            return {sp: self._get(sp[0], sp[1] - sp[0]) for sp in spans}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(spans))) as pool:
            blobs = list(pool.map(lambda sp: self._get(sp[0], sp[1] - sp[0]),
                                  spans))
        return dict(zip(spans, blobs))

    def search(self, bbox: tuple[float, float, float, float],
               limit: int = 200_000) -> list[tuple[int, int]]:
        """(feature byte offset, feature index) for every box overlapping."""
        hdr = self.header
        if not hdr.level_bounds:
            raise ValueError(f"{self.name}: file has no spatial index")
        minx, miny, maxx, maxy = bbox
        leaves_start = hdr.num_nodes - hdr.features_count
        found: list[tuple[int, int]] = []
        frontier = [0]                                  # the root node
        level = len(hdr.level_bounds) - 1
        while frontier and level >= 0:
            level_end = hdr.level_bounds[level][1]
            groups = sorted({(i, min(i + hdr.index_node_size, level_end))
                             for i in frontier})
            spans = [(hdr.header_end + a * _NODE, hdr.header_end + b * _NODE)
                     for a, b in groups]
            blobs = self._get_many(_coalesce(spans, gap=4096))
            nxt: list[int] = []
            for a, b in groups:
                start = hdr.header_end + a * _NODE
                blob_key = next(k for k in blobs if k[0] <= start < k[1])
                blob = blobs[blob_key]
                base = start - blob_key[0]
                for k in range(b - a):
                    x0, y0, x1, y1, off = struct.unpack_from(
                        "<ddddQ", blob, base + k * _NODE)
                    if x1 < minx or x0 > maxx or y1 < miny or y0 > maxy:
                        continue
                    pos = a + k
                    if pos >= leaves_start:
                        found.append((off, pos - leaves_start))
                        if len(found) >= limit:
                            return found
                    else:
                        nxt.append(int(off))
            frontier = nxt
            level -= 1
        return found

    def features(self, hits: list[tuple[int, int]]
                 ) -> Iterator[tuple[int, dict[str, Any], list[list[tuple[float, float]]]]]:
        """(feature index, properties, rings) for each search hit."""
        if not hits:
            return
        start = self.header.features_start
        # Features are size-prefixed; fetch each with a margin and grow
        # the request only for the rare one that did not fit.
        spans = [(start + off, start + off + 2048) for off, _ in hits]
        fetched = self._get_many(_coalesce(spans, gap=32768))
        chunks = sorted((s, blob) for (s, _e), blob in fetched.items())
        for off, index in sorted(hits):
            at = start + off
            chunk_start, chunk = next((cs, c) for cs, c in chunks
                                      if cs <= at < cs + len(c))
            rel = at - chunk_start
            if rel + 4 > len(chunk):
                chunk_start, chunk, rel = at, self._get(at, 4096), 0
            size = _u32(chunk, rel)
            if rel + 4 + size > len(chunk):
                chunk_start, chunk, rel = at, self._get(at, size + 4), 0
            buf = chunk[rel + 4: rel + 4 + size]
            yield (index,) + self._decode(buf)

    def _decode(self, buf: bytes
                ) -> tuple[dict[str, Any], list[list[tuple[float, float]]]]:
        feature = _Table(buf, _u32(buf, 0))
        geom = feature.table(0)
        rings: list[list[tuple[float, float]]] = []
        if geom is not None:
            parts = geom.tables(7) or [geom]
            for part in parts:
                xy = part.doubles(1)
                ends = part.uints(0) or (len(xy) // 2,)
                prev = 0
                for end in ends:
                    ring = [(xy[2 * k], xy[2 * k + 1]) for k in range(prev, end)]
                    if len(ring) >= 3:
                        rings.append(ring)
                    prev = end
        props: dict[str, Any] = {}
        raw = feature.bytes_(1)
        p = 0
        cols = self.header.columns
        while p + 2 <= len(raw):
            ci = struct.unpack_from("<H", raw, p)[0]
            p += 2
            if ci >= len(cols):
                break
            name, ctype = cols[ci]
            if ctype in _COL_FMT:
                props[name] = struct.unpack_from(_COL_FMT[ctype], raw, p)[0]
                p += _COL_SIZES[ctype]
            elif ctype in (11, 12, 13, 14):         # String Json DateTime Binary
                n = _u32(raw, p)
                value = raw[p + 4: p + 4 + n]
                props[name] = value.decode("utf-8", "replace") if ctype != 14 else value
                p += 4 + n
            else:
                break
        return props, rings


def open_url(url: str, timeout: float = 60.0) -> FgbSource:
    def read(offset: int, length: int) -> bytes:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA, "Range": f"bytes={offset}-{offset + length - 1}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    return FgbSource(read, url.rsplit("/", 1)[-1])


def open_file(path: str) -> FgbSource:
    fh = open(path, "rb")

    def read(offset: int, length: int) -> bytes:
        fh.seek(offset)
        return fh.read(length)
    return FgbSource(read, path)
