"""360-degree panoramas: recognised, and seen as the views a camera would take.

A phone's or a 360 camera's panorama is one equirectangular image covering
every direction. The locator sees a centred square of whatever it is given,
so of a 2:1 panorama it saw the middle half, squashed -- and of the scene, a
direction or two out of four. Here the panorama is cut into four ordinary
90-degree views (ahead, right, behind, left), which the locator reads one by
one and whose opinions are averaged: four views of one moment are one
observation, not four photographs, so they are never multiplied together as
separate photographs of a place are.

Recognised conservatively: the file says so (the GPano XMP tag 360 cameras
and phone apps write), or it is exactly 2:1 and at least 4096 px wide, the
shape of a full sphere. A wide crop that is merely panoramic stays one image.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

__all__ = ["is_equirectangular", "views", "VIEW_YAWS"]

VIEW_YAWS = (0.0, 90.0, 180.0, 270.0)      # degrees, clockwise from the image centre
VIEW_FOV = 90.0
VIEW_SIDE = 448
_MIN_SPHERE_WIDTH = 4096


def is_equirectangular(image: Image.Image) -> bool:
    """True for a full 360-degree equirectangular panorama."""
    w, h = image.size
    if h <= 0 or abs(w / h - 2.0) > 0.05:
        return False
    xmp = image.info.get("xmp") or image.info.get("XML:com.adobe.xmp") or b""
    if isinstance(xmp, bytes):
        xmp = xmp.decode("utf-8", "ignore")
    if "ProjectionType" in xmp and "equirectangular" in xmp.lower():
        return True
    return abs(w / h - 2.0) <= 0.01 and w >= _MIN_SPHERE_WIDTH


def views(image: Image.Image, yaws: tuple[float, ...] = VIEW_YAWS,
          fov: float = VIEW_FOV, side: int = VIEW_SIDE) -> list[Image.Image]:
    """Ordinary perspective views cut from an equirectangular panorama, level
    with the horizon, one per yaw (0 = the middle of the panorama)."""
    src = np.asarray(image.convert("RGB"))
    sh, sw = src.shape[:2]
    t = math.tan(math.radians(fov) / 2.0)
    grid = (np.arange(side) + 0.5) / side * 2.0 - 1.0
    x, y = np.meshgrid(grid * t, grid * t)          # right, down, on the image plane
    z = np.ones_like(x)
    norm = np.sqrt(x * x + y * y + z * z)
    x, y, z = x / norm, y / norm, z / norm
    out = []
    for yaw in yaws:
        a = math.radians(yaw)
        dx = x * math.cos(a) + z * math.sin(a)
        dz = -x * math.sin(a) + z * math.cos(a)
        lon = np.arctan2(dx, dz)                     # 0 at the panorama's centre
        lat = np.arcsin(np.clip(-y, -1.0, 1.0))
        px = ((lon / (2 * math.pi) + 0.5) * sw).astype(np.int64) % sw
        py = np.clip(((0.5 - lat / math.pi) * sh).astype(np.int64), 0, sh - 1)
        out.append(Image.fromarray(src[py, px], "RGB"))
    return out
