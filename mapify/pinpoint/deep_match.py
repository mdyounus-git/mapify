"""A second matcher for Pinpoint's near misses: DISK features with LightGlue.

SIFT confirms a reference at 40 inliers and never sees fewer than 14 from an
unrelated pair; between the two lie the near misses -- often the same place
under different light or from a different side, where hand-made features
fail and learned ones hold. DISK (Apache-2.0) finds the features and
LightGlue (Apache-2.0) matches them, through kornia (Apache-2.0); SuperPoint
is never used (its licence is restrictive). The weights download once into
TORCH_HOME, the models directory.

The result has the same shape as SIFT's -- matches, then the inliers one
fundamental matrix explains -- but its thresholds are its own, measured on
the same unrelated pairs (scripts/calibrate_pinpoint.py --matcher lightglue):
40 and 20 were fitted to SIFT and mean nothing here.
"""

from __future__ import annotations

import threading

import numpy as np
from PIL import Image

from .features import _F_THRESHOLD_PX, _H_THRESHOLD_PX, MatchResult, _grey

__all__ = ["DeepMatcher"]

SIDE = 640
MAX_KEYPOINTS = 2048


class DeepMatcher:
    def __init__(self, side: int = SIDE, max_keypoints: int = MAX_KEYPOINTS):
        self.side = side
        self.max_keypoints = max_keypoints
        self._disk = self._glue = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            import kornia  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        if self._disk is None:
            with self._lock:
                if self._disk is None:
                    import kornia.feature as KF
                    self._disk = KF.DISK.from_pretrained("depth").eval()
                    self._glue = KF.LightGlue("disk").eval()
        return self._disk, self._glue

    def features(self, image: Image.Image):
        """DISK keypoints and descriptors, reusable across matches."""
        import torch
        grey = _grey(image, self.side)
        rgb = np.repeat(grey[..., None], 3, axis=2).astype(np.float32) / 255.0
        tensor = torch.from_numpy(rgb).permute(2, 0, 1)[None]
        disk, _ = self._load()
        with torch.no_grad():
            feats = disk(tensor, self.max_keypoints, pad_if_not_divisible=True)[0]
        size = torch.tensor([[grey.shape[1], grey.shape[0]]], dtype=torch.float32)
        return {"keypoints": feats.keypoints[None], "descriptors": feats.descriptors[None],
                "image_size": size}

    def match(self, query: Image.Image, reference: Image.Image) -> MatchResult:
        return self.match_features(self.features(query), self.features(reference))

    def match_features(self, a, b) -> MatchResult:
        import cv2
        import torch
        _, glue = self._load()
        with torch.no_grad():
            out = glue({"image0": a, "image1": b})
        pairs = out["matches"][0].cpu().numpy()
        if len(pairs) < 12:
            return MatchResult(len(pairs), 0, 0, 0.0)
        p1 = a["keypoints"][0].cpu().numpy()[pairs[:, 0]].astype(np.float32)
        p2 = b["keypoints"][0].cpu().numpy()[pairs[:, 1]].astype(np.float32)
        method = getattr(cv2, "USAC_MAGSAC", cv2.FM_RANSAC)
        try:
            _f, mask = cv2.findFundamentalMat(p1, p2, method, _F_THRESHOLD_PX, 0.999, 10000)
            inliers = int(mask.sum()) if mask is not None else 0
        except cv2.error:
            inliers = 0
        try:
            _h, hmask = cv2.findHomography(p1, p2, method, _H_THRESHOLD_PX)
            h_inliers = int(hmask.sum()) if hmask is not None else 0
        except cv2.error:
            h_inliers = 0
        return MatchResult(len(pairs), inliers, h_inliers, inliers / max(1, len(pairs)))
