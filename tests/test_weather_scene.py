"""The weather the 3D walk is drawn in (GET /api/weather/scene).

Off when offline or when weather history is switched off, and said so; a
capture time is passed through as a time; a reading comes back with its
attribution. Nothing here touches the network.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from mapify.config import load_config
from mapify.geodata import weather as weather_mod
from mapify.server import create_app


def _client(offline: bool = False, enabled: bool = True) -> TestClient:
    cfg = load_config()
    cfg.network.offline_only = offline
    cfg.network.weather_history_enabled = enabled
    cfg.models.geoclip_enabled = False
    cfg.models.clip_evidence_enabled = False
    cfg.models.ocr_enabled = False
    return TestClient(create_app(cfg))


def test_offline_or_switched_off_says_so():
    assert _client(offline=True).get("/api/weather/scene?lat=1&lon=1").json() == {
        "available": False, "reason": "offline"}
    got = _client(enabled=False).get("/api/weather/scene?lat=1&lon=1").json()
    assert got["available"] is False and "switched off" in got["reason"]


def test_a_reading_comes_back_with_its_source(monkeypatch, tmp_path):
    seen = {}

    def scene(self, lat, lon, when):
        seen["when"] = when
        return {"source": "ERA5", "code": 63, "description": "rain",
                "attribution": weather_mod.ATTRIBUTION}

    monkeypatch.setattr(weather_mod.WeatherHistory, "scene", scene)
    monkeypatch.setenv("MAPIFY_STATE_DIR", str(tmp_path))
    c = _client()
    got = c.get("/api/weather/scene?lat=48.85&lon=2.29&at=2024-01-15T12:00:00Z").json()
    assert got["available"] is True and got["description"] == "rain"
    assert "Open-Meteo" in got["attribution"]
    assert seen["when"] == datetime(2024, 1, 15, 12, tzinfo=timezone.utc)
    assert c.get("/api/weather/scene?lat=1&lon=1&at=yesterday").status_code == 400
    assert c.get("/api/weather/scene?lat=100&lon=1").status_code == 400
