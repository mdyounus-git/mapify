# Phase 12 — Report export

**State:** implementable

A report someone can send to a person who does not have Mapify.

---

## Why

JSON export exists and is the right thing for a machine. It is the wrong thing
for the actual output of this tool, which is an argument: *here is where I
think this was taken, here is how sure I am, and here is why.* That argument
has to survive leaving the application.

The audit trail is already there — every adjustment carries its reason. Nothing
new has to be computed; it has to be laid out.

## Scope

- **A single self-contained HTML file.** Images inlined as data URIs, no
  external requests, opens in any browser forever. Not PDF: PDF needs a
  rendering dependency and produces something you cannot select text out of
  properly.
- **Contents**: the source image, the estimate with its confidence and stated
  reason, the coordinate in decimal and DMS, the full place breakdown, the
  evidence list with what each item did to the score, the alternative
  candidates, the EXIF section with its trust warnings, and a static locator
  map rendered from Natural Earth.
- **The attribution and the licence obligations travel with it.** A report
  containing OSM-derived place names carries ODbL data, and the person it is
  sent to has to be told.
- **A visible caveat block**, not a footnote: what confidence band this is and
  what that means.

## Explicitly not in scope

Anything that makes the report look more certain than the analysis was. No
"CONFIDENTIAL" banner, no fake case number, no letterhead. This is a tool
output, and dressing it as an official record would be the single most harmful
thing it could do.

## Done when

- A report opens correctly with the network off.
- An `insufficient` result exports a report that says so at the top, in the
  same weight as a `high` one says its location.
- Every OSM-derived field in an exported report is covered by the attribution
  block, asserted by a test.
