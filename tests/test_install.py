"""Installing the application, and the first launch after it.

Two things that only go wrong on somebody else's machine are tested here,
because by definition they cannot be noticed on the machine they were written
on:

  * an installed application must not write to its own directory. It may be
    under ``Program Files``, where it cannot, and the failure mode is silent
    -- a gallery cache that never saves and re-encodes for a minute on every
    single launch.
  * the first launch spends about 1.6 GB of somebody's connection. The
    application has to be able to say so *before* spending it, which means
    answering the question without loading the model stack that the answer is
    about.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

from mapify import firstrun, paths


@pytest.fixture
def relocated(tmp_path, monkeypatch):
    """Run as though the state directory were somewhere else entirely."""
    target = tmp_path / "state"
    monkeypatch.setenv("MAPIFY_STATE_DIR", str(target))
    importlib.reload(paths)
    importlib.reload(firstrun)
    yield target
    monkeypatch.delenv("MAPIFY_STATE_DIR", raising=False)
    importlib.reload(paths)
    importlib.reload(firstrun)


class TestWritablePaths:
    def test_state_dir_is_created_on_demand(self, relocated):
        assert paths.state_dir() == relocated
        assert relocated.is_dir()

    def test_cache_lives_under_state_not_under_the_install(self, relocated):
        cache = paths.cache_dir()
        assert cache.is_dir()
        assert relocated in cache.parents or cache.parent == relocated

    def test_the_cache_is_actually_writable(self, relocated):
        """The point of the whole module. If this fails, so does every launch."""
        probe = paths.cache_dir() / "probe.bin"
        probe.write_bytes(b"x" * 16)
        assert probe.read_bytes() == b"x" * 16

    def test_an_override_wins_over_the_default(self, relocated, tmp_path):
        assert paths.state_dir() != Path.home()
        assert str(relocated) in str(paths.cache_dir())

    def test_read_root_holds_the_bundled_data(self):
        """Read and write roots answer different questions and may differ."""
        assert (paths.read_root() / "mapify").is_dir()

    def test_describe_names_every_location(self):
        described = paths.describe()
        for key in ("frozen", "read_root", "state_dir", "cache_dir",
                    "config_path"):
            assert key in described and described[key]

    def test_frozen_is_false_in_a_checkout(self):
        assert paths.is_frozen() is False

    def test_no_module_resolves_a_writable_path_from___file__(self):
        """A regression guard on the bug this module exists to fix.

        Walking ``__file__`` upwards to find somewhere to write is correct in
        a checkout and wrong in an installed application, and it is an easy
        line to reintroduce.
        """
        import mapify.pipeline as pipeline
        import mapify.models.geoclip_locator as locator

        for module in (pipeline, locator):
            source = Path(module.__file__).read_text(encoding="utf-8")
            assert "data\" / \"cache" not in source, (
                f"{module.__name__} builds a cache path by hand"
            )


class TestFirstRunHonesty:
    def test_it_answers_without_importing_the_model_stack(self):
        """The screen appears while the window is opening, or it is useless.

        Importing torch takes tens of seconds. If answering "how big is the
        download" needed it, the honest warning would arrive after the point
        it was needed.
        """
        for heavy in ("torch", "transformers", "geoclip"):
            sys.modules.pop(heavy, None)
        reloaded = importlib.reload(firstrun)
        reloaded.status()
        assert "torch" not in sys.modules, "first-run check imported torch"

    def test_it_states_a_size_before_anything_is_downloaded(self, relocated):
        for component in firstrun.COMPONENTS:
            assert component.expected_bytes > 0
            assert component.origin
            assert component.licence
            assert component.why, "a component with no stated purpose"

    def test_the_stated_size_is_close_to_the_real_one(self):
        """An estimate that is wrong by a factor is worse than no estimate.

        CLIP ViT-L/14 is 1,631 MiB of weights plus a few MB of tokeniser
        files. Anything outside a 10% band means the constant has drifted
        from the model it describes.
        """
        clip = next(c for c in firstrun.COMPONENTS if c.key == "clip")
        assert 1.55e9 < clip.expected_bytes < 1.90e9

    def test_a_missing_cache_reads_as_a_required_download(self, monkeypatch,
                                                          tmp_path):
        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty"))
        reloaded = importlib.reload(firstrun)
        status = reloaded.status()
        assert status["download_required"] is True
        assert status["ready"] is False
        assert status["total_bytes"] > 1e9

    def test_a_truncated_download_does_not_read_as_present(self, monkeypatch,
                                                           tmp_path):
        """Half a weights file is not a model, and must not look like one."""
        cache = tmp_path / "hub"
        snap = (cache / "models--openai--clip-vit-large-patch14"
                / "snapshots" / "abc123")
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}", encoding="utf-8")
        (snap / "model.safetensors").write_bytes(b"0" * 4096)
        monkeypatch.setenv("HF_HUB_CACHE", str(cache))
        reloaded = importlib.reload(firstrun)
        assert reloaded.status()["download_required"] is True

    def test_progress_is_bounded_even_if_the_repo_is_bigger(self, monkeypatch,
                                                            tmp_path):
        """A fraction above 1.0 would drive the bar off the end of the card."""
        cache = tmp_path / "hub"
        blobs = cache / "models--openai--clip-vit-large-patch14" / "blobs"
        blobs.mkdir(parents=True)
        (blobs / "big").write_bytes(b"0" * 1_000_000)
        monkeypatch.setenv("HF_HUB_CACHE", str(cache))
        reloaded = importlib.reload(firstrun)
        seen = reloaded.watch()
        assert 0.0 <= seen["fraction"] <= 1.0

    def test_deferral_survives_a_restart(self, relocated):
        reloaded = importlib.reload(firstrun)
        assert reloaded.is_deferred() is False
        reloaded.set_deferred(True)
        assert reloaded.is_deferred() is True
        # A second process reading the same state directory must agree.
        again = importlib.reload(firstrun)
        assert again.is_deferred() is True
        again.set_deferred(False)
        assert again.is_deferred() is False

    def test_the_rate_estimate_does_not_swing_wildly(self):
        """Smoothed, because "2 minutes left" flipping to "18" helps nobody."""
        rate = firstrun.Rate(halflife_s=6.0)
        rate.update(0, 0.0)
        for i in range(1, 21):
            # 10 MB/s in bursts: alternate a big chunk and nothing at all.
            got = rate.update(int(i / 2) * 20_000_000, float(i) * 1.0)
        assert 5e6 < got < 15e6, f"smoothed rate wandered to {got / 1e6:.1f} MB/s"

    def test_the_gallery_is_not_counted_as_a_download(self):
        """It is computed locally. Calling it a download would be a lie."""
        status = firstrun.status()
        assert "gallery_encode_seconds" in status
        assert all(c.key != "gallery" for c in firstrun.COMPONENTS)

    def test_the_encode_estimate_matches_what_was_measured(self):
        """A regression guard on a mistake that was actually made.

        The first draft said 57 seconds, lifted from a per-call profile line
        rather than from the warmup measurement beside it. Two real
        measurements of this encode exist -- 272s in Phase 7 and 457s on a
        memory-starved machine -- so an estimate under four minutes is not
        one of them, and understating a wait by a factor of five is the
        opposite of what this screen is for.
        """
        estimate = firstrun.status()["gallery_encode_seconds"]
        assert 240 <= estimate <= 480, (
            f"{estimate}s is outside both measurements of this encode"
        )

    def test_geoclip_weights_are_not_listed_as_a_download(self):
        """They are bundled. Listing them would inflate the stated size."""
        assert all("geoclip" not in c.repo.lower() for c in firstrun.COMPONENTS)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows renderer check")
class TestWebView2Preflight:
    def test_it_reports_a_version_or_none_without_raising(self):
        from mapify.desktop import webview2_runtime

        found = webview2_runtime()
        assert found is None or isinstance(found, str)

    def test_the_absent_message_tells_a_person_what_to_do(self):
        from mapify.desktop import WEBVIEW2_MISSING

        assert "WebView2" in WEBVIEW2_MISSING
        assert "developer.microsoft.com" in WEBVIEW2_MISSING
        # A COM HRESULT is not an instruction.
        assert "0x80" not in WEBVIEW2_MISSING

    def test_a_broken_registry_value_is_not_treated_as_installed(self,
                                                                 monkeypatch):
        """EdgeUpdate leaves pv=0.0.0.0 behind after an uninstall."""
        import winreg

        from mapify import desktop

        class FakeKey:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
        monkeypatch.setattr(winreg, "QueryValueEx",
                            lambda *a, **k: ("0.0.0.0", 1))
        assert desktop.webview2_runtime() is None


class TestInstallerScripts:
    """The installer is shell script, but its promises are testable."""

    @staticmethod
    def _installer(name: str) -> str:
        path = paths.read_root() / "installer" / name
        assert path.is_file(), f"{name} is missing"
        return path.read_text(encoding="utf-8")

    def test_it_installs_per_user_and_needs_no_administrator(self):
        """Reading HKLM is fine -- that is how WebView2 is detected.

        Writing to it is not: that needs administrator, and it would put
        this user's choice into every other account on the machine.
        """
        script = self._installer("install.ps1")
        assert "LOCALAPPDATA" in script
        assert "HKCU:" in script

        writers = ("New-Item", "New-ItemProperty", "Set-ItemProperty",
                   "Remove-Item", "Remove-ItemProperty", "reg add", "reg.exe")
        for number, line in enumerate(script.splitlines(), 1):
            if "HKLM" not in line:
                continue
            assert not any(w in line for w in writers), (
                f"install.ps1:{number} writes to HKLM: {line.strip()}"
            )

    def test_it_installs_inside_the_user_profile(self):
        script = self._installer("install.ps1")
        assert "Programs\\Mapify" in script
        assert "ProgramFiles" not in script, (
            "installing to Program Files would need administrator"
        )

    def test_every_registry_key_it_writes_is_removed_again(self):
        """An uninstaller that leaves keys behind is not an uninstaller."""
        install = self._installer("install.ps1")
        uninstall = self._installer("uninstall.ps1")
        assert "Uninstall\\Mapify" in install
        assert "Uninstall\\Mapify" in uninstall
        assert "Remove-Item" in uninstall

    def test_uninstalling_offers_to_keep_the_downloaded_weights(self):
        """1.6 GB should not be silently discarded, nor silently kept."""
        uninstall = self._installer("uninstall.ps1")
        assert "KeepData" in uninstall or "keep" in uninstall.lower()

    def test_it_does_not_claim_to_be_signed(self):
        install = self._installer("install.ps1")
        assert "SmartScreen" in install, (
            "an unsigned installer has to say so; the warning is correct"
        )
