"""Vanishing points, the horizon, and how the camera was held.

Man-made scenes are full of parallel lines -- building edges, kerbs,
window rows -- and parallel lines in the world meet at a vanishing point
in the image. From those points:

  * the vertical vanishing point says how far the camera was tilted up
    or down (verticals converge above the frame when it looks up);
  * the slant of the verticals says how far it was rolled;
  * the horizontal vanishing points lie on the horizon.

This matters to the rest of the pipeline in one concrete way: measuring
the sun's elevation from its position in the frame assumes a level
camera, and this is what can say whether the camera was level.

Line segments come from OpenCV's LSD detector; vanishing points from a
RANSAC over segment pairs, scored by the total length of segments that
point at the candidate. Scenes without enough straight lines -- forests,
open country, close-ups -- return nothing rather than a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

__all__ = ["CameraGeometry", "analyse_geometry"]


@dataclass
class CameraGeometry:
    segments: int = 0
    vertical_segments: int = 0
    roll_deg: float | None = None           # + = clockwise
    pitch_deg: float | None = None          # + = looking up
    vertical_vp: tuple[float, float] | None = None     # normalised; may be far off-frame
    horizontal_vps: list[tuple[float, float]] = field(default_factory=list)
    horizon_y: float | None = None          # normalised, at the frame centre
    focal_px: float | None = None
    focal_assumed: bool = True
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        r = (lambda v: round(v, 3) if isinstance(v, float) else v)
        return {"segments": self.segments,
                "vertical_segments": self.vertical_segments,
                "roll_deg": r(self.roll_deg), "pitch_deg": r(self.pitch_deg),
                "vertical_vp": ([round(v, 3) for v in self.vertical_vp]
                                if self.vertical_vp else None),
                "horizontal_vps": [[round(v, 3) for v in p]
                                   for p in self.horizontal_vps],
                "horizon_y": r(self.horizon_y),
                "focal_assumed": self.focal_assumed, "notes": self.notes}


def _segments(img: Image.Image, max_side: int = 1000) -> tuple[np.ndarray, int, int]:
    import cv2
    grey = img.convert("L")
    grey.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
    a = np.asarray(grey, dtype=np.uint8)
    h, w = a.shape
    lsd = cv2.createLineSegmentDetector(0)
    lines = lsd.detect(a)[0]
    if lines is None:
        return np.zeros((0, 4), np.float32), w, h
    segs = lines.reshape(-1, 4)
    length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    keep = length >= 0.03 * math.hypot(w, h)
    return segs[keep], w, h


def _line(seg: np.ndarray) -> np.ndarray:
    """Homogeneous line through a segment's endpoints."""
    p = np.array([seg[0], seg[1], 1.0])
    q = np.array([seg[2], seg[3], 1.0])
    return np.cross(p, q)


def _consistent(segs: np.ndarray, vp: np.ndarray, tol_deg: float) -> np.ndarray:
    """Which segments point at a (homogeneous) vanishing point."""
    mid = np.stack([(segs[:, 0] + segs[:, 2]) / 2, (segs[:, 1] + segs[:, 3]) / 2], 1)
    d_seg = np.stack([segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1]], 1)
    if abs(vp[2]) < 1e-9:                      # at infinity: a direction
        d_vp = np.tile(vp[:2], (len(segs), 1))
    else:
        d_vp = vp[:2] / vp[2] - mid
    cos = np.abs(np.sum(d_seg * d_vp, 1)) / (
        np.linalg.norm(d_seg, axis=1) * np.linalg.norm(d_vp, axis=1) + 1e-9)
    return cos >= math.cos(math.radians(tol_deg))


def _ransac(segs: np.ndarray, iters: int = 400, tol: float = 2.0,
            rng: np.random.Generator | None = None) -> tuple[np.ndarray | None, np.ndarray]:
    if len(segs) < 3:
        return None, np.zeros(len(segs), bool)
    rng = rng or np.random.default_rng(0)
    lengths = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    probs = lengths / lengths.sum()
    best_vp, best_score, best_in = None, 0.0, np.zeros(len(segs), bool)
    for _ in range(iters):
        i, j = rng.choice(len(segs), 2, replace=False, p=probs)
        vp = np.cross(_line(segs[i]), _line(segs[j]))
        if not np.any(vp):
            continue
        n = np.linalg.norm(vp)
        vp = vp / n
        inl = _consistent(segs, vp, tol)
        score = lengths[inl].sum()
        if score > best_score:
            best_vp, best_score, best_in = vp, score, inl
    return best_vp, best_in


def analyse_geometry(img: Image.Image, fov_deg: tuple[float, float] | None = None
                     ) -> CameraGeometry:
    out = CameraGeometry()
    try:
        segs, w, h = _segments(img)
    except Exception as exc:                        # pragma: no cover
        out.notes.append(f"line detection failed ({type(exc).__name__})")
        return out
    out.segments = int(len(segs))
    if len(segs) < 12:
        out.notes.append("too few straight lines to find vanishing points")
        return out

    if fov_deg:
        out.focal_px = (w / 2) / math.tan(math.radians(fov_deg[0]) / 2)
        out.focal_assumed = False
    else:
        # About a phone's main camera (26 mm equivalent), stated as such.
        out.focal_px = 0.85 * max(w, h)
        out.notes.append("focal length not recorded: a typical phone lens "
                         "was assumed, which limits the pitch estimate")

    ang = np.degrees(np.arctan2(segs[:, 3] - segs[:, 1], segs[:, 2] - segs[:, 0]))
    ang = (ang + 180) % 180                          # 0..180, 90 = vertical
    vert = np.abs(ang - 90) <= 20
    out.vertical_segments = int(vert.sum())
    lengths = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])

    rng = np.random.default_rng(7)
    cx, cy = w / 2, h / 2
    if vert.sum() >= 5:
        # Roll: how far the verticals lean, length-weighted median.
        lean = ang[vert] - 90
        order = np.argsort(lean)
        cum = np.cumsum(lengths[vert][order])
        out.roll_deg = float(lean[order][np.searchsorted(cum, cum[-1] / 2)])
        vp, inl = _ransac(segs[vert], rng=rng)
        if vp is not None and inl.sum() >= 4:
            if abs(vp[2]) > 1e-9:
                vx, vy = vp[0] / vp[2], vp[1] / vp[2]
                out.vertical_vp = (float(vx / w), float(vy / h))
                # The world's vertical images to a point f*cot(pitch) above
                # the centre: far away when the camera is level, close in
                # when it looks steeply up (or below, looking down).
                d = cy - vy
                if abs(d) < 1e-6:
                    out.pitch_deg = None
                else:
                    out.pitch_deg = float(math.degrees(math.atan(out.focal_px / d)))
            else:
                out.pitch_deg = 0.0                 # verticals truly parallel

    rest = segs[~vert & (np.abs(ang - 90) > 30)]
    for _ in range(2):
        vp, inl = _ransac(rest, rng=rng)
        if vp is None or inl.sum() < 4:
            break
        if abs(vp[2]) > 1e-9:
            out.horizontal_vps.append((float(vp[0] / vp[2] / w), float(vp[1] / vp[2] / h)))
        rest = rest[~inl]
    finite = [p for p in out.horizontal_vps if -3 < p[1] < 4]
    if len(finite) == 2:
        (x1, y1), (x2, y2) = finite
        if abs(x2 - x1) > 1e-6:
            t = (0.5 - x1) / (x2 - x1)
            out.horizon_y = float(y1 + t * (y2 - y1))
    elif out.pitch_deg is not None:
        out.horizon_y = float(0.5 + math.tan(math.radians(out.pitch_deg))
                              * out.focal_px / h)
    return out
