"""Techniques 132-142: other images, and the file's own history.

The case store is exercised end to end -- an analysis is recorded, and
the next one recognises the same file, a resized copy of it, and a
photograph placed nearby. The forensic detectors are tested against
planted edits and against the textures that fool naive versions of them.
"""

from __future__ import annotations

import io
import json

import numpy as np
import pytest
from PIL import Image

from conftest import make_jpeg

import mapify.techniques  # noqa: F401
from mapify.techniques import Context, Finding, run_all
from mapify.techniques import casefile, phase9_provenance as p9


class Cand:
    def __init__(self, lat, lon, iso=None, probability=0.5, adjustments=()):
        self.latitude, self.longitude, self.iso_a2 = lat, lon, iso
        self.probability, self.adjustments, self.provenance = probability, list(adjustments), "model"


def _jpeg_bytes(img: Image.Image, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", **kw)
    return buf.getvalue()


def _textured(seed=3, size=(640, 480)) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray((rng.random(size[::-1]) * 60 + 100).astype("uint8")).convert("RGB")


def _ctx(raw=None, *, filename=None, candidates=(), prediction=None, embedding=None):
    from mapify.exif import read_exif
    raw = raw or make_jpeg()
    img = Image.open(io.BytesIO(raw))
    img.load()
    return Context(image=img, raw_bytes=raw, filename=filename, exif=read_exif(img),
                   candidates=list(candidates), prediction=prediction or {},
                   embedding=embedding)


def _one(ctx, tid):
    return next(iter(run_all(ctx, only=[tid])))


@pytest.fixture
def cases(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPIFY_CASE_DIR", str(tmp_path))
    return tmp_path


def _determinate(lat, lon, band="high"):
    return {"determinate": True, "confidence": band,
            "estimated": {"latitude": lat, "longitude": lon}, "uncertainty_km": 3.0}


# ------------------------------------------------------------ case store

def test_an_analysis_is_recorded_and_the_same_file_recognised(cases):
    raw = _jpeg_bytes(_textured())
    ctx = _ctx(raw, filename="street.jpg", prediction=_determinate(12.97, 77.59))
    path = casefile.record_run(raw, "street.jpg", run_all(ctx, only=[1, 2]),
                               ctx.prediction, image=ctx.image, embedding=np.ones(8))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["runs"][0]["prediction"]["confidence"] == "high"
    assert path.stat().st_size < len(raw) / 10          # findings, never the pixels
    index = casefile.load_index()
    entry = next(iter(index.values()))
    assert entry["filename"] == "street.jpg" and len(entry["dhash"]) == 16
    f = _one(_ctx(raw, filename="street.jpg"), 134)
    assert f.found and f.detail["same_file"] and "analysed before" in f.summary


def test_a_resized_copy_is_a_near_duplicate(cases):
    img = _textured()
    raw = _jpeg_bytes(img)
    ctx = _ctx(raw, prediction=_determinate(51.5, -0.08))
    casefile.record_run(raw, "bridge.jpg", [], ctx.prediction, image=ctx.image)
    small = _jpeg_bytes(img.resize((320, 240)), quality=70)
    f = _one(_ctx(small), 134)
    assert f.found and not f.detail.get("same_file")
    assert f.detail["copies"][0]["filename"] == "bridge.jpg"


def test_a_different_photograph_is_not_a_duplicate(cases):
    raw = _jpeg_bytes(_textured(seed=1))
    casefile.record_run(raw, "a.jpg", [], {}, image=_ctx(raw).image)
    f = _one(_ctx(_jpeg_bytes(_textured(seed=9))), 134)
    assert not f.found


def test_photographs_placed_nearby_are_correlated(cases):
    raw = _jpeg_bytes(_textured(seed=1))
    casefile.record_run(raw, "first.jpg", [], _determinate(12.970, 77.590),
                        image=_ctx(raw).image)
    other = _jpeg_bytes(_textured(seed=2))
    f = _one(_ctx(other, candidates=[Cand(12.98, 77.60)],
                  prediction=_determinate(12.980, 77.600)), 132)
    assert f.found and f.detail["nearby_cases"][0]["filename"] == "first.jpg"
    far = _one(_ctx(other, candidates=[Cand(51.5, -0.1)],
                    prediction=_determinate(51.5, -0.1)), 132)
    assert not far.found


def test_an_undetermined_location_correlates_with_nothing(cases):
    f = _one(_ctx(candidates=[Cand(1, 1)], prediction={"determinate": False}), 132)
    assert not f.found and "not determined" in f.summary


def test_the_same_scene_by_embedding_but_not_a_copy(cases):
    raw = _jpeg_bytes(_textured(seed=1))
    vec = np.random.default_rng(0).normal(size=16).astype(np.float32)
    casefile.record_run(raw, "scene.jpg", [], {}, image=_ctx(raw).image, embedding=vec)
    other = _jpeg_bytes(_textured(seed=5))
    f = _one(_ctx(other, embedding=vec + 0.01), 133)
    assert f.found and "scene.jpg" in f.summary and "not necessarily" in f.summary


def test_an_empty_store_says_so(cases):
    for tid in (133, 134):
        f = _one(_ctx(embedding=np.ones(4)), tid)
        assert not f.found and "case store" in f.summary

# ---------------------------------------------------------- file origin

@pytest.mark.parametrize("name, label, stamp", [
    ("IMG-20230514-WA0007.jpg", "WhatsApp on Android", "2023-05-14T00:00:00"),
    ("WhatsApp Image 2023-05-14 at 10.23.45.jpeg", "WhatsApp", "2023-05-14T10:23:45"),
    ("PXL_20230514_102345123.jpg", "Pixel", "2023-05-14T10:23:45+00:00"),
    ("FB_IMG_1684059825000.jpg", "Facebook", "2023-05-14T10:23:45+00:00"),
    ("Screenshot_20230514-102345.png", "screenshot", "2023-05-14T10:23:45"),
])
def test_file_names_name_their_origin_and_date(name, label, stamp):
    base, m, found_label, _kind, st = p9._name_match(name)
    assert label in found_label
    assert p9._name_time(m, st).isoformat() == stamp


def test_a_whatsapp_copy_is_called_one():
    f = _one(_ctx(filename="IMG-20230514-WA0007.jpg"), 135)
    assert f.found and "messaging app" in f.summary and "hints, not proof" in f.summary


def test_a_name_received_before_its_capture_date_is_a_contradiction():
    from PIL import Image as I
    exif = I.Exif()
    exif[0x8769] = {0x9003: "2023:06:01 09:00:00"}
    raw = make_jpeg(exif=exif)
    f = _one(_ctx(raw, filename="WhatsApp Image 2023-05-14 at 10.23.45.jpeg"), 136)
    assert f.detail["contradiction"] is True and "cannot be right" in f.summary
    ok = _one(_ctx(raw, filename="WhatsApp Image 2023-06-02 at 10.23.45.jpeg"), 136)
    assert ok.detail["contradiction"] is False


def test_a_plain_name_records_no_date():
    f = _one(_ctx(filename="holiday.jpg"), 136)
    assert not f.found and "records no date" in f.summary

# --------------------------------------------------------- edit history

def test_a_modification_after_capture_and_an_editor_are_edits():
    from PIL import Image as I
    exif = I.Exif()
    exif[0x0131] = "Adobe Photoshop CS3 Windows"
    exif[0x0132] = "2011:07:20 08:30:31"
    exif[0x8769] = {0x9003: "2011:02:27 13:40:31"}
    f = _one(_ctx(make_jpeg(exif=exif)), 137)
    assert f.found and "modified" in f.summary and "Photoshop" in f.summary
    assert "not that its content is false" in f.summary


def test_an_unedited_file_is_not_proof_of_an_original():
    f = _one(_ctx(make_jpeg()), 137)
    assert not f.found and "not proof" in f.summary


def test_an_xmp_history_is_read():
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:li stEvt:action="saved" '
           b'stEvt:softwareAgent="Adobe Photoshop 24.0"/><rdf:li stEvt:action="converted" '
           b'stEvt:softwareAgent="Adobe Photoshop 24.0"/></x:xmpmeta>')
    raw = make_jpeg() + xmp                       # appended: found by scanning
    f = _one(_ctx(raw), 137)
    assert f.found and f.detail["xmp_history"] == ["saved", "converted"]

# ----------------------------------------------------------- forensics

def test_a_cloned_patch_is_found_even_after_recompression():
    img = _textured()
    patch = Image.fromarray((np.random.default_rng(7).random((96, 96)) * 255)
                            .astype("uint8")).convert("RGB")
    img.paste(patch, (60, 60))
    img.paste(patch, (400, 300))
    f = _one(_ctx(_jpeg_bytes(img, quality=85)), 140)
    assert f.found and len(f.regions) == 2 and "Innocent explanation" in f.summary


@pytest.mark.parametrize("img", [
    _textured(),                                                   # plain noise
    Image.fromarray(np.tile((np.random.default_rng(2).random((32, 32)) * 255)
                            .astype("uint8"), (15, 20))).convert("RGB"),   # tiles
    Image.new("RGB", (640, 480), (90, 120, 160)),                  # flat sky
])
def test_noise_tiles_and_sky_are_not_clones(img):
    assert p9.copy_move(img) is None


def test_the_quantisation_table_is_read():
    f = _one(_ctx(_jpeg_bytes(_textured(), quality=87)), 138)
    assert f.detail["standard_quality"] == 87


def test_no_ai_declaration_proves_nothing():
    f = _one(_ctx(), 141)
    assert not f.found and "proves nothing" in f.summary


def test_a_composite_declaration_is_a_generated_region():
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><Iptc4xmpExt:DigitalSourceType>'
           b'http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia'
           b'</Iptc4xmpExt:DigitalSourceType></x:xmpmeta>')
    f = _one(_ctx(make_jpeg() + xmp), 141)
    assert f.found and "part of it was generated" in f.summary


def test_metadata_checks_are_collected_with_their_verdicts():
    ctx = _ctx(prediction={"exif_vs_estimate": {"verdict": "rejected", "distance_km": 900}})
    ctx.memo["findings"] = {59: Finding(technique=59, key="s", name="s", phase=5,
                                        found=True, detail={"contradiction": False})}
    f = _one(ctx, 142)
    assert f.found and f.detail["contradiction"] is True
    assert len(f.detail["checks"]) == 2 and "GPS" in f.summary
