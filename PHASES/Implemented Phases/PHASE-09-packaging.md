# Phase 09 — Packaging

**State:** implemented · commit `42388d2`

`Mapify.exe`, and a build that refuses to claim it works when it does not.

---

## Goal

Produce something a person can double-click without installing Python.

## What was built

**`build_exe.py`** produces a folder distribution with `Mapify.exe` in it.
A folder, not a single file: a one-file build of a PyTorch application is a
two-gigabyte self-extracting archive that unpacks to a temp directory on every
launch, which is worse in every way.

Bundled: the interface, three.js, the 8k Earth textures, the Natural Earth
data, the country table, and the licence documents. **Not** bundled: the model
weights, which live in the Hugging Face cache and are fetched on first run —
bundling them would make a 4 GB download to ship something that then downloads
nothing.

**`--selftest`** — the important part. PyInstaller reports success whether or
not the application can actually start, so after packaging the build script
runs the new selftest *inside the executable it just produced* and fails the
build if it does not come back clean. It deliberately stops short of loading
weights and checks only that every import resolves and the bundled data is
where the code expects it.

## Verification

```
1104 MB in Mapify/
    torch             ok    2.14.0+cpu
    model stack       ok    transformers 5.17.0
    pywebview         ok
    interface assets  ok    interface present
    natural earth     ok    reverse geocode -> India
    country table     ok    239 countries
    pipeline          ok    engine constructed
  SELFTEST OK

window title: "Mapify 0.1.0 — Image Geolocation Reconnaissance"
```

## What went wrong

Two failures, and the selftest is why they were caught rather than shipped:

- **The first build was 258 MB and finished in 47 seconds**, which should have
  been the tell on its own: PyTorch was not in it. The model stack is imported
  lazily on purpose, so the window opens before a gigabyte of torch does, which
  means PyInstaller's static analysis never saw any of it. Now collected
  explicitly.
- **The second build ran and died immediately** with `attempted relative import
  with no known parent package`. PyInstaller executes the entry script as a
  top-level `__main__` with no parent package, so `mapify/__main__.py` and its
  `from .desktop import run` cannot resolve. The frozen build now enters through
  `run_mapify.py`, which imports absolutely and calls
  `multiprocessing.freeze_support()` so a spawned child does not relaunch the
  window.

PyInstaller exited 0 on both.

## Left open

Unsigned. Windows SmartScreen will warn on first run, and it should — an
unsigned binary from the internet deserves a warning. Signing needs a
certificate. An installer is Phase 20.
