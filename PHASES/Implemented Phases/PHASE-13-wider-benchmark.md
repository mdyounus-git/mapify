# Phase 13 — A wider benchmark, and a calibration that survives it

**State:** implemented

This phase was flagged as the highest-value remaining work, and it earned that
by immediately proving the tool's central claim was false.

---

## Goal

Replace an eleven-sample benchmark whose calibration table had cells with n=1,
and find out whether "it is never confidently wrong" was true or merely
untested.

## What was built

**`scripts/sample_sites.py`** — a deliberate sampling frame of 165 sites rather
than whatever was convenient. Tagged by region and by scene (landmark, street,
rural, coastal), with Africa and South America over-sampled relative to their
share of photo corpora, because a benchmark that mirrors the bias of the
training data hides the weakness instead of measuring it.

**A resilient fetcher.** The first run died at sample 34 on a dropped
connection. Over 165 sites Wikimedia will drop one; that must cost a retry, not
the run. Now retries with backoff, skips a site that will not come, and
resumes from what it already has.

**An evaluator that reports what it does not know.** Refuses to print a figure
for a band with fewer than 10 samples, reports a reliability table, and breaks
results out by region and scene.

**`scripts/tune_confidence.py`** — refits the thresholds by searching on half
the samples and validating on the half it never saw, because tuning on the data
you then report is how a tool ends up with numbers that describe only its own
test set.

## What it found

The eleven-sample result was flattering, not merely small:

| | 11 samples | 163 samples |
|---|---|---|
| street ≤1 km | 45% | **18%** |
| city ≤25 km | 73% | **37%** |
| median error | 1.8 km | **368 km** |

And the central claim was broken. Under the old thresholds:

```
high    n=76  median 4.3 km  worst 15,689 km  72% within 200 km  OVERCONFIDENT
medium  n=44  median 698 km  worst 15,617 km  32% within 200 km  OVERCONFIDENT
```

A high-confidence call 15,689 km out. Dhaka read as Campinas, Brazil. Manila as
Havana. The eleven-sample set simply never contained a case where a tight,
confident GeoCLIP prediction was also completely wrong.

## Why the old rule failed

Comparing correct against incorrect high-confidence calls:

| diagnostic | correct (n=55) | wrong (n=21) |
|---|---|---|
| locator mass, median | 0.255 | 0.158 |
| locator mass, p25 | 0.131 | 0.084 |
| dispersion, p75 | 0.0 km | 113 km |
| concentration, min | 0.534 | **0.682** |

The old rule led with dispersion and concentration and treated locator mass as
a mild downgrade. That is backwards. **Concentration does not separate them at
all** — the wrong calls had a *higher* minimum than the correct ones. A
confidently wrong prediction looks exactly like a confidently right one on
dispersion, because GeoCLIP is equally sure either way.

Mass is what separates them, across the whole sample:

```
mass 0.00–0.02    0% within 200 km
mass 0.02–0.05   14%
mass 0.05–0.10   33%
mass 0.10–0.20   46%
mass 0.20–0.40   68%
mass 0.40+       62%
```

## The negative result worth recording

The first search asked for thresholds giving **zero** high-confidence misses on
at least 12 calls. Across 1,728 threshold sets, none existed.

The available diagnostics cannot support an absolute promise. That is a
property of the evidence, not a failure of the search, and the honest response
was to narrow the promise rather than tune until it looked acceptable.

## What was changed

`high` now requires **all four** conditions rather than a majority:

```
locator mass    >= 0.25
dispersion      <= 20 km
concentration   >= 0.80
evidence support >= 0.0
```

Chosen because it scored 100% precision on the tuning half **and 100% on the
held-out half** — the second number is the one worth quoting.

`medium` no longer promises a distance. Fitting showed it tops out around 85–89%
within 1500 km, so it reports that measured rate instead, in the interface as
well as the README.

Toponym corroboration can still lift a band, but **no longer to `high`**. There
is no measurement showing a gazetteer match makes a high-confidence call safe,
and `high` is the one promise this tool makes.

## Verification, after the change

```
high          n=20  median   1.2 km  worst   106 km  100% within 200 km   ok
medium        n=54  median  15.5 km  worst 15,110 km  89% within 1500 km
low           n=51  median   658 km                   63% within 1500 km
insufficient  n=38  median 6,985 km                   16% within 1500 km
```

Monotonic from 100% to 16%, so the bands carry real information. Five new tests
pin the behaviour down, including the exact regression: a perfectly tight,
perfectly concentrated cluster with thin mass must not reach `high`.

## What it also exposed

| Region | n | ≤25 km | median |
|---|---|---|---|
| North America | 19 | 58% | 15 km |
| Africa | 32 | **12%** | **2,396 km** |

| Scene | n | ≤25 km | median |
|---|---|---|---|
| landmark | 11 | 82% | 0.1 km |
| street | 130 | 34% | 561 km |

GeoCLIP is dramatically worse in Africa, and landmarks are the easy case by a
factor of two and a half. Both are now in the README rather than buried in a
headline average.

## Left open

- 163 samples makes `high` measurable at n=20, which is enough to state a rate
  and not enough to be comfortable. More would be better.
- Every sample comes from Wikimedia Commons, which is its own selection
  effect: photographs people chose to upload and geotag.
- Nothing here improves accuracy. It measures it, and stops the tool
  overstating it. Accuracy is Phase 16.
