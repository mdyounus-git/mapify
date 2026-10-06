"""Local features and geometric verification.

SIFT keypoints (patent-free since 2020, in OpenCV's main module) on a
greyscale copy, matched with Lowe's ratio test, then verified two ways:
a fundamental matrix (any rigid 3D scene seen from two positions) and a
homography (a planar surface -- a facade, a sign). Only the
fundamental-matrix inliers decide; the homography count is reported as
corroboration because a facade seen twice gives both.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

__all__ = ["Features", "MatchResult", "extract", "match", "save", "load", "windows",
           "QUERY_SIDE", "QUERY_PIXELS", "REFERENCE_SIDE", "REFINE_SIDE"]

QUERY_SIDE = 2400          # the query's longest side at most...
QUERY_PIXELS = 2_000_000   # ...and about two megapixels: sized by area, so a
                           # wide panorama keeps its height (at 1024 px wide a
                           # 5:1 panorama was a 200 px strip, its landmark a few
                           # dozen pixels tall)
REFERENCE_SIDE = 640       # first pass: references as 640 px thumbnails
REFINE_SIDE = 960          # second pass, for the best few: the same
                           # thumbnail at its full served width
_MAX_FEATURES = 3000
_RATIO = 0.75              # Lowe's ratio test
_F_THRESHOLD_PX = 1.5      # MAGSAC threshold for the fundamental matrix
_H_THRESHOLD_PX = 4.0


@dataclass
class Features:
    points: np.ndarray       # (N, 2) float32, in pixels of the working copy
    descriptors: np.ndarray  # (N, 128) float32
    size: tuple[int, int]    # working copy (w, h)


@dataclass
class MatchResult:
    good: int                # matches surviving the ratio test
    inliers: int             # consistent with one fundamental matrix
    homography_inliers: int  # consistent with one plane
    inlier_ratio: float      # inliers / good

    def as_dict(self) -> dict:
        return {"good": self.good, "inliers": self.inliers,
                "homography_inliers": self.homography_inliers,
                "inlier_ratio": round(self.inlier_ratio, 3)}


def _grey(image: Image.Image, side: int, max_pixels: int | None = None) -> np.ndarray:
    im = ImageOps.exif_transpose(image) or image
    im = im.convert("L")
    w, h = im.size
    scale = min(1.0, side / max(w, h))
    if max_pixels:
        scale = min(scale, (max_pixels / float(w * h)) ** 0.5)
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                       Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=np.uint8)


def extract(image: Image.Image, side: int = QUERY_SIDE,
            max_pixels: int | None = None, nfeatures: int = _MAX_FEATURES) -> Features:
    import cv2
    grey = _grey(image, side, max_pixels)
    sift = cv2.SIFT_create(nfeatures=nfeatures)
    kps, des = sift.detectAndCompute(grey, None)
    if des is None or not kps:
        return Features(np.zeros((0, 2), np.float32), np.zeros((0, 128), np.float32),
                        (grey.shape[1], grey.shape[0]))
    pts = np.array([k.pt for k in kps], dtype=np.float32)
    return Features(pts, des.astype(np.float32), (grey.shape[1], grey.shape[0]))


def match(query: Features, ref: Features) -> MatchResult:
    """How many feature matches one pair of camera positions explains."""
    import cv2
    if len(query.descriptors) < 8 or len(ref.descriptors) < 8:
        return MatchResult(0, 0, 0, 0.0)
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(query.descriptors, ref.descriptors, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < _RATIO * p[1].distance]
    # One query point matched by many reference points is a repeating
    # texture, not a correspondence: keep each reference point once.
    seen, unique = set(), []
    for m in sorted(good, key=lambda m: m.distance):
        if m.trainIdx not in seen:
            seen.add(m.trainIdx)
            unique.append(m)
    if len(unique) < 12:
        return MatchResult(len(unique), 0, 0, 0.0)
    p1 = query.points[[m.queryIdx for m in unique]]
    p2 = ref.points[[m.trainIdx for m in unique]]
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
    return MatchResult(len(unique), inliers, h_inliers, inliers / max(1, len(unique)))


PANORAMA_ASPECT = 2.2      # wider than this is a stitched panorama
_WINDOW_ASPECT = 1.6


def windows(features: Features) -> list[Features]:
    """The query as one view -- or, for a panorama, overlapping windows.

    A stitched panorama is not one camera: its cylindrical projection
    breaks the two-view geometry the verification fits, so even the right
    building agrees on only a few points. A window about as wide as an
    ordinary photograph is close to one camera again. Cut from the points
    already found, so it costs matches, not extraction.
    """
    w, h = features.size
    if w / max(1, h) <= PANORAMA_ASPECT:
        return [features]
    width = _WINDOW_ASPECT * h
    starts = list(np.arange(0.0, max(1.0, w - width), width / 2.0)) + [max(0.0, w - width)]
    out = []
    for x0 in starts:
        sel = (features.points[:, 0] >= x0) & (features.points[:, 0] < x0 + width)
        if sel.sum() >= 20:
            out.append(Features(features.points[sel], features.descriptors[sel],
                                (int(width), h)))
    return out or [features]


def save(features: Features, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    d = features.descriptors
    # OpenCV's SIFT descriptors are whole numbers from 0 to 255 held in
    # float32: as bytes they are stored exactly, in a quarter of the space.
    if d.size and np.array_equal(d, np.round(d)) and d.min() >= 0 and d.max() <= 255:
        d = d.astype(np.uint8)
    else:
        d = d.astype(np.float16)
    np.savez_compressed(tmp, points=features.points, descriptors=d,
                        size=np.array(features.size))
    tmp.replace(path)


def load(path: Path) -> Features | None:
    try:
        with np.load(path) as z:
            return Features(z["points"].astype(np.float32),
                            z["descriptors"].astype(np.float32),
                            tuple(int(v) for v in z["size"]))
    except (OSError, ValueError, KeyError):
        return None
