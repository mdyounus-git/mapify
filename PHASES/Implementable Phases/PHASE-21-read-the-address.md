# Phase 21 — A place name read off a sign must be able to *propose* a location

**State:** implementable · **the highest-value fix in the plan**

---

## The failure this exists to fix

A user photographed a well-known medical institute near their home. The
institute's board is in frame and legible: the college name, the street, the
town, the postcode. Mapify returned a scatter — Tamil Nadu, Hungary, elsewhere
— and settled on something no more precise than "India".

Everything needed to answer correctly was in the photograph, printed, in
Latin script, at readable size. The tool read it and could not use it.

## Why it happened

Three separate defects, in series. Any one of them alone would have caused it.

**1. A toponym can only re-rank; it cannot propose.**

`_apply_toponyms()` walks the candidates GeoCLIP produced and adjusts each
one's score. If GeoCLIP's shortlist contains nothing near the place written on
the sign, no amount of evidence can put the answer there — the best the
toponym can do is push every wrong candidate down by a similar amount, which
changes the ranking not at all and merely lowers confidence.

This is the architectural mistake. Text read off a sign is not one more weak
signal to weigh against a visual guess. It is frequently *the answer*, and the
pipeline gave it no way to say so.

**2. The gazetteer is far too coarse for an address.**

`find_toponyms()` matches against Natural Earth populated places: roughly
7,300 settlements worldwide. It has Chennai. It does not have Kelambakkam, it
does not have any street, and it has never heard of the institute. A board
carrying a complete address matches on at most the city token, if that.

**3. Only whole lines and single tokens are tried.**

`tokens(line.text, min_len=4)` yields single words. "Tamil Nadu", "New Delhi"
and "Sri Lanka" can only match via the whole-line attempt, which fails as soon
as the line has anything else on it — and a college board always does.

## What to build

**Forward geocoding of read text, as a candidate source.**

OpenStreetMap's Nominatim has a `/search` endpoint. It resolves
"Chettinad Hospital and Research Institute, Kelambakkam" to a coordinate
directly, and it knows every named building, street and hamlet that OSM
knows — which is the difference between 7,300 places and tens of millions.

- Assemble query strings from the OCR lines: the most sign-like line, the
  longest line, and combinations of lines that look like an address block.
- Query Nominatim `/search` under the *existing* policy machinery — the same
  1 req/s lock, the same disk cache, the same identifying User-Agent, and a
  hard budget of at most 3 searches per analysis.
- Every result becomes a **candidate in its own right**, with a prior derived
  from Nominatim's own importance score and the OCR confidence, and marked
  with a provenance of `read` rather than `model`.

**N-gram matching in the offline gazetteer**, so the offline path improves too:
bigrams and trigrams over each line, longest first, so "Tamil Nadu" is tried
before "Tamil".

**A provenance distinction that survives to the interface.** A candidate that
came from text read in the image is a different kind of claim from one a model
guessed, and the report, the globe marker and the exported document must all
say which is which. It is more likely to be right, and it fails differently —
a photograph of a poster advertising somewhere else is the classic case, and
the user has to be able to see that possibility rather than be told a fact.

## The rule this must not break

> The tool is allowed to be wrong. It is not allowed to be confidently wrong.

A sign is evidence, not proof. Specifically:

- A read address may raise confidence to `high` **only** when the visual
  estimate independently agrees with it — GeoCLIP putting real mass within a
  few hundred km of the geocoded address. Text alone tops out at `medium`,
  because a photograph of a brochure is indistinguishable from a photograph of
  a building.
- When text and image disagree sharply, that contradiction is reported, not
  resolved silently in favour of either.

## Done when

- A photograph of a signboard carrying a complete address is placed at that
  address, and the report says it was read rather than guessed.
- The same photograph with the network off still improves on the current
  behaviour through n-gram gazetteer matching, and says it was limited to
  offline data.
- A poster for a distant city, photographed indoors, produces a stated
  contradiction rather than a confident wrong answer.
- Nominatim search requests are bounded per analysis and cached, asserted by
  a test in the same way the batch geocoding budget is.
