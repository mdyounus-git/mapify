# Phase 14 — Landmark verification against OSM and Wikidata

**State:** implementable

Turn "there is a domed building here" into "that dome is at these coordinates".

---

## Why

Overpass already returns named features near the leading candidate, and they
are shown as enrichment. They are not used as *evidence*. If the image contains
a distinctive structure and Overpass says a structure of that kind stands 200 m
from the candidate, that is corroboration the model did not provide.

## Scope

- Extend the prompt bank with structure types that are both visible in a
  photograph and reliably mapped in OSM: minaret, church spire, pagoda,
  lighthouse, stadium, suspension bridge, cooling tower, castle, dome, obelisk,
  windmill.
- When a structure is called with confidence, query Overpass for that tag near
  each of the top candidates, not only the leading one.
- A match near one candidate and no match near its rivals is a strong,
  independent adjustment. Record it with the OSM id so a reader can check it.
- Wikidata, via its public SPARQL endpoint, for named landmarks — subject to
  its usage policy, cached, rate-limited, and off when `offline_only` is set.

## The trap

This is the easiest place in the project to build something that confirms
itself. Querying near a candidate and finding *anything* is not evidence. The
adjustment must depend on the structure being present near one candidate and
absent near the others. A test has to pin that down: a feature found near every
candidate must produce no adjustment at all.

## Done when

- A photograph of a minaret moves a candidate in a country with one nearby
  above a candidate with none.
- A feature present near all candidates produces zero adjustment, asserted.
- The query count stays bounded per analysis, asserted.
