"""Clustering, evidence re-ranking, and the confidence band.

The confidence band is the part of this program that most needs testing. A
wrong coordinate is a limitation; a wrong coordinate labelled "high
confidence" is a lie, and these tests exist to make that fail loudly.
"""

from __future__ import annotations

import math

import pytest

from conftest import requires_geodata
from mapify.config import FusionConfig
from mapify.evidence.fusion import Adjustment, Candidate, FusionEngine, cluster
from mapify.models.base import (
    GroupReading,
    OcrLine,
    OcrResult,
    RawCandidate,
    VisualEvidence,
)


def raw(lat, lon, score, source="geoclip"):
    return RawCandidate(latitude=lat, longitude=lon, score=score, source=source)


def reading(group, option, label, confidence, called=True, constrains=True):
    return GroupReading(
        group_key=group, group_title=group, option_key=option,
        option_label=label, confidence=confidence, margin=0.4,
        called=called, constrains=constrains,
    )


class TestClustering:
    def test_nearby_points_merge_and_sum_their_mass(self):
        out = cluster(
            [raw(48.8584, 2.2945, 0.01), raw(48.8590, 2.2950, 0.01)],
            radius_km=120,
        )
        assert len(out) == 1
        assert out[0].prior_score == pytest.approx(0.02)
        assert out[0].cluster_size == 2

    def test_distant_points_stay_separate(self):
        out = cluster([raw(48.85, 2.29, 0.01), raw(35.68, 139.69, 0.01)], 120)
        assert len(out) == 2

    def test_clusters_are_ordered_by_mass(self):
        out = cluster(
            [raw(0, 0, 0.01), raw(50, 50, 0.05), raw(-30, 120, 0.03)], 120
        )
        assert [round(c.prior_score, 3) for c in out] == [0.05, 0.03, 0.01]

    def test_centroid_is_pulled_by_weight(self):
        out = cluster([raw(0.0, 0.0, 0.09), raw(0.0, 1.0, 0.01)], 200)
        assert out[0].longitude == pytest.approx(0.1, abs=0.02)

    def test_spread_records_the_widest_member(self):
        out = cluster([raw(0.0, 0.0, 0.01), raw(0.0, 0.9, 0.01)], 200)
        assert out[0].spread_km == pytest.approx(50.0, abs=2.0)

    def test_empty_input(self):
        assert cluster([], 120) == []


@requires_geodata
class TestEvidenceAdjustments:
    @pytest.fixture
    def engine(self, geocoder, countries):
        return FusionEngine(FusionConfig(), geocoder, countries)

    def test_left_hand_traffic_favours_a_left_driving_country(self, engine):
        """India drives on the left, France on the right."""
        evidence = VisualEvidence(readings=[
            reading("traffic_side", "left", "Left-hand traffic", 0.9)
        ])
        result = engine.fuse(
            [raw(12.9716, 77.5946, 0.01), raw(48.8584, 2.2945, 0.01)],
            evidence=evidence,
        )
        by_country = {c.iso_a2: c for c in result.candidates}
        assert by_country["IN"].total_adjustment > 0
        assert by_country["FR"].total_adjustment < 0
        assert result.candidates[0].iso_a2 == "IN"

    def test_cyrillic_signage_favours_russia_over_france(self, engine):
        evidence = VisualEvidence(readings=[
            reading("signage_script", "Cyrl", "Cyrillic script", 0.8)
        ])
        result = engine.fuse(
            [raw(48.8584, 2.2945, 0.02), raw(55.7558, 37.6173, 0.01)],
            evidence=evidence,
        )
        assert result.candidates[0].iso_a2 == "RU"

    def test_latin_agreement_is_deliberately_weak(self, engine):
        """Almost every country signs in Latin, so agreeing proves little."""
        evidence = VisualEvidence(readings=[
            reading("signage_script", "Latn", "Latin script", 0.9)
        ])
        result = engine.fuse([raw(48.8584, 2.2945, 0.02)], evidence=evidence)
        latin = [a for a in result.candidates[0].adjustments
                 if a.source == "signage_script"]
        assert latin and latin[0].agreement == pytest.approx(0.25)

    def test_uncalled_readings_do_nothing(self, engine):
        evidence = VisualEvidence(readings=[
            reading("traffic_side", "left", "Left-hand traffic", 0.9, called=False)
        ])
        result = engine.fuse([raw(12.9716, 77.5946, 0.01)], evidence=evidence)
        assert not [a for a in result.candidates[0].adjustments
                    if a.source == "traffic_side"]

    def test_snow_contradicts_the_equator(self, engine):
        evidence = VisualEvidence(readings=[
            reading("snow_cover", "snow", "Snow on the ground", 0.9)
        ])
        result = engine.fuse([raw(1.35, 103.82, 0.01)], evidence=evidence)  # Singapore
        snow = [a for a in result.candidates[0].adjustments
                if a.source == "snow_cover"]
        assert snow and snow[0].agreement < 0

    def test_adjustment_total_is_clamped(self, engine):
        cfg = FusionConfig()
        cfg.max_total_adjustment = 0.5
        engine.cfg = cfg
        evidence = VisualEvidence(readings=[
            reading("traffic_side", "left", "Left-hand traffic", 1.0),
            reading("signage_script", "Deva", "Devanagari script", 1.0),
            reading("world_region", "s_asia", "South Asia", 1.0),
        ])
        result = engine.fuse([raw(12.9716, 77.5946, 0.01)], evidence=evidence)
        top = result.candidates[0]
        # The raw sum of agreements exceeds the clamp, but the multiplier
        # actually applied is exp(clamp), not exp(sum).
        assert top.total_adjustment > 0.5
        assert top.score / top.prior_score == pytest.approx(math.exp(0.5), rel=1e-6)

    def test_every_adjustment_carries_a_reason(self, engine):
        evidence = VisualEvidence(readings=[
            reading("traffic_side", "left", "Left-hand traffic", 0.9)
        ])
        result = engine.fuse([raw(12.9716, 77.5946, 0.01)], evidence=evidence)
        for a in result.candidates[0].adjustments:
            assert a.detail, "an adjustment with no stated reason is not auditable"
            assert a.as_dict()["direction"] in {"supports", "contradicts", "neutral"}


@requires_geodata
class TestToponyms:
    @pytest.fixture
    def engine(self, geocoder, countries):
        return FusionEngine(FusionConfig(), geocoder, countries)

    def test_a_read_place_name_is_matched_in_the_gazetteer(self, engine):
        ocr = OcrResult(lines=[OcrLine(text="Bengaluru", confidence=0.9)])
        hits = engine.find_toponyms(ocr)
        assert hits and hits[0].match.name == "Bengaluru"

    def test_cyrillic_place_name_matches_without_transliteration(self, engine):
        ocr = OcrResult(lines=[OcrLine(text="Москва", confidence=0.85)])
        hits = engine.find_toponyms(ocr)
        assert hits and hits[0].match.name == "Moscow"
        assert hits[0].match.matched_field == "NAME_RU"

    def test_a_lower_case_word_is_not_a_small_places_other_name(self, engine):
        # "Clinically proven" on a toothpaste advert in Coimbatore: "proven"
        # matched Prøven, the Danish name of a Greenland village of 200.
        ocr = OcrResult(lines=[OcrLine(text="Clinically proven", confidence=0.99)])
        assert engine.find_toponyms(ocr) == []

    def test_a_name_on_a_sign_still_reads_in_capitals_or_title_case(self, engine):
        for text in ("BENGALURU", "Bengaluru"):
            hits = engine.find_toponyms(OcrResult(lines=[OcrLine(text=text, confidence=0.9)]))
            assert hits and hits[0].match.name == "Bengaluru", text

    def test_an_offshore_place_keeps_its_countrys_codes(self, geocoder):
        # Kangersuatsiaq sits just off Natural Earth's coastline; with a name
        # and no code it escaped every check that compares countries.
        loc = geocoder.locate(72.37964, -55.549119)
        assert loc.offshore and loc.country and loc.country.iso_a2 == "GL"

    def test_low_confidence_ocr_is_ignored(self, engine):
        ocr = OcrResult(lines=[OcrLine(text="Bengaluru", confidence=0.1)])
        assert engine.find_toponyms(ocr) == []

    def test_a_matching_toponym_boosts_the_nearby_candidate(self, engine):
        ocr = OcrResult(lines=[OcrLine(text="Bengaluru", confidence=0.9)])
        result = engine.fuse(
            [raw(48.8584, 2.2945, 0.02), raw(12.9716, 77.5946, 0.01)], ocr=ocr
        )
        assert result.candidates[0].iso_a2 == "IN"

    def test_a_distant_toponym_counts_against_a_candidate(self, engine):
        ocr = OcrResult(lines=[OcrLine(text="Bengaluru", confidence=0.9)])
        result = engine.fuse([raw(48.8584, 2.2945, 0.02)], ocr=ocr)
        paris = next(c for c in result.candidates if c.iso_a2 == "FR")
        against = [a for a in paris.adjustments if a.source == "toponym"]
        assert against and against[0].agreement < 0


@requires_geodata
class TestConfidenceBanding:
    @pytest.fixture
    def engine(self, geocoder, countries):
        return FusionEngine(FusionConfig(), geocoder, countries)

    def test_tight_confident_cluster_is_high(self, engine):
        """Enough mass, tight, and nothing arguing against it."""
        result = engine.fuse([
            raw(48.8584, 2.2945, 0.12), raw(48.8590, 2.2950, 0.12),
            raw(48.8580, 2.2940, 0.10),
        ])
        assert result.confidence == "high"
        assert result.locator_mass == pytest.approx(0.34)

    def test_a_shortlist_holding_no_mass_is_insufficient(self, engine):
        """A tidy top-k means nothing if the model committed nothing to it."""
        result = engine.fuse([
            raw(48.8584, 2.2945, 0.008), raw(48.8590, 2.2950, 0.008),
        ])
        assert result.confidence == "insufficient"
        assert "did not recognise" in result.confidence_reason

    def test_a_tight_cluster_with_thin_mass_is_not_high(self, engine):
        """The regression this whole banding rewrite exists for.

        A perfectly tight, perfectly concentrated cluster used to reach
        "high" on its own. Over 163 samples that produced calls 15,689 km
        off target, because a confidently wrong GeoCLIP prediction looks
        exactly like a confidently right one on dispersion alone. Mass is
        what separates them.
        """
        result = engine.fuse([
            raw(14.5995, 120.9842, 0.05), raw(14.5996, 120.9843, 0.05),
        ])
        assert result.dispersion_km < 1.0        # as tight as it gets
        assert result.concentration == pytest.approx(1.0)
        assert result.confidence != "high"

    def test_the_high_band_names_what_is_missing(self, engine):
        """A near miss should say which condition it failed."""
        result = engine.fuse([
            raw(48.8584, 2.2945, 0.06), raw(48.8590, 2.2950, 0.06),
        ])
        assert result.confidence in {"medium", "low"}
        assert "Short of high confidence on" in result.confidence_reason

    def test_scattered_candidates_are_insufficient(self, engine):
        result = engine.fuse([
            raw(48.85, 2.29, 0.04), raw(-33.87, 151.21, 0.04),
            raw(40.71, -74.01, 0.04), raw(-22.95, -43.21, 0.04),
        ])
        assert result.confidence == "insufficient"
        assert result.dispersion_km > 2500

    def test_contradicting_evidence_downgrades(self, engine):
        """A confident locator that the image argues against claims less."""
        evidence = VisualEvidence(readings=[
            reading("world_region", "e_asia", "East Asia", 0.9),
            reading("signage_script", "Hani", "Chinese characters", 0.9),
        ])
        points = [raw(48.8584, 2.2945, 0.16), raw(48.8590, 2.2950, 0.16)]
        clean = engine.fuse(list(points))
        contradicted = engine.fuse(list(points), evidence=evidence)
        assert clean.confidence == "high"
        assert contradicted.evidence_support < 0
        assert contradicted.confidence != "high"
        assert any("contradicts" in n for n in contradicted.notes)

    def test_negative_evidence_alone_blocks_high(self, engine):
        """High requires the image to support it, not merely not object."""
        evidence = VisualEvidence(readings=[
            reading("world_region", "e_asia", "East Asia", 0.5),
        ])
        result = engine.fuse(
            [raw(48.8584, 2.2945, 0.20), raw(48.8590, 2.2950, 0.20)],
            evidence=evidence,
        )
        assert result.evidence_support < 0
        assert result.confidence != "high"

    def test_no_candidates_is_insufficient_not_a_crash(self, engine):
        result = engine.fuse([])
        assert result.confidence == "insufficient"
        assert result.candidates == []

    def test_diagnostics_are_reported(self, engine):
        result = engine.fuse([raw(48.8584, 2.2945, 0.1)])
        d = result.diagnostics()
        assert set(d) == {
            "locator_mass", "concentration", "dispersion_km", "evidence_support"
        }

    def test_returned_probabilities_sum_to_one(self, engine):
        result = engine.fuse([
            raw(48.85, 2.29, 0.04), raw(-33.87, 151.21, 0.03),
            raw(40.71, -74.01, 0.02),
        ])
        assert sum(c.probability for c in result.candidates) == pytest.approx(1.0)

    def test_candidate_count_is_capped(self, engine):
        cfg = FusionConfig()
        cfg.candidates_returned = 2
        engine.cfg = cfg
        result = engine.fuse([
            raw(48.85, 2.29, 0.04), raw(-33.87, 151.21, 0.03),
            raw(40.71, -74.01, 0.02), raw(35.68, 139.69, 0.01),
        ])
        assert len(result.candidates) == 2
