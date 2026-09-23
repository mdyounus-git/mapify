# Phase 07 — Performance

**State:** implemented · commit `4996ab6`

60 seconds per photograph to 2.7, by encoding something once instead of every
time.

---

## Goal

Find out why an analysis took about a minute, and fix the actual cause rather
than the assumed one.

## What was found

The assumption was "CPU inference is slow". Profiling each stage said
otherwise:

```
preprocess                    0.0s
CLIP vision tower             2.3s
GeoCLIP head                  0.0s
location encoder, 100k pts   57.4s
```

The location encoder runs over the whole 100,000-point GPS gallery, and **its
output depends only on the gallery, which never changes.** GeoCLIP's packaged
`predict()` recomputes it on every call, and `locate_with_embedding()` had
inherited that.

## What was built

`GeoClipLocator.gallery_features()` encodes the gallery once per process and
memoises it to disk as float16 — 98 MB rather than 196, and the precision lost
is orders of magnitude below the noise in a location estimate. `warmup()` pays
for it so the first analysis does not.

Also in this phase:

- **Markers scaled to constant apparent size.** They are built in world units,
  so on a close approach a ring 70 km across filled the screen. The uncertainty
  circle is deliberately excluded from that scaling — it is a real distance on
  the ground and must stay one.
- **The approach will not descend below what the imagery supports.** 8k of Blue
  Marble is 4.9 km per texel, so flying to 500 km was seven-times magnification
  of blur. The HUD states the resolution and flags when the camera has gone
  past it.

## Verification

Measured on this machine, offline, no OCR:

```
first ever run   warmup 308s (272s encoding the gallery), then 2.7s per image
every run after  warmup  37s (gallery read from cache),   then 2.7s per image
```

Roughly twenty times faster per image, and the one-off cost is paid once ever
rather than once per launch. Confirmed by running a second process and watching
it read the cache.

## What went wrong

The marker scaling introduced a reference-before-initialisation — the camera
distance was used in the marker loop and declared further down for the exposure
ramp — which killed the render loop entirely. Caught in the browser console.

A test script named `timeit.py` shadowed the stdlib module torch imports, which
cost a confusing minute.

## Left open

The 2.7 s is dominated by the CLIP vision tower on CPU. GPU support is Phase 17
and would put it under a second.
