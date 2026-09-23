# Phase 05 — Test suite

**State:** implemented · commit `de497ea`, extended in Phase 10

193 tests, weighted towards the claims that could mislead someone.

---

## Goal

Make the tool's claims checkable, and make the confidence property fail loudly
if it is ever broken.

## What was built

The model layer sits behind protocols, so the whole analysis chain runs against
deterministic stand-ins in milliseconds with no weights loaded. That is the
design decision that makes this phase cheap enough to be worth having.

- **Geodesy** against known values rather than against itself: Heathrow to JFK,
  a degree at the equator and at 60 north, antipodes, and the antimeridian case
  where 179E to 179W must be 222 km rather than 358 degrees.
- **EXIF**, where the tests are about doubt rather than parsing: Null Island
  discarded, `MANUAL` processing method flagged, editor software tags flagged,
  coordinates rounded to ~100 m on both axes flagged as typed rather than
  measured.
- **Fusion**, including the cases the confidence band exists for: a tidy
  shortlist holding almost no probability mass is *insufficient*, not high;
  candidates scattered across a hemisphere are insufficient; and evidence that
  contradicts the winner forces a downgrade with a stated reason.
- **Pipeline end to end**: an embedded GPS tag 9,000 km from the estimate must
  not move the estimate, and must be reported as a disagreement.
- **The service boundary**: every route 403 without the token including static
  files, and a local path readable only if the native dialog handed it out —
  including a traversal attempting to reach a sibling of an approved file.
- **OCR routing** (added in Phase 10): that an English-only reader misreading
  Cyrillic as Latin is the failure being prevented, and that a peaked Greek
  prior selects no reader because EasyOCR has no Greek model.

## Verification

```
193 passed in 11.22s
```

## What went wrong

Two test-authoring bugs of my own: Pillow needs `IFDRational` rather than
tuples to write GPS, and a clamp assertion compared against `exp(1)` when the
clamp was 0.5.

## Left open

No coverage measurement. The front end has no unit tests — it is verified by
driving a real browser, which catches rendering faults a DOM test would not.
