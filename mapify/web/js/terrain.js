/* The ground's height, for the 3D view.
 *
 * From Terrain Tiles (Mapzen/Tilezen "terrarium" PNGs on the Registry of
 * Open Data on AWS), fetched through the application's own tile cache at
 * /api/tiles/terrain: height = R*256 + G + B/256 - 32768 metres, merged
 * from SRTM, USGS 3DEP, GMTED2010, ETOPO1 and national elevation models.
 * Real data, credited in the view's attribution and in ATTRIBUTION.md.
 *
 * deck.gl's TerrainLayer draws the ground from these tiles; this module
 * also reads the height at the walker's feet, so the eye stays 1.7 m above
 * the ground on a hillside as on a quay.
 */

export const TERRAIN_URL = "/api/tiles/terrain/{z}/{x}/{y}";
export const TERRARIUM = { rScaler: 256, gScaler: 1, bScaler: 1 / 256, offset: -32768 };
export const SAMPLE_Z = 14;            // ~10 m a pixel at mid-latitudes
// Under the walker: ~2.4 m a pixel, which is bare-earth lidar where Mapterhorn
// has it (and the coarser ground, enlarged as heights, where it has not).
export const WALK_Z = 16;
export const TERRAIN_CREDIT =
  "Terrain: Mapterhorn (national models; Copernicus WorldDEM-30 \u00a9 DLR e.V., "
  + "\u00a9 Airbus DS, provided under COPERNICUS by the EU and ESA) and Terrain Tiles "
  + "on AWS; see ATTRIBUTION.md";

/** The z/x/y tile holding a point, and the point's pixel inside it. */
export function tileOf(lon, lat, z) {
  const n = 2 ** z;
  const fx = ((lon + 180) / 360) * n;
  const r = (lat * Math.PI) / 180;
  const fy = ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n;
  const x = Math.floor(fx), y = Math.floor(fy);
  return { x, y, px: (fx - x) * 256, py: (fy - y) * 256 };
}

/** [west, south, east, north] of a tile. */
export function tileBounds(x, y, z) {
  const n = 2 ** z;
  const lon = (i) => (i / n) * 360 - 180;
  const lat = (j) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * j) / n))) * 180) / Math.PI;
  return [lon(x), lat(y + 1), lon(x + 1), lat(y)];
}

/** Heights in metres from a terrarium tile's RGBA pixels. */
export function heightsFromRGBA(rgba, size = 256) {
  const out = new Float32Array(size * size);
  for (let i = 0; i < size * size; i += 1) {
    out[i] = rgba[i * 4] * 256 + rgba[i * 4 + 1] + rgba[i * 4 + 2] / 256 - 32768;
  }
  return out;
}

/** Bilinear height at pixel (px, py) of a decoded tile. */
export function sampleHeights(h, px, py, size = 256) {
  const x0 = Math.max(0, Math.min(size - 1, Math.floor(px - 0.5)));
  const y0 = Math.max(0, Math.min(size - 1, Math.floor(py - 0.5)));
  const x1 = Math.min(size - 1, x0 + 1), y1 = Math.min(size - 1, y0 + 1);
  const tx = Math.max(0, Math.min(1, px - 0.5 - x0)), ty = Math.max(0, Math.min(1, py - 0.5 - y0));
  const a = h[y0 * size + x0], b = h[y0 * size + x1];
  const c = h[y1 * size + x0], d = h[y1 * size + x1];
  return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
}

/** Reads the ground's height at a point, from tiles it fetches once. */
export class TerrainSampler {
  constructor(decode = null, z = SAMPLE_Z) {
    this.tiles = new Map();          // "z/x/y" -> Float32Array | Promise | null
    this._decode = decode || decodePng;
    this.z = z;
  }

  async load(lon, lat) {
    const t = tileOf(lon, lat, this.z);
    const key = `${this.z}/${t.x}/${t.y}`;
    if (!this.tiles.has(key)) {
      const url = TERRAIN_URL.replace("{z}", this.z).replace("{x}", t.x).replace("{y}", t.y);
      const job = this._decode(url).then((h) => { this.tiles.set(key, h); return h; },
        () => { this.tiles.set(key, null); return null; });
      this.tiles.set(key, job);
    }
    const got = this.tiles.get(key);
    return this.tiles.get(key);
  }

  /** The height if its tile is already here, else null -- never fetching. */
  peek(lon, lat) {
    const t = tileOf(lon, lat, this.z);
    const h = this.tiles.get(`${this.z}/${t.x}/${t.y}`);
    return h && !(h instanceof Promise) ? sampleHeights(h, t.px, t.py) : null;
  }

  /** The tiles within ``reach`` tiles of a point, fetched once; resolves when
   *  the ones not already here have come in (true if there were any). */
  around(lon, lat, reach) {
    const t = tileOf(lon, lat, this.z);
    const jobs = [];
    for (let dy = -reach; dy <= reach; dy += 1) {
      for (let dx = -reach; dx <= reach; dx += 1) {
        const key = `${this.z}/${t.x + dx}/${t.y + dy}`;
        if (this.tiles.has(key)) continue;        // here, failed, or on its way
        const [w, s, e, n] = tileBounds(t.x + dx, t.y + dy, this.z);
        jobs.push(this.load((w + e) / 2, (s + n) / 2));
      }
    }
    return Promise.all(jobs).then(() => jobs.length > 0);
  }

  /** The height now, or null while its tile is still loading (or missing). */
  heightAt(lon, lat) {
    const t = tileOf(lon, lat, this.z);
    const h = this.tiles.get(`${this.z}/${t.x}/${t.y}`);
    if (!h || h instanceof Promise) {
      if (h === undefined) this.load(lon, lat);
      return null;
    }
    return sampleHeights(h, t.px, t.py);
  }
}

async function decodeRGBA(blob) {
  const bmp = await createImageBitmap(blob, { colorSpaceConversion: "none",
                                             premultiplyAlpha: "none" });
  const canvas = new OffscreenCanvas(bmp.width, bmp.height);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bmp, 0, 0);
  return { rgba: ctx.getImageData(0, 0, bmp.width, bmp.height).data, size: bmp.width };
}

async function decodePng(url) {
  const resp = await fetch(url);
  if (resp.status !== 200) throw new Error(`no terrain tile (${resp.status})`);
  const { rgba, size } = await decodeRGBA(await resp.blob());
  return heightsFromRGBA(rgba, size);
}

/**
 * A terrain tile as a mesh, in the shape deck.gl's TerrainLayer draws:
 * a regular grid every ``step`` pixels across ``bounds`` (west, south,
 * east, north; north at the top of the image), heights in metres, texture
 * coordinates 0..1, and -- with ``skirt`` -- a short wall hanging down
 * each edge so neighbouring tiles meet without cracks.
 */
export function gridMesh(heights, size, bounds, step = 4, skirt = 0) {
  const [west, south, east, north] = bounds;
  const n = Math.floor(size / step) + 1;
  const pos = [], uv = [], idx = [];
  const at = (i, j) => heights[Math.min(size - 1, j * step) * size + Math.min(size - 1, i * step)];
  for (let j = 0; j < n; j += 1) {
    for (let i = 0; i < n; i += 1) {
      const u = (i * step) / size, v = (j * step) / size;
      pos.push(west + u * (east - west), north - v * (north - south), at(i, j));
      uv.push(u, v);
    }
  }
  for (let j = 0; j < n - 1; j += 1) {
    for (let i = 0; i < n - 1; i += 1) {
      const a = j * n + i, b = a + 1, c = a + n, d = c + 1;
      idx.push(a, c, b, b, c, d);
    }
  }
  if (skirt > 0) {
    const edges = [];
    for (let i = 0; i < n - 1; i += 1) {
      edges.push([i, i + 1], [(n - 1) * n + i + 1, (n - 1) * n + i],
                 [(i + 1) * n, i * n], [i * n + n - 1, (i + 1) * n + n - 1]);
    }
    for (const [a, b] of edges) {
      const base = pos.length / 3;
      for (const v of [a, b]) {
        pos.push(pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2] - skirt);
        uv.push(uv[v * 2], uv[v * 2 + 1]);
      }
      idx.push(a, base, b, b, base, base + 1);
    }
  }
  const P = new Float32Array(pos);
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let k = 0; k < P.length; k += 3) {
    for (let c = 0; c < 3; c += 1) {
      if (P[k + c] < min[c]) min[c] = P[k + c];
      if (P[k + c] > max[c]) max[c] = P[k + c];
    }
  }
  return { loaderData: { header: {} },
           header: { vertexCount: idx.length, boundingBox: [min, max] },
           mode: 4, indices: { value: Uint32Array.from(idx), size: 1 },
           attributes: { POSITION: { value: P, size: 3 },
                         TEXCOORD_0: { value: new Float32Array(uv), size: 2 } } };
}

/**
 * A terrain loader that decodes on the page's own thread. deck.gl's bundled
 * one only works in a web worker fetched from a CDN, which this page's
 * content policy refuses (and which would fail offline anyway).
 */
export const TerrainMeshLoader = {
  name: "Terrain (main thread)", id: "terrain", module: "terrain", version: "1.0.0",
  worker: false, binary: true, extensions: ["png"], mimeTypes: ["image/png"],
  options: { terrain: { bounds: null, elevationDecoder: TERRARIUM, skirtHeight: 0, meshStep: 4 } },
  async parse(data, options) {
    const t = options?.terrain || {};
    const d = t.elevationDecoder || TERRARIUM;
    const { rgba, size } = await decodeRGBA(new Blob([data], { type: "image/png" }));
    const h = new Float32Array(size * size);
    for (let k = 0; k < size * size; k += 1) {
      h[k] = rgba[k * 4] * d.rScaler + rgba[k * 4 + 1] * d.gScaler
        + rgba[k * 4 + 2] * d.bScaler + d.offset;
    }
    return gridMesh(h, size, t.bounds || [0, 0, 1, 1], t.meshStep || 4, t.skirtHeight || 0);
  },
};

/** Layers that stand on the ground keep their shape and are lifted onto it;
 *  flat ones (ground, roads, water, parks) are laid over it. */
const OFFSET_LAYERS = new Set(["buildings", "walk-floors", "walk-streets", "walk-pois", "pois",
  "target", "target-beam", "ambience-cars", "ambience-people", "matches-pole",
  "matches-label", "zoom-pulse"]);

/** The same layers, lifted onto the terrain by deck.gl's TerrainExtension. */
export function onTerrain(layers, extension) {
  if (!extension) return layers;
  return layers.map((l) => (l.props.operation?.includes?.("terrain") ? l : l.clone({
    extensions: [...(l.props.extensions || []), extension],
    terrainDrawMode: OFFSET_LAYERS.has(l.id) ? "offset" : "drape",
  })));
}
