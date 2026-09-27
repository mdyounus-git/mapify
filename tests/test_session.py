"""Several photographs of one place.

The whole feature rests on one distinction, and these tests exist to hold
it: two photographs of a place are two observations and should sharpen
the answer, while two photographs of the same *thing* are one observation
and must not. Get that wrong in the generous direction and the tool
manufactures confidence by being shown the same sign twice.
"""

from __future__ import annotations

import math

import pytest

from mapify.models.base import GroupReading, RawCandidate, VisualEvidence
from mapify.session import (
    ABSENT_MASS,
    DUPLICATE_SIMILARITY,
    MATCH_RADIUS_KM,
    Observation,
    PlaceSession,
    _mass_near,
    _unit,
)


class FakeReport:
    """Just enough of an AnalysisReport for the session to work on."""

    def __init__(self, raw=None, embedding=None, evidence=None,
                 ok=True, confidence="low", lines=0):
        self.ok = ok
        self.error = None
        self.raw_candidates = raw or []
        self.embedding = embedding
        self.visual_evidence = evidence or VisualEvidence()
        self.ocr_result = None
        self.script_reading = None
        self.read_place_objs = []
        self.ocr = {"line_count": lines}
        self.prediction = {"confidence": confidence}


class FakeFusion:
    """Records what it was handed, so the combination can be inspected."""

    def __init__(self):
        self.seen = None

    def fuse(self, raw, evidence=None, ocr=None, script_reading=None,
             extra_candidates=None, read_places=None):
        from mapify.evidence.fusion import FusionResult

        self.seen = {"raw": list(raw), "evidence": evidence,
                     "read_places": read_places}
        return FusionResult(
            candidates=[], toponyms=[], confidence="medium",
            confidence_reason="", uncertainty_km=1.0, centroid=None,
        )


class FakeEngine:
    def __init__(self):
        self.fusion = FakeFusion()
        self.queue: list[FakeReport] = []

    def analyse(self, data, filename=None, progress=None,
                online_enrichment=True):
        return self.queue.pop(0)


def paris(score, jitter=0.0):
    return RawCandidate(48.8584 + jitter, 2.2945 + jitter, score, "geoclip")


def tokyo(score):
    return RawCandidate(35.6762, 139.6503, score, "geoclip")


class TestAgreementIsRewarded:
    def test_a_place_both_images_propose_beats_one_either_likes_alone(self):
        """The point of the whole feature, in one assertion.

        Image A is fairly sure about Tokyo and slightly open to Paris;
        image B is the reverse. Neither alone prefers Paris. Combined,
        Paris is the only place that explains both, and it wins.
        """
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[tokyo(0.50), paris(0.15)]),
            FakeReport(raw=[paris(0.16, 0.01),
                            RawCandidate(-33.87, 151.21, 0.48, "geoclip")]),
        ]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        session.add(b"b", "b.jpg")

        session.combine()
        joint = engine.fusion.seen["raw"]
        top = max(joint, key=lambda c: c.score)
        assert abs(top.latitude - 48.8584) < 1.0, (
            f"combined answer went to {top.latitude:.2f} instead of Paris"
        )

    def test_a_place_only_one_image_saw_is_pushed_down(self):
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[tokyo(0.80)]),
            FakeReport(raw=[paris(0.30)]),
        ]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        session.add(b"b", "b.jpg")
        session.combine()

        joint = {round(c.latitude): c.score for c in engine.fusion.seen["raw"]}
        # Neither is corroborated, so both end up near the absent floor
        # rather than one of them inheriting its single-image confidence.
        assert max(joint.values()) < 0.1, joint

    def test_the_scale_stays_comparable_to_one_image(self):
        """The confidence thresholds were fitted against single-image mass.

        A combination that inflated the numbers would quietly re-band
        every result, so the geometric mean is used rather than a sum.
        """
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[paris(0.40)]),
            FakeReport(raw=[paris(0.40, 0.01)]),
        ]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        session.add(b"b", "b.jpg")
        session.combine()

        top = max(engine.fusion.seen["raw"], key=lambda c: c.score)
        assert 0.3 < top.score < 0.55, (
            f"two images each at 0.40 combined to {top.score:.3f}; the scale "
            "has drifted away from what the bands were fitted on"
        )

    def test_the_source_records_how_many_images_supported_it(self):
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[paris(0.4)]),
            FakeReport(raw=[paris(0.4, 0.01)]),
            FakeReport(raw=[tokyo(0.4)]),
        ]
        session = PlaceSession(engine)
        for n in "abc":
            session.add(b"x", f"{n}.jpg")
        session.combine()

        by_lat = {round(c.latitude): c.source
                  for c in engine.fusion.seen["raw"]}
        assert by_lat[49] == "session:2of3"
        assert by_lat[36] == "session:1of3"


class TestDuplicatesAreNotEvidence:
    def test_the_same_photograph_twice_is_one_observation(self):
        """Squaring one piece of evidence is how this feature would lie."""
        shared = [0.3, 0.4, 0.5, 0.2]
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[paris(0.3)], embedding=shared),
            FakeReport(raw=[paris(0.3)], embedding=list(shared)),
        ]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        second = session.add(b"b", "b.jpg")

        assert second.counted is False
        assert second.duplicate_of == 0
        assert second.similarity == pytest.approx(1.0, abs=1e-6)
        assert len(session.counted()) == 1

    def test_one_observation_does_not_combine(self):
        """With nothing to combine, the single-image answer stands."""
        shared = [1.0, 0.0, 0.0]
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[paris(0.3)], embedding=shared),
            FakeReport(raw=[paris(0.3)], embedding=list(shared)),
        ]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        session.add(b"b", "b.jpg")
        assert session.combine() is None

    def test_genuinely_different_views_are_kept(self):
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[paris(0.3)], embedding=[1.0, 0.0, 0.0]),
            FakeReport(raw=[paris(0.3)], embedding=[0.0, 1.0, 0.0]),
        ]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        second = session.add(b"b", "b.jpg")
        assert second.counted is True
        assert len(session.counted()) == 2

    def test_the_threshold_sits_between_a_crop_and_a_new_angle(self):
        """Where the line is, stated as the two cases it separates."""
        assert 0.80 < DUPLICATE_SIMILARITY < 0.97

    def test_a_missing_embedding_never_silently_drops_an_image(self):
        """Failing open is right here: a lost duplicate check costs some
        confidence, a lost observation costs the evidence itself."""
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[paris(0.3)], embedding=None),
            FakeReport(raw=[paris(0.3)], embedding=None),
        ]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        session.add(b"b", "b.jpg")
        assert len(session.counted()) == 2

    def test_the_note_says_what_was_left_out(self):
        shared = [0.5, 0.5]
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[paris(0.3)], embedding=[1.0, 0.0]),
            FakeReport(raw=[paris(0.3, 0.01)], embedding=[0.0, 1.0]),
            FakeReport(raw=[paris(0.3)], embedding=[0.0, 1.0]),
        ]
        session = PlaceSession(engine)
        for n in "abc":
            session.add(b"x", f"{n}.jpg")
        result = session.combine()
        assert result is not None
        assert "too similar" in result.notes[0]
        assert "2 photographs" in result.notes[0]


class TestMergingEvidence:
    def test_the_image_that_could_see_it_speaks_for_all_of_them(self):
        """Three photographs of a wall must not dilute the one of a road."""
        def reading(conf, option):
            return GroupReading(
                group_key="traffic_side", group_title="Traffic direction",
                option_key=option, option_label=option, confidence=conf,
                margin=0.2, called=True, constrains=True,
            )

        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[paris(0.3)], embedding=[1.0, 0.0, 0.0],
                       evidence=VisualEvidence(readings=[reading(0.31, "left")])),
            FakeReport(raw=[paris(0.3, 0.01)], embedding=[0.0, 1.0, 0.0],
                       evidence=VisualEvidence(readings=[reading(0.88, "right")])),
        ]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        session.add(b"b", "b.jpg")
        session.combine()

        merged = engine.fusion.seen["evidence"]
        assert len(merged.readings) == 1
        assert merged.readings[0].option_key == "right"
        assert merged.readings[0].confidence == pytest.approx(0.88)

    def test_country_priors_combine_as_a_product(self):
        engine = FakeEngine()
        engine.queue = [
            FakeReport(raw=[paris(0.3)], embedding=[1.0, 0.0],
                       evidence=VisualEvidence(country_prior={"FR": 0.6, "JP": 0.4})),
            FakeReport(raw=[paris(0.3, 0.01)], embedding=[0.0, 1.0],
                       evidence=VisualEvidence(country_prior={"FR": 0.5, "JP": 0.05})),
        ]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        session.add(b"b", "b.jpg")
        session.combine()

        prior = engine.fusion.seen["evidence"].country_prior
        assert prior["FR"] > prior["JP"]
        # France is agreed on and Japan is not, so the gap must widen
        # rather than average out: 0.6/0.4 = 1.5 going in.
        assert prior["FR"] / prior["JP"] > 2.0
        assert sum(prior.values()) == pytest.approx(1.0, abs=1e-6)

    def test_only_the_most_readable_image_supplies_the_text(self):
        """Two angles on one sign would otherwise be two toponyms."""
        engine = FakeEngine()
        a = FakeReport(raw=[paris(0.3)], embedding=[1.0, 0.0], lines=2)
        b = FakeReport(raw=[paris(0.3, 0.01)], embedding=[0.0, 1.0], lines=9)
        a.ocr_result, b.ocr_result = "from-a", "from-b"
        engine.queue = [a, b]
        session = PlaceSession(engine)
        session.add(b"a", "a.jpg")
        session.add(b"b", "b.jpg")
        session.combine()
        assert session._pick_ocr(session.counted())[0] == "from-b"


class TestAsking:
    @pytest.mark.parametrize("confidence,expected", [
        ("insufficient", True), ("low", True),
        ("medium", False), ("high", False),
    ])
    def test_it_asks_only_when_asking_would_help(self, confidence, expected):
        session = PlaceSession(FakeEngine())
        assert session.should_ask_for_more(confidence) is expected

    def test_it_says_what_to_photograph_rather_than_what_it_lacks(self):
        session = PlaceSession(FakeEngine())
        ask = session.ask("insufficient")
        assert ask["helps_most"]
        joined = " ".join(ask["helps_most"]).lower()
        for cue in ("sign", "street", "horizon"):
            assert cue in joined

    def test_declining_is_offered_as_an_answer_not_a_failure(self):
        session = PlaceSession(FakeEngine())
        assert "ranked" in session.ask("insufficient")["decline"]

    def test_the_wording_differs_between_low_and_insufficient(self):
        session = PlaceSession(FakeEngine())
        assert (session.ask("low")["lede"]
                != session.ask("insufficient")["lede"])


class TestHelpers:
    def test_mass_near_sums_only_what_is_within_reach(self):
        pool = [paris(0.3), paris(0.2, 0.05), tokyo(0.4)]
        near = _mass_near(pool, 48.8584, 2.2945)
        assert near == pytest.approx(0.5, abs=1e-6)

    def test_absent_is_a_floor_not_a_zero(self):
        """A place one image did not shortlist is weak evidence against it,
        not proof against it: the shortlist is the top few of 100,000."""
        assert 0 < ABSENT_MASS < 0.01
        assert math.isfinite(math.log(ABSENT_MASS))

    def test_the_match_radius_is_wider_than_a_city(self):
        """Two photographs of one street can land their gallery mass
        kilometres apart; being strict would read agreement as conflict."""
        assert MATCH_RADIUS_KM >= 25.0

    def test_unit_handles_whatever_array_turns_up(self):
        assert _unit(None) is None
        assert _unit([0.0, 0.0]) is None
        v = _unit([3.0, 4.0])
        assert v == pytest.approx([0.6, 0.8])
