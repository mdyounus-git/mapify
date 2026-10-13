"""A second, independent locator: PLONK (Dufour et al., CVPR 2025).

PLONK learns a probability density over the globe from 48 million geotagged
Flickr photographs, on a DINOv2 encoder -- a different backbone and different
training photographs from GeoCLIP's, so where the two agree it is worth more
than either, and where they disagree that is information too. The YFCC
checkpoint only: PLONK's OpenStreetView-5M checkpoints load StreetCLIP, whose
licence is non-commercial (checked in plonk/pipe.py).

Licences: PLONK code and weights MIT, DINOv2 Apache-2.0. Weights live in the
models directory (Hugging Face cache and TORCH_HOME), downloaded once with
the user's approval. On this CPU, 32 samples in 16 steps take 10-20 s, so it
is a step the user turns on (``models.plonk_enabled``), off until measured.
"""

from __future__ import annotations

import threading

import numpy as np

from .base import RawCandidate
from .clusters import km_between, places

__all__ = ["PlonkLocator", "SOURCE", "ATTRIBUTION"]

SOURCE = "plonk"
CHECKPOINT = "nicolas-dufour/PLONK_YFCC"
ATTRIBUTION = ("Second locator: PLONK (MIT), Dufour et al., CVPR 2025, YFCC checkpoint; "
               "DINOv2 (Apache-2.0)")


class PlonkLocator:
    def __init__(self, samples: int = 32, steps: int = 16):
        self.samples = samples
        self.steps = steps
        self._pipe = None
        self._lock = threading.Lock()
        self.load_error: str | None = None

    def available(self) -> bool:
        try:
            import plonk  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        if self._pipe is None and self.load_error is None:
            with self._lock:
                if self._pipe is None and self.load_error is None:
                    try:
                        from plonk.pipe import PlonkPipeline
                        self._pipe = PlonkPipeline(CHECKPOINT)
                    except Exception as exc:          # weights missing offline, ...
                        self.load_error = f"{type(exc).__name__}: {exc}"
        return self._pipe

    def sample(self, image) -> np.ndarray:
        """Coordinates drawn from PLONK's density for this photograph."""
        pipe = self._load()
        if pipe is None:
            raise RuntimeError(self.load_error or "PLONK unavailable")
        pts = pipe(image.convert("RGB"), batch_size=self.samples, num_steps=self.steps)
        return np.asarray(pts, dtype=float).reshape(-1, 2)

    def locate(self, image, top: int = 5) -> tuple[list[RawCandidate], dict]:
        """Places PLONK's samples gather in, and how spread out they are --
        a direct reading of whether the photograph can be placed at all."""
        return self.summarise(self.sample(image), top)

    def summarise(self, pts: np.ndarray, top: int = 5) -> tuple[list[RawCandidate], dict]:
        cands = places(pts[:, 0], pts[:, 1], np.ones(len(pts)), SOURCE, top=top)
        centre = cands[0] if cands else None
        spread = (float(np.median(km_between(centre.latitude, centre.longitude,
                                              pts[:, 0], pts[:, 1])))
                  if centre else None)
        return cands, {"samples": len(pts), "median_spread_km": spread,
                       "places": [{"lat": round(c.latitude, 4), "lon": round(c.longitude, 4),
                                   "share": round(c.score, 3)} for c in cands]}
