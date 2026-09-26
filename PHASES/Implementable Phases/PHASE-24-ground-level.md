# Phase 24 — Get down to the street

**State:** implementable

---

## Why

The globe lands at 8k Blue Marble, which is 4.9 km per pixel. At that
resolution "this is the place" is a blue-green smudge, and the user cannot do
the one thing that would let them confirm or reject the answer: recognise it.

Requested directly, and it is the right instinct — an estimate you can look at
is worth far more than one you have to take on trust.

## What to build

- **Raster tiles on the final descent.** The plumbing exists and is off by
  default (`basemap_tiles_enabled`), fetching at most a 4x4 grid on landing.
  This phase turns it on properly: deeper zoom, a real cache, and a tile
  source whose terms permit it.
- **The tile source is the whole licensing question.** OpenStreetMap's own
  tile servers are donated infrastructure with a usage policy that a desktop
  application fetching on every landing would violate. The options that do not
  violate anything are: a user-supplied tile URL — their own server, or a
  service they hold a key for — and public-domain imagery where it exists.
  Default stays off, the setting says why, and Mapify never ships a default
  that abuses donated bandwidth.
- **Terrain relief** on the descent from the GEBCO data already bundled, so
  the approach has real shape rather than a painted sphere.
- **4K-class imagery** where the hardware supports it. The textures are 8k
  today; 16k for the landing area specifically is worth the memory when the
  GPU can hold it. Detected, not assumed.

## Explicitly not in scope

Street-level photography. Google Street View is not licensed for this and will
not be used. Mapillary is openly licensed and is the honest candidate, and it
is a phase of its own rather than a footnote to this one.

## Done when

- A landing at a street-level result shows imagery a person can recognise, or
  states plainly that it has none and why.
- No tile source is enabled by default whose terms the default use would
  breach.
- The HUD continues to state the true resolution of what is on screen.
