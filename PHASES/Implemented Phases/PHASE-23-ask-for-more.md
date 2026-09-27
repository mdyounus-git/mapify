# Phase 23 — Ask for another photograph rather than guess

**State:** implemented, with one completion criterion unmet and stated

---

## Goal

When the evidence will not support a location, Mapify said so and stopped.
Honest, and not helpful: the person holding the phone usually has another
picture of the same place, and a second angle often contains the thing the
first one lacked.

This is also Phase 22's conclusion arriving from the other side. More prompt
groups against one embedding are correlated and did not help; a separate
photograph is genuinely independent evidence, and independence is the whole
difference.

## The design decision that matters

The obvious implementation is to analyse each image and average the answers.
It is wrong, and wrong in a way that is easy to miss: **averaging cannot tell
agreement from coincidence.** Three estimates that all say "somewhere in
France" average to the middle of France with no more justification than any
one of them had.

The right question is not "what is the average answer" but "which single
place explains all of these photographs". For independent observations that
is a product of likelihoods, a sum in log space:

```
score(x) = sum over images of log P_i(x)
```

and its behaviour is exactly what is wanted. A location every image puts mass
on multiplies up. A location only one image likes is multiplied by the
others' near-zero and falls away. The demonstration, on stubs:

```
image A   Tokyo 0.50   Paris 0.15
image B   Paris 0.16   Sydney 0.48
          -> neither image alone prefers Paris

combined  Paris 0.155   Tokyo 0.032   Sydney 0.031
```

## What broke, and the fix that was the real work

The first implementation combined the **shortlists** — GeoCLIP's top twelve.
That cannot answer the question the product rule asks. Two photographs of one
place rarely propose the same twelve gallery points out of a hundred
thousand, so every candidate was "absent" from all but the image that
proposed it, every term hit the same floor, and the combination quietly
stopped comparing places and started comparing confidences. The answer went
to whichever image was surest, right or wrong.

GeoCLIP computes the full distribution over all 100,000 points and `locate()`
discards all but twelve. It is now available on request — 400 KB an image,
taken by a session and not by a 500-image batch — with `mass_near()` to sum
probability within a radius of any coordinate. So image A can be asked what
it makes of the place image B suggested, which is the entire mechanism.

Measured on 24 photographs cut in half, two halves being different content of
unambiguously one place:

```
                      shortlists   full distribution
combined median         513.8 km        341.7 km
beat the better half       1/24            4/24
beat the worse half       13/24           17/24
```

## Verification, including what it does not show

**Duplicate suppression works.** Two photographs of the same signboard are
one observation photographed twice, and multiplying them squares a single
piece of evidence into confidence nobody earned. Near-duplicates are caught
on the CLIP embedding every analysis already computes — a dot product, no new
cost — and collapsed before any fusion runs. The same reasoning governs the
merges: per evidence group the most confident reading wins, so three pictures
of a wall do not dilute the one of a road, and only the most readable image
supplies OCR text, so one sign shot twice is not two toponyms.

**The scale is preserved.** Scores use a geometric mean rather than a sum, so
they stay comparable to a single image's and the Phase 13 confidence
thresholds keep the meaning they were fitted with. Asserted by a test,
because quietly re-banding every result would be the easy mistake.

**The second completion criterion is NOT met.** "Adding a second image of the
same place measurably tightens the answer" — it does not. The combination
reaches 341.7 km against the better half's 310.5 km. What it does is reach
that *without knowing which half was the better one*, which beats picking
blind (623 km on average), and it beats the worse half 17 times in 24.

That is the honest claim, and it happens to be the case the feature exists
for: **when the first photograph was the poor one — which is why Mapify
asked — adding another helps about seven times in ten.**

Halves are a pessimistic proxy. One camera, one instant, one exposure, and
often one half is sky. Real multi-angle pairs would be more independent and
both more informative, and there are none in the sample set. Same gap as the
read-address benchmark, and the same fix: real photographs.

45 tests. Full suite 344.

## What it looks like

The offer sits where the finding card would, because it occupies the same
slot in the argument — this is what the tool has to say, and it happens to be
a question. It says what to photograph rather than what was missing:

> Nothing in this photograph places it.
> Do you have another picture of the same place?
> - a sign, a shopfront or a street name plate — anything with words on it
> - a wider view showing the street, with traffic or parked cars in it
> - the horizon, or whatever the tallest thing around is
> - the ground and the buildings together, rather than one close up

Declining is an answer, not a failure: it ends the session and presents the
ranked possibilities that always existed, now framed as the reply to a
question the user asked.

## Left open

- No benchmark on real multi-angle pairs, which is why the tightening claim
  is not made.
- The session holds images in memory and is one-at-a-time per process. Fine
  for a desktop tool; wrong for anything shared.
- Nothing checks that two photographs really are of one place. The assertion
  is the user's, and combining images of different places would produce a
  confident answer about neither. The interface asks in those words and
  trusts the answer.
