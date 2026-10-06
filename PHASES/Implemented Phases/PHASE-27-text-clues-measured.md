# Phase 27 — Should text clues steer the answer? Measured: the benchmark cannot say

**State:** implemented and measured, 2026-10-06. The fusion input exists at
weight zero; it stays zero until a benchmark that can measure it exists.

---

## The question

Techniques 110 and 117–124 read country clues off the image as text: a
calling code ("+44"), a country-code web address (".in"), a currency sign
("₹"), a postcode in its context ("Bengaluru - 560001"), speed limits in
miles, brands that trade in a few countries. They are read, not inferred,
so they are independent of the image encoder — the one kind of evidence
Phase 22 found can improve an answer, where more readings from the same
encoder could not.

Reporting them needs no permission. Letting them move the coordinate does:
the standing rule is that a technique which constrains geography must be
measured on the benchmark first. Phase 22 built four plausible clues,
measured them, and found them inert or harmful.

## What was built

- `mapify/evidence/text_clues.py` — one reader for the techniques and the
  fusion alike. A `TextClue` per kind: what was matched, the countries it
  names, whether it is *narrow* (a handful of countries), and how hard it
  may argue (`strength`, set by judgement: a calling code above a brand).
- `FusionEngine._apply_text_clues` — per narrow clue, `w × strength ×
  agreement`, agreement +1 for a candidate the clue fits and −0.6 for one
  it does not (the toponym rule: a clue can be about somewhere else — a
  foreign brand's sign, a tourist's price list). Capped with everything
  else. `fusion.w_text_clue = 0.0`.
- `scripts/measure_text_clues.py` — tune_weights.py's method: run the
  benchmark once with OCR, record each candidate's prior, other
  adjustments and country and each photograph's clues; re-score offline
  for each weight; fit on the even samples, report on the odd ones.

## What the measurement found

The benchmark run with OCR on (offline, 163 photographs, 161 locatable):

| | |
|---|---|
| photographs with any readable text | 64 of 161 |
| photographs with a **narrow** text clue | **0** |
| photographs with any clue at all | 1 — a weak "$0" (Nairobi) |
| held-out half, every weight 0 – 2.5 | median 72.3 km, 37/80 within 25 km, 41/80 within 200 km — identical |

To check that the matchers were not simply missing clues, the OCR text of
the eight most text-heavy photographs was read by hand. None contains a
phone number, web address, price or postcode: Nairobi's 69 lines are
timestamps and sequence labels, Ulaanbaatar's only number is a year, the
rest are names and words.

**Verdict: unmeasurable on this benchmark; the weight stays zero.** This is
not a negative result about text clues. It is a statement about the
benchmark, which was built from landmark and scene photographs, and those
do not carry this kind of text. It does show that switching the input on
would change nothing on such photographs — it only ever acts when a clue
is present.

(The two halves' baselines differ widely — 595 km median on the fit half,
72 km held out — which is split variance on 80 photographs, and a reminder
that one half's median is not a property of the system.)

## What would measure it

A text-bearing set: freely licensed, geotagged photographs of shopfronts
with phone numbers, price lists, speed-limit signs in miles, postcodes on
signs — selected *for* carrying a clue. That selection is the right
condition rather than a bias, because the weight acts only when a clue is
present; the question is what it does then. The held-out rule applies
unchanged. Until that set exists, the clues report and do not steer, and
each technique's fusion note says so.
