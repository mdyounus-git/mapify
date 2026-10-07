// The life of a scene: the walk's weather follows the reading and nothing
// else, and the illustrative ambience stays on the mapped streets and
// keeps moving. Run by tests/test_scene_js.py (needs Node).

import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const here = resolve(process.argv[2] || ".");
const scene = await import(pathToFileURL(resolve(here, "mapify/web/js/scene.js")).href);
const { readWeather, skyFor, lightFor, describeWeather, Ambience, AMBIENCE_NOTE } = scene;

// ---- weather: only what the reading says
assert.equal(readWeather(null), null);
assert.equal(readWeather({ available: false }), null);

const clear = readWeather({ available: true, code: 0, cloud_cover: 0, precipitation_mm: 0,
                            snowfall_cm: 0, is_day: 1, pm2_5: 5, aerosol_optical_depth: 0.05 });
assert.equal(clear.raining, false);
assert.equal(clear.snowing, false);
assert.equal(clear.fog, false);
assert.ok(clear.haze < 0.1, "clean air is barely hazy");

const rain = readWeather({ available: true, code: 63, cloud_cover: 98, precipitation_mm: 0.4,
                           snowfall_cm: 0, is_day: 1, wind_kmh: 20, wind_from_deg: 270 });
assert.equal(rain.raining, true);
assert.equal(rain.snowing, false);
assert.ok(rain.rainRate >= 0.4);

const snow = readWeather({ available: true, code: 73, cloud_cover: 100, precipitation_mm: 0.8,
                           snowfall_cm: 0.6, is_day: 1 });
assert.equal(snow.snowing, true);
assert.equal(snow.raining, false, "snow is not drawn as rain");

const fog = readWeather({ available: true, code: 45, cloud_cover: 100, is_day: 1 });
assert.equal(fog.fog, true);
assert.ok(fog.haze > 0.4);

const night = readWeather({ available: true, code: 0, cloud_cover: 0, is_day: 0 });
assert.equal(night.night, true);

// The sky darkens with cloud, and the sun dims.
assert.notEqual(skyFor(clear), skyFor(rain));
assert.ok(skyFor(night).includes("rgb(8,14,28)"));
assert.ok(lightFor(rain).sun < lightFor(clear).sun);
assert.equal(lightFor(null).sun, 1, "no reading: the light is left as it was");

const line = describeWeather({ available: true, source: "Open-Meteo, now", description: "rain",
                               cloud_cover: 98, precipitation_mm: 0.4, temperature_c: 16.5,
                               wind_kmh: 2.2, wind_from_deg: 261, pm2_5: 11.1 });
assert.ok(line.startsWith("Weather (Open-Meteo, now): rain"));
assert.ok(line.includes("98% cloud") && line.includes("from W"));
assert.equal(describeWeather({ available: false }), "");

// ---- ambience: illustrative, on the mapped streets, moving
assert.ok(AMBIENCE_NOTE.includes("illustrative"));
const street = { class: "primary", width: 10,
                 path: [[77.0, 12.0], [77.0, 12.003], [77.002, 12.003]] };   // ~550 m
const footway = { class: "footway", path: [[77.001, 12.0], [77.001, 12.002]] };
const far = { class: "primary", path: [[78.0, 13.0], [78.0, 13.01]] };
const amb = new Ambience();
amb.build([street, footway, far], 77.0, 12.001, 420);
assert.ok(amb.cars.length > 0, "vehicles on the street");
assert.ok(amb.people.length > 0, "people on the footway and pavements");
assert.ok(amb.cars.every((c) => c.path === street.path), "no vehicles on a footway or far away");
const before = amb.cars.map((c) => c.s);
amb.step(1.0);
assert.ok(amb.cars.some((c, i) => c.s !== before[i]), "they move");
for (const c of amb.cars) assert.ok(c.s >= 0 && c.s <= c.len, "and stay on their street");
for (const p of amb.people) assert.ok(p.s >= 0 && p.s <= p.len);

// Layers build without a browser, given a stand-in for deck.gl.
const fakeDeck = { SolidPolygonLayer: class { constructor(o) { this.o = o; } },
                   ColumnLayer: class { constructor(o) { this.o = o; } } };
const [cars, people] = amb.layers(fakeDeck);
assert.equal(cars.o.data.length, amb.cars.length);
assert.equal(cars.o.data[0].polygon.length, 4, "a vehicle is a box");
assert.equal(people.o.data.length, amb.people.length);
// A box stays within a few metres of its street.
const [lon, lat] = cars.o.data[0].polygon[0];
assert.ok(Math.abs(lon - 77.001) < 0.0015 && Math.abs(lat - 12.0015) < 0.0025);

console.log("scene: all passed");
