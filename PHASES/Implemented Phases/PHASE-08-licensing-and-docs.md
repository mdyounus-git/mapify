# Phase 08 — Licensing, documentation, configuration

**State:** implemented · commit `d97ad39`

The phase that makes the project legally usable by someone other than its
author.

---

## Goal

Account for every external component honestly, and document the tool in a way
that does not overstate it.

## What was built

**`ATTRIBUTION.md`** — the ledger. Every model, dataset and library with the
licence it is actually under, the obligation that licence creates, and how the
project meets it. Organised so that the two obligations which travel to *users*
are impossible to miss:

- **OpenStreetMap-derived data** (place names, nearby features, time-zone
  boundaries) is ODbL 1.0. It must be attributed, and a *database* built from
  Mapify output containing it is itself subject to ODbL. A single report shown
  to a person is a produced work and is not.
- **NASA imagery** is not copyrighted but a credit line is requested, and the
  NASA name may not be used to imply endorsement.

It also states plainly what is deliberately absent: no Google, Mapbox, HERE or
Bing; no commercial geolocation service; no paid model endpoint; no scraped
imagery and no dataset whose terms forbid this use.

**`LICENSE`** — MIT for the source, with those two obligations restated on the
face of it rather than buried.

**`README.md`** — what the tool does, the measured accuracy, and the
calibration table, because "45% street-level" means nothing without "and
nothing labelled high confidence was worse than 13 km out". Documents GeoCLIP's
Latin-America bias on tropical and Sub-Saharan scenes as a known weakness. Has
a responsible-use section saying this locates places, not people.

**`config.example.toml`** — every setting with its reasoning attached. The
`[network]` section is the complete list of things the application may contact,
written so it reads as a policy document rather than a settings dump.

## Verification

Each licence claim checked against the component: GeoCLIP MIT, CLIP ViT-L/14
MIT, Natural Earth public domain, three.js MIT, pywebview BSD-3, EasyOCR
Apache-2.0, timezonefinder MIT code over ODbL data, NASA imagery uncopyrighted
with credit requested. `scripts/fetch_data.py` and `scripts/fetch_textures.py`
record a SHA-256 for every file they fetch in `data/MANIFEST.json`, so what is
in the tree is traceable to where it came from.

## Left open

No SPDX manifest or automated licence scanning. The ledger is maintained by
hand and says so, with a line inviting corrections as bug reports.
