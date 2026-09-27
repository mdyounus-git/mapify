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
import math
import logging
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL.Image import Image as PILImage

from .base import RawCandidate

log = logging.getLogger("mapify.geoclip")

from ..paths import cache_dir as _cache_dir

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

        This is the expensive half of GeoCLIP -- measured at 272s and 457s
        on CPU for the 100,000-point gallery, depending on how much memory
        the machine has free -- and the packaged ``predict()`` recomputes it
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
        path = _cache_dir() / f"geoclip_gallery_{key}.npy"

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
                "encoding the %d-point GPS gallery "
                "(one-off, 5-8 minutes on CPU, cached afterwards)",
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

    def locate_modes(self, image: PILImage, top_k: int = 12,
                     radius_km: float = 40.0, seeds: int = 300,
                     focus_km: float = 5.0
                     ) -> tuple[list[RawCandidate], np.ndarray | None]:
        """Find the places the distribution favours, not its loudest points.

        ``locate`` returns the twelve gallery points with the highest
        individual probability, which is not the same as the twelve most
        likely *places*: the gallery is not evenly dense. A city covered
        by fifty points at half a per cent each holds a quarter of the
        distribution and may contribute nothing to a point-wise top-12,
        while a sparsely sampled place with twelve points at 2% takes the
        entire list.

        Measured over 163 photographs the point-wise top-12 held a median
        of **14.5%** of the distribution. Everything downstream -- the
        evidence re-ranking, the clustering, the confidence banding --
        was reasoning about a seventh of what the locator knew. And the
        samples where most was discarded are the ones that go wrong:
        under 10% captured, the median error was 2,603 km; over 25%, it
        was 20 km.

        So the strongest points become seeds, the probability within
        ``radius_km`` of each is summed, and modes are taken greedily by
        that sum with everything inside the radius removed as each is
        taken. A mode's coordinate is its probability-weighted centre,
        which estimates one place better than any single gallery sample
        of it.

        Returns the same pair as ``locate_with_embedding`` -- candidates
        and the normalised CLIP embedding -- because the evidence
        extractor needs that embedding and the vision tower is most of
        the cost on CPU.
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
            image_features = encoder.mlp(clip_feats)
            image_features = torch.nn.functional.normalize(image_features, dim=1)
            logits = model.logit_scale.exp() * (
                image_features @ self.gallery_features().t()
            )
            probs = logits.softmax(dim=-1)[0].cpu().numpy().astype(np.float64)
            normed = clip_feats / clip_feats.norm(dim=-1, keepdim=True)
            embedding = normed[0].cpu().numpy().astype(np.float32)

        coords = self.gallery_coords()
        if coords is None:
            return [], embedding

        lat = np.radians(coords[:, 0])
        lon = np.radians(coords[:, 1])
        cos_lat = np.cos(lat)
        order = np.argsort(probs)[::-1][:max(seeds, top_k)]
        remaining = probs.copy()
        out: list[RawCandidate] = []

        for _ in range(top_k):
            best_sum, best_mask = 0.0, None
            for idx in order:
                if remaining[idx] <= 0:
                    continue
                sin_dlat = np.sin((lat - lat[idx]) / 2.0)
                sin_dlon = np.sin((lon - lon[idx]) / 2.0)
                a = sin_dlat ** 2 + cos_lat[idx] * cos_lat * sin_dlon ** 2
                dist = 2.0 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
                mask = dist <= radius_km
                total = float(remaining[mask].sum())
                if total > best_sum:
                    best_sum, best_mask = total, mask
            if best_mask is None or best_sum <= 0:
                break

            # Where to *report* the mode is a different question from how
            # wide to gather its mass. Gathering over 75 km finds the right
            # region; averaging over 75 km then throws away the precision
            # the locator had inside it -- measured, that cost 31 images
            # within a kilometre and returned 23.
            #
            # So the mass of the whole mode decides the ranking, and the
            # coordinate comes from the peak and its immediate
            # neighbourhood only. Local averaging still smooths the
            # gallery's sampling noise; it just stops averaging in a tail
            # fifty kilometres away.
            peak = int(np.argmax(np.where(best_mask, remaining, -1.0)))
            sin_dlat = np.sin((lat - lat[peak]) / 2.0)
            sin_dlon = np.sin((lon - lon[peak]) / 2.0)
            a = sin_dlat ** 2 + cos_lat[peak] * cos_lat * sin_dlon ** 2
            near = 2.0 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
            focus = best_mask & (near <= focus_km)
            if not focus.any():
                focus = best_mask

            weights = remaining[focus]
            pts = coords[focus]
            rlat = np.radians(pts[:, 0])
            rlon = np.radians(pts[:, 1])
            x = float((weights * np.cos(rlat) * np.cos(rlon)).sum())
            y = float((weights * np.cos(rlat) * np.sin(rlon)).sum())
            z = float((weights * np.sin(rlat)).sum())
            norm = math.sqrt(x * x + y * y + z * z)
            if norm <= 0:
                break
            x, y, z = x / norm, y / norm, z / norm
            out.append(
                RawCandidate(
                    latitude=math.degrees(math.atan2(z, math.hypot(x, y))),
                    longitude=math.degrees(math.atan2(y, x)),
                    score=best_sum,
                    source=self.name,
                )
            )
            remaining[best_mask] = 0.0

        return out, embedding

    def distribution(self, image: PILImage) -> np.ndarray | None:
        """This image's probability over the whole GPS gallery.

        ``locate`` computes exactly this and then throws all but the top
        twelve away, which is right for a single analysis and wrong for
        combining several. Two photographs of one place rarely propose
        the same twelve gallery points, so a shortlist cannot answer
        "what does image A think of the place image B suggested?" -- and
        without that answer, combining them degenerates into picking
        whichever image was more confident, whether or not it was right.

        100,000 float32 is 400 KB, which is why this is not kept by
        default. A session asks for it; a batch of five hundred images
        would not thank us for it.
        """
        if not self.load():
            return None
        torch = self._torch
        model = self._model
        try:
            pixel_values = model.image_encoder.preprocess_image(
                image.convert("RGB")
            ).to(self.device)
            with torch.no_grad():
                feats = model.image_encoder(pixel_values)
                feats = torch.nn.functional.normalize(feats, dim=1)
                logits = model.logit_scale.exp() * (
                    feats @ self.gallery_features().t()
                )
                probs = logits.softmax(dim=-1)[0]
            return probs.cpu().numpy().astype(np.float32)
        except Exception as exc:          # pragma: no cover - guard
            log.warning("gallery distribution unavailable: %s", exc)
            return None

    def gallery_coords(self) -> np.ndarray | None:
        """The gallery's coordinates, as a plain array, cached."""
        if getattr(self, "_gallery_coords", None) is not None:
            return self._gallery_coords
        if not self.load():
            return None
        self._gallery_coords = self._model.gps_gallery.cpu().numpy()
        return self._gallery_coords

    def mass_near(self, probs: np.ndarray, lat: float, lon: float,
                  radius_km: float) -> float:
        """How much probability this image put within reach of a point.

        Great-circle distance over the whole gallery, vectorised. The
        distribution is computed once per image and reused for every
        query, so this is one pass over 100,000 floats -- microseconds,
        not the matmul it looks like.
        """
        coords = self.gallery_coords()
        if coords is None or probs is None:
            return 0.0
        lat0 = np.radians(lat)
        lon0 = np.radians(lon)
        lats = np.radians(coords[:, 0])
        lons = np.radians(coords[:, 1])
        sin_dlat = np.sin((lats - lat0) / 2.0)
        sin_dlon = np.sin((lons - lon0) / 2.0)
        a = sin_dlat ** 2 + np.cos(lat0) * np.cos(lats) * sin_dlon ** 2
        dist = 2.0 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
        return float(probs[dist <= radius_km].sum())

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
