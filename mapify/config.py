"""Configuration.

Loaded from ``config.toml`` next to the repository root when present, else
defaults.  Every network-touching feature is listed here so that a reader can
tell, from one file, exactly what the tool may talk to.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .paths import config_path, read_root, state_dir

ROOT = read_root()

# The image formats Mapify will process. Declared here rather than in the
# pipeline so the desktop shell can filter its file dialog without importing
# the model stack -- the window should open before PyTorch does. HEIC/HEIF
# decodes only with the optional decoder (see heif.py); it is listed either
# way so a HEIC file reaches the analysis and is told how to add it.
ACCEPTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "MPO", "HEIF"})
ACCEPTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})
DATA_DIR = ROOT / "data"
PKG_DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass
class NetworkConfig:
    """Outbound network policy.

    ``offline_only`` is the master switch: when true nothing in the process may
    make an outbound request, and the enrichment steps degrade to the bundled
    Natural Earth data.
    """

    offline_only: bool = False
    # Nominatim (OpenStreetMap).  Usage policy requires an identifying
    # User-Agent and at most 1 request/second.  See ATTRIBUTION.md.
    nominatim_enabled: bool = True
    nominatim_endpoint: str = "https://nominatim.openstreetmap.org"
    nominatim_min_interval_s: float = 1.1
    nominatim_max_lookups_per_analysis: int = 6
    # Nearby named features come from local map data now: an imported
    # OpenStreetMap region, or OpenFreeMap vector tiles fetched once and
    # kept (mapify/geodata/nearby.py). The public Overpass servers are off
    # by default because their usage policy names relying on them as an
    # application's backend as the thing not to do, and one query per
    # analysis is exactly that. Point overpass_endpoint at your own
    # instance and switch this on if you run one.
    overpass_enabled: bool = False
    # Historical weather and air quality at the leading candidate for the
    # capture hour (techniques 61, 95-97): Open-Meteo, ERA5 and CAMS. Only
    # a coordinate and a date are sent, never the photograph. Free for
    # non-commercial use; a commercial deployment needs Open-Meteo's paid
    # plan, or this switched off.
    weather_history_enabled: bool = True
    # Earthquakes (the USGS catalogue) and natural events (NASA EONET:
    # fires, volcanoes, storms) around the located place, at the
    # photograph's time or lately: context in the 3D view, never evidence.
    # Both public domain, no key. Only a coordinate and dates are sent.
    events_enabled: bool = True
    # Pinpoint (mapify/pinpoint): asks Wikimedia Commons which freely
    # licensed geotagged photographs exist near the candidates, downloads
    # their thumbnails and matches them locally. Coordinates go out; the
    # photograph never does. Runs only when asked for.
    pinpoint_enabled: bool = True
    # Pinpoint keeps reference thumbnails and their matching features so a
    # second search nearby costs nothing; past this size the ones used
    # longest ago are deleted.
    pinpoint_cache_mb: int = 2048
    # Pinpoint also asks Panoramax (api.panoramax.xyz), the open commons of
    # street-level imagery, for pictures near each candidate: no account,
    # Etalab 2.0 or CC BY-SA 4.0, most with the camera's heading. Dense in
    # France and Germany, sparse or absent elsewhere. Only a coordinate box
    # is sent.
    pinpoint_panoramax: bool = True
    # A Mapillary access token, from your own free Mapillary account, adds
    # its street-level imagery (CC BY-SA 4.0) to Pinpoint. Empty: Commons
    # only. Kept in your config file; never written anywhere else.
    mapillary_token: str = ""
    overpass_endpoint: str = "https://overpass-api.de/api/interpreter"
    overpass_min_interval_s: float = 2.0
    overpass_timeout_s: float = 25.0
    overpass_radius_m: int = 1200
    # Sent on every request so operators can identify us, per OSM policy.
    user_agent: str = "Mapify/0.1 (open-source image geolocation; local use)"
    request_timeout_s: float = 20.0
    # Basemap raster tiles for the globe.  Off by default: the globe renders
    # from bundled public-domain Natural Earth vectors with zero requests.
    # Ground imagery for the end of the approach.
    #
    # On by default, which was only possible once there was a source whose
    # terms permit it. The previous default pointed at OpenStreetMap's own
    # tile servers: donated capacity with a usage policy that a desktop
    # application fetching on every landing would breach, which is why it
    # had to ship switched off and therefore never ran.
    #
    # NASA's Global Imagery Browse Services states its own terms in its
    # WMTS capabilities document, in as many words:
    #
    #     <ows:Fees>none</ows:Fees>
    #     <ows:AccessConstraints>none</ows:AccessConstraints>
    #     <ows:ProviderName>National Aeronautics and Space
    #     Administration</ows:ProviderName>
    #
    # NASA imagery is not copyrighted; attribution is requested and given.
    # No key, no account, no rate limit published.
    #
    # Landsat WELD is a cloud-free global annual composite at roughly 38 m
    # per pixel -- about 130 times finer than the 4.9 km per pixel of the
    # bundled Blue Marble texture. At that scale a river, a coastline, an
    # airport, a park and the shape of a city are all legible. Streets are
    # not: see the note in the README about what is and is not available
    # under a licence that allows redistribution.
    #
    # Note the {z}/{y}/{x} order. WMTS is row-then-column, the opposite of
    # the XYZ convention, and getting it the wrong way round produces
    # plausible imagery of entirely the wrong place.
    basemap_tiles_enabled: bool = True
    basemap_tile_url: str = (
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
        "Landsat_WELD_CorrectedReflectance_TrueColor_Global_Annual/default/"
        "2000-12-01/GoogleMapsCompatible_Level12/{z}/{y}/{x}.jpeg"
    )
    # No "Ground imagery:" prefix -- the interface adds "Ground detail: "
    # in front of whatever this says, and the two together read as
    # "Ground detail: Ground imagery: NASA...".
    basemap_attribution: str = "NASA EOSDIS GIBS, Landsat WELD. Public domain."
    # The deepest zoom the source actually has. Asking for more returns
    # 404s, and a grid of missing tiles looks like a broken renderer
    # rather than like the end of the available detail.
    basemap_max_zoom: int = 12


@dataclass
class ModelConfig:
    """Which local models to load and how."""

    device: str = "cpu"  # "cpu" | "cuda"
    # Primary locator: GeoCLIP (MIT).  Falls back to the CLIP gazetteer
    # locator if unavailable.
    geoclip_enabled: bool = True
    geoclip_top_k: int = 12
    # Select candidate *places* by how much probability sits around them,
    # rather than taking the individual gallery points with the highest
    # probability. The gallery is not evenly dense, so the two differ: a
    # city covered by fifty points at half a per cent each holds a
    # quarter of the distribution and can contribute nothing to a top-12
    # chosen point by point.
    #
    # Measured over 163 photographs, the point-wise top-12 held a median
    # of 14.5% of the distribution. Mode finding holds about 99%, for the
    # same cost -- the distribution was already being computed inside
    # locate() and thrown away.
    geoclip_mode_search: bool = True
    # How far apart two places have to be to count as different ones,
    # and how far from a mode's peak its reported coordinate is averaged.
    #
    # Swept over the 163-sample benchmark against the locator alone. The
    # pairing matters more than either number: gathering wide finds the
    # right region, averaging wide then throws away the precision found
    # inside it.
    #
    #   radius  focus   median   <=1km  <=25km  <=200km
    #   point-wise        594.6     28      50       64
    #      40      5      427.4     33      62       73
    #      75     15      385.3     27      59       75
    #     150      5      385.3     30      55       72
    #
    # 40/5 beats point-wise selection on every band, which none of the
    # wider settings do.
    geoclip_mode_radius_km: float = 40.0
    geoclip_mode_focus_km: float = 5.0
    # Secondary locator / evidence backbone: HF CLIP (MIT weights).
    clip_model_id: str = "openai/clip-vit-large-patch14"
    clip_evidence_enabled: bool = True
    gazetteer_locator_enabled: bool = True
    # OCR is optional; EasyOCR (Apache-2.0) if installed.
    ocr_enabled: bool = True
    ocr_languages: list[str] = field(default_factory=lambda: ["en"])
    # Extra EasyOCR language groups for a multi-script sweep.  Each group is a
    # separate model load, so this is slow; empty by default.
    ocr_extra_groups: list[list[str]] = field(default_factory=list)
    # Place names read off a sign are also looked up in GeoNames (CC BY 4.0;
    # built by scripts/fetch_research_data.py geonames, then
    # python -m mapify.geo.geonames) when Natural Earth knows no such place.
    # Off until measured on the text-bearing benchmark: a match moves the
    # estimate, and nothing moves it before it has been measured to help.
    geonames_enabled: bool = False
    # Where photographs like this one were taken, from a local memory of
    # street-level photographs (OpenStreetView-5M, CC BY-SA 4.0; built by
    # scripts/build_retrieval.py). Off until measured on the benchmark.
    retrieval_enabled: bool = False
    # The language of text read off the photograph (GlotLID, Apache-2.0 plus
    # notice) and the countries that speak it (Unicode CLDR). Reported in
    # technique 12, weight zero until measured; the 1.7 GB model is loaded
    # only when enough letters were read.
    language_id_enabled: bool = True
    # A second, independent locator (PLONK, MIT; DINOv2, Apache-2.0): its
    # places join the candidates and its spread says how placeable the
    # photograph is. 10-20 s more an analysis on a CPU; off until measured.
    plonk_enabled: bool = False
    max_image_side: int = 1600


@dataclass
class FusionConfig:
    """Weights for evidence-driven re-ranking.

    Each weight is the maximum log-odds adjustment a fully-confident piece of
    evidence may apply to a candidate.  They are deliberately modest: evidence
    re-ranks the model's candidates, it never invents new ones.
    """

    w_driving_side: float = 0.90
    w_script: float = 1.10
    w_toponym: float = 1.60
    w_climate: float = 0.55
    w_plate: float = 0.45
    w_region_hint: float = 0.50
    # Narrow clues read off the image as text -- a calling code, a
    # country-code domain, a currency sign, a postcode, miles
    # (evidence/text_clues.py). Zero until measured. Measured 2026-10-06
    # (scripts/measure_text_clues.py, PHASES/Implemented Phases/PHASE-27):
    # none of the 163 benchmark photographs carries such a clue, so no
    # weight changes any answer there and none can be fitted. It needs a
    # text-bearing benchmark before it may be anything but zero.
    w_text_clue: float = 0.0
    max_total_adjustment: float = 2.5
    # ---- Confidence banding -------------------------------------------
    #
    # These are not guesses. They were fitted by scripts/tune_confidence.py
    # over a 163-sample benchmark, searching on half and validating on the
    # other half, and the held-out numbers are recorded in
    # PHASES/Implemented Phases/PHASE-13.
    #
    # Refitted when the locator switched from point-wise top-k to mode
    # finding, which had to happen: the old numbers were calibrated
    # against a locator_mass whose median was 0.145, and mode finding
    # captures about 99% of the distribution, so every one of them would
    # have been trivially satisfied and everything would have read
    # "high". Thresholds are only meaningful against the scale they were
    # measured on, and changing the scale silently is how a calibrated
    # tool stops being one.
    #
    # Refit: 100% precision on 17 high-confidence calls on the tuning
    # half, 100% of 11 on the held-out half, worst error 47 km.
    #
    # The earlier values came from an 11-sample set and were badly wrong:
    # "high" was reached by results 15,689 km off target.
    #
    # "high" requires all four of these. Mass is the primary gate -- it is
    # what actually separates a correct prediction from a confidently wrong
    # one -- and dispersion is secondary.
    high_locator_mass: float = 0.15
    high_spread_km: float = 40.0
    high_concentration: float = 0.80
    # The search wanted -1.0, which is no gate at all. Kept at 0: the
    # search maximises how many high calls can be made at full
    # precision, and this project would rather make fewer. "High
    # requires the image to support the answer, not merely fail to
    # object" is a stance from Phase 13, and being stricter than the
    # fitted optimum can only improve precision, never harm it.
    high_min_evidence: float = 0.0

    # "medium" is a weaker claim and the data supports about 85-90% of these
    # landing within 1500 km. It is not a location; it is a region.
    medium_locator_mass: float = 0.08
    medium_spread_km: float = 200.0
    medium_concentration: float = 0.20

    insufficient_spread_km: float = 2500.0

    # Below this the locator recognised nothing in particular and we say so.
    min_locator_mass: float = 0.03

    # Net log-odds below which the evidence is treated as contradicting.
    contradiction_threshold: float = -0.35

    candidates_returned: int = 5
    cluster_radius_km: float = 120.0


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8771
    max_upload_bytes: int = 32 * 1024 * 1024
    # Uploads are processed in memory and never written to disk unless this is
    # switched on (kept for debugging only).
    keep_uploads: bool = False


@dataclass
class RecordsConfig:
    # The persistent case file the specification asks for: every
    # technique's finding and the provenance of each, written per image to
    # data/cases/<content hash>.json, and a small index (the estimate, a
    # 64-bit image hash and the encoder's embedding) that lets a later
    # analysis recognise the same photograph or the same place. The image
    # itself is never stored, and nothing leaves the machine. Switch off
    # to keep no record at all.
    case_files: bool = True


@dataclass
class Config:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    records: RecordsConfig = field(default_factory=RecordsConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _merge(section: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if hasattr(section, key):
            setattr(section, key, value)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    cfg = Config()
    candidate = Path(path) if path else config_path()
    if candidate.exists():
        with candidate.open("rb") as fh:
            raw = tomllib.load(fh)
        for name in ("network", "models", "fusion", "server", "records"):
            if isinstance(raw.get(name), dict):
                _merge(getattr(cfg, name), raw[name])
    if os.environ.get("MAPIFY_OFFLINE") in {"1", "true", "yes"}:
        cfg.network.offline_only = True
    return cfg
