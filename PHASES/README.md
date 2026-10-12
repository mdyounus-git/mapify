# Mapify — phase plan

The work is divided into phases. A phase is a self-contained piece of the tool
that can be built, verified and argued with on its own.

```
PHASES/
├── Implemented Phases/      — built, verified, in the repository
└── Implementable Phases/    — specified, not yet built
```

A phase moves from *Implementable* to *Implemented* only when it is working in
the product and there is evidence it works. Every implemented phase document
records what was actually measured, what it cost, what went wrong on the way,
and what it deliberately left open. A phase with no verification section is not
finished, whatever the code says.

---

## Status

**19 of 27 phases implemented, four more partly (about 79% counting the
parts; 2026-10-12).** The research-led upgrade is tracked in
[`MILESTONE-research-upgrade.md`](Implementable%20Phases/MILESTONE-research-upgrade.md).

| # | Phase | State |
|---|---|---|
| 01 | Foundations — offline geocoding, EXIF trust, country attributes | ✅ implemented |
| 02 | The globe — 3D Earth and the approach flight | ✅ implemented |
| 03 | The analysis pipeline, and a benchmark that holds it honest | ✅ implemented |
| 04 | Windows desktop application | ✅ implemented |
| 05 | Test suite | ✅ implemented |
| 06 | Photoreal globe and the interface rebuild | ✅ implemented |
| 07 | Performance — the gallery cache | ✅ implemented |
| 08 | Licensing, documentation, configuration | ✅ implemented |
| 09 | Packaging — Mapify.exe | ✅ implemented |
| 10 | OCR and script-adaptive reading | ✅ implemented |
| 11 | Batch analysis | ✅ implemented |
| 12 | Report export | ✅ implemented |
| 13 | A wider benchmark, and a calibration that survives it | ✅ implemented |
| 14 | Landmark verification against OSM and Wikidata | ◨ started — Wikidata landmarks feed Pinpoint, not the estimate |
| 15 | Shadow and sun-angle latitude estimation | ⬜ implementable |
| 16 | A second locator, and an ensemble | ◨ built (PLONK), off until measured |
| 17 | GPU support | ⬜ implementable |
| 18 | Session history | ⬜ implementable |
| 19 | Accessibility and keyboard completeness | ⬜ implementable |
| 20 | Installer | ✅ implemented |
| 21 | Read the address, not just the city | ✅ implemented |
| 22 | Metadata first, then everything the image itself shows | ✅ implemented |
| 23 | Ask for another photograph rather than guess | ✅ implemented |
| 24 | Get down to the street | ◨ mostly — Pinpoint, the 3D view and the walk |
| 25 | The globe settles, and the interface stops looking generated | ◨ part done |
| 26 | The locator was being read wrong | ✅ implemented |
| 27 | Should text clues steer the answer? Measured | ✅ implemented |

---

## What the phases are ordered by

Not by how interesting they are. By what would make the tool wrong or
unusable if it were missing:

1. **Phases 1–3** are the tool. Without them there is no estimate, no place
   name, and no way to know whether either is any good.
2. **Phases 4–5** make it a program rather than a script, and make its claims
   checkable.
3. **Phases 6–10** are the difference between something that works and
   something a person would use: it has to look like an instrument, respond in
   seconds rather than a minute, install without a Python toolchain, and read
   the writing that is actually in the photograph.
4. **Phases 11 onwards** widen it. None of them are needed for a single image
   to be analysed correctly, which is why they come after.

## The rule that outranks the plan

> The tool is allowed to be wrong. It is not allowed to be confidently wrong.

Any phase that would improve a headline number while weakening that property
does not get built.

Phase 13 is why this is written down. On eleven samples the rule appeared to
hold; on 163 it did not, and `high` confidence was being assigned to results
15,689 km off target. The thresholds were refitted on a held-out split and the
promise narrowed to what the evidence supports. The headline accuracy numbers
got substantially worse in the process, and that was the right outcome.

## Where 21 to 25 came from

Not from the plan. From someone using it.

A photograph of a medical institute, with the institute's own board legible in
frame — name, street, town, postcode — came back as a scatter across Tamil
Nadu and Hungary and settled on "India". Everything needed to answer it was
printed in the picture. The tool read the text and had no way to act on it,
because a toponym could only re-rank what the visual model had already
guessed; it could not propose a location of its own. That is Phase 21, and it
is the most valuable thing in this file.

The rest of that session's feedback is Phases 22 to 25: investigate in a
stated order and look at far more of the image; ask for a second photograph
instead of guessing; get close enough to the ground that the answer can be
recognised rather than believed; and stop the globe drifting once it has
landed on something, in an interface that currently looks generated.
