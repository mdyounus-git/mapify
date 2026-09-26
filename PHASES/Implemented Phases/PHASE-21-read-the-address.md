# Phase 21 — Read the address, not just the city

**State:** implemented

---

## The failure

A user photographed a well-known medical institute near their home. The
institute's board is in frame and legible: the name, the street, the town, the
postcode. Mapify returned a scatter — Tamil Nadu, Hungary, elsewhere — and
settled on something no more precise than "India".

Everything needed to answer correctly was printed in the photograph.

**After this phase, the same board resolves to 0.00 km from Chettinad
Hospital, Kelambakkam, on one network request.**

## A correction to my own diagnosis

The obvious hypothesis was that a read place name can only re-rank candidates
the visual model proposed and cannot propose one of its own. **That was
wrong.** `fuse()` has injected a candidate for every toponym hit since Phase 3,
at `fusion.py:537`. The machinery was there. It had nothing to put through it.

Worth recording because the wrong diagnosis is more flattering — an
architectural insight rather than a data problem — and it would have led to
rebuilding something that already worked.

## What was actually wrong

**The gazetteer is 7,300 settlements.** `find_toponyms()` matches Natural
Earth populated places. That layer has Chennai. It does not have Kelambakkam,
it contains no streets, no postcodes and no buildings, so a board carrying a
complete address matched nothing and the injection step had nothing to inject.

**Only whole lines and single tokens were tried**, so "Tamil Nadu" could only
match a line with nothing else on it.

**A read address could not outrank a guess.** The injected candidate was
capped at the best locator score times 0.85, so on prior alone it could never
lead. For a vague city name off a poster that is right. For a legible street
address it is badly miscalibrated.

## What was built

`mapify/geo/readplace.py`, plus `search()` and `search_structured()` on the
Nominatim client, under the existing rate limiter, cache, User-Agent and
per-analysis budget.

Nearly every decision in it was made by running the thing against the real
service and looking at what came back. Four of them were surprises:

**The whole board as one free-text query is the worst query there is.**
Nominatim requires every term to match, and a board carries slogans,
department lists and a web address. `"Chettinad Academy of Research and
Education, Kelambakkam"` returns nothing; `"CHETTINAD Kelambakkam"` returns
the building. Shorter is better, which is the opposite of the intuition.

**EasyOCR returns lines sorted by confidence, not position.** So the name was
never identifiable as the name, and "Kelambakkam" arrived three lines away
from its own postcode. Lines are now restored to reading order from their
bounding boxes and fragments sharing a row are rejoined, which is what makes
the locality-beside-the-postcode heuristic work at all.

**The name is the biggest text.** Whoever made the sign already marked which
words are the name. That beats "first line" and it beats "most confident".

**Two near-misses looked like successes.** Searching the street line returned
a chicken and mutton stall on that road — right street, right town, nothing to
do with the sign — and it came back as a *building*, outranking everything.
Searching "HOSPITAL AND RESEARCH Chengalpattu District" returned a different
medical college 20 km away, on a perfect overlap of words every hospital
shares. So corroboration now ignores generic institution words and separately
requires the **name** to appear in the result: the town is in every result
from that town and confirms nothing.

**Clustering was averaging a street address with a model guess** and cost
2.9 km on its own. A cluster containing a corroborated address now reports the
address's coordinate, and keeps the real spread.

And `precision_km` was added, because "±123 km" beside a five-decimal
coordinate answers neither question honestly. How precise the coordinate is
(the size of the matched building) and how sure we are it is the right place
(the spread of the candidates) are different, and they now say so separately.

## Verification

```
board rendered from the description, run through the real pipeline:

  OCR            7 lines, name read at 0.92
  query          "CHETTINAD Kelambakkam"        <- 1 request, stopped early
  match          Chettinad Hospital, Padur, Kelambakkam   building, overlap 1.00
  verdict        medium confidence
  coordinate     12.79716, 80.21760
  place          Kelambakkam, Tamil Nadu, India
  error          0.00 km
```

31 tests in `tests/test_readplace.py`, including both near-misses as explicit
cases so they cannot come back. Full suite: **307 passed**.

## What it deliberately does not do

A read address caps at **medium** confidence. Not because it is weak — it is
usually the best evidence in the frame — but because there is no benchmark for
it. Phase 13 is the precedent: the high band is the one promise this tool
makes, and it is not handed out on reasoning. A signboard benchmark would let
that rule be revisited with numbers behind it, and until then the cap stays.

## Left open

- No benchmark for the read path, which is why the band is capped.
- Queries are Latin-script-shaped. The postcode patterns and the address
  vocabulary cover the common cases and will miss others; a board in a script
  Mapify can read but whose address conventions it does not know will fall
  back to the structured locality query.
- Nominatim only. A self-hosted Photon or Pelias would lift the rate limit
  and is the obvious next step for anyone running this in bulk.
