"""Several photographs of one place, combined.

When a single image will not support a location, Mapify says so and stops.
That is honest and it is not helpful: the person holding the phone usually
has another picture of the same place, and the second angle often contains
the thing the first one lacked.

The temptation is to analyse each image and average the answers. That is
wrong, and wrong in a way that matters. Averaging three finished answers
cannot tell agreement from coincidence -- three estimates that all say
"somewhere in France" average to the middle of France with no more
justification than any one of them had. What you want is the opposite
operation: treat each photograph as an *observation* of one unknown place,
and ask which place explains all of them.

For independent observations that is a product of likelihoods, which is a
sum in log space:

    score(x) = sum over images of log P_i(x)

and its behaviour is exactly right. A location every image puts mass on
multiplies up. A location only one image likes gets multiplied by the
others' near-zero and falls away. Agreement is rewarded; coincidence is
not.

The word doing the work is **independent**, and it is also where this can
go wrong. Two photographs of the same signboard are not two observations,
they are one observation photographed twice, and multiplying them squares
a single piece of evidence into false confidence. So near-duplicates are
detected and collapsed before any of this runs.

This is the same conclusion Phase 22 reached from the other direction:
more prompt groups against one embedding are correlated and do not help,
while genuinely separate looks at the world do.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from .evidence.fusion import FusionResult, cluster
from .geo.geodesy import haversine_km
from .models.base import GroupReading, RawCandidate, VisualEvidence

log = logging.getLogger("mapify.session")

__all__ = ["Observation", "PlaceSession", "SUGGESTIONS"]


# How close two candidate coordinates must be to count as the same place
# when matching one image's mass against another's. Wider than the fusion
# engine's own clustering radius on purpose: two photographs of one street
# can easily put their gallery mass a few kilometres apart, and being
# strict here would read genuine agreement as disagreement.
MATCH_RADIUS_KM = 60.0

# The mass an image is treated as having assigned to a place it did not
# propose at all. Not zero: a location absent from one image's shortlist
# is weak evidence against, not proof against, because the shortlist is
# only the top few of a hundred thousand gallery points.
ABSENT_MASS = 2e-3

# Cosine similarity above which two images are treated as one observation.
# Set from measurement rather than taste -- see tests/test_session.py,
# where a re-encode of the same photograph scores ~1.0, a crop of it
# ~0.95, and two genuinely different views of one building ~0.7.
DUPLICATE_SIMILARITY = 0.92

# What a person should go and photograph, in the order that helps most.
# Written as instructions rather than as a list of features, because the
# user is standing somewhere deciding where to point a camera.
SUGGESTIONS = (
    "a sign, a shopfront or a street name plate -- anything with words on it",
    "a wider view showing the street, with traffic or parked cars in it",
    "the horizon, or whatever the tallest thing around is",
    "the ground and the buildings together, rather than one close up",
)


@dataclass
class Observation:
    """One photograph, and what it turned out to be worth."""

    index: int
    name: str
    report: Any
    duplicate_of: int | None = None
    similarity: float = 0.0

    @property
    def counted(self) -> bool:
        return self.duplicate_of is None

    @property
    def ok(self) -> bool:
        return bool(getattr(self.report, "ok", False))

    def as_dict(self) -> dict[str, Any]:
        pred = getattr(self.report, "prediction", {}) or {}
        return {
            "index": self.index,
            "name": self.name,
            "ok": self.ok,
            "counted": self.counted,
            "duplicate_of": self.duplicate_of,
            "similarity": round(self.similarity, 4),
            "confidence": pred.get("confidence"),
            "error": getattr(self.report, "error", None),
        }


class PlaceSession:
    """Photographs the user asserts are of one place.

    The assertion is the user's, and it is load-bearing: nothing here
    checks that two pictures really are of the same street, and combining
    images of different places would produce a confident answer about
    neither. The interface asks the question in those terms, and this
    class trusts the answer.
    """

    def __init__(self, engine: Any):
        self.engine = engine
        self.observations: list[Observation] = []

    # ------------------------------------------------------------- adding

    def add(self, data: bytes, filename: str | None = None,
            progress: Any = None) -> Observation:
        """Analyse one more photograph of the same place."""
        report = self.engine.analyse(
            data, filename, progress, keep_distribution=True,
            online_enrichment=False,
        )
        obs = Observation(
            index=len(self.observations),
            name=filename or f"image {len(self.observations) + 1}",
            report=report,
        )
        self._mark_if_duplicate(obs)
        self.observations.append(obs)
        return obs

    def _mark_if_duplicate(self, obs: Observation) -> None:
        """Is this the same photograph again, or the same thing photographed?

        Multiplying two views of one signboard squares a single piece of
        evidence and manufactures confidence out of nothing. The image
        embedding is already computed for every analysis, so the check is
        a dot product and costs nothing.
        """
        new = _unit(getattr(obs.report, "embedding", None))
        if new is None:
            return
        for prior in self.observations:
            if not prior.counted:
                continue
            old = _unit(getattr(prior.report, "embedding", None))
            if old is None:
                continue
            similarity = float(sum(a * b for a, b in zip(new, old)))
            if similarity >= DUPLICATE_SIMILARITY:
                obs.duplicate_of = prior.index
                obs.similarity = similarity
                log.info(
                    "%s duplicates %s (cosine %.3f); it will not be counted "
                    "twice", obs.name, prior.name, similarity,
                )
                return
            obs.similarity = max(obs.similarity, similarity)

    # ------------------------------------------------------------ fusing

    def counted(self) -> list[Observation]:
        return [o for o in self.observations if o.counted and o.ok]

    def combine(self) -> FusionResult | None:
        """Ask which single place explains every photograph.

        Returns None when there is nothing to combine, in which case the
        caller should keep whatever the single-image analysis said rather
        than invent a session result.
        """
        usable = self.counted()
        if not usable:
            return None
        if len(usable) == 1:
            return None          # one observation is not a combination

        pools = [list(getattr(o.report, "raw_candidates", []) or [])
                 for o in usable]
        if not any(pools):
            return None
        # The locator's full opinion, where we have it. Without this the
        # product rule degenerates: two images rarely shortlist the same
        # gallery points, so every place would be "absent" from all but
        # one image and the answer would go to whichever image was most
        # confident rather than to the place they agree on. Measured: on
        # 24 photographs cut in half, shortlists-only beat the better
        # half 1 time in 24.
        dists = [getattr(o.report, "gallery_distribution", None)
                 for o in usable]
        locator = getattr(self.engine, "geoclip", None)

        # Cluster everything every image proposed, so the same place
        # suggested by three images is one candidate rather than three.
        merged = [c for pool in pools for c in pool]
        clusters = cluster(merged, MATCH_RADIUS_KM)

        joint: list[RawCandidate] = []
        for cand in clusters:
            total = 0.0
            supporters = 0
            for index, pool in enumerate(pools):
                dist = dists[index]
                if dist is not None and locator is not None:
                    mass = locator.mass_near(
                        dist, cand.latitude, cand.longitude, MATCH_RADIUS_KM
                    )
                else:
                    mass = _mass_near(pool, cand.latitude, cand.longitude)
                if mass > ABSENT_MASS:
                    supporters += 1
                total += math.log(max(mass, ABSENT_MASS))
            joint.append(
                RawCandidate(
                    latitude=cand.latitude,
                    longitude=cand.longitude,
                    # Geometric mean, so the scale stays comparable to a
                    # single image's masses and the existing confidence
                    # thresholds keep the meaning they were fitted with.
                    score=math.exp(total / len(pools)),
                    source=f"session:{supporters}of{len(pools)}",
                )
            )

        joint.sort(key=lambda c: -c.score)
        evidence = self._merge_evidence(usable)
        ocr, script_reading = self._pick_ocr(usable)
        read_places = [
            p for o in usable
            for p in (getattr(o.report, "read_place_objs", []) or [])
        ]

        result = self.engine.fusion.fuse(
            joint,
            evidence=evidence,
            ocr=ocr,
            script_reading=script_reading,
            read_places=read_places or None,
        )
        result.notes.insert(0, self._provenance_note(usable))
        return result

    def _merge_evidence(self, usable: list[Observation]) -> VisualEvidence:
        """One set of readings from several looks at the same place.

        Per group, the most confident reading wins: the photograph that
        could actually see the traffic gets to say which side it was on,
        and the three that were pointed at a wall do not dilute it.

        The country prior is a distribution, so it combines the same way
        the coordinates do -- as a product across observations.
        """
        best: dict[str, GroupReading] = {}
        for obs in usable:
            evidence = getattr(obs.report, "visual_evidence", None)
            for reading in getattr(evidence, "readings", []) or []:
                held = best.get(reading.group_key)
                if held is None or reading.confidence > held.confidence:
                    best[reading.group_key] = reading

        priors = [
            getattr(o.report, "visual_evidence", None) for o in usable
        ]
        countries: set[str] = set()
        for evidence in priors:
            countries |= set(getattr(evidence, "country_prior", {}) or {})

        combined: dict[str, float] = {}
        for iso in countries:
            total = 0.0
            for evidence in priors:
                prior = getattr(evidence, "country_prior", {}) or {}
                total += math.log(max(prior.get(iso, 0.0), 1e-6))
            combined[iso] = math.exp(total / max(1, len(priors)))
        scale = sum(combined.values())
        if scale > 0:
            combined = {k: v / scale for k, v in combined.items()}

        return VisualEvidence(
            readings=list(best.values()),
            country_prior=combined,
            model=next(
                (getattr(e, "model", None) for e in priors
                 if getattr(e, "model", None)), None
            ),
        )

    @staticmethod
    def _pick_ocr(usable: list[Observation]) -> tuple[Any, Any]:
        """The image that read the most text speaks for all of them.

        Concatenating the text from every photograph would let one sign
        read twice from two angles count as two independent toponyms,
        which is the duplicate problem again in another costume.
        """
        best_obs = None
        best_lines = -1
        for obs in usable:
            lines = (getattr(obs.report, "ocr", {}) or {}).get("line_count", 0)
            if lines > best_lines:
                best_lines, best_obs = lines, obs
        if best_obs is None or best_lines <= 0:
            return None, None
        return (
            getattr(best_obs.report, "ocr_result", None),
            getattr(best_obs.report, "script_reading", None),
        )

    def _provenance_note(self, usable: list[Observation]) -> str:
        duplicates = [o for o in self.observations if not o.counted]
        note = (
            f"Combined from {len(usable)} photographs the user states are of "
            f"one place. Agreement between separate images is stronger than "
            f"any one of them, which is why this can say more than the "
            f"individual analyses did."
        )
        if duplicates:
            note += (
                f" {len(duplicates)} further image"
                f"{'s were' if len(duplicates) > 1 else ' was'} too similar "
                f"to count as separate evidence and "
                f"{'were' if len(duplicates) > 1 else 'was'} left out."
            )
        return note

    # ------------------------------------------------------------ asking

    def should_ask_for_more(self, confidence: str) -> bool:
        return confidence in {"insufficient", "low"}

    def ask(self, confidence: str) -> dict[str, Any]:
        """What to say when a photograph will not place itself."""
        counted = len(self.counted())
        if confidence == "insufficient":
            lede = ("Nothing in this photograph places it.")
        else:
            lede = ("This photograph narrows it down but will not place it.")
        return {
            "ask": True,
            "counted": counted,
            "lede": lede,
            "question": (
                "Do you have another picture of the same place?"
                if counted <= 1 else
                "Another picture of the same place would still help."
            ),
            "helps_most": list(SUGGESTIONS),
            "decline": (
                "If that is the only picture there is, Mapify will give the "
                "possibilities it has, ranked, with what each one rests on."
            ),
        }


def _unit(vector: Any) -> list[float] | None:
    """A plain normalised list, whatever array type turned up."""
    if vector is None:
        return None
    try:
        values = [float(v) for v in list(vector)]
    except (TypeError, ValueError):
        try:                                    # a torch tensor, maybe
            values = [float(v) for v in vector.flatten().tolist()]
        except Exception:
            return None
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 0:
        return None
    return [v / norm for v in values]


def _mass_near(pool: list[RawCandidate], lat: float, lon: float) -> float:
    """How much probability one image put within reach of this place."""
    total = 0.0
    for cand in pool:
        if haversine_km(lat, lon, cand.latitude, cand.longitude) <= MATCH_RADIUS_KM:
            total += max(0.0, cand.score)
    return total
