"""The street-photo memory: nearest photographs, grouped into places.

A tiny index is written here with two places whose photographs embed in two
different directions; nothing loads a model.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapify.models.retrieval import SOURCE, RetrievalMemory


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def write_index(tmp_path, rng=None):
    rng = rng or np.random.default_rng(0)
    d = 768
    nairobi, paris = _unit(rng.normal(size=d)), _unit(rng.normal(size=d))
    rows = []
    for centre, (lat, lon), n in ((nairobi, (-1.2864, 36.8172), 30),
                                  (paris, (48.8566, 2.3522), 30)):
        for _ in range(n):
            e = _unit(centre + rng.normal(scale=0.05, size=d))
            rows.append((e, lat + rng.normal(scale=0.02), lon + rng.normal(scale=0.02)))
    emb = np.stack([r[0] for r in rows]).astype(np.float16)
    np.savez(tmp_path / "00-000.npz", emb=emb,
             lat=np.array([r[1] for r in rows], np.float32),
             lon=np.array([r[2] for r in rows], np.float32),
             cc=np.array(["KE"] * 30 + ["FR"] * 30), id=np.arange(60).astype(str))
    return nairobi, paris


def test_the_nearest_photographs_say_where(tmp_path):
    nairobi, paris = write_index(tmp_path)
    mem = RetrievalMemory(tmp_path)
    assert mem.available() and mem.size == 60
    nb = mem.neighbours(nairobi, k=10)
    assert len(nb) == 10 and all(abs(lat + 1.29) < 0.2 for lat, _, _ in nb)
    assert nb == sorted(nb, key=lambda n: -n[2])


def test_neighbours_grouped_into_places_scored_by_their_share(tmp_path):
    nairobi, paris = write_index(tmp_path)
    places = RetrievalMemory(tmp_path).candidates(_unit(nairobi * 0.9 + paris * 0.1), k=60)
    assert places[0].source == SOURCE
    assert abs(places[0].latitude + 1.2864) < 0.1 and abs(places[0].longitude - 36.817) < 0.1
    assert places[0].score > 0.9                     # nearly all the weight
    assert sum(p.score for p in places) == pytest.approx(1.0, abs=1e-6)


def test_an_empty_or_missing_memory_answers_nothing(tmp_path):
    mem = RetrievalMemory(tmp_path / "none")
    assert not mem.available()
    assert mem.candidates(np.ones(768)) == []


def test_the_analysis_offers_the_memorys_places_only_when_switched_on(tmp_path):
    from mapify.config import load_config
    from mapify.models.base import RawCandidate
    from mapify.pipeline import AnalysisEngine, AnalysisReport

    nairobi, _ = write_index(tmp_path)
    cfg = load_config()
    cfg.models.geoclip_enabled = cfg.models.clip_evidence_enabled = False
    cfg.models.ocr_enabled = False
    engine = AnalysisEngine(cfg)
    engine._memory = RetrievalMemory(tmp_path)
    raw = [RawCandidate(-1.0, 36.0, 0.4, "geoclip")]
    report = AnalysisReport(ok=False, elapsed_s=0)
    assert engine._remembered(nairobi, raw, report) == []          # off by default
    cfg.models.retrieval_enabled = True
    got = engine._remembered(nairobi, raw, report)
    assert got and got[0].source == SOURCE
    assert got[0].score <= 0.4 * 0.5 + 1e-9                         # below GeoCLIP's best
    assert report.evidence["street_memory"]["photographs"] == 60
