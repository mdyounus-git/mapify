# Next milestone — Pinpoint and walking the spot

The user asked for two things: a place you can **walk around in**, and a
**dedicated engine that maps the exact location** a photograph was taken.
Both exist, work, and are in the installed application. This records how
far each got, what was measured, and exactly where to pick up. Updated
2026-10-07 after the fourth session.

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

## The fourth session (2026-10-07, evening): a shareable package and a real place

The user asked for one package that works wherever it is shared, whether
Mapify runs fully offline (answered: **no** -- analysis yes; Pinpoint,
3D for places not seen before, and weather need the internet), and for the
3D view and walk to look like the real place. They chose **one portable
ZIP** and an **optional, labelled ambience**. What was built:

| Commit | What |
|---|---|
| `4515c4bd` | English names (`name:en`) of landmarks kept; named search weighs them and searches both names -- centre 600 m off, named search now confirms Eiffel, Tower Bridge, Sydney **and Athens** (4 of the 6 that pinpoint at the true spot) |
| `f8f863f2` | `scripts/make_portable.py --verify`: one ZIP (program + CLIP + 12 OCR models + GPS gallery, nothing personal); the packaged self-test reports the weights |
| `4f33343c` | The walk's real weather (Open-Meteo: sky, light, rain, snow, fog, haze, wind) and optional **Ambience** (people, vehicles, birds; labelled illustrative) |
| `0bee2721` | Map tiles older than 30 days are fetched again |
| `1608cf4d` | Smooth zoom in the 3D view and a settle-in animation with a pulse on zooming in (the globe untouched) |
| `0a9b3e40` | **Every part of a multi-part map feature kept**: central Paris showed 54 buildings around a street, now 2,219 -- an old bug that hid most buildings in dense cities everywhere |
| `40fd2935`, `d46829b3` | Walk label layer fix; ambience drives the street network |
| `233681a4`, `2fd5fb0e`, `5b91dd55` | **Real terrain**: Terrain Tiles on AWS through the tile cache; the overview's ground is the terrain under the imagery, the walk's eye stays 1.7 m above the real ground; own main-thread mesh loader (deck.gl's needs a CDN worker the page refuses) |

Seen in headless screenshots (`bench/cdp_shot.py`, Edge InPrivate with sync
off -- a fresh profile otherwise signs in and syncs the user's data): a
Paris street in the current rain with buildings on both sides, people and
vehicles on the street; Twin Peaks as a hill under the USGS photograph;
a walk on Russian Hill 78 m up. Not possible free: photographic facades
everywhere (Google's photorealistic tiles are a paid API) and real people
or vehicles.

## Where to pick up, in order

1. **Rebuild once more, then reinstall.** The ZIP made at the pause
   (`dist\Mapify-0.1.0-portable.zip`, 3.5 GB, `bench/portable3.log` ends
   "VERIFIED: self-contained") has terrain, the multi-part map fix,
   weather and ambience, but not the last two commits (`ed0df2fb` imagery
   over-zoom for sharp terrain close up, `353963dc` lifted rings). So: `python
   build_exe.py --clean` then `python scripts/make_portable.py --verify`
   (one WMI chain, ~45 min; do not edit the source until the build log says
   "COLLECT-00.toc completed"), then `installer\install.ps1 -Force` (into
   `E:\mapify\app`) and `E:\mapify\app\Mapify.exe --selftest`. The
   installed app is still the build from `f79574fc` (before all of this).
2. **Seen by eye now** (headless Edge, `bench/cdp_shot.py` for the 3D
   harness and `bench/cdp_flow.py` for the whole app page): the full app
   flow -- analyse the Eiffel photograph, Pinpoint ("Pinpointed", 59
   points, the Commons photo with its credit, "also searched for ... Eiffel
   Tower"), Walk here at the pinpointed spot in the current rain; terrain
   at Twin Peaks, Russian Hill (walk) and Corcovado; the zoom pulse (layer
   checked mid-animation). Not yet: the photo overlay over terrain, the
   matched-photo markers seen in a frame.
3. Ideas not started: trees in mapped parks and woods (positions would be
   illustrative, so they belong with ambience); English names for Moscow,
   Giza and Rio still choose wrong.
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
