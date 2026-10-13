"""A learned re-ranker over the fused candidates.

Measured on Indian photographs: a candidate within 25 km of the truth is on
the list for half of them and first for a quarter -- the candidates usually
hold the answer and the order misses it. The recipe behind every recent lead
in the field is the same (retrieval, then a trained re-ranker: Pinpoint 2026,
GeoRanker 2025); this is a version that runs on a CPU and ships as a handful
of numbers: a linear score over features the sources already give, trained
so that, photograph by photograph, the candidate nearest the truth comes
first (scripts/train_reranker.py, on photographs no benchmark holds).

Features, per candidate (``FEATURES``):

  log_prior, prob, rank      what the fusion already thinks of it
  ev_script, ev_text,        the evidence adjustments it received, by kind
  ev_region                  (the second locator's own is left out: the
                             PLONK shares below carry it)
  plonk_25 .. plonk_750      share of PLONK's samples within that distance
  mem_25 .. mem_250          share of the street-photo memory's nearest
                             photographs within that distance (0 without it)
  from_text                  the candidate rests on text read in the image
  log_cluster, log_from_top  its cluster's size; how far it lies from the
                             fusion's own first choice

Nothing here moves an answer until the model file exists and the switch is
on, and the switch follows the measurement.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["FEATURES", "candidate_features", "Reranker", "default_path"]

FEATURES = (
    "log_prior", "prob", "rank",
    "ev_script", "ev_text", "ev_region",
    "plonk_25", "plonk_100", "plonk_250", "plonk_750",
    "mem_25", "mem_100", "mem_250",
    "from_text", "log_cluster", "log_from_top",
)
_SCRIPT = {"signage_script", "ocr_script", "ocr_language"}
_TEXT = {"toponym", "text_clue"}
_SKIP = {"second_locator"}
_MEM_TEMPERATURE = 0.02       # as models/retrieval.py weighs its neighbours


def default_path() -> Path:
    from ..paths import models_dir
    return models_dir() / "reranker" / "weights.json"


def _km(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    la, lo = math.radians(lat), math.radians(lon)
    p, q = np.radians(lats), np.radians(lons)
    a = np.sin((p - la) / 2) ** 2 + math.cos(la) * np.cos(p) * np.sin((q - lo) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def candidate_features(cands: list[Any], plonk: Any = None,
                       neighbours: list[tuple[float, float, float]] | None = None
                       ) -> np.ndarray:
    """One row of FEATURES per fused candidate, in their given order."""
    pts = np.asarray(plonk, dtype=float).reshape(-1, 2) if plonk is not None else None
    if neighbours:
        nb = np.asarray(neighbours, dtype=float)
        w = np.exp((nb[:, 2] - nb[:, 2].max()) / _MEM_TEMPERATURE)
        w = w / w.sum()
    else:
        nb = w = None
    top = cands[0] if cands else None
    rows = []
    for i, c in enumerate(cands):
        script = text = region = 0.0
        for a in c.adjustments:
            if a.source in _SKIP:
                continue
            if a.source in _SCRIPT:
                script += a.delta
            elif a.source in _TEXT or a.source.startswith("text_"):
                text += a.delta
            else:
                region += a.delta
        row = [math.log(max(c.prior_score, 1e-9)), float(c.probability), float(i),
               script, text, region]
        for r in (25, 100, 250, 750):
            row.append(float((_km(c.latitude, c.longitude, pts[:, 0], pts[:, 1]) <= r).mean())
                       if pts is not None and len(pts) else 0.0)
        for r in (25, 100, 250):
            row.append(float(w[_km(c.latitude, c.longitude, nb[:, 0], nb[:, 1]) <= r].sum())
                       if nb is not None else 0.0)
        from_text = 1.0 if getattr(c, "provenance", "model") != "model" else 0.0
        far = _km(top.latitude, top.longitude, np.array([c.latitude]), np.array([c.longitude]))[0]
        row += [from_text, math.log1p(getattr(c, "cluster_size", 1)), math.log1p(far)]
        rows.append(row)
    return np.asarray(rows, dtype=float).reshape(-1, len(FEATURES))


class Reranker:
    """A linear score over standardised features, from a weights file."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_path()
        self._model: dict | None = None

    def available(self) -> bool:
        return self.path.is_file()

    def _load(self) -> dict | None:
        if self._model is None and self.path.is_file():
            model = json.loads(self.path.read_text(encoding="utf-8"))
            if tuple(model.get("features", ())) != FEATURES:
                return None                      # trained on another feature set
            self._model = model
        return self._model

    def scores(self, feats: np.ndarray) -> np.ndarray | None:
        m = self._load()
        if m is None or not len(feats):
            return None
        z = (feats - np.asarray(m["mean"])) / np.asarray(m["std"])
        return z @ np.asarray(m["weights"]) + float(m.get("bias", 0.0))
