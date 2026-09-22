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

ROOT = Path(__file__).resolve().parent.parent
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
    # Overpass (OpenStreetMap) for nearby named features.
    overpass_enabled: bool = True
    overpass_endpoint: str = "https://overpass-api.de/api/interpreter"
    overpass_min_interval_s: float = 2.0
    overpass_timeout_s: float = 25.0
    overpass_radius_m: int = 1200
    # Sent on every request so operators can identify us, per OSM policy.
    user_agent: str = "Mapify/0.1 (open-source image geolocation; local use)"
    request_timeout_s: float = 20.0
    # Basemap raster tiles for the globe.  Off by default: the globe renders
    # from bundled public-domain Natural Earth vectors with zero requests.
    basemap_tiles_enabled: bool = False
    basemap_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    basemap_attribution: str = "© OpenStreetMap contributors"


@dataclass
class ModelConfig:
    """Which local models to load and how."""

    device: str = "cpu"  # "cpu" | "cuda"
    # Primary locator: GeoCLIP (MIT).  Falls back to the CLIP gazetteer
    # locator if unavailable.
    geoclip_enabled: bool = True
    geoclip_top_k: int = 12
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
    max_total_adjustment: float = 2.5
    # Confidence banding.
    high_prob: float = 0.45
    high_spread_km: float = 160.0
    medium_prob: float = 0.20
    medium_spread_km: float = 900.0
    insufficient_prob: float = 0.10
    insufficient_spread_km: float = 2500.0
    candidates_returned: int = 5
    cluster_radius_km: float = 120.0


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 877
    max_upload_bytes: int = 32 * 1024 * 1024
    # Uploads are processed in memory and never written to disk unless this is
    # switched on (kept for debugging only).
    keep_uploads: bool = False


@dataclass
class Config:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _merge(section: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if hasattr(section, key):
            setattr(section, key, value)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    cfg = Config()
    candidate = Path(path) if path else ROOT / "config.toml"
    if candidate.exists():
        with candidate.open("rb") as fh:
            raw = tomllib.load(fh)
        for name in ("network", "models", "fusion", "server"):
            if isinstance(raw.get(name), dict):
                _merge(getattr(cfg, name), raw[name])
    if os.environ.get("MAPIFY_OFFLINE") in {"1", "true", "yes"}:
        cfg.network.offline_only = True
    return cfg
