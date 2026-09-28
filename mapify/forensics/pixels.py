"""What the pixels and the container say about the file's history.

Technique 5 of the specification is "resolution and pixel inspection".
Recording the width and height satisfies the words; it does not do the
job. What an analyst wants from this stage is whether the image in front
of them is a camera original, and if not, what was done to it -- because
a picture that has been cropped, upscaled, screenshotted or re-saved
three times has had its most useful evidence thinned out before anyone
looked at it.

Everything here is measured from the file. Where a measurement only
supports a weak inference, it is reported as a weak inference; several
of these signals are individually unremarkable and only mean something
together, and the report says which.

What this cannot do
-------------------

Decide that an image is fake. Error-level analysis and quantisation
forensics produce suggestive pictures and a great many false positives,
and the published record on them is much weaker than their popularity
suggests. This module reports observations and their ordinary innocent
explanations alongside them.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

__all__ = ["PixelReport", "inspect_pixels", "estimate_jpeg_quality"]


# The luminance quantisation table from the JPEG standard's Annex K, the
# base every quality setting is scaled from. Comparing a file's actual
# table against this recovers roughly what quality it was saved at.
_ANNEX_K_LUMA = (
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
)

# Aspect ratios worth naming, as (w/h, name). A 16:9 still from a phone
# camera is unusual enough to mention; 4:3 and 3:2 are what cameras do.
_RATIOS = (
    (1.0, "square"),
    (4 / 3, "4:3"),
    (3 / 2, "3:2"),
    (16 / 9, "16:9"),
    (16 / 10, "16:10"),
    (2 / 1, "2:1"),
    (21 / 9, "21:9"),
    (3 / 1, "panorama"),
)

# Screen resolutions common enough that an image at exactly one of them,
# with no camera metadata, is more likely a screenshot than a photograph.
_SCREEN_SIZES = frozenset({
    (1280, 720), (1280, 800), (1366, 768), (1440, 900), (1536, 864),
    (1600, 900), (1680, 1050), (1920, 1080), (1920, 1200), (2048, 1152),
    (2560, 1440), (2560, 1600), (2880, 1800), (3440, 1440), (3840, 2160),
    # Phone screens, in the orientation a screenshot is taken in.
    (750, 1334), (828, 1792), (1080, 1920), (1080, 2340), (1125, 2436),
    (1170, 2532), (1179, 2556), (1284, 2778), (1290, 2796), (1440, 3120),
})


@dataclass
class PixelReport:
    """Measurements of the file, and what they do and do not imply."""

    width: int = 0
    height: int = 0
    megapixels: float = 0.0
    aspect: float = 0.0
    aspect_name: str = ""
    orientation: str = ""              # landscape / portrait / square
    image_format: str | None = None
    mode: str | None = None
    bit_depth: int | None = None
    file_bytes: int = 0
    bits_per_pixel: float | None = None
    jpeg_quality: int | None = None
    chroma_subsampling: str | None = None
    has_alpha: bool = False
    is_animated: bool = False
    sharpness: float | None = None     # variance of the Laplacian
    # Plain-language observations, each safe to show a reader.
    notes: list[str] = field(default_factory=list)
    # Observations that bear on whether the file is a camera original.
    # Each is (what was seen, what it usually means).
    signals: list[tuple[str, str]] = field(default_factory=list)

    @property
    def likely_screenshot(self) -> bool:
        return any("screen resolution" in s for s, _ in self.signals)

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "megapixels": round(self.megapixels, 2),
            "aspect": round(self.aspect, 4),
            "aspect_name": self.aspect_name,
            "orientation": self.orientation,
            "format": self.image_format,
            "mode": self.mode,
            "bit_depth": self.bit_depth,
            "file_bytes": self.file_bytes,
            "bits_per_pixel": (round(self.bits_per_pixel, 3)
                               if self.bits_per_pixel is not None else None),
            "jpeg_quality": self.jpeg_quality,
            "chroma_subsampling": self.chroma_subsampling,
            "has_alpha": self.has_alpha,
            "is_animated": self.is_animated,
            "sharpness": (round(self.sharpness, 1)
                          if self.sharpness is not None else None),
            "notes": self.notes,
            "signals": [{"observed": a, "usually_means": b}
                        for a, b in self.signals],
        }


def estimate_jpeg_quality(table: list[int] | tuple[int, ...]) -> int | None:
    """Recover the approximate IJG quality setting from a luma table.

    The encoder scales Annex K by a factor derived from the quality
    number, so the ratio between the two recovers it. Tables that were
    not produced that way -- some phones and most professional cameras
    use their own -- give an answer that should be read as "comparable
    to quality N", which is what the caller reports.
    """
    if not table or len(table) < 64:
        return None
    # The scale factor the encoder used, averaged over the coefficients
    # where quantisation is coarse enough to measure reliably. The first
    # coefficient saturates at low quality and is skipped.
    ratios = []
    for i in range(1, 64):
        base = _ANNEX_K_LUMA[i]
        value = table[i]
        if value <= 0 or base <= 0:
            continue
        if value >= 255:        # saturated; carries no information
            continue
        ratios.append(value * 100.0 / base)
    if not ratios:
        return None
    scale = sorted(ratios)[len(ratios) // 2]      # median, not mean
    if scale <= 0:
        return None
    if scale < 100:
        quality = 100 - scale / 2.0
    else:
        quality = 5000.0 / scale
    return max(1, min(100, int(round(quality))))


def _name_aspect(aspect: float) -> str:
    if aspect <= 0:
        return ""
    # Compare in log space: 16:9 and 9:16 are the same shape turned over,
    # and the ratio is taken on the long side already.
    best, gap = "", math.inf
    for value, name in _RATIOS:
        d = abs(math.log(aspect / value))
        if d < gap:
            best, gap = name, d
    return best if gap < 0.02 else f"{aspect:.2f}:1"


def _sharpness(image: Image.Image) -> float | None:
    """Variance of the Laplacian, on a downscaled grey copy.

    A very low value on a large image is the signature of upscaling:
    the pixels are there but the detail is not. It is also the signature
    of an out-of-focus photograph, which is why this is reported as a
    number and only becomes a signal in combination with size.
    """
    try:
        from PIL import ImageFilter, ImageStat
    except ImportError:                                # pragma: no cover
        return None
    try:
        grey = image.convert("L")
        # Bound the work: sharpness of a 48 MP image does not need 48 MP.
        grey.thumbnail((1024, 1024), Image.Resampling.BILINEAR)
        edges = grey.filter(ImageFilter.FIND_EDGES)
        stat = ImageStat.Stat(edges)
        return float(stat.stddev[0] ** 2)
    except Exception:                                  # pragma: no cover
        return None


def inspect_pixels(image: Image.Image, raw_bytes: bytes = b"",
                   exif: Any = None) -> PixelReport:
    """Measure the file, and name what the measurements suggest.

    ``exif`` is optional; when given, disagreements between what the
    metadata claims and what the pixels show become available, and those
    are the most informative signals here.
    """
    report = PixelReport()
    report.width, report.height = image.size
    report.megapixels = (report.width * report.height) / 1e6
    long_side = max(report.width, report.height)
    short_side = max(1, min(report.width, report.height))
    report.aspect = long_side / short_side
    report.aspect_name = _name_aspect(report.aspect)
    report.orientation = ("square" if report.width == report.height
                          else "landscape" if report.width > report.height
                          else "portrait")
    report.image_format = getattr(image, "format", None)
    report.mode = image.mode
    report.has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        "transparency" in getattr(image, "info", {}))
    report.is_animated = bool(getattr(image, "n_frames", 1) > 1)
    depth = {"1": 1, "L": 8, "P": 8, "RGB": 24, "RGBA": 32,
             "CMYK": 32, "I;16": 16, "I": 32, "F": 32}.get(image.mode)
    report.bit_depth = depth
    report.file_bytes = len(raw_bytes)

    if raw_bytes and report.width and report.height:
        report.bits_per_pixel = (len(raw_bytes) * 8.0
                                 / (report.width * report.height))

    # ---------------------------------------------------------- JPEG
    quant = getattr(image, "quantization", None)
    if quant:
        luma = quant.get(0)
        if luma is not None:
            report.jpeg_quality = estimate_jpeg_quality(list(luma))
        sampling = getattr(image, "layers", None)
        if sampling:
            try:
                # (id, hsamp, vsamp, qtable) per component.
                h, v = sampling[0][1], sampling[0][2]
                report.chroma_subsampling = {
                    (1, 1): "4:4:4", (2, 1): "4:2:2", (2, 2): "4:2:0",
                }.get((h, v), f"{h}x{v}")
            except (IndexError, TypeError):            # pragma: no cover
                pass

    report.sharpness = _sharpness(image)

    # ------------------------------------------------------- the notes
    report.notes.append(
        f"{report.width} x {report.height} pixels "
        f"({report.megapixels:.1f} MP, {report.aspect_name}, "
        f"{report.orientation})"
    )
    if report.image_format:
        bits = report.image_format
        if report.jpeg_quality is not None:
            bits += f", saved at about quality {report.jpeg_quality}"
        if report.chroma_subsampling:
            bits += f", {report.chroma_subsampling} chroma"
        report.notes.append(bits)

    # ----------------------------------------------------- the signals
    _add_signals(report, image, exif)
    return report


def _add_signals(report: PixelReport, image: Image.Image, exif: Any) -> None:
    """Name the specific disagreements, with their innocent explanations."""

    size = (report.width, report.height)
    if size in _SCREEN_SIZES or (size[1], size[0]) in _SCREEN_SIZES:
        report.signals.append((
            f"{report.width}x{report.height} is exactly a common screen "
            f"resolution",
            "often a screenshot or a re-encode sized to a display, rather "
            "than a camera frame; some cameras and video stills match too",
        ))

    # EXIF claims a size the pixels do not have. Cropping and resizing
    # both do this, and both are ordinary -- but it means the frame is
    # not the one the camera produced, so anything inferred from framing
    # or field of view is unsafe.
    claimed = getattr(exif, "pixel_size", None) if exif is not None else None
    if claimed and tuple(claimed) not in (size, (size[1], size[0])):
        report.signals.append((
            f"metadata records {claimed[0]}x{claimed[1]} but the file holds "
            f"{report.width}x{report.height}",
            "the image was cropped or resized after capture; the frame is "
            "not the one the camera recorded",
        ))

    if report.jpeg_quality is not None:
        if report.jpeg_quality <= 70:
            report.signals.append((
                f"JPEG quality about {report.jpeg_quality}",
                "heavier compression than cameras use; typical of an image "
                "that has been through a messaging app or a web upload, "
                "which also strips metadata",
            ))
        elif report.jpeg_quality >= 98:
            report.signals.append((
                f"JPEG quality about {report.jpeg_quality}",
                "a re-save at near-maximum quality; editors do this, "
                "cameras rarely",
            ))

    # Very low detail for the pixel count. Upscaling leaves this, and so
    # does a soft or motion-blurred photograph, so it is offered as a
    # question rather than a finding.
    if (report.sharpness is not None and report.megapixels >= 2.0
            and report.sharpness < 12.0):
        report.signals.append((
            f"very little fine detail for a {report.megapixels:.0f} MP image",
            "consistent with upscaling from a smaller original, and equally "
            "with a soft, blurred or heavily denoised photograph",
        ))

    if (report.bits_per_pixel is not None and report.file_bytes > 0
            and report.bits_per_pixel < 0.35
            and (report.image_format or "").upper() in ("JPEG", "WEBP")):
        report.signals.append((
            f"{report.bits_per_pixel:.2f} bits per pixel on disk",
            "very aggressive compression; fine texture that scene analysis "
            "relies on will already be gone",
        ))

    if report.has_alpha and (report.image_format or "").upper() == "PNG":
        report.signals.append((
            "PNG with an alpha channel",
            "a composited or exported graphic rather than a camera file; "
            "screenshots and edited images arrive this way",
        ))

    software = getattr(exif, "software", None) if exif is not None else None
    if software:
        low = str(software).lower()
        if any(w in low for w in ("photoshop", "gimp", "lightroom",
                                  "affinity", "pixelmator", "snapseed",
                                  "picsart", "canva")):
            report.signals.append((
                f"the file names an editor: {software}",
                "the image was opened and re-saved in editing software; "
                "that is not evidence of manipulation by itself, but the "
                "pixels are no longer the camera's",
            ))

    if exif is not None and not getattr(exif, "has_exif", False):
        report.signals.append((
            "no metadata at all",
            "stripped by a messaging app, social platform or screenshot; "
            "common and usually innocent, but it removes the strongest "
            "location evidence a file can carry",
        ))
