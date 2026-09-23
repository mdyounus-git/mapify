# Phase 02 — The globe

**State:** implemented · commit `98a1b5f` · substantially rebuilt in Phase 06

A 3D Earth the operator can fly, rather than a map pinned to a sphere.

---

## Goal

Show a result on a globe that is worth looking at, with an approach flight that
communicates distance and uncertainty instead of teleporting.

## What was built

**three.js (MIT), no bundler.** ES modules loaded through an import map, so the
whole front end is readable files with nothing generated in between. The
library is vendored by `scripts/fetch_data.py`, which records a SHA-256 for
every file it fetches.

**`flyTo()` is the centrepiece.** Orientation slerps along the great circle
between where the camera is and where the target is, while the radius climbs to
an apogee and then eases down onto the coordinate. Longer slews earn a longer
flight and a higher apogee, so crossing a hemisphere arcs out through orbit and
crossing a city does not.

**Markers** carry a standoff spike, a pulsing surface ring, a canvas label and
an uncertainty circle drawn at its true angular radius on the sphere.

**Two looks.** Satellite imagery, and a "recon grid" drawn at runtime onto a
4096x2048 canvas from the Natural Earth outlines and caged in orbital traces —
for reading geometry rather than admiring terrain.

## Verification

Rendered and flown in a real browser at 1580x940. Both modes confirmed; the
approach flight confirmed arcing through orbit rather than sliding across the
surface.

## What went wrong

- MapLibre was the original choice and was wrong for this. Its globe is a map
  projected onto a sphere: no atmosphere, no terminator, no camera flight
  through orbit. Replaced with three.js before anything depended on it.
- MapLibre 6 ships ES modules only, discovered by a 404 on the UMD path.
  Irrelevant after the switch, but it set the no-bundler pattern.
- Chromium does not honour CSP hashes for inline import maps, only nonces. The
  first attempt computed a SHA-256 of the import map and was refused; the
  server now issues a per-response nonce.

## Left open

Everything about making it look *real* — lighting, atmosphere, clouds, bloom.
That became Phase 06, because this phase lit the planet from behind the camera
and the result looked like a sticker.
