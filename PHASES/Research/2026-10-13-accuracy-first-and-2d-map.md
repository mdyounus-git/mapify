# Mapify, refocused: accuracy first, a real 2D map second

*13 October 2026. Written for the owner's redirect of the same day:*

> The core purpose of Mapify should be accurately identifying the real-world location of any
> uploaded image. Dedicate 90% of your research and development effort to improving geolocation
> accuracy. [...] The second priority is to provide a realistic 2D satellite mapping experience.

and his answers to three questions: the 3D walk, 3D buildings, ambience and cinematic effects
are **deleted**; the globe and every one of its animations stay **exactly as they are**;
zooming into a place shows a **flat 2D map with 2D building footprints**; for building-level
imagery worldwide he will create a **free-tier key** (Esri or Mapbox) and paste it into Mapify.

This builds on [`2026-10-09-free-geolocation-and-3d.md`](2026-10-09-free-geolocation-and-3d.md)
(sections 1-5 still stand for geolocation; section 6, the 3D world, is retired) and on what
was measured since. Labels: **Measured** = run here; **Reported** = a paper's own number.

---

## 1. What limits accuracy here -- measured

| Set | n | <=1 km | <=25 km | <=750 km | <=2500 km | Median |
|---|---|---|---|---|---|---|
| World benchmark (163, OCR off, Sept) | 163 | 17% | 39% | 58% | 74% | 317 km |
| India frame (48, OCR on, 12 Oct) | 46 | 13% | 26% | 50% | 80% | 705 km |

**The candidates usually contain the answer; the ranking does not choose it.** On the India
frame, a candidate within 25 km of the truth is somewhere in the twelve the pipeline weighs for
**50%** of photographs, and first for **26%**; within 200 km, 63% against 28%
(`python scripts/fusion_variants.py india`, Measured). The New Delhi skyline had a candidate 0 km
from the truth and answered Mumbai; Guwahati had one at 2 km and answered 1,140 km away.

What does *not* close that gap (re-fused on cached inputs, Measured, India): heavier script or
region weights, other evidence caps, and dividing GeoCLIP's modes by its gallery's density
(country level +2-4 points, street level halved). The evidence Mapify already has cannot tell
Mumbai from Delhi; new evidence must.

## 2. What the field does now -- checked

Retrieval and re-ranking is the recipe behind every recent lead:

- **Pinpoint** (arXiv 2606.04133, June 2026; Reported): candidates from three retrieval
  channels -- a learned image embedding, a GPS embedding and frozen SigLIP 2 -- over MP16-Pro
  (4.1 M Flickr photographs) and OpenStreetView-5M; a small Transformer re-ranker (4 layers,
  512 wide) scores each candidate from the query, the candidate's own embeddings and a
  "support token" of nearby gallery evidence. No vision-language model at inference.
  IM2GPS3k 20.5 / 47.4 / 63.5 / 79.0 / 90.2 % at 1 / 25 / 200 / 750 / 2500 km; YFCC4k 33.0 /
  44.4 / 57.5 / 71.8 / 84.5. **Code released for academic analysis; weights not released**;
  trained on one RTX 5090 with precomputed SigLIP 2 giant embeddings for every gallery image --
  not reproducible on this CPU.
- **GeoRanker** (NeurIPS 2025; Reported): a distance-aware re-ranker on Qwen2-VL-7B (LoRA),
  whose candidate pool includes GPT-4V's guesses at inference -- the paid part. 18.8 / 45.1 /
  61.5 / 76.3 / 89.3 on IM2GPS3k.
- **REVERSE** (2026), **G3**, **Img2Loc**: their leads come from GPT-4V or paid search APIs
  that upload the photograph (see the 9 Oct report, 3.1). Excluded by the owner's rules.

**The CPU-feasible version of the recipe, with licences that fit:**

1. **Candidates from independent sources** -- GeoCLIP's modes (have), the street-photo memory
   over OpenStreetView-5M (built, CC BY-SA 4.0, index ~1 day of CPU for 57 k images), PLONK's
   samples (built, MIT + DINOv2 Apache-2.0), read place names (have).
2. **A learned re-ranker, trained here**: per candidate, features the sources already give --
   GeoCLIP mass, how much of the memory's nearest photographs fall within reach, PLONK's
   density there, how many sources agree, each evidence channel's adjustment -- scored by a
   small model (gradient boosting, scikit-learn BSD-3) trained on OpenStreetView-5M photographs
   *not* in any benchmark: the 200,000 in the four shards on E: carry their true coordinates.
   Pairwise loss, as Pinpoint and GeoRanker use; a few thousand training photographs at
   ~20 s each is an overnight run.
3. **Kept honest**: trained on OSV-5M shards, measured on the world benchmark, the India frame
   and the text set, which it never sees; adopted only where it beats the current fusion.

Further levers, measured after the re-ranker: OCR at full resolution for phone photographs
(small text; the benchmark's 1,280 px thumbnails cannot show it), a Latin-script GeoNames
filter good enough for "Tambaram" without "ROAD" or "Bamboo", Pinpoint's second matcher.

## 3. The 2D map

**Imagery**, by what the owner can use without breaking his rules:

| Source | Resolution | Where | Licence / cost | Use |
|---|---|---|---|---|
| **Esri World Imagery** (ArcGIS Location Platform) | 0.3-0.5 m in most cities, coarser rurally | Worldwide | Free tier 2,000,000 basemap tiles a month, then $0.15 per 1,000; account + API key; attribution "Esri, Maxar, Earthstar Geographics, and the GIS User Community" | With his key: `ibasemaps-api.arcgis.com/.../World_Imagery/MapServer/tile/{z}/{y}/{x}?token=` (Esri's MapLibre guide) |
| **Mapbox Satellite** | similar (Maxar and others) | Worldwide | Raster Tiles API free allowance (stated on the live pricing page; per-tile billing outside Mapbox GL JS); token | With his token, as the alternative |
| National orthophotos | 0.1-1 m | FR, NL, CH, ES, JP, US | Open (Etalab, CC BY, PD) | Keyless, already served |
| Landsat WELD / HLS | 30 m | Worldwide | Public domain | Keyless fallback |
| Sentinel-2 cloudless (EOX) | 10 m | Worldwide | Now CC BY-NC-SA for non-commercial use, commercial licence otherwise | **Not used**: "free" is not licence-free |
| Google Maps / photorealistic tiles | -- | -- | Billing account | Excluded |

Neither provider documents permission to store tiles, so keyed tiles pass through Mapify's
server (the page may only talk to it) and are **not written to disk**; the key stays in the
local config file and never appears in the page.

**Roads, names, building footprints**: OpenStreetMap through OpenFreeMap vector tiles (ODbL,
no key, already used), and Google Open Buildings v3 footprints (CC BY 4.0) where OSM is thin
-- most of India, Africa and South-East Asia -- from the store Mapify already keeps. Drawn
**flat**: outlines and fills, no extrusion, no generated buildings.

**Rendering**: MapLibre GL JS (BSD-3-Clause), a 2D top-down map in the panel the 3D view
used. The globe hands over at its closest zoom exactly as before and takes back on zoom-out;
`globe.js` and its animation are not touched. The analysis pin, its uncertainty circle,
Pinpoint's spot and the candidates are drawn in the same Web Mercator coordinates as the tiles,
so they sit where the coordinates say.

**Limits, stated**: without a key, building-level imagery exists only in the six countries
above; elsewhere 30 m Landsat shows districts, not buildings. With a key, rural and remote
imagery can still be coarse or years old. Footprints are as complete as OSM and Open
Buildings are there.

## 4. Removed

The 3D street view (`ground3d.js`), the walk (`walk.js`), the ambience and weather sky
(`scene.js`), the 3D terrain (`terrain.js`), the weather-scene and events routes that only
they used, and their tests. History keeps them.

## 5. Order of work

1. Remove the 3D code; the 2D map with keyed and keyless imagery, roads, labels, footprints,
   the pin; a settings field for the key. Rebuild and install.
2. Build the street-photo memory (resumable, hidden) and a training set of OSV-5M photographs
   with their GeoCLIP, PLONK and memory candidates cached.
3. Train the re-ranker; measure on the three sets; adopt only what wins.
4. OCR resolution, the Latin GeoNames filter, Pinpoint's second matcher -- each measured.

## Sources

- Pinpoint: https://arxiv.org/abs/2606.04133
- GeoRanker: https://arxiv.org/abs/2505.13731
- REVERSE: https://arxiv.org/abs/2605.26861
- Street-level geolocalization with MLLMs and RAG: https://arxiv.org/abs/2509.01341
- ArcGIS Location Platform pricing: https://location.arcgis.com/pricing/
- Esri MapLibre raster basemaps (World Imagery with a token): https://developers.arcgis.com/maplibre-gl-js/maps/display-multiple-basemap-layers/
- ArcGIS Static Basemap Tiles: https://developers.arcgis.com/rest/static-basemap-tiles/
- Mapbox pricing: https://www.mapbox.com/pricing ; Raster Tiles API: https://docs.mapbox.com/api/maps/raster-tiles/ ; Mapbox in MapLibre: https://docs.mapbox.com/help/dive-deeper/mapbox-in-maplibre/
- EOxCloudless licence: https://cloudless.eox.at/documentation/license
