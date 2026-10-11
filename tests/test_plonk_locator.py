"""The second locator: PLONK's samples grouped into places, its spread
reported, and its places offered only when switched on. A stand-in returns
fixed samples, so nothing loads DINOv2.
"""

from __future__ import annotations

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
    engine = AnalysisEngine(cfg)
    engine._plonk = PlonkLocator()
    engine._plonk._pipe = FakePipe(nairobi_and_a_stray())
    raw = [RawCandidate(-1.0, 36.0, 0.4, "geoclip")]
    report = AnalysisReport(ok=False, elapsed_s=0)
    image = Image.new("RGB", (64, 64))
    assert engine._second_opinion(image, raw, report) == []
    cfg.models.plonk_enabled = True
    got = engine._second_opinion(image, raw, report)
    assert got[0].source == SOURCE and got[0].score <= 0.4
    assert report.evidence["second_locator"]["model"] == "PLONK (YFCC)"
