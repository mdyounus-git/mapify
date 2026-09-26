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
photographs from Wikimedia Commons. **163 samples**, six continents, offline
mode, OCR off. The sampling frame is in `scripts/sample_sites.py` and
deliberately over-samples Africa and South America, because that is where the
model is known to be weak.

| Band | Threshold | Hit rate |
|---|---|---|
| Street | ≤ 1 km | 30/163 — 18% |
| City | ≤ 25 km | 61/163 — 37% |
| Region | ≤ 200 km | 74/163 — 45% |
| Country | ≤ 750 km | 97/163 — 60% |
| Continent | ≤ 2500 km | 113/163 — 69% |

Median error **368 km**.

That is a much weaker result than an earlier eleven-sample set suggested
(45% street-level, median 1.8 km). The eleven samples were mostly landmarks in
well-photographed cities. They were not representative, and the numbers they
produced were flattering rather than wrong-by-accident. This is what the tool
actually does.

### The number that matters

| Stated confidence | n | Median | Worst | ≤200 km | ≤1500 km |
|---|---|---|---|---|---|
| **high** | 20 | **1.2 km** | **106 km** | **100%** | 100% |
| medium | 54 | 15.5 km | 15,110 km | 63% | 89% |
| low | 51 | 658 km | 16,854 km | 37% | 63% |
| insufficient | 38 | 6,985 km | 16,214 km | 3% | 16% |

**When Mapify says high confidence, all 20 such calls landed within 106 km.**
The bands fall monotonically from there, so they carry real information.

Only `high` makes a promise. `medium` is reported with its measured rate —
89% within 1500 km — because threshold fitting showed that is its ceiling on
this data, and promising a distance it cannot keep would be the exact failure
this project exists to avoid.

Those thresholds are not guesses. They were fitted by
`scripts/tune_confidence.py`, which searches on half the samples and validates
on the half it never saw. The chosen rule scored 100% precision on both halves.
[Phase 13](PHASES/Implemented%20Phases/PHASE-13-wider-benchmark.md) records how,
including the version that got it wrong.

### Where it is weak, stated plainly

| Region | n | ≤25 km | Median |
|---|---|---|---|
| North America | 19 | 58% | 15 km |
| Oceania | 9 | 56% | 14 km |
| Asia | 43 | 47% | 50 km |
| Europe | 39 | 38% | 192 km |
| South America | 21 | 29% | 723 km |
| **Africa** | **32** | **12%** | **2,396 km** |

| Scene | n | ≤25 km | Median |
|---|---|---|---|
| landmark | 11 | 82% | 0.1 km |
| rural | 12 | 42% | 71 km |
| street | 130 | 34% | 561 km |
| coastal | 10 | 30% | 1,158 km |

Two things to take from this. **GeoCLIP is substantially worse in Africa** —
tropical and Sub-Saharan scenes are frequently read as Latin America, and the
headline number hides it. And **landmarks are the easy case**: 82% within
25 km against 34% for ordinary streets. Any geolocation benchmark built from
tourist photography will look better than the tool really is.

Reproduce all of it:

```bash
python scripts/fetch_samples.py      # 163 geotagged CC/PD photographs
python scripts/evaluate.py --offline
python scripts/tune_confidence.py    # refit and validate the thresholds
```

## Install

### As an application

Build it, then install it for your user. No administrator, and nothing on the
system drive:

```bash
.venv\Scripts\python build_exe.py
```

```bash
powershell -ExecutionPolicy Bypass -File installer\install.ps1 -Desktop
```

Everything Mapify owns goes under **one root, on the same drive as the
project** — an `app` folder beside it, unless you pass `-Root`:

```
<project>\app\           the program
<project>\app\data\      caches, config.toml, mapify.log
<project>\app\models\    the 1.6 GB of weights, and the OCR models
```

That matters more than it sounds. Left to their defaults, the Hugging Face
cache and EasyOCR's weights put about 2.2 GB in two hidden places under
`C:\Users\you`, on top of a gigabyte of program — unhelpful on any machine
whose C: drive is the small one, and hard to find when you want it back.

The installer also adds a Start menu shortcut and one `HKCU` registry entry so
Mapify appears in **Settings → Apps** like anything else.
`installer\uninstall.ps1` removes exactly those, reading the install location
from the registry rather than assuming it. It leaves the *shared* Hugging Face
cache alone unless you pass `-PurgeModels`, because other tools use it.

`installer\mapify.iss` builds a single `Setup.exe` with
[Inno Setup](https://jrsoftware.org/) if you would rather hand someone that.

Neither is code-signed, so SmartScreen will warn that the publisher is
unverified. **That warning is correct.** Do not turn SmartScreen off -- build
it yourself from source, which is what these commands do.

Mapify draws its window with the Microsoft Edge WebView2 Runtime. Windows 11
has it; some Windows 10 installations do not, and both the installer and the
application say so with a link rather than failing with a COM error.

### From source

You need Windows 10 or 11 and Python 3.11-3.13. Everything else is fetched by
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

### The first launch

Mapify ships without model weights, and the first launch says so before it
spends anything. A screen states the size (**about 1.6 GB**, OpenAI CLIP
ViT-L/14, MIT), where it comes from, where it will be stored and what happens
afterwards -- and you can decline it. A refusal is remembered, and you are
asked again when you open an image rather than on every launch.

GeoCLIP's own weights are 44 MB and are bundled, so they are not part of that
download. The obvious assumption is that the geolocation model is the big one.
It is not.

Setting up then encodes GeoCLIP's 100,000-point GPS gallery. That is local
computation with no network at all, and it is slow: **272 seconds measured on
one machine, 457 on one with 0.6 GB of RAM free**. It is cached to disk, so it
happens once ever, and `install.ps1` copies an existing cache into a new
installation so you never pay it twice. Analyses after that take **about three
seconds each**.

After all of it, the application works with no network at all.

If something goes wrong, `Mapify-console.bat` runs the same application with a
visible console and a log.

### A whole folder at once

**Open folder** analyses every image in a directory, then groups the results by
place. A folder of two hundred photographs of one street costs **one**
geocoding request, not two hundred: the batch analyses everything with the
network half switched off, clusters the results, and names each *place* once.
Nominatim allows one request per second, so that is the difference between
seconds and minutes.

Stop halts after the image being analysed and keeps everything already done.
Selecting any image in the queue shows its full assessment.

### Sending a result to someone else

**Export** writes a single self-contained HTML file: the photograph, the
estimate with its confidence and the reason for it, the coordinate in decimal
and DMS, the place breakdown, a locator map drawn from Natural Earth, the EXIF
block with its trust warnings, and the full scoring trail. Nothing in it is
fetched from anywhere — it opens in any browser with the network off.

It is deliberately not dressed as an official record. There is no letterhead,
no case number and no CONFIDENTIAL banner, every report says *this is an
estimate produced by a model, not a record of fact*, and the confidence band is
followed by what that band was measured to mean rather than by an adjective.
The OpenStreetMap attribution and the ODbL share-alike obligation travel with
the file, because the place names in it are ODbL data.

**Save report** still writes JSON, which is the right format for a machine.

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
