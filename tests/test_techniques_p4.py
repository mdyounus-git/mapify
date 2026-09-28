"""Techniques 43-56: geometry, cross-references, forensics, provenance.

The forensic signals are tested in both directions on images whose
history is known because the test made them: an honest single save must
raise nothing, and a clear case -- a patch compressed at a very
different quality, a noise-free patch in a noisy frame, a file saved
twice -- must raise its signal. Weak splices that these classical
methods genuinely cannot see are not asserted either way.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, PngImagePlugin

from conftest import make_jpeg

import mapify.techniques.phase4_vision     # noqa: F401  registers 43-56
from mapify.techniques import REGISTRY, Context, run_all


def _noisy(w=900, h=700, seed=0, sigma=9.0) -> Image.Image:
    rng = np.random.default_rng(seed)
    y = np.linspace(70, 200, h)[:, None, None]
    x = np.linspace(-20, 20, w)[None, :, None]
    arr = (y + x + rng.normal(0, sigma, (h, w, 3))).clip(0, 255).astype("uint8")
    return Image.fromarray(arr)


def _jpeg(img: Image.Image, q: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q)
    return buf.getvalue()


def _ctx(raw: bytes, **kw) -> Context:
    from mapify.exif import read_exif
    img = Image.open(io.BytesIO(raw))
    img.load()
    return Context(image=img, raw_bytes=raw, exif=read_exif(img), **kw)


def _one(ctx, tid):
    return next(f for f in run_all(ctx, only=[tid]))


def test_the_registry_holds_43_to_56():
    assert set(range(43, 57)) <= set(REGISTRY)
    for tid in range(43, 57):
        assert not REGISTRY[tid].feeds_fusion
        assert REGISTRY[tid].fusion_note


def test_blocked_techniques_say_why_instead_of_staying_silent():
    ctx = _ctx(make_jpeg())
    for tid in (46, 48, 50, 51, 52, 53):
        f = _one(ctx, tid)
        assert not f.ran and len(f.skipped_because) > 20, tid


# ------------------------------------------------------------- geometry

def _building_scene(tilt_px=0) -> Image.Image:
    """Parallel vertical edges (a facade) with an optional convergence."""
    img = Image.new("RGB", (1000, 750), (205, 205, 200))
    d = ImageDraw.Draw(img)
    for k in range(14):
        x = 80 + k * 60
        top = x + (x - 500) * tilt_px / 1000 if tilt_px else x
        d.line([(x, 720), (top, 40)], fill=(30, 30, 35), width=4)
    for yy in range(80, 700, 70):
        d.line([(60, yy), (940, yy + 20)], fill=(60, 60, 70), width=3)
    return img


def test_parallel_verticals_read_as_a_level_camera():
    f = _one(_ctx(_jpeg(_building_scene(), 95)), 44)
    assert f.found
    assert abs(f.detail["pitch_deg"]) < 3


def test_converging_verticals_read_as_a_camera_looking_up():
    f = _one(_ctx(_jpeg(_building_scene(tilt_px=-260), 95)), 44)
    assert f.found and f.detail["pitch_deg"] > 5


def test_a_featureless_frame_says_it_has_no_structure():
    f = _one(_ctx(make_jpeg()), 43)
    assert f.ran and not f.found


def test_lens_reports_the_field_of_view_when_the_file_allows():
    img = Image.new("RGB", (1200, 900), (100, 120, 140))
    ex = Image.Exif()
    ex[0x8769] = {0x920A: 4.25, 0xA405: 26}
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=ex)
    f = _one(_ctx(buf.getvalue()), 45)
    assert f.found and f.detail["fov_deg"][0] == pytest.approx(69.4, abs=1.0)


# ------------------------------------------------------------ forensics

def test_an_honest_single_save_raises_nothing():
    raw = _jpeg(_noisy(), 92)
    f = _one(_ctx(raw), 54)
    assert f.ran and not f.found
    assert not _one(_ctx(raw), 55).found


def test_a_noise_free_patch_in_a_noisy_frame_is_flagged():
    img = _noisy(sigma=10.0)
    ImageDraw.Draw(img).rectangle([300, 200, 560, 420], fill=(140, 140, 140))
    f = _one(_ctx(_jpeg(img, 97)), 54)
    assert f.found
    assert f.detail["noise"]["flagged"]
    assert any(r.label == "noise" for r in f.regions)
    assert "Innocent" in f.summary or "ordinary causes" in f.summary


def test_a_file_saved_twice_shows_a_ghost():
    first = Image.open(io.BytesIO(_jpeg(_noisy(), 60)))
    first.load()
    raw = _jpeg(first.convert("RGB"), 95)
    f = _one(_ctx(raw), 55)
    assert f.found
    assert 55 <= min(f.detail["dips"]) <= 65


def test_a_png_is_not_given_a_compression_history():
    buf = io.BytesIO()
    _noisy().save(buf, "PNG")
    f = _one(_ctx(buf.getvalue()), 55)
    assert not f.found and "Not a JPEG" in f.summary


# ----------------------------------------------------------- provenance

def test_generation_settings_in_a_png_are_read_as_a_declaration():
    meta = PngImagePlugin.PngInfo()
    meta.add_text("parameters", "a busy street, Steps: 30, Sampler: DPM++ 2M")
    buf = io.BytesIO()
    _noisy(200, 150).save(buf, "PNG", pnginfo=meta)
    f = _one(_ctx(buf.getvalue()), 56)
    assert f.found and f.detail["ai_declared"]


def test_an_iptc_ai_source_type_is_read():
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
           b'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/'
           b'digitalsourcetype/trainedAlgorithmicMedia"/></rdf:RDF></x:xmpmeta>')
    raw = _jpeg(_noisy(300, 200), 90)
    # An APP1 XMP segment right after SOI.
    seg = b"http://ns.adobe.com/xap/1.0/\x00" + xmp
    raw = raw[:2] + b"\xff\xe1" + (len(seg) + 2).to_bytes(2, "big") + seg + raw[2:]
    f = _one(_ctx(raw), 56)
    assert f.found and f.detail["digital_source_type"] == "trainedalgorithmicmedia"


def test_no_declaration_is_reported_as_proving_nothing():
    f = _one(_ctx(_jpeg(_noisy(), 90)), 56)
    assert not f.found and "proves nothing" in f.summary
