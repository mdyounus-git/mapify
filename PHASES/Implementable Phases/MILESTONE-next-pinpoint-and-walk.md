# Next milestone — Pinpoint and walking the spot

The user asked for two things: a place you can **walk around in**, and a
**dedicated engine that maps the exact location** a photograph was taken.
Both exist, work, and are in the installed application. This records how
far each got, what was measured, and exactly where to pick up. Updated
2026-10-07 after the third session.

---

## What is in the repository

| Commit | What |
|---|---|
| `6a050a48` | Walk mode: eye height 1.7 m, WASD/arrows, collision with buildings, street labels, floor lines, sun at the capture time |
| `1fdb1952` | The matcher: SIFT + ratio test + fundamental matrix (MAGSAC); `scripts/calibrate_pinpoint.py` |
| `27b9338b` | Reference sources: Wikimedia Commons (listing + cached details), optional Mapillary with the user's own token; Wikimedia robot policy kept |
| `02917322` | The engine: area coverage, priority order, batched comparison with early stop, budget, bounded cache; `scripts/eval_pinpoint.py` |
| `6b03838b` | Server endpoints (streaming, cancellable), config, tests |
| `ac10c0ba` | "Pinpoint exact spot" button, result panel, "Search further" |
| `8f4b31ed` | Walk mode: your photograph over the street (P), lens from EXIF, matched-photograph markers |
| `3670b965` | ATTRIBUTION.md section for Commons and Mapillary |
| `3bee1d77` | Regression fix: nearest photographs first again within a crowded spot |
| `e4369b4b` | Search Commons by the name of the landmark the photograph fits (CLIP over local OSM landmark names, `nearcoord:` text search) |
| `2cc043b6` | Details as POST (long Cyrillic titles), retry dropped connections, a failed search is *unavailable*, never *none* |
| `f79574fc` | The packaged build's self-test covers Pinpoint (SIFT) and the walk-mode scripts |
| `10942fdb` | Named search's measured result recorded in the engine |

`tests/test_pinpoint.py`: 29 tests, all passing; the full suite passes.
**Installed** 2026-10-07: `build_exe.py --clean` from `f79574fc` (1,110 MB,
SELFTEST OK including Pinpoint), `installer\install.ps1 -Force` into
`E:\mapify\app` (data and models kept), installed `Mapify.exe --selftest` OK.

## What was measured

**The confirmation line** (calibration, 600 pairs of different places):
unrelated photographs reach at most **14** inliers (median 8); warped
copies of a photograph at least **94**. Pinpointed at **40**, possible at
**20**.

**At the true spot, current engine** (radius 800 m, budget 400;
`bench/eval-pinpoint-true-v3.log`, `-v3b.log`, `-v3c.log`):

| Landmark | Result | Error | Compared | Time |
|---|---|---|---|---|
| Tower Bridge | pinpointed, 62 points | 0 m | 100 | 109 s |
| Sydney Opera House | pinpointed, 140 | 0 m | 99 | 58 s |
| Rome (Celio, by the Colosseum) | pinpointed, 125 | 1 m | 100 | 112 s |
| Athens (Parthenon) | pinpointed, 132 | 6 m | 100 | 131 s |
| Eiffel Tower | pinpointed, 55 | 7 m | 200 | 206 s |
| Tokyo (Sensō-ji) | pinpointed, 149 | 24 m | 100 | 148 s |
| Golden Gate | possible, 24 | — | 400 | 86 s |
| Moscow | none, best 16 | — | 400 | 701 s |
| Rio | none, best 16 | — | 400 | 246 s |
| Beijing | none, best 13 | — | 400 | 199 s |
| Giza | none, best 11 | — | 400 | 191 s |

**6 of 11 pinpointed, median error 3.5 m** (the earlier nearest-300 search:
4 of 11). The four *none* never rose above 16 points — chance level: the
Commons photographs near those spots do not show the same view.

**With the centre 600 m from the truth** (radius 1.5 km, budget 400):

| Landmark | By distance alone | With named search (`--names`) |
|---|---|---|
| Eiffel Tower | none (13) | **pinpointed, 59 points, 16 m** (Tour Eiffel) |
| Tower Bridge | possible (22) | **pinpointed, 62, 0 m** (Tower Bridge) |
| Rome | none (14) | none (18) (Colosseo — the query shows a Celio street) |
| Golden Gate | not run | none (19) (only *possible* even at the true spot) |
| Sydney Opera House | not run | **pinpointed, 140, 0 m** (Sydney Opera House) |

`bench/eval-pinpoint-offset600-names.log`. The thresholds
(`NAMED_MIN_Z` 1.0, `NAMED_MIN_MARGIN` 0.03, `NAMED_MIN_SCORE` 0.22,
`NAMED_TOP` 150, `NAMED_LIFT_M` 800) are kept.

**The regression found and fixed on the way:** with photographs inside a
60 m spot ordered by hash and pushed back 120 m / √n, the Eiffel photograph
at its true spot dropped to *possible* (33): its match was taken 7 m away,
but the 760 photographs at the tower's foot were spread over kilometres of
priority (`bench/eval-pinpoint-true-sqrt.log`). Nearest-first within a
spot, a whole spot spread over at most 120 m, restored 55 points / 7 m.

**Where named search chooses wrong** (`bench/names_probe.py`, centre 600 m
off): Moscow, Tokyo, Beijing, Cairo, Rio — local names in Cyrillic or
Chinese, which CLIP reads poorly. OpenFreeMap's tiles carry the English
name too (`name:en`, e.g. Saint Basil's Cathedral), but the local store
keeps only `name`.

**Costs on this machine:**
- A thumbnail Wikimedia has not cached takes **1-3 s**, at every standard
  width. The robot policy allows **2 at once**: a first search runs at
  ~0.6-1 photograph a second; a repeat search in a cached area takes seconds.
- Matching 26 ms and feature extraction 53 ms per photograph.
- One geo search lists at most 500 files and takes ~4 s; weighing 250 names
  with CLIP takes a few seconds.
- Fifty Cyrillic titles made a 9,946-character GET URL the server refused;
  details now go as POST.

**Tried and not adopted:** ranking by CLIP similarity to the Commons
*titles* (weak, and 47-405 s of CPU).

## What was verified in the application

- **The whole flow through the application's own endpoints**
  (`bench/flow_http.py`: `/api/analyse`, then `/api/pinpoint/stream` with the
  areas `app.js` derives): the Eiffel photograph is **pinpointed in 93 s,
  16 m from where it was taken**, by two matching photographs (59 and 46
  points), after listing 7,190 photographs and searching by the name "Tour
  Eiffel"; the matched thumbnail is served with its CC BY-SA 4.0 licence and
  author. In the first session the same flow said *no match*.
- The Pinpoint panel streams progress and shows an honest outcome.
- The walk overlay: the photograph fills the view top to bottom, `[`/`]`
  fade it, `-`/`=` change the lens (72° → 66° measured), the HUD says
  whether the lens is the photograph's own, Esc clears everything.

**Not yet seen by eye:** the panel rendering a *pinpointed* result with the
"Also searched … for photographs of X" note, "Search further", the
matched-photograph markers in the street, and "Walk here" at a pinpointed
spot. The in-app browser tools did not work in the third session (each
pointed to another that was not offered), so this was checked over HTTP
only.

---

## Where to pick up, in order

1. **English landmark names** for the places named search gets wrong. Keep
   OSM `name:en` beside `name` in the local store (the OpenFreeMap POI
   layer has it; the local OSM import has `name:en` tags), weigh
   "a photo of <English name>" with CLIP, and search Commons by both names.
   Already-fetched tiles lack it: re-fetch POIs lazily (a store version
   bump that clears the OpenFreeMap fetched-marks). Measure with
   `bench/names_probe.py` (no Commons calls) and then
   `scripts/eval_pinpoint.py --names --offset 600 --sites moscow,tokyo-asakusa,beijing`
   — but only Tokyo pinpoints at the true spot, so judge by name choice
   first.
2. **See the flow by eye** once the in-app browser tools work: dev server
   (`MAPIFY_CASE_DIR=E:/mapify/bench/cases-dev python -m mapify.server`,
   port 8771; copy a sample into `mapify/web/vendor/_dev/`, dispatch it to
   `#file-input` from JavaScript on `document.body`, delete the copy
   afterwards): Pinpoint → pinpointed panel → Walk here → P overlay →
   markers; and "Search further" on a photograph that is not found.
   Never while an evaluation is searching Commons.
3. **Rebuild and reinstall** after any engine change: `python build_exe.py
   --clean` (~22 min), then `installer\install.ps1 -Force` (installs into
   `E:\mapify\app`, despite its help text); `Mapify.exe --selftest`.
4. Still pending from before: the text-bearing benchmark
   (`scripts/fetch_text_samples.py --target 70`, approved by the user) and
   `measure_text_clues.py --set text`.

Long runs go through WMI (`Invoke-CimMethod Win32_Process Create`) so they
survive the session; one Commons-searching process at a time; logs in
`bench/`.

## Rules that still hold

The globe stays exactly as it is (only `setPaused` was ever added; the
Pinpoint marker uses the existing `addMarker`). Everything lives on
`E:\mapify`. Free and legal sources only; Wikimedia's robot policy kept,
and never two processes searching Commons at once. No location is adopted
below the measured line, and a failed search is never reported as an
empty one.
