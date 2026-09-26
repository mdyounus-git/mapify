# Phase 21 — Read the address, not just the city

**State:** implementable · **the highest-value fix in the plan**

---

## The failure this exists to fix

A user photographed a well-known medical institute near their home. The
institute's board is in frame and legible: the college name, the street, the
town, the postcode. Mapify returned a scatter — Tamil Nadu, Hungary,
elsewhere — and settled on something no more precise than "India".

Everything needed to answer correctly was in the photograph, printed, in Latin
script, at readable size.

## Why it happened

**A correction first.** The obvious hypothesis was that a read place name can
only re-rank candidates the visual model proposed and cannot propose one of
its own. That is wrong. `fuse()` already injects a candidate for every toponym
hit, at `fusion.py:537`, and has since Phase 3. The machinery was there. It
had nothing to put through it.

The real causes, in order of how much damage they do:

**1. The gazetteer is 7,300 settlements.**

`find_toponyms()` matches against Natural Earth populated places. That layer
has Chennai. It does not have Kelambakkam. It contains no streets, no
postcodes, no buildings and no institutions, so a board carrying a complete
address matches on at most the city token — and often on nothing at all,
which is what happened here. The injection step then had no hit to inject.

Natural Earth is the right offline gazetteer and it is staying. It is simply
the wrong instrument for an address: a map of where settlements are, not a
directory of places.

**2. Only whole lines and single tokens are matched.**

`tokens(line.text, min_len=4)` yields single words, and the only multi-word
attempt is the entire line. "Tamil Nadu" therefore matches only on a line
containing nothing else, and a college board always contains something else.

**3. A read address cannot outrank a guess.**

The injected candidate is capped at `ceiling * strength`, where `ceiling` is
the best score any locator candidate got and `strength` tops out at 0.85. On
prior alone a read place name can never lead; it needs positive adjustments to
catch up. For a vague city name read off a poster, that caution is right. For
a legible street address with a postcode it is badly miscalibrated — that is
usually the strongest evidence in the entire frame.

## What to build

**Forward geocoding of read text.** OpenStreetMap's Nominatim has a `/search`
endpoint. It resolves "Chettinad Academy of Research and Education,
Kelambakkam" to a coordinate directly, because OSM knows every named building,
street and hamlet. That is the difference between 7,300 places and tens of
millions, and it is the fix.

- Build query strings from the OCR lines: the whole board as free text first,
  since Nominatim handles that well; then narrower fallbacks from the lines
  that look like an address — a postcode-shaped token, a street word, a
  district word.
- Run them through the *existing* policy machinery: the same 1 req/s lock, the
  same disk cache, the same identifying User-Agent, and a hard budget of at
  most 3 searches per analysis.
- Each result becomes a candidate whose prior reflects **how specific the
  match is**. A matched building or street may exceed the model ceiling; a
  matched city may not.

**N-gram matching offline**, so the no-network path improves too: bigrams and
trigrams per line, longest first, so "Tamil Nadu" is tried before "Tamil".

**Provenance kept visible.** A candidate derived from text read in the image
is a different kind of claim from one a model guessed, and the report, the
globe marker and the exported document must say which is which. It is more
often right, and it fails differently — a photograph of a poster advertising
somewhere else is the classic case, and the user has to be able to see that
possibility rather than be handed a fact.

## The rule this must not break

> The tool is allowed to be wrong. It is not allowed to be confidently wrong.

A sign is evidence, not proof:

- A read address may reach `high` **only** when the visual estimate
  independently agrees — real locator mass within a few hundred km. Text alone
  tops out at `medium`, because a photograph of a brochure and a photograph of
  a building look identical to an OCR engine.
- When text and image disagree sharply, the contradiction is reported, not
  silently resolved in favour of either.

## Done when

- A photograph of a signboard carrying a complete address is placed at that
  address, and the report says it was read rather than guessed.
- With the network off, the same photograph still does better than it does
  today, through n-gram gazetteer matching, and says it was limited to offline
  data.
- A poster for a distant city, photographed indoors, produces a stated
  contradiction rather than a confident wrong answer.
- Nominatim search requests are bounded per analysis and cached, asserted by a
  test, the way the batch geocoding budget already is.
