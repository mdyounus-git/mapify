"""The shared CLIP backbone.

GeoCLIP is built on ``openai/clip-vit-large-patch14``, so the evidence
extractor and the country gazetteer locator reuse that same loaded model
rather than pulling a second 1.7 GB of weights. The CLIP weights are MIT
licensed.

Text embeddings for the prompt bank never change between runs, so they are
computed once and cached to disk; after the first run, startup is a file read.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL.Image import Image as PILImage

log = logging.getLogger("mapify.clip")

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"


class ClipBackend:
    """Lazily-loaded CLIP image/text encoder with a disk-cached text side."""

    def __init__(self, model_id: str = "openai/clip-vit-large-patch14",
                 device: str = "cpu"):
        self.model_id = model_id
        self.device = device
        self._model: Any = None
        self._processor: Any = None
        self._torch: Any = None
        self._lock = threading.Lock()
        self.load_error: str | None = None

    # ------------------------------------------------------------- loading

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self, shared_clip: Any = None, shared_processor: Any = None) -> bool:
        """Load the encoder, or adopt one GeoCLIP has already loaded."""
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            try:
                import torch

                self._torch = torch
                if shared_clip is not None and shared_processor is not None:
                    self._model = shared_clip
                    self._processor = shared_processor
                    log.info("CLIP: reusing the encoder GeoCLIP already loaded")
                else:
                    from transformers import AutoProcessor, CLIPModel

                    self._model = CLIPModel.from_pretrained(self.model_id)
                    self._processor = AutoProcessor.from_pretrained(self.model_id)
                    log.info("CLIP: loaded %s", self.model_id)
                self._model.eval()
                self._model.to(self.device)
                return True
            except Exception as exc:  # pragma: no cover - environment dependent
                self.load_error = f"{type(exc).__name__}: {exc}"
                log.warning("CLIP failed to load: %s", self.load_error)
                return False

    # ---------------------------------------------------------- embeddings

    def encode_image(self, image: PILImage) -> np.ndarray:
        """L2-normalised image embedding, shape (D,)."""
        if not self.load():
            raise RuntimeError(self.load_error or "CLIP unavailable")
        torch = self._torch
        inputs = self._processor(images=image.convert("RGB"), return_tensors="pt")
        pixel_values = inputs["pixel_values"].to(self.device)
        with torch.no_grad():
            feats = self._extract(self._model.get_image_features(
                pixel_values=pixel_values
            ))
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats[0].cpu().numpy().astype(np.float32)

    def encode_texts(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        """L2-normalised text embeddings, shape (N, D)."""
        if not self.load():
            raise RuntimeError(self.load_error or "CLIP unavailable")
        torch = self._torch
        chunks: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            inputs = self._processor(
                text=batch, return_tensors="pt", padding=True, truncation=True
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()
                      if k in {"input_ids", "attention_mask"}}
            with torch.no_grad():
                feats = self._extract(self._model.get_text_features(**inputs))
                feats = feats / feats.norm(dim=-1, keepdim=True)
            chunks.append(feats.cpu().numpy().astype(np.float32))
        return np.concatenate(chunks, axis=0)

    def encode_texts_cached(self, texts: list[str], tag: str) -> np.ndarray:
        """Text embeddings, memoised on disk under a content hash.

        The key covers the model id and the exact prompt list, so editing a
        prompt invalidates the cache automatically.
        """
        digest = hashlib.sha256(
            ("\x00".join([self.model_id, *texts])).encode("utf-8")
        ).hexdigest()[:24]
        path = _CACHE_DIR / f"text_emb_{tag}_{digest}.npy"
        if path.exists():
            try:
                arr = np.load(path)
                if arr.shape[0] == len(texts):
                    return arr
            except Exception:
                path.unlink(missing_ok=True)
        arr = self.encode_texts(texts)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Remove stale embeddings for the same tag so the cache cannot grow
            # without bound as prompts are edited.
            for old in path.parent.glob(f"text_emb_{tag}_*.npy"):
                if old != path:
                    old.unlink(missing_ok=True)
            np.save(path, arr)
        except OSError:
            pass
        return arr

    @staticmethod
    def _extract(output: Any) -> Any:
        """get_*_features returns a tensor on 4.x and an object on 5.x."""
        if hasattr(output, "shape"):
            return output
        pooled = getattr(output, "pooler_output", None)
        if pooled is not None:
            return pooled
        if isinstance(output, (tuple, list)) and output:
            return output[0]
        raise TypeError(f"unexpected CLIP output: {type(output).__name__}")


def softmax(x: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    z = (x - np.max(x)) / max(temperature, 1e-6)
    e = np.exp(z)
    return e / max(float(e.sum()), 1e-12)
