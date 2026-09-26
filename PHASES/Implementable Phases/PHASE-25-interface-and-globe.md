# Phase 25 — The globe settles, and the interface stops looking generated

**State:** implementable

---

## The globe

**It must come to rest.** The idle rotation is right while nothing has been
found; continuing to drift after landing on a result is wrong. The camera has
made an assertion and the user is reading it, and a moving target is hostile
to that. Rotation stops on arrival and resumes only when the result is
cleared.

**Quality.** 8k textures on a sphere are about 4.9 km per texel, visibly soft
as soon as the camera gets close. The work: 16k imagery for the region being
approached, anisotropic filtering at the device maximum, a real normal map on
the landing area, and a render scale that follows the display pixel ratio
rather than assuming 1.

## The interface

The criticism is that it looks generated, and it is fair. The specific faults,
rather than a vague restyle:

- **Everything is the same.** Uniform spacing, uniform weight, uniform card.
  Nothing on the screen says which parts matter, so the eye has nowhere to
  land. Real instruments have a hierarchy: one thing is the answer and
  everything else is subordinate to it.
- **Decorative typography.** Monospace on things that are not measured values,
  eyebrow labels above panels that need no label, capitals used for emphasis
  rather than for a class of thing.
- **The panels do not earn their width.** Two fixed rails at all times,
  whether or not they have anything in them.
- **Motion that means nothing.** Transitions on elements that are not moving.

The fix is subtractive: fewer surfaces, fewer borders, one accent used for one
purpose, spacing that varies with importance, and the confidence verdict given
the visual weight it actually has. Measured against a real instrument panel,
not against another dark-themed dashboard.

## Done when

- The globe is still when a result is on screen, and moving when one is not.
- Close approach is sharp on a display that can show it, and the HUD says what
  resolution is actually being drawn.
- The interface has a clear first thing to look at in every screen state, and
  no element that exists only to fill space.
