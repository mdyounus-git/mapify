"""Where Mapify reads from, and where it is allowed to write.

Those are two different questions and they used to be answered in four
different places, each of them by walking ``__file__`` upwards. That works
perfectly while the code lives in a checkout you own, and breaks the moment
someone installs the application somewhere sensible:

  * PyInstaller unpacks the bundled data next to the executable, not three
    directories above the module, so ``__file__``-relative reads find nothing;
  * an application installed under ``Program Files`` cannot write to its own
    directory at all. The SQLite lookup cache would fail to open and the 98 MB
    GPS gallery cache would silently fail to save -- meaning a minute of
    re-encoding on *every* launch, forever, with no error to explain it.

So this module answers both questions once:

    read_root()   what shipped with the application  (may be read-only)
    state_dir()   what this user accumulates         (always writable)

Running from a source checkout, ``state_dir()`` stays inside the tree. That is
deliberate: a developer's caches belong with the checkout they came from, and
moving them would orphan a gallery cache that took a minute to build. Once
frozen, state moves to the user profile, which is the only place an installed
Windows application may assume it can write.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "is_frozen", "read_root", "state_dir", "cache_dir", "config_path",
    "install_dir", "models_dir", "apply_model_cache_env", "describe",
]

_PKG = Path(__file__).resolve().parent


def is_frozen() -> bool:
    """True inside a PyInstaller build, false in a source checkout."""
    return bool(getattr(sys, "frozen", False))


def read_root() -> Path:
    """The directory holding data that shipped with the application.

    In a bundle that is PyInstaller's extraction root (``_MEIPASS``), which
    for a onedir build is the ``_internal`` folder beside the executable. In a
    checkout it is the repository root.
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return _PKG.parent


def _user_base() -> Path:
    """The per-user directory this platform expects applications to use."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base)
        return Path.home() / "AppData" / "Local"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def install_dir() -> Path | None:
    """The directory the executable was installed into, if frozen."""
    if not is_frozen():
        return None
    return Path(sys.executable).resolve().parent


def _writable(path: Path) -> bool:
    """Can we actually write here? Asked, not assumed."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_bytes(b"1")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def state_dir() -> Path:
    """Where this user's Mapify state lives. Created on demand.

    A frozen build keeps its state **beside the executable** whenever that
    directory can be written to. That makes an installation self-contained:
    everything Mapify owns -- caches, settings, logs, downloaded weights --
    lives under the folder it was installed into, on whichever drive that
    is, and moving the folder moves all of it.

    It falls back to the per-user profile when the install directory is
    read-only, which is what happens under ``Program Files``. Getting that
    fallback wrong is not cosmetic: the encoded GPS gallery silently fails
    to save and every launch pays five to eight minutes to rebuild it.

    ``MAPIFY_STATE_DIR`` overrides both, for tests and for anyone who wants
    their state somewhere specific.
    """
    override = os.environ.get("MAPIFY_STATE_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    if is_frozen():
        beside = install_dir()
        if beside is not None:
            candidate = beside / "data"
            if _writable(candidate):
                return candidate
        path = _user_base() / "Mapify"
    else:
        path = _PKG.parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    """Where downloaded model weights go.

    Beside the executable, for the same reason as the state directory: a
    1.6 GB download is the largest thing Mapify is responsible for, and it
    should live wherever the person put the application rather than
    defaulting onto the system drive.
    """
    override = os.environ.get("MAPIFY_MODELS_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        path = state_dir().parent / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def apply_model_cache_env() -> Path | None:
    """Point Hugging Face at our models directory, for a frozen build.

    Must run **before** anything imports ``huggingface_hub``, which reads
    these variables once at import time. Called first thing in ``run()``.

    Only for a frozen build. In a source checkout the developer's existing
    cache is almost certainly already populated, and silently relocating a
    1.6 GB download because they ran a script would be rude.

    An explicit ``HF_HOME`` or ``HF_HUB_CACHE`` in the environment always
    wins -- that is someone stating where their cache lives, and it is not
    ours to override.
    """
    if not is_frozen():
        return None
    if os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE"):
        return None
    try:
        target = models_dir()
    except OSError:
        return None
    if not _writable(target):
        return None
    os.environ["HF_HOME"] = str(target)
    os.environ["HF_HUB_CACHE"] = str(target / "hub")
    return target


def cache_dir() -> Path:
    """Caches: the HTTP lookup database and the encoded GPS gallery.

    Everything in here is reconstructible. Deleting it costs a minute of
    re-encoding and some repeated Nominatim lookups, and nothing else -- which
    is the property that makes it safe for an uninstaller to remove.
    """
    path = state_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    """The user's ``config.toml``, if they have written one.

    Beside the executable first, so a portable copy on a stick carries its own
    settings; then the per-user state directory; then, running from source,
    the checkout. The first that exists wins, and if none does the caller
    falls back to built-in defaults.
    """
    if is_frozen():
        candidates = [
            Path(sys.executable).resolve().parent / "config.toml",
            state_dir() / "config.toml",
        ]
    else:
        candidates = [
            _PKG.parent / "config.toml",
            state_dir() / "config.toml",
        ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def describe() -> dict[str, str]:
    """For the status endpoint and for bug reports: where everything is."""
    return {
        "frozen": str(is_frozen()),
        "read_root": str(read_root()),
        "install_dir": str(install_dir() or ""),
        "state_dir": str(state_dir()),
        "cache_dir": str(cache_dir()),
        "models_dir": str(models_dir()),
        "config_path": str(config_path()),
    }
