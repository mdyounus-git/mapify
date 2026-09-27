# Phase 26 — The locator was being read wrong

**State:** implemented

Not in the plan. It came from a user saying the mapping was inaccurate.

---

## The complaint, and whether it was fair

> the mapping is not perfect, very inaccurate mapping

It was fair. Median error 342 km; more than half of photographs placed further
than 25 km from the truth.

## Diagnosis before treatment

`scripts/diagnose_accuracy.py` measures four numbers for each photograph
instead of one, because the plausible causes have nothing in common and the
fixes for them are different work:

```
locator's own top-1        594.6 km median    28 within 1 km
best in its shortlist       50.2 km           47
locator centroid           581.2 km           28
what Mapify reported       341.8 km           31
```

Which settles three things at once. The fusion **is** helping — 594 to 342,
better on 57 samples and worse on 23 — so the evidence machinery is not the
problem. Only 32 of 163 are hopeless in the sense that nothing near the truth
was proposed at all. And in 102 of 163 cases the shortlist already contained a
better answer than the one reported.

So: the ranking, not the locator.

## Two fixes that failed, measured

**Refitting the evidence weights.** `tune_weights.py` dumps every candidate
once with each piece of evidence recorded as a *unit* contribution — the
agreement term with the weight divided out — so a 972-point grid can be
searched without re-running a model. Phase 13's split: fit on half, report on
the other. The fitted weights improved the median on the fit half by shuffling
hopeless cases and placed **not one extra image** within 25 km; held out they
were identical to the existing ones. The hand-chosen weights are fine.

**Reporting a cluster's strongest member instead of its centroid.** The
oracle said the best point inside the winning clusters would have put 47
images within a kilometre against the centroid's 33, so the averaging looked
like pure loss. Measured: worse. Median 352 against 342, 15 better and 34
worse, sign test p=0.009. The centroid averages out the gallery's sampling
noise, and the best-*scoring* member is not the best member — that 47 was an
oracle only visible with the answer already in hand. Reverted.

## The fix that worked, and why it was invisible

GeoCLIP softmaxes over 100,000 gallery points and `locate()` returns the
twelve with the highest **individual** probability. That is not the twelve
most likely *places*, because the gallery is not evenly dense. A city covered
by fifty points at half a per cent each holds a quarter of the distribution
and can contribute nothing to a point-wise top twelve, while a sparsely
sampled place with twelve points at 2% takes the whole list.

The number that made this obvious:

```
probability mass held by the top-12 shortlist:  median 14.5%

  where it held under 10% (n=57):  median error 2,603 km
  where it held over  25% (n=48):  median error    20 km
```

Everything downstream — the evidence re-ranking, the clustering, the
confidence banding — was reasoning about a seventh of what the locator knew,
and the samples where most was discarded are precisely the ones that went
wrong.

`locate_modes()` sums probability within a radius of each strong point and
takes modes greedily, removing each mode's neighbourhood as it goes. The
distribution was already being computed inside `locate()` and thrown away, so
it costs nothing.

### Both parameters were swept, not guessed

`scripts/sweep_modes.py` computes the distribution once per photograph and
evaluates every combination against it, so twelve settings cost one pass
rather than twelve benchmark runs:

```
radius  focus   median   <=1km  <=25km  <=200km
point-wise        594.6     28      50       64
   40      5      427.4     33      62       73
   75     15      385.3     27      59       75
  150      5      385.3     30      55       72
```

Gathering wide finds the right region; averaging wide then throws away the
precision found inside it. 40/5 beats point-wise selection on **every** band,
which none of the wider settings manage. My first guess had been 75/15 — the
worst corner for precision.

## Result

```
              median   <=1  <=25  <=200  <=2500   high  worst high
before         341.8    31    62     75     114     20      106 km
after          317.1    28    64     77     121     26       47 km
```

Seven fewer answers on the wrong continent, six more usable high-confidence
calls, and the worst high-confidence error more than halved. Street-level
precision costs three images, which is the one regression and is stated
rather than buried.

## The part that was not optional

The confidence thresholds had to be refitted. They were calibrated against a
`locator_mass` whose median was 0.145; mode finding captures about 99%, so
every threshold would have been trivially satisfied and **everything would
have read "high"**. A tool that silently changes the scale its calibration was
measured on stops being calibrated, and would have failed in exactly the
direction Phase 13 exists to prevent.

Refitted on the same held-out split: 100% precision on 17 high-confidence
calls fitting, 100% of 11 held out, worst 47 km.

The search wanted the evidence gate removed entirely (`evid >= -1.0`). It is
kept at zero. The search maximises how many high calls are possible at full
precision; this project would rather make fewer, and "high requires the image
to support the answer, not merely fail to object" is a stance worth more than
six extra calls.

## Left open

- Street-level precision is slightly worse and the reason is understood:
  averaging within 5 km of the peak trades a little precision for a lot of
  robustness. A locator with a denser gallery would not face the trade.
- 32 of 163 photographs have nothing near the truth anywhere in the
  distribution. No amount of re-ranking reaches those; they need a second
  locator (Phase 16) or evidence from outside the image model.
- Africa remains the weak region: 16% within 25 km against 58% for North
  America. That is GeoCLIP's training distribution, not something this phase
  could move.
