# Phase 16 — A second locator, and an ensemble

**State:** implementable

---

## Why

GeoCLIP is a single point of failure, and its failure mode is known and
systematic: tropical and Sub-Saharan African scenes read as Latin America. An
independent locator with a different training distribution would either
disagree — which is itself information the confidence band should use — or
agree, which is corroboration.

The CLIP country gazetteer is already a second opinion, but it runs on GeoCLIP's
own backbone and therefore inherits some of its biases. It is not independent.

## Scope

- Survey what is genuinely available under a permissive licence. StreetCLIP is
  the obvious candidate, and **its terms must be read before any code is
  written** — "downloadable from Hugging Face" is not a licence, and this
  project has a rule about that.
- Run both locators and cluster the union of their candidates.
- **Disagreement is a signal.** Two independent locators pointing at different
  continents should *lower* confidence, not be averaged into a point somewhere
  between them. Averaging is the specific failure mode to avoid.
- Report which locator produced each candidate, so the operator sees the
  disagreement rather than only its resolution.

## Constraints

Another model is another 1–2 GB download and another few seconds per image. It
must be opt-in, and the accuracy gain has to be measured against that cost on
the Phase 13 benchmark before it becomes a default.

## Done when

- Both locators run and their candidates are attributed in the report.
- Continental disagreement demonstrably lowers the confidence band, asserted.
- The licence review is written into ATTRIBUTION.md — including the outcome if
  the answer turns out to be that the model cannot be used here.
