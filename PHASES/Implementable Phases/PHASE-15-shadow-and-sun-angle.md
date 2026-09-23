# Phase 15 — Shadow and sun-angle latitude estimation

**State:** implementable · speculative, and the most likely to be abandoned

---

## Why

A shadow encodes latitude. Given the sun's elevation and the capture time, the
observer's latitude follows from the same solar geometry the globe already
computes in `subsolarPoint()`. It is a *physical* constraint rather than a
learned association, which is what makes it worth trying: it would be the only
evidence channel in the tool that does not come from a model's priors.

## Scope

- Estimate solar elevation from the ratio of a vertical object to its shadow,
  where both can be identified.
- Combine with the EXIF capture time — clearly marked as depending on a claim
  the tool otherwise refuses to trust — to constrain latitude.
- Report as a latitude *band*, never a point, and weight it low.

## Why it will probably fail

- Finding a vertical object and its shadow, and knowing the object is actually
  vertical, is a segmentation problem in its own right.
- Without a capture time the constraint degrades to "somewhere in these two
  latitude bands", which is close to worthless.
- The time comes from EXIF, and this tool's whole stance is that EXIF is a
  claim. Using it here means the report must say exactly that, prominently.

## Done when

Either it beats a coin flip on a held-out set of images with known latitude and
a sun-lit vertical object, **or it is written up as a negative result and
removed.**

A channel that adds noise under a scientific-sounding name is worse than no
channel at all. The honest outcome of a speculative phase is often that it did
not work, and that outcome should be recorded rather than quietly dropped.
