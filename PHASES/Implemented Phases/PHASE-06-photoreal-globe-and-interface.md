# Phase 06 — Photoreal globe and the interface rebuild

**State:** implemented · commit `e847707`

Triggered by a direct and accurate complaint: the globe did not look real and
the interface looked generic. Both were symptoms of specific decisions.

---

## Goal

Make the globe read as a photograph rather than a texture on a ball, and the
interface as an instrument rather than a dark dashboard.

## The globe

**The core problem was the light.** The sun direction was being lerped toward
the camera every frame, so the terminator could never be anywhere and the
planet looked flat. `subsolarPoint()` now computes the subsolar latitude and
longitude from a date using the NOAA low-precision solar algorithm, and three
modes decide where to put it: lit for the target, the real sun right now, or
flat.

Everything else follows from having a real light:

- **Atmosphere in two parts.** Aerial perspective on the surface washes the
  limb out and turns orange near the terminator; a separate shell handles the
  glow outside the silhouette, with a density that is two stacked exponentials
  — a bright band hugging the surface and a broad thin haze above it. One
  exponential alone is either a hairline or a fog bank.
- **Clouds** as their own lit shell, casting a soft shadow onto the surface by
  sampling the cloud map offset along the sun direction in the tangent plane,
  and catching sunset colour where the sun is low.
- **Night lights** tinted like sodium lamps, occluded by cloud, with the Black
  Marble composite's blue ocean wash filtered out so only real lights remain.
- **Relief** from the GEBCO elevation map perturbing the normal in a tangent
  frame, divided by cos(latitude) so Greenland stops growing mountains the
  equirectangular projection invented.
- **Bloom**, thresholded above white so lights and the limb bleed but daylight
  does not.
- **8k textures** rebuilt from the 21600x10800 Blue Marble master, the 13500px
  Black Marble and the 8192px cloud composite, plus an ocean mask rendered from
  the Natural Earth coastlines — deriving "is this water" from image luminance
  mistook dense forest for ocean and sea ice for cloud.

## The interface

A real design system rather than the default dark-dashboard look: Windows own
type (Segoe UI Variable), which matches the OS and needs no outbound request; a
desaturated graphite palette where colour is reserved for meaning — confidence,
provenance, agreement — and nothing else may use it; surfaces layered by
luminance with a 1px lit top edge; styled selects instead of browser defaults;
tabular numerals on every coordinate; instrument corner ticks on the viewport;
real empty states; a progress card showing which of seven stages is running.

## Verification

Rendered at 1580x940 and inspected: real terminator with North American city
lights, cloud volume over the Atlantic, a warm sunset band along the
terminator, and a soft blue limb. A full analysis of the Bengaluru sample
lands at 2,159 km over southern India with the imagery still sharp.

## What went wrong

Four things, all found by looking at the output:

- **A hard ring around the planet.** A Fresnel term on a back-side shell peaks
  at the shell's own silhouette, which is exactly the wrong place. Replaced
  with a falloff measured from the view axis.
- **The planet washed out on close approach** because the camera passed inside
  the atmosphere shell and the back-side sphere filled the screen with additive
  haze. Now fades across the boundary.
- **A searchlight in the mid-Atlantic.** The sun glint had a specular lobe wide
  enough to cover an ocean and bloom amplified it. Exponent raised from 260 to
  1400, intensity halved.
- **A stale stylesheet.** The desktop rewrite had dropped the `Cache-Control`
  header, so the window was rendering the previous CSS. Found because the new
  layout did not apply and the served file was the wrong length.

## Left open

The base imagery is 4.9 km per texel, which is the real limit on how close an
approach can usefully go. Handled in Phase 07 by refusing to descend past it
and saying so; going lower needs ground tiles.
