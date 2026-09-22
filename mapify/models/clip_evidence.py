"""Zero-shot visual evidence, and a country-level locator, from CLIP.

Two jobs off one image embedding:

1. Score every prompt group in :mod:`mapify.evidence.prompts` and report the
   winning option per group, with the margin over its nearest rival. Groups
   that do not clear their thresholds are reported as "no call" rather than
   silently guessing.

2. Score the image against "a photo taken in {country}" for every country in
   the table, giving a country prior. On its own this is a coarse locator; in
   fusion it is a second opinion that did not come from GeoCLIP.
"""

from __future__ import annotations

import logging

import numpy as np
from PIL.Image import Image as PILImage

from ..data.countries import get_table
from ..evidence import prompts as P
from .base import GroupReading, RawCandidate, VisualEvidence
from .clip_backend import ClipBackend, softmax

log = logging.getLogger("mapify.evidence")

# CLIP similarities live in a narrow band, so a temperature well below 1
# sharpens them into something a softmax can separate. 0.01 is the value
# CLIP itself is trained with (logit_scale = 1/0.01).
SIM_TEMPERATURE = 0.02


class ClipEvidenceExtractor:
    """Scores the prompt bank against an image."""

    name = "clip-zero-shot"

    def __init__(self, backend: ClipBackend):
        self.backend = backend
        self._prompt_emb: np.ndarray | None = None
        self._index = P.prompt_index()

    def _ensure_prompts(self) -> np.ndarray:
        if self._prompt_emb is None:
            self._prompt_emb = self.backend.encode_texts_cached(
                P.all_prompts(), "promptbank"
            )
        return self._prompt_emb

    def extract(self, image: PILImage) -> VisualEvidence:
        try:
            emb = self.backend.encode_image(image)
        except Exception as exc:
            return VisualEvidence(error=f"{type(exc).__name__}: {exc}")
        return self.score_embedding(emb)

    def score_embedding(self, emb: np.ndarray) -> VisualEvidence:
        """Score the prompt bank against an embedding already computed.

        GeoCLIP runs the same vision tower, so the pipeline hands its
        embedding straight here rather than paying for it twice.
        """
        try:
            bank = self._ensure_prompts()
        except Exception as exc:
            return VisualEvidence(error=f"{type(exc).__name__}: {exc}")

        sims = bank @ emb  # (N,)
        readings: list[GroupReading] = []

        for group in P.ALL_GROUPS:
            # An option may have several phrasings; take the best-matching one
            # so a single awkward wording cannot sink a correct option.
            option_scores: list[float] = []
            for option in group.options:
                lo, hi = self._index[(group.key, option.key)]
                option_scores.append(float(np.max(sims[lo:hi])))

            probs = softmax(np.asarray(option_scores), SIM_TEMPERATURE)
            order = np.argsort(-probs)
            best = int(order[0])
            second = float(probs[order[1]]) if len(order) > 1 else 0.0
            top_p = float(probs[best])
            margin = top_p - second
            option = group.options[best]

            called = (
                top_p >= group.min_confidence
                and margin >= group.min_margin
                and option.constrains
            )
            readings.append(
                GroupReading(
                    group_key=group.key,
                    group_title=group.title,
                    option_key=option.key,
                    option_label=option.label,
                    confidence=top_p,
                    margin=margin,
                    called=called,
                    constrains=option.constrains,
                    runners_up=[
                        (group.options[int(i)].label, float(probs[int(i)]))
                        for i in order[1:3]
                    ],
                )
            )

        return VisualEvidence(readings=readings, model=self.backend.model_id)


class ClipGazetteerLocator:
    """A country-level locator: CLIP against "a photo taken in {country}".

    Coarse by construction -- it returns a country's label point, not a street
    -- but it is a genuine, independent estimate, and it is the fallback that
    keeps the tool working if GeoCLIP cannot load.
    """

    name = "clip-country-gazetteer"

    # Countries too small to have a distinctive look, or that CLIP will only
    # ever confuse with their neighbours, are still included: excluding them
    # would bias the prior rather than improve it.
    def __init__(self, backend: ClipBackend):
        self.backend = backend
        self._emb: np.ndarray | None = None
        self._codes: list[str] = []
        self._per_country = len(P.COUNTRY_PROMPT_TEMPLATES)

    def _ensure(self) -> tuple[np.ndarray, list[str]]:
        if self._emb is None:
            table = get_table()
            texts: list[str] = []
            codes: list[str] = []
            for country in sorted(table.all(), key=lambda c: c.iso_a2):
                name = country.name_long or country.name
                for tpl in P.COUNTRY_PROMPT_TEMPLATES:
                    texts.append(tpl.format(name=name))
                codes.append(country.iso_a2)
            self._emb = self.backend.encode_texts_cached(texts, "countries")
            self._codes = codes
        return self._emb, self._codes

    def country_prior(self, image_embedding: np.ndarray) -> dict[str, float]:
        emb, codes = self._ensure()
        sims = emb @ image_embedding
        # Average the template phrasings for each country.
        per = sims.reshape(len(codes), self._per_country).mean(axis=1)
        probs = softmax(per, SIM_TEMPERATURE)
        return {code: float(p) for code, p in zip(codes, probs)}

    def locate(self, image: PILImage, top_k: int = 8) -> list[RawCandidate]:
        emb = self.backend.encode_image(image)
        prior = self.country_prior(emb)
        table = get_table()
        ranked = sorted(prior.items(), key=lambda kv: -kv[1])[:top_k]
        out: list[RawCandidate] = []
        for code, p in ranked:
            country = table.get(code)
            if not country:
                continue
            out.append(
                RawCandidate(
                    latitude=country.label_lat,
                    longitude=country.label_lon,
                    score=p,
                    source=self.name,
                )
            )
        return out
