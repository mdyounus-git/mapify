"""Pinpoint: finding the exact spot by matching a geotagged photograph.

Geometric matching is tested both ways -- a second view of the same scene
must clear the confirmation line, an unrelated photograph must stay far
below it -- and the engine's verdicts with local stand-ins for Commons:
a match adopts the reference's location, no match leaves the estimate
alone and says so. Nothing here touches the network.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from mapify.geo.geodesy import haversine_km
from mapify.pinpoint import engine as engine_mod
from mapify.pinpoint import sources
from mapify.pinpoint.engine import STRONG_INLIERS, WEAK_INLIERS, Area, PinpointEngine
from mapify.pinpoint.features import extract, match, windows
from mapify.pinpoint.sources import Reference


def _scene(seed: int, size=(900, 650)) -> Image.Image:
    """A structured 'street': rectangles, windows and noise, like facades."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", size, (128, 128, 128))
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = int(rng.integers(0, size[0] - 60)), int(rng.integers(0, size[1] - 60))
        w, h = int(rng.integers(20, 140)), int(rng.integers(20, 140))
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        d.rectangle([x, y, x + w, y + h], fill=c, outline=(0, 0, 0))
        for k in range(int(rng.integers(1, 5))):
            d.rectangle([x + 4 + 10 * k, y + 4, x + 10 + 10 * k, y + 14], fill=(250, 250, 240))
    a = np.asarray(img).astype(np.int16) + rng.integers(-12, 12, (size[1], size[0], 3))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _second_view(img: Image.Image) -> Image.Image:
    """The same scene from a little to one side, in different light."""
    import cv2
    a = np.asarray(img)
    h, w = a.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[w * .06, h * .04], [w * .95, 0], [w, h * .97], [w * .02, h]])
    out = cv2.warpPerspective(a, cv2.getPerspectiveTransform(src, dst), (w, h))
    buf = io.BytesIO()
    Image.fromarray((out * 0.85).astype(np.uint8)).save(buf, "JPEG", quality=75)
    return Image.open(io.BytesIO(buf.getvalue()))


# --------------------------------------------------------------- matching

def test_a_second_view_of_the_same_scene_clears_the_confirmation_line():
    q = extract(_scene(1))
    r = extract(_second_view(_scene(1)), 640)
    assert match(q, r).inliers >= STRONG_INLIERS


def test_an_unrelated_scene_stays_below_even_the_weak_line():
    q = extract(_scene(1))
    r = extract(_scene(2), 640)
    assert match(q, r).inliers < WEAK_INLIERS


def test_a_featureless_image_matches_nothing():
    blank = Image.new("RGB", (640, 480), (90, 140, 200))
    assert len(extract(blank).points) == 0
    assert match(extract(blank), extract(_scene(3), 640)).inliers == 0


def test_stored_features_come_back_exactly_and_compact(tmp_path):
    from mapify.pinpoint.features import load, save
    f = extract(_scene(6), 640)
    save(f, tmp_path / "f.npz")
    back = load(tmp_path / "f.npz")
    assert np.array_equal(back.descriptors, f.descriptors)
    assert back.size == f.size
    with np.load(tmp_path / "f.npz") as z:
        assert z["descriptors"].dtype == np.uint8


def test_the_cache_is_pruned_oldest_used_first(tmp_path):
    import os
    eng = PinpointEngine(tmp_path, cache_mb=1)
    (tmp_path / "thumbs").mkdir()
    for i in range(6):                                   # 6 x 300 KB > 1 MB
        f = tmp_path / "thumbs" / f"{i}.jpg"
        f.write_bytes(b"x" * 300_000)
        os.utime(f, (1000 + i, 1000 + i))
    assert eng.prune() > 0
    left = sorted(p.name for p in (tmp_path / "thumbs").iterdir())
    assert left == ["3.jpg", "4.jpg", "5.jpg"][-len(left):] and "0.jpg" not in left
    assert sum(p.stat().st_size for p in (tmp_path / "thumbs").iterdir()) <= 0.8 * 2**20


def test_a_panorama_is_matched_in_windows():
    pano = extract(_scene(4, size=(2400, 500)))
    views = windows(pano)
    assert len(views) >= 3
    assert all(v.size[0] < 2400 for v in views)
    assert windows(extract(_scene(5)))[0].size == extract(_scene(5)).size


# ----------------------------------------------------------------- engine

def _ref(tmp_path: Path, name: str, img: Image.Image, lat: float, lon: float) -> Reference:
    path = tmp_path / f"{name}.jpg"
    img.save(path, "JPEG", quality=90)
    return Reference(source="commons", id=f"File:{name}.jpg", lat=lat, lon=lon,
                     thumb_url=path.resolve().as_uri(), page_url=f"https://example.org/{name}",
                     licence="CC BY-SA 4.0", author="Tester", title=f"{name}.jpg")


@pytest.fixture
def fake_commons(monkeypatch):
    """Resolved local references, listed wherever the engine asks."""
    refs: list[Reference] = []
    monkeypatch.setattr(engine_mod, "commons_listing",
                        lambda lat, lon, radius, cache: list(refs))
    return refs


def _bare(name: str, lat: float, lon: float) -> Reference:
    return Reference(source="commons", id=f"File:{name}.jpg", lat=lat, lon=lon,
                     thumb_url="", page_url="", licence="", author="", title=f"{name}.jpg")


def test_a_match_pinpoints_the_reference_location(tmp_path, fake_commons):
    fake_commons += [_ref(tmp_path, "unrelated", _scene(7), 12.9700, 77.5800),
                     _ref(tmp_path, "same-corner", _second_view(_scene(1)), 12.9794, 77.5850)]
    eng = PinpointEngine(tmp_path / "cache", workers=2)
    res = eng.run(_scene(1), [Area(12.97, 77.58, 1500, "the estimate")])
    assert res["verdict"] == "pinpointed"
    assert res["location"]["lat"] == pytest.approx(12.9794)
    assert res["location"]["precision_m"] >= 150
    assert res["best"]["title"] == "same-corner.jpg"
    assert res["best"]["licence"] == "CC BY-SA 4.0" and res["best"]["author"] == "Tester"
    json.dumps(res)                                   # the server streams it as JSON


def test_no_match_leaves_the_estimate_and_says_so(tmp_path, fake_commons):
    fake_commons += [_ref(tmp_path, f"other-{i}", _scene(20 + i), 12.97, 77.58)
                     for i in range(3)]
    res = PinpointEngine(tmp_path / "cache", workers=2).run(
        _scene(1), [Area(12.97, 77.58, 1500)])
    assert res["verdict"] == "none" and res["location"] is None
    assert "estimate stands" in res["message"]


def test_excluded_references_are_never_matched(tmp_path, fake_commons):
    fake_commons.append(_ref(tmp_path, "itself", _scene(1), 12.97, 77.58))
    res = PinpointEngine(tmp_path / "cache", workers=2).run(
        _scene(1), [Area(12.97, 77.58, 1500)], exclude={"File:itself.jpg"})
    assert res["verdict"] == "none"


def test_a_dense_area_is_covered_by_more_searches_not_just_its_middle(tmp_path, monkeypatch):
    # 500 photographs within 300 m of the centre (a city centre), and the
    # one that matters 900 m out: only a search placed further out lists it.
    crowd = [_bare(f"crowd-{i}", 12.97 + (i % 25) * 1e-4, 77.58 + (i // 25) * 1e-4)
             for i in range(500)]
    calls = []
    far = (12.97 + 900 / 111320.0, 77.58)

    def listing(lat, lon, radius, cache):
        calls.append((lat, lon, radius))
        if len(calls) == 1:
            return list(crowd)
        in_reach = haversine_km(lat, lon, *far) * 1000 <= radius
        return [_bare("the-corner", *far)] if in_reach else []

    monkeypatch.setattr(engine_mod, "commons_listing", listing)
    order, notes, cover = PinpointEngine(tmp_path)._plan(
        [Area(12.97, 77.58, 1500)], lambda k, m: None, set())
    assert len(calls) == engine_mod.PROBES_PER_AREA          # centre + rings
    assert calls[1][2] < 1500                                # probes sized by the reach
    assert "File:the-corner.jpg" in {r.id for r in order}
    assert cover[0]["probes"] == engine_mod.PROBES_PER_AREA


def test_a_crowded_spot_inside_the_area_gets_a_ring_of_its_own(tmp_path, monkeypatch):
    # The centre fills 500 within ~300 m; one ring search lands on a spot
    # so crowded it fills 500 within 40 m (a landmark's foot).
    calls = []

    def listing(lat, lon, radius, cache):
        calls.append((lat, lon, radius))
        if len(calls) == 1:
            return [_bare(f"c-{i}", 12.97 + (i % 25) * 1e-4, 77.58 + (i // 25) * 1e-4)
                    for i in range(500)]
        if len(calls) == 2:
            out = [_bare(f"d-{i}", lat + (i % 20) * 1.5e-5, lon + (i // 20) * 1.5e-5)
                   for i in range(500)]
            for r in out:
                r.distance_m = haversine_km(lat, lon, r.lat, r.lon) * 1000
            return sorted(out, key=lambda r: r.distance_m)
        return []

    monkeypatch.setattr(engine_mod, "commons_listing", listing)
    PinpointEngine(tmp_path)._plan([Area(12.97, 77.58, 1500)], lambda k, m: None, set())
    assert len(calls) == engine_mod.PROBES_PER_AREA + engine_mod.DENSE_PROBES
    assert all(r < 100 for _, _, r in calls[engine_mod.PROBES_PER_AREA:])


def test_repeat_shots_from_one_spot_are_pushed_back(tmp_path, monkeypatch):
    # Five shots from one spot 100 m away: after the first, each is pushed
    # back 120 / sqrt(5) = 54 m, so a lone photograph at 130 m comes second.
    spot = [_bare(f"spot-{i}", 12.97 + 100 / 111320.0, 77.58) for i in range(5)]
    lone = _bare("lone", 12.97 - 130 / 111320.0, 77.58)
    monkeypatch.setattr(engine_mod, "commons_listing",
                        lambda lat, lon, radius, cache: spot + [lone])
    order, _, _ = PinpointEngine(tmp_path)._plan(
        [Area(12.97, 77.58, 1000)], lambda k, m: None, set())
    assert order[0].id.startswith("File:spot-")
    assert order[1].id == "File:lone.jpg"                    # before the 2nd shot


def test_a_popular_viewpoint_gets_more_comparisons_than_a_quiet_one(tmp_path, monkeypatch):
    busy = [_bare(f"busy-{i}", 12.97 + 300 / 111320.0, 77.58) for i in range(100)]
    quiet = [_bare(f"quiet-{i}", 12.97 - 300 / 111320.0, 77.58) for i in range(4)]
    monkeypatch.setattr(engine_mod, "commons_listing",
                        lambda lat, lon, radius, cache: busy + quiet)
    order, _, _ = PinpointEngine(tmp_path)._plan(
        [Area(12.97, 77.58, 1000)], lambda k, m: None, set())
    first = [r.id for r in order[:12]]
    assert sum("busy" in i for i in first) > sum("quiet" in i for i in first) >= 2


def test_the_search_stops_once_a_match_confirms(tmp_path, fake_commons, monkeypatch):
    monkeypatch.setattr(engine_mod, "BATCH", 2)
    fake_commons += [_ref(tmp_path, "same-corner", _second_view(_scene(1)), 12.9701, 77.5801),
                     _ref(tmp_path, "close-other", _scene(8), 12.9702, 77.5802)]
    fake_commons += [_ref(tmp_path, f"far-{i}", _scene(30 + i), 12.975 + i * 1e-3, 77.58)
                     for i in range(3)]
    res = PinpointEngine(tmp_path / "cache", workers=2).run(
        _scene(1), [Area(12.97, 77.58, 1500)])
    assert res["verdict"] == "pinpointed"
    assert res["searched"]["references"] == 2 and res["searched"]["listed"] == 5


def test_the_budget_caps_the_comparison_and_says_what_is_left(tmp_path, fake_commons):
    fake_commons += [_ref(tmp_path, f"other-{i}", _scene(40 + i), 12.97 + i * 1e-4, 77.58)
                     for i in range(4)]
    res = PinpointEngine(tmp_path / "cache", workers=2).run(
        _scene(1), [Area(12.97, 77.58, 1500)], budget=2)
    assert res["verdict"] == "none"
    assert res["searched"]["references"] == 2 and res["searched"]["listed"] == 4
    assert "2 more listed" in res["message"]


def test_offline_and_no_area_are_unavailable(tmp_path):
    assert PinpointEngine(tmp_path, online=False).run(
        _scene(1), [Area(0, 0, 500)])["verdict"] == "unavailable"
    assert PinpointEngine(tmp_path).run(_scene(1), [])["verdict"] == "unavailable"


def test_a_failed_search_is_not_reported_as_an_empty_area(tmp_path, monkeypatch):
    import urllib.error

    def down(lat, lon, radius, cache):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(engine_mod, "commons_listing", down)
    res = PinpointEngine(tmp_path).run(_scene(1), [Area(12.97, 77.58, 800)])
    assert res["verdict"] == "unavailable" and "failed" in res["message"]


def test_a_featureless_photograph_is_refused_before_any_search(tmp_path, fake_commons):
    res = PinpointEngine(tmp_path).run(Image.new("RGB", (800, 600), (100, 150, 210)),
                                       [Area(1, 1, 500)])
    assert res["verdict"] == "none" and "distinctive points" in res["message"]


# ----------------------------------------------------------------- sources

def _commons_answer(pages: list[dict]) -> callable:
    listing = {"query": {"geosearch": [{"title": p["title"], "lat": p["lat"],
                                        "lon": p["lon"]} for p in pages]}}
    details = {"query": {"pages": {str(i): {
        "title": p["title"],
        "imageinfo": [{"mime": p.get("mime", "image/jpeg"), "thumburl": "https://t/x.jpg",
                       "descriptionurl": "https://c/x",
                       "extmetadata": {"LicenseShortName": {"value": p["licence"]},
                                       "Artist": {"value": "<a>Someone</a>"}}}]}
        for i, p in enumerate(pages)}}}
    return lambda url, timeout=30.0: listing if "geosearch" in url else details


def test_commons_keeps_free_photographs_nearest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "_get_json", _commons_answer([
        {"title": "File:far.jpg", "lat": 12.99, "lon": 77.59, "licence": "CC BY 4.0"},
        {"title": "File:near.jpg", "lat": 12.9701, "lon": 77.5801, "licence": "CC BY-SA 3.0"},
        {"title": "File:unfree.jpg", "lat": 12.9702, "lon": 77.58, "licence": "Fair use"},
        {"title": "File:map.svg", "lat": 12.9703, "lon": 77.58, "licence": "CC0",
         "mime": "image/svg+xml"},
    ]))
    refs = sources.commons_near(12.97, 77.58, 3000, tmp_path)
    assert [r.title for r in refs] == ["near.jpg", "far.jpg"]
    assert refs[0].author == "Someone" and refs[0].distance_m < 50


def test_details_are_asked_for_once_and_unfree_files_dropped(tmp_path, monkeypatch):
    calls = []
    answer = _commons_answer([
        {"title": "File:a.jpg", "lat": 1, "lon": 1, "licence": "CC BY 4.0"},
        {"title": "File:b.jpg", "lat": 1, "lon": 1, "licence": "All rights reserved"}])
    monkeypatch.setattr(sources, "_get_json",
                        lambda url, timeout=30.0: calls.append(url) or answer(url))
    first = sources.commons_details([_bare("a", 1, 1), _bare("b", 1, 1)], tmp_path)
    again = sources.commons_details([_bare("a", 1, 1), _bare("b", 1, 1)], tmp_path)
    assert [r.title for r in first] == [r.title for r in again] == ["a.jpg"]
    assert first[0].licence == "CC BY 4.0" and first[0].thumb_url
    assert len(calls) == 1                                   # the second came from disk


def test_mapillary_needs_the_users_token_and_never_stores_it(tmp_path, monkeypatch):
    assert sources.mapillary_near(1, 1, 500, "", tmp_path) == []
    monkeypatch.setattr(sources, "_get_json", lambda url, timeout=30.0: {"data": [
        {"id": 42, "thumb_1024_url": "https://m/42.jpg",
         "computed_geometry": {"coordinates": [1.0001, 1.0001]},
         "compass_angle": 90, "creator": {"username": "walker"}}]})
    refs = sources.mapillary_near(1.0, 1.0, 500, "SECRET-TOKEN", tmp_path)
    assert refs[0].licence == "CC BY-SA 4.0" and refs[0].heading == 90
    for f in (tmp_path / "queries").glob("*.json"):
        assert "SECRET-TOKEN" not in f.read_text(encoding="utf-8")
        assert "SECRET-TOKEN" not in f.name


# ------------------------------------------------------------------ server

def test_the_server_validates_areas_and_thumbnail_keys(monkeypatch, tmp_path):
    from mapify.config import load_config
    from mapify.server import create_app
    cfg = load_config()
    cfg.network.offline_only = True
    cfg.models.geoclip_enabled = False
    cfg.models.clip_evidence_enabled = False
    cfg.models.ocr_enabled = False
    client = TestClient(create_app(cfg))
    buf = io.BytesIO()
    _scene(1).save(buf, "JPEG")
    files = {"image": ("x.jpg", buf.getvalue(), "image/jpeg")}
    bad = client.post("/api/pinpoint/stream", files=files,
                      data={"areas": json.dumps([{"lat": 200, "lon": 0}])})
    assert bad.status_code == 400
    ok = client.post("/api/pinpoint/stream", files=files,
                     data={"areas": json.dumps([{"lat": 12.97, "lon": 77.58}])})
    lines = [json.loads(l) for l in ok.text.splitlines() if l.strip()]
    result = next(l for l in lines if l["type"] == "result")["pinpoint"]
    assert result["verdict"] == "unavailable"          # offline: no search, said so
    assert client.get("/api/pinpoint/thumb/../../secret").status_code == 404
    assert client.get("/api/pinpoint/thumb/" + "0" * 20).status_code == 404
