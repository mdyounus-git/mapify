# Next milestone — Pinpoint and walking the spot

Paused twice at the user's request, with everything committed: first on
2026-10-07 early, then again later the same day after a second session.
The user asked for two things: a place you can **walk around in**, and a
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
| `6b03838b` | Server endpoints (streaming, cancellable), config, tests |
| `ac10c0ba` | "Pinpoint exact spot" button, result panel, "Search further" |
| `8f4b31ed` | Walk mode: your photograph over the street (P), lens from EXIF, matched-photograph markers |
| `3670b965` | ATTRIBUTION.md section for Commons and Mapillary |
| `3bee1d77` | **Second session.** Regression fix: nearest photographs first again within a crowded spot |
| `e4369b4b` | Search Commons by the name of the landmark the photograph fits (CLIP over local OSM landmark names, `nearcoord:` text search) |
| `2cc043b6` | Details as POST (long Cyrillic titles), retry dropped connections, a failed search is *unavailable*, never *none* |

`tests/test_pinpoint.py`: 29 tests, all passing; the full suite passes.

## What was measured

**The confirmation line** (calibration, 600 pairs of different places):
unrelated photographs reach at most **14** inliers (median 8); warped
copies of a photograph at least **94**. Pinpointed at **40**, possible at
**20**.

**At the true spot, current engine** (radius 800 m, budget 400,
`bench/eval-pinpoint-true-v3.log`):

| Landmark | Result | Error | Compared | Time |
|---|---|---|---|---|
| Eiffel Tower | pinpointed, 55 points | 7 m | 200 | 206 s |
| Tower Bridge | pinpointed, 62 | 0 m | 100 | 109 s |
| Rome (Colosseum area) | pinpointed, 125 | 1 m | 100 | 112 s |
| Athens (Parthenon) | pinpointed, 132 | 6 m | 100 | 131 s |
| Tokyo (Sensō-ji) | pinpointed, 149 | 24 m | 100 | 148 s |
| Beijing | none (best 13) | — | 400 | 199 s |
| Moscow | failed: details URL too long (fixed in `2cc043b6`) | | | |
| Giza, Rio, San Francisco, Sydney | failed: this machine's connection dropped | | | |

**5 of the 6 that ran pinpointed, median error 6 m**, each within 100-200
comparisons and about 2 minutes. Athens and Tokyo, which had failed
transiently before, are now found. The earlier nearest-300 search got 4 of
11. Moscow, Giza, Rio, San Francisco and Sydney are being re-run (see
below).

**The regression found and fixed on the way:** with photographs inside a
60 m spot ordered by hash and pushed back 120 m / √n, the Eiffel photograph
at its true spot dropped to *possible* (33): its match was taken 7 m away,
but the 760 photographs at the tower's foot were spread over kilometres of
priority (`bench/eval-pinpoint-true-sqrt.log`). Nearest-first within a
spot, with a whole spot spread over at most 120 m, restored 55 points / 7 m.

**With the centre 600 m from the truth, before named search** (radius
1.5 km, budget 400): Eiffel *none* (13), Tower Bridge *possible* (22), Rome
*none* (14). ~5,000 photographs are listed in a dense centre; 400
comparisons by distance do not reach the right viewpoint.

**Named search, offline check** (`bench/names_probe.py`, centre 600 m off,
up to 250 landmark names weighed with CLIP): the right name is chosen for
Paris (Tour Eiffel), London (Tower Bridge), Rome (Colosseo), San Francisco
(Golden Gate Bridge) and Sydney (Sydney Opera House) — exactly the five
that match at the true spot; not for Moscow, Tokyo, Beijing, Cairo or Rio
(Cyrillic/Chinese names, which CLIP reads poorly). Live: "Tower Bridge"
returns the known matching photograph at relevance rank 113 (inside the 150
moved forward); "Tour Eiffel" does not return that particular one (its
title is English), though other photographs from under the tower may match.
**Its effect on recall is not measured yet** — the first attempt ran while
the connection was down and every search failed.

**Costs on this machine:**
- A thumbnail Wikimedia has not cached takes **1-3 s**, at every standard
  width (no edge cache hits measured). The robot policy allows **2 at
  once**: a first search runs at ~0.6-1 photograph a second. A repeat
  search in a cached area takes seconds.
- Matching 26 ms and feature extraction 53 ms per photograph.
- One geo search lists at most 500 files and takes ~4 s. Weighing 250
  names with CLIP takes a few seconds.

**Tried and not adopted:** ranking by CLIP similarity to the Commons
*titles* (weak, and 47-405 s of CPU). The `lift` hook in
`PinpointEngine._plan` remains; named search uses it internally.

## What was verified in the application

- The Pinpoint panel streams progress and shows an honest outcome.
- The walk overlay: the photograph fills the view top to bottom, `[`/`]`
  fade it, `-`/`=` change the lens (72° → 66° measured), the HUD says
  whether the lens is the photograph's own, Esc clears everything.

**Not yet seen in the browser:** the current engine end to end in the app
(including the "Also searched ... for photographs of X" note), "Search
further", the matched-photograph markers, and "Walk here" at a pinpointed
spot. **The installed `E:\mapify\app` predates walk mode and Pinpoint.**

---

## Where to pick up, in order

0. **Results waiting on disk.** At the pause a detached evaluation chain
   was still running (started 08:21 via WMI): first the five true-spot
   re-runs → `bench/eval-pinpoint-true-v3b.log`, then the centre-600-m-off
   run with named search → `bench/eval-pinpoint-offset600-names.log`.
   Before starting any other Commons search, check it has finished:
   `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` must show no
   `eval_pinpoint`. Read both logs; compare the names run with the
   no-names offset results above (Eiffel none, Tower Bridge possible 22,
   Rome none). If a log shows URLError throughout, the connection dropped
   again — re-run that part.
1. **Judge named search on those numbers.** If it confirms more of the
   five, keep the starting thresholds (`NAMED_MIN_Z` 1.0,
   `NAMED_MIN_MARGIN` 0.03, `NAMED_MIN_SCORE` 0.22, `NAMED_TOP` 150,
   `NAMED_LIFT_M` 800) and record the result in the engine's comments; if
   not, record that too. Ideas if names are the weak point: store OSM
   `name:en` alongside `name` (needs the store schema and a tile
   re-fetch), or search both the local and the English name.
2. **Browser test of the whole flow** with the dev server
   (`MAPIFY_CASE_DIR=E:/mapify/bench/cases-dev python -m mapify.server`,
   port 8771; copy a sample into `mapify/web/vendor/_dev/`, dispatch it to
   `#file-input` from JavaScript on `document.body`, delete the copy
   afterwards): Pinpoint → Search further → Walk here → P overlay →
   markers. Not while an evaluation is searching Commons.
3. **Rebuild and reinstall**: `python build_exe.py --clean` (~27 min), then
   `installer\install.ps1 -Force`; `Mapify.exe --selftest`.
4. Still pending from before: the text-bearing benchmark
   (`scripts/fetch_text_samples.py --target 70`, approved by the user) and
   `measure_text_clues.py --set text`.

## Rules that still hold

The globe stays exactly as it is (only `setPaused` was ever added; the
Pinpoint marker uses the existing `addMarker`). Everything lives on
`E:\mapify`. Free and legal sources only; Wikimedia's robot policy kept,
and never two processes searching Commons at once. No location is adopted
below the measured line, and a failed search is never reported as an
empty one.
