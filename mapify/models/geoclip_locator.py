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

import logging
import threading
from typing import Any

import numpy as np
from PIL.Image import Image as PILImage

from .base import RawCandidate

log = logging.getLogger("mapify.geoclip")

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

    def locate(self, image: PILImage, top_k: int = 12) -> list[RawCandidate]:
        if not self.load():
            raise RuntimeError(self.load_error or "GeoCLIP unavailable")
        torch = self._torch
        model = self._model

        pixel_values = model.image_encoder.preprocess_image(image.convert("RGB"))
        pixel_values = pixel_values.to(self.device)

        with torch.no_grad():
            logits = model.forward(pixel_values, model.gps_gallery)
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
            location_features = model.location_encoder(model.gps_gallery)
            image_features = torch.nn.functional.normalize(image_features, dim=1)
            location_features = torch.nn.functional.normalize(location_features, dim=1)
            logits = model.logit_scale.exp() * (image_features @ location_features.t())
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
