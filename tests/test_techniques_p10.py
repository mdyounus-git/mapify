"""Techniques 143-153: the synthesis, and the registry as a whole.

Tested for its honesty rules: measured intervals with their sample
sizes, no interval for an undetermined location, agreement counted by
independent line rather than by finding, every contradiction listed
once, and every one of the 153 techniques accounted for.
"""

from __future__ import annotations

import io
import json

import numpy as np
import pytest
from PIL import Image

from conftest import make_jpeg

import mapify.techniques  # noqa: F401
from mapify.techniques import REGISTRY, Context, Finding, run_all
from mapify.techniques.phase10_synthesis import BAND_ERROR_KM
from mapify.techniques.spec import NAMES, phase_of


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


# ------------------------------------------------------------- registry

def test_all_153_techniques_are_registered_as_specified():
    assert set(NAMES) <= set(REGISTRY)
    for tid in NAMES:
        assert REGISTRY[tid].name == NAMES[tid], tid
        assert REGISTRY[tid].phase == phase_of(tid), tid


def test_a_full_run_has_153_findings_none_failed_and_all_serialise():
    findings = [f for f in run_all(_ctx(make_jpeg(800, 600))) if f.technique <= 153]
    assert len(findings) == 153                  # ids above 153 are test-only
    assert not [f.technique for f in findings
                if f.skipped_because == "the technique raised"]
    json.dumps([f.as_dict() for f in findings])

# ------------------------------------------------------------ synthesis

def test_the_error_interval_is_the_benchmarks_with_its_sample_size():
    f = _one(_ctx(prediction=_determinate(12.97, 77.59, "high")), 148)
    assert f.detail["radii_km"] == {"50%": BAND_ERROR_KM["high"]["p50"],
                                    "75%": BAND_ERROR_KM["high"]["p75"],
                                    "90%": BAND_ERROR_KM["high"]["p90"]}
    assert "n=26" in f.summary


def test_a_corroborated_gps_tag_gives_metres():
    pred = _determinate(12.97, 77.59)
    pred["estimated"]["precision_km"] = 0.02
    f = _one(_ctx(prediction=pred), 147)
    assert f.detail["radius_km"] == 0.02 and "20 m" in f.summary


def test_an_undetermined_location_has_no_interval():
    for tid in (147, 148):
        f = _one(_ctx(prediction={"determinate": False, "confidence": "insufficient"}), tid)
        assert not f.found and "not determined" in f.summary


def test_the_image_model_alone_is_one_line_of_evidence():
    f = _one(_ctx(candidates=[Cand(51.5, -0.1, "GB")]), 144)
    assert f.detail["independent_support"] == 1
    assert "nothing independent confirms" in f.summary


def test_independent_text_counts_as_a_second_line():
    ctx = _ctx(candidates=[Cand(13.0, 80.2, "IN")])
    ctx.memo["findings"] = {121: Finding(
        technique=121, key="c", name="Currency", phase=8, found=True,
        detail={"leader_inside": True, "contradiction": False, "narrow": True,
                "countries": ["IN"]}, sources=["EasyOCR (Apache-2.0), run locally"])}
    f = _one(ctx, 144)
    assert f.detail["independent_support"] == 2
    assert 121 in f.detail["supporting_lines"]["text read from the image"]


def test_contradictions_are_listed_once_each():
    ctx = _ctx(candidates=[Cand(13.0, 80.2, "IN")])
    ctx.memo["findings"] = {
        59: Finding(technique=59, key="s", name="Season", phase=5, found=True,
                    summary="bare trees in July", detail={"contradiction": True}),
        121: Finding(technique=121, key="c", name="Currency", phase=8, found=True,
                     detail={"contradiction": True, "leader_inside": False},
                     sources=["EasyOCR (Apache-2.0), run locally"])}
    run_all(ctx, only=[144])
    f = _one(ctx, 145)
    assert [c["technique"] for c in f.detail["contradictions"]] == [59, 121]


def test_text_favouring_a_lower_candidate_is_flagged():
    ctx = _ctx(candidates=[Cand(51.5, -0.1, "GB", 0.6), Cand(13.0, 80.2, "IN", 0.3)])
    ctx.memo["findings"] = {121: Finding(technique=121, key="c", name="c", phase=8,
                                         found=True, detail={"narrow": True,
                                                             "countries": ["IN"]})}
    f = _one(ctx, 146)
    assert f.detail["text_favours_another"] is True and "worth a look" in f.summary


def test_the_date_interval_names_where_each_bound_came_from():
    ctx = _ctx()
    ctx.memo["findings"] = {
        69: Finding(technique=69, key="d", name="d", phase=5, found=True,
                    detail={"bounds": {"not_before_year": 2019}, "contradicted_by": []}),
        136: Finding(technique=136, key="u", name="u", phase=9, found=True,
                     detail={"meaning": "received or saved",
                             "name_time": "2023-05-14T10:23:45"})}
    f = _one(ctx, 150)
    assert f.detail["from"] == "2019-01-01" and f.detail["to"] == "2023-05-14"
    assert "not earlier" in f.summary and "not later" in f.summary


def test_the_evidence_map_collects_every_marked_region():
    ctx = _ctx()
    ctx.memo["findings"] = {6: Finding(technique=6, key="o", name="OCR", phase=1, found=True,
                                       regions=[mapify.techniques.Region(0.1, 0.1, 0.4, 0.2, "SALE")])}
    f = _one(ctx, 151)
    assert f.detail["regions"][0]["technique"] == 6
    assert f.detail["regions"][0]["box"] == [0.1, 0.1, 0.4, 0.2]


def test_the_provenance_record_hashes_the_input():
    import hashlib
    raw = make_jpeg()
    f = _one(_ctx(raw), 152)
    assert f.detail["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert f.detail["techniques_registered"] >= 153


def test_the_orchestration_accounts_for_every_technique():
    findings = run_all(_ctx(make_jpeg(800, 600)))
    f = next(x for x in findings if x.technique == 153)
    assert f.detail["techniques_total"] == len([x for x in findings if x.technique <= 153])
    assert set(f.detail["phases"]) == {str(p) for p in range(1, 11)}
