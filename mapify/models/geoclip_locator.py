"""GeoCLIP: the primary locator.

GeoCLIP (Vivanco Cepeda, Nayak and Shah, NeurIPS 2023) pairs a CLIP image
encoder with a learned location encoder and scores the image against a gallery
of 100,000 GPS coordinates spread over the inhabited world. The published
implementation and weights are MIT licensed.

Two departures from the packaged ``predict()``:

  * we accept a PIL image rather than a path, so an upload is never written to
    disk just to be read back;
  * we expose the loaded CLIP encoder so the evidence extractor can share it
    instead of loading a second copy.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL.Image import Image as PILImage

from .base import RawCandidate

log = logging.getLogger("mapify.geoclip")

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"

CITATION = (
    "GeoCLIP: Clip-Inspired Alignment between Locations and Images for "
    "Effective Worldwide Geo-localization. Vicente Vivanco Cepeda, "
    "Gaurav Kumar Nayak, Mubarak Shah. NeurIPS 2023. MIT licence."
)


class GeoClipLocator:
    """Worldwide coordinate prediction, run locally on CPU or CUDA."""

    name = "geoclip"

    def __init__(self, device: str = "cpu"):
        self.device = device
        self._model: Any = None
        self._torch: Any = None
        self._lock = threading.Lock()
        self._gallery_features: Any = None
        self._gallery_lock = threading.Lock()
        self.load_error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            try:
                import torch
                from geoclip import GeoCLIP

                self._torch = torch
                model = GeoCLIP()
                model.eval()
                model.to(self.device)
                self._model = model
                log.info(
                    "GeoCLIP loaded on %s, gallery=%d",
                    self.device, int(model.gps_gallery.shape[0]),
                )
                return True
            except Exception as exc:  # pragma: no cover - environment dependent
                self.load_error = f"{type(exc).__name__}: {exc}"
                log.warning("GeoCLIP failed to load: %s", self.load_error)
                return False

    @property
    def gallery_size(self) -> int:
        if self._model is None:
            return 0
        return int(self._model.gps_gallery.shape[0])

    def shared_clip(self) -> tuple[Any, Any] | tuple[None, None]:
        """The CLIP encoder and processor GeoCLIP already holds."""
        if self._model is None:
            return None, None
        enc = self._model.image_encoder
        return getattr(enc, "CLIP", None), getattr(enc, "image_processor", None)

    # ------------------------------------------------------------- gallery

    def gallery_features(self):
        """The encoded, L2-normalised GPS gallery.

        This is the expensive half of GeoCLIP -- 57 seconds on CPU for the
        100,000-point gallery -- and the packaged ``predict()`` recomputes it
        on every single call. It depends only on the gallery, which never
        changes, so it is computed once per process and memoised to disk.

        Cached as float16: at 100k x 512 that is 98 MB rather than 196, and
        the precision lost is far below the noise in a location estimate.
        """
        if self._gallery_features is not None:
            return self._gallery_features

        torch = self._torch
        model = self._model
        gallery = model.gps_gallery
        key = hashlib.sha256(
            gallery.cpu().numpy().tobytes()
        ).hexdigest()[:16]
        path = _CACHE_DIR / f"geoclip_gallery_{key}.npy"

        with self._gallery_lock:
            if self._gallery_features is not None:
                return self._gallery_features

            if path.exists():
                try:
                    arr = np.load(path)
                    if arr.shape[0] == int(gallery.shape[0]):
                        feats = torch.from_numpy(arr).to(
                            self.device, dtype=torch.float32
                        )
                        self._gallery_features = feats
                        log.info("GeoCLIP gallery features loaded from cache")
                        return feats
                except Exception:
                    path.unlink(missing_ok=True)

            log.info(
                "encoding the %d-point GPS gallery (one-off, ~1 minute on CPU)",
                int(gallery.shape[0]),
            )
            started = time.time()
            with torch.no_grad():
                feats = model.location_encoder(gallery)
                feats = torch.nn.functional.normalize(feats, dim=1)
            log.info("gallery encoded in %.1fs", time.time() - started)

            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                for stale in path.parent.glob("geoclip_gallery_*.npy"):
                    if stale != path:
                        stale.unlink(missing_ok=True)
                np.save(path, feats.cpu().numpy().astype(np.float16))
            except OSError:
                pass

            self._gallery_features = feats
            return feats

    def locate(self, image: PILImage, top_k: int = 12) -> list[RawCandidate]:
        if not self.load():
            raise RuntimeError(self.load_error or "GeoCLIP unavailable")
        torch = self._torch
        model = self._model

        pixel_values = model.image_encoder.preprocess_image(image.convert("RGB"))
        pixel_values = pixel_values.to(self.device)

        with torch.no_grad():
            image_features = model.image_encoder(pixel_values)
            image_features = torch.nn.functional.normalize(image_features, dim=1)
            logits = model.logit_scale.exp() * (
                image_features @ self.gallery_features().t()
            )
            probs = logits.softmax(dim=-1)
            k = max(1, min(int(top_k), int(model.gps_gallery.shape[0])))
            top = torch.topk(probs, k, dim=1)
            coords = model.gps_gallery.index_select(0, top.indices[0]).cpu().numpy()
            scores = top.values[0].cpu().numpy()

        return [
            RawCandidate(
                latitude=float(coords[i][0]),
                longitude=float(coords[i][1]),
                score=float(scores[i]),
                source=self.name,
            )
            for i in range(len(scores))
        ]

    def locate_with_embedding(
        self, image: PILImage, top_k: int = 12
    ) -> tuple[list[RawCandidate], np.ndarray | None]:
        """Candidates plus the CLIP image embedding, computed once.

        The evidence extractor needs the same embedding, and on CPU the vision
        tower is most of the cost, so this saves roughly half the runtime.
        """
        if not self.load():
            raise RuntimeError(self.load_error or "GeoCLIP unavailable")
        torch = self._torch
        model = self._model
        encoder = model.image_encoder

        pixel_values = encoder.preprocess_image(image.convert("RGB")).to(self.device)

        with torch.no_grad():
            clip_feats = encoder.CLIP.get_image_features(pixel_values=pixel_values)
            clip_feats = _as_tensor(clip_feats)
            # GeoCLIP's own head runs on the raw CLIP features.
            image_features = encoder.mlp(clip_feats)
            image_features = torch.nn.functional.normalize(image_features, dim=1)
            # Encoded once per process, not once per image: see
            # gallery_features().
            logits = model.logit_scale.exp() * (
                image_features @ self.gallery_features().t()
            )
            probs = logits.softmax(dim=-1)
            k = max(1, min(int(top_k), int(model.gps_gallery.shape[0])))
            top = torch.topk(probs, k, dim=1)
            coords = model.gps_gallery.index_select(0, top.indices[0]).cpu().numpy()
            scores = top.values[0].cpu().numpy()

            # The zero-shot side wants the normalised CLIP embedding, not the
            # GeoCLIP projection.
            normed = clip_feats / clip_feats.norm(dim=-1, keepdim=True)
            embedding = normed[0].cpu().numpy().astype(np.float32)

        candidates = [
            RawCandidate(
                latitude=float(coords[i][0]),
                longitude=float(coords[i][1]),
                score=float(scores[i]),
                source=self.name,
            )
            for i in range(len(scores))
        ]
        return candidates, embedding


def _as_tensor(output: Any) -> Any:
    if hasattr(output, "norm"):
        return output
    pooled = getattr(output, "pooler_output", None)
    if pooled is not None:
        return pooled
    if isinstance(output, (tuple, list)) and output:
        return output[0]
    raise TypeError(f"unexpected CLIP output: {type(output).__name__}")
