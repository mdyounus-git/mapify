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

## In the working tree, waiting on a measurement

**The mirror test.** A selfie camera or a flipped repost reverses the
scene, and "Turkish text read backwards" is evidence against Turkey. When the
first OCR reading is poor (quality < 0.6), the flipped copy is read with the
same reader; if it reads clearly better (>= 0.6 and +0.2), the analysis runs
again on the flipped copy, says so, shows the photograph flipped, and
Pinpoint matches it flipped. `scripts/measure_mirror.py` reads every
photograph both ways and reports wrong flips and catches:

- benchmark (163, mostly little text): **0 wrongly flipped** at every
  threshold tried; 3 of the 4 with well-read text caught when mirrored;
- text set (`samples/text/`, being fetched): pending.

Commit once the text set has been measured, with the thresholds it supports.

## Measured coverage (Panoramax, 800 m radius)

Eiffel Tower 150 pictures / 9 sequences; Berlin Alexanderplatz 14; Nairobi 17
(nearest 723 m); Lagos none. `eval_pinpoint.py --panoramax` (11 landmarks) has
not been run yet: it must not run while another process searches Commons.

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

1. Finish the mirror test (above), then `eval_pinpoint.py --panoramax` on the
   11 landmarks once the text fetch has stopped searching Commons.
2. Wikidata -> Commons category for named landmarks (Moscow, Giza, Rio).
3. Walk terrain finer than z14 in Mapterhorn's lidar countries.
4. The rebuild and portable ZIP from the previous milestone, which now also
   need to include everything above.

The globe (`globe.js`) and its animation have not been touched.
