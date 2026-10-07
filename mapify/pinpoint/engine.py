"""The Pinpoint engine: from "around here" to "here", or an honest no.

Search areas come from the analysis (the leading candidates, an
uncontradicted GPS tag). Reference photographs taken in them are compared
with the query by geometric matching, and the verdict follows measured
thresholds:

  pinpointed  the best match has at least STRONG_INLIERS points that one
              pair of camera positions explains. Unrelated photographs
              never reached more than NEGATIVE_MAX in calibration. The
              location becomes where that photograph was taken -- or the
              weighted centre of several such matches -- give or take
              the distance between two people photographing one scene.
  possible    WEAK_INLIERS or more: shown for the reader to judge, and
              no coordinate is adopted.
  none        nothing near the candidates shows this scene. Said plainly;
              the estimate stands as it was.

The search covers each area rather than its middle. One Commons geo search
returns the nearest 500 files, which in a city centre reach a few hundred
metres; when it is cut short, more searches are placed on a hexagonal grid
whose spacing that reach sets, until the area is listed or the probe
allowance is spent. Photographs are then compared in order of promise --
nearest the area's centre first, with repeat shots from one spot pushed
back a little, so that eighteen frames from one tripod do not all go
before the next spot. A little: a whole spot spreads over at most
REPEAT_PENALTY_M of priority, however many photographs it holds, because a
viewpoint hundreds of people chose is where the query was likely taken
too -- and when the centre is right, the nearest photographs are the ones
that match (the Eiffel photograph's match was taken 7 m from it; spreading
the 760 at the tower's foot over kilometres lost it). Comparison runs in
batches and stops once one confirms.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .features import (QUERY_PIXELS, QUERY_SIDE, REFERENCE_SIDE, REFINE_SIDE, extract,
                       load, match, save, windows)
from .sources import (LISTING_CAP, Reference, commons_details, commons_listing, fetch_thumb,
                      mapillary_near, touch)

log = logging.getLogger("mapify.pinpoint")

__all__ = ["Area", "PinpointEngine", "STRONG_INLIERS", "WEAK_INLIERS", "NEGATIVE_MAX"]

# Measured by scripts/calibrate_pinpoint.py on the benchmark: 600 pairs of
# photographs of different places reached at most 14 inliers (median 8,
# 99th percentile 11); a photograph against a warped copy of itself, 231
# at the 10th percentile. The confirmation line sits near three times the
# worst chance score.
NEGATIVE_MAX = 14
WEAK_INLIERS = 20
STRONG_INLIERS = 40
# Two people photographing the same facade usually stand within a street
# or two of each other, and Commons camera positions are themselves
# placed by hand or by phone GPS.
PRECISION_M = 150.0
MAX_AREAS = 4
REFINE_TOP = 15
# Search effort. Probes are geo searches (one paced API call each, cached
# for 30 days); the budget is photographs compared in one run. A thumbnail
# Wikimedia has not cached takes it 1-3 s to make, and at most two are
# fetched at once, so a first search of BUDGET photographs takes a few
# minutes; "search further" raises the budget, and the photographs already
# compared then come from the cache in a fraction of a second each.
PROBES_PER_AREA = 13          # the centre and two hexagonal rings
DENSE_PROBES = 6              # then a small ring round the nearest crowded spot
MAX_PROBES = 26
BUDGET = 400
MAX_BUDGET = 3000
BATCH = 100
# The priority order: distance from the area's centre, plus this much in
# all spread over the photographs of one spot (cells of SPOT_M), nearest
# first, plus this much per area down the list (the analysis's best guess
# comes first).
SPOT_M = 60.0
REPEAT_PENALTY_M = 120.0
AREA_PENALTY_M = 100.0

ProgressFn = Callable[[str, str], None]
# Metres to move each listed photograph forward in the comparison order.
LiftFn = Callable[[list[Reference]], list[float]]


@dataclass
class Area:
    lat: float
    lon: float
    radius_m: float
    label: str = "the estimate"


def _fmt_m(m: float) -> str:
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{m:.0f} m"


class PinpointEngine:
    def __init__(self, cache_dir: Path, online: bool = True, mapillary_token: str = "",
                 workers: int = 6, cache_mb: int = 2048):
        self.cache = Path(cache_dir)
        self.online = online
        self.token = mapillary_token or ""
        self.workers = workers
        self.cache_mb = cache_mb

    def prune(self) -> int:
        """Keep thumbnails and features under ``cache_mb``: past it, the
        files used longest ago go first, down to four fifths of the cap.
        Returns the number of files removed."""
        files = []
        for sub in ("thumbs", "features"):
            d = self.cache / sub
            if d.is_dir():
                for f in d.iterdir():
                    try:
                        st = f.stat()
                    except OSError:
                        continue
                    files.append((st.st_mtime, st.st_size, f))
        total = sum(size for _, size, _ in files)
        cap = self.cache_mb * 1024 * 1024
        if total <= cap:
            return 0
        removed = 0
        for _, size, f in sorted(files):
            if total <= cap * 0.8:
                break
            try:
                f.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        return removed

    # ------------------------------------------------------------ pieces

    def _probe_points(self, a: Area, reach_m: float) -> list[tuple[float, float]]:
        """Hexagonal grid points within the area, nearest the centre first.

        Circles of radius ``reach_m`` centred on a hexagonal grid of spacing
        reach * sqrt(3) cover the plane without gaps.
        """
        spacing = reach_m * math.sqrt(3)
        n = int(a.radius_m / spacing) + 2
        pts = []
        for j in range(-n, n + 1):
            y = j * spacing * math.sqrt(3) / 2
            for i in range(-n, n + 1):
                x = i * spacing + (spacing / 2 if j % 2 else 0.0)
                d = math.hypot(x, y)
                if 0 < d <= a.radius_m + reach_m / 2:
                    pts.append((d, y, x))
        pts.sort()
        coslat = max(0.2, math.cos(math.radians(a.lat)))
        return [(a.lat + y / 111320.0, a.lon + x / (111320.0 * coslat)) for _, y, x in pts]

    def _plan(self, areas: list[Area], step: ProgressFn, exclude: set[str],
              lift: LiftFn | None = None) -> tuple[list[Reference], list[str], list[dict]]:
        """Every photograph listed in the areas, in the order to compare them."""
        from ..geo.geodesy import haversine_km
        notes: list[str] = []
        coverage: list[dict] = []
        found: dict[str, tuple[float, int, Reference]] = {}     # key -> (dist, area, ref)
        probes_left = MAX_PROBES

        def take(refs: list[Reference], idx: int, a: Area) -> None:
            for r in refs:
                if r.id in exclude:
                    continue
                d = haversine_km(a.lat, a.lon, r.lat, r.lon) * 1000
                if d > a.radius_m:
                    continue
                old = found.get(r.key)
                if old is None or d + idx * AREA_PENALTY_M < old[0] + old[1] * AREA_PENALTY_M:
                    r.distance_m = d
                    r.extra["area"] = a.label
                    found[r.key] = (d, idx, r)

        for idx, a in enumerate(areas[:MAX_AREAS]):
            info = {"label": a.label, "radius_m": round(a.radius_m), "probes": 0,
                    "listed_within_m": None}
            coverage.append(info)
            if probes_left <= 0:
                notes.append(f"The probe allowance was spent before {a.label} was searched.")
                continue
            step("search", f"Listing photographs taken within {_fmt_m(a.radius_m)} "
                           f"of {a.label}")
            try:
                first = commons_listing(a.lat, a.lon, int(a.radius_m), self.cache)
            except Exception as exc:                        # network, quota, format
                notes.append(f"Wikimedia Commons could not be searched near {a.label} "
                             f"({type(exc).__name__})")
                continue
            probes_left -= 1
            info["probes"] = 1
            take(first, idx, a)
            reach = first[-1].distance_m if len(first) >= LISTING_CAP else a.radius_m
            if reach < a.radius_m * 0.95:
                reach = max(reach, 50.0)
                points = self._probe_points(a, reach)[:min(PROBES_PER_AREA - 1, probes_left)]
                crowded = []                    # (distance, lat, lon, reach) of full searches
                for k, (plat, plon) in enumerate(points, 1):
                    step("search", f"Listing photographs around {a.label}: "
                                   f"{k + 1} of {len(points) + 1} places")
                    try:
                        more = commons_listing(plat, plon, int(math.ceil(reach)), self.cache)
                    except Exception as exc:
                        notes.append(f"A Commons search near {a.label} failed "
                                     f"({type(exc).__name__}); that patch is a gap.")
                        continue
                    probes_left -= 1
                    info["probes"] += 1
                    # This search's own reach, before take() measures from the centre.
                    own = more[-1].distance_m if len(more) >= LISTING_CAP else None
                    take(more, idx, a)
                    if own is not None and own < reach * 0.5:
                        crowded.append((haversine_km(a.lat, a.lon, plat, plon) * 1000,
                                        plat, plon, max(25.0, own)))
                # A search that filled up in under half the reach sits on a far
                # denser spot than the centre -- a landmark's foot, a square --
                # and the grid leaves gaps there. The nearest one gets a ring
                # of its own, sized by its own reach.
                if crowded and probes_left > 0:
                    _, clat, clon, creach = min(crowded)
                    ring = self._probe_points(Area(clat, clon, creach * 1.8), creach)
                    for plat, plon in ring[:min(DENSE_PROBES, probes_left)]:
                        step("search", f"Listing photographs at a crowded spot near "
                                       f"{a.label}")
                        try:
                            more = commons_listing(plat, plon, int(math.ceil(creach)),
                                                   self.cache)
                        except Exception:
                            continue
                        probes_left -= 1
                        info["probes"] += 1
                        take(more, idx, a)
                far = haversine_km(a.lat, a.lon, *points[-1]) * 1000 if points else 0.0
                listed_to = min(a.radius_m, far + reach)
                info["listed_within_m"] = round(listed_to)
                if listed_to < a.radius_m * 0.95:
                    notes.append(f"Photographs around {a.label} are dense: they were "
                                 f"listed out to about {_fmt_m(listed_to)} of the "
                                 f"{_fmt_m(a.radius_m)} asked for.")
            else:
                info["listed_within_m"] = round(a.radius_m)
            if self.token:
                try:
                    take(mapillary_near(a.lat, a.lon, int(a.radius_m), self.token,
                                        self.cache), idx, a)
                except Exception as exc:
                    notes.append(f"Mapillary could not be searched near {a.label} "
                                 f"({type(exc).__name__})")

        # Priority: distance, a small push per earlier photograph from the
        # same spot, and a push per area down the list.
        def cell(r: Reference) -> tuple[int, int]:
            return (int(math.floor(r.lat * 111320.0 / SPOT_M)),
                    int(math.floor(r.lon * 111320.0
                                   * max(0.2, math.cos(math.radians(r.lat))) / SPOT_M)))

        # ``lift``, when given, moves photographs forward by so many metres
        # each -- first within their spot, then overall.
        lifts: dict[str, float] = {}
        if lift is not None and found:
            listed = [t[2] for t in found.values()]
            try:
                lifts = {r.key: max(0.0, float(v)) for r, v in zip(listed, lift(listed))}
            except Exception as exc:
                notes.append(f"The titles could not be weighed ({type(exc).__name__}); "
                             f"distance alone orders the search.")
        spots: dict[tuple[int, int], list] = {}
        for d, idx, r in found.values():
            spots.setdefault(cell(r), []).append((d, idx, r))
        ranked = []
        for members in spots.values():
            members.sort(key=lambda t: (-lifts.get(t[2].key, 0.0), t[0], t[2].key))
            step_m = REPEAT_PENALTY_M / len(members)
            for k, (d, idx, r) in enumerate(members):
                ranked.append((d + k * step_m + idx * AREA_PENALTY_M
                               - lifts.get(r.key, 0.0), r))
        ranked.sort(key=lambda t: (t[0], t[1].key))
        return [r for _, r in ranked], notes, coverage

    def _features(self, ref: Reference, width: int = REFERENCE_SIDE):
        suffix = "" if width == REFERENCE_SIDE else f"-{width}"
        path = self.cache / "features" / f"{ref.key}{suffix}.npz"
        feats = load(path) if path.is_file() else None
        if feats is not None:
            touch(path)
            return feats
        thumb = fetch_thumb(ref, self.cache)
        if thumb is None:
            return None
        try:
            with Image.open(thumb) as im:
                feats = extract(im, width)
        except Exception:                                   # a broken thumbnail
            return None
        save(feats, path)
        return feats

    # ------------------------------------------------------------- run

    def run(self, image: Image.Image, areas: list[Area],
            progress: ProgressFn | None = None,
            exclude: set[str] | None = None, budget: int = BUDGET) -> dict[str, Any]:
        """``budget``: photographs to compare at most, in order of promise.
        ``exclude``: reference ids never to match -- for evaluation, where a
        benchmark photograph must not be found as its own Commons copy."""
        try:
            return self._run(image, areas, progress, exclude,
                             max(1, min(MAX_BUDGET, int(budget))))
        finally:
            try:
                self.prune()
            except OSError:                             # pragma: no cover
                log.warning("the Pinpoint cache could not be pruned")

    def _run(self, image: Image.Image, areas: list[Area], progress: ProgressFn | None,
             exclude: set[str] | None, budget: int) -> dict[str, Any]:
        step = progress or (lambda k, m: None)
        searched = {"areas": [{"lat": a.lat, "lon": a.lon, "radius_m": a.radius_m,
                               "label": a.label} for a in areas[:MAX_AREAS]],
                    "references": 0, "budget": budget, "sources": ["Wikimedia Commons"]
                    + (["Mapillary"] if self.token else [])}
        threshold = {"strong": STRONG_INLIERS, "weak": WEAK_INLIERS,
                     "unrelated_photographs_reach": NEGATIVE_MAX}
        base = {"searched": searched, "threshold": threshold, "matches": [],
                "best": None, "location": None, "notes": []}
        if not self.online:
            return {**base, "verdict": "unavailable",
                    "message": "Pinpoint needs the network to find reference "
                               "photographs, and the application is offline."}
        if not areas:
            return {**base, "verdict": "unavailable",
                    "message": "There is no estimate to search around."}

        step("features", "Reading the photograph's distinctive points")
        query = extract(image, QUERY_SIDE, QUERY_PIXELS, nfeatures=6000)
        if len(query.points) < 40:
            return {**base, "verdict": "none",
                    "message": "The photograph has too few distinctive points to match "
                               "(sky, blur, water or a blank wall); nothing can be "
                               "pinpointed from it."}

        views = windows(query)

        def best_match(feats):
            return max((match(v, feats) for v in views), key=lambda m: m.inliers)

        order, notes, coverage = self._plan(areas, step, set(exclude or ()))
        searched["areas"] = [{**sa, **cv} for sa, cv in zip(searched["areas"], coverage)]
        searched["listed"] = len(order)
        base["notes"] = notes
        if not order and any(c["probes"] == 0 for c in coverage):
            return {**base, "verdict": "unavailable",
                    "message": "The search for reference photographs failed ("
                               + "; ".join(notes) + "), so nothing was compared. Try "
                               "again later; the estimate stands."}
        if not order:
            return {**base, "verdict": "none",
                    "message": "No geotagged photographs were found near the candidates, "
                               "so there is nothing to match against. The estimate stands."}

        scored: list = []
        refined: dict[str, Any] = {}
        compared = 0

        def refine(pairs) -> None:
            """The more promising ones again at REFINE_SIDE: a building a few
            dozen pixels across in a thumbnail has many more points to agree
            on at the larger size; the better of the two counts."""
            pairs = [(m, r) for m, r in pairs if r.key not in refined and m.inliers >= 8]
            if not pairs:
                return
            step("refine", f"Looking closer at {len(pairs)} similar photograph"
                           f"{'' if len(pairs) == 1 else 's'}")
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {pool.submit(self._features, r, REFINE_SIDE): (m, r)
                           for m, r in pairs}
                for fut in as_completed(futures):
                    m, r = futures[fut]
                    try:
                        feats = fut.result()
                    except Exception:
                        feats = None
                    m2 = best_match(feats) if feats is not None else None
                    refined[r.key] = m2 if m2 is not None and m2.inliers > m.inliers else m

        pos = 0
        while pos < len(order) and compared < budget:
            raw = order[pos:pos + min(BATCH, budget - compared)]
            pos += len(raw)
            try:
                batch = commons_details(raw, self.cache)
            except Exception as exc:
                notes.append(f"Photograph details could not be fetched "
                             f"({type(exc).__name__}); the search stopped there.")
                break
            batch_scored = []
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {pool.submit(self._features, r): r for r in batch}
                for fut in as_completed(futures):
                    ref = futures[fut]
                    compared += 1
                    try:
                        feats = fut.result()
                    except Exception:
                        feats = None
                    if feats is not None:
                        batch_scored.append((best_match(feats), ref))
                    if compared % 10 == 0:
                        step("compare", f"Compared {compared} photographs "
                                        f"(of {len(order)} listed)")
            batch_scored.sort(key=lambda mr: -mr[0].inliers)
            refine(batch_scored[:5])
            scored += [(refined.get(r.key, m), r) for m, r in batch_scored]
            if any(m.inliers >= STRONG_INLIERS for m, _ in scored):
                break                                   # confirmed: no need to go on
        searched["references"] = compared

        scored.sort(key=lambda mr: (-mr[0].inliers, mr[1].distance_m))
        if not any(m.inliers >= STRONG_INLIERS for m, _ in scored):
            refine(scored[:REFINE_TOP])
            scored = [(refined.get(r.key, m), r) for m, r in scored]
            scored.sort(key=lambda mr: (-mr[0].inliers, mr[1].distance_m))

        top = [{**r.as_dict(), "match": m.as_dict(), "area": r.extra.get("area")}
               for m, r in scored[:6]]
        base["matches"] = top
        if not scored:
            return {**base, "verdict": "none",
                    "message": "The reference photographs could not be downloaded. "
                               "The estimate stands."}

        best_m, best_r = scored[0]
        base["best"] = top[0]
        if best_m.inliers >= STRONG_INLIERS:
            strong = [(m, r) for m, r in scored if m.inliers >= STRONG_INLIERS]
            # Several confirmed matches of one scene: their inlier-weighted
            # centre, and the spread between them as the honest radius.
            from ..geo.geodesy import haversine_km
            near = [(m, r) for m, r in strong
                    if haversine_km(best_r.lat, best_r.lon, r.lat, r.lon) <= 0.4]
            wsum = sum(m.inliers for m, _ in near)
            lat = sum(m.inliers * r.lat for m, r in near) / wsum
            lon = sum(m.inliers * r.lon for m, r in near) / wsum
            spread = max(haversine_km(lat, lon, r.lat, r.lon) * 1000 for _, r in near)
            precision = max(PRECISION_M, spread)
            location = {"lat": round(lat, 6), "lon": round(lon, 6),
                        "precision_m": round(precision), "matched_photographs": len(near)}
            return {**base, "verdict": "pinpointed", "location": location,
                    "message": (f"Pinpointed: this photograph shows the same scene as "
                                f"{'a photograph' if len(near) == 1 else f'{len(near)} photographs'} "
                                f"taken here -- {best_m.inliers} matching points that one "
                                f"pair of camera positions explains, where unrelated "
                                f"photographs reach at most {NEGATIVE_MAX}. The camera "
                                f"stood within roughly {_fmt_m(precision)} of this spot.")}
        if best_m.inliers >= WEAK_INLIERS:
            return {**base, "verdict": "possible",
                    "message": (f"A possible match ({best_m.inliers} matching points), "
                                f"below the {STRONG_INLIERS} needed to confirm it: shown "
                                f"for you to judge by eye, and no location is adopted.")}
        unread = len(order) - compared
        return {**base, "verdict": "none",
                "message": (f"None of the {compared} photographs compared near the "
                            f"candidates shows this scene (best: {best_m.inliers} points, "
                            f"chance level)"
                            + (f"; {unread} more listed further out have not been "
                               f"compared yet" if unread > 0 else "")
                            + ". The estimate stands as it was.")}
