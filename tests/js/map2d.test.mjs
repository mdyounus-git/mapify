// The 2D map's own arithmetic: the scale handed over from the globe, the
// uncertainty circle, and footprints as GeoJSON. Run by tests/test_map2d_js.py.

import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const here = resolve(process.argv[2] || ".");
const m = await import(pathToFileURL(resolve(here, "mapify/web/js/map2d.js")).href);
const { zoomForSpan, spanForZoom, circleRing, footprintFeatures, ZOOM_HANDBACK } = m;

// The globe hands over at the scale it was showing, and takes back the same.
for (const [span, lat] of [[1200, 13.04], [8000, 48.86], [300, -33.86]]) {
  const z = zoomForSpan(span, lat, 800);
  assert.ok(Math.abs(spanForZoom(z, lat, 800) - span) < 1e-6, "inverse of each other");
}
assert.ok(zoomForSpan(1000, 60, 800) < zoomForSpan(1000, 0, 800), "Mercator: higher latitude, nearer zoom");
assert.ok(ZOOM_HANDBACK > 8 && ZOOM_HANDBACK < 12);

// The uncertainty circle: closed, and every point the radius from the centre.
const ring = circleRing(80.2341, 13.0418, 2500, 36);
assert.equal(ring.length, 37);
assert.ok(Math.abs(ring[0][0] - ring[36][0]) < 1e-9 && Math.abs(ring[0][1] - ring[36][1]) < 1e-9);
const R = 6371008.8, d = Math.PI / 180;
for (const [lon, lat] of ring) {
  const s = Math.sin((lat - 13.0418) * d / 2) ** 2 + Math.cos(13.0418 * d) * Math.cos(lat * d)
    * Math.sin((lon - 80.2341) * d / 2) ** 2;
  assert.ok(Math.abs(2 * R * Math.asin(Math.sqrt(s)) - 2500) < 0.5, "on the circle");
}

// Footprints: polygons as the server sends them, flat, with their source.
const fs = footprintFeatures([
  { id: "osm/1", source: "osm", name: "Tower", polygon: [[[0, 0], [0, 1], [1, 1], [0, 0]]] },
  { id: "gob/2", source: "gob", polygon: [[[2, 2], [2, 3], [3, 3], [2, 2]]] },
  { id: "x", polygon: [] },
]);
assert.equal(fs.length, 2);
assert.equal(fs[0].geometry.type, "Polygon");
assert.equal(fs[1].properties.source, "gob");
assert.ok(!("height" in fs[0].properties), "nothing extruded");

console.log("map2d.test.mjs: all passed");
