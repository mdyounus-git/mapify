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

**14 of 20 phases implemented (70%).**

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
| 14 | Landmark verification against OSM and Wikidata | ⬜ implementable |
| 15 | Shadow and sun-angle latitude estimation | ⬜ implementable |
| 16 | A second locator, and an ensemble | ⬜ implementable |
| 17 | GPU support | ⬜ implementable |
| 18 | Session history | ⬜ implementable |
| 19 | Accessibility and keyboard completeness | ⬜ implementable |
| 20 | Installer | ✅ implemented |

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
