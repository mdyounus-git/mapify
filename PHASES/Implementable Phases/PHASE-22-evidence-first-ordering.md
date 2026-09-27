# Phase 22 — Metadata first, then everything the image itself shows

**State:** partly implemented — step 2 is done, step 4 is open

An explicit order of investigation, and a great deal more looked at.

---

## The order

Requested directly, and right:

1. **Read the metadata first.** If there is a GPS fix, that is the first
   hypothesis — not the answer.
2. **Test it against the picture.** If the image says Norway and the tag says
   Bangkok, the tag is discarded with the reason stated. Metadata is the
   easiest part of a file to edit and the least costly to fake.
3. **If there is no metadata, skip it silently** and carry on. An absent GPS
   tag is not a finding and should not be reported as one.
4. **Then read the image properly** — everything in the frame that carries
   information about where it is.

Steps 1-3 mostly exist: `mapify/exif.py` already treats a GPS tag as a claim
and carries tamper signals, and the report keeps embedded GPS visually
separate from the estimate. What is missing is step 2 as an actual *test*
rather than a distance readout, and step 4 in any depth.

## What step 2 needs

Today the pipeline computes the distance between the tag and the estimate and
prints it. That is a measurement, not a decision. It should be a decision:

- tag and image agree, within the estimate's own uncertainty — the tag is
  corroborated, and the two together justify a tighter answer than either
  alone;
- they disagree, and the image evidence is strong — the tag is **rejected**,
  in those words, with the evidence that rejected it;
- they disagree, and the image evidence is weak — neither is trusted, and
  that is said plainly.

## What step 4 needs

The evidence pass currently scores twelve mutually-exclusive groups. It is
worth extending, in this order of expected value:

- **Signage and lettering** beyond place names: shop-type words, language of
  the incidental text, the shape of the street-name plate, phone number
  formats, the format of the postcode itself.
- **Vehicles**: plate aspect ratio and colour, which are close to decisive
  between continents; taxi livery; bus type; the side of the vehicle the
  driver sits on, which is independent evidence from the side of the road.
- **Roads**: line colour and pattern, kerb painting, bollard and barrier
  design, the shape of road signs, the design of traffic lights and where
  they are mounted.
- **Infrastructure**: utility pole material and cross-arm shape, overhead
  versus buried cable, the design of street lights, manhole covers, fire
  hydrants, post boxes.
- **Buildings**: roof material and pitch, window proportion, balcony and
  shutter style, wall material, the presence and style of air-conditioning
  units.
- **People**, stated carefully. Clothing conventions, uniforms and what is
  worn for the weather are legitimate geographic evidence. Inferring ethnicity
  from faces is not: it is unreliable, it is the same inference that powers
  the worst uses of this class of tool, and this project will not do it. The
  prompt bank will say so where a reader would otherwise wonder why it is
  absent.
- **Environment**: vegetation type, soil colour, sun angle and shadow
  direction, snow, the state of the sky.

Each group is cheap — one more set of text embeddings against an image
embedding already computed — so the cost is a few hundred milliseconds for
all of them together, not a few hundred each.

## The honest part

Adding groups adds ways to be confidently wrong. Every group added here has to
be measured on the 163-sample benchmark before it is kept, and any group that
does not improve the error distribution gets deleted rather than left in
because it sounded clever. Phase 13 is the precedent: a negative result is a
result.

## Done when

- A rejected GPS tag says it was rejected, and why, in the report and the
  export.
- An absent GPS tag produces no noise at all.
- Every new evidence group is accompanied by its measured effect on the
  benchmark, kept or deleted on that basis.
- The prompt bank states, in the file, why faces are not used.

---

## Done: the tag now gets a verdict

Steps 1 and 3 already worked. Step 2 — *test the tag against the picture* —
was the gap, and it is closed.

The old code measured the distance between the tag and the estimate and
printed it with a boolean. That is a readout, and it leaves the decision with
the reader at exactly the point where the tool knows more than they do.

There are three outcomes:

| verdict | when | what happens |
|---|---|---|
| `corroborated` | the image independently agrees | the tag *becomes* the reported coordinate — it is metres where a model estimate is kilometres — with provenance saying so and precision set to 20 m |
| `rejected` | they disagree **and** the image evidence is strong: a read address resolves elsewhere, or the locator committed real mass elsewhere | the estimate stands, the tag is still reported, and the note says it was rejected and why |
| `unresolved` | they disagree and the image evidence is too weak to adjudicate | neither is trusted, and that is said |

The middle distinction is the whole point. A locator holding four per cent of
its distribution has not earned the right to call a GPS tag a lie, and the old
boolean said "inconsistent" in both cases in identical words.

**Verified end to end.** The Kelambakkam board, re-tagged with Budapest
coordinates 6,811 km away:

```
verdict     rejected
note        ...an address read from the image itself resolves elsewhere...
reported    12.79716, 80.21760   Kelambakkam, India
error       0.00 km
tag         still reported: 47.4979, 19.0402
```

Then reduced to two stub tests with no network: the read-address case rejects,
and the identical case without it stays `unresolved`, so the second is the
control for the first.

## Still open: step 4

Everything under *What step 4 needs* above. None of it has been started, and
the rule stands — each new evidence group has to be measured on the 163-sample
benchmark before it is kept.
