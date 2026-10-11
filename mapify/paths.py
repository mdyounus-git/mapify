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
    "install_dir", "models_dir", "ocr_model_dir",
    "apply_model_cache_env", "describe",
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
    """Where downloaded model weights live, or will.

    Beside the application, for the same reason as the state directory: a
    1.6 GB download is the largest thing Mapify is responsible for, and it
    belongs wherever the person put the application rather than defaulting
    onto the system drive.

    But an existing copy beats a tidy one. A source checkout and an
    installation built from it sit next to each other and need the same
    weights, and downloading them twice to keep two directories symmetrical
    would be 1.6 GB of nothing. So a few known locations are checked for a
    copy that is already there, and only if none has one does the default
    get created.
    """
    override = os.environ.get("MAPIFY_MODELS_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    primary = state_dir().parent / "models"
    candidates = [
        primary,
        # A checkout looking at the installation built beside it, which is
        # where install.ps1 puts things by default.
        read_root() / "app" / "models",
        # An installation looking at the checkout it came from.
        read_root() / "models",
    ]
    for candidate in candidates:
        try:
            if (candidate / "hub" / _CLIP_CACHE_DIR / "snapshots").is_dir():
                return candidate
        except OSError:
            continue

    primary.mkdir(parents=True, exist_ok=True)
    return primary


def geodata_dir() -> Path:
    """Where the local map-feature store and its source extracts live.

    The same rule as the model weights: an existing store beats a tidy
    one. A regional import is hundreds of megabytes and minutes of
    processing, and a checkout and the installation beside it should
    share one rather than each building their own.
    """
    override = os.environ.get("MAPIFY_GEODATA_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    primary = state_dir() / "geodata"
    candidates = [primary]
    beside = install_dir()
    if is_frozen() and beside is not None:
        # An installation looking at the checkout it was built from.
        candidates.append(beside.parent / "data" / "geodata")
    else:
        # A checkout looking at the installation built beside it.
        candidates.append(read_root() / "app" / "data" / "geodata")
    for candidate in candidates:
        try:
            if (candidate / "store.sqlite").is_file():
                return candidate
        except OSError:
            continue
    primary.mkdir(parents=True, exist_ok=True)
    return primary


def ocr_model_dir() -> Path | None:
    """Where EasyOCR should keep its weights, or None to leave it alone.

    EasyOCR defaults to ``~/.EasyOCR``: 565 MB of detection and
    recognition models on the system drive, whatever drive the
    application was installed on. A frozen build keeps them with
    everything else it owns.

    A source checkout is left alone -- the developer's copy is already
    downloaded and moving it would just download it again.
    """
    override = os.environ.get("MAPIFY_OCR_MODELS_DIR")
    if override:
        path = Path(override).expanduser()
    elif is_frozen():
        path = models_dir() / "easyocr"
    else:
        # The project's own copy wins when it exists, even from a source
        # checkout. Unlike the Hugging Face cache there is no argument
        # for deferring to ~/.EasyOCR: reading from the project costs
        # nobody a download, and defaulting to the home directory puts
        # half a gigabyte on the system drive for no benefit.
        #
        # Only when the project has nothing does EasyOCR keep its own
        # default, because at that point somebody's existing ~/.EasyOCR
        # is the only copy there is.
        candidate = models_dir() / "easyocr"
        try:
            if not any((candidate / "model").glob("*.pth")):
                return None
        except OSError:
            return None
        path = candidate
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


# The repository directory name Hugging Face gives the model Mapify needs.
# ``firstrun.COMPONENTS`` is the source of truth for which model that is;
# this duplicates the name because paths.py sits underneath firstrun and
# must not import it. A test asserts the two agree.
_CLIP_CACHE_DIR = "models--openai--clip-vit-large-patch14"


def _default_hf_cache_has_weights() -> bool:
    """Does the machine-wide Hugging Face cache already hold *our* model?

    Asked only in a source checkout, and only to decide whether leaving
    that cache alone would cost the developer a re-download.

    It has to name the model rather than ask whether the cache is
    non-empty: the cache is shared, and another tool's weights sitting in
    it say nothing about whether ours are there. Answering "yes" on the
    strength of somebody else's models is how you end up fetching 1.6 GB
    you already have a copy of two directories away.
    """
    base = os.environ.get("XDG_CACHE_HOME")
    hub = (Path(base) if base else Path.home() / ".cache") / "huggingface" / "hub"
    try:
        return (hub / _CLIP_CACHE_DIR / "snapshots").is_dir()
    except OSError:
        return False


def _snapshot_has_weights(hub: Path) -> bool:
    """Is a complete copy of the CLIP weights in this hub cache?

    A snapshot directory can exist half-filled after an interrupted
    download, so the weights file itself is what is asked about.
    """
    try:
        for snap in (hub / _CLIP_CACHE_DIR / "snapshots").iterdir():
            for name in ("model.safetensors", "pytorch_model.bin"):
                if (snap / name).exists():
                    return True
    except OSError:
        return False
    return False


def _go_offline_if_complete(hub: Path) -> None:
    """Stop Hugging Face checking the network when the weights are here.

    Without this every launch asked the Hub whether the model had changed
    -- the installed application's log said so: "You are sending
    unauthenticated requests to the HF Hub". Nothing needed downloading,
    and an application that says it works locally should not phone a
    server to find that out. Only switched on when the weights are
    verifiably present, because offline mode with a missing file is an
    error rather than a download. An explicit setting always wins.
    """
    if _snapshot_has_weights(hub):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _point_torch_hub_at_models() -> None:
    """Keep torch.hub's cache beside the other weights, off the system drive.

    Anything loaded through torch.hub -- weights and the code it fetches
    with them -- goes to ``~/.cache/torch`` unless ``TORCH_HOME`` says
    otherwise. Unlike Hugging Face there is no existing copy to preserve, so
    this applies to a checkout as well as a frozen build. torch reads the
    variable each time it resolves the directory; an explicit one wins.
    """
    if os.environ.get("TORCH_HOME"):
        return
    try:
        target = models_dir()
    except OSError:
        return
    if _writable(target):
        os.environ["TORCH_HOME"] = str(target / "torch")


def apply_model_cache_env() -> Path | None:
    """Point Hugging Face at our models directory, for a frozen build.

    Must run **before** anything imports ``huggingface_hub``, which reads
    these variables once at import time. Called first thing in ``run()``.

    A frozen build always does this. A source checkout does it only when
    the default cache does not already hold the weights -- because the
    developer's copy is usually already there, and silently relocating a
    1.6 GB download because somebody ran a script would be rude. When it
    is *not* there, falling back to the project's own models directory is
    strictly better than fetching the whole thing again: an installed
    Mapify beside the checkout has already downloaded it.

    An explicit ``HF_HOME`` or ``HF_HUB_CACHE`` in the environment always
    wins -- that is someone stating where their cache lives, and it is not
    ours to override.
    """
    _point_torch_hub_at_models()
    if os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE"):
        return None
    try:
        ours = (models_dir() / "hub" / _CLIP_CACHE_DIR / "snapshots").is_dir()
    except OSError:
        ours = False
    # A checkout uses the models directory when it already holds the weights,
    # even if the system drive's shared cache does too: the owner keeps
    # nothing of Mapify's on C:, and downloads made now must land beside the
    # rest. The shared cache is used only when it is the one copy there is.
    if not is_frozen() and not ours and _default_hf_cache_has_weights():
        base = os.environ.get("XDG_CACHE_HOME")
        _go_offline_if_complete(
            (Path(base) if base else Path.home() / ".cache") / "huggingface" / "hub")
        return None
    try:
        target = models_dir()
    except OSError:
        return None
    if not _writable(target):
        return None
    os.environ["HF_HOME"] = str(target)
    os.environ["HF_HUB_CACHE"] = str(target / "hub")
    _go_offline_if_complete(target / "hub")

    # EasyOCR reads this once, when easyocr.config is imported, and derives
    # its own model/ and user_network/ underneath it -- which is exactly
    # the layout the installer lays down. Belt and braces with the explicit
    # kwargs in models/ocr.py, because a miss here is a silent 565 MB
    # re-download rather than an error.
    if not os.environ.get("EASYOCR_MODULE_PATH"):
        os.environ["EASYOCR_MODULE_PATH"] = str(target / "easyocr")
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
        "ocr_model_dir": str(ocr_model_dir() or ""),
        "config_path": str(config_path()),
    }
