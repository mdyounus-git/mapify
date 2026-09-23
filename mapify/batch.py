"""Analyse a folder of photographs in one pass.

Everything expensive is already amortised: the models load once, the GPS
gallery is encoded once, the prompt bank is cached. A second image costs about
three seconds, so making someone open them one at a time wastes all of it.

The design point that matters is **when** the geocoding happens. Naming a
place is the only part of an analysis that leaves this machine, and Nominatim
allows one request per second. Geocoding every image in a 200-image folder
would take three minutes of waiting and would be rude besides, when most of
those images are usually the same handful of places.

So batch mode runs in two passes:

  1. analyse every image with the network half of enrichment switched off;
  2. cluster the results, then name each *cluster* once and hand the name back
     to every image in it.

A folder of 200 photographs of one street costs one geocoding request.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .geo.geodesy import haversine_km, weighted_centroid
from .pipeline import AnalysisEngine, AnalysisReport

log = logging.getLogger("mapify.batch")

# Two results this close together are treated as the same place for naming.
# Generous on purpose: the point is to avoid geocoding the same street twice,
# not to make a fine distinction.
CLUSTER_RADIUS_KM = 25.0

# Confidence bands whose coordinate is not worth spending a lookup on.
UNNAMEABLE = {"insufficient"}


@dataclass
class BatchItem:
    """One image in a batch, and whatever became of it."""

    path: str
    name: str
    state: str = "queued"           # queued | running | done | failed | skipped
    error: str | None = None
    elapsed_s: float = 0.0
    latitude: float | None = None
    longitude: float | None = None
    confidence: str | None = None
    uncertainty_km: float | None = None
    cluster: int | None = None
    place: str | None = None
    report: dict[str, Any] | None = None

    def as_dict(self, include_report: bool = False) -> dict[str, Any]:
        d = {
            "path": self.path,
            "name": self.name,
            "state": self.state,
            "error": self.error,
            "elapsed_s": round(self.elapsed_s, 2),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "confidence": self.confidence,
            "uncertainty_km": self.uncertainty_km,
            "cluster": self.cluster,
            "place": self.place,
        }
        if include_report:
            d["report"] = self.report
        return d


@dataclass
class Cluster:
    """A place that several images in the batch appear to share."""

    index: int
    latitude: float
    longitude: float
    members: list[int] = field(default_factory=list)
    place: str | None = None
    naming: dict[str, Any] = field(default_factory=dict)
    radius_km: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "members": self.members,
            "size": len(self.members),
            "place": self.place,
            "radius_km": round(self.radius_km, 1),
            "naming": self.naming,
        }


ProgressFn = Callable[[dict[str, Any]], None]


class BatchRunner:
    """Runs a list of images through the engine, then names the clusters."""

    def __init__(self, engine: AnalysisEngine, max_items: int = 500):
        self.engine = engine
        self.max_items = max_items
        self._cancel = threading.Event()
        self.items: list[BatchItem] = []
        self.clusters: list[Cluster] = []
        self.geocode_requests = 0

    def cancel(self) -> None:
        """Stop after the image currently being analysed.

        Whatever has finished is kept: a cancelled batch is a shorter batch,
        not a lost one.
        """
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ------------------------------------------------------------------ run

    def run(self, paths: Iterable[str], progress: ProgressFn | None = None
            ) -> dict[str, Any]:
        emit = progress or (lambda _: None)
        started = time.time()

        self.items = [
            BatchItem(path=str(p), name=Path(p).name)
            for p in list(paths)[: self.max_items]
        ]
        total = len(self.items)
        emit({"type": "start", "total": total})

        # ---- pass one: analyse, offline naming only ----------------------
        for i, item in enumerate(self.items):
            if self._cancel.is_set():
                for rest in self.items[i:]:
                    if rest.state == "queued":
                        rest.state = "skipped"
                break

            item.state = "running"
            emit({"type": "item", "index": i, "item": item.as_dict()})
            t0 = time.time()
            try:
                data = Path(item.path).read_bytes()
                report = self.engine.analyse(
                    data, item.name, online_enrichment=False
                )
            except Exception as exc:
                item.state = "failed"
                item.error = f"{type(exc).__name__}: {exc}"
                item.elapsed_s = time.time() - t0
                emit({"type": "item", "index": i, "item": item.as_dict()})
                continue

            item.elapsed_s = time.time() - t0
            if not report.ok:
                item.state = "failed"
                item.error = report.error
            else:
                item.state = "done"
                item.report = report.as_dict()
                pred = report.prediction or {}
                est = pred.get("estimated") or {}
                item.confidence = pred.get("confidence")
                item.uncertainty_km = pred.get("uncertainty_km")
                if est:
                    item.latitude = est.get("latitude")
                    item.longitude = est.get("longitude")
                    item.place = self._offline_name(est.get("place") or {})
            emit({
                "type": "item", "index": i, "item": item.as_dict(),
                "done": i + 1, "total": total,
            })

        # ---- pass two: cluster, and name each cluster once ---------------
        emit({"type": "phase", "phase": "clustering"})
        self._build_clusters()
        emit({"type": "phase", "phase": "naming",
              "clusters": len(self.clusters)})
        self._name_clusters(emit)

        elapsed = time.time() - started
        placed = [i for i in self.items if i.latitude is not None]
        summary = {
            "type": "complete",
            "total": total,
            "analysed": sum(1 for i in self.items if i.state == "done"),
            "failed": sum(1 for i in self.items if i.state == "failed"),
            "skipped": sum(1 for i in self.items if i.state == "skipped"),
            "cancelled": self._cancel.is_set(),
            "clusters": [c.as_dict() for c in self.clusters],
            "items": [i.as_dict() for i in self.items],
            "geocode_requests": self.geocode_requests,
            "elapsed_s": round(elapsed, 1),
            "seconds_per_image": (
                round(elapsed / len(placed), 2) if placed else None
            ),
        }
        emit(summary)
        return summary

    # ------------------------------------------------------------ clusters

    def _build_clusters(self) -> None:
        """Group results that are close enough to be the same place."""
        self.clusters = []
        for index, item in enumerate(self.items):
            if item.latitude is None or item.longitude is None:
                continue
            # An image the tool refused to place should not drag a cluster
            # centre around, and is not worth a lookup.
            if item.confidence in UNNAMEABLE:
                continue

            placed = False
            for cl in self.clusters:
                if haversine_km(cl.latitude, cl.longitude,
                                item.latitude, item.longitude) <= CLUSTER_RADIUS_KM:
                    cl.members.append(index)
                    points = [
                        (self.items[m].latitude, self.items[m].longitude, 1.0)
                        for m in cl.members
                    ]
                    cl.latitude, cl.longitude = weighted_centroid(points)
                    placed = True
                    break
            if not placed:
                self.clusters.append(Cluster(
                    index=len(self.clusters),
                    latitude=item.latitude,
                    longitude=item.longitude,
                    members=[index],
                ))

        # Biggest first: the place most of the batch came from is the finding.
        self.clusters.sort(key=lambda c: -len(c.members))
        for new_index, cl in enumerate(self.clusters):
            cl.index = new_index
            cl.radius_km = max(
                (haversine_km(cl.latitude, cl.longitude,
                              self.items[m].latitude, self.items[m].longitude)
                 for m in cl.members),
                default=0.0,
            )
            for m in cl.members:
                self.items[m].cluster = new_index

    def _name_clusters(self, emit: ProgressFn) -> None:
        """One geocoding request per cluster, not per image."""
        self.engine.nominatim.reset_budget()
        for cl in self.clusters:
            if self._cancel.is_set():
                break
            naming = self.engine.name_coordinate(cl.latitude, cl.longitude)
            nom = naming.get("nominatim")
            if nom and not nom.get("from_cache"):
                self.geocode_requests += 1
            cl.naming = naming
            cl.place = self._cluster_name(naming)
            for m in cl.members:
                self.items[m].place = cl.place
            emit({"type": "cluster", "cluster": cl.as_dict()})

    # ------------------------------------------------------------- naming

    @staticmethod
    def _offline_name(place: dict[str, Any]) -> str:
        parts = [place.get("city"), place.get("region"), place.get("country")]
        return ", ".join(p for p in parts if p) or "Unnamed"

    @staticmethod
    def _cluster_name(naming: dict[str, Any]) -> str:
        nom = naming.get("nominatim") or {}
        offline = naming.get("offline") or {}
        city = nom.get("city")
        region = nom.get("state")
        country = nom.get("country")
        if not city:
            nearest = (offline.get("nearest_places") or [{}])[0]
            city = nearest.get("name")
        if not region:
            region = (offline.get("admin1") or {}).get("name")
        if not country:
            country = (offline.get("country") or {}).get("name")
        return ", ".join(p for p in (city, region, country) if p) or "Unnamed"
