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

    def test_models_stay_with_the_project_not_on_the_system_drive(self):
        """The 1.6 GB download is the biggest thing Mapify is responsible
        for, and it belongs wherever the person put the application."""
        models = paths.models_dir()
        assert paths.read_root() in models.parents or models.parent == paths.read_root()             or paths.state_dir().parent in models.parents             or models.parent == paths.state_dir().parent, models

    def test_an_existing_copy_beats_a_tidy_one(self, monkeypatch, tmp_path):
        """A checkout and the install built beside it share one download.

        Keeping two directories symmetrical at the cost of fetching 1.6 GB
        twice is 1.6 GB of nothing.
        """
        monkeypatch.delenv("MAPIFY_MODELS_DIR", raising=False)
        planted = tmp_path / "app" / "models"
        (planted / "hub" / paths._CLIP_CACHE_DIR / "snapshots").mkdir(parents=True)
        monkeypatch.setattr(paths, "read_root", lambda: tmp_path)
        monkeypatch.setenv("MAPIFY_STATE_DIR", str(tmp_path / "data"))
        assert paths.models_dir() == planted

    def test_the_cache_probe_names_the_model_rather_than_counting_files(self):
        """The Hugging Face cache is shared.

        Another tool's weights sitting in it say nothing about whether
        ours are there, and answering "yes" on the strength of somebody
        else's models is how you fetch 1.6 GB you already have.
        """
        from mapify import firstrun

        clip = next(c for c in firstrun.COMPONENTS if c.key == "clip")
        expected = "models--" + clip.repo.replace("/", "--")
        assert paths._CLIP_CACHE_DIR == expected, (
            "paths.py and firstrun.COMPONENTS disagree about the model"
        )

    def test_a_populated_shared_cache_is_left_alone(self, monkeypatch):
        """Relocating someone's existing download would be rude."""
        monkeypatch.delenv("HF_HOME", raising=False)
        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        monkeypatch.setattr(paths, "_default_hf_cache_has_weights", lambda: True)
        assert paths.is_frozen() is False
        assert paths.apply_model_cache_env() is None

    def test_an_empty_shared_cache_falls_back_to_the_project(self, monkeypatch):
        """Nothing to preserve, so use the copy the project already has."""
        monkeypatch.delenv("HF_HOME", raising=False)
        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        monkeypatch.setattr(paths, "_default_hf_cache_has_weights", lambda: False)
        assert paths.apply_model_cache_env() is not None

    def test_an_explicit_hf_home_is_never_overridden(self, monkeypatch):
        """That is someone stating where their cache lives."""
        monkeypatch.setenv("HF_HOME", r"D:\somewhere\else")
        assert paths.apply_model_cache_env() is None

    def test_easyocr_is_pointed_at_the_directory_holding_the_weights(self):
        """One level out is a silent 565 MB re-download, not an error.

        EasyOCR joins the filename straight onto model_storage_directory,
        so that argument names the directory that *contains* the .pth
        files. Pointing it at the parent finds nothing, and EasyOCR's
        response to finding nothing is to fetch it all again -- quietly,
        into the wrong place, on the user's connection.
        """
        import inspect

        from mapify.models import ocr as ocr_module

        source = inspect.getsource(ocr_module)
        assert 'store / "model"' in source, (
            "model_storage_directory must point at the model/ subdirectory"
        )
        assert 'store / "user_network"' in source

    def test_the_layout_the_installer_creates_is_the_one_easyocr_expects(self):
        """The installer and the reader have to agree about the shape.

        EasyOCR derives <MODULE_PATH>/model and <MODULE_PATH>/user_network
        itself, and the installer copies ~/.EasyOCR -- which already has
        that shape -- into the models directory wholesale. This asserts
        the two halves still describe the same tree.
        """
        installer = (paths.read_root() / "installer" / "install.ps1").read_text(
            encoding="utf-8"
        )
        assert "$easyTarget = Join-Path $ModelsDir 'easyocr'" in installer
        assert "Join-Path $easyTarget 'model'" in installer, (
            "the installer must check for the model/ subdirectory it copies"
        )

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


class TestItStartsWithNoConsole:
    """A windowed build has no stdout, and that killed one at startup.

    The packaged application died before drawing anything, with "Unable to
    configure formatter 'default'". Underneath: uvicorn's default log config
    builds a formatter whose `__init__` calls `sys.stdout.isatty()` to decide
    about colour, and a `--windowed` PyInstaller executable has no console, so
    `sys.stdout` is None.

    Every import resolved. The build's own self-test passed. Neither of those
    is the same as the program starting, which is what these check.
    """

    def test_uvicorn_is_not_allowed_to_configure_logging(self):
        """The one-line fix, asserted where it cannot be quietly undone."""
        import inspect

        from mapify import desktop

        source = inspect.getsource(desktop.run)
        assert "log_config=None" in source, (
            "uvicorn will run its own dictConfig and die without a console"
        )

    def test_uvicorns_default_config_really_does_fail_without_stdout(self):
        """Proof the fix is not cargo cult: reproduce the crash it prevents."""
        import logging.config

        import uvicorn.config

        saved = sys.stdout
        try:
            sys.stdout = None  # type: ignore[assignment]
            with pytest.raises(ValueError):
                logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
        finally:
            sys.stdout = saved

    def test_logging_configures_with_no_stdout_at_all(self, relocated):
        """Our own setup must survive what uvicorn's did not."""
        import logging

        from mapify import desktop

        saved_out, saved_err = sys.stdout, sys.stderr
        saved_handlers = logging.getLogger().handlers[:]
        try:
            sys.stdout = None  # type: ignore[assignment]
            sys.stderr = None  # type: ignore[assignment]
            log_path = desktop._configure_logging("warning")
            logging.getLogger("mapify.test").warning("into the file")
            for handler in logging.getLogger().handlers:
                handler.flush()
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
            for handler in logging.getLogger().handlers:
                handler.close()
            logging.getLogger().handlers = saved_handlers

        assert log_path is not None, "no console and no log file is no diagnosis"
        assert log_path.exists()
        assert "into the file" in log_path.read_text(encoding="utf-8")

    def test_the_selftest_covers_startup_not_just_imports(self):
        """The gap that let the broken build ship."""
        import inspect

        from mapify import desktop

        source = inspect.getsource(desktop._selftest)
        assert "headless startup" in source
        assert "writable state" in source


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

    def test_it_installs_beside_the_project_not_on_the_system_drive(self):
        """Everything Mapify owns under one root, on the project's drive.

        A gigabyte of program on C:, 1.6 GB of weights in a hidden profile
        directory and a cache somewhere else again is an installation
        nobody can find, move or delete. The default root is derived from
        where the project is, so a project on E: installs to E:.
        """
        script = self._installer("install.ps1")
        assert "$Root = Join-Path $ProjectRoot" in script
        assert "$InstallDir  = $Root" in script
        assert "$DataDir     = Join-Path $Root" in script
        assert "$ModelsDir   = Join-Path $Root" in script
        assert "ProgramFiles" not in script, (
            "installing to Program Files would need administrator"
        )

    def test_the_uninstaller_finds_a_non_default_location(self):
        """It can be installed anywhere, so the path cannot be hardcoded."""
        script = self._installer("uninstall.ps1")
        assert "InstallLocation" in script, (
            "the uninstaller must read where the installer actually put it"
        )

    def test_keeping_data_happens_before_the_program_is_removed(self):
        """Data lives inside the installation, so order is the whole thing.

        Removing the install directory first would delete the data that
        -KeepData exists to preserve, and the flag would be a lie.
        """
        script = self._installer("uninstall.ps1")
        rescue = script.index("rescue anything being kept")
        remove = script.index("# -------------------------------------------------------------- the program")
        assert rescue < remove, "-KeepData runs after the deletion it protects against"

    def test_an_older_install_under_the_profile_is_cleaned_up_too(self):
        script = self._installer("uninstall.ps1")
        assert "LegacyData" in script

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
