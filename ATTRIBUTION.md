# Attribution and licensing

Mapify is assembled entirely from openly licensed software, models and data.
Nothing here requires an account, an API key, a subscription or a payment, and
nothing here is used outside the terms its author set.

This file is the ledger. Every external component is listed with the licence
it is actually under, what obligation that licence puts on us, and how that
obligation is met. If you add a dependency, add it here.

"Free to download" is not the same as "free to use". Two entries below —
OpenStreetMap-derived data, and the models' training provenance — are the ones
worth reading properly before you rely on this tool for anything that matters.

---

## Mapify itself

Licensed under the MIT Licence — see [LICENSE](LICENSE).

That covers the code in this repository. It does **not** relicense the
components below, which keep their own terms.

---

## Models

| Component | Licence | Obligation | How it is met |
|---|---|---|---|
| **GeoCLIP** — location encoder and image-encoder head | MIT | Keep the notice; cite in academic use | Cited in `mapify/models/geoclip_locator.py` and below |
| **OpenAI CLIP ViT-L/14** — the vision backbone both GeoCLIP and the zero-shot evidence run on | MIT | Keep the notice | Model card is MIT; weights pulled from Hugging Face at first run |
| **PyTorch** | BSD-3-Clause | Keep the notice | Installed as a dependency, notice ships in the wheel |
| **Hugging Face Transformers** | Apache-2.0 | Keep the notice and NOTICE file | Installed as a dependency |
| **EasyOCR** (optional) | Apache-2.0 | Keep the notice | Optional; only loaded if the user installs it |
| **GlotLID** — language identification of the text read off a photograph (fastText model, about 2,000 languages), through **fasttext-wheel** (MIT) | Apache-2.0 **plus notices**: the authors make no representation about the licensing of the data it was trained on, and invite rights holders to ask for exclusion | Keep the licence and its notices (`models/glotlid/LICENSE`) | Downloaded once by `scripts/fetch_research_data.py glotlid`; the finding is reported in technique 12 with weight zero until measured (`models.language_id_enabled`) |
| **Unicode CLDR** — which countries speak each language, and how many of their people (`supplementalData.xml`, `supplementalMetadata.xml`) | Unicode License v3 | Keep the copyright and permission notice (`models/cldr/LICENSE`) | Downloaded once by `scripts/fetch_research_data.py cldr` and read locally |
| **c2pa-python** — reads and checks C2PA Content Credentials when a manifest is present (technique: provenance) | MIT **or** Apache-2.0 | Keep the notice | Installed with the user's approval; reports the generator, the signer and the signature's validity. A valid signature proves who signed, not that the picture is true, and the report says so |
| **PLONK** (YFCC checkpoint), Dufour et al., CVPR 2025 — a second, independent locator, with **DINOv2** (its encoder, via torch.hub) | PLONK code and weights MIT; DINOv2 Apache-2.0 | Keep the notices; cite in academic use | Downloaded once into the models directory (Hugging Face cache and `TORCH_HOME`). Only the YFCC checkpoint: the OpenStreetView-5M checkpoints load StreetCLIP, which is non-commercial. Off until measured (`models.plonk_enabled`) |
| **DISK** (Tyszkiewicz et al., NeurIPS 2020) features and **LightGlue** (Lindenberger et al., ICCV 2023) matching, through **kornia** — Pinpoint's second matcher for SIFT's close calls | DISK code and weights Apache-2.0; LightGlue code and its DISK weights Apache-2.0; kornia Apache-2.0 | Keep the notices | Weights downloaded once into `TORCH_HOME` in the models directory. Never SuperPoint (its weights are non-commercial). Confirms by its own measured thresholds; off until measured (`network.pinpoint_second_matcher`) |
| **pi-heif** (optional) — reads HEIC/HEIF photographs | Code BSD-3-Clause; its wheels bundle libheif and libde265, both LGPL-3.0, and no encoder | Keep the notices; libheif and libde265 stay separate, replaceable DLLs (LGPL) | Optional; only loaded if the user installs it. HEVC, the codec inside HEIC, is covered by patents in some countries, which is why it is not installed by default. Not `pillow-heif`: its wheels carry the GPL-2.0 x265 encoder |

GeoCLIP citation:

> Vicente Vivanco Cepeda, Gaurav Kumar Nayak, Mubarak Shah.
> *GeoCLIP: Clip-Inspired Alignment between Locations and Images for Effective
> Worldwide Geo-localization.* NeurIPS 2023.

### What the model licences do and do not settle

The MIT licence on GeoCLIP's weights covers **our use of the weights**. It says
nothing about the provenance of the imagery the model was trained on, which is
a separate question the authors addressed in their paper and which no
downstream licence can resolve for you. If your use is one where training-data
provenance matters — evidence, journalism, anything adversarial — read the
paper rather than relying on this file.

---

## Geographic data

| Source | Licence | Obligation | How it is met |
|---|---|---|---|
| **Natural Earth** — admin-0 countries, admin-1 states/provinces, populated places, 110m coastlines | Public domain | None; attribution requested as a courtesy | Credited in the application's status bar and on the globe |
| **OpenStreetMap** via **Nominatim** (reverse geocoding) | Data: ODbL 1.0 | Attribute; respect the usage policy | See below |
| **OpenStreetMap** via **Overpass** (nearby named features) | Data: ODbL 1.0 | Attribute; respect the usage policy | **Off by default** since the map work — see below |
| **OpenStreetMap regional extracts** from **Geofabrik** (`download.geofabrik.de`), cut from the planet file | Data: ODbL 1.0 | Attribute; share-alike on derived databases | Imported into a local store on the user's machine (`scripts/import_osm.py`), never redistributed; attribution on the map and in every export |
| **OpenFreeMap** vector tiles (`tiles.openfreemap.org`), OpenStreetMap in the OpenMapTiles schema | Data: ODbL 1.0. Service: free, no key, "no limits on the number of map views or requests" | "OpenFreeMap © OpenMapTiles Data from OpenStreetMap" | Credited on the 2D map, whose roads, boundaries and names come from these tiles; each tile fetched once through Mapify and kept. The labels' fonts (**Noto Sans**, SIL Open Font License 1.1) come from the same host the same way |
| **Google Research Open Buildings v3** — 1.8 billion footprints, read from the FlatGeobuf copy on Source Cooperative | CC BY 4.0 **or** ODbL 1.0, the user's choice; used here under CC BY 4.0 | Attribute | "Buildings: OpenStreetMap; Google Open Buildings (CC BY 4.0)" on the 2D map and in every export |
| **timezone-boundary-builder** data, used by `timezonefinder` | Data: ODbL 1.0 (derived from OSM) | Attribute | Credited in every report that carries a timezone |
| **GeoNames** — `allCountries` and `alternateNamesV2` (`download.geonames.org`), indexed locally as settlements of at least 1,000 people and countries and regions | **CC BY 4.0** | Attribute "GeoNames geographical database, geonames.org" | Downloaded once by `scripts/fetch_research_data.py geonames` into the models directory and indexed by `python -m mapify.geo.geonames`; consulted only when Natural Earth knows no place by a name read off a sign, and only with `models.geonames_enabled` (off until measured). Credited wherever a match it supplied is shown |
| **OpenStreetView-5M** (`huggingface.co/datasets/osv5m/osv5m`) — street-level photographs and where they were taken | **CC BY-SA 4.0**; imagery by Mapillary contributors | Attribute; share-alike on anything redistributed from it | `scripts/fetch_research_data.py osv5m-csv osv5m-shards ...` and `scripts/build_retrieval.py` build a local index of CLIP embeddings and positions in the models directory; the photographs can be deleted afterwards, and nothing derived from it is redistributed. Used only with `models.retrieval_enabled` (off until measured); credited "Street-level retrieval: OpenStreetView-5M (CC BY-SA 4.0), imagery by Mapillary contributors" |
| **OpenStreetMap raster tiles** (optional ground detail) | Data: ODbL 1.0; tiles: OSMF usage policy | Attribute; respect the tile policy | Off by default; see below |

### The 2D map, and where its footprints come from

Zooming past the globe's closest view — or pressing "View on the map" —
opens a flat satellite map of the place with its building footprints drawn
as outlines. Three sources, in order of trust:

1. **An imported OpenStreetMap region.** The richest: every `height` and
   `building:levels` tag, and a record of which heights were measured. The
   Southern India extract holds 7,439,863 buildings, of which 10,832 carry a
   measured height and 53,652 a floor count — **under one percent** between
   them. (Heights are kept in the store for the exports; the map draws
   footprints flat.)
2. **OpenFreeMap tiles**, for everywhere without an import. OpenMapTiles
   folds OSM's height information into one number (`render_height`: the
   height tag, else levels × 3.66 m, else 5 m) and does not say which.
3. **Google Open Buildings**, where OSM is thin. Machine detections with a
   confidence score and no height, type or name; outlined in a colour of
   their own and dropped wherever a mapped building already stands.

The local store is a **derivative database** of OpenStreetMap under ODbL
1.0. It is built on the user's machine and not distributed by this project.
A GeoPackage or kepler.gl file exported from it carries the same obligations,
and carries a `notice` naming every source and licence so they travel with
it.

**The 2D map** draws these as they are: roads, boundaries and names from
the OpenFreeMap tiles, building footprints from the local store (OSM and
Open Buildings) as flat outlines. Nothing is extruded and nothing is
invented.

### Exports for QGIS and kepler.gl

Mapify writes files these tools open; it bundles neither. **QGIS** (GPL) is
desktop software the user installs themselves — the GeoPackage opens in it
styled, with a 2.5D building renderer, and "Open in QGIS" launches an
installed copy. **kepler.gl** (MIT) is a web application — the exported map
file is dropped onto it. GeoPackage is written with the standard library to
the OGC specification (12-128r18) and read back by GDAL in the tests.

### ODbL, and what it means for your output

Nominatim and Overpass results are OpenStreetMap data under the
[Open Database Licence 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
That licence attaches to the **data**, and it travels:

- **Attribution.** Any public use of a Mapify report that contains OSM-derived
  place names, road numbers or nearby features must credit
  "© OpenStreetMap contributors". The application does this on screen and in
  every saved report.
- **Share-alike on derived databases.** If you build a *database* from Mapify
  output that contains OSM data, that database is subject to ODbL. A single
  report shown to a person is a "produced work" and is not; a table of
  thousands of geocoded results is a derived database and is.

If neither obligation suits you, run with `offline_only = true`. Natural Earth
is public domain and carries neither.

### The OpenStreetMap usage policies

These are conditions of access to donated infrastructure, not suggestions. The
code enforces the parts that are ours to enforce, and the enforcement is in
one place so it can be audited:

**Nominatim** ([policy](https://operations.osmfoundation.org/policies/nominatim/))
— `mapify/geo/nominatim.py`:

- an identifying `User-Agent` on every request;
- a hard floor of one request per second, held by a lock shared across
  threads rather than trusted to call sites;
- an on-disk cache, so re-analysing an image costs nothing;
- a budget of six lookups per analysis, so one image cannot fan out into a
  crawl;
- no bulk or systematic querying, and no use as a general geocoding backend.

**Overpass** ([policy](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html))
— **off by default.** The policy names "setting up an app ... and relying on
the public instances as backend" as the thing not to do, and one query per
analysis was exactly that. Nearby landmarks now come from local map data
(`mapify/geodata/nearby.py`): an imported region, or OpenFreeMap tiles
fetched once and kept. `network.overpass_enabled` remains for anyone who
runs their own instance; when on, `mapify/geo/landmarks.py` still sends one
cached, rate-limited query per analysis.

**Geofabrik** extracts are downloaded by hand or by the user's choice, once
per region, with the published MD5 checked; the regional files are the
intended distribution of the planet data for exactly this kind of use.

**Source Cooperative** serves the Open Buildings FlatGeobuf files from a
public bucket. They are read by HTTP range request — the access pattern the
format and the host are built for — so a landing fetches the few hundred
kilobytes around it, not the file. The envelope of every file is shipped
in `mapify/data/open_buildings_index.json` (read once from their headers)
so no landing has to ask 333 files where they are.

**Tile usage** ([policy](https://operations.osmfoundation.org/policies/tiles/))
— `mapify/web/js/detail.js`: **off by default.** When switched on, a landing
fetches at most a 4×4 tile grid, cached per coordinate, only once the camera
is close enough to need it. This is a personal-scale tool; do not point it at
the OSMF tile servers at volume. Set `network.basemap_tile_url` to your own
tile source if you need more.

---

## Earth imagery

| Source | Terms | How it is met |
|---|---|---|
| **NASA Blue Marble Next Generation** (December 2004, topography and bathymetry) — Reto Stockli, NASA Earth Observatory | NASA imagery is generally not copyrighted; a credit line is requested | Credited on the globe and in `mapify/web/textures/CREDITS.txt` |
| **NASA Earth at Night 2012** (Black Marble) — NASA Earth Observatory / NOAA NGDC | As above | As above |
| **NASA Blue Marble cloud composite** | As above | As above |
| **GEBCO_08 elevation and bathymetry**, rendered by NASA Earth Observatory | NASA rendering: as above. The underlying GEBCO grid is © GEBCO/BODC and free for non-commercial use with attribution | Credited; used only as a greyscale relief channel |
| **Esri World Imagery** through the **ArcGIS Location Platform** — the 2D map, worldwide, **only with the owner's own API key** | ArcGIS Location Platform terms; free tier of 2,000,000 basemap tiles a month at the time of writing; attribution required | Used only when the owner has created a key himself and saved it in Mapify; credited "Esri, Maxar, Earthstar Geographics, and the GIS User Community" whenever its tiles are drawn. Tiles are passed through, never stored; the key stays in config.toml and never reaches the page |
| **Mapbox Satellite** through the Raster Tiles API — the 2D map, worldwide, **only with the owner's own access token** | Mapbox terms of service; free monthly allowance on the owner's own account | As above, credited "(c) Mapbox (c) Maxar" |
| **USGS The National Map orthoimagery** (`USGSImageryOnly`, USDA NAIP) — the 2D map, United States only | Public domain; USGS requests the credit "Map services and data available from U.S. Geological Survey, National Geospatial Program." | Credited on the map whenever its tiles are drawn; it returns nothing outside the US |
| **IGN BD ORTHO** through the Geoplateforme (`data.geopf.fr`) — the 2D map, France and its overseas departments | **Etalab Open Licence 2.0**; the Geoplateforme's general terms of use (cartes.gouv.fr/cgu) | Credited "IGN - BD ORTHO" whenever its tiles are drawn; offered only for a place in France |
| **Beeldmateriaal Nederland** "Actueel" aerial photographs through PDOK — the 2D map, the Netherlands | **CC BY 4.0**; PDOK states no fees and no access constraints | Credited "Beeldmateriaal Nederland (Kadaster), through PDOK"; offered only in the Netherlands |
| **swisstopo SWISSIMAGE** (`wmts.geo.admin.ch`) — the 2D map, Switzerland and Liechtenstein | Swiss federal open government data: free use with the source credited | Credited "(c) swisstopo - SWISSIMAGE"; offered only in Switzerland and Liechtenstein |
| **PNOA** through IGN Espana (`ign.es/wmts/pnoa-ma`) — the 2D map, Spain | **CC BY 4.0** (scne.es), as the service's own metadata states | Credited "PNOA cedido por (c) Instituto Geografico Nacional de Espana"; offered only in Spain. Over Portugal and the sea it answers with a flat fill, which is recognised and not drawn |
| **GSI seamless aerial photographs** (`cyberjapandata.gsi.go.jp`) — the 2D map, Japan | The GSI content terms of use (compatible with CC BY 4.0), with the credit "Chiriin tiles" (GSI tiles) | Credited on the view; offered only in Japan |
| **Landsat WELD** annual composite via GIBS — the 2D map everywhere else | Public domain; GIBS: Fees none, AccessConstraints none; Landsat courtesy of the USGS | Credited on the map. 30 m a pixel: the map says buildings are not visible in it and suggests a key |

USGS **EarthExplorer** itself needs a personal account for downloads. Mapify
does not handle anyone's credentials, so Landsat is reached through NASA
GIBS, which serves the same public-domain data without one. Every imagery
tile is fetched by the application and kept under the data folder, so a
place seen once can be seen again offline and no tile is requested twice.

See <https://www.nasa.gov/nasa-brand-center/images-and-media/>. NASA does not
endorse this project, and the NASA name and logos are not used.

Textures are downloaded by `scripts/fetch_textures.py`, which records a
SHA-256 for each file in `data/MANIFEST.json` and writes the credit line
alongside them. They are optional: without them the globe renders from Natural
Earth vectors instead.

---

## Weather, air and terrain records (the time and terrain techniques)

| Source | Terms | How it is met |
|---|---|---|
| **Open-Meteo** historical weather API — the **ERA5** reanalysis (Copernicus Climate Change Service) — techniques 61, 95, 96 | Free for **non-commercial** use, no key, under 10,000 calls a day; data **CC BY 4.0**, credited as "Weather data by Open-Meteo.com" | Credited in every finding that uses it. One request a second at most, every answer cached on disk, so re-analysing an image asks nothing |
| **Open-Meteo** air-quality API — **CAMS** (Copernicus Atmosphere Monitoring Service), from 2013 — technique 97 | As above | As above |
| **Open-Meteo** elevation API — the **Copernicus DEM GLO-90**, © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA — techniques 127, 128 | As above for the service; the DEM's own credit line is required | The full DEM credit is carried by both findings |

Only a coordinate and a date go out — the leading candidate's, never the
photograph and never anything read from it. A **commercial** user of Mapify
needs Open-Meteo's paid plan; `network.weather_history_enabled = false`
switches all three off, and the techniques then say "no data" rather than
guess.

## Regional facts (techniques 111-131)

`mapify/data/regional.py` — ITU calling codes, country-code top-level
domains, currency signs, mile-using countries, postcode shapes, Indian
registration state codes, where a handful of brands operate, and sign
conventions — is a table of public facts typed in by hand. None of it is
copied from a dataset, and its coverage is partial: a missing entry proves
nothing, only a match is evidence.

## Case files (technique 152)

Each analysis writes a case file to `data/cases/` on this machine: the
findings, the prediction and the provenance record (input hash, models,
data sources, versions). It never contains the image. The index beside the
case files holds, per image, the estimate, a 64-bit difference hash and the
encoder's embedding, so a later analysis can recognise the same photograph.
`records.case_files = false` keeps no record at all.

## Pinpoint: reference photographs

Pinpoint runs only when you press **Pinpoint exact spot**. It compares your
photograph, on this machine, with geotagged photographs taken near the
analysis's candidates, and adopts a location only when enough points match
under one pair of camera positions (see `mapify/pinpoint/engine.py` for the
measured thresholds).

| Source | Terms | How it is met |
|---|---|---|
| **Wikimedia Commons** — the MediaWiki Action API (geo search, image details) and thumbnails from upload.wikimedia.org | Each photograph is under **its own free licence** (CC0, CC BY, CC BY-SA, public domain...), chosen by its author; Commons hosts no other kind. The [Wikimedia robot policy](https://wikitech.wikimedia.org/wiki/Robot_policy) and User-Agent policy govern automated access | Only files whose licence Mapify recognises as free are used. The matching photograph is always shown with its **author, licence and description page**. Requests carry a User-Agent naming the project and its repository; API calls go one at a time, a second apart, waiting as asked when told to slow down; thumbnails are fetched at a standard width (960 px), at most two at once |
| **Wikidata** — the notable landmarks near each candidate, through the Wikidata Query Service | Data **CC0**; the query service's User-Agent policy and usage limits | One query per area, with the project's User-Agent, one at a time and cached for 30 days. Only the landmark's names and its Commons category are used, to search Commons for photographs of it; nothing about the photograph is sent |
| **Panoramax** — street-level pictures through the federated API at api.panoramax.xyz (IGN, OpenStreetMap France and other instances) | Each picture under **Etalab Open Licence 2.0** or **CC BY-SA 4.0**, as its instance publishes it; no account, no key | Only pictures under those licences (or CC BY / CC0) are used, and a match is shown with its **producer, licence and Panoramax record**. Requests carry the project's User-Agent and share the one-at-a-time, one-a-second pace of the Commons calls; three box searches per area, cached for 30 days. `network.pinpoint_panoramax = false` switches it off |
| **Mapillary** (optional) — street-level images through the Mapillary API | Imagery **CC BY-SA 4.0**; API use under Mapillary's terms, with **your own** free access token | Off unless you put a token in `network.mapillary_token`. The token stays in your config file and is never part of a cache key or written anywhere else. Matches are credited to their contributor |

Only coordinates and a radius go out, never the photograph and never
anything read from it. Search answers are cached for 30 days, and the
thumbnails and their matching features are kept under `data/pinpoint/`
(git-ignored), capped by `network.pinpoint_cache_mb` (2 GB by default, the
files used longest ago removed first). Nothing from them is redistributed.
`network.pinpoint_enabled = false` switches the search off.

---

## Software

| Component | Licence |
|---|---|
| **three.js** — the 3D globe | MIT |
| **pywebview** — the native window | BSD-3-Clause |
| **pythonnet** — the WebView2 bridge on Windows | MIT |
| **Microsoft Edge WebView2 Runtime** | Microsoft proprietary, redistributable; ships with Windows 10/11 — we use it, we do not bundle or modify it |
| **FastAPI** | MIT |
| **Starlette** | BSD-3-Clause |
| **Uvicorn** | BSD-3-Clause |
| **Pillow** | MIT-CMU (HPND) |
| **NumPy** | BSD-3-Clause |
| **pyshp** | MIT |
| **timezonefinder** | MIT (code) |
| **httpx** | BSD-3-Clause |
| **MapLibre GL JS** — the 2D map | BSD-3-Clause |
| **pyosmium** — reading OpenStreetMap extracts (optional; imports only) | BSD-2-Clause; **libosmium** inside it: Boost Software License 1.0 |
| **pytest** | MIT |
| **pyogrio** / **GDAL** — test-only independent reader for GeoPackage and FlatGeobuf | MIT / MIT-X11 |

three.js and MapLibre GL JS are vendored into `mapify/web/vendor/` by
`scripts/fetch_data.py`, which also fetches each `LICENSE` file, unmodified.
MapLibre is pinned to a version and checked against a SHA-256 before it is
kept: it is a script the page executes.

The FlatGeobuf and vector-tile readers (`mapify/geodata/fgb.py`,
`mapify/geodata/mvt.py`) are written from the public specifications
(flatgeobuf.org, BSD-2-Clause; the Mapbox Vector Tile specification 2.1,
CC BY 3.0) with no code copied.

---

## Test inputs

`scripts/fetch_samples.py` fetches geotagged photographs from Wikimedia
Commons for local accuracy measurement. It only accepts files whose licence is
CC0, CC BY, CC BY-SA or public domain, and it records each file's licence,
author and source page in `samples/manifest.json`.

Those files are **not** redistributed by this repository — `samples/` is
git-ignored. They are inputs you fetch for your own evaluation, and if you
republish anything derived from them, the photographer's licence applies to
you, not to us.

Wikimedia's API is used with a descriptive User-Agent and a delay between
requests, per their
[etiquette](https://www.mediawiki.org/wiki/API:Etiquette).

---

## What is deliberately absent

No Google Maps, Places, Street View or Vision API. No HERE, no Bing. Esri
or Mapbox imagery only if the owner creates a key of his own and saves it --
never required, and nothing else of theirs is used. No commercial geolocation or reverse-image service. No paid model
endpoint. No proprietary gazetteer. No scraped imagery, no leaked dataset, no
dataset whose terms forbid this use.

Nothing in Mapify bypasses authentication, a paywall or an access control, and
nothing scrapes a site whose terms prohibit it.

---

## Reporting a licensing problem

If something here is wrong — a licence misstated, an attribution missing, a
component whose terms this use does not actually permit — please open an issue
on the repository. A licensing error is a bug, and it will be treated as one.
