# Phase 13 — A wider benchmark, and a calibration curve

**State:** implementable · **the highest-value phase remaining**

---

## Why

Eleven samples. Every accuracy number in the README rests on them, and the
calibration table — the thing this project claims as its central property —
has cells with **n=1**.

"medium, n=1, median 190 km" is not a calibration measurement. It is one data
point wearing the costume of one.

## Scope

- **150+ samples**, fetched the same way: geotagged, CC or public domain, from
  Wikimedia Commons, with licence and author recorded and nothing
  redistributed.
- **Stratified deliberately**, not by whatever geosearch returns first:
  - by continent, with Africa and South America *over*-sampled, because that
    is where GeoCLIP is known to fail and where the current sample is thinnest;
  - by scene type — street level, landmark, interior, rural, coastal — because
    landmarks are the easy case and are over-represented in tourist photography;
  - by whether legible signage is present, so the OCR contribution can be
    measured on the images where it could possibly matter.
- **A reliability diagram**: predicted confidence against observed accuracy,
  which is the honest way to show calibration and would make any drift obvious.
- **Per-band n reported everywhere.** If a band has fewer than 10 samples the
  output must say the band is unmeasured rather than print a median.
- **Committed results with a date and a commit hash**, so a regression is
  visible.

## What it will probably show

That the current thresholds are conservative — `low` and `insufficient`
between them caught both failures, but `rio` was correct to 1.8 km and still
labelled `low`. A false negative on confidence is the safe direction, but if it
happens often the band is not informative.

Resist the urge to retune until the sample is big enough to justify it. Tuning
thresholds on eleven points is how a tool ends up confidently wrong.

## Done when

- Every confidence band has n ≥ 10, or is reported as unmeasured.
- A reliability diagram is in the README.
- The Africa and South America subsets are reported separately, because the
  headline number hides the known weakness.
