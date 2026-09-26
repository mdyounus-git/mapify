"""Evidence-driven re-ranking.

The locator proposes coordinates. Everything else in the image -- the script on
the signs, which side the traffic keeps to, the vegetation, a road number, a
place name -- is used to re-rank those proposals, never to invent them, with
one deliberate exception: a place name read off a sign and matched in the
gazetteer earns its own candidate, because that is genuine positional evidence
rather than a guess.

Every adjustment is recorded with its reason and its size, so the report can
show precisely what moved a candidate and by how much. An analyst who
disagrees with a weight can see exactly what it did.

Scores are combined in log space:

    score' = score * exp(sum of adjustments)

with the total clamped, so no pile-up of weak agreements can overwhelm what the
locator actually saw.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..config import FusionConfig
from ..data.countries import CountryTable, get_table
from ..evidence import prompts as P
from ..evidence.script_detect import ScriptReading, tokens
from ..geo.geodesy import containment_radius_km, haversine_km, weighted_centroid
from ..geo.offline import GazetteerMatch, OfflineGeocoder, OfflineLocation
from ..models.base import OcrResult, RawCandidate, VisualEvidence


@dataclass
class Adjustment:
    """One piece of evidence acting on one candidate."""

    source: str        # "traffic_side", "toponym", "country_prior", ...
    detail: str        # human-readable reason
    delta: float       # log-space adjustment actually applied
    agreement: float   # -1 .. +1

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "detail": self.detail,
            "delta": round(self.delta, 4),
            "agreement": round(self.agreement, 3),
            "direction": "supports" if self.delta > 0 else
                         ("contradicts" if self.delta < 0 else "neutral"),
        }


@dataclass
class Candidate:
    latitude: float
    longitude: float
    prior_score: float          # what the locator gave it
    score: float                # after fusion, before normalisation
    probability: float = 0.0    # normalised across candidates
    sources: list[str] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    location: OfflineLocation | None = None
    # Filled in later by the enrichment step.
    enrichment: dict[str, Any] = field(default_factory=dict)
    cluster_size: int = 1
    spread_km: float = 0.0
    provenance: str = "model"   # "model" | "text" | "exif"

    @property
    def iso_a2(self) -> str | None:
        if self.location and self.location.country:
            return self.location.country.iso_a2
        return None

    @property
    def total_adjustment(self) -> float:
        return sum(a.delta for a in self.adjustments)

    def as_dict(self) -> dict[str, Any]:
        return {
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "probability": round(self.probability, 5),
            "prior_score": round(self.prior_score, 6),
            "provenance": self.provenance,
            "sources": self.sources,
            "cluster_size": self.cluster_size,
            "spread_km": round(self.spread_km, 1),
            "total_adjustment": round(self.total_adjustment, 4),
            "adjustments": [a.as_dict() for a in self.adjustments],
            "location": self.location.as_dict() if self.location else None,
            **self.enrichment,
        }


@dataclass
class ToponymHit:
    """A place name read off the image and found in the gazetteer."""

    text: str
    ocr_confidence: float
    match: GazetteerMatch
    corroborated_km: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "ocr_confidence": round(self.ocr_confidence, 3),
            "matched_name": self.match.name,
            "matched_field": self.match.matched_field,
            "language": self.match.language,
            "latitude": self.match.latitude,
            "longitude": self.match.longitude,
            "admin0": self.match.admin0,
            "admin1": self.match.admin1,
            "population": self.match.population,
            "corroborated_km": (
                round(self.corroborated_km, 1)
                if self.corroborated_km is not None else None
            ),
        }


@dataclass
class FusionResult:
    candidates: list[Candidate]
    toponyms: list[ToponymHit]
    confidence: str            # high | medium | low | insufficient
    confidence_reason: str
    uncertainty_km: float
    centroid: tuple[float, float] | None
    notes: list[str] = field(default_factory=list)
    # Places resolved from text read in the image, in the order they were
    # believed. Kept on the result so the report can show what was read and
    # what it was matched against, rather than only where it landed.
    read_places: list[Any] = field(default_factory=list)
    # Diagnostics the confidence band is actually built from. Reported so the
    # banding can be argued with rather than taken on faith.
    locator_mass: float = 0.0       # share of the locator's full distribution
    concentration: float = 0.0      # share of that mass in the top cluster
    dispersion_km: float = 0.0      # spread of all clusters, not just returned
    evidence_support: float = 0.0   # net log-odds the evidence gave the winner

    def diagnostics(self) -> dict[str, Any]:
        return {
            "locator_mass": round(self.locator_mass, 5),
            "concentration": round(self.concentration, 4),
            "dispersion_km": round(self.dispersion_km, 1),
            "evidence_support": round(self.evidence_support, 3),
        }


# --------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------

def cluster(raw: Iterable[RawCandidate], radius_km: float
            ) -> list[Candidate]:
    """Merge coordinates that are effectively the same place.

    GeoCLIP's gallery is dense, so its top-k is usually several points in one
    city. Treating those as separate candidates would understate confidence.
    """
    clusters: list[dict[str, Any]] = []
    for item in sorted(raw, key=lambda c: -c.score):
        placed = False
        for cl in clusters:
            if haversine_km(cl["lat"], cl["lon"], item.latitude, item.longitude) <= radius_km:
                cl["members"].append(item)
                total = cl["score"] + item.score
                if total > 0:
                    # Probability-weighted centroid keeps the anchor honest.
                    w_old = cl["score"] / total
                    w_new = item.score / total
                    lat, lon = weighted_centroid([
                        (cl["lat"], cl["lon"], w_old),
                        (item.latitude, item.longitude, w_new),
                    ])
                    cl["lat"], cl["lon"] = lat, lon
                cl["score"] = total
                placed = True
                break
        if not placed:
            clusters.append({
                "lat": item.latitude,
                "lon": item.longitude,
                "score": item.score,
                "members": [item],
            })

    out: list[Candidate] = []
    for cl in clusters:
        members = cl["members"]
        spread = 0.0
        for m in members:
            spread = max(spread, haversine_km(cl["lat"], cl["lon"],
                                              m.latitude, m.longitude))
        out.append(
            Candidate(
                latitude=cl["lat"],
                longitude=cl["lon"],
                prior_score=cl["score"],
                score=cl["score"],
                sources=sorted({m.source for m in members}),
                cluster_size=len(members),
                spread_km=spread,
            )
        )
    out.sort(key=lambda c: -c.score)
    return out


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------

class FusionEngine:
    def __init__(self, cfg: FusionConfig, geocoder: OfflineGeocoder,
                 table: CountryTable | None = None):
        self.cfg = cfg
        self.geocoder = geocoder
        self.table = table or get_table()

    # ------------------------------------------------------------ toponyms

    def find_toponyms(self, ocr: OcrResult, min_conf: float = 0.35,
                      limit: int = 6) -> list[ToponymHit]:
        """Place names read off the image that exist in the gazetteer."""
        hits: list[ToponymHit] = []
        seen: set[str] = set()
        for line in ocr.lines:
            if line.confidence < min_conf:
                continue
            # Try the whole line first: "SAINT PETERSBURG" beats "SAINT".
            candidates_text = [line.text.strip()]
            candidates_text.extend(tokens(line.text, min_len=4))
            for text in candidates_text:
                key = text.casefold()
                if key in seen or len(key) < 3:
                    continue
                matches = self.geocoder.find_place(text, limit=3)
                if not matches:
                    continue
                seen.add(key)
                hits.append(
                    ToponymHit(
                        text=text,
                        ocr_confidence=line.confidence,
                        match=matches[0],
                    )
                )
                break
            if len(hits) >= limit:
                break
        # A bigger settlement is a safer read of an ambiguous name.
        hits.sort(key=lambda h: -((h.match.population or 0) * h.ocr_confidence))
        return hits[:limit]

    # ------------------------------------------------------------- scoring

    def _script_agreement(self, iso2: str | None, script: str) -> float:
        country = self.table.get(iso2)
        if not country:
            return 0.0
        if script in country.scripts:
            # Latin is near-universal, so agreeing with it proves little.
            if script == "Latn":
                return 0.25
            # Agreement with a rare script is strong.
            users = len(self.table.countries_using_script(script))
            return 1.0 if users <= 12 else 0.7
        return -1.0 if script != "Latn" else -0.35

    def _lat_band_agreement(self, lat: float, band: tuple[float, float]) -> float:
        lo, hi = band
        a = abs(lat)
        if lo <= a <= hi:
            return 1.0
        # Degrade smoothly: 10 degrees outside the band is full disagreement.
        outside = (lo - a) if a < lo else (a - hi)
        return max(-1.0, -outside / 10.0)

    def _apply_visual(self, cand: Candidate, evidence: VisualEvidence) -> None:
        cfg = self.cfg
        country = self.table.get(cand.iso_a2)

        for reading in evidence.readings:
            if not reading.called:
                continue
            group = P.GROUPS_BY_KEY.get(reading.group_key)
            if not group:
                continue
            option = next(
                (o for o in group.options if o.key == reading.option_key), None
            )
            if option is None or not option.constrains:
                continue

            weight = getattr(cfg, group.weight_key, 0.0)
            if weight <= 0:
                continue

            agreement = 0.0
            detail = ""

            if option.drive_side and country:
                agreement = 1.0 if country.drive_side == option.drive_side else -1.0
                detail = (
                    f"{reading.option_label} vs {country.name} driving on the "
                    f"{country.drive_side}"
                )
            elif option.script:
                agreement = self._script_agreement(cand.iso_a2, option.script)
                detail = (
                    f"{reading.option_label} vs signage scripts of "
                    f"{country.name if country else 'unknown country'}"
                )
            elif option.plate_class and country:
                # A plate mismatch is weaker evidence than a match: plates are
                # often too small or too blurred to read correctly.
                agreement = 1.0 if country.plate_class == option.plate_class else -0.5
                detail = f"{reading.option_label} vs {country.plate_class}"
            elif option.countries:
                if country:
                    agreement = 1.0 if country.iso_a2 in option.countries else -0.8
                    detail = f"{reading.option_label} vs {country.name}"
            elif option.subregions or option.continents:
                if country:
                    inside = (
                        (country.subregion in option.subregions)
                        or (country.continent in option.continents)
                    )
                    agreement = 1.0 if inside else -1.0
                    detail = (
                        f"{reading.option_label} vs {country.subregion or '?'}"
                    )
            elif option.lat_band:
                agreement = self._lat_band_agreement(cand.latitude, option.lat_band)
                detail = (
                    f"{reading.option_label} vs latitude "
                    f"{abs(cand.latitude):.1f} degrees"
                )

            if agreement == 0.0:
                continue
            delta = weight * reading.confidence * agreement
            cand.adjustments.append(
                Adjustment(
                    source=reading.group_key,
                    detail=detail or reading.option_label,
                    delta=delta,
                    agreement=agreement,
                )
            )

    def _apply_country_prior(self, cand: Candidate, prior: dict[str, float]) -> None:
        if not prior or not cand.iso_a2:
            return
        p_max = max(prior.values())
        if p_max <= 0:
            return
        values = sorted(prior.values(), reverse=True)
        # How peaked is the prior? A flat prior is not evidence.
        median = values[len(values) // 2]
        peakedness = min(1.0, (p_max - median) / max(p_max, 1e-9))
        if peakedness < 0.25:
            return
        p = prior.get(cand.iso_a2, 0.0)
        agreement = 2.0 * (p / p_max) - 1.0
        delta = self.cfg.w_region_hint * peakedness * agreement
        top_iso = max(prior, key=lambda k: prior[k])
        top_country = self.table.get(top_iso)
        cand.adjustments.append(
            Adjustment(
                source="country_prior",
                detail=(
                    f"CLIP country prior favours "
                    f"{top_country.name if top_country else top_iso} "
                    f"({p_max:.0%}); this candidate scores {p:.0%}"
                ),
                delta=delta,
                agreement=agreement,
            )
        )

    def _apply_script_reading(self, cand: Candidate, reading: ScriptReading) -> None:
        """OCR-derived scripts are stronger evidence than the CLIP guess."""
        if not reading.total_letters:
            return
        for script, proportion in reading.dominant(minimum=0.20):
            if script in {"Hira", "Kana"}:
                continue  # folded into Jpan already
            agreement = self._script_agreement(cand.iso_a2, script)
            if agreement == 0.0:
                continue
            from .script_detect import SCRIPT_NAMES

            delta = self.cfg.w_script * proportion * agreement
            cand.adjustments.append(
                Adjustment(
                    source="ocr_script",
                    detail=(
                        f"OCR read {proportion:.0%} of characters as "
                        f"{SCRIPT_NAMES.get(script, script)}"
                    ),
                    delta=delta,
                    agreement=agreement,
                )
            )

    def _apply_language_hints(self, cand: Candidate, reading: ScriptReading
                              ) -> None:
        """Language-marking characters in the recognised text.

        Script tells you the writing system; these tell you the language
        inside it, which is far more discriminating. Latin covers half the
        world, but a sign reading MECBURI ISTIKAMET OTOBUSLERI carries
        characters only a handful of languages use.
        """
        if not reading.language_hints:
            return
        country = self.table.get(cand.iso_a2)
        if not country or not country.languages:
            return

        best_lang, best_weight = None, 0.0
        for lang, weight in reading.language_hints.items():
            if lang in country.languages and weight > best_weight:
                best_lang, best_weight = lang, weight

        if best_lang:
            # A marker shared by many languages proves less than a rare one.
            speakers = len(self.table.countries_speaking(best_lang))
            agreement = 1.0 if speakers <= 3 else (0.7 if speakers <= 8 else 0.4)
            detail = (
                f"Sign text carries characters specific to {best_lang!r}, "
                f"which {country.name} signs in"
            )
        else:
            top_lang, top_weight = max(
                reading.language_hints.items(), key=lambda kv: kv[1]
            )
            # Only count a mismatch when the hint is strong and narrow.
            speakers = len(self.table.countries_speaking(top_lang))
            if top_weight < 0.9 or speakers == 0 or speakers > 8:
                return
            best_weight = top_weight
            agreement = -0.7
            detail = (
                f"Sign text looks like {top_lang!r}, which is not a signage "
                f"language of {country.name}"
            )

        cand.adjustments.append(
            Adjustment(
                source="ocr_language",
                detail=detail,
                delta=self.cfg.w_script * 0.8 * best_weight * agreement,
                agreement=agreement,
            )
        )

    def _apply_toponyms(self, cand: Candidate, hits: list[ToponymHit]) -> None:
        for hit in hits:
            d = haversine_km(
                cand.latitude, cand.longitude,
                hit.match.latitude, hit.match.longitude,
            )
            if d <= 60.0:
                agreement = 1.0
                detail = (
                    f"Sign text {hit.text!r} matches {hit.match.name} "
                    f"({hit.match.matched_field}), {d:.0f} km away"
                )
            elif d <= 250.0:
                agreement = 0.35
                detail = (
                    f"Sign text {hit.text!r} matches {hit.match.name}, "
                    f"{d:.0f} km away -- same region"
                )
            else:
                # A confident place name a long way off is real evidence
                # against this candidate.
                agreement = -0.6
                detail = (
                    f"Sign text {hit.text!r} matches {hit.match.name}, "
                    f"{d:.0f} km away -- inconsistent"
                )
            delta = self.cfg.w_toponym * min(1.0, hit.ocr_confidence + 0.25) * agreement
            cand.adjustments.append(
                Adjustment(
                    source="toponym",
                    detail=detail,
                    delta=delta,
                    agreement=agreement,
                )
            )
            if agreement > 0 and (
                hit.corroborated_km is None or d < hit.corroborated_km
            ):
                hit.corroborated_km = d

    # ---------------------------------------------------------------- main

    def fuse(
        self,
        raw: list[RawCandidate],
        evidence: VisualEvidence | None = None,
        ocr: OcrResult | None = None,
        script_reading: ScriptReading | None = None,
        extra_candidates: list[RawCandidate] | None = None,
        read_places: list[Any] | None = None,
    ) -> FusionResult:
        cfg = self.cfg
        notes: list[str] = []

        toponyms: list[ToponymHit] = []
        if ocr and ocr.lines:
            toponyms = self.find_toponyms(ocr)

        pool = list(raw)
        if extra_candidates:
            pool.extend(extra_candidates)

        # A confidently-read place name deserves its own candidate, capped so
        # it must still be corroborated to win outright.
        if toponyms and pool:
            ceiling = max(c.score for c in pool)
            for hit in toponyms[:3]:
                strength = min(1.0, hit.ocr_confidence) * (
                    0.85 if (hit.match.population or 0) > 100_000 else 0.55
                )
                pool.append(
                    RawCandidate(
                        latitude=hit.match.latitude,
                        longitude=hit.match.longitude,
                        score=ceiling * strength,
                        source="ocr-toponym",
                    )
                )

        if not pool:
            return FusionResult(
                candidates=[], toponyms=toponyms,
                confidence="insufficient",
                confidence_reason="No locator produced any candidate.",
                uncertainty_km=0.0, centroid=None,
                notes=["No candidates were produced, so no location is reported."],
            )

        candidates = cluster(pool, cfg.cluster_radius_km)

        # Clustering averages its members, which is right for a handful of
        # fuzzy gallery points in one city and wrong for a street address.
        # A geocoded building is a *point*; blending it with a model guess
        # 20 km away moves the answer off the building for no gain. On the
        # board that prompted this, that averaging alone cost 2.9 km.
        #
        # So where a cluster contains a corroborated read address, the
        # cluster reports the address's own coordinate.
        if read_places:
            precise = [
                p for p in read_places
                if p.corroborated
                and p.result.specificity in ("building", "street")
            ]
            for cand in candidates:
                if "read-address" not in cand.sources or not precise:
                    continue
                # The best-corroborated match within the cluster, not the
                # one that happens to sit nearest the averaged centroid: a
                # hospital and its blood bank are both in range, and the
                # ranking already knows which is the better read.
                in_range = [
                    p for p in precise
                    if haversine_km(cand.latitude, cand.longitude,
                                    p.result.latitude,
                                    p.result.longitude) <= cfg.cluster_radius_km
                ]
                if not in_range:
                    continue
                nearest = max(in_range, key=lambda p: p.rank)
                moved = haversine_km(
                    cand.latitude, cand.longitude,
                    nearest.result.latitude, nearest.result.longitude,
                )
                if moved <= cfg.cluster_radius_km:
                    cand.latitude = nearest.result.latitude
                    cand.longitude = nearest.result.longitude
                    # The spread is still real -- the model's own points are
                    # that far away -- and must not be quietly reset.
                    cand.spread_km = max(cand.spread_km, moved)

        # Reverse geocode before scoring: most adjustments need the country.
        for cand in candidates[: max(cfg.candidates_returned * 3, 12)]:
            cand.location = self.geocoder.locate(
                cand.latitude, cand.longitude, place_limit=4
            )
            text_sources = {"ocr-toponym", "read-address"}
            if text_sources & set(cand.sources):
                cand.provenance = (
                    "text" if set(cand.sources) <= text_sources else "model+text"
                )

        # Apply every evidence channel.
        for cand in candidates:
            if cand.location is None:
                continue
            if evidence:
                self._apply_visual(cand, evidence)
                self._apply_country_prior(cand, evidence.country_prior)
            if script_reading:
                self._apply_script_reading(cand, script_reading)
                self._apply_language_hints(cand, script_reading)
            if toponyms:
                self._apply_toponyms(cand, toponyms)

            total = max(
                -cfg.max_total_adjustment,
                min(cfg.max_total_adjustment, cand.total_adjustment),
            )
            cand.score = max(1e-12, cand.prior_score * math.exp(total))

        candidates.sort(key=lambda c: -c.score)

        # Measure spread over *every* cluster, before truncation. Trimming to
        # the returned five and then measuring their spread would quietly
        # discard the evidence that the model was unsure.
        all_points = [(c.latitude, c.longitude, max(c.score, 0.0))
                      for c in candidates]
        full_centroid = weighted_centroid(all_points) if all_points else None
        dispersion = (
            containment_radius_km(all_points, full_centroid, mass=0.68)
            if full_centroid else 0.0
        )

        # How much of the locator's own distribution we are looking at, and
        # how much of that sits in the winning cluster. With one cluster the
        # normalised probability is trivially 1.0, so it cannot carry the
        # confidence call on its own.
        # Mass means "how much of its distribution the *visual locator*
        # committed". Candidates we injected from read text are not the
        # locator's opinion and must not inflate it -- otherwise reading a
        # sign would raise confidence in the image evidence, which is
        # exactly backwards.
        _injected = {"ocr-toponym", "read-address"}
        locator_mass = sum(
            max(c.score, 0.0) for c in pool if c.source not in _injected
        )
        post_total = sum(max(c.score, 0.0) for c in candidates)
        concentration = (
            max(candidates[0].score, 0.0) / post_total if post_total > 0 else 0.0
        )

        candidates = candidates[: max(1, cfg.candidates_returned)]
        total = sum(c.score for c in candidates)
        for c in candidates:
            c.probability = c.score / total if total > 0 else 0.0

        points = [(c.latitude, c.longitude, c.probability) for c in candidates]
        centroid = weighted_centroid(points) if points else None
        uncertainty = max(
            dispersion,
            containment_radius_km(points, centroid, mass=0.68) if centroid else 0.0,
        )
        # A lone cluster has no observable spread between candidates, so use
        # the spread of the raw points that formed it.
        if len(candidates) == 1:
            uncertainty = max(uncertainty, candidates[0].spread_km)

        support = sum(a.delta for a in candidates[0].adjustments)

        # Did an address read off the image win, and if so, does the visual
        # estimate agree with it? Both questions matter to the band.
        read_leader = None
        disagreement_km = None
        if read_places and "read-address" in candidates[0].sources:
            leader = candidates[0]
            nearest = min(
                read_places,
                key=lambda p: haversine_km(
                    leader.latitude, leader.longitude,
                    p.result.latitude, p.result.longitude,
                ),
            )
            read_leader = nearest.as_dict()
            model_points = [
                (c.latitude, c.longitude, max(c.score, 0.0))
                for c in candidates if "read-address" not in c.sources
            ]
            if model_points:
                model_centroid = weighted_centroid(model_points)
                if model_centroid:
                    gap = haversine_km(
                        leader.latitude, leader.longitude, *model_centroid
                    )
                    # A gap only counts as disagreement if it is bigger than
                    # the model's own scatter. When GeoCLIP has spread its
                    # candidates over a thousand kilometres it has not
                    # contradicted anything -- it simply has no opinion, and
                    # reporting that as a conflict would frighten someone
                    # away from a correct answer.
                    model_spread = containment_radius_km(
                        model_points, model_centroid, mass=0.68
                    )
                    # Only a genuine conflict counts. A model that has
                    # scattered its candidates across a region has not
                    # contradicted anything -- it has no opinion -- and
                    # calling that a conflict would push someone away from
                    # a correct answer. Different countries always count.
                    leader_cc = leader.iso_a2
                    model_cc = None
                    for c in candidates:
                        if "read-address" not in c.sources:
                            model_cc = c.iso_a2
                            break
                    different_country = bool(
                        leader_cc and model_cc and leader_cc != model_cc
                    )
                    if different_country or gap > max(500.0, 3.0 * model_spread):
                        disagreement_km = gap

        confidence, reason, extra_notes = self._band(
            candidates, uncertainty, locator_mass, concentration, support,
            read_leader=read_leader, disagreement_km=disagreement_km,
        )
        notes.extend(extra_notes)

        if confidence == "insufficient":
            notes.append(
                "The evidence does not support naming a location. The "
                "candidates below are what the model produced, not a finding."
            )
        if any(c.provenance.endswith("text") for c in candidates[:1]):
            notes.append(
                "The leading candidate rests partly on text read from the "
                "image. Check the OCR output before relying on it."
            )

        return FusionResult(
            candidates=candidates,
            toponyms=toponyms,
            read_places=list(read_places or []),
            confidence=confidence,
            confidence_reason=reason,
            uncertainty_km=uncertainty,
            centroid=centroid,
            notes=notes,
            locator_mass=locator_mass,
            concentration=concentration,
            dispersion_km=dispersion,
            evidence_support=support,
        )

    _ORDER = ("insufficient", "low", "medium", "high")

    def _band(
        self,
        candidates: list[Candidate],
        uncertainty_km: float,
        locator_mass: float,
        concentration: float,
        evidence_support: float,
        read_leader: dict[str, Any] | None = None,
        disagreement_km: float | None = None,
    ) -> tuple[str, str, list[str]]:
        """Decide how much to claim.

        The rule here is not intuition. It was fitted over a 163-sample
        benchmark by scripts/tune_confidence.py, searching on half the samples
        and validating on the other half.

        The previous rule led with dispersion and treated locator mass as a
        mild downgrade, which let results 15,689 km off target be called
        "high confidence". The data says mass is the primary separator: below
        0.10 only a third of predictions land within 200 km, above 0.25 nearly
        all of them do. Concentration, which the old rule leaned on hardest,
        barely separates correct from incorrect at all.

        "High" therefore requires all four conditions, not a majority of them.
        """
        cfg = self.cfg
        notes: list[str] = []
        if not candidates:
            return "insufficient", "No candidates were produced.", notes

        # 1. Nothing recognised. Said plainly, before anything else.
        if locator_mass > 0 and locator_mass < cfg.min_locator_mass:
            return (
                "insufficient",
                f"The locator committed only {locator_mass:.1%} of its "
                f"probability to its entire shortlist. It did not recognise "
                f"anything specific in this image.",
                ["No location is reported. The candidates below are what the "
                 "model produced, not a finding."],
            )

        # 2. High: all four, or not at all.
        high_checks = {
            "mass": locator_mass >= cfg.high_locator_mass,
            "spread": uncertainty_km <= cfg.high_spread_km,
            "concentration": concentration >= cfg.high_concentration,
            "evidence": evidence_support >= cfg.high_min_evidence,
        }
        if all(high_checks.values()):
            return (
                "high",
                f"The locator put {locator_mass:.0%} of its distribution into "
                f"a shortlist that agrees to within {uncertainty_km:.0f} km, "
                f"and the rest of the image supports it.",
                notes,
            )

        # 3. Medium: a region, not a location.
        if (locator_mass >= cfg.medium_locator_mass
                and uncertainty_km <= cfg.medium_spread_km
                and concentration >= cfg.medium_concentration):
            band = "medium"
            missing = [k for k, ok in high_checks.items() if not ok]
            reason = (
                f"Candidates span {uncertainty_km:.0f} km with "
                f"{locator_mass:.0%} of the locator's distribution behind "
                f"them. Short of high confidence on: {', '.join(missing)}."
            )
        elif uncertainty_km <= cfg.insufficient_spread_km:
            band = "low"
            reason = (
                f"Candidates are spread over {uncertainty_km:.0f} km with "
                f"{locator_mass:.0%} of the locator's distribution behind "
                f"them. Treat this as a lead, not a location."
            )
        else:
            band = "insufficient"
            reason = (
                f"Candidates are scattered over {uncertainty_km:.0f} km, "
                f"which is most of a hemisphere. The image cannot be placed."
            )

        # 4. Contradiction. If the rest of the image argues against the
        #    winner, say so and claim less.
        if evidence_support < cfg.contradiction_threshold and band != "insufficient":
            idx = max(0, self._ORDER.index(band) - 1)
            band = self._ORDER[idx]
            notes.append(
                "Downgraded: the visual evidence contradicts the leading "
                f"candidate on balance (net {evidence_support:+.2f} in log "
                "odds). Check the evidence list before using this."
            )

        # 5. Corroboration from text can lift a band, because a place name
        #    matched in the gazetteer is positional evidence in its own right.
        #
        #    It cannot lift anything to "high". There is no measurement
        #    showing that a toponym match makes a high-confidence call safe,
        #    and the high band is the one promise this tool makes.
        corroborated = [
            a for a in candidates[0].adjustments
            if a.source == "toponym" and a.delta > 0
        ]
        if (corroborated and band in {"low", "insufficient"}
                and uncertainty_km <= cfg.medium_spread_km):
            band = "medium"
            reason += (
                " Text read from the image independently corroborates it: "
                + corroborated[0].detail + "."
            )

        # 6. The leading candidate came from an address read in the image
        #    and matched in OpenStreetMap. That is positional evidence of a
        #    different kind from anything else here: not a guess about what
        #    the scenery resembles, but a name that OSM can point at.
        #
        #    It is allowed to lift the band to "medium" and no further. Not
        #    because a read address is weak -- it is usually the best thing
        #    in the frame -- but because there is no benchmark for it yet.
        #    Phase 13 is the precedent: the high band is the one promise
        #    this tool makes, and it is not given out on reasoning alone.
        #    When a signboard benchmark exists this rule gets revisited with
        #    numbers behind it.
        if read_leader is not None:
            place = read_leader
            where = place.get("display_name", "").split(",")[0]
            if band in {"low", "insufficient"}:
                band = "medium"
            reason = (
                f"The address in the image reads as {where!r}, which "
                f"OpenStreetMap places as a {place.get('specificity')} at "
                f"this coordinate. " + reason
            )
            notes.append(
                "This location was read from text in the image, not inferred "
                "from what the image looks like. It is capped at medium "
                "confidence because that path has not been benchmarked yet."
            )
            if disagreement_km:
                notes.append(
                    f"The visual estimate is {disagreement_km:.0f} km from "
                    f"the address that was read. That is worth a look: a "
                    f"photograph of a poster, a brochure or a vehicle "
                    f"livery carries an address that is not where the "
                    f"picture was taken."
                )

        return band, reason, notes
