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
on the user. Updated 2026-10-09.

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

## Waiting on the user (downloads need permission)

| Step | Download | Size | Why |
|---|---|---|---|
| Offline gazetteer | GeoNames `allCountries` + `alternateNamesV2` (CC BY 4.0) | 0.6 GB | Read text resolved without Nominatim; 13 M places instead of Natural Earth's 7,300 |
| Language of read text | GlotLID (Apache-2.0 plus a training-data notice) | 1.7 GB | Latin script across Africa, Europe and the Americas says little; the language says more |
| Retrieval memory | OpenStreetView-5M, 4 starter shards (CC BY-SA 4.0) | ~10 GB, then ~1 day of CPU | The largest published lead at city level on street imagery, reusing the CLIP embedding already computed; also where verified photographs are stored |
| Second locator | PLONK_YFCC (MIT) + DINOv2 via torch.hub (Apache-2.0) | ~1.3 GB | An independent density that can score the existing candidates |
| Near-miss matching | LightGlue + DISK weights (Apache-2.0) | small | A second pass on SIFT's near-misses, with its own calibrated threshold |

Each is accepted or rejected by the benchmark, as everything else is.

## Next, without downloads

1. The 600 m-offset evaluation with Wikidata names
   (`bench/eval-pinpoint-offset600-v4.log`), the case named search exists for.
2. Rebuild, make the portable ZIP and reinstall, so the app has all of this.
3. Walk terrain finer than z14 in Mapterhorn's lidar countries.
4. The rebuild and portable ZIP from the previous milestone, which now also
   need to include everything above.

The globe (`globe.js`) and its animation have not been touched.
