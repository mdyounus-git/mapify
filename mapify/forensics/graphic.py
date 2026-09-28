"""Is this a photograph, or something drawn on a computer?

A camera sees the world through a lens and a noisy sensor. Nothing in a
photograph is perfectly flat: even a clear sky varies by a few levels
from pixel to pixel, and the number of distinct colours is enormous. A
poster, a chart, a slide or a screenshot is the opposite -- large areas
filled with exactly one colour, drawn from a small palette, with hard
edges between them.

That difference is measurable without any model, which is what makes it
worth having beside one. The zero-shot image-kind reading can be fooled
by a photograph that looks designed, or a poster that contains a photo;
these statistics are fooled by different things -- a heavily compressed
JPEG flattens smooth sky, a photograph of a white wall is nearly one
colour -- so where both agree, the agreement means something.

Every number here is a measurement of the pixels. None of them is a
verdict; the locatability gate combines them with the model's reading
and the text coverage, and the thresholds it uses were fitted on images
that are and are not photographs of places.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

__all__ = ["GraphicStats", "graphic_stats", "text_coverage"]

# Work on a bounded copy: these are statistics of the image's character,
# not of its fine detail, and a 48 MP photograph does not need 48 MP.
_WORK_SIZE = 384
_BLOCK = 8


@dataclass
class GraphicStats:
    # Fraction of 8x8 blocks with essentially no variation at all. The
    # single strongest separator: sensor noise makes these rare in
    # photographs and flat fills make them common in graphics.
    flat_fraction: float = 0.0
    # The same, counting only mid-tone blocks. Pure black and pure white
    # are flat in photographs too -- a night sky, a blown-out window, the
    # black around a photograph of the Moon -- so they say nothing about
    # whether a person drew the image. A flat block of mid-grey-green does.
    flat_mid_fraction: float = 0.0
    # Fraction of pixels covered by the eight most common colours, after
    # reducing to 32 levels per channel. Graphics are drawn from small
    # palettes; photographs spread over thousands of colours.
    palette_share: float = 0.0
    # Distinct reduced colours per thousand pixels.
    colour_richness: float = 0.0
    # Fraction of pixels that are near-pure white. Documents, slides and
    # charts sit on white; photographs almost never contain large areas
    # of it.
    white_fraction: float = 0.0
    # Fraction of blocks with the moderate, everywhere-present variation
    # of natural texture -- foliage, masonry, asphalt, fabric.
    texture_fraction: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {k: round(float(v), 4) for k, v in self.__dict__.items()}


def graphic_stats(image: Image.Image) -> GraphicStats:
    """Measure how drawn, rather than photographed, the image looks."""
    rgb = image.convert("RGB")
    rgb.thumbnail((_WORK_SIZE, _WORK_SIZE), Image.Resampling.BILINEAR)
    a = np.asarray(rgb, dtype=np.float32)
    h, w, _ = a.shape
    stats = GraphicStats()
    if h < _BLOCK * 2 or w < _BLOCK * 2:
        return stats

    # ---- block variation --------------------------------------------
    bh, bw = h // _BLOCK, w // _BLOCK
    blocks = a[: bh * _BLOCK, : bw * _BLOCK].reshape(
        bh, _BLOCK, bw, _BLOCK, 3).transpose(0, 2, 1, 3, 4)
    # Worst channel's spread within each block.
    spread = blocks.reshape(bh, bw, -1, 3).std(axis=2).max(axis=2)
    n_blocks = spread.size
    flat = spread < 1.5
    stats.flat_fraction = float(flat.sum() / n_blocks)
    means = blocks.reshape(bh, bw, -1, 3).mean(axis=2).mean(axis=2)
    midtone = (means >= 10.0) & (means <= 245.0)
    stats.flat_mid_fraction = float((flat & midtone).sum() / n_blocks)
    stats.texture_fraction = float(((spread >= 6.0) & (spread < 40.0)).sum()
                                   / n_blocks)

    # ---- palette ------------------------------------------------------
    q = (a.astype(np.uint8) >> 3).astype(np.int32)     # 32 levels a channel
    codes = (q[..., 0] << 10) | (q[..., 1] << 5) | q[..., 2]
    _, counts = np.unique(codes, return_counts=True)
    total = codes.size
    top = np.sort(counts)[::-1][:8]
    stats.palette_share = float(top.sum() / total)
    stats.colour_richness = float(len(counts) * 1000.0 / total)

    # ---- white ---------------------------------------------------------
    stats.white_fraction = float((a.min(axis=2) >= 245).sum() / (h * w))
    return stats


def text_coverage(ocr: Any) -> float:
    """Fraction of the frame covered by recognised text boxes.

    Summed box area, which double-counts overlaps; capped at one. On a
    street photograph this is a few percent even with signs in view. On
    a slide, a document or a poster it is a large part of the frame.
    """
    lines = getattr(ocr, "lines", None) or []
    area = 0.0
    for line in lines:
        box = getattr(line, "box", None)
        if not box:
            continue
        x0, y0, x1, y1 = box
        area += max(0.0, abs(x1 - x0)) * max(0.0, abs(y1 - y0))
    return float(min(1.0, area))
