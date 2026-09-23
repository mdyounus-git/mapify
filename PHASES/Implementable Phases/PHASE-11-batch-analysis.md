# Phase 11 — Batch analysis

**State:** implementable

Analyse a folder of photographs in one pass.

---

## Why

Everything expensive is already amortised. The models load once, the GPS
gallery is encoded once, the prompt bank is cached — a second image costs 2.7
seconds. Making someone open them one at a time wastes all of that.

The real use is also plural: an investigator verifying a set, a researcher
measuring accuracy over a corpus, a journalist working through what came out of
one camera. One-at-a-time is the artificial constraint.

## Scope

- **Pick a folder** through the native dialog; recurse optionally.
- **A queue in the left rail** replacing the candidate list while a batch runs:
  each image with a thumbnail, its state, and its result once placed.
- **Analyse sequentially**, not in parallel. Torch on CPU is already using
  every core; concurrency would only add contention and make the progress
  meaningless.
- **Cancellable**, with partial results kept.
- **A cluster view**: images that landed within one uncertainty radius of each
  other are probably the same place, and saying so is worth more than five
  separate reports.
- **Export the set** as one JSON with a row per image.

## Constraints

- **Nominatim.** One request per second is a hard floor, and a folder of 200
  images would take three and a half minutes of geocoding alone even at the
  6-lookup budget. Batch mode must either run offline-only by default, or
  geocode only the *distinct* clusters rather than every image. The second is
  better and is the design.
- **Memory.** Reports accumulate. Cap the in-memory set and spill to disk past
  a few hundred.

## Done when

- A folder of 50 images completes without intervention.
- The Nominatim request count for that run is bounded by the number of distinct
  clusters, not the number of images, and that is asserted in a test.
- Cancelling mid-run leaves the completed results intact and usable.
