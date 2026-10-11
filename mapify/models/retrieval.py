"""Retrieval memory: where photographs that look like this one were taken.

GeoCLIP answers from a fixed gallery of 100,000 coordinates. A memory of real
street-level photographs answers with real positions: the photograph's CLIP
embedding is compared with every photograph in the memory
(scripts/build_retrieval.py, OpenStreetView-5M, CC BY-SA 4.0), and the
nearest neighbours, grouped into places, become candidate positions.

On OpenStreetView-5M's own street-level test set, nearest-neighbour retrieval
with this very embedding found the right city 19.3% of the time against 5.9%
for the authors' trained model (Reported, the dataset's paper). Whether it
helps on Mapify's photographs is for the benchmark to say, so it is off until
measured (``models.retrieval_enabled``).

It is not an independent line of evidence: it reads the same embedding as
GeoCLIP. Its neighbours are offered as positions for the fusion to score,
never as a second vote.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from .base import RawCandidate
from .clusters import places

__all__ = ["RetrievalMemory", "SOURCE", "ATTRIBUTION"]

SOURCE = "street-photo-memory"
ATTRIBUTION = ("Street-level retrieval: OpenStreetView-5M (CC BY-SA 4.0), imagery by "
               "Mapillary contributors")
_TEMPERATURE = 0.02          # similarities this far apart weigh e-fold apart


def _default_dir() -> Path:
    from ..paths import models_dir
    return models_dir() / "osv5m" / "index"


class RetrievalMemory:
    def __init__(self, index_dir: Path | None = None):
        self.dir = Path(index_dir) if index_dir else _default_dir()
        self._lock = threading.Lock()
        self._emb: np.ndarray | None = None
        self._lat = self._lon = None

    def available(self) -> bool:
        return self.dir.is_dir() and any(self.dir.glob("*.npz"))

    def _load(self) -> bool:
        if self._emb is not None:
            return True
        with self._lock:
            if self._emb is not None:
                return True
            parts = sorted(p for p in self.dir.glob("*.npz") if ".part" not in p.name)
            if not parts:
                return False
            embs, lats, lons = [], [], []
            for p in parts:
                with np.load(p) as z:
                    embs.append(z["emb"].astype(np.float16))
                    lats.append(z["lat"].astype(np.float32))
                    lons.append(z["lon"].astype(np.float32))
            self._lat = np.concatenate(lats)
            self._lon = np.concatenate(lons)
            self._emb = np.concatenate(embs)
            return True

    @property
    def size(self) -> int:
        return int(self._emb.shape[0]) if self._load() else 0

    def neighbours(self, embedding: np.ndarray, k: int = 50
                   ) -> list[tuple[float, float, float]]:
        """The ``k`` most similar photographs: (lat, lon, cosine similarity)."""
        if embedding is None or not self._load():
            return []
        q = np.asarray(embedding, dtype=np.float32).ravel()
        q = q / (np.linalg.norm(q) or 1.0)
        sims = np.empty(self._emb.shape[0], dtype=np.float32)
        step = 65536                      # float16 rows, scored in float32 chunks
        for i in range(0, self._emb.shape[0], step):
            sims[i:i + step] = self._emb[i:i + step].astype(np.float32) @ q
        k = min(k, sims.shape[0])
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        return [(float(self._lat[i]), float(self._lon[i]), float(sims[i])) for i in top]

    def candidates(self, embedding: np.ndarray, k: int = 50, radius_km: float = 25.0,
                   top: int = 5) -> list[RawCandidate]:
        """The neighbours grouped into places, each scored by the share of the
        neighbours' weight it holds; at most ``top``."""
        nb = self.neighbours(embedding, k)
        if not nb:
            return []
        sim = np.array([n[2] for n in nb])
        weight = np.exp((sim - sim.max()) / _TEMPERATURE)
        return places([n[0] for n in nb], [n[1] for n in nb], weight, SOURCE,
                      radius_km=radius_km, top=top)
