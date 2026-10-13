"""Batch analysis, and the property it exists for.

Naming a place is the only part of an analysis that leaves the machine, and
Nominatim allows one request per second. The point of batch mode is that a
folder of two hundred photographs of one street costs *one* geocoding request,
not two hundred. If that stops being true the feature is worse than useless —
it is a way to hammer donated infrastructure by accident — so it is the first
thing tested here.
"""

from __future__ import annotations

import pytest

from conftest import make_jpeg, requires_geodata
from mapify.batch import CLUSTER_RADIUS_KM, BatchRunner
from mapify.config import load_config
from mapify.pipeline import AnalysisEngine
from mapify.models.base import RawCandidate


class ScriptedLocator:
    """Returns a different coordinate per call, from a fixed script."""

    name = "scripted"
    load_error = None
    loaded = True
    gallery_size = 1

    def __init__(self, coords: list[tuple[float, float, float]]):
        self.coords = coords
        self.calls = 0

    def load(self):
        return True

    def shared_clip(self):
        return None, None

    def locate_with_embedding(self, image, top_k=12):
        lat, lon, score = self.coords[min(self.calls, len(self.coords) - 1)]
        self.calls += 1
        return [RawCandidate(lat, lon, score, self.name)], None


class CountingNominatim:
    """Stands in for the real client and counts what batch mode asks of it."""

    def __init__(self):
        self.calls: list[tuple[float, float]] = []
        self.last_error = None

    def reset_budget(self):
        pass

    def reverse(self, lat, lon, zoom=14):
        self.calls.append((round(lat, 3), round(lon, 3)))

        class R:
            def as_dict(self_inner):
                return {"city": f"City@{lat:.2f}", "country": "Testland",
                        "from_cache": False}
        return R()


@pytest.fixture
def engine(tmp_path):
    cfg = load_config()
    cfg.network.offline_only = False      # we want the geocoding path exercised
    cfg.network.overpass_enabled = False
    cfg.models.geoclip_enabled = False
    cfg.models.clip_evidence_enabled = False
    cfg.models.plonk_enabled = False     # a stand-in locator, alone
    cfg.models.ocr_enabled = False
    eng = AnalysisEngine(cfg)
    eng.nominatim = CountingNominatim()
    return eng


def write_images(tmp_path, n: int) -> list[str]:
    paths = []
    for i in range(n):
        p = tmp_path / f"img{i:03d}.jpg"
        p.write_bytes(make_jpeg(colour=(60 + i, 90, 120)))
        paths.append(str(p))
    return paths


@requires_geodata
class TestGeocodingIsBoundedByPlaces:
    def test_many_images_of_one_place_cost_one_lookup(self, engine, tmp_path):
        """The property the whole feature exists for."""
        paths = write_images(tmp_path, 20)
        # Every image lands on the same street.
        engine.geoclip = ScriptedLocator([(48.8584, 2.2945, 0.3)] * 20)

        runner = BatchRunner(engine)
        summary = runner.run(paths)

        assert summary["analysed"] == 20
        assert len(summary["clusters"]) == 1
        assert len(engine.nominatim.calls) == 1, (
            f"20 images of one place should cost one geocoding request, "
            f"not {len(engine.nominatim.calls)}"
        )

    def test_three_places_cost_three_lookups(self, engine, tmp_path):
        paths = write_images(tmp_path, 9)
        engine.geoclip = ScriptedLocator([
            (48.8584, 2.2945, 0.3), (48.8590, 2.2950, 0.3), (48.8580, 2.2940, 0.3),
            (35.6762, 139.6503, 0.3), (35.6770, 139.6510, 0.3), (35.6750, 139.6490, 0.3),
            (-33.8688, 151.2093, 0.3), (-33.8690, 151.2100, 0.3), (-33.8680, 151.2080, 0.3),
        ])
        summary = BatchRunner(engine).run(paths)

        assert len(summary["clusters"]) == 3
        assert len(engine.nominatim.calls) == 3
        assert {c["size"] for c in summary["clusters"]} == {3}

    def test_unplaceable_images_are_not_geocoded(self, engine, tmp_path):
        """An image the tool refused to place is not worth a request."""
        paths = write_images(tmp_path, 4)
        # Mass far below the floor: every one comes back insufficient.
        engine.geoclip = ScriptedLocator([(10.0, 10.0, 0.001)] * 4)
        summary = BatchRunner(engine).run(paths)

        assert all(i["confidence"] == "insufficient" for i in summary["items"])
        assert summary["clusters"] == []
        assert engine.nominatim.calls == []


@requires_geodata
class TestClustering:
    def test_results_within_the_radius_group(self, engine, tmp_path):
        paths = write_images(tmp_path, 2)
        engine.geoclip = ScriptedLocator([
            (48.8584, 2.2945, 0.3),
            (48.9000, 2.3500, 0.3),      # ~6 km away
        ])
        summary = BatchRunner(engine).run(paths)
        assert len(summary["clusters"]) == 1
        assert summary["clusters"][0]["size"] == 2

    def test_results_beyond_the_radius_do_not(self, engine, tmp_path):
        paths = write_images(tmp_path, 2)
        engine.geoclip = ScriptedLocator([
            (48.8584, 2.2945, 0.3),
            (50.0000, 2.2945, 0.3),      # ~127 km away
        ])
        summary = BatchRunner(engine).run(paths)
        assert CLUSTER_RADIUS_KM < 127
        assert len(summary["clusters"]) == 2

    def test_clusters_are_ordered_by_size(self, engine, tmp_path):
        paths = write_images(tmp_path, 4)
        engine.geoclip = ScriptedLocator([
            (35.6762, 139.6503, 0.3),
            (48.8584, 2.2945, 0.3), (48.8590, 2.2950, 0.3), (48.8580, 2.2940, 0.3),
        ])
        summary = BatchRunner(engine).run(paths)
        sizes = [c["size"] for c in summary["clusters"]]
        assert sizes == sorted(sizes, reverse=True)
        assert sizes == [3, 1]

    def test_every_placed_image_is_assigned_a_cluster(self, engine, tmp_path):
        paths = write_images(tmp_path, 5)
        engine.geoclip = ScriptedLocator([(48.8584, 2.2945, 0.3)] * 5)
        summary = BatchRunner(engine).run(paths)
        for item in summary["items"]:
            if item["latitude"] is not None:
                assert item["cluster"] is not None
                assert item["place"]


@requires_geodata
class TestCancellation:
    def test_cancelling_keeps_what_finished(self, engine, tmp_path):
        """A cancelled batch is a shorter batch, not a lost one."""
        paths = write_images(tmp_path, 10)
        engine.geoclip = ScriptedLocator([(48.8584, 2.2945, 0.3)] * 10)
        runner = BatchRunner(engine)

        seen = []

        def progress(event):
            seen.append(event)
            # Stop once three images are through.
            if event.get("type") == "item" and event.get("done") == 3:
                runner.cancel()

        summary = runner.run(paths, progress)

        assert summary["cancelled"] is True
        assert summary["analysed"] == 3
        assert summary["skipped"] == 7
        # The three that completed are still usable, and still named.
        done = [i for i in summary["items"] if i["state"] == "done"]
        assert len(done) == 3
        assert all(i["place"] for i in done)

    def test_cancelling_before_anything_runs_is_clean(self, engine, tmp_path):
        paths = write_images(tmp_path, 3)
        engine.geoclip = ScriptedLocator([(48.8584, 2.2945, 0.3)] * 3)
        runner = BatchRunner(engine)
        runner.cancel()
        summary = runner.run(paths)
        assert summary["analysed"] == 0
        assert summary["skipped"] == 3
        assert summary["clusters"] == []


@requires_geodata
class TestFailuresDoNotStopTheRun:
    def test_a_broken_file_is_recorded_and_skipped(self, engine, tmp_path):
        good = write_images(tmp_path, 2)
        bad = tmp_path / "broken.jpg"
        bad.write_bytes(b"this is not an image")
        engine.geoclip = ScriptedLocator([(48.8584, 2.2945, 0.3)] * 3)

        summary = BatchRunner(engine).run([good[0], str(bad), good[1]])

        assert summary["analysed"] == 2
        assert summary["failed"] == 1
        failed = [i for i in summary["items"] if i["state"] == "failed"]
        assert failed and failed[0]["error"]

    def test_a_missing_file_is_recorded_and_skipped(self, engine, tmp_path):
        paths = write_images(tmp_path, 1)
        engine.geoclip = ScriptedLocator([(48.8584, 2.2945, 0.3)])
        summary = BatchRunner(engine).run(
            [paths[0], str(tmp_path / "absent.jpg")]
        )
        assert summary["analysed"] == 1
        assert summary["failed"] == 1


@requires_geodata
class TestLimits:
    def test_the_batch_size_is_capped(self, engine, tmp_path):
        paths = write_images(tmp_path, 12)
        engine.geoclip = ScriptedLocator([(48.8584, 2.2945, 0.3)] * 12)
        summary = BatchRunner(engine, max_items=5).run(paths)
        assert summary["total"] == 5

    def test_offline_mode_makes_no_requests_at_all(self, tmp_path):
        cfg = load_config()
        cfg.network.offline_only = True
        cfg.models.geoclip_enabled = False
        cfg.models.clip_evidence_enabled = False
        cfg.models.plonk_enabled = False     # a stand-in locator, alone
        cfg.models.ocr_enabled = False
        eng = AnalysisEngine(cfg)
        eng.nominatim = CountingNominatim()
        eng.geoclip = ScriptedLocator([(48.8584, 2.2945, 0.3)] * 3)

        paths = write_images(tmp_path, 3)
        summary = BatchRunner(eng).run(paths)

        assert eng.nominatim.calls == []
        # Offline still names the place, from Natural Earth.
        assert summary["clusters"] and summary["clusters"][0]["place"]
        assert "France" in summary["clusters"][0]["place"]
