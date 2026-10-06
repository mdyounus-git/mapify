// Walking through the 3D model: walls are solid, a walk that starts inside
// a building finds the street, and the scene's helpers draw only what the
// data supports. Run by tests/test_walk_js.py (needs Node).

import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const here = resolve(process.argv[2] || ".");
const walk = await import(pathToFileURL(resolve(here, "mapify/web/js/walk.js")).href);
const { BuildingIndex, nearestRoadPoint, floorLines, streetLabels, Walker } = walk;

// A 20 m square building north-east of the origin, with a 4 m courtyard.
const ring = [[77.0001, 12.0001], [77.0003, 12.0001], [77.0003, 12.0003],
              [77.0001, 12.0003], [77.0001, 12.0001]];
const court = [[77.00019, 12.00019], [77.00021, 12.00019], [77.00021, 12.00021],
               [77.00019, 12.00021], [77.00019, 12.00019]];
const building = { id: "b1", polygon: [ring, court], centroid: [77.0002, 12.0002],
                   height: 12, base: 0 };
const road = { name: "Test Road", class: "residential", width: 6,
               path: [[76.9995, 12.00005], [77.0010, 12.00005]] };

const index = new BuildingIndex([building]);
const M = 111320 * Math.cos(12 * Math.PI / 180);

// walls are solid; open ground and courtyards are not
assert.ok(index.hit(77.00015, 12.00015), "inside the walls is blocked");
assert.equal(index.hit(77.0000, 12.0000), null, "open ground is free");
assert.equal(index.hit(77.0002, 12.0002), null, "a courtyard is open");
assert.ok(index.hit(77.0001 - 0.2 / M, 12.0002), "standing 20 cm from a wall is too close");
assert.equal(index.hit(77.0001 - 1.0 / M, 12.0002), null, "a metre from a wall is fine");
assert.equal(index.hit(77.00015, 12.00015, "b1"), null, "the start building can be ignored");

// a walk that starts inside finds the street, facing along it
const p = nearestRoadPoint([road], 77.00015, 12.00015, 100);
assert.equal(p.road.name, "Test Road");
assert.ok(Math.abs(p.bearing - 90) < 1, "faces along the road");
assert.equal(index.hit(p.lon, p.lat), null, "the street point is outside the walls");
assert.equal(nearestRoadPoint([road], 77.01, 12.01, 100), null, "too far is nothing");

// storeys only up to the building's height
assert.equal(floorLines([building], 77.0002, 12.0002).length, 3, "12 m: lines at 3, 6, 9");
assert.equal(floorLines([{ ...building, height: 3 }], 77.0002, 12.0002).length, 0);
assert.equal(floorLines([building], 77.05, 12.05).length, 0, "far away: nothing");

// street names, once each, nearest first
const labels = streetLabels([road, { ...road }], 77.0002, 12.0002);
assert.deepEqual(labels.map((l) => l.name), ["Test Road"]);
assert.equal(streetLabels([{ ...road, name: "" }], 77.0002, 12.0002).length, 0);

// the walker moves along its bearing, slides along walls, and stops at them
let state = { longitude: 77.0002, latitude: 12.00005, bearing: 0, pitch: 0 };
const walker = new Walker({ getState: () => state, setState: (v) => { state = v; },
                            index: () => index });
walker.keys.add("w");
for (let i = 0; i < 40; i++) walker._step(0.1);        // 4 s north, into the wall
assert.ok(state.latitude < 12.0001, "stopped at the wall");
assert.ok(state.latitude > 12.00005, "but walked up to it");
walker.keys.clear();
walker.keys.add("e");
walker._step(1.0);
assert.ok(Math.abs(state.bearing - 80) < 1e-6, "turns at 80 degrees a second");
walker.keys.clear();
const before = state.longitude;
state = { ...state, bearing: 45 };                     // diagonal into the wall: slide
walker.keys.add("w");
walker._step(0.5);
assert.ok(state.longitude > before, "slides along the wall instead of sticking");

console.log("walk.test.mjs: all passed");
