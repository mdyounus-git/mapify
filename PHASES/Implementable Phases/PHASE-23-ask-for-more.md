# Phase 23 — Ask for another photograph rather than guess

**State:** implementable

---

## Why

When the evidence will not support a location, Mapify says so and stops. That
is honest, and it is not helpful. A person holding a phone usually has more
than one photograph of the same place, and a second angle frequently contains
the thing the first one lacked — a sign, a street, a skyline.

So when an analysis comes back `insufficient` or `low`, the right move is to
**ask**, and to say what would help:

> Nothing in this photograph places it. Another picture of the same place
> would help most if it showed a sign, a street with traffic on it, or a wide
> view with the horizon.

## What to build

- **A multi-image session.** Several photographs asserted by the user to be of
  the same place, analysed independently and then fused — evidence from all of
  them scored against one shared candidate set, rather than three separate
  answers averaged.
- **The fusion is the interesting part.** Independent images of one place give
  genuinely independent evidence, so agreement between them is strong and
  should be allowed to raise confidence. Two photographs of the same *sign*
  are not independent and must not count twice; near-duplicate detection keeps
  that honest.
- **I have no more.** The user says so, and Mapify then gives what it has: a
  ranked list of possible places with what each one rests on, explicitly as
  possibilities. This is the existing candidate list, presented as the answer
  to a question the user asked rather than as a shortfall.

## Done when

- An `insufficient` result offers to take more images and says what kind would
  help.
- Adding a second image of the same place measurably tightens the answer on
  the benchmark, and two copies of one image do not.
- Declining produces a ranked list of possibilities, each with its evidence,
  and no invented coordinate.
