"""What the first launch is going to cost, said before it is spent.

Mapify ships without model weights. The first launch downloads about 1.6 GB
from Hugging Face and then spends a minute encoding the GPS gallery. For a
developer that is a known tax. For anybody else it is an application that
opens, says "loading models" in eight-point type at the bottom of the window,
and then does nothing at all for ten minutes on a slow connection.

So this module exists to answer three questions *before* any of it starts, and
to answer them without importing PyTorch -- the whole point is that the screen
appears immediately:

    status()      is a download needed, how big, from where, under what licence
    watch()       how far through it is right now
    DEFERRED      the user said not yet, and that has to be remembered

Everything here reads the filesystem. Nothing here downloads anything; that is
the caller's decision, made after the user has seen the number.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .paths import cache_dir, state_dir

__all__ = ["Component", "COMPONENTS", "status", "watch", "hf_cache_dir",
           "is_deferred", "set_deferred"]


@dataclass(frozen=True)
class Component:
    """A model that has to arrive over the network before anything works."""

    key: str
    label: str
    repo: str
    expected_bytes: int
    licence: str
    origin: str
    why: str


# Measured on 2026-09-26 from a complete cache: model.safetensors is 1,631 MiB
# and the tokeniser and config files add four more. Stated as an expectation,
# not a promise -- if upstream repacks the repository this number moves, and
# the progress readout says "of about" for that reason.
COMPONENTS: tuple[Component, ...] = (
    Component(
        key="clip",
        label="OpenAI CLIP ViT-L/14",
        repo="openai/clip-vit-large-patch14",
        expected_bytes=1_715_000_000,
        licence="MIT",
        origin="huggingface.co",
        why="Both halves of the analysis run on it: GeoCLIP uses it to place "
            "the photograph, and the evidence pass uses the same encoder to "
            "read traffic direction, architecture, biome and script.",
    ),
)

# GeoCLIP's own weights -- the location encoder and the 100,000-point gallery,
# 44 MB in total -- are package data inside the pip distribution and are
# bundled into the build, so they never appear here. Worth stating because the
# obvious assumption is that the geolocation model is the big download, and it
# is not.

_DEFER_MARKER = "deferred-download"


def hf_cache_dir() -> Path:
    """Where Hugging Face keeps downloaded weights on this machine.

    Resolved from the environment exactly the way ``huggingface_hub`` resolves
    it, but without importing it: this runs while the window is still opening
    and must not pull in a dependency tree to answer a filesystem question.
    """
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return Path(explicit)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "huggingface" / "hub"


def _repo_dir(repo: str) -> Path:
    return hf_cache_dir() / ("models--" + repo.replace("/", "--"))


def _present(component: Component) -> bool:
    """True when the weights are on disk and complete.

    Hugging Face writes into ``blobs/*.incomplete`` and only links a snapshot
    once a file has finished, so the presence of a plausible weight file in a
    snapshot is a sound completeness test. The size floor rejects a truncated
    file left behind by an older client.
    """
    snapshots = _repo_dir(component.repo) / "snapshots"
    if not snapshots.is_dir():
        return False
    for snapshot in snapshots.iterdir():
        if not (snapshot / "config.json").is_file():
            continue
        for name in ("model.safetensors", "pytorch_model.bin"):
            weights = snapshot / name
            try:
                if weights.is_file() and weights.stat().st_size > 100_000_000:
                    return True
            except OSError:
                continue
    return False


def _bytes_on_disk(component: Component) -> int:
    """How much of this component has arrived, counting partial files."""
    root = _repo_dir(component.repo)
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def gallery_cached() -> bool:
    """True when the encoded GPS gallery is memoised and the minute is saved."""
    try:
        return any(cache_dir().glob("geoclip_gallery_*.npy"))
    except OSError:
        return False


def _defer_file() -> Path:
    return state_dir() / _DEFER_MARKER


def is_deferred() -> bool:
    """Did the user decline the download on a previous launch?"""
    return _defer_file().exists()


def set_deferred(value: bool) -> None:
    """Remember the answer, so we do not ask again on every launch."""
    marker = _defer_file()
    try:
        if value:
            marker.write_text(
                "The first-run download was declined. Delete this file, or "
                "press Download in the application, to be offered it again.\n",
                encoding="utf-8",
            )
        else:
            marker.unlink(missing_ok=True)
    except OSError:
        pass


def status() -> dict:
    """Everything the first-run screen needs, cheap enough to call on boot."""
    missing = [c for c in COMPONENTS if not _present(c)]
    have_gallery = gallery_cached()
    return {
        "download_required": bool(missing),
        "gallery_encode_required": not have_gallery,
        # Nothing to do at all: weights present, gallery memoised. The screen
        # never appears and warmup finishes in a couple of seconds.
        "ready": not missing and have_gallery,
        "deferred": is_deferred(),
        "total_bytes": sum(c.expected_bytes for c in missing),
        "cache_dir": str(hf_cache_dir()),
        "components": [
            {
                "key": c.key,
                "label": c.label,
                "repo": c.repo,
                "bytes": c.expected_bytes,
                "licence": c.licence,
                "origin": c.origin,
                "why": c.why,
                "present": c not in missing,
            }
            for c in COMPONENTS
        ],
        # Two measurements, both on CPU, both of the same 100,000-point
        # encode: 272s during Phase 7, and 457s on 2026-09-26 on a machine
        # with 0.6 GB of RAM free. It is memory-hungry, so the spread is
        # real. 300 is the honest middle, the screen says "usually", and
        # the bar stops claiming a position once it is exceeded.
        #
        # An earlier draft of this used 57s, taken from a per-call profile
        # line rather than from the warmup measurement beside it. A first-run
        # screen that understates the wait by a factor of five is worse than
        # no screen, which is why the number now carries its provenance.
        "gallery_encode_seconds": 300,
    }


def watch() -> dict:
    """How far the download has got, right now.

    Polling the cache directory rather than hooking the downloader's progress
    bar: it is accurate to within one poll interval, needs no cooperation from
    whichever version of ``huggingface_hub`` is installed, and keeps counting
    correctly if the transfer is resumed after a restart.
    """
    have = 0
    want = 0
    for component in COMPONENTS:
        want += component.expected_bytes
        have += min(_bytes_on_disk(component), component.expected_bytes)
    return {
        "bytes": have,
        "total": want,
        "fraction": (have / want) if want else 1.0,
    }


class Rate:
    """A smoothed transfer rate, so the estimate does not flicker.

    An exponential moving average over the last few samples. A raw
    difference between two polls half a second apart swings between 0 and
    twice the true rate depending on where the writes landed, and an estimate
    that jumps from "2 minutes" to "18 minutes" and back is worse than none.
    """

    def __init__(self, halflife_s: float = 6.0):
        self._alpha_halflife = halflife_s
        self._last_bytes: int | None = None
        self._last_time: float | None = None
        self._rate = 0.0
        self._lock = threading.Lock()

    def update(self, byte_count: int, now: float) -> float:
        import math

        with self._lock:
            if self._last_time is None:
                self._last_bytes, self._last_time = byte_count, now
                return 0.0
            dt = now - self._last_time
            if dt < 0.2:
                return self._rate
            instant = max(0.0, (byte_count - (self._last_bytes or 0)) / dt)
            weight = 1.0 - math.exp(-dt / self._alpha_halflife)
            self._rate = (1 - weight) * self._rate + weight * instant
            self._last_bytes, self._last_time = byte_count, now
            return self._rate

    @property
    def bytes_per_second(self) -> float:
        return self._rate
