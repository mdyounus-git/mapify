"""Find the sun's disc in a photograph, and what its position implies.

The rare case where a photograph measures the sun directly. If the disc
is in frame, its height above the image centre and the lens's field of
view give its elevation; with the compass direction the camera faced
(``GPSImgDirection``), its horizontal position gives its azimuth. Both
can then be checked against where astronomy says the sun was.

Assumptions, stated in every result: the camera was held roughly level
(the horizon through the image centre) and the lens has no strong
distortion. A tilted phone moves the horizon; the error bar says so.

Detection is deliberately strict -- a compact, round, saturated blob in
the upper part of the frame, not too big -- because a false sun is worse
than none: a white sign, a window reflection or a street lamp would
otherwise "measure" a solar elevation that means nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

__all__ = ["SunDisc", "find_sun", "field_of_view"]


@dataclass
class SunDisc:
    x: float                  # normalised 0..1 across the frame
    y: float                  # normalised 0..1 down the frame
    radius: float             # normalised to the frame width
    circularity: float

    def as_dict(self) -> dict[str, Any]:
        return {"x": round(self.x, 4), "y": round(self.y, 4),
                "radius": round(self.radius, 4),
                "circularity": round(self.circularity, 3)}


def field_of_view(exif_other: dict, width: int, height: int
                  ) -> tuple[float, float] | None:
    """(horizontal, vertical) field of view in degrees, from the lens data.

    ``FocalLengthIn35mmFilm`` is the one that works without knowing the
    sensor size; the raw focal length alone does not. Returns None rather
    than assume a lens.
    """
    f35 = exif_other.get("FocalLengthIn35mmFilm")
    try:
        f35 = float(f35)
    except (TypeError, ValueError):
        return None
    if not 8.0 <= f35 <= 2000.0:
        return None
    # The 35 mm frame is 36 x 24 mm; the image's long side takes the 36.
    long_fov = 2 * math.degrees(math.atan(36.0 / (2 * f35)))
    aspect = max(width, height) / max(1, min(width, height))
    short_fov = 2 * math.degrees(math.atan(math.tan(math.radians(long_fov) / 2)
                                           / aspect))
    return (long_fov, short_fov) if width >= height else (short_fov, long_fov)


def find_sun(image: Image.Image) -> SunDisc | None:
    """The sun's disc, if the frame holds one clearly; otherwise None."""
    grey = image.convert("L")
    grey.thumbnail((640, 640), Image.Resampling.BILINEAR)
    a = np.asarray(grey, dtype=np.uint8)
    h, w = a.shape
    if h < 32 or w < 32:
        return None
    hot = a >= 250
    frac = hot.mean()
    # A frame mostly blown out (an overexposed sky, a white wall) has no
    # measurable disc in it.
    if frac == 0 or frac > 0.08:
        return None
    try:
        from scipy import ndimage
    except ImportError:                              # pragma: no cover
        return None
    labels, n = ndimage.label(hot)
    if n == 0 or n > 400:
        return None
    best = None
    for idx, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        ys, xs = sl
        bh, bw = ys.stop - ys.start, xs.stop - xs.start
        area = int((labels[sl] == idx).sum())
        if area < 12:
            continue
        rel = area / (w * h)
        if rel > 0.02:
            continue                                  # too large for a disc
        r = max(bh, bw) / 2.0
        circ = area / (math.pi * r * r) if r > 0 else 0.0
        aspect = max(bh, bw) / max(1, min(bh, bw))
        cy = (ys.start + ys.stop) / 2.0 / h
        cx = (xs.start + xs.stop) / 2.0 / w
        if circ < 0.55 or aspect > 1.6 or cy > 0.75:
            continue
        # A real sun is surrounded by glare: the ring just outside it is
        # bright too. A lamp or a sign against a dark background is not.
        pad = int(max(3, r))
        y0, y1 = max(0, ys.start - pad), min(h, ys.stop + pad)
        x0, x1 = max(0, xs.start - pad), min(w, xs.stop + pad)
        ring = a[y0:y1, x0:x1].astype(np.float32)
        glow = float(ring[ring < 250].mean()) if (ring < 250).any() else 255.0
        if glow < 150:
            continue
        score = circ * min(1.0, glow / 220.0) * (1.0 - cy)
        if best is None or score > best[0]:
            best = (score, SunDisc(x=cx, y=cy, radius=r / w, circularity=circ))
    return best[1] if best else None


def measured_angles(disc: SunDisc, fov: tuple[float, float],
                    heading: float | None) -> dict[str, Any]:
    """Elevation (and azimuth, with a heading) implied by the disc's place."""
    hfov, vfov = fov
    # Pinhole projection about a level optical axis.
    dx = (disc.x - 0.5) * 2 * math.tan(math.radians(hfov) / 2)
    dy = (0.5 - disc.y) * 2 * math.tan(math.radians(vfov) / 2)
    elev = math.degrees(math.atan2(dy, math.sqrt(1 + dx * dx)))
    horiz = math.degrees(math.atan(dx))
    out = {"elevation": round(elev, 1), "horizontal_offset": round(horiz, 1),
           # A handheld camera is rarely within a few degrees of level.
           "elevation_uncertainty": 6.0,
           "assumption": "camera held level, horizon through the frame centre"}
    if heading is not None:
        out["azimuth"] = round((heading + horiz) % 360.0, 1)
        out["azimuth_uncertainty"] = 8.0
    return out
