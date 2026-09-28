# The 153 techniques, against what actually exists

A pipeline of 153 named techniques was specified. Before building any of it,
this is an honest audit of each one against the code, because roughly a third
already exists, several were built and **deliberately deleted** after
measurement, and a handful cannot be done at all under this project's
licensing rules.

Building from a guess about what is here would mean reimplementing working
code, re-adding measured harm, and promising capabilities that no free
licence permits.

Status key:

| | meaning |
|---|---|
| **done** | implemented, tested, and in the shipped pipeline |
| **part** | some of it exists; the rest is real work |
| **todo** | not built, and buildable |
| **measured-out** | built, measured on the 163-sample benchmark, and removed because it made results worse or did nothing |
| **blocked** | cannot be done under the licensing or privacy rules; see the note |

---

## Their Phase 1 — Metadata & image forensics

| # | Technique | Status | Where / why |
|---|---|---|---|
| 1 | EXIF / metadata extraction | **done** | `mapify/exif.py` |
| 2 | GPS coordinates | **done** | `exif.py`, with tamper signals |
| 3 | Capture timestamp | **done** | `captured_at`, and impossible-offset checks |
| 4 | Camera / device identification | **done** | make, model, lens, software |
| 5 | Resolution & pixel inspection | **part** | dimensions and format recorded; no pixel forensics |
| 6 | OCR / text extraction | **done** | EasyOCR, script-adaptive reader selection (Phase 10) |
| 7 | Signboards & billboards | **done** | Phase 21 — 0.00 km on a legible board |
| 8 | Street names / addresses | **done** | Phase 21, Nominatim forward geocoding |
| 9 | Business names & logos | **part** | names yes, via OCR; no logo recognition |
| 10 | Road signs & traffic signs | **measured-out** | Phase 22: never once cleared its threshold — **0 of 163 samples moved** |
| 11 | License plates | **part** | plate *class* scored; plates are not read |
| 12 | Language & script identification | **done** | `evidence/script_detect.py` |
| 13 | Architecture analysis | **done** | `ARCHITECTURE` prompt group |
| 14 | Building shapes & facades | **part** | covered coarsely by `ARCHITECTURE` |
| 15 | Windows, doors, structure | **measured-out** | Phase 22 roof group: median error 342 → 368 km |

## Their Phase 2 — Scene & infrastructure

| # | Technique | Status | Where / why |
|---|---|---|---|
| 16 | Road design & markings | **done** | `ROAD` |
| 17 | Sidewalk / pavement | **todo** | |
| 18 | Utility poles & electrical | **done** | `UTILITY` |
| 19 | Power-line configuration | **part** | inside `UTILITY` |
| 20 | Street furniture | **measured-out** | Phase 22: median 342 → 368 km |
| 21 | Traffic-light design | **todo** | |
| 22 | Vehicle models | **done** | `VEHICLES` |
| 23 | Plate-format analysis | **done** | `PLATE` against a 239-country table |
| 24 | Public transport | **part** | inside `VEHICLES` |
| 25 | Landmark identification | **part** | implicit in GeoCLIP; 82% of landmarks land within 25 km |
| 26 | Mountain / skyline matching | **todo** | needs DEM horizon synthesis |
| 27 | Coastline / river / lake | **part** | inside `TERRAIN` |
| 28 | Vegetation & tree species | **done** | `BIOME` |
| 29 | Terrain / geology | **done** | `TERRAIN` |
| 30 | Soil & ground | **todo** | |
| 31 | Weather / atmospheric | **part** | `SNOW`; no general weather read |
| 32 | Cloud & visibility | **todo** | |

**A warning this table earns.** Four of the techniques in these two phases were
built exactly as specified, with real geographic constraints attached, and
every one was inert or harmful. The reason generalises: they are more opinions
from the *same image encoder* about the *same embedding*, and the fusion
combines evidence in log space as though it were independent. Stacking
correlated opinions does not sharpen an estimate, it amplifies what that
encoder already had wrong. Adding the remaining prompt-group techniques from
these phases should be expected to do the same, and each must be measured.

## Their Phase 3 — Solar, shadow & astronomical

| # | Technique | Status |
|---|---|---|
| 33–42 | sun position, shadow direction and length, solar elevation/azimuth, local time, sunrise/sunset constraints, season, moon, star field, artificial light | **todo — all of it** |

**This is the largest genuinely new capability in the whole specification**, and
the one that adds an output dimension the tool does not have: *time and date*.
It is also independent evidence in the sense Phase 22 established is the only
kind that helps — a shadow angle is a physical measurement, not another
opinion from the image encoder.

Some groundwork exists: a NOAA subsolar-point solver and a lunar ephemeris,
both already written for the globe renderer, in JavaScript. They need a Python
counterpart and, much harder, shadow geometry recovered from pixels.

## Their Phase 4 — Computer vision & geometric

| # | Technique | Status | Note |
|---|---|---|---|
| 43 | Vanishing points | **todo** | |
| 44 | Camera orientation | **todo** | |
| 45 | Focal length / lens | **part** | read from EXIF, not used |
| 46 | Geometric landmark matching | **todo** | |
| 47 | Satellite-image comparison | **todo** | newly possible: Phase 24 brought in 25 m/px NASA imagery |
| 48 | Street-view comparison | **blocked** | Google Street View is not licensed for this. Mapillary is openly licensed and is the honest route; it is its own phase |
| 49 | Map / GIS cross-reference | **done** | Natural Earth, Nominatim, Overpass |
| 50 | Reverse-image search | **blocked** | every engine with an API charges or forbids automated use. "Free" is not "licence-free" |
| 51 | Source / repost tracing | **blocked** | same |
| 52 | Video frame extraction | **todo** | straightforward |
| 53 | Adjacent-frame comparison | **todo** | |
| 54 | Manipulation / compositing detection | **todo** | |
| 55 | Compression & editing artifacts | **todo** | |
| 56 | AI-generation detection | **todo, with a caveat** | detectors are unreliable and asymmetric. A false "this is real" is worse than no answer, so this can only ever report a signal, never a verdict |

## Their Phase 5 — Historical & temporal correlation

| # | Technique | Status |
|---|---|---|
| 57–63 | historical imagery, construction state, seasonal vegetation, historical signage, weather history, solar verification, shadow verification | **todo** |
| 64 | Multiple-clue correlation | **done** — the fusion engine |
| 65 | Candidate elimination | **done** |
| 66 | Constraint intersection / convergence | **done** |
| 67 | Final lat/lon | **done** |
| 68 | Time-window estimation | **todo** — depends on Phase 3 |
| 69 | Date estimation | **todo** — depends on Phase 3 |
| 70 | Confidence scoring | **done** — fitted on a held-out split, Phase 13 and 26 |
| 71 | Alternative locations | **done** — the candidate rail |
| 72 | Evidence-chain documentation | **done** — every adjustment carries its reason; the HTML export carries the chain |

## Their Phase 6 — Advanced pixel & vision

73–90 (tiling/ROI, colour and texture, detection and segmentation, fine-grained
recognition, enhancement, low-light, reflections, glass and water
reconstruction, lens distortion, rolling shutter, depth, relative distance,
object height, shadow–object matching, multiple shadow sources, occlusion, sun
obstruction): **all todo.** 87–90 are the useful half, because they feed the
shadow geometry that Phase 3 needs.

## Their Phase 7 — Advanced geospatial & astronomical

| # | Technique | Status |
|---|---|---|
| 91–92 | Solar-path simulation, ephemeris matching | **todo** — core of chronolocation |
| 93 | Timezone / UTC offset | **done** — `geo/timezone.py`, and the timezone→location path added for the globe's opening view |
| 94 | Date narrowing from astronomy | **todo** |
| 95–97 | Weather-station, cloud-history, air-quality correlation | **todo** — Open-Meteo's historical API is free and openly licensed, so this is reachable |
| 98 | Unique environmental features | **todo** |
| 99–101 | Street-network, intersection, building-footprint matching | **todo** — Overpass is already wired in, so reachable |
| 102 | Facade / window-pattern matching | **todo** |
| 103 | Utility-infrastructure database | **todo** |
| 104–107 | Traffic-sign, road-marking, kerb, streetlight standards | **partly measured-out** — see 10 and 20 |
| 108 | Manhole / utility covers | **todo** |
| 109 | Bus stop / transit infrastructure | **todo** |
| 110 | Telecom / ISP clues | **todo** |

## Their Phase 8 — Regional & geographic intelligence

| # | Technique | Status |
|---|---|---|
| 111 | Vehicle registration region | **part** |
| 112 | Driving side | **done** — the single strongest coarse discriminator, `TRAFFIC` |
| 113 | Lane orientation | **part** |
| 114–118 | Construction materials, facade paint, cultural clues, retail-chain and brand distribution | **todo** |
| 119–124 | Phone numbers, domains, currency, date format, units, postcodes | **todo — and the best-value group left after Phase 3.** All six are *text*, read out of OCR rather than guessed from pixels, which is exactly the independent evidence that has been shown to work here |
| 125 | Business-directory cross-reference | **part** — Nominatim forward search does much of this |
| 126 | OpenStreetMap feature matching | **done** — Overpass landmarks, Nominatim search |
| 127–128 | DEM / elevation-profile matching | **todo** — GEBCO is already bundled for the globe |
| 129 | Road-bearing / azimuth matching | **todo** |
| 130–131 | Viewpoint matching, geometric re-projection | **todo** |

## Their Phase 9 — Multi-source & provenance

| # | Technique | Status |
|---|---|---|
| 132 | Multi-image correlation | **done** — Phase 23, product of likelihoods over the full distribution |
| 133 | Cross-source matching | **blocked** — needs reverse image search |
| 134 | Near-duplicate detection | **done** — Phase 23, on the CLIP embedding |
| 135–137 | Original source, upload timestamps, edit history | **blocked / part** — EXIF software tags are read; the rest needs services that forbid it |
| 138 | JPEG quantization signatures | **todo** |
| 139 | Resampling artifacts | **todo** |
| 140 | Splice detection | **todo** |
| 141 | AI-generated region detection | **todo, same caveat as 56** |
| 142 | Metadata vs visual consistency | **done** — Phase 22: corroborated / rejected / unresolved, with reasons |

## Their Phase 10 — Evidence fusion & final geolocation

| # | Technique | Status |
|---|---|---|
| 143 | Clue reliability weighting | **done** — and the weights were refit on a held-out split in Phase 26; the hand-chosen ones survived |
| 144 | Independent cross-validation | **done** |
| 145 | Contradiction detection | **done** |
| 146 | Candidate ranking | **done** |
| 147 | Error radius | **done** |
| 148 | Coordinate confidence interval | **done** |
| 149 | Time uncertainty | **todo** — depends on Phase 3 |
| 150 | Date uncertainty | **todo** — depends on Phase 3 |
| 151 | Annotated evidence map | **todo** — the report has the evidence, not the annotated image |
| 152 | Reproducibility / provenance | **done** — attribution, tool and source list travel with every export |
| 153 | Orchestration | **done** — `pipeline.py` |

---

## Where that leaves it

```
done            47
part            18
measured-out     4
blocked          8
todo            76
```

## What to build, in order, and why

**1. Chronolocation (their Phase 3, plus 68, 69, 91, 92, 94, 149, 150).**
The largest missing capability, an output dimension the tool does not have,
and independent evidence of exactly the kind Phase 22 proved is the only kind
that helps. A shadow is physics, not another opinion from the image encoder.

**2. Structured text extraction (their 119–124).** Phone numbers, postcodes,
currency, date formats, units, domains — all read out of OCR text. Cheap,
independent, and the same class of evidence as Phase 21's address reading,
which was worth 0.00 km on a legible board.

**3. Image forensics (54, 55, 138, 139, 140).** Self-contained, needs no
network, and answers a question the tool currently cannot: *has this file been
edited?*

**4. The annotated evidence map (151).** The report holds the evidence already;
this puts it back on the pixels it came from.

**Not next, on the evidence:** more prompt groups. Four were measured and all
four were inert or harmful, for a reason that applies to the rest of them.

## The two output fields that will stay empty for now

The specification asks for **Time** and **Date** on every image. Until
chronolocation exists those fields will say so, and after it exists they will
carry an uncertainty interval rather than a value — a shadow gives a time of
day to a band of minutes only when the sun is visible, the ground is level and
an object of known verticality is in frame. Most photographs do not qualify,
and for those the honest output is "not determinable", not a guess.
