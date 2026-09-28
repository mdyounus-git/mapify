# Next milestone

Three things, in this order. The first is a correctness bug and outranks
the other two.

Set down 2026-09-28, immediately after Phase 1 of the 153 landed
(`6e61743c`, 374 tests passing).

---

## 1. The poster bug — the tool located an image that has no location

**What happened.** A poster of cyber-security content — no street, no
building, no sky, nothing geographic — was analysed and came back with
map locations.

**Why it happened, and why it was inevitable.** GeoCLIP is a softmax over
a gallery of 100,000 GPS points. A softmax has to sum to one. Feed it a
poster, a spreadsheet screenshot or a photograph of a cat and it still
returns a ranked list of coordinates, because returning nothing is not
something the architecture can do. Every downstream stage then treats
that list as evidence. Nothing in the pipeline currently asks the prior
question: *is this a photograph of a place at all?*

This is the exact failure the specification's own rule names — "never
fabricate coordinates" — and it is worse than an inaccurate answer,
because an inaccurate answer is at least about somewhere.

**The fix, in three independent signals.** One is not enough; each has a
failure mode the others do not.

1. **An image-kind prompt group.** Outdoor scene / indoor scene /
   document or poster / screenshot or UI / diagram or chart / artwork or
   illustration / product shot / portrait. Descriptive, constrains
   nothing. Only the first two are locatable by this tool.

2. **Distribution concentration, which is already computed.** The Phase
   26 mode-finding work added `mass_near()` over the full gallery
   distribution. A real place concentrates probability; a poster spreads
   it thinly across every continent. The thresholds refitted in Phase 26
   (`high_concentration 0.80`, `high_locator_mass 0.15`) are the right
   instrument and already exist — what is missing is a *floor* below
   which the answer is withheld entirely rather than reported as "low
   confidence".

3. **Pixel forensics, which also already exists.** Technique 5 flags
   screen-resolution dimensions, alpha channels and PNG export. A poster
   is very often a screenshot or an export.

**The behaviour to build.** When the gate says this is not a photograph
of a place, the report must say so and show **no coordinates, no
candidates and no globe pin** — not a dimmed pin, not a pin with a
caveat. It should say what the image appears to be instead, and that
nothing about its location can be determined from it. The techniques
that *did* run still report: text was read, a script was identified.
Those are true and useful. The location is not.

**How to know it works.** The benchmark cannot answer this — all 163
samples are photographs of places. A second small set is needed:
posters, screenshots, documents, diagrams, product shots, indoor
close-ups, and perhaps twenty genuine photographs to prove the gate does
not swallow them. The measure is a gate that refuses ~all of the first
group and ~none of the second, and the second number matters more: a
gate that starts refusing real photographs has broken the tool to fix a
poster.

---

## 2. The remaining 138 techniques

Phase 1 (techniques 1–15) is done, tested and in the pipeline. The
framework — registry, `Finding`, `Context`, `Constraint`, case file — is
built and is what the rest hangs off. `PHASES/TECHNIQUE-AUDIT.md` has
every technique classified against the code.

Order to build them in, and why:

| Their phase | Techniques | Why here |
|---|---|---|
| 3 — solar & astronomical | 33–42 | The one large block that is **genuinely independent** of the image encoder. Shadow angle is a physical measurement, not another opinion about the same embedding. This is also all of chronolocation, which the specification wants and the tool has none of. |
| 7 — text & structured extraction | 119–124 | Extends Phase 21, the single most accurate thing in the pipeline (0.00 km on a legible board). Phone numbers, postcodes, opening hours, price formats, domain TLDs — all read off the image, all independent. |
| 8 — forensics | 54, 55, 138–140 | `mapify/forensics/` now exists with pixel inspection in it. Compression history and metadata-vs-pixel disagreement build directly on it. |
| 2 — scene & infrastructure | 17, 21, 26, 30, 32 | The leftovers. **Expect these to do nothing.** Four of their neighbours were built and measured out; the reason generalises, and is written up in `prompts.py`. Build them as descriptive, not as fusion inputs, unless a benchmark run says otherwise. |
| 9/10 — evidence map, orchestration | 151, 153 | `Finding.regions` already carries normalised boxes for the annotated evidence map; nothing draws them yet. |
| — | blocked: 8 techniques | Reverse image search, repost tracing, cross-source matching, Street View. No free licence permits them. Recorded in the audit; not to be quietly attempted. |

**The rule that governs all of it, unchanged:** a technique that
constrains geography must be measured on the benchmark before it is
allowed to. Reporting needs no permission; scoring does.

---

## 3. The open geospatial sources

Requested: Planet OSM, QGIS, kepler.gl, deck.gl, Google Research Open
Buildings, USGS EarthExplorer (Landsat). Goal: **after zooming in, look
at buildings from various angles.**

Licences must be confirmed from each project's own terms before any code
ships — that is this project's standing rule and it has already caught
one source (OSM tile servers in Phase 24). What follows is the plan and
the questions it has to answer, not a licence finding.

### What actually serves the goal

Viewing buildings from various angles means **geometry**, not imagery.
Satellite imagery is flat: rotating the camera over a photograph of a
rooftop shows the same rooftop. What lets you orbit a building is a
footprint with a height, extruded.

So the spine of this work is:

- **OSM building footprints** (ODbL — attribution and share-alike apply),
  with `building:levels` and `height` tags where present, fetched for a
  small bounding box around the landed coordinate. Overpass API for a
  desktop app fetching one small box per landing; its usage policy needs
  reading first, and Geofabrik regional extracts are the fallback if it
  does not permit this. **The full planet PBF is ~80 GB and is not the
  right instrument for a per-landing box.**
- **Google Research Open Buildings** where OSM is thin — Africa, South
  and South-East Asia, Latin America. Distributed as per-S2-cell CSV.
  Confirm the exact licence text and the attribution string it requires
  before use.
- **deck.gl** (MIT) to render them extruded, or direct extrusion in the
  existing Three.js globe. deck.gl is the lighter decision only if the
  building view is a separate surface; mixing two WebGL renderers in one
  canvas is not. **Decide this before writing either.**

### Where each requested source honestly lands

| Source | Use | Note |
|---|---|---|
| **Planet OSM** | building footprints, heights, street geometry | ODbL: attribution *and* share-alike on derived data. Use an API or regional extract, not the 80 GB planet file. |
| **deck.gl** | extruded building layer, orbitable | MIT. The direct fit for the goal. |
| **Google Open Buildings** | footprints where OSM is sparse | Confirm licence and attribution; coverage is the global south, which is where OSM most needs it. |
| **kepler.gl** | export a case as a kepler.gl dataset + config | MIT. An export path, not an embed — it is a full React application. |
| **QGIS** | export a case as GeoPackage / QGIS project | GPL. Not embeddable; the right relationship is that Mapify writes files QGIS opens. GDAL is the embeddable piece if one is needed. |
| **USGS Landsat** | **cannot show buildings** | 30 m per pixel, 15 m panchromatic. A house is a fraction of one pixel. Landsat is excellent for terrain, land cover and season — which is real value for the chronolocation work above — but it will not serve the stated goal, and shipping it as though it might would produce a view that looks broken. Say this in the interface rather than rendering a grey smear. EarthExplorer also requires a free account for bulk download. |

**What would give photographic building detail** — and the honest answer
is: very little, globally and freely. NAIP (~0.6 m, public domain) covers
the United States only. Several countries publish open orthophotos at
sub-metre resolution. There is no free global sub-metre source, and the
tool should say which source is in use and what its resolution is at
every zoom, the way the Phase 24 HUD already does.

### Sequence

1. Confirm every licence from its own terms. Write them into
   `ATTRIBUTION.md` before any fetching code exists.
2. OSM footprints for one bounding box, cached, with the attribution
   line displayed.
3. Extrusion and orbit in the existing globe.
4. Open Buildings as the fallback source where OSM returns nothing.
5. kepler.gl and QGIS export.
6. Landsat stays where Phase 24 put it — NASA GIBS, terrain scale, with
   its resolution stated.

---

## State when this was written

- `6e61743c` — Phase 1 of the 153. 374 tests pass.
- The installed application at `E:\mapify\app` **predates Phase 24 and
  all of the accuracy work**. It needs a rebuild and reinstall before
  the user sees any of this.
