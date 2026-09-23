# Phase 03 — The analysis pipeline, and a benchmark that holds it honest

**State:** implemented · commit `0fd7ddb`

The part that answers the question, and the part that stops it lying about the
answer.

---

## Goal

Estimate a location from an image using only openly licensed models, re-rank
that estimate with the rest of the evidence in the photograph, and report a
confidence worth believing.

## What was built

**GeoCLIP as the primary locator** (MIT), scoring the image against a gallery
of 100,000 GPS coordinates. Run on a PIL image rather than a path, so an upload
is never written to disk just to be read back, and returning the CLIP embedding
it already computed.

**Zero-shot evidence from that same embedding.** A prompt bank of twelve groups
— traffic side, signage script, world region, architecture, biome, snow, road
markings, utility poles, plate class, vehicle mix, terrain, settlement type —
plus "a photo taken in {country}" for all 239 countries as a second opinion.
Each option declares how it constrains geography; options that constrain
nothing are reported as observation and move no candidate.

**Fusion** re-ranks in log space with a per-adjustment audit trail. A place name
read off a sign and found in the gazetteer earns its own candidate, because
that is positional evidence rather than a guess.

**The confidence model.** The first version normalised probability across the
returned candidates, which made a single merged cluster trivially "100%" and
therefore always high confidence. It now rests on three independent things:

- **locator mass** — how much of its 100k-point distribution the model
  committed to its shortlist at all;
- **concentration and dispersion** — how tightly that mass clusters;
- **evidence support** — whether the rest of the image agrees, with
  contradiction forcing a downgrade and saying so.

## Verification

`scripts/evaluate.py` over geotagged CC/PD photographs from Wikimedia Commons,
fetched by `scripts/fetch_samples.py` and not redistributed. Eleven samples,
six continents, offline, no OCR:

```
street    <=    1 km   5/11   45%
city      <=   25 km   8/11   73%
region    <=  200 km   9/11   82%
median error 1.8 km

high          n=7  median   0.5 km  worst    12.7 km
medium        n=1  median 190.2 km  worst   190.2 km
low           n=2  median  12514 km
insufficient  n=1  median  11783 km
```

Both catastrophic misses — Cairo read as Argentina, Nairobi as Colombia — are
labelled `low` and `insufficient`. Nothing labelled `high` is worse than 13 km
out. That asymmetry is the phase's actual deliverable.

## What went wrong

The confidence bug above, found by noticing that every single result said
"100% of the probability" and asking why that could be.

## Left open

- GeoCLIP reads tropical and Sub-Saharan African scenes as Latin America. Real,
  documented in the README, and not fixable from outside the model.
- Eleven samples is a small benchmark. Phase 13.
