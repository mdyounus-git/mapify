"""The analysis pipeline.

Order matters here, and so does what each stage is allowed to conclude:

  1. decode and normalise the image (never trusting the declared type);
  2. read EXIF -- recorded as a *claim*, kept out of the estimate;
  3. GeoCLIP proposes coordinates, and hands back the CLIP embedding it
     already computed;
  4. that same embedding is scored against the prompt bank and the country
     gazetteer, so the second opinion costs one matrix multiply;
  5. OCR, if installed, reads signage; its text feeds script detection and
     gazetteer matching;
  6. fusion re-ranks, with an audit trail;
  7. enrichment names the winning places, offline first, online only if the
     operator allowed it.

Everything above step 7 is local. Nothing sends the image anywhere, ever.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps

from . import heif
from .config import ACCEPTED_EXTENSIONS, ACCEPTED_FORMATS, Config
from .panorama import is_equirectangular
from .panorama import views as panorama_views
from .data.countries import get_table
from .evidence import script_detect
from .evidence.fusion import Candidate, FusionEngine, FusionResult
from .exif import ExifReport, read_exif
from .geo import timezone as tz
from .geo.cache import LookupCache
from .geo.geodesy import haversine_km
from .geo.landmarks import OverpassClient
from .geo.nominatim import NominatimClient
from .geo.offline import get_geocoder
from .geo import readplace
from .models.base import OcrResult, RawCandidate, VisualEvidence
from .models.clip_backend import ClipBackend
from .models.clip_evidence import ClipEvidenceExtractor, ClipGazetteerLocator
from .models.geoclip_locator import GeoClipLocator
from .models import ocr as ocr_groups
from .models.ocr import build_ocr_engine
from .paths import cache_dir

log = logging.getLogger("mapify.pipeline")


ProgressFn = Callable[[str, str], None]


class ImageRejected(ValueError):
    """The upload is not an image we will process."""


# A mirrored photograph is told by its text: read flipped, it reads clearly
# better. The flipped reading is only tried when the first one is poor, so a
# photograph whose text reads well costs nothing extra. Measured with
# scripts/measure_mirror.py, every photograph read both ways (the 163-photo
# benchmark and the text-bearing set): no photograph the right way round was
# flipped at any threshold tried, and with these every mirrored one whose
# text reads well was caught. The price is a second reading for about a
# third of ordinary photographs -- those whose text already reads poorly.
MIRROR_TRY_BELOW = 0.6        # first reading's quality below which to try
MIRROR_MIN_QUALITY = 0.6      # the flipped reading must be at least this good
MIRROR_MIN_GAIN = 0.1         # ... and this much better than the first
MIRRORED_NOTE = (
    "The photograph appears to be mirrored (a selfie camera or a flipped "
    "copy): its text reads properly only when flipped, so it was analysed "
    "flipped, and is shown that way."
)


def reading_quality(ocr: OcrResult) -> float:
    """Mean OCR confidence, weighted by how many letters and digits each
    line holds, so a one-character fragment cannot outvote a sign."""
    total = weight = 0.0
    for line in ocr.lines:
        n = sum(ch.isalnum() for ch in line.text)
        if n >= 2:
            total += line.confidence * n
            weight += n
    return total / weight if weight else 0.0


@dataclass
class _Look:
    """What looking at the pixels produced, before any fusion."""
    raw: list[RawCandidate]
    embedding: Any
    evidence: VisualEvidence
    extra: list[RawCandidate]
    ocr_result: OcrResult
    script_reading: Any
    extra_group: tuple[str, ...] | None


@dataclass
class AnalysisReport:
    ok: bool
    elapsed_s: float
    image: dict[str, Any] = field(default_factory=dict)
    exif: dict[str, Any] = field(default_factory=dict)
    prediction: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    ocr: dict[str, Any] = field(default_factory=dict)
    # Which extra OCR recogniser was loaded for this image, if any.
    ocr_reader_hint: str | None = None
    toponyms: list[dict[str, Any]] = field(default_factory=list)
    read_places: list[dict[str, Any]] = field(default_factory=list)
    # Every numbered technique that was tried, what it found, and -- for
    # the ones that could not run -- why not. Absence and silence are
    # different, and a reader who cannot tell them apart will misread
    # the whole report.
    techniques: list[dict[str, Any]] = field(default_factory=list)
    case_id: str | None = None
    # Whether this is a photograph of a place at all, and why the gate
    # decided as it did. When it is not, nothing downstream is allowed
    # to produce a coordinate: see mapify/locatability.py.
    locatability: dict[str, Any] = field(default_factory=dict)
    locatable: bool = True

    # Not serialised. A multi-image session has to fuse the *inputs* to
    # each analysis, not its conclusions -- averaging three finished
    # answers is not the same as combining three observations, and only
    # the latter can say that two images agreeing is worth more than
    # either alone. A JSON consumer has no use for any of it.
    raw_candidates: list[Any] = field(default_factory=list, repr=False)
    embedding: Any = field(default=None, repr=False)
    visual_evidence: Any = field(default=None, repr=False)
    ocr_result: Any = field(default=None, repr=False)
    script_reading: Any = field(default=None, repr=False)
    read_place_objs: list[Any] = field(default_factory=list, repr=False)
    # The locator's opinion of every gallery point, 400 KB, kept only
    # when a caller asks -- a session needs it, a 500-image batch would
    # be 200 MB of it.
    gallery_distribution: Any = field(default=None, repr=False)
    engine: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    attribution: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "elapsed_s": round(self.elapsed_s, 2),
            "image": self.image,
            "exif": self.exif,
            "prediction": self.prediction,
            "candidates": self.candidates,
            "evidence": self.evidence,
            "ocr": {**self.ocr, "reader_hint": self.ocr_reader_hint},
            "toponyms": self.toponyms,
            "read_places": self.read_places,
            "techniques": self.techniques,
            "case_id": self.case_id,
            "locatability": self.locatability,
            "engine": self.engine,
            "warnings": self.warnings,
            "attribution": self.attribution,
            "error": self.error,
        }


class AnalysisEngine:
    """Holds the loaded models and runs analyses."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.geocoder = get_geocoder()
        self.geocoder.use_geonames = cfg.models.geonames_enabled
        self._memory = None           # the street-photo memory, loaded when first used
        self._lid = None              # the language identifier, likewise
        self._plonk = None            # the second locator, likewise
        self.table = get_table()
        self.fusion = FusionEngine(cfg.fusion, self.geocoder, self.table)

        self.geoclip = GeoClipLocator(cfg.models.device) if cfg.models.geoclip_enabled else None
        self.clip = ClipBackend(cfg.models.clip_model_id, cfg.models.device)
        self.evidence_extractor = ClipEvidenceExtractor(self.clip)
        self.gazetteer_locator = ClipGazetteerLocator(self.clip)
        self.ocr = (
            build_ocr_engine(
                cfg.models.ocr_languages, cfg.models.ocr_extra_groups,
                cfg.models.device,
            )
            if cfg.models.ocr_enabled
            else None
        )

        cache_path = cache_dir() / "lookups.sqlite"
        self.cache = LookupCache(cache_path)
        self.nominatim = NominatimClient(
            cfg.network.nominatim_endpoint,
            cfg.network.user_agent,
            self.cache,
            cfg.network.nominatim_min_interval_s,
            cfg.network.request_timeout_s,
            cfg.network.nominatim_max_lookups_per_analysis,
        )
        self.overpass = OverpassClient(
            cfg.network.overpass_endpoint,
            cfg.network.user_agent,
            self.cache,
            cfg.network.overpass_min_interval_s,
            cfg.network.overpass_timeout_s,
        )

    # --------------------------------------------------------------- setup

    def warmup(self, progress: ProgressFn | None = None) -> dict[str, Any]:
        """Load what we can, and report honestly on what we could not.

        ``progress`` is called with a stage key and a sentence as each part
        starts. The first launch spends ten minutes in here and the caller
        needs to be able to say which of those minutes is which -- a download
        that has stalled and a gallery that is still encoding look identical
        from outside.
        """
        def step(stage: str, message: str) -> None:
            if progress:
                progress(stage, message)

        status: dict[str, Any] = {}
        if self.geoclip:
            step("locator", "Loading the geolocation model")
            ok = self.geoclip.load()
            status["geoclip"] = {
                "loaded": ok,
                "error": self.geoclip.load_error,
                "gallery": self.geoclip.gallery_size,
            }
            if ok:
                clip_model, processor = self.geoclip.shared_clip()
                self.clip.load(clip_model, processor)
                # Encode the GPS gallery now. It is the same for every image,
                # so paying for it here makes each analysis ~20x faster.
                step("gallery", "Encoding the GPS gallery")
                t0 = time.time()
                self.geoclip.gallery_features()
                status["geoclip"]["gallery_encode_s"] = round(time.time() - t0, 1)
        else:
            status["geoclip"] = {"loaded": False, "error": "disabled in config"}

        if not self.clip.loaded:
            step("evidence", "Loading the evidence encoder")
            self.clip.load()
        status["clip"] = {
            "loaded": self.clip.loaded,
            "model": self.cfg.models.clip_model_id,
            "error": self.clip.load_error,
        }
        status["ocr"] = {
            "engine": getattr(self.ocr, "name", None),
            "available": bool(self.ocr and self.ocr.name != "none"),
        }
        step("geodata", "Indexing the offline gazetteer")
        status["offline_geodata"] = self.geocoder.status()
        status["timezone_lookup"] = tz.available()
        step("ready", "Ready")
        return status

    def engine_status(self) -> dict[str, Any]:
        return {
            "geoclip_loaded": bool(self.geoclip and self.geoclip.loaded),
            "clip_loaded": self.clip.loaded,
            "ocr_engine": getattr(self.ocr, "name", None),
            "offline_only": self.cfg.network.offline_only,
            "nominatim_enabled": (
                self.cfg.network.nominatim_enabled
                and not self.cfg.network.offline_only
            ),
            "overpass_enabled": (
                self.cfg.network.overpass_enabled
                and not self.cfg.network.offline_only
            ),
            "timezone_lookup": tz.available(),
            "geodata": self.geocoder.status(),
            "device": self.cfg.models.device,
        }

    # -------------------------------------------------------------- decode

    def decode(self, data: bytes, filename: str | None = None
               ) -> tuple[Image.Image, dict[str, Any]]:
        """Decode and normalise, refusing anything that is not a real image."""
        if not data:
            raise ImageRejected("Empty upload.")
        if len(data) > self.cfg.server.max_upload_bytes:
            raise ImageRejected(
                f"Image is {len(data) / 1e6:.1f} MB; the limit is "
                f"{self.cfg.server.max_upload_bytes / 1e6:.0f} MB."
            )
        if heif.looks_like_heif(data) and not heif.enable():
            raise ImageRejected(heif.MISSING)
        try:
            probe = Image.open(io.BytesIO(data))
            probe.verify()          # structural check; consumes the file object
            img = Image.open(io.BytesIO(data))
        except Exception as exc:
            raise ImageRejected(
                f"Could not decode this file as an image ({type(exc).__name__})."
            ) from exc

        fmt = (img.format or "").upper()
        if fmt not in ACCEPTED_FORMATS:
            raise ImageRejected(
                f"Format {fmt or 'unknown'} is not supported. "
                "Use JPG, PNG, WEBP or HEIC."
            )

        meta = {
            "filename": filename,
            "format": fmt,
            "mode": img.mode,
            "width": img.width,
            "height": img.height,
            "bytes": len(data),
        }
        if img.width < 64 or img.height < 64:
            raise ImageRejected(
                f"Image is only {img.width}x{img.height}. Too small to analyse."
            )
        return img, meta

    @staticmethod
    def _prepare(img: Image.Image, max_side: int) -> Image.Image:
        """Orient by EXIF and cap the long side for the model."""
        out = ImageOps.exif_transpose(img) or img
        out = out.convert("RGB")
        if max(out.size) > max_side:
            scale = max_side / max(out.size)
            out = out.resize(
                (max(1, int(out.width * scale)), max(1, int(out.height * scale))),
                Image.LANCZOS,
            )
        return out

    # ------------------------------------------------------------- analyse

    def analyse(self, data: bytes, filename: str | None = None,
                progress: ProgressFn | None = None,
                keep_distribution: bool = False,
                online_enrichment: bool = True) -> AnalysisReport:
        """Analyse one image.

        ``online_enrichment=False`` keeps the offline naming (Natural Earth,
        time zone) and skips Nominatim and Overpass. Batch mode uses it so it
        can cluster first and geocode once per distinct place rather than once
        per image.
        """
        t0 = time.time()
        report = AnalysisReport(ok=False, elapsed_s=0.0)
        step = progress or (lambda k, m: None)

        try:
            step("decode", "Decoding image")
            source, meta = self.decode(data, filename)
            report.image = meta

            step("exif", "Reading embedded metadata")
            exif_report = read_exif(source)
            report.exif = exif_report.as_dict()

            image = self._prepare(source, self.cfg.models.max_image_side)
            report.image["analysed_size"] = [image.width, image.height]
            # A 360-degree panorama is read as four ordinary views of one
            # moment (mapify/panorama.py); the file's own tag says so, and
            # it has gone from the prepared copy, so ask the source.
            views = panorama_views(image) if is_equirectangular(source) else None
            if views:
                report.image["panorama"] = True

            warnings_before = len(report.warnings)
            look = self._look(image, report, step, views=views)
            mirrored = self._mirrored_reading(image, look)
            if mirrored is not None:
                # A selfie camera or a flipped repost reverses the whole
                # scene: the text, the side of the road, every shape. Text
                # that reads properly only when flipped says which way round
                # it really is, so the analysis starts again on that copy.
                image = ImageOps.mirror(image)
                del report.warnings[warnings_before:]
                views = panorama_views(image) if views else None
                look = self._look(image, report, step, ocr=mirrored, views=views)
                report.image["mirrored"] = True
                report.warnings.append(MIRRORED_NOTE)
            raw, embedding, evidence = look.raw, look.embedding, look.evidence
            extra = look.extra
            ocr_result, script_reading = look.ocr_result, look.script_reading

            # ---- what the text actually says ------------------------------
            #
            # The gazetteer bundled with Mapify knows 7,300 settlements. A
            # signboard carrying a name, a street and a postcode matches
            # almost none of it, which is how a photograph of a building
            # with its own address written on it came back as "India".
            #
            # OpenStreetMap knows the building. Ask it -- once, within the
            # same budget and rate limit as everything else, and only when
            # there is text worth asking about.
            read_places: list[Any] = []
            if (
                ocr_result.lines
                and online_enrichment
                and not self.cfg.network.offline_only
                and self.cfg.network.nominatim_enabled
            ):
                step("readplace", "Looking up the address on the sign")
                read_places = readplace.resolve(ocr_result, self.nominatim)
                report.read_places = [p.as_dict() for p in read_places]

            extra_pool = list(extra or [])
            if read_places:
                ceiling = max((c.score for c in raw), default=0.05)
                extra_pool.extend(readplace.to_candidates(read_places, ceiling))
            extra_pool.extend(self._remembered(embedding, raw, report))
            second = self._second_opinion(image, report)

            # ---- is this a photograph of a place? --------------------------
            #
            # Asked of evidence the locator did not produce, because the
            # locator cannot decline: it is a softmax, it always answers.
            step("gate", "Checking this is a photograph of a place")
            gate = self._assess_locatability(source, embedding, ocr_result,
                                             exif_report)
            report.locatability = gate.as_dict()
            report.locatable = gate.locatable

            # ---- fusion ---------------------------------------------------
            step("fuse", "Weighing the evidence")
            fused = self.fusion.fuse(
                raw, evidence=evidence, ocr=ocr_result,
                script_reading=script_reading, extra_candidates=extra_pool,
                read_places=read_places, second_opinion=second,
            )
            report.toponyms = [t.as_dict() for t in fused.toponyms]

            # ---- enrichment -----------------------------------------------
            step("enrich", "Naming the location")
            self._enrich(fused, report, online=online_enrichment)

            if keep_distribution and self.geoclip:
                report.gallery_distribution = self.geoclip.distribution(image)

            report.raw_candidates = list(raw) + list(extra_pool)
            report.embedding = embedding
            report.visual_evidence = evidence
            report.ocr_result = ocr_result
            report.script_reading = script_reading
            report.read_place_objs = list(read_places)

            if gate.locatable:
                report.candidates = [c.as_dict() for c in fused.candidates]
                report.prediction = self._build_prediction(fused, exif_report)
                report.prediction["locatable"] = True
                report.prediction["image_kind"] = gate.as_dict()
                if gate.withhold:
                    # Doubtful: listed, not located. Same shape as any
                    # other undetermined result, with the reason stated.
                    pred = report.prediction
                    pred["confidence"] = "insufficient"
                    pred["determinate"] = False
                    pred["confidence_reason"] = (
                        gate.headline() + " " + pred.get("confidence_reason", ""))
                    pred.setdefault("notes", []).insert(0, (
                        "The candidates below are what the model produced "
                        "for this image, not a finding: "
                        + ("it may not be a photograph of a place."
                           if gate.verdict == "doubtful" else
                           "there is nothing around the subject to place "
                           "it by.")))
            else:
                # Withheld, not softened. No candidates, no estimate, and
                # nothing a session or a batch could fuse: the locator's
                # opinion of a poster is not an observation of anywhere.
                report.candidates = []
                report.raw_candidates = []
                report.gallery_distribution = None
                report.prediction = self._not_a_place_prediction(
                    gate, fused, exif_report, read_places)

            # ---- the numbered techniques ----------------------------------
            # Run last, over everything the analysis already paid for, and
            # after the prediction exists, so the correlating techniques can
            # read it. An image refused as not a place gets no candidates
            # here either: a technique must not describe "the leading
            # candidate" of a poster.
            step("techniques", "Recording what each technique saw")
            report.techniques = self._run_techniques(
                image=image, data=data, filename=filename,
                exif=exif_report, ocr=ocr_result,
                script_reading=script_reading, embedding=embedding,
                evidence=evidence,
                candidates=list(fused.candidates) if gate.locatable else [],
                read_places=list(read_places), online=online_enrichment,
                prediction=report.prediction, fused=fused if gate.locatable else None,
                locatability=report.locatability,
            )
            report.engine = self.engine_status()
            report.attribution = self.attribution_lines()
            report.ok = True

        except ImageRejected as exc:
            report.error = str(exc)
        except Exception as exc:  # pragma: no cover - last-resort guard
            log.exception("analysis failed")
            report.error = f"{type(exc).__name__}: {exc}"

        report.elapsed_s = time.time() - t0
        step("done", "Complete")
        return report

    def _look(self, image: Image.Image, report: AnalysisReport,
              step: Callable[[str, str], None],
              ocr: OcrResult | None = None,
              views: list[Image.Image] | None = None) -> _Look:
        """Locate, read the visual clues and the text: everything that looks
        at the pixels. Run again on a flipped copy when the photograph turns
        out to be mirrored, with the text already read from that copy.
        """
        # ---- locators -------------------------------------------------
        step("locate", "Estimating coordinates")
        raw: list[RawCandidate] = []
        embedding = None
        evidence = VisualEvidence()

        if self.geoclip and self.geoclip.load():
            try:
                # Mode search needs the locator's full distribution.
                # Any locator can decline to offer one -- a stand-in
                # in the tests, or a different model later -- and the
                # point-wise shortlist is the correct fallback rather
                # than a failure.
                modes = (self.cfg.models.geoclip_mode_search
                         and hasattr(self.geoclip, "locate_modes"))
                if modes:
                    raw, embedding = self.geoclip.locate_modes(
                        views or image,
                        self.cfg.models.geoclip_top_k,
                        self.cfg.models.geoclip_mode_radius_km,
                        focus_km=self.cfg.models.geoclip_mode_focus_km,
                    )
                else:
                    raw, embedding = self.geoclip.locate_with_embedding(
                        views or image, self.cfg.models.geoclip_top_k
                    )
            except Exception as exc:
                report.warnings.append(
                    f"GeoCLIP failed on this image: {type(exc).__name__}: {exc}"
                )
        elif self.geoclip:
            report.warnings.append(
                "GeoCLIP is unavailable "
                f"({self.geoclip.load_error}); falling back to the CLIP "
                "country gazetteer, which is far coarser."
            )

        # ---- zero-shot evidence --------------------------------------
        if self.cfg.models.clip_evidence_enabled and self.clip.load():
            step("evidence", "Reading visual clues")
            if embedding is None:
                try:
                    embedding = self.clip.encode_image(image)
                except Exception as exc:
                    report.warnings.append(f"CLIP encode failed: {exc}")
            if embedding is not None:
                evidence = self.evidence_extractor.score_embedding(embedding)
                if self.cfg.models.gazetteer_locator_enabled:
                    try:
                        evidence.country_prior = (
                            self.gazetteer_locator.country_prior(embedding)
                        )
                    except Exception as exc:
                        report.warnings.append(
                            f"Country prior unavailable: {exc}"
                        )

        # Fallback locator: if nothing proposed coordinates, use the
        # country prior's best guesses -- coarse, and labelled as such.
        extra: list[RawCandidate] = []
        if not raw and evidence.country_prior:
            ranked = sorted(
                evidence.country_prior.items(), key=lambda kv: -kv[1]
            )[:8]
            for code, p in ranked:
                country = self.table.get(code)
                if country:
                    extra.append(
                        RawCandidate(
                            latitude=country.label_lat,
                            longitude=country.label_lon,
                            score=p,
                            source="clip-country-gazetteer",
                        )
                    )
            report.warnings.append(
                "Coordinates come from the country-level CLIP gazetteer, "
                "not GeoCLIP. Expect country-scale accuracy at best."
            )

        report.evidence = evidence.as_dict()

        # ---- OCR ------------------------------------------------------
        ocr_result = ocr if ocr is not None else OcrResult()
        extra_group: tuple[str, ...] | None = None
        script_reading = None
        if self.ocr and ocr is None:
            step("ocr", "Reading signage text")
            extra_group = self._choose_ocr_group(evidence)
            try:
                ocr_result = self.ocr.read(image, extra_group)
            except TypeError:
                ocr_result = self.ocr.read(image)
            except Exception as exc:
                ocr_result = OcrResult(error=f"{type(exc).__name__}: {exc}")
            if extra_group:
                report.ocr_reader_hint = "+".join(extra_group)
        if ocr_result.text.strip():
            script_reading = script_detect.analyse(ocr_result.text)
            if self.cfg.models.language_id_enabled and ocr_result.language is None:
                ocr_result.language = self._language_of(ocr_result.text)
        report.ocr = ocr_result.as_dict()
        if script_reading:
            report.ocr["scripts"] = script_reading.as_dict()
        return _Look(raw, embedding, evidence, extra, ocr_result,
                     script_reading, extra_group)

    def _language_of(self, text: str) -> dict[str, Any] | None:
        """The language of the recognised text and where it is spoken
        (evidence/language_id.py). Reported in technique 12; it moves
        nothing until measured."""
        if self._lid is None:
            from .evidence.language_id import LanguageIdentifier
            self._lid = LanguageIdentifier()
        try:
            return self._lid.identify(text)
        except Exception:                         # a damaged model file
            return None

    def _second_opinion(self, image: Image.Image, report: AnalysisReport):
        """PLONK's samples, a second line independent of GeoCLIP (different
        encoder, different training photographs), for the fusion to judge
        GeoCLIP's candidates by. Off unless models.plonk_enabled; None when
        it cannot run."""
        if not self.cfg.models.plonk_enabled:
            return None
        if self._plonk is None:
            from .models.plonk_locator import PlonkLocator
            self._plonk = PlonkLocator(samples=self.cfg.models.plonk_samples)
        if not self._plonk.available():
            return None
        try:
            pts = self._plonk.sample(image)
            _cands, summary = self._plonk.summarise(pts)
        except Exception as exc:
            report.warnings.append(f"The second locator did not run: {exc}")
            return None
        report.evidence["second_locator"] = {"model": "PLONK (YFCC)", **summary}
        return pts

    def _remembered(self, embedding, raw: list[RawCandidate],
                    report: AnalysisReport) -> list[RawCandidate]:
        """Places where photographs like this one were taken, from the
        street-photo memory (models/retrieval.py), as positions for the
        fusion to score. The same embedding as GeoCLIP's, so not a second
        vote: scored below GeoCLIP's best, they win only on the evidence."""
        if not self.cfg.models.retrieval_enabled or embedding is None:
            return []
        if self._memory is None:
            from .models.retrieval import RetrievalMemory
            self._memory = RetrievalMemory()
        if not self._memory.available():
            return []
        try:
            places = self._memory.candidates(embedding)
        except Exception as exc:                  # a damaged index
            report.warnings.append(f"Street-photo memory unavailable: {exc}")
            return []
        ceiling = max((c.score for c in raw), default=0.05)
        report.evidence["street_memory"] = {
            "photographs": self._memory.size,
            "places": [{"lat": round(c.latitude, 5), "lon": round(c.longitude, 5),
                        "share": round(c.score, 3)} for c in places]}
        return [RawCandidate(c.latitude, c.longitude, c.score * ceiling * 0.5, c.source)
                for c in places]

    def _mirrored_reading(self, image: Image.Image,
                          look: _Look) -> OcrResult | None:
        """The text read off a flipped copy, when it reads clearly better.

        None when there is nothing to compare: no OCR, no text, or a first
        reading good enough that the photograph is the right way round.
        """
        first = look.ocr_result
        if not self.ocr or first.error or not first.lines:
            return None
        before = reading_quality(first)
        if before >= MIRROR_TRY_BELOW:
            return None
        flipped = ImageOps.mirror(image)
        try:
            after = self.ocr.read(flipped, look.extra_group)
        except TypeError:
            after = self.ocr.read(flipped)
        except Exception:
            return None
        quality = reading_quality(after)
        if quality >= MIRROR_MIN_QUALITY and quality - before >= MIRROR_MIN_GAIN:
            return after
        return None

    def _assess_locatability(self, source, embedding, ocr, exif):
        """Run the gate. Never raises: a failed check lets the image through.

        Withholding an answer is a claim, and it needs evidence. If the
        gate itself cannot run, the image goes on to the ordinary
        confidence path, which is conservative in its own right.
        """
        from .forensics.graphic import graphic_stats, text_coverage
        from .locatability import Locatability, assess

        try:
            kinds = (self.evidence_extractor.group_probabilities(
                embedding, "image_kind") if embedding is not None else {})
            return assess(kinds, graphic_stats(source), text_coverage(ocr),
                          exif)
        except Exception as exc:                        # pragma: no cover
            log.warning("locatability check failed: %s", exc)
            return Locatability(verdict="place",
                                reasons=[f"the check failed ({exc})"])

    def _not_a_place_prediction(self, gate, fused: FusionResult,
                                exif: ExifReport, read_places
                                ) -> dict[str, Any]:
        """The prediction for an image that is not of anywhere.

        Same keys as a normal prediction, so nothing downstream has to
        special-case its shape -- but every positional field is empty.
        What the image *does* say is kept: a GPS tag is the file's own
        claim and is reported as one, and a place named in the text is a
        thing the text says, not where the image was taken.
        """
        embedded = None
        if exif.gps:
            embedded = {
                "latitude": exif.gps.latitude,
                "longitude": exif.gps.longitude,
                "altitude_m": exif.gps.altitude_m,
                "timestamp_utc": exif.gps.timestamp_utc,
                "processing_method": exif.gps.processing_method,
                "place": self.geocoder.locate(
                    exif.gps.latitude, exif.gps.longitude, place_limit=3
                ).as_dict(),
                "timezone": tz.lookup(exif.gps.latitude,
                                      exif.gps.longitude).as_dict(),
                "trust_warnings": exif.trust_warnings,
                "note": ("The file carries this GPS tag, but the image is not "
                         "a photograph of a place, so nothing in it can "
                         "confirm or contradict the tag."),
            }
        mentioned = [{"display_name": p.result.display_name,
                      "latitude": p.result.latitude,
                      "longitude": p.result.longitude,
                      "query": p.query}
                     for p in (read_places or [])[:5]]
        notes = [
            "Nothing about where this image was taken can be determined "
            "from its content, so no location is given -- not a "
            "low-confidence one, none.",
        ]
        if mentioned:
            notes.append(
                "The text in the image names a place. That is something the "
                "image says, not where it was taken, and it is listed as "
                "such rather than placed on the globe.")
        reason = gate.headline()
        if gate.reasons:
            reason += " " + "; ".join(gate.reasons).capitalize() + "."
        return {
            "confidence": "insufficient",
            "confidence_reason": reason,
            "diagnostics": fused.diagnostics(),
            "uncertainty_km": None,
            "precision_km": None,
            "centroid": None,
            "estimated": None,
            "embedded_gps": embedded,
            "exif_vs_estimate": None,
            "notes": notes,
            "determinate": False,
            "locatable": False,
            "image_kind": gate.as_dict(),
            "mentioned_places": mentioned,
        }

    def _run_techniques(self, *, image, data, filename, exif, ocr,
                        script_reading, embedding, evidence, candidates,
                        read_places, online, prediction=None, fused=None,
                        locatability=None) -> list[dict[str, Any]]:
        """Run every applicable numbered technique and record all of them.

        Failures here must not cost the analysis. The techniques are a
        second, descriptive account of an image whose location has
        already been worked out; a broken detector should cost its own
        finding and nothing else.
        """
        try:
            from .techniques import Context, run_all
        except Exception as exc:                        # pragma: no cover
            log.warning("technique registry unavailable: %s", exc)
            return []

        ctx = Context(
            image=image, raw_bytes=data, filename=filename, exif=exif,
            ocr=ocr, script_reading=script_reading, embedding=embedding,
            evidence=evidence, candidates=candidates,
            read_places=read_places, cfg=self.cfg, engine=self,
            online=online, prediction=prediction or {}, fused=fused,
            locatability=locatability or {},
        )
        try:
            findings = run_all(ctx)
        except Exception:                               # pragma: no cover
            log.exception("the technique pass failed")
            return []
        records = getattr(self.cfg, "records", None)
        if getattr(records, "case_files", False):
            # The persistent case file: findings and provenance, never the
            # image. A failure to write it costs the record, not the answer.
            try:
                from .techniques.casefile import record_run
                f152 = ctx.finding(152)
                record_run(data, filename, findings, prediction, image=image,
                           embedding=embedding,
                           provenance=f152.detail if f152 is not None and f152.ran
                           else None)
            except Exception:                           # pragma: no cover
                log.exception("could not write the case file")
        return [f.as_dict() for f in findings]

    def _choose_ocr_group(self, evidence: VisualEvidence
                          ) -> tuple[str, ...] | None:
        """Pick one extra OCR recogniser for this image.

        Which reader to load is a *measurement* decision, not evidence, and
        the two deserve different thresholds. A wrong guess here costs one
        model load and reads nothing extra; the bar for letting a reading
        move a candidate's score is much higher, and stays where it is.

        There is no circularity in letting the model's own guesses choose the
        reader. Loading the Turkish recogniser does not invent Turkish
        letters -- if the sign says MAIN STREET it still reads MAIN STREET
        and produces no language hint. The guess decides where to look; the
        image decides what is found.
        """
        readings = {r.group_key: r for r in evidence.readings}

        # 1. A non-Latin script, if one is plausibly present. Latin wins the
        #    group on almost any mixed sign, so the runner-up is the
        #    informative answer and the threshold is deliberately low.
        script = readings.get("signage_script")
        if script:
            ranked = script.ranked or [(script.option_key, script.confidence)]
            for key, conf in ranked:
                if conf < 0.10:
                    continue
                group = ocr_groups.group_for_script(key)
                if group:
                    return group

        # 2. Otherwise a Latin language whose diacritics the English model
        #    would flatten, chosen from the country prior when it is peaked.
        prior = evidence.country_prior
        if prior:
            top_iso = max(prior, key=lambda k: prior[k])
            values = sorted(prior.values(), reverse=True)
            median = values[len(values) // 2]
            peaked = (prior[top_iso] - median) / max(prior[top_iso], 1e-9)
            if peaked >= 0.30:
                country = self.table.get(top_iso)
                for lang in (country.languages if country else ()):
                    group = ocr_groups.group_for_language(lang)
                    if group:
                        return group
        return None

    # ---------------------------------------------------------- enrichment

    def report_for_fusion(self, fused: FusionResult, label: str | None = None,
                          online: bool = True) -> dict[str, Any]:
        """Dress a fusion result as a report, for results with no one image.

        A combined session answer has no single source photograph, no EXIF
        of its own and no OCR of its own -- it is a conclusion drawn from
        several analyses that each already have those. But it still needs
        naming, candidates and a prediction, and the interface should not
        have to know it is looking at a different shape from every other
        result.
        """
        report = AnalysisReport(ok=False, elapsed_s=0.0)
        report.image = {"filename": label}
        self._enrich(fused, report, online=online)
        report.candidates = [c.as_dict() for c in fused.candidates]
        report.prediction = self._build_prediction(fused, ExifReport())
        report.toponyms = [t.as_dict() for t in fused.toponyms]
        report.read_places = [
            p.as_dict() for p in (fused.read_places or [])
        ]
        report.engine = self.engine_status()
        report.attribution = self.attribution_lines()
        report.ok = True
        return report.as_dict()

    def _enrich(self, fused: FusionResult, report: AnalysisReport,
                online: bool = True) -> None:
        """Name the places. Offline first; online only where permitted."""
        net = self.cfg.network
        online = online and not net.offline_only
        self.nominatim.reset_budget()

        for rank, cand in enumerate(fused.candidates):
            if cand.location is None:
                cand.location = self.geocoder.locate(
                    cand.latitude, cand.longitude, place_limit=4
                )

            info: dict[str, Any] = {}

            # Timezone is always offline.
            info["timezone"] = tz.lookup(cand.latitude, cand.longitude).as_dict()

            # Nominatim: the top few candidates only, inside the budget.
            if online and net.nominatim_enabled and rank < 3:
                result = self.nominatim.reverse(cand.latitude, cand.longitude)
                if result:
                    info["nominatim"] = result.as_dict()
                elif self.nominatim.last_error and rank == 0:
                    report.warnings.append(
                        f"Nominatim lookup failed ({self.nominatim.last_error}); "
                        "place names come from the offline Natural Earth data."
                    )

            # Nearby landmarks, leading candidate only. Local map data
            # first -- an imported region or cached vector tiles, which
            # cost no public server anything -- and Overpass only when
            # someone has pointed it at their own instance and asked.
            if rank == 0:
                surroundings = self._nearby(cand.latitude, cand.longitude,
                                            online, net)
                if surroundings is not None:
                    info["surroundings"] = surroundings.as_dict()
                    if surroundings.error and not surroundings.features:
                        report.warnings.append(
                            f"Nearby-feature lookup skipped: {surroundings.error}"
                        )

            cand.enrichment = info

    def _nearby(self, lat: float, lon: float, online: bool, net):
        """Landmarks near a point: local data, else an opted-in Overpass."""
        if net.overpass_enabled and online:
            return self.overpass.nearby(lat, lon, net.overpass_radius_m)
        try:
            from .geodata.nearby import nearby
            from .geodata.service import get_service
            svc = get_service(online=not net.offline_only)
            return nearby(svc, lat, lon, float(net.overpass_radius_m),
                          fetch=online)
        except Exception as exc:                        # pragma: no cover
            log.warning("nearby features unavailable: %s", exc)
            return None

    def name_coordinate(self, lat: float, lon: float) -> dict[str, Any]:
        """Everything we can say about one coordinate, online included.

        Batch mode calls this once per cluster rather than once per image,
        which is the difference between six Nominatim requests and six
        hundred.
        """
        out: dict[str, Any] = {
            "offline": self.geocoder.locate(lat, lon, place_limit=4).as_dict(),
            "timezone": tz.lookup(lat, lon).as_dict(),
        }
        net = self.cfg.network
        if net.offline_only:
            return out
        if net.nominatim_enabled:
            result = self.nominatim.reverse(lat, lon)
            if result:
                out["nominatim"] = result.as_dict()
            elif self.nominatim.last_error:
                out["nominatim_error"] = self.nominatim.last_error
        return out

    # ---------------------------------------------------------- prediction

    def _build_prediction(self, fused: FusionResult, exif: ExifReport
                          ) -> dict[str, Any]:
        top = fused.candidates[0] if fused.candidates else None

        estimated: dict[str, Any] | None = None
        if top is not None:
            estimated = {
                "latitude": round(top.latitude, 6),
                "longitude": round(top.longitude, 6),
                "probability": round(top.probability, 4),
                "place": self._describe(top),
                "uncertainty_km": round(fused.uncertainty_km, 1),
                "precision_km": (round(fused.precision_km, 2)
                                 if fused.precision_km is not None else None),
                "provenance": top.provenance,
            }

        embedded: dict[str, Any] | None = None
        agreement: dict[str, Any] | None = None
        if exif.gps:
            loc = self.geocoder.locate(
                exif.gps.latitude, exif.gps.longitude, place_limit=3
            )
            embedded = {
                "latitude": exif.gps.latitude,
                "longitude": exif.gps.longitude,
                "altitude_m": exif.gps.altitude_m,
                "timestamp_utc": exif.gps.timestamp_utc,
                "processing_method": exif.gps.processing_method,
                "place": loc.as_dict(),
                "timezone": tz.lookup(
                    exif.gps.latitude, exif.gps.longitude
                ).as_dict(),
                "trust_warnings": exif.trust_warnings,
            }
            if top is not None:
                agreement = self._judge_exif(fused, exif, top)

                # A tag the image independently agrees with is the best
                # coordinate available: a GPS fix is metres where a model
                # estimate is kilometres. Adopt it, and say plainly that
                # this is the tag being *used* rather than the model
                # suddenly becoming precise.
                if agreement["verdict"] == "corroborated" and estimated:
                    estimated["latitude"] = round(exif.gps.latitude, 6)
                    estimated["longitude"] = round(exif.gps.longitude, 6)
                    estimated["provenance"] = (
                        "exif" if top.provenance == "model"
                        else f"{top.provenance}+exif"
                    )
                    # A consumer handset fix is good to roughly this,
                    # and claiming better from a tag would be silly.
                    estimated["precision_km"] = 0.02
                    estimated["place"] = self._describe_point(
                        exif.gps.latitude, exif.gps.longitude
                    )

        return {
            "confidence": fused.confidence,
            "confidence_reason": fused.confidence_reason,
            "diagnostics": fused.diagnostics(),
            "uncertainty_km": round(fused.uncertainty_km, 1),
            "precision_km": (round(fused.precision_km, 2)
                             if fused.precision_km is not None else None),
            "centroid": (
                [round(fused.centroid[0], 6), round(fused.centroid[1], 6)]
                if fused.centroid else None
            ),
            "estimated": estimated,
            "embedded_gps": embedded,
            "exif_vs_estimate": agreement,
            "notes": fused.notes,
            "determinate": fused.confidence != "insufficient" and top is not None,
        }

    # ------------------------------------------------------- exif verdict

    # How far apart the tag and the image estimate may be before they are
    # in conflict. The floor exists because an estimate with a thousand
    # kilometres of spread agrees with almost anything, and calling that
    # corroboration would make the tag self-certifying.
    EXIF_AGREE_FLOOR_KM = 150.0

    def _judge_exif(self, fused: FusionResult, exif: ExifReport,
                    top: Candidate) -> dict[str, Any]:
        """Decide what to do about the GPS tag, rather than measure it.

        The old version reported a distance and a boolean, which leaves the
        decision to the reader at exactly the point where the tool knows
        more than they do. Metadata is the easiest part of a file to edit,
        so a tag is a claim to be tested against the picture -- and the
        test has three outcomes, not two:

          corroborated  the image independently agrees. The tag is then
                        the better coordinate and gets used.
          rejected      they disagree *and* the image evidence is strong
                        enough to say which is wrong.
          unresolved    they disagree and the image evidence is too weak
                        to adjudicate. Neither is trusted, and saying so
                        is the honest answer.
        """
        d = haversine_km(
            top.latitude, top.longitude,
            exif.gps.latitude, exif.gps.longitude,
        )
        tolerance = max(fused.uncertainty_km * 1.5, self.EXIF_AGREE_FLOOR_KM)

        if d <= tolerance:
            return {
                "distance_km": round(d, 1),
                "consistent": True,
                "verdict": "corroborated",
                "note": (
                    f"The tag and the image agree to within {d:.0f} km. Two "
                    f"independent things pointing at one place is the "
                    f"strongest result this tool produces, and the tag is "
                    f"the more precise of the two, so it is the coordinate "
                    f"reported above."
                ),
            }

        # They disagree. Is there enough in the picture to say which is wrong?
        read_leader = "read-address" in top.sources
        strong = (
            read_leader
            or fused.locator_mass >= self.cfg.fusion.high_locator_mass
        )

        if strong:
            why = (
                "an address read from the image itself resolves elsewhere"
                if read_leader else
                f"the locator committed {fused.locator_mass:.0%} of its "
                f"distribution to a different part of the world"
            )
            return {
                "distance_km": round(d, 1),
                "consistent": False,
                "verdict": "rejected",
                "note": (
                    f"The tag is {d:,.0f} km from what the picture shows, and "
                    f"{why}. The tag is rejected: it is the easier of the two "
                    f"to fake, and nothing in the image supports it. It is "
                    f"still reported below, because it is a fact about the "
                    f"file even when it is not a fact about the place."
                ),
            }

        return {
            "distance_km": round(d, 1),
            "consistent": False,
            "verdict": "unresolved",
            "note": (
                f"The tag is {d:,.0f} km from the estimate, and the image "
                f"evidence is too weak to say which is wrong. Neither is "
                f"trusted. If the tag matters, it needs corroborating from "
                f"outside this tool."
            ),
        }

    def _describe_point(self, lat: float, lon: float) -> dict[str, Any]:
        """Name a bare coordinate, for a GPS tag we have decided to adopt."""
        loc = self.geocoder.locate(lat, lon, place_limit=3)
        stand_in = Candidate(
            latitude=lat, longitude=lon, prior_score=1.0, score=1.0,
            location=loc, provenance="exif",
        )
        return self._describe(stand_in)

    def _describe(self, cand: Candidate) -> dict[str, Any]:
        """Best available naming, saying which source each field came from."""
        out: dict[str, Any] = {
            "country": None, "country_code": None, "region": None,
            "city": None, "suburb": None, "road": None, "postcode": None,
            "display_name": None, "source": "natural-earth",
        }
        loc = cand.location
        if loc:
            if loc.country:
                out["country"] = loc.country.name
                out["country_code"] = loc.country.iso_a2
            if loc.admin1:
                out["region"] = loc.admin1.name
            if loc.nearest_places:
                # A city across a border is not where the point is: "Lahore,
                # India" named a point on the Indian side, 27 km from Amritsar.
                same = [p for p in loc.nearest_places
                        if loc.country and p.admin0 == loc.country.name]
                near = same[0] if same else loc.nearest_places[0]
                out["city"] = near.name
                out["nearest_place_km"] = near.distance_km
            out["offshore"] = loc.offshore

        nom = cand.enrichment.get("nominatim")
        if isinstance(nom, dict):
            out["source"] = "nominatim" + (" (cached)" if nom.get("from_cache") else "")
            for key in ("country", "state", "city", "suburb", "road", "postcode"):
                value = nom.get(key)
                if value:
                    out["region" if key == "state" else key] = value
            if nom.get("country_code"):
                out["country_code"] = nom["country_code"]
            if nom.get("display_name"):
                out["display_name"] = nom["display_name"]

        country = self.table.get(out.get("country_code"))
        if country and country.disputed_boundary_note:
            out["boundary_note"] = (
                "This area has a disputed or non-universally recognised "
                "status; the boundary used is the Natural Earth default view."
            )
        return out

    def attribution_lines(self) -> list[str]:
        items = [
            "Country, region and gazetteer data: Natural Earth (public domain)",
            "Geolocation model: GeoCLIP (MIT) on OpenAI CLIP ViT-L/14 (MIT)",
        ]
        if tz.available():
            items.append(tz.ATTRIBUTION)
        if not self.cfg.network.offline_only:
            if self.cfg.network.nominatim_enabled:
                items.append(
                    "Reverse geocoding: Nominatim / OpenStreetMap contributors, ODbL"
                )
            if self.cfg.network.overpass_enabled:
                items.append(
                    "Nearby features: Overpass / OpenStreetMap contributors, ODbL"
                )
            else:
                items.append(
                    "Nearby features and 3D buildings: OpenStreetMap "
                    "contributors, ODbL (local import and OpenFreeMap "
                    "\u00a9 OpenMapTiles); Google Open Buildings, CC BY 4.0"
                )
        if self.ocr and getattr(self.ocr, "name", None) == "easyocr":
            items.append("OCR: EasyOCR (Apache-2.0)")
        return items
