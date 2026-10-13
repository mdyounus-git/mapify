# Milestone — the research-led upgrade

The user asked for deep research into free, legitimately usable technology
that could make Mapify locate a photograph more exactly and show the place in
realistic 3D with a walk, without touching the globe or its animation. The
research is in [`PHASES/Research/2026-10-09-free-geolocation-and-3d.md`](../Research/2026-10-09-free-geolocation-and-3d.md):
every source checked against its licence file, code and activity, every
number labelled Tested, Read or Reported. Its key claims were re-checked by
hand on 9 October 2026 (GeoCLIP's 1.2.2/1.2.3 releases, PLONK's OSV-5M
checkpoints loading the non-commercial StreetCLIP, the deck.gl bundle lacking
a 3D Tiles loader, PDOK, Mapterhorn and Panoramax all answering) and held.

This records what has been built from it, what was measured, and what waits
on the user. Updated 2026-10-12 (paused here by the user).

---

## Built (all pushed)

| Commit | What |
|---|---|
| `5b148ca1` | GeoCLIP pinned to 1.2.1: 1.2.2 loads from the Hub and 1.2.3 changes CLIP's initialisation, and `>=1.2` would have taken either untested |
| `bc4d556c` | `TORCH_HOME` beside the models (torch.hub would write to `~/.cache/torch` on C:) |
| `f031eead` | HEIC/HEIF, the iPhone default, read when the optional `pi-heif` is installed (BSD-3 code, LGPL-3.0 wheels, no encoder; HEVC patents are why it is optional); refused with instructions otherwise; previews, Pinpoint and exports read it too |
| `166f597d` | Pinpoint asks Panoramax (no account, Etalab 2.0 / CC BY-SA 4.0, camera heading) — searched newest-first in three shells, one picture per sequence per 15 m |
| `50816763` | 3D view: national aerial photographs for France, the Netherlands, Switzerland, Spain and Japan, each only at home; flat-fill tiles recognised as missing; dropped tile connections retried |
| `461ae491` | 3D view: bare-earth terrain from Mapterhorn (Dam Square 1.8 m where the AWS tiles read 13.8 m of roofs), the AWS tiles as fallback (ocean, outage, offline) |
| `af3945d6` | Pinpoint finds landmarks through Wikidata (CC0) and searches their Commons category first: Red Square, Kremlin, St. Basil's for Moscow; the Great Pyramid and the Sphinx for Giza; Christ the Redeemer for Corcovado. Cities, things no longer standing and transit networks left out |
| `9d90f82c` | Terrain past zoom 15 cut from the deepest tile as heights, so finer ground never just vanishes |
| `a80055c1` | `GET /api/events/near`: earthquakes (USGS) and natural events (NASA EONET) around the place, at the photograph's time or lately; public domain, no key, context only |
| `511789ef` | Mirrored photographs (selfie cameras, flipped reposts) analysed the right way round; 0 of 177 photographs wrongly flipped, all 14 mirrored ones with readable text caught |
| `2519b672` | The walk's ground at zoom 16 under the walker, eye height read there (Bern: 540.4 m bare earth, eye 542.1 m) |
| `6ad5ebed` | "Around here" in the 3D view: natural events and the strongest earthquakes near the place, with distance and date |
| `70f42e5d` | The optional HEIC decoder kept out of the built package (the owner's call) |
| `d06f817d` | 360-degree panoramas read as four views of one moment, their opinions averaged |
| `72497353` | Pinpoint: Panoramax pictures queue behind the photographs of the place (Sydney at 600 m back to 0 m) |
| `0f434f0e` | GeoNames (CC BY 4.0, 13 M places, lookups under 1 ms) as the offline gazetteer's fallback, and a street-photo memory from OpenStreetView-5M (CC BY-SA 4.0); both off until measured |
| `e3b47425` | The language of text read off a photograph (GlotLID) and the countries that speak it (CLDR); reported in technique 12, weight zero until measured |
| `e768c507` | C2PA Content Credentials read and their signature checked (c2pa-python); "imagen" inside a camera tag no longer flags AI |
| `9c3c9281` | A checkout uses the models directory's weights, never the system drive's caches |
| `22fa17c3` | PLONK (MIT, YFCC checkpoint) as a second, independent locator; off until measured |
| `614461a1` | Packaging: c2pa's DLL bundled alone, a self-test line for the optional parts, no PLONK in the ZIP |
| `5d199612` | **Walk: buildings stand on the ground again** -- the terrain extension does not lift layers under the first-person camera (buildings at sea level under ground 14 m up in Chennai, 927 m in Bengaluru), and deck.gl's lighting failed hundreds of metres up; everything is now placed at its own ground, drawn relative to the walker's height |
| `9b9b22cf` | An India test frame: 48 places (`fetch_samples.py --frame india`, `evaluate.py --set india`) |
| `ca8c438b` | Pinpoint: DISK + LightGlue looks again at SIFT's close calls, calibrated on 400 unrelated pairs (chance max 34, strong 100); off until the 11-landmark run |
| `04684d79` | **Tamil is read**: EasyOCR 1.7.2's Tamil alphabet is truncated (126 of 142), so its model never loaded |
| `8cf32fee` | A lower-case word is not a place ("proven" = Prøven, Greenland); an offshore place keeps its country's codes |
| `6c3cb0b1` | A point is named after a city in its own country ("Lahore, India" -> Amritsar) |
| `5deb2883`, `cfa08ab2` | Build: kornia ships its source (TorchScript), PLONK stays out of the package |
| `56a02e31` | GeoNames asked for non-Latin-script names only ("ROAD" = Rode, Somerset; "Bamboo", Jamaica) |
| `139cee8e` | `evaluate.py --offset`, to resume a stopped run |
| (this commit) | Fusion: the evidence cap applies to each candidate relative to the others |

The user's direction (2026-10-11): the inspiration is "God's Eye". Taken as
fusing every open clue with its evidence shown, the globe-to-street dive and
live public layers around a place -- never finding or tracking people (no
face recognition, no private or hacked camera feeds).

## The mirror test (`511789ef`)

**The mirror test.** A selfie camera or a flipped repost reverses the
scene, and "Turkish text read backwards" is evidence against Turkey. When the
first OCR reading is poor (quality < 0.6), the flipped copy is read with the
same reader; if it reads clearly better (>= 0.6 and +0.1), the analysis runs
again on the flipped copy, says so, shows the photograph flipped, and
Pinpoint matches it flipped. `scripts/measure_mirror.py` reads every
photograph both ways and reports wrong flips and catches:

- benchmark (163, mostly little text): **0 wrongly flipped** at every
  threshold tried; all 7 with well-read text caught when mirrored; a second
  reading for 59 (those whose text reads poorly);
- text set (`samples/text/`, 15 fetched of the 70 approved): **0 wrongly
  flipped**; every one with well-read text caught.

## Measured coverage (Panoramax, 800 m radius)

Eiffel Tower 150 pictures / 9 sequences; Berlin Alexanderplatz 14; Nairobi 17
(nearest 723 m); Lagos none.

## Pinpoint at the true spot, with Wikidata names and Panoramax (2026-10-11)

`eval_pinpoint.py --names --panoramax`, radius 800 m, budget 400
(`bench/eval-pinpoint-true-v4-names-panoramax.log`), against the v3 runs:

| Landmark | Now | Before |
|---|---|---|
| Eiffel Tower | pinpointed, 16 m, 58 s | 7 m, 206 s |
| Tower Bridge | 0 m, 58 s | 0 m, 109 s |
| Rome (Celio) | 1 m | 1 m |
| Athens | 22 m | 6 m |
| Tokyo (Senso-ji) | **8 m** | 24 m |
| Sydney Opera House | 0 m, 54 s | 0 m, 58 s |
| Golden Gate | possible, 24 | possible, 24 |
| Moscow, Beijing, Giza, Rio | none (best 15, 13, 11, 16) | none |

**6 of 11, the same as before**; median 8 m (was 3.5 m), mostly faster. The
Wikidata category search did find photographs of St. Basil's, the Pyramid
of Khafre and Christ the Redeemer, but none shows the samples' own view, so
the four hard cases stay unmatched -- the limit is still the references, not
the names. Every winning match was a Commons photograph; Panoramax added
references but no pinpoint here.

## Downloads (all approved by the user on 2026-10-11, all on E:)

| Step | Download | Size | Why |
|---|---|---|---|
| Offline gazetteer | GeoNames `allCountries` + `alternateNamesV2` (CC BY 4.0) | 0.6 GB | Read text resolved without Nominatim; 13 M places instead of Natural Earth's 7,300 |
| Language of read text | GlotLID (Apache-2.0 plus a training-data notice) | 1.7 GB | Latin script across Africa, Europe and the Americas says little; the language says more |
| Retrieval memory | OpenStreetView-5M, 4 starter shards (CC BY-SA 4.0) | ~10 GB, then ~1 day of CPU | The largest published lead at city level on street imagery, reusing the CLIP embedding already computed; also where verified photographs are stored |
| Second locator | PLONK_YFCC (MIT) + DINOv2 via torch.hub (Apache-2.0) | ~1.3 GB | An independent density that can score the existing candidates |
| Near-miss matching | LightGlue + DISK weights (Apache-2.0) | small | A second pass on SIFT's near-misses, with its own calibrated threshold |

All five are downloaded into `app/models` (GeoNames 0.7 GB, GlotLID 1.6 GB,
OSV-5M 13 GB with four shards, PLONK 0.1 GB plus DINOv2 1.2 GB with the DISK
and LightGlue weights in `torch/`). Each is accepted or rejected by the
benchmark, as everything else is; until then each is off or report-only.

## India, measured (2026-10-12)

The user reported Indian photographs placed in the United States or Europe
and the walk showing no buildings. Both were real. The world frame holds one
Indian photograph, so an India frame of 48 was fetched (two are not places
and are refused: a map, code on a screen). Offline, OCR on:

| | In India | <=25 km | <=750 km | <=2500 km | Median |
|---|---|---|---|---|---|
| Before the fixes | 70% | 26% | 48% | 78% | 800 km |
| Tamil, word filter, offshore codes | 72% | 26% | 50% | 80% | 705 km |
| + relative cap + GeoNames (non-Latin) | -- | 26% | 50% | 80% | 704 km; Coimbatore 228 -> 2 km |

Found on the way: the Tamil reader never loaded; "proven" on an advert
matched a Greenland village; the offshore fallback dropped country codes; the
evidence cap was spent by evidence every candidate shares, so a sign reading
"Coimbatore" could not move the answer off Bengaluru (relative cap). Tried and
not adopted (re-fused on cached inputs, `scripts/fusion_sweep.py`): doubling the
region hint, a heavier script weight, other caps, and dividing GeoCLIP's modes
by its gallery's density (country-level up 2-4 points, street-level halved).

**What limits India now** is choosing among candidates, not finding them: a
candidate within 25 km is on the list for 50% of photographs and chosen for
26% (`python scripts/fusion_variants.py india`). The visual model cannot tell Mumbai
from Delhi or Chennai from Bengaluru; the evidence that could is new evidence
-- the second locator (PLONK) and the street-photo memory -- both built, off,
and next to measure.

**Measuring cheaply.** `scripts/fusion_variants.py <set>` runs the analysis once
per photograph and caches its inputs to fusion (`bench/cache/fusion/<set>/`);
variants then re-fuse in seconds. One analysis process at a time: each holds
7-8 GB (a sign reader per script), and two made the machine swap.

## Where it stopped (2026-10-12, user: "stop here, continue later")

- **Installed app** (`E:\mapify\app`): the build of 16:20 -- walk fix, Tamil,
  word filter, offshore codes, Telugu/Kannada readers (models folder). It does
  *not* yet have: the same-country label, the GeoNames script rule, the
  relative cap, PLONK left out (its self-test line still fails on PLONK, which
  is off and unused).
- **World check of the relative cap and GeoNames**: 96 of 163 photographs
  cached; on the first 89 no variant changed any answer. Resume with
  `bench\launch_hidden.ps1 -Cmd 'cmd.exe /c E:\mapify\bench\run-variants.cmd bench'`
  (cached photographs are skipped), then `python scripts/fusion_variants.py bench --cached-only`.
- **Then**: if nothing gets worse, turn `geonames_enabled` on, rebuild
  (`bench\run-build.cmd`, hidden), `installer\install.ps1 -Force` with Mapify
  closed, `app\Mapify.exe --selftest`.

## Next

1. Finish the world check above; ship the result in one rebuild and install.
2. Measure PLONK on the India frame (`fusion_variants` with `plonk_enabled`),
   then build the street-photo memory (about a day of CPU, hidden, resumable).
3. The 11-landmark Pinpoint run with `--second-matcher`.
4. OCR at more than 1,600 px for full-size phone photographs (small text);
   the benchmark's 1,280 px thumbnails cannot measure it.
5. A Latin-script GeoNames filter good enough for Indian town names
   ("Tambaram") without "ROAD" and "Bamboo".

The globe (`globe.js`) and its animation have not been touched.
