# Mapify: finding the place, pinpointing it, and walking it — what free technology can really do

Research for the owner of Mapify, 8–9 October 2026. Nothing in `E:\mapify` was changed (its git
working tree is clean). Every number below is either something I tested, something I read in the
project's own code, or a figure a paper or README reports — and each is labelled so you can tell
which.

**Labels used throughout**

- **Tested** — I ran the query, download listing, timing or browser check myself on 8–9 Oct 2026
  and saw the result.
- **Read** — I read it in the project's code, config, model card, documentation or licence file.
- **Reported** — a number from a paper or README that I could not reproduce here. Treat it as a
  claim until your own benchmark confirms it.

**Licence tiers** (your rules: no paid APIs, no subscriptions, "free does not mean licence-free")

- **A** — permissive or attribution-only (MIT, Apache-2.0, BSD, CC0, CC BY, public domain,
  CDLA-Permissive, ODbL with its usual duties). Can be a default and can go in the portable ZIP.
- **B** — free but restricted: non-commercial (NC), share-alike that would bind redistribution,
  or needing your own free token. Opt-in only, downloaded by your own action, never bundled,
  switched off by one `commercial_use = true` setting, and listed in `ATTRIBUTION.md`.
- **X** — not usable under your rules: paid, research-only, no licence at all, needs an account
  you have not chosen to make, or would send the photograph to a third party.

---

## 1. The answer in brief

1. **Keep GeoCLIP as the core — it does what it claims.** Its paper reports 14.1 % within 1 km and
   34.5 % within 25 km on Im2GPS3k; your own 163-photo benchmark measured 17 % and 39 %. No free,
   local model has been *shown* to beat it by a wide margin on ordinary photographs; the most
   promising one (PLONK, 4.2.2) is reported ahead on YFCC4k but has not been checked on yours.
2. **The large gains in recent papers are not free.** G3, Img2Loc, GeoRanker and REVERSE get
   their lead from GPT-4V or from paid search (REVERSE calls the Oxylabs Google Lens API and
   Tavily and uploads image crops). With an open model in place of GPT-4V, G3's own table puts it
   level with GeoCLIP. Untuned open vision-language models look strong on Im2GPS3k and fall apart
   on ordinary photos: Qwen3-VL-8B scores 37.2 % within 25 km on Im2GPS3k but 10.8 % on YFCC4k,
   where GeoCLIP scores 19.3 % (Reported, REVERSE paper) — the pattern of a model that memorised
   famous places, the same split your benchmark shows between landmarks (82 %) and streets (34 %).
3. **What should move your weak numbers (streets 34 %, Africa 12 % within 25 km), in order:**
   - a **retrieval memory** over OpenStreetView-5M (4.9 M street-level training images, 225
     countries, CC BY-SA 4.0) using the CLIP ViT-L/14 embedding you already compute. On the
     OSV-5M street-level test set, nearest-neighbour retrieval with exactly that backbone found
     the right *city* 19.3 % of the time. The authors' best trained model managed 5.9 % and PLONK
     15.4 %, and at country level retrieval scored 72.8 % against 68.0 % (Reported, two papers;
     4.2.1). That is the largest lead at the finest level any free method shows on street
     imagery. How much it adds on your own benchmark is unmeasured. It is also the store that
     continual learning writes into. One day of CPU builds a 200,000-image starter index (Tested
     timing);
   - **PLONK** as an independent second locator (MIT; DINOv2 encoder, Apache-2.0). It returns a
     probability density and an exact log-likelihood for any coordinate, so it can score the
     candidates you already have instead of being averaged with them. Use the YFCC checkpoint:
     the OSV-5M checkpoint silently loads StreetCLIP, which is CC BY-NC;
   - an **offline GeoNames gazetteer** (CC BY 4.0, 422 MB) with language identification, so read
     text resolves without Nominatim;
   - **species evidence** (BioCLIP 2 + GBIF ranges) for rural and nature scenes, where Africa
     and South America are lost today.
4. **For the exact spot, coverage is the limit, not the matcher.** Your four Pinpoint failures
   never rose above 16 inliers — chance level — because no reference photo showed the same view.
   Add Panoramax (no key, carries heading and field of view; dense in France and Germany, a few
   sequences in some cities elsewhere — Nairobi, Dakar, Bogotá, Bengaluru, Tokyo — none in many
   others: Tested on 15 cities), Commons photos reached through Wikidata, Mapillary's
   SfM-corrected camera poses, and a LightGlue second pass only on SIFT's near-misses with its own
   calibrated threshold.
5. **3D: there is no free photorealistic 3D of the whole world.** Google's photorealistic tiles
   are a Google Maps Platform API (key plus billing account — excluded by your rules) and Cesium
   ion needs an account. What is free and real:
   - terrain at 0.25–2 m in about 30 countries (most of Europe, the US, Canada, Japan,
     Australia, New Zealand) and 30 m everywhere else, including almost all of Africa, South
     America and Asia (Mapterhorn);
   - sub-metre orthophotos in France, the Netherlands, Switzerland, Spain, Japan and the US, and
     10 m Sentinel-2 everywhere else;
   - building heights (Microsoft footprints; Google Open Buildings 2.5D for Africa, South Asia and
     Latin America, adding heights to the Open Buildings v3 footprints you already draw) and 3D
     city models for Japan and the Netherlands through deck.gl's `Tile3DLayer`;
   - real tree heights (Meta canopy height v2), the seafloor (GEBCO 2026) and the sea state
     (Open-Meteo Marine).

   **Two traps decide whether any of it appears on screen** (Section 6.1). The page's own
   security policy lets the browser talk only to Mapify's server, so every new source must be
   fetched *by the server*. City models are in ellipsoid heights, 36–49 m off your sea-level
   terrain in Japan, the Netherlands and Switzerland, so they must be shifted down or they float.
   Both are tested, and both have a fix below.

   A photo-real walk exists only where street imagery exists, and belongs in its own panel. The
   globe keeps doing the world-scale mapping exactly as now; the extra detail appears in the
   ground view it already hands over to.
6. **Not knowable from free data:** the vehicles, people and ships present at the moment of the
   photograph (except historical ship tracks in US waters), photographic facades
   outside a handful of cities, and imagery finer than 10 m outside the countries above.
7. **The globe and its animation stay exactly as they are.** Every addition lands in the
   analysis pipeline, the server, the deck.gl ground view, or a new panel. Section 6.5 is the
   contract.

---

## 2. The cases that break geolocation — designed for first

Each row is an input that defeats a naive system, what goes wrong, and what handles it. The
technologies named here are the ones evaluated in Sections 3–4. (The cases that break the 3D view
are in Section 6.1.)

| # | Input | What goes wrong | What handles it | What Mapify should say |
|---|---|---|---|---|
| 1 | Open sea, sky, featureless desert, snowfield, fog | Every locator spreads thin; any pin is invented | Existing gate and locator mass; add PLONK's density spread as a second "can this be located at all" measure (its paper uses the density to quantify localisability) | "Cannot be placed"; at most a latitude band if the sun and a trusted time are available |
| 2 | Interior, close-up, food, document | Style of a room is not a location | Existing gate; OCR or the optional reader may still find a place name, which then goes through the gazetteer | Coordinates only from verified text, never from décor |
| 3 | Mirrored image (selfie camera, flipped repost) | Driving side and script read backwards, which is evidence *against* the truth | Run OCR on the image and its mirror; if the mirror reads far better, analyse the mirrored copy and say so | "This image appears to be mirrored" |
| 4 | Replica landmark (Eiffel Tower in Las Vegas or Hangzhou, Statue of Liberty copies) | Recognition picks the original | Pinpoint must search *every* candidate cluster, and the surroundings (driving side, script, vegetation) must agree before "exact" | Never "exact" from recognition alone |
| 5 | Landmark-heavy benchmarks | Inflate every claim (VLMs memorise landmarks) | Judge everything on your street-heavy benchmark plus an OSV-5M test sample, reported per scene type | Report accuracy per scene, as the README already does |
| 6 | Tropical Africa read as Latin America | Systematic model bias, measured | Retrieval memory with African street imagery; language ID (Swahili, Amharic, Hausa, French/Portuguese mixes); species ranges; GeoNames | Show both continents until independent evidence separates them |
| 7 | Text in a language spoken in many countries; common names ("San José", "Victoria") | One read name matches dozens of places | Weigh GeoNames matches by population, feature type and agreement with the locator density; a single city match stays weak (your `SPECIFICITY_WEIGHT`) | List the alternatives |
| 8 | Global brands, foreign cars or plates | Real text, wrong country | Brand facts only where country-specific (`regional.py`); plates stay weak evidence | Weight zero unless measured |
| 9 | Historical photo | Buildings changed; current references do not match | Commons holds historical photos; OSM is current; date the reference used | "The place may have changed since" |
| 10 | Night photo | CLIP and matchers both weaken | Lower confidence automatically; pinpoint against night references only if present | Honest band |
| 11 | 360° panorama (equirectangular) | Distortion; one wide view treated as one crop | Split into 4–8 perspective views (`py360convert`, MIT). They are one moment, not independent photos — combine them by best/mean, not by your product of likelihoods | Treat as one observation |
| 12 | Aerial, drone or airplane-window photo | Ground-level models fail | Detect a downward camera (GeoCalib pitch); match against orthophotos near the candidates (sub-metre only in the countries in Section 6.2, OpenAerialMap drone imagery where it exists, 10 m Sentinel-2 elsewhere) | Refuse when no imagery fine enough exists |
| 13 | HEIC (the iPhone default) | Rejected today ("Use JPG, PNG or WEBP") | `pi-heif` (decoder only, wheels LGPL-3.0): `from pi_heif import register_heif_opener; register_heif_opener()` before decoding, and add `HEIF` to `ACCEPTED_FORMATS`. **Not** `pillow-heif`: its wheels are GPL-2.0 because they bundle the x265 encoder (Read, its `LICENSES_bundled.txt`) | Accept HEIC |
| 14 | Re-encoded social-media copy | EXIF gone, resolution low | Models cope; Pinpoint needs pixels | Ask for the original when Pinpoint is wanted |
| 15 | AI-generated or composited image | Can look like a real place | Read C2PA Content Credentials when present (`c2pa-python`, MIT/Apache-2.0); do **not** gate on an "AI detector" — such classifiers are trained on particular generators and their scores mean little on new ones | "Provenance unknown" or "C2PA says: made with …" |
| 16 | Photo of a screen, poster or print | The pictured place is not where the camera was | Existing poster/screenshot gate | Say which place is *depicted* vs where it was *taken* |
| 17 | Season or day/night differs from every reference | SIFT fails across illumination | LightGlue/RoMa second pass; record the reference date | Lower the claim, not the threshold |
| 18 | Coastal photo whose candidate falls offshore | A pin in the water | Do not snap to land; keep the uncertainty; the 3D view opens an ocean scene if the point is water | Show the radius |
| 19 | Arctic, Antarctic, antimeridian | Web-Mercator tiles stop at ±85.05°; clusters split across ±180° | Haversine clustering; GIBS polar projections for imagery near the poles | Say when imagery is unavailable |
| 20 | Faked EXIF GPS | Already handled — kept out of the estimate | Keep as is | As today |
| 21 | Mountains with no buildings | Few semantic clues | Skyline against terrain, but only to *verify* candidates within ~20 km (Section 4.2.5) | Verified or not, never guessed |
| 22 | Offline | Pinpoint, new 3D areas and weather need the network | Retrieval memory, PLONK and GeoNames all run offline; 3D areas can be pre-fetched as PMTiles extracts and tileset copies on E: | Status bar states what is offline |
| 23 | CPU-only PC | Heavy models take minutes | Everything heavy is opt-in, cancellable and shows its time estimate | Never blocks the UI |

---

## 3. Claims checked against reality

You asked not to trust confident descriptions. For each project I looked at the licence file or
model card, the code where it mattered, the activity (GitHub stars, forks, commits on the default
branch, contributors, last commit — all read via the GitHub API on 8 Oct 2026), and whether the
headline number survives a harder benchmark or a free setup.

### 3.1 Geolocation systems

| System | Claim | What checking found | Licence | Activity | Verdict |
|---|---|---|---|---|---|
| **GeoCLIP** (VicenteVivan/geo-clip) — your locator | State of the art on Im2GPS3k and others | Paper: 14.1/34.5/50.7/69.7/83.8 % at 1/25/200/750/2500 km. Your bench reproduces the street and city figures (17/39 %). Training code is in the repo (`geoclip/train/train.py`, Read); the full evaluation artefact is not released (issue #18, open). **New this week:** PyPI 1.2.2 and 1.2.3 (6–7 Oct 2026) replace the loader with a Hub-based `from_pretrained`; you have 1.2.1 installed and `requirements.txt` says `geoclip>=1.2`, so the next setup would silently take the new loader | MIT | 404★, 50 forks, 45 commits, 2 contributors, last commit 2026-10-07 | **Keep (A)**; pin `geoclip==1.2.1` until 1.2.3 passes your benchmark and offline test |
| **PIGEON / PIGEOTTO** | Planet-scale state of the art | README: "Geocell shapes and coordinates, training and validation datasets, and model weights are not provided". Trained on Street View | No weights | 282★, 27 forks, 8 commits | **X** — nothing to run |
| **G3** (NeurIPS 2024) | Beats GeoCLIP everywhere | Uses GPT-4V. Its own open-model variant (LLaVA-Next-LLaMA3-8B) scores 14.3/35.9/49.4/66.9/81.8 on Im2GPS3k — level with GeoCLIP, worse beyond 25 km (Reported) | Apache-2.0 code | 46★, 15 commits, 1 contributor | **X** paid; no gain open |
| **Img2Loc** (SIGIR 2024) | Beats GeoCLIP without training | GPT-4V. With LLaVA: 10.2/29.1/39.5/56.4/71.1 — *below* GeoCLIP (Reported, G3 paper). No official code found | — | — | **X** |
| **GeoRanker** (2025) | 18.8/45.1/61.5/76.3/89.3 on Im2GPS3k | Its "generated candidates" come from GPT-4V at inference; trained with LoRA on Qwen2-VL-7B on 4×L40S | Apache-2.0 | 23★, 1 fork, 9 commits, 1 contributor | **X** as published |
| **REVERSE** (2026) | 4B model rivals much larger ones | Calls the Oxylabs Google Lens API and the Tavily API, uploading image crops to cloud storage. YFCC4k 14.1/27.5 at 1/25 km, below G3 | Paper CC BY-SA; code licence not stated | — | **X** paid, sends the image out |
| **GeoReasoner** (ICML 2024) | Beats other VLMs by 25–38 % | Repository has **no licence** (all rights reserved by default); fine-tuned on Google Street View images | None | 78★, 3 forks, 20 commits, 1 contributor | **X** |
| **Qwen3-VL-8B**, untuned | General VLM | Im2GPS3k 10.5/37.2/55.3/72.8/84.9 but YFCC4k 3.3/10.8/19.6/31.1/40.4 vs GeoCLIP 9.6/19.3/32.6/55.0/74.7 (Reported, REVERSE paper): memorised landmarks, weak on ordinary photos | Apache-2.0 (2B/4B/8B, Read on the model cards) | 20,042★ | **A as a clue reader only** (4.2.7) |
| **StreetCLIP** | Strong street-level zero-shot | Model card licence | **CC BY-NC 4.0** | 13.5 k downloads/month | **B** |
| **PLONK** (nicolas-dufour/plonk, CVPR 2025) | New state of the art on OSV-5M, YFCC, iNat | Trained on 48 M geotagged YFCC images. YFCC4k 23.7/36.4/54.5/73.6 at 25/200/750/2500 km vs GeoCLIP 19.3/32.6/55.0/74.7; the same model trained 10× longer 33.5/45.3/61.1/77.7 (Reported). On OSV-5M: country 76.2, region 44.2, **city 15.4 % — below plain CLIP retrieval's 19.3–19.9 %** (Reported, its own Table 1). No 1 km figure (issue #3 asks); no Im2GPS3k result. Code read: `PLONK_YFCC` and `PLONK_iNaturalist` use DINOv2 ViT-L/14-reg; **`PLONK_OSV_5M` loads `geolocal/StreetCLIP`** — so its MIT label does not make it usable | MIT (+ DINOv2 Apache-2.0) | 304★, 27 forks, 14 commits, 1 contributor, last commit 2025-09-29 | **A** for YFCC/iNat checkpoints; measure first |
| **OSV-5M baseline** (osv5m/baseline) | Country 68.0 %, city 5.9 % on OSV-5M | Config read: backbone is CLIP ViT-L/14 *DataComp* (open), not StreetCLIP. (The PLONK paper reprints this model's city score as 15.9 % — one of the two papers has a typo) | MIT | 189★, 30 commits, 4 contributors, last commit 2024-05-06 | **A**, but weaker than retrieval at city level |
| **OpenStreetView-5M** dataset | Open street-level benchmark | 4,894,685 train / 210,122 test, 512 px tall. 98 train shards of ~2.52 GB each (Tested listing). Test images lie at least 1 km from every training image (appendix; the main text's "11km" reads as a typo) | **CC BY-SA 4.0** | — | **A** (local index; share-alike if ever redistributed) |

### 3.2 Matching, place recognition, camera and scene models

| Project | What checking found | Licence | Activity | Verdict |
|---|---|---|---|---|
| **LightGlue** (cvg) | 20 FPS at 512 keypoints on an i7 CPU (Reported). Works with DISK (Apache-2.0) and ALIKED (BSD-3); **SuperPoint "follows a different, restrictive license"** (README). In `kornia` 0.8.3 as `LightGlueMatcher` | Apache-2.0 | 4,817★, 40 commits, 7 contributors | **A** with DISK/ALIKED only |
| **RoMa** | Dense matcher, robust to big viewpoint and lighting change; heavy (560–864 px internal); Tiny RoMa (XFeat-based) is the light variant | MIT (+DINOv2 Apache-2.0) | 1,312★, 77 commits, 12 contributors | **A**, last few pairs only |
| **XFeat** | Light features for CPU | Apache-2.0 | 1,743★, 30 commits, last commit 2025-01 | A (fallback) |
| **MASt3R / DUSt3R** | 3D-grounded matching | Code **CC BY-NC-SA 4.0**; checkpoints also bound by training-set licences, one "very restrictive" (README) | 3,120★ / 7,335★ | **X** |
| **VGGT** | Feed-forward 3D reconstruction | 1B weights **CC BY-NC 4.0**; the commercial variant is gated behind a manual approval | 14,462★ | **X/B** |
| **MegaLoc** | "SOTA on most VPR datasets" — no numbers in README (paper 2502.17237); 8,448-dimensional descriptor | MIT | 305★, 36 commits, 4 contributors, last commit 2026-07-28 | A — useful only over a *local* image index |
| **MixVPR**, **Sample4Geo** | Popular in papers | **No licence** on either repo; Sample4Geo's datasets are built on Google imagery | — | 337★ / 16 commits; 168★ / 9 commits | **X** |
| **SALAD** | VPR | GPL-3.0 — cannot be bundled into an MIT ZIP | 382★, 17 commits | X for bundling |
| **OrienterNet** (Meta) | Localises a photo against OpenStreetMap: position within 1/3/5 m for 14.4/48.7/61.7 % of single Mapillary images (Reported) | **CC BY-NC** (code and weights) | 581★, 34 commits, 2 contributors, last commit 2024-09-07 | **B** |
| **GeoCalib** (cvg) | Roll, pitch, field of view and distortion from one image; CPU supported | Code Apache-2.0, weights CC BY 4.0 | 932★, 26 commits, 5 contributors | **A** |
| **MoGe-2** (Microsoft) | Metric depth, point map, normals and field of view from one image | Code MIT (DINOv2 part Apache-2.0); weights MIT on the model card | 3,016★, 60 commits, 11 contributors | **A** |
| **Depth Anything V2** | Relative depth | **Small: Apache-2.0; Base and Large: CC BY-NC 4.0** (model cards) | 8,913★ | A (Small only) |
| **Depth Pro** (Apple) | Metric depth | "Apple Machine Learning Research Model … exclusively for Research Purposes", excludes "product development" (licence text, Read) | — | **X** |
| **BioCLIP 2** | Species-level recognition of organisms | MIT | 106 k downloads/month; repo 469 commits, 42 contributors | **A** |
| **EasyOCR** (yours) / **RapidOCR** (PP-OCR models on ONNX Runtime) | Scene-text OCR | Both Apache-2.0 | EasyOCR 30,051★, 129 contributors, last commit 2025-12-05; RapidOCR 8,068★, 1,551 commits, 51 contributors, pushed 2026-10-08 | **A** — compare on your text set |
| **DINOv2** / **DINOv3** | Backbones | DINOv2 Apache-2.0; DINOv3 custom licence, gated by manual approval | 13,403★ / 11,499★ | A / X |
| **Qwen2.5-VL-3B**, **Gemma 3** | Small VLMs | `qwen-research` (non-commercial); Gemma terms, gated | — | X — use Qwen3-VL 2B/4B/8B or Qwen2.5-VL-7B (Apache-2.0) |
| **YOLO11** (Ultralytics) | Object detection | **AGPL-3.0** | — | X — use OWLv2, Grounding DINO or RT-DETRv2 (all Apache-2.0) |
| **SegFormer**, **Mask2Former-ADE** | Sky/scene segmentation | Licence "other" (NVIDIA / Meta terms) | — | X — use OneFormer ADE20K Swin-L (MIT) or DPT-large-ADE (Apache-2.0) |
| fastText language ID (HF copy) | Language identification | **CC BY-NC 4.0** | — | X — use GlotLID (Apache-2.0 plus notices) |

### 3.3 Map, terrain, imagery and 3D data

| Source | What checking found | Licence | Verdict |
|---|---|---|---|
| **Mapterhorn** terrain | Terrarium-encoded WebP, 512 px tiles; planet PMTiles z0–12 plus regional z13–17; 151 sources (Tested `attribution.json`): Copernicus GLO-30 as the world base; 0.25–2 m national terrain models in about 30 countries — 21 European ones (0.25 m in parts of Germany and Zürich, 0.4 m Denmark), the US (1 m), Canada (2 m), Japan (1 m), Australia (2 m), New Zealand (1 m); in Africa only Rwanda (10 m). No public usage policy found → fetch once, keep | Code BSD-3; data per source, mostly CC BY 4.0 / US public domain / Etalab 2.0; GSI sources carry GSI terms | **A**; 409★, 208 commits, 9 contributors, pushed 2026-10-08 |
| **Copernicus GLO-30** | It is a *surface* model (roofs and canopy included) | Copernicus free licence, credit DLR/Airbus | A — see the seating case in 6.1 |
| **Terrain Tiles on AWS** (your current terrain) | Also surface-biased in cities: it reads **13 m at Dam Square, Amsterdam** (Tested, z15 Terrarium); the old centre of Amsterdam lies within a few metres of sea level, so roughly 10 m of that is likely roofs | As in your ATTRIBUTION.md | Keep as fallback; prefer lidar terrain where it exists |
| **FABDEM** (bare-earth GLO-30) | Removes trees and buildings | **CC BY-NC-SA 4.0** | B |
| **Microsoft Global ML Building Footprints** | 1.4 B footprints; heights where estimated (−1 otherwise; 174 M had heights in 2023); refreshed 13 Aug 2026; 71 commits, 16 contributors | **CDLA-Permissive 2.0** | **A** |
| **Google Open Buildings** | You already draw **v3 footprints** (no heights) from the Source Cooperative FlatGeobuf (Read, `geodata/open_buildings.py`). The **2.5D Temporal** product adds building presence and height rasters (0–100 m), 4 m effective, yearly 2016–2023, Africa, South and South-East Asia, Latin America and the Caribbean. The catalogue page points non-Earth-Engine users to Google's download notebook; check that it runs without signing in before relying on it | CC BY 4.0 or ODbL, your choice | **A** — the Africa heights you lack |
| **GlobalBuildingAtlas** (TUM) | 2.75 B footprints, 2.68 B LoD1 models with heights, RMSE 1.5–8.9 m by continent (Reported) | **CC BY-NC 4.0**; code "MIT with Commons Clause" | B (2,241★, 46 commits, 5 contributors) |
| **Overture Maps buildings** | Conflates OSM, Microsoft, Google, Esri; heights where available; GeoParquet on S3, no account; `overturemaps` CLI (MIT) | ODbL (OSM-derived) | A |
| **PLATEAU** (Japan) | Textured LoD2 3D city models, 3D Tiles is an official format, 250+ cities open; Tokyo's geoid height is 36.4 m (Tested), so the datum shift of 6.3 applies | CC BY 4.0 / ODbL / ODC-BY (choose) | **A** |
| **3D Basisvoorziening — 3D Tiles Gebouwen** (Netherlands) | Nationwide buildings as 3D Tiles **1.1** at `api.pdok.nl/kadaster/3d-basisvoorziening/ogc/v1/collections/gebouwen/3dtiles/tileset.json`, no key (Tested). Content is glb that **requires `EXT_meshopt_compression` and `KHR_mesh_quantization`** (Tested on the leaf over Dam Square). Leaf regions read 23–343 m against the root's −20–300 m: the national range shifted by ≈43 m, which is the geoid height there (Tested) | CC BY 4.0 | **A**, with the decoder and datum steps of 6.3 |
| **Meta Canopy Height Maps v2** | Global tree heights in metres; one GeoTIFF per zoom-10 quadkey, 32,768 × 32,768 px at 1.19 m (Web Mercator), internally tiled 512 × 512 and deflate-compressed, so an HTTP range read fetches only the area needed; 10 m mean/p95 versions too; anonymous access works (Tested, header read) | CC BY 4.0 (credit Meta/WRI; imagery © Maxar) | **A** |
| **ESA WorldCover** | 10 m land cover 2020/2021, and Sentinel-2 RGB-NIR median composites as 1°×1° COGs of ~520–580 MB, readable by range request (Tested listing) | CC BY 4.0 per the AWS registry; one ESA catalogue record says "other" for the composite — confirm on its DOI page | **A** |
| **EOxCloudless** (Sentinel-2 cloudless) | Non-commercial use free | CC BY-NC-SA 4.0 | B — prefer WorldCover's composite |
| **National orthophotos** | One z17 tile each, no key, all HTTP 200 JPEG (Tested 9 Oct). Plain XYZ templates for `SOURCES`: France `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/jpeg`; Switzerland `https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg`; Netherlands `https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_orthoHR/EPSG:3857/{z}/{x}/{y}.jpeg`; Spain `https://www.ign.es/wmts/pnoa-ma?request=GetTile&service=WMTS&VERSION=1.0.0&Layer=OI.OrthoimageCoverage&Style=default&Format=image/jpeg&TileMatrixSet=GoogleMapsCompatible&TileMatrix={z}&TileRow={y}&TileCol={x}`; Japan `https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg` | Etalab 2.0; CC BY 4.0 (check the record); OGD with "©swisstopo"; CC BY 4.0; GSI terms with attribution | **A** |
| **OpenAerialMap** | Per-image licences: near Nairobi, 5–8 cm drone mosaics under CC BY-SA 4.0 and CC BY 4.0, and a Maxar 2019 mosaic under CC BY-NC 4.0 (Tested) | Per image | A/B — filter by licence |
| **Panoramax** | Search API with no key; most results carry `view:azimuth` and field of view. Tested with limit 100 on 15 city boxes of about 8 × 7 km (9 Oct), plus 3 landmarks (8 Oct). **Full (100):** Paris, Berlin (31 sequences, 10 providers, 2016–2026), Tokyo (4 sequences), Nairobi (6), Bengaluru (2), Dakar (1), Bogotá (2). **Some:** Mumbai (17 results, only 4 with an azimuth). **None:** Lagos, Kampala, Accra, Cairo, Johannesburg, São Paulo, Lima, Corcovado, Giza | Etalab 2.0 or CC BY-SA 4.0 per instance | **A**. Dense in France and Germany, thin and patchy elsewhere: always worth one query, never assumed |
| **Mapillary** (your optional token) | API image fields include `computed_geometry`, `computed_compass_angle`, `camera_parameters`, `sfm_cluster` (point cloud) and `mesh`; limits 60,000/min entity, 10,000/min search, 50,000 tiles/day; bbox searches must be < 0.01 deg² (Read, API docs) | Imagery CC BY-SA 4.0 | A with token (as today) |
| **KartaView** | API returned a query-timeout error for a 300 m radius at the Eiffel Tower (Tested) | CC BY-SA 4.0 | Do not rely on it |
| **GEBCO 2026 grid** | 15 arc-second ocean and land; a companion TID grid says which cells are measured and which interpolated | Public domain | **A** |
| **Open-Meteo Marine** | Wave height/direction/period, swell, sea-surface temperature, currents (Tested) | Free API is non-commercial; data CC BY 4.0 | Same footing as the Open-Meteo weather you already use (B if Mapify ever goes commercial) |
| **NASA GIBS** clouds | MODIS Terra true colour daily since 2000-02-24; VIIRS since 2015-11-24; GOES-East/West GeoColor and Himawari every 10 minutes, but the capabilities list times only back to August 2026; no Meteosat layer, so no geostationary view of Europe and most of Africa (Tested capabilities) | NASA/NOAA layers public domain; Himawari imagery comes from the Japan Meteorological Agency — credit it | **A** |
| **EGM2008 geoid** (for the datum shift) | Geoid height N: Amsterdam 43.2 m, Bern 49.4 m, Tokyo 36.4 m; across the Netherlands 41.0 m (Groningen) to 45.9 m (Maastricht) (Tested, GeographicLib GeoidEval). Offline: the NGA grid as PROJ-data `us_nga_egm08_25.tif`, sampled with `rasterio` or `pyproj` | NGA, a US Government work (public domain); confirm on the PROJ-data listing | **A** |
| **GeoNames** | Over 13 M features, 5.2 M of them populated places; `allCountries.zip` 422 MB, `alternateNamesV2.zip` 205 MB (Tested) | CC BY 4.0 | **A** |
| **GBIF** | Species match and occurrence facets with no key (Tested: *Acacia tortilis* → Saudi Arabia, Tanzania, Sudan, Djibouti, Kenya, Mauritania) | Records CC0/CC BY/CC BY-NC per dataset; credit GBIF.org | A for counts |
| **Streets GL** | Beautiful OSM renderer, but its README says "early development"; its terrain is an **Esri** tileset; the OSM wiki says its hosted tiles held September 2023 data as of April 2025; needs a discrete GPU | MIT | Do not adopt; borrow its roof generation code if useful (MIT) |
| **OSM2World** | Turns 250+ OSM tags (roof shapes, trees, benches, lane markings, power lines) into glTF/OBJ | MIT | **A**; 784★, 1,999 commits, 27 contributors, last commit 2026-09-08 |
| **Google Photorealistic 3D Tiles**, **Cesium ion** assets, **Esri World Imagery**, **Bing**, **Mapbox** | Paid API or account/subscription | — | **X** |

### 3.4 What the checking taught

- **A licence label covers one file, not what it loads.** `PLONK_OSV_5M` is "MIT" and pulls a
  CC BY-NC encoder at run time; only reading `pipe.py` shows it.
- **Stars measure attention, not delivery.** PIGEON has 282★ and no weights; Streets GL has
  1,101★ and stale data on a commercial terrain service; GeoRanker's headline needs GPT-4V.
- **The benchmark decides the winner, and numbers only compare within one benchmark.** Im2GPS3k
  is landmark-heavy; OSV-5M is pure street view with an administrative "city" metric; your
  benchmark is Commons photographs measured by distance. A 19 % on one and a 34 % on another say
  nothing about each other.
- **Dependencies hide traps.** `diff-plonk` 0.4 pins `scipy==1.13.1`, which has no Windows wheel
  for Python 3.13 (your version), and declares a dependency on the PyPI package `geos` — an
  unrelated GPL-3.0 map server that PLONK's inference code never imports (Read). `pillow-heif`
  wheels are GPL-2.0. "No licence" (MixVPR, Sample4Geo, GeoReasoner) means you may not use it.
- **Your own application is part of the platform.** A layer that works in a demo page can stay
  blank inside Mapify, because Mapify's content policy is deliberately strict (6.1).
- **Heights have a datum.** "Height" in a 3D tileset and "height" in a terrain tile are measured
  from different surfaces, 36–49 m apart in the countries with free city models (6.1).

---

## 4. The geolocation upgrade

### 4.1 Working toward the exact location: a refinement ladder

"Continuously working toward the exact location" without ever inventing it means each level is
claimed only when the evidence that establishes it is present, and each level's precision is
measured on the benchmark, as your confidence bands already are.

| Level | Claim | What establishes it | Cost |
|---|---|---|---|
| L0 | "Cannot be placed" | Gate fails, or both locators' densities are flat | — |
| L1 | Country / region | Two locators with different backbones agree, or script + language + GeoNames agree | Seconds |
| L2 | City (≤ 25 km) | Retrieval neighbours cluster, or a read place name matches inside the locator density | Seconds |
| L3 | Street (≤ 1 km) | A street or address resolves (GeoNames/Nominatim), or a named landmark is confirmed | Seconds to a minute |
| L4 | Spot (tens of metres) | Geometric verification against a geotagged reference: ≥ 40 SIFT inliers today, or the separately calibrated LightGlue line | 1–10 minutes (download-bound) |
| L5 | Camera position and heading | The matched reference's own pose (Mapillary computed pose, Panoramax azimuth) combined with the relative rotation from the match — which needs both focal lengths (EXIF or GeoCalib for yours, the reference's stated field of view) — or OrienterNet (B) | Minutes |

Mapify climbs automatically while the next step is cheap, offers the expensive steps (Pinpoint,
reader, OrienterNet) as buttons with a time estimate, and when it stalls asks for another
photograph — as it already does — naming the clue that would unlock the next level.

### 4.2 Additions, in order of expected value

Each is something your benchmark can accept or reject; your rule "reporting a clue and steering
the answer are different" stays — nothing gets weight until it is measured.

#### 4.2.1 Retrieval memory (the largest published lead at city level on street imagery)

- **What:** a local index of (CLIP ViT-L/14 image embedding — the one GeoCLIP already computes,
  latitude, longitude, source id, licence). At analysis: cosine top-50 neighbours → cluster them
  (haversine, ~25 km) → candidates with their neighbour count → fusion.
- **Why (Reported, all on the OSV-5M test set: 210,122 street-level images, each at least 1 km
  from every training image):**

  | Method | Country | Region | City | Source |
  |---|---|---|---|---|
  | Nearest neighbour, **OpenAI CLIP ViT-L/14** (your embedding) | 72.8 | 44.4 | **19.3** | OSV-5M paper, Table 7 |
  | Nearest neighbour, StreetCLIP (NC — not usable) | 73.4 | 45.8 | 19.9 | OSV-5M Table 7 |
  | Authors' best trained model (hybrid) | 68.0 | 39.4 | 5.9 | OSV-5M Table 6 (reprinted as 15.9 in the PLONK paper) |
  | PLONK (OSV-5M checkpoint) | 76.2 | 44.2 | 15.4 | PLONK paper, Table 1 |

  "City" means the right administrative city, not a radius. Retrieval's lead is largest at the
  finest level, which is the level your weak numbers are about. It also answers with real
  photograph positions instead of GeoCLIP's fixed 100,000-point gallery. **What is not known:**
  no paper measures retrieval *added to* GeoCLIP, and none uses photographs like your Commons
  benchmark, so the gain on your own numbers is unmeasured. Its OSV-5M figures cannot be set
  against your 34 % within 25 km. Your benchmark decides ("Done when" below).
- **Not an independent line.** It uses GeoCLIP's own embedding, so in your fusion it belongs to
  the existing "image model" line ("twelve readings from the same image model are one"). Its value
  is better candidate *positions* — dense where GeoCLIP's gallery is sparse — which the fusion
  then scores; it must not double their weight.
- **Data:** OSV-5M on Hugging Face, 98 train shards of ~2.52 GB plus `train.csv` (2.9 GB) with
  coordinates. Each shard holds exactly 50,000 images named by image id, and its zip directory
  (4.6 MB) sits at the end of the file, readable with one HTTP range request (Tested on shard
  00). The authors say the training set leans toward Western countries, so read the 98
  directories (~0.45 GB), join them with `train.csv`, and start with the 4 shards that carry the
  most African and South American images (~200 k images, ~10 GB download).
  The index is small: 200 k × 768 × 2 bytes ≈ 0.3 GB in float16; plain NumPy matrix products
  are enough up to ~1 M vectors, `faiss-cpu` (MIT) beyond. Delete the images after embedding,
  unless you want to show neighbours.
- **Cost (Tested on this PC, 9 Oct, CPU PyTorch 2.14 from Mapify's environment, 14 threads):** one
  ViT-L/14 image forward took **0.54 s** for a single image and **0.46 s per image** in batches
  of 16 (fp32; timed with randomly initialised weights, since cost does not depend on weight
  values). That is **6–7 hours per 50,000-image shard**, **about one day for the four starter
  shards**, and about **26 days for all 4.9 M**. JPEG decoding of the 512 px images adds little.
  Run it as a cancellable background job that resumes by shard. A GPU (Phase 17) makes the
  full set practical.
- **Licence:** CC BY-SA 4.0. Keep the index local; credit "OpenStreetView-5M (CC BY-SA 4.0),
  imagery by Mapillary contributors" whenever a neighbour contributed. Exclude the OSV-5M *test*
  split (you evaluate on it) and any image whose difference hash matches your benchmark.
- **Done when:** on the 163-photo benchmark plus a 1,000-image OSV-5M test sample, the ≤ 25 km
  rate rises for "street" and for Africa, and the "high" band still holds (26/26 within 47 km
  today). If it does not rise, retrieval stays as a candidate source for the 3D view's
  "similar photos" only.

#### 4.2.2 PLONK: an independent second locator (Phase 16)

- **What:** `PlonkPipeline("nicolas-dufour/PLONK_YFCC")` draws coordinate samples (e.g. 256) from
  a learned density; `compute_likelihood(images=…, coordinates=[…])` returns the exact
  log-likelihood of any coordinate — so it can score GeoCLIP's twelve candidates, the OCR
  candidates and the retrieval clusters directly.
- **Why:** different backbone (DINOv2) and different training data from GeoCLIP, so its errors
  are less correlated — exactly what Phase 16 wants: disagreement lowers confidence instead of
  being averaged. On YFCC4k it is reported ahead of GeoCLIP at 25 and 200 km and level beyond
  (3.1). The card does not say whether the released checkpoint is the 1 M- or the 10 M-iteration
  model — measure.
- **Licence:** MIT; DINOv2 Apache-2.0. Avoid `PLONK_OSV_5M` (StreetCLIP, NC).
  `PLONK_iNaturalist` (DINOv2, MIT) is a useful weak prior for plant and animal photos.
- **Install traps (Read):** `pip install diff-plonk` will fail on Python 3.13 (`scipy==1.13.1`).
  Install with `--no-deps` and add `geoopt` (Apache-2.0), `torchdiffeq` (MIT), `einops` (MIT) and
  a current SciPy — or vendor the inference files (MIT). Leave out `geos`. DINOv2 arrives through
  `torch.hub`, which downloads and *executes* code from GitHub and caches it under
  `C:\Users\…\.cache\torch` unless `TORCH_HOME` is set; your `apply_model_cache_env()` sets
  `HF_HOME` and EasyOCR's path but not `TORCH_HOME`. Set it to the models folder and pin the
  hub commit.
- **Cost:** one more ViT-L forward (≈0.5 s on this PC, measured as above) plus 16 flow steps of
  a 36 M-parameter network (paper: ~2 ms per image for one sample, 72 ms for 32). Exact
  likelihoods solve an ODE with a divergence term per coordinate, which can be far slower on a
  CPU; batch the ≤ 20 candidates in one call, and if that is still too slow, score the
  candidates with a kernel density over 512–1,024 drawn samples instead.
- **Done when:** both locators' candidates are attributed in the report; a continental
  disagreement demonstrably lowers the band (asserted in a test); the benchmark gain pays for
  the extra seconds.

#### 4.2.3 Reading text offline: gazetteer, language, OCR

- **GeoNames** `allCountries` (over 13 M features, 5.2 M populated places, with population,
  feature class and admin codes) and `alternateNamesV2` (multilingual spellings), CC BY 4.0,
  ~0.6 GB zipped. It replaces Natural Earth's ~7,300 settlements as the *offline* gazetteer;
  Nominatim stays for full addresses when you allow it. Weigh by population and feature type,
  and require agreement with a locator density for anything below country level (row 7 of
  Section 2).
- **GlotLID** (Apache-2.0 plus notices) identifies the language of each OCR line; language is a
  strong country prior where script alone is ambiguous (Latin script across Africa, Europe and
  the Americas).
- **OCR:** keep EasyOCR (Apache-2.0; its last commit was December 2025, so it is slowing). Test
  PP-OCRv5 (PaddleOCR, Apache-2.0, 106 languages per its current docs) through **RapidOCR**
  (Apache-2.0, ONNX Runtime, no PaddlePaddle install) on the text-bearing set you already
  approved (`fetch_text_samples.py --target 70`). Keep whichever reads more place names
  correctly; no published head-to-head on street signs exists.
- **Shop and business names:** a legible shop sign is often the best street-level clue, and
  Natural Earth and GeoNames do not hold businesses. Overture's *places* theme does, with
  permissive terms per provider (most CDLA-Permissive 2.0, Foursquare Apache-2.0 with its NOTICE,
  AllThePlaces CC0). Fetch only the candidate areas —
  `overturemaps download --bbox=W,S,E,N -f geoparquet --type=place -o …` (CLI, MIT, no account) —
  and match OCR text inside each candidate cluster. Chains (one name, thousands of branches)
  count only with a second clue.
- **Map-based structure checks (your Phase 14)** should read the local OSM store and these
  Overture extracts rather than Overpass, keeping your rule that a feature found near *every*
  candidate moves nothing.
- **Done when:** on the text set, correct toponym matches rise and wrong "high" calls do not.

#### 4.2.4 Pinpoint: more references, better near-miss matching

- **Panoramax:** `GET https://api.panoramax.xyz/api/search?bbox=W,S,E,N&limit=…` (STAC, no key).
  Most hits carry a heading and field of view, which is what L5 needs; skip hits without an
  azimuth for L5 (13 of 17 in Mumbai had none). Coverage (Tested, 15 cities plus 3 landmarks) is
  dense in France and Germany, a few contributor sequences in Nairobi, Dakar, Bogotá, Bengaluru
  and Tokyo, and nothing in Lagos, Kampala, Accra, Cairo, Johannesburg, São Paulo or Lima — so
  query it for every candidate cluster (it costs one request) and report "no references here"
  honestly. Few sequences means few viewpoints: in Nairobi the 100 hits came from 6 sequences.
  Same etiquette as Commons: one request at a time, identifying User-Agent, cache.
- **Wikidata → Commons:** for a recognised landmark, Wikidata (CC0) gives its coordinate
  (P625), its Commons category (P373) and labels in every language — the fix for the
  named-search cases your milestone still lists as choosing wrong (Moscow, Giza, Rio), where CLIP
  reads local names poorly: search the landmark's category instead of guessing its name. Stay
  well inside the query service's per-client limits: one query at a time, identifying
  User-Agent, cached answers.
- **Mapillary:** use `computed_geometry` and `computed_compass_angle` (structure-from-motion
  corrected) rather than raw GPS as the reference position; it improves L4 and gives L5.
- **LightGlue second pass:** `kornia.feature.LightGlueMatcher` with DISK or ALIKED features
  (never SuperPoint). Run it only on references whose SIFT inliers fell between your negative
  ceiling (14) and the confirmation line (40), plus the SIFT top 10. Calibrate its *own* lines
  with `calibrate_pinpoint.py` on the same 600 unrelated pairs; never reuse 40/20 for another
  matcher. RoMa (or Tiny RoMa) only for the final 3–5 pairs.
- **Check:** some Commons files carry only an *object* location (the subject) rather than a
  camera location; a match to such a file locates the subject, not where you stood.
- **Done when:** the 11-landmark evaluation pinpoints more at the true spot and at 600 m off,
  with the false-positive ceiling held.

#### 4.2.5 Camera geometry: sun, shadows, skylines

- **GeoCalib** gives gravity (roll, pitch) and field of view from one image: needed for the
  sun/shadow technique (your Phase 15), for skyline matching, for detecting a downward-looking
  aerial photo, and for the walk overlay's lens when EXIF has no focal length.
- **MoGe-2** gives metric depth and field of view: distance to the nearest building, camera
  height, and a 3D proxy of the photograph for walk mode. Depth Anything V2 *Small* is the
  lighter alternative.
- **Skyline against terrain (build it):** sky mask (OneFormer ADE20K, MIT) → skyline curve →
  render the horizon from Mapterhorn/Copernicus terrain at 1.7 m above ground around each
  candidate → 1-D correlation over heading. No maintained open-source implementation exists;
  published systems needed a prior within a few hundred metres or reported low open-search
  success with 30 m terrain (one 2019 thesis: ≤ 17 %). So: **verification and re-ranking within
  ~20 km only, never a world search.**
- **Time caveat:** sun-based constraints need a trustworthy capture time (EXIF clocks are often
  wrong or in an unknown zone); without one the technique reports "not run".

#### 4.2.6 Nature and rural evidence

- **BioCLIP 2** names plants and animals in the frame; **GBIF** (`api.gbif.org`, no key) says
  where that taxon occurs. *Acacia tortilis* returned the Sahel, East Africa and Arabia (Tested) —
  exactly the separation from Latin America that GeoCLIP misses.
- **Keep it offline:** a species name is something read from the photo, and your rule is that
  nothing read from it leaves the machine unnecessarily. Fetch the country counts for the taxa in
  BioCLIP's label list once, in advance (`/v1/occurrence/search?taxonKey=…&limit=0&facet=country`),
  store them in `data\`, and look them up locally at analysis time.
- **Traps:** planted and invasive species (palms, eucalyptus, jacaranda) are everywhere; GBIF is
  sampled heavily in Europe and North America. Use continent-level presence, small weight, and
  only after it is measured. SINR (MIT) is an offline species-range model if you want no
  network call.

#### 4.2.7 Optional local reader (a VLM that explains, never decides)

- **Qwen3-VL-4B or -8B-Instruct** (Apache-2.0). Qwen publishes official GGUF files
  (`Qwen/Qwen3-VL-4B-Instruct-GGUF`: Q4_K_M 2.5 GB plus a vision projector) for **llama.cpp**
  (MIT). Download the model and `mmproj` files into the E: models folder and have Mapify start
  `llama-server -m <model.gguf> --mmproj <mmproj.gguf> --host 127.0.0.1 --port <random>
  --api-key <random>` only when the reader is used — the same loopback-and-token boundary your
  own server keeps. **Ollama** ≥ 0.12.7 also works (`qwen3-vl:4b` 3.3 GB, `:8b` 6.1 GB) but runs
  as a standing service on a fixed port with its model store on C: unless `OLLAMA_MODELS` is
  set, so llama.cpp fits your rules better.
- **Use:** a JSON list of clues — text verbatim, script, language, driving side, signage,
  vegetation, architecture — each routed into the module that can *check* it (text → gazetteer,
  plants → GBIF). Its own coordinate is never adopted (YFCC4k: 10.8 % within 25 km).
- **Cost:** minutes per image on CPU. Opt-in.

#### 4.2.8 Tier B options, only if you accept non-commercial terms

- **OrienterNet** (CC BY-NC): camera position and heading against OpenStreetMap when the prior is
  already within tens of metres and no reference photo matched — L5 in mapped cities. Needs
  GeoCalib's gravity and a focal length.
- **StreetCLIP** and **PLONK_OSV_5M**: possibly better on street scenes; NC.
- **FABDEM**, **GlobalBuildingAtlas**, **EOxCloudless**: see Section 6.

### 4.3 How the pieces combine

```
photo ─ decode (+HEIC via pi-heif) ─ EXIF (claim, not answer) ─ C2PA (provenance, if present)
  │      edge gates: mirror test · panorama → views · aerial (GeoCalib pitch) · screen/poster
  │
  ├─ CLIP ViT-L/14 ─┬─ GeoCLIP, 100k gallery ───────────────┐
  │                 └─ retrieval memory (OSV-5M + verified) ─┤ image line
  ├─ DINOv2-L ─ PLONK density · likelihood(all candidates) ──┤ second image line
  ├─ OCR ─ GlotLID ─ GeoNames · Overture places / Nominatim ┤ text line
  ├─ BioCLIP 2 ─ GBIF ranges ───────────────────────────────┤ nature line
  ├─ GeoCalib + shadows + trusted time ─────────────────────┤ sun line
  └─ optional reader → clues routed into the lines above     │
                                                             ▼
             existing log-space fusion; per-line weights fitted on the benchmark
                                                             ▼
   candidates + measured bands ─► Pinpoint: Commons · Wikidata · Panoramax · Mapillary
                                  SIFT → LightGlue on near-misses → L4
                                                             ▼
   skyline check (mountain scenes) · reference pose / OrienterNet (B) → L5 → globe, 3D view
```

---

## 5. Continual learning from verified data

The safest form of learning here is **memory plus recalibration**, not retraining.

1. **What counts as verified:** a coordinate you confirm on the map, or a photo set you supply
   with known positions. Never Mapify's own output, never EXIF on its own word, never a Pinpoint
   result you have not confirmed. Learning from its own guesses would make the tool confirm
   itself — the failure your design exists to prevent.
2. **What is stored** (in `data\memory\`, beside the case files): the CLIP embedding, coordinate,
   your stated accuracy, date, source, licence and difference hash. Not the image, unless you
   opt in — the same rule as today's case index.
3. **How it is used:**
   - as entries in the retrieval memory (4.2.1) — they work immediately, no training;
   - as extra coordinates in GeoCLIP's GPS gallery — it is a plain tensor of coordinates
     (`model.gps_gallery`), and your gallery cache is keyed by the gallery's hash, so adding
     points costs one re-encode (your README measured 272 s for the 100 k-point gallery);
   - as data for **refitting fusion weights and confidence thresholds** with the existing
     `tune_confidence.py` split (fit on one half, validate on the other);
   - after a few thousand verified pairs: **fine-tuning GeoCLIP's small trainable parts**. Its
     CLIP backbone is frozen (`requires_grad = False` in `image_encoder.py`, Read); only a
     768→768→512 MLP and the location encoder learn. Because you already store the CLIP
     embedding, training needs no image passes and is feasible on a CPU with the released
     `train.py` logic. Mix in an equal share of OSV-5M embeddings from the retrieval memory so the
     model does not forget the rest of the world.
4. **Safeguards:** near-duplicates (hash or embedding cosine > 0.95) count once; anything whose
   hash is in the benchmark is refused (otherwise your measured accuracy becomes fiction); a cap
   per 25 km cell so one city cannot dominate; every entry can be listed and deleted; a new model
   or threshold is promoted only if the frozen benchmark does not get worse, and the previous
   version is kept for rollback.
5. **Privacy:** embeddings stay local, exactly like today's case index.

---

## 6. The 3D world and the walk

### 6.1 The cases that break the 3D view — designed for first

These were found by testing against Mapify's own server policy, the vendored deck.gl bundle and
live data, not by reading feature lists.

| # | Case | What goes wrong (evidence) | What handles it |
|---|---|---|---|
| 3D-1 | **Any new layer the browser fetches from another host** | `server.py` sends `connect-src 'self'` and widens `img-src`/`connect-src` only for the one configured basemap origin (Read, `_CSP_BASE`, `_csp`). A `TileLayer` or `Tile3DLayer` pointed at PDOK, swisstopo, IGN, OpenAerialMap or a PLATEAU host is refused and draws nothing | **The server fetches everything; the policy's host list is not widened.** Raster sources become new `TileSource` entries in `geodata/tiles.py`, served by the existing `/api/tiles/{source}/{z}/{x}/{y}` (which `ground3d.js` already uses, Read) with its disk cache, 204-with-reason and attribution. 3D Tiles go through a new `/api/3dtiles/{id}/{path}` (6.3). COG sources (WorldCover, canopy) are read by the server with range requests |
| 3D-2 | **Compressed meshes need WebAssembly** | The Dutch tileset *requires* `EXT_meshopt_compression` (Tested); the meshopt decoder inside the vendored bundle calls `WebAssembly.instantiate` (Read). Under Mapify's `script-src 'self' 'nonce-…'`, Chromium refuses it: "Compiling or instantiating WebAssembly module violates … 'unsafe-eval' is not an allowed source" (Tested, same policy in a loopback test page). Adding `'wasm-unsafe-eval'` let it run (Tested) | **Decision:** add the single token `'wasm-unsafe-eval'` to `script-src` in `_CSP_BASE`. It permits compiling WebAssembly, not `eval` of JavaScript, and scripts still come only from Mapify. The alternative — decompressing every glb on the server before serving it (e.g. glTF-Transform, MIT, a Node tool) — keeps the policy byte-identical but adds a runtime to the ZIP; choose it if you prefer the policy untouched |
| 3D-3 | **Draco-compressed tilesets fetch their decoder from the internet** | The bundle's Draco path points at `www.gstatic.com/draco/versioned/decoders/1.5.6` and loaders.gl's library loader at `unpkg.com` (Read); both are blocked, and loaders.gl injects fetched libraries as inline scripts, which the nonce policy also blocks | Serve the Draco decoder files (Apache-2.0) from `/vendor/draco/`, load the wrapper with a normal script tag from Mapify, and hand it to loaders.gl through `loadOptions.modules.draco3d` — the bundle uses that object directly when present (Read: `r.draco3d ? r.draco3d.createDecoderModule({})`). Keep `loadOptions.worker: false`, as `ground3d.js` already does for terrain (Read), so no worker script is fetched from unpkg. Check a tileset's glTF `extensionsRequired` before listing it |
| 3D-4 | **City models float: ellipsoid vs sea level** | 3D Tiles positions are Earth-centred, so deck.gl draws them at *ellipsoidal* height; Terrarium terrain (current and Mapterhorn) is height above sea level. The gap is the geoid height N: 43.2 m Amsterdam, 49.4 m Bern, 36.4 m Tokyo (Tested). The Dutch tileset's leaf regions sit ≈43 m above its root range (Tested) | Shift the tileset down by N along the local vertical (6.3). Take N from the EGM2008 grid **at the view centre** and recompute when the view moves > 20 km: N spans 41.0–45.9 m across the Netherlands alone (Tested), so one value per national tileset would be wrong by up to ±2.5 m |
| 3D-5 | **City models sink or float even after the datum fix** | Surface-model terrain is raised in cities: the current Terrain Tiles read 13 m at Dam Square (Tested), where the street is a few metres above sea level | **Seating check:** at three addresses per tileset, compare where a wall meets the ground with the terrain under it (pick both in the walk view). Residual ≤ 2 m: done. Larger: use Mapterhorn's lidar terrain there (it covers these countries) and, failing that, label "terrain here includes roofs; ground under buildings is approximate". Never shift the model to hide a residual |
| 3D-6 | **The walk eye lands on roofs or canopy** | Same surface-model bias; Copernicus GLO-30 is a surface model everywhere | Clamp the eye to heights sampled along OSM road centrelines; prefer lidar terrain; label the terrain type |
| 3D-7 | **A proxy becomes an open relay** | A loopback endpoint that forwards any URL lets any page or local process use Mapify to fetch anything | Allow-list: each `{id}` maps to one fixed base URL in config; reject `..`, schemes, absolute or protocol-relative URIs in `{path}`; keep the existing session token requirement |
| 3D-8 | **A tileset uses an extension the loader lacks** | Blank tiles or an exception | Read `extensionsUsed`/`extensionsRequired` of one glb per tileset in a setup test; list a tileset only after it renders at a test address |
| 3D-9 | **The street-image panel needs many image hosts** | MapillaryJS and the Panoramax viewer fetch from their CDNs, which the policy blocks | Open them on a **separate page** that Mapify serves with its own policy naming only those hosts; the main page's policy stays as it is |
| 3D-10 | **Upstream down, or offline** | Missing tiles | The existing 204-with-reason pattern; pre-fetch an area into `data\` (PMTiles extracts, tileset copies) |

### 6.2 What is achievable, element by element

| Element | Real, free data | Licence | Achievable now | Not achievable free |
|---|---|---|---|---|
| Terrain | Mapterhorn (0.25–2 m in about 30 countries, Copernicus 30 m elsewhere); your AWS Terrain Tiles as fallback | A | Cliffs, terraces and embankments in those countries; 30 m shape elsewhere | Fine or bare-earth terrain in Africa, South America and most of Asia, except FABDEM (B) at 30 m |
| Ground imagery | National orthophotos (FR, NL, CH, ES, JP — templates tested, 3.3; USGS/NAIP already); ESA WorldCover Sentinel-2 composite 10 m; OpenAerialMap drone mosaics where they exist | A (per source) | Houses visible in those countries; fields, roads and coasts elsewhere | Sub-metre imagery elsewhere (commercial only) |
| Building footprints | OSM and Google Open Buildings v3 (both already drawn), plus Microsoft and Overture | A | Near-global | — |
| Building heights | OSM tags (already); Microsoft estimates; Google Open Buildings 2.5D (Africa, Asia, Latin America); GBA (B) | A / B | Measured or estimated heights in many more places, coloured by source as your `heights.py` already does for OSM | Exact heights everywhere |
| Roof shapes, detail | OSM `roof:*` and building parts through OSM2World; PLATEAU LoD2 (Japan) and NL 3D Tiles | A | Correct roofs where mapped; true textured models in Japan — after the datum shift and seating check (6.1) | Photo facades elsewhere |
| Roads, lanes, paths, bridges, tunnels | OSM (import or Overture transportation) | ODbL | Lanes, widths, surfaces, layers | — |
| Infrastructure and street furniture | OSM street lamps, poles, power lines, signals, benches (OSM2World draws 250+ tags); Mapillary map features (lights, poles, signs detected from imagery, with your token) | ODbL / CC BY-SA | Real positions where mapped | Unmapped objects |
| Vegetation | Meta canopy height v2 (~1 m tree heights → tree tops and heights); WorldCover classes; OSM trees and woods | A | Trees where trees are, at their measured height | Species and exact crowns |
| Water and ocean | OSM water; GEBCO 2026 seafloor with its measured/interpolated map; Open-Meteo Marine waves | A / non-commercial | Coast, depth, sea state at the photo's hour | Real wave surface at that instant |
| Weather and atmosphere | Open-Meteo ERA5/CAMS (now); MODIS/VIIRS daily satellite clouds; GOES/Himawari 10-minute clouds for the last ~2 months (not Europe/Africa) | A / non-commercial | Sky, rain, snow, haze, plus the day's real cloud picture | The clouds at the exact second |
| Vehicles, people | None free | — | Illustrative, labelled (as now) | Real positions at the moment |
| Ships, aircraft | Historical AIS for US waters (MarineCadastre, NOAA/BOEM; a US federal work — read the use constraints on its record). Denmark's archive needs an application and may carry fees, so it is not counted | Check record terms | Ships near US coasts on that day | Elsewhere; aircraft at a past moment (OpenSky's history needs an account) |
| Photo-real street | Mapillary or Panoramax imagery in a street-image viewer; Gaussian splats from such sequences or your own photos | CC BY-SA / Etalab | Where imagery exists, in a separate panel (3D-9) | Everywhere |

### 6.3 Rendering pieces that fit the current deck.gl view

Every piece below obeys 6.1: the server fetches it, and the browser asks only Mapify's server.

- **Terrain:** your front end already asks your own server for `/api/tiles/terrain/{z}/{x}/{y}`
  as 256-px Terrarium PNGs. Add Mapterhorn as an upstream in `geodata/tiles.py`: fetch its 512-px
  WebP tile at zoom z and serve each 256-px quarter as the PNG for zoom z+1 (same encoding,
  Pillow decodes WebP), so `terrain.js` does not change. The planet file stops at z12; regional
  z13–17 files exist only where fine data does — fall back to the current source elsewhere. For
  offline use, `pmtiles extract --bbox=…` (BSD-3) pre-fetches a place into `data\terrain\`.
  Mapterhorn mixes national height systems; their differences from EGM2008 are small next to N,
  but record each source's vertical reference from `attribution.json` alongside the tile.
- **Imagery:** one `TileSource` per national service with the tested templates in 3.3, a
  country-polygon test in `sources_for()` the way `_US_BOXES` gates USGS today, and each
  source's credit string. Your existing `TileLayer` + `BitmapLayer` then draws them unchanged.
  WorldCover's Sentinel-2 composite and other COGs: cut into tiles by the server with HTTP range
  reads (`rasterio`, BSD-3, ships GDAL in its wheel). OpenAerialMap: one source per mosaic,
  filtered by licence.
- **City models (3D Tiles):**
  1. *Loader:* `Tile3DLayer` is in your deck.gl 9.3.11 bundle, but the bundle contains no 3D
     Tiles loader (Read). Vendor `@loaders.gl/3d-tiles@4.4.3/dist/dist.min.js` (292 KB, MIT,
     released 2026-06-13), pin its SHA-256 in `scripts/fetch_data.py` like deck.gl, and pass
     `loader: Tiles3DLoader` with `loadOptions: { worker: false, modules: { draco3d } }`.
  2. *Transport:* `/api/3dtiles/{id}/{path}` on Mapify's server, with `{id}` allow-listed to a
     fixed base (for the Netherlands,
     `https://api.pdok.nl/kadaster/3d-basisvoorziening/ogc/v1/collections/gebouwen/3dtiles/`).
     Relative URIs inside `tileset.json` (`t/10/430/604.glb`, `tileset-5-…json` — Tested) then
     resolve through the proxy by themselves. Cache every response under `data\3dtiles\{id}\`.
     For Japan, download a city's PLATEAU 3D Tiles once into the same folder and serve it locally,
     which also works offline.
  3. *Decoders:* `'wasm-unsafe-eval'` in `script-src` for meshopt and Draco (decision in 3D-2);
     the Draco decoder served from `/vendor/draco/` (3D-3).
  4. *Datum:* compute N at the view centre from the EGM2008 grid (PROJ-data
     `us_nga_egm08_25.tif`, sampled by `rasterio`; or `pyproj` with `EPSG:4979` →
     `EPSG:4326+3855`), send it with the view, and lower the tileset by N along the local up
     vector: in Earth-centred coordinates, a translation of −N × (cos φ cos λ, cos φ sin λ,
     sin φ) at the view centre, applied through the tileset's model matrix (loaders.gl
     `Tileset3D`, reached from `onTilesetLoad`). If PROJ is used, set
     `PROJ_USER_WRITABLE_DIRECTORY` and `PROJ_DATA` to the E: models folder, or its grid cache
     goes to `%LOCALAPPDATA%` on C:.
  5. *Seating check* (3D-5) before a tileset is listed.
  6. Test each tileset before relying on it: the loader accepts 3D Tiles 1.0 and 1.1, but not
     every extension a publisher may use.
- **OSM detail:** run OSM2World (Java, MIT) on the area around a pinpointed spot → glTF →
  `ScenegraphLayer`, served from `data\`; or instanced meshes for trees and lamps through
  `SimpleMeshLayer`.
- **Trees:** local maxima of Meta canopy height (read by the server) → instanced trees at
  measured height, labelled "from a canopy-height model".
- **Real 3D points:** Mapillary `sfm_cluster` point clouds, fetched by the server with your
  token → `PointCloudLayer`.
- **Photo-real panel:** MapillaryJS (MIT) or the Panoramax viewer for street imagery on its own
  page (3D-9); Gaussian splats trained with **Brush** (Apache-2.0; 5,150★, 1,227 commits, 25
  contributors; runs on AMD, Nvidia or Intel GPUs without CUDA; takes COLMAP input, COLMAP is BSD)
  and displayed with **Spark** (MIT; 3,701★, 629 commits, 33 contributors; a three.js renderer) in
  its own canvas from a local file. This needs a GPU and tens of overlapping images of one spot;
  credit the imagery's authors (CC BY-SA for Mapillary, so a shared splat is CC BY-SA too).
- **Ocean:** GEBCO seafloor as terrain below sea level; a water surface whose wave height and
  direction come from Open-Meteo Marine (fetched by the server, as weather is today).
- **Sky:** your `scene.js`; add the day's MODIS true-colour cloud picture as a dated GIBS
  `TileSource` (the same pattern as your HLS layer), labelled "satellite view of the clouds that
  day, ~10:30 local overpass".

### 6.4 Per environment

- **Cities:** OSM + Microsoft heights; OSM2World roofs and street furniture; PLATEAU/NL 3D Tiles
  after the datum shift and seating check; orthophotos in the six countries; the walk eye clamped
  to road heights (3D-6).
- **Remote areas:** Google Open Buildings footprints (already) with 2.5D heights and Microsoft
  footprints; Meta tree heights; WorldCover classes; 10 m Sentinel-2 composite; OpenAerialMap
  where humanitarian mapping flew drones.
- **Oceans:** GEBCO seafloor; the TID grid lets the view say where depths are interpolated;
  Open-Meteo Marine sea state at the capture hour. Geolocation of open water stays "cannot be
  placed" unless land, a readable name or a sun fix exists.
- **Islands:** OSM coastline polygons over terrain; check the DEM actually covers small atolls
  before drawing land; GEBCO for the reef slope.
- **Mountains:** 1–2 m terrain in the Alps, Norway, Sweden, Finland, the US, Canada, Japan and
  New Zealand; 30 m in the Andes, the Himalaya and Africa's ranges;
  OSM peaks with names and heights for labels; snow from Open-Meteo (ERA5 snow depth) and MODIS
  snow cover; skyline verification (4.2.5).
- **Volcanic regions:** OSM `natural=volcano`; Smithsonian GVP volcano list (non-commercial
  terms, citation required → B); WorldCover bare/sparse classes for lava fields. **Data age:**
  Copernicus DEM elevations come from 2011–2015 radar; newer cones and lava fields (for
  example La Palma 2021) are missing — show each layer's date.

### 6.5 Leaving the animation exactly as it is

- `globe.js` is not edited. The hand-off at the globe's closest zoom (~25 km) into the ground
  view, the great-circle arc, the descent, clouds, bloom, sun, Landsat WELD pick-up and the
  settle-in pulse all stay.
- New data is only *prefetched* while the existing flight runs (selecting a candidate is the
  trigger), and new layers fade in inside `Ground3D` after the settle-in pulse. No camera path,
  timing or shader changes.
- Candidates keep using the existing `addMarker` API.
- The only change outside the ground view is the one policy token of 3D-2 in `server.py`
  (or none, if you choose server-side decompression). It changes what the page *may* compile,
  not anything it draws.
- **Test:** the same scripted flight (`bench/cdp_flow.py`), with a fixed date and lighting mode
  and captured at fixed timestamps, must give identical globe frames before and after each
  change, apart from tile content; the frame time must not rise; and the browser console must
  show no content-policy violations during the flight or in the ground view.

---

## 7. Three reviews, one answer

I reviewed every option from three positions — a geolocation researcher (accuracy), a geodata and
licensing engineer (terms, storage, CPU, security), and a real-time 3D engineer (rendering, the
walk, the animation). The strongest idea each brought:

- **Researcher:** reuse the embedding you already compute for retrieval, and let PLONK *score*
  existing candidates through its likelihood instead of adding another list to average.
- **Licensing engineer:** read what a model loads, not what its card says (`PLONK_OSV_5M`,
  `pillow-heif`, `diff-plonk`'s `geos`), keep every restricted item behind one switch, and keep
  the browser talking only to Mapify's own server.
- **3D engineer:** change the data behind your existing endpoints (terrain re-tiled on the
  server, imagery as new tile sources, layers added inside `Ground3D`) so neither `terrain.js`
  nor `globe.js` has to move — and put every model on the same vertical datum as the ground.

Where they disagreed:

| Question | Researcher | Licensing engineer | 3D engineer | Decision |
|---|---|---|---|---|
| Use a large VLM as the locator? | Qwen3-VL-8B beats GeoCLIP at 25 km on Im2GPS3k | It falls to 10.8 % on YFCC4k; the big wins need GPT-4V or paid search | 6 GB on a CPU = minutes | Reader only; coordinates never adopted |
| Non-commercial models (StreetCLIP, OrienterNet, GBA, FABDEM)? | Some are best in class | Mapify is MIT and shared as a ZIP | — | Tier B: opt-in, never bundled, off when `commercial_use` is set; prefer the permissive equivalent |
| Photoreal 3D everywhere? | — | No free global source exists | Splats look real | Data-derived realism everywhere, labelled; photo-real only where imagery exists, in its own panel |
| Replace SIFT? | LightGlue is better | Thresholds 40/20 were calibrated for SIFT | CPU time | SIFT stays; LightGlue on near-misses with its own calibration |
| Full 4.9 M retrieval index? | Bigger is better | 246 GB download; ~26 days of CPU at the measured 0.46 s/image | — | Four shards first (~1 day); grow only if the benchmark rewards it |
| Terrain for the walk? | — | FABDEM is bare earth but NC | Eye on rooftops with a surface model | Mapterhorn lidar where available + clamp to roads; FABDEM as B |
| Widen the page policy for new layers? | Easiest path to more data | The policy exists to name every host; a list of a dozen hosts is how it erodes | Workers, Draco and meshopt need more than hosts anyway | No new hosts: the server proxies everything. One token (`'wasm-unsafe-eval'`) for decoders, or server-side decompression if you prefer zero change |

---

## 8. Build order, each step gated by measurement

| Step | Contents | Done when |
|---|---|---|
| 1 | Pin `geoclip==1.2.1`; HEIC (`pi-heif`), mirror test, panorama splitting, C2PA read, `TORCH_HOME` in `apply_model_cache_env()`, GeoNames + GlotLID offline | Edge-case tests pass; the text set improves; benchmark unchanged elsewhere |
| 2 | Verified-data store and retrieval memory (4 OSV-5M shards, ~1 day of background CPU) | Benchmark rule of 4.2.1 |
| 3 | PLONK_YFCC with likelihood fusion | Phase 16 criteria + measured gain |
| 4 | Pinpoint: Panoramax, Wikidata categories, Mapillary computed poses, LightGlue near-miss pass | 11-landmark evaluation improves at the same false-positive ceiling |
| 5a | 3D rasters through the server: Mapterhorn, national orthophotos, WorldCover composite, canopy heights, GEBCO + Marine, MODIS clouds | Each source returns a tile through `/api/tiles/` at a test address; no content-policy violation in the console; every layer credited on screen; animation test of 6.5 passes |
| 5b | 3D Tiles: vendored loader, `/api/3dtiles/` proxy with allow-list, decoder decision (3D-2), Draco via `modules.draco3d`, EGM2008 datum shift, seating check; Microsoft/Open Buildings heights | Dutch buildings render at Dam Square, Utrecht and Rotterdam with walls meeting the ground within 2 m; the proxy rejects a non-listed host and `..`; animation test passes |
| 6 | GeoCalib + MoGe-2 → sun/shadow (Phase 15) and skyline verification | Mountain subset improves; no regression elsewhere |
| 7 | Optional: reader VLM, OrienterNet (B), OSM2World detail, splat panel (GPU), street-image page | Each opt-in with its own measured benefit |

---

## 9. Where everything lives on E:

All under your existing app root (`E:\mapify\app\`), so nothing lands on C:.

| Path | Contents | Size |
|---|---|---|
| `models\hub\` | Hugging Face models (already `HF_HOME`): PLONK (36 M parameters), BioCLIP 2, MoGe-2 | ~0.2 GB to a few GB |
| `models\torch\` | `TORCH_HOME`: DINOv2 via torch.hub (~1.2 GB), and the GeoCalib and kornia LightGlue/DISK weights, which also download through torch's cache | ~1.5 GB |
| `models\proj\` | EGM2008 geoid grid (`us_nga_egm08_25.tif`); `PROJ_DATA` / `PROJ_USER_WRITABLE_DIRECTORY` point here | tens of MB |
| `models\llm\` | optional GGUF reader model and its `mmproj` (llama.cpp `-m`, or `OLLAMA_MODELS`) | 2.5–6.1 GB |
| `data\retrieval\osv5m\` | shard downloads (delete after embedding) and the index | 2.5 GB per shard; ~77 MB index per shard |
| `data\memory\` | verified entries | small |
| `data\gazetteer\geonames\` | GeoNames dumps | ~0.6 GB zipped |
| `data\terrain\` | Mapterhorn PMTiles extracts | per area |
| `data\3dtiles\` | proxied and downloaded 3D Tiles (NL per area, PLATEAU per city) | per area |
| `data\bathymetry\` | GEBCO 2026 GeoTIFF | 4 GB zipped, 7.2 GB unzipped |
| `data\canopy\`, `data\buildings\`, `data\imagery\` | canopy windows read by range request, footprints, cached imagery | per area |

---

## 10. Attribution lines to add to ATTRIBUTION.md

- "Place names: GeoNames (CC BY 4.0)" · "Street-level retrieval: OpenStreetView-5M (CC BY-SA 4.0), imagery by Mapillary contributors"
- "Second locator: PLONK (MIT), Dufour et al., CVPR 2025; DINOv2 (Apache-2.0)" · "Species: BioCLIP 2 (MIT); occurrence data GBIF.org"
- "Terrain: Mapterhorn and its sources (attribution.json)" · "Bathymetry: GEBCO Compilation Group (2026) GEBCO_2026 Grid" · "Geoid: EGM2008, NGA"
- "Tree heights: Meta and World Resources Institute, Canopy Height Maps v2 (CC BY 4.0); source imagery © Maxar"
- "Land cover / imagery: ESA WorldCover (CC BY 4.0)" · national orthophoto credits as each requires ("©swisstopo", "IGN – Etalab 2.0", "Kadaster / Beeldmateriaal.nl", "IGN España – CC BY 4.0", "地理院タイル (GSI)")
- "Buildings: Microsoft Global ML Building Footprints (CDLA-Permissive 2.0)" · "Google Open Buildings 2.5D Temporal (CC BY 4.0)" · "PLATEAU (MLIT, CC BY 4.0)" · "3D Basisvoorziening (Kadaster, CC BY 4.0)"
- "Street imagery: Panoramax contributors (Etalab 2.0 / CC BY-SA 4.0)" — per photo, like Commons today.
- "Places: Overture Maps Foundation, overturemaps.org" plus each provider's notice (Foursquare's NOTICE file when its records are used).
- "3D Tiles loader: loaders.gl (MIT)" · "Draco decoder (Apache-2.0)"

---

## 11. Assumptions

- "Free" means no payment, no paid tier and no account, except the optional Mapillary token you
  already chose. Non-commercial licences are acceptable only as opt-ins, because Mapify is a
  free MIT project shared publicly; if you ever commercialise it, Tier B must be switched off.
- "Verified data" means coordinates you confirm, not EXIF and not Mapify's own estimates.
- The target machine is a CPU-only Windows PC; a GPU is optional (Phase 17). The CPU timings
  above were taken on the PC Mapify is installed on (20 logical processors).
- Mapify's content policy is a deliberate security choice; this research changes it by at most
  one token, and names the alternative that changes nothing.
- Stars, commits, coverage and dataset sizes change; all were read on 8–9 October 2026.

---

## 12. Checks this report was held to

| Check | Result |
|---|---|
| Every recommended item states its licence, data, cost, plug-in point and a measurable "done when" | Pass |
| No recommendation depends on a paid API, Google Maps, a commercial geolocation service, a subscription, or an account beyond your optional Mapillary token | Pass — paid and account-bound options are in Tier X with the reason |
| Each source's claim was checked against code, licence file, activity or a harder benchmark — and labelled Tested/Read/Reported | Pass |
| Accuracy figures are compared only within one benchmark and one metric | Pass (4.2.1 table; 3.4) |
| Coverage claims rest on more than a handful of probes, including the weak regions | Pass (Panoramax: 15 cities, 7 African) |
| Every cost estimate prices the work it describes | Pass (ViT-L/14 forward measured: 0.46–0.54 s) |
| Every new 3D source reaches the screen under Mapify's actual content policy | Pass (6.1 3D-1 to 3D-3; WebAssembly behaviour tested) |
| City models sit on the terrain (vertical datum handled and checked) | Pass (3D-4, 3D-5; geoid heights tested) |
| Achievable now / with work / not achievable free is stated for geolocation and for every 3D element and environment the request names (cities, remote areas, oceans, islands, mountains, volcanic regions; buildings, roads, terrain, vegetation, vehicles, infrastructure, weather, atmosphere) | Pass (Sections 4, 6.2, 6.4) |
| The globe and its animation are untouched, with a test that proves it | Pass (6.5) |
| The photograph never leaves the machine; only coordinates go out, as today | Pass — REVERSE-style tools excluded for that reason; tile and tileset requests carry only map coordinates |
| Nothing claims "exact" without measured geometric evidence | Pass (ladder in 4.1) |
| Continual learning cannot learn from its own guesses or leak the benchmark | Pass (Section 5) |
| All storage is on E:, including PROJ's grid cache | Pass (Section 9) |

---

## Sources

Geolocation: GeoCLIP <https://github.com/VicenteVivan/geo-clip> · PIGEON <https://github.com/LukasHaas/PIGEON> ·
G3 <https://arxiv.org/abs/2405.14702>, <https://github.com/Applied-Machine-Learning-Lab/G3> ·
Img2Loc <https://arxiv.org/abs/2403.19584> · GeoRanker <https://arxiv.org/abs/2505.13731> ·
REVERSE <https://arxiv.org/abs/2605.26861> · GeoReasoner <https://github.com/lingli1996/GeoReasoner> ·
PLONK <https://arxiv.org/abs/2412.06781> (Tables 1–2), <https://github.com/nicolas-dufour/plonk>,
<https://huggingface.co/nicolas-dufour/PLONK_YFCC> · OSV-5M <https://arxiv.org/abs/2404.18873> (Tables 3, 6, 7; Appendix A.1),
<https://huggingface.co/datasets/osv5m/osv5m>, <https://huggingface.co/osv5m/baseline> ·
StreetCLIP <https://huggingface.co/geolocal/StreetCLIP> · Qwen3-VL <https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct>,
<https://ollama.com/library/qwen3-vl> · llama.cpp multimodal <https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md>

Matching and geometry: LightGlue <https://github.com/cvg/LightGlue> · RoMa <https://github.com/Parskatt/RoMa> ·
MASt3R <https://github.com/naver/mast3r> · VGGT <https://huggingface.co/facebook/VGGT-1B> ·
MegaLoc <https://github.com/gmberton/MegaLoc> · OrienterNet <https://github.com/facebookresearch/OrienterNet> ·
GeoCalib <https://github.com/cvg/GeoCalib> · MoGe <https://github.com/microsoft/MoGe> ·
Depth Anything V2 <https://huggingface.co/depth-anything> · Depth Pro licence <https://huggingface.co/apple/DepthPro> ·
BioCLIP 2 <https://huggingface.co/imageomics/bioclip-2> · GBIF API <https://api.gbif.org/v1/> ·
GlotLID <https://huggingface.co/cis-lmu/glotlid> · PP-OCRv5 <https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html> ·
pillow-heif licences <https://github.com/bigcat88/pillow_heif/blob/master/LICENSES_bundled.txt>

Data: Mapterhorn <https://mapterhorn.com/data-access/>, <https://download.mapterhorn.com/attribution.json> ·
GEBCO <https://www.gebco.net/data-products/gridded-bathymetry-data> · Microsoft footprints <https://github.com/microsoft/GlobalMLBuildingFootprints> ·
Open Buildings 2.5D <https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_Research_open-buildings-temporal_v1> ·
GlobalBuildingAtlas <https://github.com/zhu-xlab/GlobalBuildingAtlas> · Overture <https://docs.overturemaps.org/getting-data/cloud-sources/> ·
Meta canopy v2 <https://registry.opendata.aws/dataforgood-fb-forestsv2> · ESA WorldCover <https://registry.opendata.aws/esa-worldcover-vito/> ·
EOxCloudless licence <https://cloudless.eox.at/documentation/license> · FABDEM <https://gee-community-catalog.org/projects/fabdem/> ·
PLATEAU <https://wiki.openstreetmap.org/wiki/MLIT_PLATEAU> · NL 3D Tiles <https://data.overheid.nl/dataset/75647-3d-basisvoorziening--3d-tiles-gebouwen>,
<https://api.pdok.nl/kadaster/3d-basisvoorziening/ogc/v1/collections/gebouwen/3dtiles/tileset.json> ·
swisstopo terms <https://www.swisstopo.admin.ch/en/terms-and-conditions> · Panoramax <https://docs.panoramax.fr/federated-catalog/> ·
Mapillary API <https://www.mapillary.com/developer/api-documentation> · OpenAerialMap <https://api.openaerialmap.org/meta> ·
GeoNames <https://download.geonames.org/export/dump/readme.txt> · NASA GIBS <https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/1.0.0/WMTSCapabilities.xml> ·
Streets GL <https://wiki.openstreetmap.org/wiki/Streets_GL> · OSM2World <https://osm2world.org/> ·
loaders.gl 3D Tiles <https://www.npmjs.com/package/@loaders.gl/3d-tiles> · Brush <https://github.com/ArthurBrussee/brush> ·
Spark <https://github.com/sparkjsdev/spark> · Smithsonian GVP <https://volcano.si.edu/gvp_cite.cfm> ·
MarineCadastre AIS <https://www.fisheries.noaa.gov/inport/item/77594>

3D integration: EGM2008 geoid heights via GeographicLib GeoidEval <https://geographiclib.sourceforge.io/cgi-bin/GeoidEval> ·
PROJ-data grids <https://cdn.proj.org/> · CSP `'wasm-unsafe-eval'` <https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy/script-src> ·
Draco <https://github.com/google/draco> · meshoptimizer / `EXT_meshopt_compression` <https://github.com/zeux/meshoptimizer> ·
3D Tiles specification (heights on the WGS84 ellipsoid) <https://github.com/CesiumGS/3d-tiles>
