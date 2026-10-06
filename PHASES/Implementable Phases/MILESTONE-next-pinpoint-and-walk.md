# Next milestone — Pinpoint and walking the spot

Paused 2026-10-07 at the user's request, with everything committed. The
user asked for two things: a place you can **walk around in**, and a
**dedicated engine that maps the exact location** a photograph was taken.
Both exist and work; this records how far each got, what was measured,
and exactly where to pick up.

---

## What is in the repository

| Commit | What |
|---|---|
| `6a050a48` | Walk mode: eye height 1.7 m, WASD/arrows, collision with buildings, street labels, floor lines, sun at the capture time |
| `1fdb1952` | The matcher: SIFT + ratio test + fundamental matrix (MAGSAC); `scripts/calibrate_pinpoint.py` |
| `27b9338b` | Reference sources: Wikimedia Commons (listing + cached details), optional Mapillary with the user's own token; Wikimedia robot policy kept |
| `02917322` | The engine: area coverage, priority order, batched comparison with early stop, budget, bounded cache; `scripts/eval_pinpoint.py` |
| `6b03838b` | Server endpoints (streaming, cancellable), config, 22 tests |
| `ac10c0ba` | "Pinpoint exact spot" button, result panel, "Search further" |
| `8f4b31ed` | Walk mode: your photograph over the street (P), lens from EXIF, matched-photograph markers |
| `3670b965` | ATTRIBUTION.md section for Commons and Mapillary |

The full test suite passes.

## What was measured

**The confirmation line** (calibration, 600 pairs of different places):
unrelated photographs reach at most **14** inliers (median 8); warped
copies of a photograph at least **94**. Pinpointed at **40**, possible at
**20**.

**At the true spot** (radius 800 m, the earlier nearest-300 search, before
the coverage work): **4 of 11 landmarks pinpointed, median error 4 m** —
Eiffel Tower 7 m, Tower Bridge 2 m, Rome 4 m, Sydney Opera House 0 m;
Golden Gate *possible* (24 points); Giza, Rio, Beijing *none*. Athens,
Moscow and Tokyo came back with **0 photographs in 1-2 s**: a transient
search failure (most likely throttling while two processes searched at
once), not an empty area. The engine now retries on 429/503 and reports a
failed search as *unavailable*; these three must be re-run.

**With the centre 600 m from the truth** (radius 1.5 km, the current
engine, budget 400): Eiffel *none* (13), Tower Bridge *possible* (22),
Rome *none* (14); San Francisco and Sydney not run (stopped here). In a
dense city centre ~5,000 photographs fall in the area and 400 comparisons
do not reach the right viewpoint. **Recall under estimate error is the
open problem.**

**Costs on this machine:**
- A thumbnail Wikimedia has not cached takes **1-3 s** to arrive, at every
  standard width (250, 330, 500, 960 px all measured ~1.7-2 s, no edge
  cache hits). The robot policy allows **2 at once**, so a first search
  runs at ~0.6-1 photograph a second: 400 photographs ≈ 7-11 min. A
  repeat search in a cached area takes seconds.
- Matching 26 ms and feature extraction 53 ms per photograph: compute is
  not the bottleneck.
- One geo search lists at most 500 files and takes ~4 s; in central Paris
  the nearest 300 reach only 253 m. The foot of the Eiffel Tower is one
  60 m cell with 760 photographs.

**Tried and not adopted:** ranking by CLIP similarity between the query
and the Commons *titles*. It moved Tower Bridge's 22-point photograph from
position 273 to 78 but the Eiffel match only from 4967 to 3822, and
encoding 2,800-5,000 titles took 47-405 s on this CPU. The `lift` hook in
`PinpointEngine._plan` is kept for it, unused.

## What was verified in the application

- The Pinpoint panel streams progress and shows an honest outcome (the
  first Eiffel run said *no match* because the search area missed the
  spot — which is what led to the coverage work).
- The walk overlay: the photograph fills the view top to bottom, `[`/`]`
  fade it, `-`/`=` change the lens (72° → 66° measured), the HUD says
  whether the lens is the photograph's own, Esc clears everything.

**Not yet seen in the browser:** the new engine end to end in the app,
"Search further", the matched-photograph markers, and "Walk here" at a
pinpointed spot. **The installed `E:\mapify\app` predates walk mode and
Pinpoint** — it has neither until rebuilt.

---

## Where to pick up, in order

1. **Re-run the evaluation with the current engine** (one process at a
   time — never two searching Commons at once):
   `python scripts/eval_pinpoint.py --scene landmark --radius 800`
   (all 11, including Athens/Moscow/Tokyo), then
   `--radius 1500 --offset 600 --sites san-francisco,sydney-opera`.
   Long runs go through WMI (`Invoke-CimMethod Win32_Process Create`),
   logs to `bench/`.
2. **Recall under estimate error.** Options, none tried yet:
   - Commons full-text search restricted by place —
     `list=search&srsearch=<name> nearcoord:<r>,<lat>,<lon>` in the File
     namespace — using names of landmarks/POIs near the estimate from the
     OSM data the 3D view already loads. Targets the photographs of the
     thing, however crowded the spot.
   - Search more of the analysis's candidates (each its own area).
   - A cheaper visual pre-ranking needs a small model; any new weights
     need the user's permission to download.
3. **Browser test of the whole flow** with the dev server
   (`MAPIFY_CASE_DIR=E:/mapify/bench/cases-dev python -m mapify.server`,
   port 8771; copy a sample into `mapify/web/vendor/_dev/`, dispatch it to
   `#file-input`, delete the copy afterwards): Pinpoint → Search further →
   Walk here → P overlay → markers.
4. **Rebuild and reinstall**: `python build_exe.py --clean` (~27 min), then
   `installer\install.ps1 -Force`; `Mapify.exe --selftest`.
5. Still pending from before: the text-bearing benchmark
   (`scripts/fetch_text_samples.py --target 70`, approved by the user) and
   `measure_text_clues.py --set text`.

## Rules that still hold

The globe stays exactly as it is (only `setPaused` was ever added; the
Pinpoint marker uses the existing `addMarker`). Everything lives on
`E:\mapify`. Free and legal sources only; Wikimedia's robot policy kept.
No location is adopted below the measured line, and a failed search is
never reported as an empty one.
