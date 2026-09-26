# Phase 20 — Installer

**State:** implemented

Put it on a machine that is not the one it was written on.

---

## Goal

`dist/Mapify/` is 1.1 GB of folder, and "unzip this and find the exe" is where
non-technical people stop. But the plan said the installer was the smaller half
of this phase, and that was right: the bigger half is everything that only
goes wrong once the application is somewhere other than a developer's
checkout.

Three of those turned up.

## 1. An installed application may not be able to write to itself

Mapify resolved both of its writable paths by walking `__file__` upwards:

```python
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"
cache_path = ROOT / "data" / "cache" / "lookups.sqlite"
```

Perfect in a checkout you own. Under `Program Files` the SQLite cache cannot
be opened at all, and — worse, because it is silent — the 98 MB encoded
gallery fails to save. The code catches `OSError` and moves on, so the only
symptom is five to eight minutes of re-encoding on **every** launch, forever,
with nothing on screen to explain it.

`mapify/paths.py` now answers two questions that were being conflated:

```
read_root()   what shipped with the application  (may be read-only)
state_dir()   what this user accumulates         (always writable)
```

It is frozen-aware — `_MEIPASS` in a bundle, the repository root in a
checkout — and in a checkout `state_dir()` deliberately stays inside the tree,
so nobody's existing gallery cache is orphaned by the change. `MAPIFY_STATE_DIR`
overrides it for tests and portable installs.

## 2. The first launch spends ten minutes and used to say "loading models…"

In eight-point type, in the status bar, after the decision had already been
taken. That is not informing somebody.

There is now a first-run screen, and it is the actual content of this phase.
It appears before anything starts, because `mapify/firstrun.py` answers from
the filesystem and imports nothing heavy — **50 ms**, asserted by a test that
fails if `torch` ends up in `sys.modules`.

It states the size, where it comes from, under what licence, where it will be
stored, and what happens afterwards. Then it can be **declined**, and the
refusal is remembered rather than re-asked on every launch; the offer comes
back when the user opens an image, which is the moment it actually matters.

Progress merges two sources, because neither is sufficient alone:

- the engine calls back as each stage begins — the only way to distinguish a
  download from an encode, which otherwise look identical from outside;
- the Hugging Face cache directory is polled for a byte count — the only part
  a person can judge progress by. Polling rather than hooking the downloader's
  progress bar: accurate to within one poll, independent of which
  `huggingface_hub` is installed, and still correct across a resumed transfer.

Three situations reach the screen and it distinguishes them honestly. Weights
missing is a download. Weights present with no encoded gallery is local
computation, and saying "needs to download" there would be a plain untruth, so
it does not. Nothing to do at all skips the screen entirely and warms up in
silence, as it always did.

## 3. A missing WebView2 runtime produced a COM HRESULT

`0x80040154`, from inside .NET interop. True, and useless. A registry read now
happens before pywebview is asked for a window, and the failure is a sentence
with a URL in it.

## The installer itself

Per-user, deliberately:

```
Program        %LOCALAPPDATA%\Programs\Mapify
Data & caches  %LOCALAPPDATA%\Mapify
Shortcuts      Start menu, desktop if asked
Registry       HKCU:\...\Uninstall\Mapify
```

That is the whole list, and `uninstall.ps1` removes exactly those four. No
administrator, no HKLM write — a test walks `install.ps1` line by line and
fails on any write cmdlet on a line mentioning HKLM, while allowing the reads
that detect WebView2. A tool that analyses somebody's photographs has no
business installing itself for every account on the machine.

The installer copies an existing encoded gallery into the new installation.
That is safe because the cache filename is a hash of the gallery it was built
from and the loader verifies it, and it saves a new install the entire
five-to-eight-minute wait.

`installer/mapify.iss` produces a single `Setup.exe` with Inno Setup for people
who should not have to run a PowerShell script. It makes the same three
decisions. Neither is code-signed, both say so, and `first-run-notice.txt`
tells the reader that the SmartScreen warning they are about to see is
**correct** rather than suggesting a way around it.

## Verification

27 tests in `tests/test_install.py`:

```
TestWritablePaths       the cache is writable; no module rebuilds a cache
                        path from __file__ (a regression guard on the bug)
TestFirstRunHonesty     answering does not import torch
                        a truncated download does not read as present
                        progress cannot exceed 1.0
                        deferral survives a restart
                        the rate estimate does not swing
                        the gallery is not counted as a download
                        geoclip's bundled weights are not counted either
TestWebView2Preflight   pv=0.0.0.0 is not "installed"
                        the message contains no HRESULT
TestInstallerScripts    per-user only; every key written is removed again
                        uninstalling offers to keep the 1.6 GB
                        it does not claim to be signed
```

Full suite: **266 passed**.

And both first-run states were driven for real in a browser against a live
service:

```
empty HF cache      "1.72 GB to download", MIT chip, origin, destination,
                    "Not now" -> marker written, offer not repeated on boot
weights present,    "Mapify needs a minute to finish setting up",
no gallery cache    "Nothing to download", button reads "Set up now",
                    encode ran to completion and cached 102 MB
```

The stated size is accurate to 0.05%: 1,714,256,514 bytes actually present
against 1,715,000,000 expected.

## What went wrong

Both faults were in the honesty of the screen, which is the one thing it
exists for.

- **The bar pinned at 96% and sat there.** Past the estimate it was clamped,
  so a long encode looked exactly like a hang. It now stops claiming a
  position: an indeterminate sweep, and text that says how long it has been
  and that a machine short on memory takes longer.
- **The estimate was wrong by a factor of five.** I wrote 57 seconds, taken
  from a per-call profile line in the Phase 7 document rather than from the
  warmup measurement four paragraphs below it in the same file, which says
  272s. The live run took **457s** on a machine with 0.6 GB of RAM free. It
  now says 300, the comment carries both measurements, and a test rejects
  anything outside them. A first-run screen that understates the wait by a
  factor of five is worse than no first-run screen.

## Left open

- **No code signing.** Without a certificate SmartScreen warns, and that
  warning is correct. Documented rather than worked around.
- The Inno script is written and not yet compiled here, because Inno Setup is
  not installed on this machine. `install.ps1` is the path that is actually
  exercised on every build.
- No update mechanism. Reinstalling over the top works and keeps the caches;
  there is no check for a newer version.
