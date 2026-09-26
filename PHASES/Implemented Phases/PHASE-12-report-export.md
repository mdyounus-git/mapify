# Phase 12 — Report export

**State:** implemented

A report someone can send to a person who does not have Mapify.

---

## Goal

JSON export was already there and is the right thing for a machine. It is the
wrong thing for what this tool actually produces, which is an argument: *here
is where I think this was taken, here is how sure I am, and here is why.* That
argument has to survive leaving the application, and it has to survive being
read by someone who was not sitting here when it was made.

Nothing new had to be computed. Every adjustment in the fusion engine already
carries its own reason. The phase was about laying that out so it cannot be
misread.

## The decision that shaped it

**One HTML file with nothing outside it.** Not PDF, which needs a rendering
dependency and produces text you cannot properly select; not HTML with a
folder of assets beside it, because the folder gets lost the first time
someone forwards the file. The source photograph is inlined as a base64 data
URI, the CSS is inline, and the locator map is an SVG generated from the
Natural Earth 110m shapefile at export time. The whole document is one file
that opens in any browser, on a machine with no network, years from now.

The second decision was subtractive. A report is the point where a model's
guess is most likely to be mistaken for a finding, so the layout deliberately
refuses the visual grammar of an official record: no letterhead, no case
number, no CONFIDENTIAL banner, no seal. Every report, at every confidence
band, carries the same sentence near the top:

> This is an estimate produced by a model, not a record of fact.

And the confidence pill is followed by what that band was *measured* to mean
on the 163-sample benchmark from Phase 13 — not an adjective, a number.

## What was built

- **`mapify/report.py`** (596 lines) — `build_single()`, `build_batch()` and
  `locator_svg()`. Light theme, because it is a document and it will be
  printed. Everything escaped through one helper; the audit trail, the EXIF
  block with its trust warnings, the alternative candidates, and the full
  attribution all render from the same report dictionary the API returns.
- **`locator_svg(lat, lon)`** — an orthographic projection of the 110m
  countries centred so the point is visible, with a marker on it. Pure Python
  against the shapefile already in `data/`: no plotting library, no tiles, no
  network.
- **`POST /api/report/html`** in the service, which reads the source image
  only from the bridge-approved path set — the same rule the analysis
  endpoints follow, so exporting cannot become a file-read primitive.
- **`save_html()` on the desktop bridge** and an **Export** button in the
  header, with `state.sourcePath` tracking the native path so the photograph
  can be inlined rather than re-uploaded.

## Verification

31 tests in `tests/test_report_export.py`, organised around the three
properties that have to survive the journey:

```
TestSelfContained          no http/https, no <script>, no @import, no url(http)
                           every src/href is a data: URI
                           renders with no image at all
TestUncertaintyIsNotBuried an insufficient result says so in the headline
                           it prints no coordinate it does not have
                           the band states its measured meaning
                           every report carries the caveat
TestAttributionTravels     every attribution line reproduced verbatim
                           the share-alike obligation is spelled out
                           the batch report carries it too
TestNoFalseAuthority       parametrized over CONFIDENTIAL / OFFICIAL /
                           Case No / Exhibit / Certified / SECRET
TestEmbeddedGpsStaysApart  a disagreeing GPS tag is shown and labelled
TestLocator                renders at the poles and the antimeridian, no NaN
TestEscaping               a hostile place name cannot inject markup
```

Full suite: **239 passed**.

And one real export, from a real analysis of `samples/paris-eiffel.jpg` with
the network enabled so the Nominatim path was exercised too:

```
wrote .report-sample.html  (663 KB)
confidence      : medium
place           : Paris
external refs   : none
http outside svg: False
has <script>    : False
embedded image  : yes
locator svg     : yes
ODbL stated     : yes
caveat present  : yes
```

Opened in a browser it reads: the headline place with a *Medium confidence*
pill, the reason ("Short of high confidence on: mass."), what medium was
measured to mean, the orthographic locator with its marker, the coordinate in
decimal and DMS, the place breakdown down to the postcode, the timezone, who
named it, the photograph, the detected evidence, and the scoring trail
(country prior +0.49, world region +0.34) with the four diagnostics.

## What went wrong

Three of my own assertions were testing the wrong thing, and sharpening them
was the useful part of the phase:

- **`xmlns="http://www.w3.org/2000/svg"` is not a network reference.** It is a
  namespace identifier and no browser resolves it. Asserting `"http" not in
  html` failed on it, and the fix was not to remove the namespace — it was to
  strip that one string before checking, and say in the test why.
- **`onerror=alert(1)` surviving as text is the correct outcome.** I had
  asserted the substring was absent. Once the angle brackets are entities the
  payload is inert prose; what must not survive is an actual tag or a quote
  that escapes an attribute, so that is what the test now checks.
- The batch self-containment check was missing `@import`, which is the one
  CSS construct that would quietly reach out.

## Left open

- The locator is a 110m orthographic outline. It shows which part of the world
  the point is in, and nothing finer. A report for a street-level result would
  be better with a small inset, which needs raster tiles and therefore an
  attribution and caching story of its own.
- No PDF. A browser's Print to PDF produces a good one from this file, and
  adding a rendering dependency to do it worse is not worth it.
- The batch report lists clusters and images but does not embed the
  photographs. At 500 images that would be a gigabyte.
