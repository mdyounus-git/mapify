"""Earthquakes and natural events around a place (GET /api/events/near).

Context, never evidence: the USGS catalogue and NASA EONET, read through
one-at-a-time cached requests that send only a coordinate and dates.
Nothing here touches the network.
"""

from __future__ import annotations

import io
import json
import urllib.request
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from mapify.config import load_config
from mapify.geodata import events as events_mod
from mapify.geodata.events import NearbyEvents
from mapify.server import create_app

USGS = {"features": [
    {"properties": {"mag": 4.9, "place": "3 km WSW of Shibayama, Japan",
                    "time": 1791400430830, "url": "https://earthquake.usgs.gov/x"},
     "geometry": {"coordinates": [140.39, 35.69, 40.2]}},
    {"properties": {"mag": None, "place": "unknown", "time": 1},
     "geometry": {"coordinates": [140.0, 35.0, 10]}},
]}
EONET = {"events": [
    {"title": "La Palma Volcano, Canary Islands, Spain", "closed": None,
     "categories": [{"title": "Volcanoes"}],
     "sources": [{"url": "https://volcano.si.edu/volcano.cfm?vn=383010"}],
     "geometry": [{"type": "Point", "date": "2021-09-19T00:00:00Z",
                   "coordinates": [-17.83, 28.57]},
                  {"type": "Point", "date": "2021-09-25T00:00:00Z",
                   "coordinates": [-17.84, 28.57]}]},
    {"title": "A fire far away", "categories": [{"title": "Wildfires"}],
     "geometry": [{"type": "Point", "date": "2021-09-20T00:00:00Z",
                   "coordinates": [10.0, 50.0]}]},
]}


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake(monkeypatch, calls):
    def urlopen(req, timeout=30):
        calls.append(req.full_url)
        assert req.get_header("User-agent", "").startswith("Mapify/")
        body = USGS if "earthquake.usgs.gov" in req.full_url else EONET
        return Reply(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(events_mod.time, "sleep", lambda s: None)


def test_quakes_and_events_are_read_and_kept(tmp_path, monkeypatch):
    calls = []
    _fake(monkeypatch, calls)
    n = NearbyEvents(tmp_path)
    quakes = n.earthquakes(35.68, 139.76)
    assert [q["magnitude"] for q in quakes] == [4.9]          # no magnitude: dropped
    assert quakes[0]["source"] == "USGS" and 50 < quakes[0]["km"] < 70
    when = datetime(2021, 9, 20, tzinfo=timezone.utc)
    events = n.natural(28.57, -17.84, around=when)
    assert [e["title"] for e in events] == ["La Palma Volcano, Canary Islands, Spain"]
    assert events[0]["date"] == "2021-09-25T00:00:00Z"        # its latest position
    assert events[0]["url"].startswith("https://volcano.si.edu/")
    # Thirty days either side of the photograph's time.
    assert "start=2021-08-21&end=2021-10-20" in calls[-1] and "status=all" in calls[-1]
    before = len(calls)
    n.natural(28.57, -17.84, around=when)
    assert len(calls) == before, "a past date is answered from the disk"


def test_only_a_coordinate_and_dates_go_out(tmp_path, monkeypatch):
    calls = []
    _fake(monkeypatch, calls)
    NearbyEvents(tmp_path).near(35.68, 139.76)
    for url in calls:
        query = url.split("?", 1)[1]
        keys = {kv.split("=", 1)[0] for kv in query.split("&")}
        assert keys <= {"format", "latitude", "longitude", "maxradiuskm", "starttime",
                        "endtime", "minmagnitude", "orderby", "limit",
                        "bbox", "start", "end", "status"}


def test_offline_nothing_is_fetched(tmp_path, monkeypatch):
    calls = []
    _fake(monkeypatch, calls)
    got = NearbyEvents(tmp_path, online=False).near(35.68, 139.76)
    assert got["earthquakes"] == [] and got["events"] == [] and calls == []


def _client(offline: bool = False, enabled: bool = True) -> TestClient:
    cfg = load_config()
    cfg.network.offline_only = offline
    cfg.network.events_enabled = enabled
    cfg.models.geoclip_enabled = False
    cfg.models.clip_evidence_enabled = False
    cfg.models.ocr_enabled = False
    return TestClient(create_app(cfg))


def test_the_endpoint_says_when_it_is_off():
    assert _client(offline=True).get("/api/events/near?lat=1&lon=1").json() == {
        "available": False, "reason": "offline"}
    got = _client(enabled=False).get("/api/events/near?lat=1&lon=1").json()
    assert got["available"] is False and "switched off" in got["reason"]
    assert _client().get("/api/events/near?lat=95&lon=1").status_code == 400


def test_the_endpoint_passes_the_photographs_time(monkeypatch, tmp_path):
    seen = {}

    def near(self, lat, lon, around=None):
        seen["around"] = around
        return {"available": True, "earthquakes": [], "events": [],
                "attribution": events_mod.ATTRIBUTION}

    monkeypatch.setattr(NearbyEvents, "near", near)
    monkeypatch.setenv("MAPIFY_STATE_DIR", str(tmp_path))
    got = _client().get("/api/events/near?lat=28.57&lon=-17.84"
                        "&at=2021-09-20T10:00:00Z").json()
    assert got["available"] is True and "EONET" in got["attribution"]
    assert seen["around"] == datetime(2021, 9, 20, 10, tzinfo=timezone.utc)
