// The ground's height: terrarium decoding, tile maths, sampling, and the
// sampler's loading. Run by tests/test_terrain_js.py (needs Node).

import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const here = resolve(process.argv[2] || ".");
const t = await import(pathToFileURL(resolve(here, "mapify/web/js/terrain.js")).href);
const { tileOf, tileBounds, heightsFromRGBA, sampleHeights, TerrainSampler, onTerrain, SAMPLE_Z, WALK_Z,
        gridMesh, TerrainMeshLoader } = t;

// Terrarium: R*256 + G + B/256 - 32768.
const rgba = new Uint8ClampedArray(4 * 4);
[[128, 0, 0], [128, 36, 160], [127, 255, 0], [130, 0, 128]].forEach((c, i) => rgba.set([...c, 255], i * 4));
const h = heightsFromRGBA(rgba, 2);
assert.equal(h[0], 0);
assert.equal(h[1], 36.625);              // Paris, about
assert.equal(h[2], -1);                  // below the sea
assert.equal(h[3], 512.5);

// The tile holding Paris at z12, as the server's tile cache fetched it.
const p = tileOf(2.2945, 48.8584, 12);
assert.equal(p.x, 2074);
assert.equal(p.y, 1409);
const [w, s, e, n] = tileBounds(p.x, p.y, 12);
assert.ok(w <= 2.2945 && 2.2945 <= e && s <= 48.8584 && 48.8584 <= n, "the point is inside its tile");

// Bilinear: halfway between two pixel centres is halfway between heights.
const flat = new Float32Array([0, 10, 0, 10]);
assert.equal(sampleHeights(flat, 1.0, 0.5, 2), 5);
assert.equal(sampleHeights(flat, 0.5, 0.5, 2), 0);

// The sampler: nothing until its tile has loaded, then the height.
const decoded = new Float32Array(256 * 256).fill(123);
let asked = 0;
const sampler = new TerrainSampler(async (url) => { asked += 1; assert.ok(url.includes(`/${SAMPLE_Z}/`)); return decoded; });
assert.equal(sampler.heightAt(2.2945, 48.8584), null, "loading: no height yet");
await sampler.load(2.2945, 48.8584);
assert.equal(sampler.heightAt(2.2945, 48.8584), 123);
await sampler.load(2.2946, 48.8584);
assert.equal(asked, 1, "a tile is fetched once");
const missing = new TerrainSampler(async () => { throw new Error("404"); });
await missing.load(0, 0);
assert.equal(missing.heightAt(0, 0), null, "no tile: no height, and no endless retrying");
const walking = new TerrainSampler(async (url) => { assert.ok(url.includes(`/${WALK_Z}/`)); return decoded; }, WALK_Z);
await walking.load(2.2945, 48.8584);
assert.equal(walking.heightAt(2.2945, 48.8584), 123, "the walk reads its own, finer zoom");
assert.ok(WALK_Z > SAMPLE_Z);

// peek reads only what is here: a coastline reaching far out to sea must
// not send for a tile per corner (it exhausted the browser's connections).
let fetched = 0;
const quiet = new TerrainSampler(async () => { fetched += 1; return decoded; }, WALK_Z);
assert.equal(quiet.peek(80.2341, 13.0418), null);
assert.equal(fetched, 0, "peek never fetches");
// around fetches the block about a point once, and says whether it did.
assert.equal(await quiet.around(80.2341, 13.0418, 2), true);
assert.equal(fetched, 25, "a 5 x 5 block");
assert.equal(quiet.peek(80.2341, 13.0418), 123);
assert.equal(await quiet.around(80.2342, 13.0418, 2), false, "nothing new: no redraw asked for");
assert.equal(fetched, 25);
const gone = new TerrainSampler(async () => { throw new Error("404"); }, WALK_Z);
assert.equal(await gone.around(0, 0, 1), true);
assert.equal(await gone.around(0, 0, 1), false, "a failed tile is not asked for again and again");

// Lifting layers onto the terrain: standing things offset, flat things draped.
class L { constructor(id, props = {}) { this.id = id; this.props = props; }
          clone(p) { return new L(this.id, { ...this.props, ...p }); } }
const ext = { name: "terrain" };
const out = onTerrain([new L("terrain", { operation: "terrain+draw" }), new L("buildings"),
                       new L("walk-roads")], ext);
assert.equal(out[0].props.extensions, undefined, "the terrain itself is left alone");
assert.equal(out[1].props.terrainDrawMode, "offset");
assert.equal(out[2].props.terrainDrawMode, "drape");
assert.deepEqual(out[1].props.extensions, [ext]);
assert.equal(onTerrain(out, null), out, "no extension: unchanged");

// The mesh deck.gl's TerrainLayer draws: north-west corner first, heights
// as z, texture coordinates 0..1, a bounding box, and skirts hanging down.
const ramp = new Float32Array(8 * 8).map((_, k) => k % 8);       // rises eastward
const mesh = gridMesh(ramp, 8, [10, 20, 11, 21], 4, 0);
const P = mesh.attributes.POSITION.value, UV = mesh.attributes.TEXCOORD_0.value;
assert.equal(P.length / 3, 9, "a 3 x 3 grid for 8 px every 4 px");
assert.deepEqual([P[0], P[1]], [10, 21], "the first vertex is the north-west corner");
assert.deepEqual([UV[0], UV[1]], [0, 0]);
assert.equal(P[2 * 3 + 2], 7, "the east edge takes the last pixel's height");
assert.equal(mesh.indices.value.length, 2 * 2 * 6, "two triangles per cell");
assert.equal(mesh.header.vertexCount, mesh.indices.value.length);
assert.deepEqual(mesh.header.boundingBox[0].slice(0, 2), [10, 20]);
assert.equal(mesh.mode, 4);
const skirted = gridMesh(ramp, 8, [10, 20, 11, 21], 4, 50);
const SP = skirted.attributes.POSITION.value;
assert.ok(SP.length / 3 > 9, "skirts add vertices");
assert.equal(Math.min(...Array.from(SP).filter((_, k) => k % 3 === 2)), -50, "hanging below the edge");
assert.equal(TerrainMeshLoader.worker, false, "decodes on the page's own thread");
assert.equal(typeof TerrainMeshLoader.parse, "function");

console.log("terrain: all passed");
