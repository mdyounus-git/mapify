# Phase 11 — Batch analysis

**State:** implemented

Analyse a folder in one pass, and geocode places rather than images.

---

## Goal

Everything expensive is already amortised — the models load once, the GPS
gallery is encoded once, the prompt bank is cached — so a second image costs
about two seconds. Making someone open them one at a time wastes all of it.

## The design decision that matters

Naming a place is the only part of an analysis that leaves the machine, and
Nominatim allows one request per second. Geocoding every image in a 200-image
folder would be three minutes of waiting, and rude besides, when most of those
images are usually the same handful of places.

So batch mode runs in two passes:

1. analyse every image with the network half of enrichment switched off;
2. cluster the results, then name each **cluster** once and hand the name back
   to every image in it.

A folder of two hundred photographs of one street costs **one** geocoding
request. That property is the first thing the test suite checks, because if it
ever stops being true the feature is not merely slow — it is a way to hammer
donated infrastructure by accident.

This needed `AnalysisEngine.analyse(online_enrichment=False)` and a new
`name_coordinate()`, so the offline naming (Natural Earth, time zone) still
happens per image while the online half happens per place.

## What was built

- **`mapify/batch.py`** — `BatchRunner`: sequential analysis, clustering at a
  25 km radius, one naming call per cluster, and a progress event per image.
  Sequential on purpose: torch on CPU already uses every core, so concurrency
  would only add contention and make the progress meaningless.
- **Cancellation** that keeps what finished. A cancelled batch is a shorter
  batch, not a lost one — completed images stay analysed, clustered and named.
- **`open_folder()`** on the desktop bridge: native folder dialog, filters to
  supported extensions, skips oversize files, caps at 500, and adds every path
  to the same approved set a single Open would.
- **`POST /api/batch/stream`** — NDJSON, one batch at a time, refusing any path
  the folder dialog did not hand out.
- **The left rail becomes a queue**, grouped by place once naming has
  happened, with a progress bar and a running estimate. Selecting any image
  shows its full assessment and flies to it. The globe shows one marker per
  place, labelled with how many images landed there.

## Verification

13 tests, led by the bounded-geocoding property:

```
20 images of one place      -> 1 cluster,  1 geocoding request
9 images of three places    -> 3 clusters, 3 geocoding requests
4 unplaceable images        -> 0 clusters, 0 geocoding requests
offline mode                -> 0 requests, still named from Natural Earth
cancel after 3 of 10        -> 3 analysed and named, 7 skipped, nothing lost
a corrupt file mid-run      -> recorded as failed, run continues
```

And a real run over twelve sample photographs:

```
analysed 12/12 in 25.8s (2.15s each)
10 distinct places
  2x  Tokyo, Tokyo, Japan          +/- 10.5 km   [asakusa + shibuya grouped]
  1x  Athens, Attica, Greece
  1x  Istanbul, Istanbul, Turkey
  ...
```

The two Tokyo images grouped correctly at 10.5 km apart. One image
(`paris-belleville`) came back `insufficient` and was correctly left out of
clustering rather than dragging a cluster centre around — an image the tool
refused to place is not worth a lookup.

## What went wrong

- **`state.native` was captured once at boot.** pywebview's `pywebviewready`
  can fire after the page finishes loading, so a user clicking Open folder in
  that window was told the desktop app was a browser. The bridge is now
  re-checked at call time.
- **The Stop button would not hide.** `.btn` sets `display: inline-flex`,
  which beats the user agent's `[hidden] { display: none }`. Caught by looking
  at the finished state rather than assuming the attribute did something.

## Left open

- Results accumulate in memory. 500 is the cap, and at that size the reports
  are a few tens of megabytes, which is fine — but a genuinely large corpus
  would want spilling to disk.
- Clustering is a fixed 25 km radius, chosen to avoid geocoding one street
  twice rather than to make fine distinctions. A batch spanning a single large
  city will collapse to one place.
- Save report exports the batch as JSON. A readable batch report is Phase 12.
