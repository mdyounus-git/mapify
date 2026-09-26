# Phase 25 — The globe settles, and the interface stops looking generated

**State:** implementable — two pieces already landed, the rest is open

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

---

## Already done, out of order

Two items were pulled forward because they were cheap and the complaints
about them were specific.

**The globe comes to rest.** Arriving settles it, and it stays settled
through panning and zooming until a new image clears the result. The cloud
shell still turns, which is weather rather than the camera, and is what keeps
a still frame from reading as a crashed renderer.

**The answer has somewhere to be.** A finding card sits on the globe, next to
the thing it is an assertion about, and leads with whether the location was
*read from* the image or *estimated from* it — a distinction the interface
previously did not make anywhere. The right rail stopped repeating the
headline and became the detail behind it.

That fixed the worst of the "no hierarchy" problem. What is left of the
interface criticism is the smaller stuff listed above: the eyebrow labels,
the monospace on things that are not measurements, rails that hold their
width whether or not they have anything in them.

**The globe's resolution is untouched and is the bigger remaining piece.**
Pixel ratio already follows the display and anisotropic filtering is already
at the device maximum, so the softness on approach is not a filtering
problem — it is that 8k Blue Marble is 4.9 km per texel and there is nothing
finer to show. That is Phase 24's tile work, and it cannot be fixed here.
