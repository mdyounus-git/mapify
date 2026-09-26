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

from .config import ACCEPTED_EXTENSIONS, ACCEPTED_FORMATS, Config
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
                "Use JPG, PNG or WEBP."
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

            # ---- locators -------------------------------------------------
            step("locate", "Estimating coordinates")
            raw: list[RawCandidate] = []
            embedding = None
            evidence = VisualEvidence()

            if self.geoclip and self.geoclip.load():
                try:
                    raw, embedding = self.geoclip.locate_with_embedding(
                        image, self.cfg.models.geoclip_top_k
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
            ocr_result = OcrResult()
            script_reading = None
            if self.ocr:
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
            report.ocr = ocr_result.as_dict()
            if script_reading:
                report.ocr["scripts"] = script_reading.as_dict()

            # ---- fusion ---------------------------------------------------
            step("fuse", "Weighing the evidence")
            fused = self.fusion.fuse(
                raw, evidence=evidence, ocr=ocr_result,
                script_reading=script_reading, extra_candidates=extra,
            )
            report.toponyms = [t.as_dict() for t in fused.toponyms]

            # ---- enrichment -----------------------------------------------
            step("enrich", "Naming the location")
            self._enrich(fused, report, online=online_enrichment)

            report.candidates = [c.as_dict() for c in fused.candidates]
            report.prediction = self._build_prediction(fused, exif_report)
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

            # Overpass: leading candidate only, one query per analysis.
            if online and net.overpass_enabled and rank == 0:
                surroundings = self.overpass.nearby(
                    cand.latitude, cand.longitude, net.overpass_radius_m
                )
                info["surroundings"] = surroundings.as_dict()
                if surroundings.error:
                    report.warnings.append(
                        f"Nearby-feature lookup skipped: {surroundings.error}"
                    )

            cand.enrichment = info

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
                d = haversine_km(
                    top.latitude, top.longitude,
                    exif.gps.latitude, exif.gps.longitude,
                )
                agreement = {
                    "distance_km": round(d, 1),
                    "consistent": d <= max(fused.uncertainty_km * 1.5, 150.0),
                    "note": (
                        "The embedded GPS tag and the image content point to "
                        "the same area."
                        if d <= max(fused.uncertainty_km * 1.5, 150.0)
                        else
                        "The embedded GPS tag disagrees with what the image "
                        "looks like. One of them is wrong, and the tag is the "
                        "easier of the two to fake."
                    ),
                }

        return {
            "confidence": fused.confidence,
            "confidence_reason": fused.confidence_reason,
            "diagnostics": fused.diagnostics(),
            "uncertainty_km": round(fused.uncertainty_km, 1),
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
                near = loc.nearest_places[0]
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
        if self.ocr and getattr(self.ocr, "name", None) == "easyocr":
            items.append("OCR: EasyOCR (Apache-2.0)")
        return items
