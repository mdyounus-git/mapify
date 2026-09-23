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
| **OpenStreetMap** via **Overpass** (nearby named features) | Data: ODbL 1.0 | Attribute; respect the usage policy | See below |
| **timezone-boundary-builder** data, used by `timezonefinder` | Data: ODbL 1.0 (derived from OSM) | Attribute | Credited in every report that carries a timezone |
| **OpenStreetMap raster tiles** (optional ground detail) | Data: ODbL 1.0; tiles: OSMF usage policy | Attribute; respect the tile policy | Off by default; see below |

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

**Overpass** ([policy](https://dev.overpass-api.de/)) —
`mapify/geo/landmarks.py`: one query per analysis, cached, rate-limited, and
abandoned quietly on failure rather than retried.

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

See <https://www.nasa.gov/nasa-brand-center/images-and-media/>. NASA does not
endorse this project, and the NASA name and logos are not used.

Textures are downloaded by `scripts/fetch_textures.py`, which records a
SHA-256 for each file in `data/MANIFEST.json` and writes the credit line
alongside them. They are optional: without them the globe renders from Natural
Earth vectors instead.

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
| **pytest** | MIT |

three.js is vendored into `mapify/web/vendor/` by `scripts/fetch_data.py`,
which also fetches its `LICENSE` file into the same directory, unmodified.

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

No Google Maps, Places, Street View or Vision API. No Mapbox, no HERE, no
Bing. No commercial geolocation or reverse-image service. No paid model
endpoint. No proprietary gazetteer. No scraped imagery, no leaked dataset, no
dataset whose terms forbid this use.

Nothing in Mapify bypasses authentication, a paywall or an access control, and
nothing scrapes a site whose terms prohibit it.

---

## Reporting a licensing problem

If something here is wrong — a licence misstated, an attribution missing, a
component whose terms this use does not actually permit — please open an issue
on the repository. A licensing error is a bug, and it will be treated as one.
