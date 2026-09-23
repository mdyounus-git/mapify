<div align="center">

# Mapify

**Work out where a photograph was taken — on your own machine, with open models and open data, for nothing.**

A Windows desktop application. Open an image, and Mapify estimates where it was
taken, shows you the evidence it used, and flies a 3D Earth to the coordinate.

</div>

---

## What it actually does

You give it a photograph. It gives you a coordinate, a place name, a confidence
band, and — the part that matters — the reasoning:

```
Estimated location   Bengaluru, Karnataka, India
                     High confidence  ·  ±2.0 km

Latitude             12.973763
Longitude            77.590963
Suburb               Sampangirama Nagar
Postcode             560001
Timezone             Asia/Kolkata (UTC+5.5) · 19:34 local

Detected evidence    South Asia                    51%
                     Auto-rickshaws                47%

How it scored        country prior     +0.48   CLIP favours India (57%)
                     architecture      +0.25   South Asian vs Southern Asia
                     world region      +0.26   South Asia vs Southern Asia

Diagnostics          locator mass 40.1%  ·  concentration 100%
                     dispersion 0 km     ·  evidence +0.99
```

And when it cannot tell, it says so instead of inventing coordinates. That is
the whole design principle, and there is a benchmark below that holds it to it.

---

## Accuracy, measured

`scripts/evaluate.py` runs the pipeline over geotagged, freely-licensed
photographs from Wikimedia Commons and reports the error. Eleven samples across
six continents, offline mode, OCR off:

| Band | Threshold | Hit rate |
|---|---|---|
| Street | ≤ 1 km | 5/11 — 45% |
| City | ≤ 25 km | 8/11 — 73% |
| Region | ≤ 200 km | 9/11 — 82% |
| Country | ≤ 750 km | 9/11 — 82% |

Median error **1.8 km**.

The number that matters more is the calibration:

| Stated confidence | n | Median error | Worst error |
|---|---|---|---|
| high | 7 | 0.5 km | 12.7 km |
| medium | 1 | 190 km | 190 km |
| low | 2 | 12,514 km | 12,514 km |
| insufficient | 1 | 11,783 km | 11,783 km |

Both catastrophic misses — Cairo read as Argentina, Nairobi as Colombia — were
labelled `low` and `insufficient`. Nothing labelled `high` was worse than 13 km
out. **The tool is allowed to be wrong. It is not allowed to be confidently
wrong.**

Reproduce it yourself:

```bash
python scripts/fetch_samples.py
python scripts/evaluate.py --offline
```

### Known weakness

GeoCLIP's training distribution shows: tropical and Sub-Saharan African scenes
are frequently misread as Latin America. Evidence fusion re-ranks around this
when the image gives it something to work with, and the confidence band catches
it when it does not, but the underlying bias is real and you should know about
it before trusting a result from those regions.

---

## Install

You need Windows 10 or 11 and Python 3.11–3.13. Everything else is fetched by
the setup script.

```bash
git clone https://github.com/mdyounus-git/mapify.git
cd mapify
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
```

That creates a virtual environment, installs CPU PyTorch and the rest, and
downloads the open geodata (Natural Earth, ~91 MB), the vendored front-end
libraries (three.js) and the Earth textures (NASA, ~20 MB at 8k).

Then:

```bash
.\Mapify.bat
```

**First launch downloads about 1.7 GB of model weights** from Hugging Face —
GeoCLIP and the CLIP ViT-L/14 backbone it runs on. After that the application
works with no network at all.

The first warm-up also encodes GeoCLIP's 100,000-point GPS gallery, which takes
a few minutes on CPU. It is cached to disk, so it happens once, ever. Analyses
after that take **about three seconds each**.

If something goes wrong, `Mapify-console.bat` runs the same application with a
visible console and a log.

### Optional: read text on signs

```bash
.venv\Scripts\pip install easyocr
```

Text is the strongest clue a photograph can carry, because a place name can be
matched against the gazetteer and *checked* rather than guessed. EasyOCR pulls
about 300 MB of dependencies, which is why it is not installed by default.

Mapify picks the recogniser to load from the writing system the zero-shot pass
identified, for that image only. This matters more than it sounds: an
English-only recogniser does not fail on a Cyrillic sign, it returns Latin
lookalikes. On a rendered Moscow street sign:

```
english only : MOCKBA                      -> scored as Latin 100%
with ru+en   : МОСКВА | Тверская улица     -> Cyrillic 53%, Latin 47%
               TOPONYM 'МОСКВА' -> Moscow (NAME_RU, Russia)
```

The first reading is worse than no reading, because "100% Latin" is evidence
*against* Russia. The second closes the whole chain: script detected, right
reader loaded, sign read, gazetteer matched by its Russian name.

The same applies within Latin script — the English model flattens Turkish
`İ ğ ş` to ASCII, losing exactly the characters that identify the language — so
a peaked country prior can also select a Latin-language recogniser.

On the eleven-sample benchmark OCR changed no verdict: the images it read
(Turkish road signs in Istanbul, Kannada on the Bengaluru metro) were already
placed correctly, and the two failures have no legible text at all. It earns
its place on images that carry a readable place name, not as a general
accuracy boost, and the benchmark says so rather than implying otherwise.

---

## How it works

```
   image
     │
     ├─ decode ─────────── format checked from the bytes, never the extension
     │
     ├─ EXIF ───────────── read, and held apart as a claim, not an answer
     │
     ├─ GeoCLIP ────────── 12 candidate coordinates from a 100k-point gallery,
     │                     plus the CLIP embedding it already computed
     │
     ├─ zero-shot ──────── that same embedding scored against a prompt bank:
     │                     traffic side, signage script, world region,
     │                     architecture, biome, snow, road markings, utility
     │                     poles, plate class, vehicle mix, terrain, settlement
     │                     — and against all 239 countries, for a second opinion
     │
     ├─ OCR ────────────── if installed; feeds script detection and gazetteer
     │                     matching in ~30 languages
     │
     ├─ fusion ─────────── candidates re-ranked in log space, every adjustment
     │                     recorded with its reason
     │
     └─ enrichment ─────── Natural Earth always; Nominatim and Overpass only if
                           you allowed them
```

### What the confidence band is built from

Three independent things, all reported in the UI so you can argue with them:

- **Locator mass** — how much of its full distribution GeoCLIP committed to its
  shortlist at all. A tidy top-12 holding 1% of the probability means it
  recognised nothing in particular.
- **Concentration and dispersion** — how tightly that mass clusters.
- **Evidence support** — whether the rest of the image agrees. Evidence that
  contradicts the leading candidate forces a downgrade and says so.

An earlier version normalised probability across the returned candidates, which
made a single merged cluster trivially "100%" and therefore always high
confidence. That is exactly the failure this design exists to prevent, and the
test suite now pins it down.

### EXIF is never the answer

A GPS tag is what the writer of the file recorded. It is trivially editable,
frequently survives re-encoding, and is routinely wrong. Mapify reads it, shows
it as its own purple-marked candidate, resolves it to a place — and keeps it
entirely out of the estimate. If the two disagree, the report says so and gives
the distance:

> **Disagreement** — 9,240 km from the image estimate. The embedded GPS tag
> disagrees with what the image looks like. One of them is wrong, and the tag is
> the easier of the two to fake.

It also flags the tamper signals it can actually check: Null Island, a `MANUAL`
processing method, editor software tags, coordinates rounded to ~100 m on both
axes, a non-WGS-84 datum, and capture times that differ from the GPS time by
more than any real time zone.

---

## The globe

three.js, rendered locally, no tile server required.

The thing that makes a globe look like a sticker is lighting it from behind the
camera. Mapify computes the real subsolar point from a date, so there is an
actual terminator in an actual place, and everything else follows: atmospheric
scattering that goes orange along the terminator, clouds lit by the same sun
casting a soft shadow on the surface, city lights on the dark side in the colour
of sodium lamps, relief from GEBCO elevation, and bloom thresholded above white
so lights bleed but daylight does not.

Three modes for lighting — lit for the target, the real sun right now, or flat —
and two for the globe: NASA satellite imagery, or a vector "recon grid" drawn at
runtime from the Natural Earth coastlines.

Selecting a candidate arcs the camera out through orbit along a great circle and
descends onto the coordinate. It **will not descend below what the imagery can
support** — 8k of Blue Marble is 4.9 km per texel, and flying closer just
magnifies blur. The HUD states the resolution and flags when you have zoomed
past it. Enable ground tiles in `config.toml` if you need to go lower.

---

## Free, and legally so

No paid API, no key, no account, no subscription, nowhere. Every model, dataset
and library is openly licensed, and **[ATTRIBUTION.md](ATTRIBUTION.md) is the
ledger** — each component with its actual licence, the obligation that licence
creates, and how this project meets it.

Two are worth reading before you rely on this:

- **OpenStreetMap data** (place names, nearby features, time-zone boundaries) is
  ODbL. It must be attributed, and a *database* you build from Mapify output
  that contains it is itself subject to ODbL. Run with `offline_only = true` if
  neither suits you — Natural Earth is public domain and carries neither.
- **NASA imagery** is not copyrighted but asks for a credit line, which the
  application displays on the globe. NASA does not endorse this project.

Nothing here bypasses authentication, a paywall or an access control. Nothing
scrapes a site whose terms forbid it. No pirated datasets, no leaked databases.

---

## Privacy

The image is decoded, analysed and discarded in memory. It is never written to
disk and never sent anywhere — not to a model host, not to a geocoder, not to
anyone. What can leave the machine is at most a *coordinate*, and only if you
left Nominatim or Overpass enabled.

`offline_only = true` stops even that. The status bar always says which mode you
are in.

There is no telemetry, no analytics, no crash reporting and no account.

The analysis service the window talks to binds to loopback on a port the OS
picks at launch, behind a random per-session token that is never written to
disk. Other programs on the machine — including a browser you open by hand —
cannot reach it. A file on disk is only readable if you chose it in the
application's own Open dialog.

---

## Responsible use

This locates *places*, not people, and it is built from published research and
public data — but it is still a capability worth thinking about before you point
it at someone else's photograph. Geolocating an image a person shared without
expecting it to be traced can put them at real risk.

Use it on imagery you have a reason to be working with: your own, your
organisation's, material you are verifying as a journalist or researcher, or a
public-interest investigation. Do not use it to find or follow an individual.

Treat every output as an estimate. The confidence band is there to be believed,
including when it says the evidence will not support a location.

---

## Development

```bash
.venv\Scripts\python -m pytest              # 193 tests
.venv\Scripts\python -m mapify.server       # the service without the window
.venv\Scripts\python -m mapify --debug      # the app with devtools open
```

Tests run against deterministic stand-in models, so the whole analysis chain is
exercised in seconds with no weights loaded.

| Path | What lives there |
|---|---|
| `mapify/desktop.py` | the native window and the Python/JS bridge |
| `mapify/server.py` | the local analysis service and its security boundary |
| `mapify/pipeline.py` | the seven analysis stages, in order |
| `mapify/models/` | GeoCLIP, the CLIP backbone, zero-shot evidence, OCR |
| `mapify/evidence/` | the prompt bank, script detection, fusion |
| `mapify/geo/` | offline Natural Earth, Nominatim, Overpass, time zones |
| `mapify/web/` | the interface and the globe |
| `scripts/` | data fetchers, the icon, the benchmark |

---

## Licence

MIT for the Mapify source — see [LICENSE](LICENSE).

The models, data and libraries keep their own terms. See
[ATTRIBUTION.md](ATTRIBUTION.md), and read it before redistributing anything
built on this.
