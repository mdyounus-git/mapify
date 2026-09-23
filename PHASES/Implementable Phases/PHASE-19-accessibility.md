# Phase 19 — Accessibility and keyboard completeness

**State:** implementable

---

## Why

Ctrl+O, Ctrl+S and arrow-key candidate navigation exist. That is not the same
as being usable without a mouse, and nothing has been checked with a screen
reader.

A dense, dark, information-heavy interface is exactly the kind that is easy to
make unusable without noticing, because the person building it is looking at it
and it looks fine.

## Scope

- Full keyboard reachability with a visible focus ring everywhere. The ring
  exists in the stylesheet but has not been walked through end to end.
- ARIA roles and live regions, so progress and results are announced rather
  than silently replacing content.
- A contrast audit against WCAG AA. The palette was chosen by eye, and
  `--ink-4` on `--panel` is the token most likely to fail.
- **The globe needs a text equivalent.** A screen-reader user should get the
  coordinate, the place and the confidence without the canvas existing.
- `prefers-reduced-motion` already disables transitions; the camera flight
  should honour it too and cut directly rather than arc.
- Test at 150% and 200% Windows display scaling, which is common and untried.

## Done when

- Every control is reachable and operable by keyboard, walked through and the
  walk-through recorded.
- Contrast is measured, not estimated, and failures are fixed.
- A full analysis is comprehensible with the canvas ignored entirely.
