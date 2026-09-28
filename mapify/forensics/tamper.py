"""Signs that a file has been edited: re-saves, splices, resampling.

Five classical measurements, each with a known and published way of
being wrong. They are reported as signals with their innocent
explanations attached, and never combined into a verdict: the research
record on "is this image manipulated?" detectors is much weaker than
their popularity, and a false "this is genuine" is the most damaging
thing a forensic report can say.

  error level    re-save at a known quality and look at what changes.
                 Regions that change much more or less than the rest can
                 be pasted in -- or can simply be edges, text or sky.
  noise          every camera sensor leaves a fairly uniform noise floor.
                 A region whose noise differs sharply from the rest of
                 the frame may come from another image.
  JPEG ghost     re-compress at a range of qualities; the error dips at
                 the quality the file was last saved at, and a second dip
                 at a lower quality means it was saved at least twice.
  resampling     interpolation leaves periodic correlations between
                 neighbouring pixels; scaling or rotating an image leaves
                 a periodic peak in their spectrum.
  quantisation   the table a JPEG was compressed with names the encoder
                 family: libjpeg's standard table scaled to a quality, or
                 a camera maker's own.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

__all__ = ["error_levels", "noise_consistency", "jpeg_ghost", "resampling",
           "quant_signature", "Signal"]

_WORK = 1024


@dataclass
class Signal:
    name: str
    flagged: bool
    summary: str
    innocent: str                      # the ordinary explanation
    detail: dict[str, Any] = field(default_factory=dict)
    regions: list[tuple[float, float, float, float]] = field(default_factory=list)


def _rgb(img: Image.Image) -> Image.Image:
    im = img.convert("RGB")
    im.thumbnail((_WORK, _WORK), Image.Resampling.BILINEAR)
    return im


def _components(mask: np.ndarray, min_frac: float) -> list[tuple[float, float, float, float]]:
    """Normalised boxes of connected regions covering at least min_frac."""
    from scipy import ndimage
    labels, n = ndimage.label(mask)
    h, w = mask.shape
    out = []
    for idx, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        area = (labels[sl] == idx).sum() / (w * h)
        if area >= min_frac:
            ys, xs = sl
            out.append((xs.start / w, ys.start / h, xs.stop / w, ys.stop / h))
    return out


# ------------------------------------------------------------ error level

def error_levels(img: Image.Image, quality: int = 90) -> Signal:
    im = _rgb(img)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    re = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    a = np.asarray(im, dtype=np.int16)
    b = np.asarray(re, dtype=np.int16)
    ela = np.abs(a - b).max(axis=2).astype(np.float32)
    # Normalise by local detail: edges and text re-compress badly in any
    # honest photograph, and without this they are all that stands out.
    grey = np.asarray(im.convert("L"), dtype=np.float32)
    gx = np.abs(np.diff(grey, axis=1, append=grey[:, -1:]))
    gy = np.abs(np.diff(grey, axis=0, append=grey[-1:, :]))
    detail = gx + gy
    bs = 16
    h, w = ela.shape
    hb, wb = h // bs, w // bs
    if hb < 4 or wb < 4:
        return Signal("error_level", False, "Too small to measure.", "")
    e = ela[:hb * bs, :wb * bs].reshape(hb, bs, wb, bs).mean(axis=(1, 3))
    d = detail[:hb * bs, :wb * bs].reshape(hb, bs, wb, bs).mean(axis=(1, 3))
    ratio = e / (d + 4.0)
    med = float(np.median(ratio))
    mad = float(np.median(np.abs(ratio - med))) + 1e-6
    z = (ratio - med) / (1.4826 * mad)
    hot = z > 6.0
    boxes = _components(hot, 0.01)
    flagged = bool(boxes)
    return Signal(
        "error_level", flagged,
        (f"{len(boxes)} region(s) re-compress quite unlike the rest of the "
         f"frame" if flagged else
         "Error levels are even across the frame."),
        "strong error-level differences also come from saturated colour, "
        "smooth gradients and areas the camera itself processed differently; "
        "this is a pointer for a closer look, not a finding of editing",
        {"median_ratio": round(med, 4), "hot_blocks": int(hot.sum()),
         "blocks": int(hot.size)}, boxes)


# ---------------------------------------------------------------- noise

def noise_consistency(img: Image.Image) -> Signal:
    from scipy import ndimage
    grey = np.asarray(_rgb(img).convert("L"), dtype=np.float32)
    resid = grey - ndimage.median_filter(grey, size=3)
    grad = np.hypot(ndimage.sobel(grey, 0), ndimage.sobel(grey, 1))
    bs = 32
    h, w = grey.shape
    hb, wb = h // bs, w // bs
    if hb < 4 or wb < 4:
        return Signal("noise", False, "Too small to measure.", "")
    r = resid[:hb * bs, :wb * bs].reshape(hb, bs, wb, bs).transpose(0, 2, 1, 3)
    g = grad[:hb * bs, :wb * bs].reshape(hb, bs, wb, bs).transpose(0, 2, 1, 3)
    noise = np.median(np.abs(r - np.median(r, axis=(2, 3), keepdims=True)),
                      axis=(2, 3)) * 1.4826
    busy = g.mean(axis=(2, 3))
    # Only blocks smooth enough for the residual to be noise, not texture.
    usable = busy < np.percentile(busy, 60)
    if usable.sum() < 8:
        return Signal("noise", False, "Too little smooth area to compare "
                      "noise across the frame.", "")
    ref = float(np.median(noise[usable]))
    if ref < 0.3:
        return Signal("noise", False, "The image is almost noise-free (heavily "
                      "processed or synthetic), so noise cannot be compared.",
                      "phones denoise aggressively; this says nothing on its own",
                      {"reference_noise": round(ref, 3)})
    odd = usable & ((noise > ref * 2.5) | (noise < ref / 2.5))
    boxes = _components(odd, 0.02)
    return Signal(
        "noise", bool(boxes),
        (f"{len(boxes)} smooth region(s) with a noise level unlike the rest "
         f"of the frame" if boxes else
         "The sensor noise is consistent across the smooth parts of the frame."),
        "noise also varies with exposure -- shadows are noisier than "
        "highlights -- and with local denoising by the phone",
        {"reference_noise": round(ref, 3), "odd_blocks": int(odd.sum())}, boxes)


# ------------------------------------------------------------ JPEG ghost

def jpeg_ghost(img: Image.Image, fmt: str | None) -> Signal:
    if (fmt or "").upper() != "JPEG":
        return Signal("jpeg_ghost", False, "Not a JPEG: no compression history "
                      "to read.", "", {"format": fmt})
    im = _rgb(img)
    a = np.asarray(im, dtype=np.float32)
    curve = []
    for q in range(40, 101, 5):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q)
        b = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"),
                       dtype=np.float32)
        curve.append((q, float(((a - b) ** 2).mean())))
    qs = [q for q, _ in curve]
    errs = np.array([e for _, e in curve])
    # Local minima below the top quality: a dip means the image already
    # sits on that quality's quantisation grid.
    dips = [qs[i] for i in range(1, len(errs) - 1)
            if errs[i] < errs[i - 1] and errs[i] < errs[i + 1]]
    flagged = len(dips) >= 1 and min(dips) < 90
    return Signal(
        "jpeg_ghost", flagged,
        (f"The recompression error dips at quality {', '.join(map(str, dips))}: "
         f"the image was probably saved as JPEG more than once" if flagged else
         "No sign of more than one JPEG save."),
        "a single save through a messaging app followed by one more is "
        "entirely ordinary; double compression says the file was re-saved, "
        "not that its content was changed",
        {"curve": [(q, round(e, 2)) for q, e in curve], "dips": dips})


# ------------------------------------------------------------ resampling

def resampling(img: Image.Image) -> Signal:
    grey = np.asarray(_rgb(img).convert("L"), dtype=np.float32)
    if min(grey.shape) < 128:
        return Signal("resampling", False, "Too small to measure.", "")

    def peak(axis: int) -> tuple[float, float]:
        d2 = np.abs(np.diff(grey, n=2, axis=axis))
        profile = d2.mean(axis=1 - axis)
        profile = profile - profile.mean()
        spec = np.abs(np.fft.rfft(profile * np.hanning(len(profile))))
        freqs = np.fft.rfftfreq(len(profile))
        # Skip DC, the lowest frequencies (scene content) and the JPEG
        # 8-pixel block grid and its harmonics, which every JPEG has.
        ok = (freqs > 0.05) & (np.min(np.abs(freqs[:, None]
                                             - np.array([1/8, 2/8, 3/8, 4/8])[None, :]),
                                      axis=1) > 0.01)
        if not ok.any():
            return 0.0, 0.0
        s = spec[ok]
        i = int(np.argmax(s))
        return float(s[i] / (np.median(s) + 1e-9)), float(freqs[ok][i])

    px, fx = peak(1)
    py, fy = peak(0)
    flagged = px > 12 and py > 12
    return Signal(
        "resampling", flagged,
        ("A periodic interpolation signature on both axes: the image was "
         "probably scaled or rotated after capture" if flagged else
         "No periodic interpolation signature."),
        "almost every shared photograph has been resized somewhere between "
        "the camera and here; resampling says the pixels are not the "
        "sensor's, not that anything was altered",
        {"peak_ratio_x": round(px, 2), "peak_ratio_y": round(py, 2),
         "freq_x": round(fx, 4), "freq_y": round(fy, 4)})


# ---------------------------------------------------------- quantisation

# libjpeg's standard luminance table (JPEG Annex K).
_STD_LUMA = np.array([
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99])


def _ijg(q: int) -> np.ndarray:
    scale = 5000 / q if q < 50 else 200 - 2 * q
    t = np.floor((_STD_LUMA * scale + 50) / 100)
    return np.clip(t, 1, 255)


def quant_signature(img: Image.Image, camera: str | None) -> Signal:
    quant = getattr(img, "quantization", None)
    if not quant or 0 not in quant:
        return Signal("quantisation", False, "Not a JPEG, or no quantisation "
                      "table to read.", "")
    table = np.array(list(quant[0])[:64], dtype=np.float64)
    # PIL gives the table in zig-zag order for some versions and natural
    # order for others; libjpeg's tables are compared in both.
    zigzag = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19,
              26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28, 35, 42,
              49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59,
              52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63]
    std_match = None
    for q in range(1, 101):
        ref = _ijg(q)
        if np.array_equal(table, ref) or np.array_equal(table, ref[zigzag]):
            std_match = q
            break
    if std_match is not None:
        flagged = bool(camera)
        return Signal(
            "quantisation", flagged,
            (f"Compressed with libjpeg's standard table at quality {std_match}"
             + (f", although the file names a camera ({camera}) -- cameras "
                f"usually use their own tables, so this looks re-encoded by "
                f"software" if camera else "")),
            "many phones and most apps do use the standard table; on its own "
            "it identifies an encoder family, not an edit",
            {"standard_quality": std_match, "camera": camera})
    return Signal(
        "quantisation", False,
        "A non-standard quantisation table: typical of a camera's own "
        "encoder, not of a generic software re-save.",
        "some editors also use custom tables, so this favours a camera "
        "original without proving one",
        {"standard_quality": None, "camera": camera})
