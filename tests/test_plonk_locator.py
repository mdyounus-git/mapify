"""The second locator: PLONK's samples grouped into places, its spread
reported, and its places offered only when switched on. A stand-in returns
fixed samples, so nothing loads DINOv2.
"""

from __future__ import annotations

import pytest

import numpy as np
from PIL import Image

from mapify.models.plonk_locator import SOURCE, PlonkLocator


class FakePipe:
    def __init__(self, pts):
        self.pts = np.asarray(pts, dtype=float)
        self.calls = []

    def __call__(self, image, batch_size=None, num_steps=None):
        self.calls.append((batch_size, num_steps))
        return self.pts


def nairobi_and_a_stray():
    rng = np.random.default_rng(1)
    pts = [(-1.29 + rng.normal(scale=0.05), 36.82 + rng.normal(scale=0.05)) for _ in range(28)]
    return pts + [(48.85, 2.35)] * 4


def test_samples_become_places_and_a_spread():
    loc = PlonkLocator(samples=32, steps=16)
    loc._pipe = FakePipe(nairobi_and_a_stray())
    cands, summary = loc.locate(Image.new("RGB", (64, 64)))
    assert loc._pipe.calls == [(32, 16)]
    assert cands[0].source == SOURCE and abs(cands[0].latitude + 1.29) < 0.1
    assert cands[0].score == 28 / 32 and cands[1].score == 4 / 32
    assert summary["samples"] == 32 and summary["median_spread_km"] < 20


def test_the_analysis_asks_plonk_only_when_switched_on():
    from mapify.config import load_config
    from mapify.models.base import RawCandidate
    from mapify.pipeline import AnalysisEngine, AnalysisReport

    cfg = load_config()
    cfg.models.geoclip_enabled = cfg.models.clip_evidence_enabled = False
    cfg.models.ocr_enabled = False
    cfg.models.plonk_enabled = False
    engine = AnalysisEngine(cfg)
    engine._plonk = PlonkLocator()
    engine._plonk._pipe = FakePipe(nairobi_and_a_stray())
    report = AnalysisReport(ok=False, elapsed_s=0)
    image = Image.new("RGB", (64, 64))
    assert engine._second_opinion(image, report) is None
    cfg.models.plonk_enabled = True
    got = engine._second_opinion(image, report)
    assert got.shape[1] == 2 and len(got) == len(nairobi_and_a_stray())
    assert report.evidence["second_locator"]["model"] == "PLONK (YFCC)"


def test_the_second_locator_judges_the_candidates_it_agrees_with():
    """Its samples weigh GeoCLIP's candidates; they are not candidates."""
    import numpy as np

    from mapify.config import FusionConfig
    from mapify.evidence.fusion import FusionEngine
    from mapify.geo.offline import get_geocoder
    from mapify.models.base import RawCandidate

    geo = get_geocoder()
    if not geo.available():
        pytest.skip("Natural Earth not installed")
    fe = FusionEngine(FusionConfig(), geo)
    raw = [RawCandidate(19.07, 72.88, 0.30, "geoclip"),     # Mumbai, the model's favourite
           RawCandidate(28.61, 77.21, 0.20, "geoclip")]     # Delhi
    near_delhi = np.array([[28.6 + i * 0.01, 77.2] for i in range(48)]
                          + [[19.0, 72.9]] * 4)
    alone = fe.fuse(raw)
    judged = fe.fuse(raw, second_opinion=near_delhi)
    assert abs(alone.candidates[0].latitude - 19.07) < 0.1
    assert abs(judged.candidates[0].latitude - 28.61) < 0.1
    assert any(a.source == "second_locator" for a in judged.candidates[0].adjustments)
    assert len(judged.candidates) == len(alone.candidates), "no candidates added"
