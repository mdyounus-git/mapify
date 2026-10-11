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


def test_only_a_name_that_clearly_fits_is_chosen():
    from mapify.pinpoint.engine import choose_names
    names = ["Tour Eiffel", "Cafe Kleber", "Pharmacie", "Musee du Quai Branly", "Hotel"]
    assert choose_names(names, [0.31, 0.18, 0.17, 0.20, 0.18]) == ["Tour Eiffel"]
    assert choose_names(names, [0.19, 0.18, 0.18, 0.19, 0.18]) == []   # nothing stands out
    assert choose_names(["Tower"], [0.25]) == ["Tower"]                # too few: absolute
    assert choose_names(["Kiosk"], [0.15]) == []


def test_landmarks_are_weighed_by_their_english_name_and_searched_by_both():
    from mapify.pinpoint.engine import landmark_labels
    labels = landmark_labels([
        ("Собор Василия Блаженного", 55.75, 37.62, "Saint Basil's Cathedral"),
        ("Tower Bridge", 51.5, -0.07, None),
        ("Tower Bridge", 51.5, -0.07),                       # a second POI, same name
        ("Café", 51.5, -0.07, "Café")])                      # English the same
    assert labels == {"Saint Basil's Cathedral":
                      ["Saint Basil's Cathedral", "Собор Василия Блаженного"],
                      "Tower Bridge": ["Tower Bridge"], "Café": ["Café"]}


def test_a_name_search_in_two_languages_interleaves_by_relevance(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "commons_listing", lambda lat, lon, radius, cache: [])
    asked = []

    def search(text, lat, lon, radius, cache):
        asked.append(text)
        tag = "en" if text.startswith("Saint") else "ru"
        return [_bare(f"{tag}-{i}", 55.7525, 37.6231) for i in range(3)]

    monkeypatch.setattr(engine_mod, "commons_search_near", search)
    marks = [("Собор Василия Блаженного", 55.7525, 37.6231, "Saint Basil's Cathedral"),
             ("Аптека", 55.75, 37.62), ("Банк", 55.75, 37.621), ("Кафе", 55.751, 37.62),
             ("Магазин", 55.749, 37.62)]
    weighed = []

    def namer(names):
        weighed.extend(names)
        return [0.30 if n.startswith("Saint") else 0.18 for n in names]

    eng = PinpointEngine(tmp_path, landmarks=lambda lat, lon, r: marks)
    named = eng._named(Area(55.7525, 37.6231, 1000), namer, lambda k, m: None, [])
    assert "Saint Basil's Cathedral" in weighed and "Собор Василия Блаженного" not in weighed
    assert asked == ["Saint Basil's Cathedral", "Собор Василия Блаженного"]
    (name, refs), = named
    assert [r.title for r in refs[:4]] == ["en-0.jpg", "ru-0.jpg", "en-1.jpg", "ru-1.jpg"]


def _named_engine(tmp_path, monkeypatch, scores):
    crowd = [_bare(f"c-{i}", 12.97 + (i % 20) * 2e-5, 77.58 + (i // 20) * 2e-5)
             for i in range(200)]
    tower = [_bare(f"tower-{i}", 12.97 + 600 / 111320.0, 77.58) for i in range(3)]
    searched = []
    monkeypatch.setattr(engine_mod, "commons_listing",
                        lambda lat, lon, radius, cache: list(crowd))

    def search(text, lat, lon, radius, cache):
        searched.append(text)
        return list(tower)

    monkeypatch.setattr(engine_mod, "commons_search_near", search)
    marks = [("Tower", 12.975, 77.58), ("Kiosk", 12.97, 77.58), ("Bakery", 12.97, 77.581),
             ("Shop", 12.971, 77.58), ("Bank", 12.97, 77.579)]
    eng = PinpointEngine(tmp_path, landmarks=lambda lat, lon, r: marks)
    order, notes, cover = eng._plan([Area(12.97, 77.58, 1000)], lambda k, m: None, set(),
                                    namer=lambda names: [scores.get(n, 0.18) for n in names])
    return order, notes, cover, searched


def test_photographs_of_the_landmark_that_fits_go_first(tmp_path, monkeypatch):
    # Listed by distance, the tower's photographs (600 m out) would come
    # after the 200 nearer ones; found by its name, they lead.
    order, notes, cover, searched = _named_engine(tmp_path, monkeypatch, {"Tower": 0.31})
    assert searched == ["Tower"]
    assert all(r.id.startswith("File:tower-") for r in order[:3])
    assert cover[0]["named"][0] == {"name": "Tower", "found": 3, "not_listed_otherwise": 3}
    assert any("photographs of Tower" in n for n in notes)


def test_no_clear_fit_searches_no_name(tmp_path, monkeypatch):
    order, notes, cover, searched = _named_engine(tmp_path, monkeypatch, {})
    assert searched == [] and cover[0]["named"] == []
    assert not any(r.id.startswith("File:tower-") for r in order)


def test_repeat_shots_from_one_spot_are_pushed_back(tmp_path, monkeypatch):
    # Five shots from one spot 100 m away: after the first, each is pushed
    # back 120 / 5 = 24 m, so a lone photograph at 115 m comes second.
    spot = [_bare(f"spot-{i}", 12.97 + 100 / 111320.0, 77.58) for i in range(5)]
    lone = _bare("lone", 12.97 - 115 / 111320.0, 77.58)
    monkeypatch.setattr(engine_mod, "commons_listing",
                        lambda lat, lon, radius, cache: spot + [lone])
    order, _, _ = PinpointEngine(tmp_path)._plan(
        [Area(12.97, 77.58, 1000)], lambda k, m: None, set())
    assert order[0].id.startswith("File:spot-")
    assert order[1].id == "File:lone.jpg"                    # before the 2nd shot


def test_within_a_spot_the_nearest_photograph_comes_first(tmp_path, monkeypatch):
    # With the centre right, the photograph taken nearest it is the likeliest
    # match, crowded spot or not.
    crowd = [_bare(f"crowd-{i}", 12.97 + (20 + i % 30) / 111320.0, 77.58)
             for i in range(300)]
    exact = _bare("exact", 12.97 + 2 / 111320.0, 77.58)
    monkeypatch.setattr(engine_mod, "commons_listing",
                        lambda lat, lon, radius, cache: crowd + [exact])
    order, _, _ = PinpointEngine(tmp_path)._plan(
        [Area(12.97, 77.58, 800)], lambda k, m: None, set())
    assert order[0].id == "File:exact.jpg"


def test_a_popular_viewpoint_gets_more_comparisons_than_a_quiet_one(tmp_path, monkeypatch):
    busy = [_bare(f"busy-{i}", 12.97 + 300 / 111320.0, 77.58) for i in range(100)]
    quiet = [_bare(f"quiet-{i}", 12.97 - 300 / 111320.0, 77.58) for i in range(4)]
    monkeypatch.setattr(engine_mod, "commons_listing",
                        lambda lat, lon, radius, cache: busy + quiet)
    order, _, _ = PinpointEngine(tmp_path)._plan(
        [Area(12.97, 77.58, 1000)], lambda k, m: None, set())
    # Equally far: the spot a hundred people chose takes most of the early
    # comparisons (its 100 spread over 120 m of priority), and the quiet
    # spot still gets its first photograph in.
    first = [r.id for r in order[:12]]
    assert sum("busy" in i for i in first) >= 10 and sum("quiet" in i for i in first) >= 1


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


def test_a_failed_details_fetch_is_unavailable_not_none(tmp_path, monkeypatch):
    import urllib.error
    monkeypatch.setattr(engine_mod, "commons_listing",
                        lambda lat, lon, radius, cache: [_bare("x", 12.97, 77.58)])

    def down(refs, cache):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(engine_mod, "commons_details", down)
    res = PinpointEngine(tmp_path).run(_scene(1), [Area(12.97, 77.58, 800)])
    assert res["verdict"] == "unavailable"
    assert "could not be fetched" in res["message"]
    assert res["searched"]["references"] == 0


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
    return lambda url, timeout=30.0, data=None: listing if "geosearch" in url else details


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


def test_a_name_search_keeps_relevance_order_and_the_circle(tmp_path, monkeypatch):
    urls = []
    answer = {"query": {"pages": {
        "1": {"title": "File:second.jpg", "index": 2,
              "coordinates": [{"lat": 12.9701, "lon": 77.5801}]},
        "2": {"title": "File:first.jpg", "index": 1,
              "coordinates": [{"lat": 12.9702, "lon": 77.5802}]},
        "3": {"title": "File:no-camera.jpg", "index": 3},
        "4": {"title": "File:far.jpg", "index": 4,
              "coordinates": [{"lat": 13.1, "lon": 77.58}]}}}}
    monkeypatch.setattr(sources, "_get_json",
                        lambda url, timeout=30.0, data=None: urls.append(url) or answer)
    refs = sources.commons_search_near('Tour "Eiffel"', 12.97, 77.58, 1000, tmp_path)
    assert [r.title for r in refs] == ["first.jpg", "second.jpg"]
    assert "nearcoord%3A1000m%2C12.97000%2C77.58000" in urls[0]
    assert "%22" not in urls[0]                         # quotes stripped
    assert sources.commons_search_near("   ", 1, 1, 500, tmp_path) == []


def test_details_are_asked_for_once_and_unfree_files_dropped(tmp_path, monkeypatch):
    calls = []
    answer = _commons_answer([
        {"title": "File:a.jpg", "lat": 1, "lon": 1, "licence": "CC BY 4.0"},
        {"title": "File:b.jpg", "lat": 1, "lon": 1, "licence": "All rights reserved"}])
    sent = []
    monkeypatch.setattr(sources, "_get_json",
                        lambda url, timeout=30.0, data=None:
                        calls.append(url) or sent.append(data) or answer(url))
    first = sources.commons_details([_bare("a", 1, 1), _bare("b", 1, 1)], tmp_path)
    again = sources.commons_details([_bare("a", 1, 1), _bare("b", 1, 1)], tmp_path)
    assert [r.title for r in first] == [r.title for r in again] == ["a.jpg"]
    assert first[0].licence == "CC BY 4.0" and first[0].thumb_url
    assert len(calls) == 1                                   # the second came from disk
    # Titles go in a POST body: fifty Cyrillic ones overflow a URL.
    assert "File:a.jpg" in sent[0]["titles"] and "titles" not in calls[0]


def test_a_dropped_connection_is_tried_again(monkeypatch):
    import urllib.error
    tries = []

    class Answer:
        def __enter__(self):
            return io.BytesIO(b'{"ok": 1}')

        def __exit__(self, *a):
            return False

    def flaky(req, timeout=30.0):
        tries.append(req)
        if len(tries) == 1:
            raise urllib.error.URLError("connection reset")
        return Answer()

    monkeypatch.setattr(sources.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    assert sources._get_json("https://example.org/api") == {"ok": 1}
    assert len(tries) == 2


def test_mapillary_needs_the_users_token_and_never_stores_it(tmp_path, monkeypatch):
    assert sources.mapillary_near(1, 1, 500, "", tmp_path) == []
    monkeypatch.setattr(sources, "_get_json", lambda url, timeout=30.0, data=None: {"data": [
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


def _pic(pid, lat, lon, seq="s1", licence="CC-BY-SA-4.0", azimuth=90):
    return {"id": pid, "collection": seq,
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"license": licence, "view:azimuth": azimuth,
                           "datetime": "2026-05-01T10:00:00Z"},
            "providers": [{"name": "someone", "roles": ["producer"]}],
            "assets": {"sd": {"href": f"https://example.org/{pid}/sd.jpg"}},
            "links": [{"rel": "self", "href": f"https://api.example/{pid}"}]}


def test_panoramax_keeps_open_licences_and_one_picture_per_patch(tmp_path, monkeypatch):
    """A sequence a few metres apart must not fill the comparison budget."""
    asked = []
    pics = [_pic(f"walk-{i}", 48.8584 + i * 1e-5, 2.2945) for i in range(20)]  # ~1 m apart
    pics += [_pic("other-seq", 48.8590, 2.2950, seq="s2"),
             _pic("closed", 48.8585, 2.2946, seq="s3", licence="proprietary"),
             _pic("far", 48.8700, 2.2945, seq="s4")]

    def fake_get_json(url, timeout=30.0, data=None):
        asked.append(url)
        return {"features": pics}

    monkeypatch.setattr(sources, "_get_json", fake_get_json)
    refs = sources.panoramax_near(48.8584, 2.2945, 800, tmp_path)
    ids = [r.id for r in refs]
    assert "closed" not in ids                     # not an open licence
    assert "far" not in ids                        # outside the radius
    assert "other-seq" in ids
    # 20 pictures over 22 m of street: one per 15 m cell, so at most three.
    assert 1 <= sum(i.startswith("walk-") for i in ids) <= 3
    assert refs == sorted(refs, key=lambda r: r.distance_m)
    first = refs[0]
    assert first.source == "panoramax" and first.licence == "CC BY-SA 4.0"
    assert first.heading == 90 and first.author == "someone"
    assert first.page_url.startswith("https://api.example/")
    assert all("sortby=-datetime" in u for u in asked)
    assert len(asked) == len(sources._PANORAMAX_SHELLS)
    sources.panoramax_near(48.8584, 2.2945, 800, tmp_path)
    assert len(asked) == len(sources._PANORAMAX_SHELLS)   # second time: the cache


def test_the_engine_asks_panoramax_only_when_switched_on(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "commons_listing", lambda lat, lon, radius, cache: [])
    asked = []

    def near(lat, lon, radius, cache, limit=150):
        asked.append(limit)
        r = Reference(source="panoramax", id="pic-1", lat=lat, lon=lon,
                      thumb_url="", page_url="", licence="CC BY-SA 4.0", author="a")
        return [r]

    monkeypatch.setattr(engine_mod, "panoramax_near", near)
    order, _, _ = PinpointEngine(tmp_path)._plan(
        [Area(48.8584, 2.2945, 800)], lambda k, m: None, set())
    assert asked == [] and not order
    order, _, _ = PinpointEngine(tmp_path, panoramax=True)._plan(
        [Area(48.8584, 2.2945, 800)], lambda k, m: None, set())
    assert asked == [engine_mod.PANORAMAX_PER_AREA]
    assert [r.id for r in order] == ["pic-1"]


def test_wikidata_landmarks_carry_their_commons_category(tmp_path, monkeypatch):
    asked = []

    def fake_get_json(url, timeout=30.0, data=None):
        asked.append(url)
        row = lambda q, label, en, cat, lon, lat, links: {     # noqa: E731
            "item": {"value": f"http://www.wikidata.org/entity/{q}"},
            "itemLabel": {"value": label}, "cat": {"value": cat},
            "coord": {"value": f"Point({lon} {lat})"}, "links": {"value": str(links)},
            **({"en": {"value": en}} if en else {})}
        return {"results": {"bindings": [
            row("Q1", "Храм Василия Блаженного", "Saint Basil's Cathedral",
                "Saint Basil's Cathedral", 37.6231, 55.7525, 120),
            row("Q2", "Q2", None, "Something", 37.62, 55.75, 3),          # no label
            {"itemLabel": {"value": "No coordinate"}, "cat": {"value": "X"}},
        ]}}

    monkeypatch.setattr(sources, "_get_json", fake_get_json)
    marks = sources.wikidata_landmarks(55.7539, 37.6208, 800, tmp_path)
    assert marks == [("Храм Василия Блаженного", 55.7525, 37.6231,
                      "Saint Basil's Cathedral", "Saint Basil's Cathedral")]
    assert "query.wikidata.org" in asked[0] and "P373" in asked[0]
    sources.wikidata_landmarks(55.7539, 37.6208, 800, tmp_path)
    assert len(asked) == 1                      # cached


def test_a_commons_category_is_searched_as_a_category(tmp_path, monkeypatch):
    asked = []

    def fake_get_json(url, timeout=30.0, data=None):
        asked.append(url)
        return {"query": {"pages": {"1": {"title": "File:Basil.jpg", "index": 1,
                                          "coordinates": [{"lat": 55.7526, "lon": 37.6230}]}}}}

    monkeypatch.setattr(sources, "_get_json", fake_get_json)
    refs = sources.commons_category_near('Saint "Basil\'s" Cathedral', 55.7539, 37.6208,
                                         800, tmp_path)
    assert [r.id for r in refs] == ["File:Basil.jpg"]
    import urllib.parse
    query = urllib.parse.parse_qs(urllib.parse.urlparse(asked[0]).query)["gsrsearch"][0]
    assert query.startswith('incategory:"Saint  Basil\'s  Cathedral"'.replace("  ", " "))
    assert "nearcoord:800m," in query


def test_named_search_looks_in_the_landmarks_category_first(tmp_path, monkeypatch):
    marks = [("Храм Василия Блаженного", 55.7525, 37.6231, "Saint Basil's Cathedral",
              "Saint Basil's Cathedral"),
             ("ГУМ", 55.7547, 37.6215, "GUM")]
    searched = []

    def by_text(term, lat, lon, radius, cache):
        searched.append(("text", term))
        return [_bare(f"text-{term}", lat, lon)]

    def by_category(cat, lat, lon, radius, cache):
        searched.append(("category", cat))
        return [_bare(f"cat-{cat}", lat, lon)]

    monkeypatch.setattr(engine_mod, "commons_search_near", by_text)
    monkeypatch.setattr(engine_mod, "commons_category_near", by_category)
    eng = PinpointEngine(tmp_path, landmarks=lambda lat, lon, r: marks)
    named = eng._named(Area(55.7539, 37.6208, 800),
                       lambda names: [5.0 if "Basil" in n else 0.0 for n in names],
                       lambda k, m: None, [])
    assert ("category", "Saint Basil's Cathedral") in searched
    assert not any(kind == "category" and "GUM" in what for kind, what in searched)
    name, refs = named[0]
    assert name == "Saint Basil's Cathedral"
    # Interleaved with the name searches: their first results and the
    # category's both come first.
    assert "File:cat-Saint Basil's Cathedral.jpg" in {r.id for r in refs[:3]}


def test_panoramax_pictures_queue_behind_the_photographs_of_the_place(tmp_path, monkeypatch):
    """Ranked by distance alone, street pictures near a wrong centre pushed
    the Sydney Opera House's matching photograph out of the budget."""
    monkeypatch.setattr(engine_mod, "commons_listing",
                        lambda lat, lon, radius, cache: [_bare("the-landmark", lat + 0.008, lon)])

    def near(lat, lon, radius, cache, limit=150):
        return [Reference(source="panoramax", id=f"street-{i}", lat=lat + i * 1e-5, lon=lon,
                          thumb_url="", page_url="", licence="CC BY-SA 4.0", author="a")
                for i in range(5)]

    monkeypatch.setattr(engine_mod, "panoramax_near", near)
    order, _, _ = PinpointEngine(tmp_path, panoramax=True)._plan(
        [Area(-33.8568, 151.2153, 1500)], lambda k, m: None, set())
    ids = [r.id for r in order]
    assert ids[0] == "File:the-landmark.jpg", ids        # 900 m away, still first
    assert set(ids[1:]) == {f"street-{i}" for i in range(5)}
