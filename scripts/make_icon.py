#!/usr/bin/env python3
"""Draw the application icon.

Generated rather than checked in as a binary, so the shape is reviewable and
reproducible. Produces a multi-resolution .ico for the window, the taskbar and
the built executable.

    python scripts/make_icon.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "mapify.ico"
PNG = ROOT / "assets" / "mapify.png"

SIZES = (16, 24, 32, 48, 64, 128, 256)

BG_OUTER = (5, 7, 13, 255)
OCEAN_TOP = (18, 58, 94)
OCEAN_BOTTOM = (4, 16, 31)
GRID = (56, 189, 248)
LIMB = (125, 211, 252, 255)
PIN = (56, 189, 248, 255)


def draw(size: int):
    from PIL import Image, ImageDraw

    # Supersample, then downscale: the only cheap way to get clean curves at
    # 16 px.
    ss = 8
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = s * 0.055
    box = (pad, pad, s - pad, s - pad)
    cx = cy = s / 2
    r = (s - 2 * pad) / 2

    # Rounded dark plate so the mark reads on any taskbar colour.
    d.rounded_rectangle((0, 0, s, s), radius=s * 0.22, fill=BG_OUTER)

    # Ocean disc with a vertical gradient.
    for i in range(int(r * 2)):
        t = i / max(1, r * 2 - 1)
        col = tuple(
            int(OCEAN_TOP[c] + (OCEAN_BOTTOM[c] - OCEAN_TOP[c]) * t) for c in range(3)
        )
        y = pad + i
        half = math.sqrt(max(0.0, r * r - (y - cy) ** 2))
        d.line((cx - half, y, cx + half, y), fill=col + (255,))

    # Graticule: three parallels and three meridians, as ellipses.
    lw = max(1, int(s * 0.016))
    for frac in (-0.55, 0.0, 0.55):
        yy = cy + r * frac
        half = math.sqrt(max(0.0, r * r - (yy - cy) ** 2))
        ry = max(lw, r * 0.13 * (1 - abs(frac)))
        d.ellipse((cx - half, yy - ry, cx + half, yy + ry),
                  outline=GRID + (150,), width=lw)
    for frac in (0.34, 0.72, 1.0):
        rx = r * frac
        d.ellipse((cx - rx, cy - r, cx + rx, cy + r),
                  outline=GRID + (150,), width=lw)

    # Limb.
    d.ellipse(box, outline=LIMB, width=max(1, int(s * 0.026)))

    # The reconnaissance pin: a dot on the globe with a standoff mark.
    px = cx + r * 0.36
    py = cy - r * 0.34
    rad = r * 0.15
    d.ellipse((px - rad, py - rad, px + rad, py + rad), fill=PIN)
    d.ellipse((px - rad * 2.1, py - rad * 2.1, px + rad * 2.1, py + rad * 2.1),
              outline=PIN, width=max(1, int(s * 0.018)))

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("Pillow is required: pip install pillow", file=sys.stderr)
        return 2

    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [draw(n) for n in SIZES]
    frames[-1].save(PNG)
    frames[-1].save(OUT, format="ICO",
                    sizes=[(n, n) for n in SIZES])
    print(f"wrote {OUT} ({OUT.stat().st_size:,} B) and {PNG.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
