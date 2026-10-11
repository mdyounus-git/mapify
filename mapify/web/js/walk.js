/* Walking, at eye level, through the 3D model of a place.
 *
 * The 3D ground view shows a place from above; this puts the analyst in
 * the street, 1.7 m up, to walk it: W/A/S/D or the arrow keys to move, Q
 * and E to turn, Shift to run, drag to look around, Esc to stop walking.
 * Buildings are solid -- you slide along a wall rather than through it --
 * because a model you can walk through is a diagram, not a place.
 *
 * Everything drawn is the same data the overview shows: OpenStreetMap
 * buildings, streets, water and parks, OpenMapTiles heights, Google Open
 * Buildings outlines. Nothing is invented to make it look fuller: no
 * trees are scattered where none are mapped, no facade is textured with
 * windows it may not have. Floor lines are drawn at three-metre storeys
 * only up to a building's known (or stated-nominal) height, which is what
 * the data supports.
 */

export const EYE_HEIGHT_M = 1.7;
const WALK_MPS = 3.0;          // a brisk walk, a little faster than life
const RUN_MPS = 9.0;
const TURN_DPS = 80;
const BODY_M = 0.35;           // how close to a wall you can stand
const CELL_DEG = 0.0012;       // ~130 m grid for the collision index
const M_PER_DEG = 111320;

const VEHICULAR = new Set(["motorway", "trunk", "primary", "secondary", "tertiary",
  "unclassified", "residential", "living_street", "service", "road",
  "motorway_link", "trunk_link", "primary_link", "secondary_link",
  "tertiary_link", "minor"]);
const WITH_SIDEWALK = new Set(["primary", "secondary", "tertiary", "unclassified",
  "residential", "living_street", "primary_link", "secondary_link",
  "tertiary_link", "minor", "road"]);
const MARKED = new Set(["motorway", "trunk", "primary", "secondary", "tertiary"]);

/* ───────────────────────────── geometry ───────────────────────────── */

function kx(lat) { return M_PER_DEG * Math.cos((lat * Math.PI) / 180); }

/** Ray casting on one ring of [lon, lat] points. */
function inRing(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > lat) !== (yj > lat)
        && lon < ((xj - xi) * (lat - yi)) / (yj - yi + 1e-15) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** Metres from a point to the nearest edge of a ring. */
function ringDistanceM(lon, lat, ring) {
  const ax = kx(lat);
  let best = Infinity;
  for (let i = 0; i + 1 < ring.length; i++) {
    const x1 = (ring[i][0] - lon) * ax, y1 = (ring[i][1] - lat) * M_PER_DEG;
    const x2 = (ring[i + 1][0] - lon) * ax, y2 = (ring[i + 1][1] - lat) * M_PER_DEG;
    const dx = x2 - x1, dy = y2 - y1;
    const len = dx * dx + dy * dy;
    const t = len ? Math.max(0, Math.min(1, -(x1 * dx + y1 * dy) / len)) : 0;
    const d = Math.hypot(x1 + t * dx, y1 + t * dy);
    if (d < best) best = d;
  }
  return best;
}

/** A grid of building outlines, for "is this spot inside a wall?". */
export class BuildingIndex {
  constructor(buildings) {
    this.cells = new Map();
    this.items = [];
    for (const b of buildings) {
      const ring = b.polygon?.[0];
      if (!ring || ring.length < 4) continue;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of ring) {
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
      const item = { id: b.id, ring, holes: b.polygon.slice(1), bbox: [x0, y0, x1, y1] };
      this.items.push(item);
      for (let cx = Math.floor(x0 / CELL_DEG); cx <= Math.floor(x1 / CELL_DEG); cx++) {
        for (let cy = Math.floor(y0 / CELL_DEG); cy <= Math.floor(y1 / CELL_DEG); cy++) {
          const key = `${cx}:${cy}`;
          if (!this.cells.has(key)) this.cells.set(key, []);
          this.cells.get(key).push(item);
        }
      }
    }
  }

  /** The building whose walls a body at this spot would be in, or null. */
  hit(lon, lat, ignoreId = null) {
    const list = this.cells.get(`${Math.floor(lon / CELL_DEG)}:${Math.floor(lat / CELL_DEG)}`);
    if (!list) return null;
    const pad = BODY_M / M_PER_DEG * 1.6;
    for (const it of list) {
      if (it.id === ignoreId) continue;
      const [x0, y0, x1, y1] = it.bbox;
      if (lon < x0 - pad || lon > x1 + pad || lat < y0 - pad || lat > y1 + pad) continue;
      const inside = inRing(lon, lat, it.ring)
        && !it.holes.some((h) => inRing(lon, lat, h));     // a courtyard is open
      if (inside || ringDistanceM(lon, lat, it.ring) < BODY_M) return it;
    }
    return null;
  }
}

/** The nearest point on any road to (lon, lat), within maxM, and its bearing. */
export function nearestRoadPoint(roads, lon, lat, maxM = 200) {
  const ax = kx(lat);
  let best = null;
  for (const r of roads) {
    const p = r.path;
    for (let i = 0; p && i + 1 < p.length; i++) {
      const x1 = (p[i][0] - lon) * ax, y1 = (p[i][1] - lat) * M_PER_DEG;
      const x2 = (p[i + 1][0] - lon) * ax, y2 = (p[i + 1][1] - lat) * M_PER_DEG;
      const dx = x2 - x1, dy = y2 - y1;
      const len = dx * dx + dy * dy;
      const t = len ? Math.max(0, Math.min(1, -(x1 * dx + y1 * dy) / len)) : 0;
      const qx = x1 + t * dx, qy = y1 + t * dy;
      const d = Math.hypot(qx, qy);
      if (d <= maxM && (!best || d < best.d)) {
        best = { d, lon: lon + qx / ax, lat: lat + qy / M_PER_DEG,
                 bearing: (Math.atan2(dx, dy) * 180) / Math.PI, road: r };
      }
    }
  }
  return best;
}

/* ───────────────────────────── the walker ───────────────────────────── */

export class Walker {
  /**
   * @param {object} hooks
   *   getState() / setState(viewState) -- the deck view state
   *   index() -- the current BuildingIndex
   *   onMove(lon, lat) -- called as the walker moves (loading, HUD)
   *   onExit() -- Esc was pressed
   */
  constructor(hooks) {
    this.h = hooks;
    this.keys = new Set();
    this.on = false;
    this.ghostOf = null;          // a building the walk started inside
    this._frame = null;
    this._down = (e) => this._key(e, true);
    this._up = (e) => this._key(e, false);
    this._blur = () => this.keys.clear();
  }

  start() {
    if (this.on) return;
    this.on = true;
    window.addEventListener("keydown", this._down, true);
    window.addEventListener("keyup", this._up, true);
    window.addEventListener("blur", this._blur);
    let last = performance.now();
    const step = (now) => {
      if (!this.on) return;
      const dt = Math.min(0.25, (now - last) / 1000);   // steady speed on a slow frame rate
      last = now;
      this._step(dt);
      this._frame = requestAnimationFrame(step);
    };
    this._frame = requestAnimationFrame(step);
  }

  stop() {
    this.on = false;
    this.keys.clear();
    window.removeEventListener("keydown", this._down, true);
    window.removeEventListener("keyup", this._up, true);
    window.removeEventListener("blur", this._blur);
    if (this._frame) cancelAnimationFrame(this._frame);
    this._frame = null;
  }

  get running() { return this.keys.has("shift"); }

  _key(e, down) {
    if (e.target.closest?.("input, select, textarea")) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase();
    if (down && k === "escape") {
      e.preventDefault();
      e.stopImmediatePropagation();
      this.h.onExit?.();
      return;
    }
    const used = ["w", "a", "s", "d", "q", "e", "arrowup", "arrowdown",
                  "arrowleft", "arrowright", "shift", "r", "f"];
    if (!used.includes(k)) return;
    e.preventDefault();
    e.stopImmediatePropagation();     // the overview's own keys stay quiet
    if (down) this.keys.add(k); else this.keys.delete(k);
  }

  _step(dt) {
    const k = this.keys;
    const fwd = (k.has("w") || k.has("arrowup") ? 1 : 0) - (k.has("s") || k.has("arrowdown") ? 1 : 0);
    const side = (k.has("d") ? 1 : 0) - (k.has("a") ? 1 : 0);
    const turn = (k.has("e") || k.has("arrowright") ? 1 : 0)
      - (k.has("q") || k.has("arrowleft") ? 1 : 0);
    const look = (k.has("r") ? 1 : 0) - (k.has("f") ? 1 : 0);
    if (!fwd && !side && !turn && !look) return;

    const v = { ...this.h.getState() };
    v.bearing = (v.bearing + turn * TURN_DPS * dt + 360) % 360;
    v.pitch = Math.max(-80, Math.min(80, v.pitch + look * 50 * dt));
    if (fwd || side) {
      const speed = (k.has("shift") ? RUN_MPS : WALK_MPS) * dt;
      const b = (v.bearing * Math.PI) / 180;
      const norm = Math.hypot(fwd, side) || 1;
      const dx = ((Math.sin(b) * fwd + Math.cos(b) * side) / norm) * speed;
      const dy = ((Math.cos(b) * fwd - Math.sin(b) * side) / norm) * speed;
      const ax = kx(v.latitude);
      const tryAt = (lon, lat) => {
        const hit = this.h.index()?.hit(lon, lat, this.ghostOf);
        return !hit;
      };
      const nx = v.longitude + dx / ax, ny = v.latitude + dy / M_PER_DEG;
      if (tryAt(nx, ny)) { v.longitude = nx; v.latitude = ny; }
      else if (tryAt(nx, v.latitude)) v.longitude = nx;          // slide along x
      else if (tryAt(v.longitude, ny)) v.latitude = ny;          // slide along y
      // Out of the building the walk began in: from now on it is solid too.
      if (this.ghostOf && !this.h.index()?.hit(v.longitude, v.latitude)) this.ghostOf = null;
    }
    this.h.setState(v);
    this.h.onMove?.(v.longitude, v.latitude);
  }
}

/* ───────────────────────────── the scene ───────────────────────────── */

const ASPHALT = [70, 74, 80];
const FOOTWAY = [180, 170, 150];
const SIDEWALK = [188, 184, 176];
const GROUND = [158, 156, 146];

const LEVEL = () => 0;

/** Floor lines -- one outline per three-metre storey -- for nearby buildings.
 *  ``ground(lon, lat)`` is the ground's height under a building (level 0
 *  when there is no terrain). */
export function floorLines(buildings, lon, lat, radiusM = 650, ground = LEVEL) {
  const ax = kx(lat);
  const out = [];
  for (const b of buildings) {
    const ring = b.polygon?.[0];
    if (!ring || !b.centroid) continue;
    const dx = (b.centroid[0] - lon) * ax, dy = (b.centroid[1] - lat) * M_PER_DEG;
    if (dx * dx + dy * dy > radiusM * radiusM) continue;
    const top = b.height || 0;
    const base = b.base || 0;
    const g = top - 0.8 > base + 3 ? ground(b.centroid[0], b.centroid[1]) : 0;
    for (let z = base + 3; z < top - 0.8; z += 3) {
      out.push({ path: ring.map((p) => [p[0], p[1], g + z]) });
    }
  }
  return out;
}

/** Where to put each named street's label: its longest segment's middle. */
export function streetLabels(roads, lon, lat, radiusM = 450) {
  const ax = kx(lat);
  const seen = new Set();
  const out = [];
  for (const r of roads) {
    if (!r.name || !r.path || r.path.length < 2) continue;
    let best = null;
    for (let i = 0; i + 1 < r.path.length; i++) {
      const [x1, y1] = r.path[i], [x2, y2] = r.path[i + 1];
      const len = Math.hypot((x2 - x1) * ax, (y2 - y1) * M_PER_DEG);
      if (!best || len > best.len) best = { len, x: (x1 + x2) / 2, y: (y1 + y2) / 2 };
    }
    const d = Math.hypot((best.x - lon) * ax, (best.y - lat) * M_PER_DEG);
    const key = `${r.name}:${Math.round(best.x * 2000)}:${Math.round(best.y * 2000)}`;
    if (d > radiusM || best.len < 12 || seen.has(key)) continue;
    seen.add(key);
    out.push({ name: r.name, position: [best.x, best.y, 3.2], dist: d });
  }
  return out.sort((a, b) => a.dist - b.dist).slice(0, 40);
}

/** The layers that make the street-level scene, beneath the buildings.
 *  ``ground(lon, lat)``: the ground's height at a point, with terrain on; the
 *  level plane is then left out, since the terrain itself is the ground, and
 *  everything sits ``lift`` above it so the terrain's mesh (an approximation
 *  of the same heights) does not swallow it. Heights are read here, before
 *  the layers are made, so a tile still on its way is noticed at once. */
export function groundLayers(deck, data, origin, ground = null, lift = 0) {
  const [lon, lat] = origin;
  const span = 0.03;                      // ~3 km of ground in every direction
  const roads = [...data.roads.values()];
  const green = [...data.green.values()];
  const water = [...data.water.values()];
  const at = ground || LEVEL;
  const heights = new Map();
  const of = (pts) => {
    let h = heights.get(pts);
    if (!h) { h = pts.map((p) => at(p[0], p[1]) + lift); heights.set(pts, h); }
    return h;
  };
  for (const r of roads) if (r.path) of(r.path);
  for (const f of [...green, ...water]) for (const ring of f.polygon) of(ring);
  const on = (pts, off) => { const h = of(pts); return pts.map((p, i) => [p[0], p[1], h[i] + off]); };
  const layers = ground ? [] : [
    new deck.SolidPolygonLayer({
      id: "walk-ground",
      data: [[[lon - span, lat - span], [lon + span, lat - span],
              [lon + span, lat + span], [lon - span, lat + span]]],
      getPolygon: (p) => p, getFillColor: GROUND,
    }),
  ];
  layers.push(
    new deck.SolidPolygonLayer({
      id: "walk-green", data: green,
      getPolygon: (f) => f.polygon.map((r) => on(r, 0.02)),
      getFillColor: [96, 138, 82],
    }),
    new deck.SolidPolygonLayer({
      id: "walk-water", data: water,
      getPolygon: (f) => f.polygon.map((r) => on(r, 0.03)),
      getFillColor: [64, 118, 168],
    }),
    new deck.PathLayer({
      id: "walk-sidewalks", data: roads.filter((r) => WITH_SIDEWALK.has(r.class)),
      getPath: (r) => on(r.path, 0.04),
      widthUnits: "meters", getWidth: (r) => r.width + 3.4,
      getColor: SIDEWALK, capRounded: true, jointRounded: true,
    }),
    new deck.PathLayer({
      id: "walk-roads", data: roads,
      getPath: (r) => on(r.path, 0.06),
      widthUnits: "meters", getWidth: (r) => r.width,
      getColor: (r) => (VEHICULAR.has(r.class) ? ASPHALT : FOOTWAY),
      capRounded: true, jointRounded: true, pickable: true,
    }),
  );
  if (deck.PathStyleExtension) {
    layers.push(new deck.PathLayer({
      id: "walk-markings", data: roads.filter((r) => MARKED.has(r.class)),
      getPath: (r) => on(r.path, 0.08),
      widthUnits: "meters", getWidth: 0.15, getColor: [232, 232, 222],
      getDashArray: [20, 40], dashJustified: true,
      extensions: [new deck.PathStyleExtension({ dash: true })],
    }));
  }
  return layers;
}
