/* The life of a scene in the 3D walk: its real weather, and -- only when
 * the analyst switches it on -- illustrative ambience.
 *
 * Weather is data. The sky's colour, the light, rain, snow, fog, haze and
 * wind come from Open-Meteo for this place: at the hour the photograph was
 * taken when that is known (the ERA5 reanalysis, a ~25 km grid-cell
 * average for the hour), otherwise now. Nothing is drawn that the reading
 * does not say.
 *
 * Ambience is not data. No free source knows who or what is in a street,
 * so the people, vehicles and birds it adds are invented: moving along the
 * mapped streets and footways, but not observed there. It is off unless
 * switched on, and while it is on the walk says so. This is the one place
 * in the walk where anything is made up, and it is kept apart for that
 * reason -- see walk.js, which draws only what the map holds.
 */

export const AMBIENCE_NOTE =
  "Ambience on: the people, vehicles and birds are illustrative, not observed at this place.";

const M_PER_DEG = 111320;
const VEHICULAR = new Set(["motorway", "trunk", "primary", "secondary", "tertiary",
  "unclassified", "residential", "living_street", "road", "minor",
  "primary_link", "secondary_link", "tertiary_link"]);
const ON_FOOT = new Set(["footway", "pedestrian", "path", "steps", "living_street",
  "residential", "service"]);

/* ───────────────────────────── weather ───────────────────────────── */

const lerp = (a, b, t) => a + (b - a) * t;
const mix = (c1, c2, t) => c1.map((v, i) => Math.round(lerp(v, c2[i], t)));
const rgb = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;

/** What the reading says, in the terms the drawing needs. */
export function readWeather(w) {
  if (!w || !w.available) return null;
  const code = Number(w.code ?? 0);
  const cloud = Math.max(0, Math.min(100, Number(w.cloud_cover ?? 0))) / 100;
  const snowCm = Math.max(0, Number(w.snowfall_cm ?? 0));
  const precip = Math.max(0, Number(w.precipitation_mm ?? 0));
  const fog = code === 45 || code === 48;
  const thunder = code >= 95;
  const snowing = snowCm > 0.02 || (code >= 71 && code <= 77) || code === 85 || code === 86;
  const raining = !snowing && (precip > 0.05 || (code >= 51 && code <= 67)
    || (code >= 80 && code <= 82) || thunder);
  // Haze from fine particles and the aerosol optical depth; fog is thicker.
  const pm = Number(w.pm2_5 ?? 0), aod = Number(w.aerosol_optical_depth ?? 0);
  const haze = fog ? 0.55 : Math.max(0, Math.min(0.4, pm / 160 + aod * 0.5));
  return {
    code, cloud, fog, thunder, snowing, raining, haze,
    night: w.is_day === 0,
    rainRate: raining ? Math.max(precip, 0.3) : 0,           // mm/h
    snowRate: snowing ? Math.max(snowCm, 0.1) : 0,           // cm/h
    windKmh: Math.max(0, Number(w.wind_kmh ?? 0)),
    windFrom: Number(w.wind_from_deg ?? 0),
  };
}

/** The sky behind the street, as a CSS background. */
export function skyFor(r) {
  let top = [79, 134, 198], mid = [134, 180, 227], low = [201, 222, 242], rim = [230, 238, 246];
  if (r) {
    const grey = r.raining || r.thunder ? 0.85 : 0.75;
    const t = r.cloud * grey;
    top = mix(top, [118, 126, 138], t); mid = mix(mid, [156, 163, 172], t);
    low = mix(low, [194, 199, 204], t); rim = mix(rim, [212, 216, 220], t);
    if (r.fog) { top = mid = low = rim = [204, 208, 211]; }
    if (r.night) {
      top = [8, 14, 28]; mid = [16, 26, 46]; low = [30, 42, 64]; rim = [44, 56, 78];
    }
  }
  return `linear-gradient(180deg, ${rgb(top)} 0%, ${rgb(mid)} 38%, ${rgb(low)} 56%, `
    + `${rgb(rim)} 64%, #9a9a90 64.2%, #8e8d84 100%)`;
}

/** How much the cloud dims the sun and lifts the diffuse light. */
export function lightFor(r) {
  if (!r) return { sun: 1, ambient: 1 };
  const sun = Math.max(0.2, 1 - 0.75 * r.cloud - (r.raining || r.snowing ? 0.15 : 0)
    - (r.fog ? 0.3 : 0));
  return { sun, ambient: 1 + 0.25 * r.cloud };
}

/** One line for the walk's HUD. */
export function describeWeather(w) {
  if (!w || !w.available) return "";
  const bits = [w.description];
  if (w.cloud_cover != null) bits.push(`${Math.round(w.cloud_cover)}% cloud`);
  if (w.precipitation_mm) bits.push(`${w.precipitation_mm} mm/h`);
  if (w.snowfall_cm) bits.push(`snow ${w.snowfall_cm} cm/h`);
  if (w.temperature_c != null) bits.push(`${Math.round(w.temperature_c)} °C`);
  if (w.wind_kmh != null) {
    const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    bits.push(`wind ${Math.round(w.wind_kmh)} km/h from ${
      dirs[Math.round((((w.wind_from_deg ?? 0) % 360) + 360) % 360 / 45) % 8]}`);
  }
  if (w.pm2_5 != null) bits.push(`PM2.5 ${Math.round(w.pm2_5)}`);
  return `Weather (${w.source}): ${bits.join(" · ")}`;
}

/* ─────────────────────────── the fx canvas ─────────────────────────── */

/**
 * Rain, snow, lightning and haze -- and, with ambience on, birds -- drawn
 * over the street on a transparent canvas. Screen-space: the drops do not
 * know which building they fall in front of, which at walking pace reads
 * as weather rather than as geometry.
 */
export class SceneFx {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.r = null;
    this.bearing = 0;
    this.birdsOn = false;
    this.drops = [];
    this.birds = [];
    this.flash = 0;
    this._next = 0;
    this._last = 0;
    this._raf = null;
    this._seed = 1;
  }

  _rand() {                       // deterministic, so a scene looks the same twice
    this._seed = (this._seed * 16807) % 2147483647;
    return (this._seed - 1) / 2147483646;
  }

  set(reading) {
    this.r = reading;
    this.drops = [];
    this._sizeCanvas();
  }

  setBirds(on) { this.birdsOn = on; if (!on) this.birds = []; }

  get active() {
    const r = this.r;
    return Boolean(this.birdsOn || (r && (r.raining || r.snowing || r.haze > 0.02 || r.thunder)));
  }

  start() {
    if (this._raf) return;
    this._last = performance.now();
    const tick = (now) => {
      this._raf = requestAnimationFrame(tick);
      const dt = Math.min(0.1, (now - this._last) / 1000);
      this._last = now;
      this._draw(dt, now);
    };
    this._raf = requestAnimationFrame(tick);
  }

  stop() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  _sizeCanvas() {
    const c = this.canvas;
    const w = c.clientWidth || 800, h = c.clientHeight || 600;
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  }

  _draw(dt, now) {
    this._sizeCanvas();
    const { ctx, canvas } = this;
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    const r = this.r;
    if (r && r.haze > 0.02) {
      // Thickest at the horizon, thinning upward and toward the feet.
      const g = ctx.createLinearGradient(0, 0, 0, H);
      const c = r.fog ? "214,218,221" : "205,208,210";
      g.addColorStop(0, `rgba(${c},${r.haze * 0.35})`);
      g.addColorStop(0.5, `rgba(${c},${r.haze})`);
      g.addColorStop(1, `rgba(${c},${r.haze * 0.25})`);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
    }
    // Wind across the view: from the side it comes, drops slant away.
    const across = r ? Math.sin(((r.windFrom - this.bearing + 180) * Math.PI) / 180)
      * Math.min(1, r.windKmh / 40) : 0;
    if (r && r.raining) this._rain(dt, W, H, across);
    if (r && r.snowing) this._snow(dt, W, H, across, now);
    if (r && r.thunder) {
      if (now > this._next) {
        this.flash = 0.55;
        this._next = now + 6000 + this._rand() * 12000;
      }
      if (this.flash > 0.01) {
        ctx.fillStyle = `rgba(240,244,255,${this.flash})`;
        ctx.fillRect(0, 0, W, H);
        this.flash *= Math.pow(0.02, dt);
      }
    }
    if (this.birdsOn && !(r && (r.night || r.rainRate > 4 || r.fog))) this._birds(dt, W, H, now);
  }

  _rain(dt, W, H, across) {
    const want = Math.min(1400, Math.round(160 + this.r.rainRate * 260));
    while (this.drops.length < want) {
      this.drops.push({ x: this._rand() * W, y: this._rand() * H, v: 900 + this._rand() * 500,
                        l: 10 + this._rand() * 12 });
    }
    this.drops.length = want;
    const { ctx } = this;
    ctx.strokeStyle = "rgba(210,220,235,0.55)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    const slant = across * 0.45;
    for (const d of this.drops) {
      d.y += d.v * dt;
      d.x += d.v * dt * slant;
      if (d.y > H) { d.y = -d.l; d.x = this._rand() * W; }
      if (d.x > W) d.x -= W; else if (d.x < 0) d.x += W;
      ctx.moveTo(d.x, d.y);
      ctx.lineTo(d.x - d.l * slant, d.y - d.l);
    }
    ctx.stroke();
  }

  _snow(dt, W, H, across, now) {
    const want = Math.min(900, Math.round(120 + this.r.snowRate * 300));
    while (this.drops.length < want) {
      this.drops.push({ x: this._rand() * W, y: this._rand() * H, v: 40 + this._rand() * 60,
                        s: 1 + this._rand() * 2.4, p: this._rand() * 6.28 });
    }
    this.drops.length = want;
    const { ctx } = this;
    ctx.fillStyle = "rgba(250,252,255,0.9)";
    for (const d of this.drops) {
      d.y += d.v * dt;
      d.x += (across * 50 + Math.sin(now / 900 + d.p) * 18) * dt;
      if (d.y > H) { d.y = -4; d.x = this._rand() * W; }
      if (d.x > W) d.x -= W; else if (d.x < 0) d.x += W;
      ctx.beginPath();
      ctx.arc(d.x, d.y, d.s, 0, 6.283);
      ctx.fill();
    }
  }

  _birds(dt, W, H, now) {
    if (this.birds.length === 0) {
      const n = 5 + Math.floor(this._rand() * 5);
      const y0 = H * (0.12 + this._rand() * 0.18), x0 = -this._rand() * W * 0.4;
      for (let i = 0; i < n; i += 1) {
        this.birds.push({ x: x0 - i * 26 - this._rand() * 18, y: y0 + (i % 2 ? 12 : -8) * (i / 2),
                          v: 38 + this._rand() * 14, p: this._rand() * 6.28,
                          s: 5 + this._rand() * 3 });
      }
    }
    const { ctx } = this;
    ctx.strokeStyle = "rgba(30,34,40,0.85)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    let gone = 0;
    for (const b of this.birds) {
      b.x += b.v * dt;
      b.y += Math.sin(now / 1700 + b.p) * 6 * dt;
      if (b.x > W + 40) { gone += 1; continue; }
      const flap = Math.sin(now / 160 + b.p) * b.s * 0.7;
      ctx.moveTo(b.x - b.s, b.y - flap);
      ctx.lineTo(b.x, b.y);
      ctx.lineTo(b.x + b.s, b.y - flap);
    }
    ctx.stroke();
    if (gone === this.birds.length) this.birds = [];          // the next flock
  }
}

/* ─────────────────────────── ambience in 3D ─────────────────────────── */

function pathLength(path) {
  let total = 0;
  for (let i = 1; i < path.length; i += 1) total += segM(path[i - 1], path[i]);
  return total;
}

function segM(a, b) {
  const k = Math.cos((a[1] * Math.PI) / 180);
  return Math.hypot((b[0] - a[0]) * M_PER_DEG * k, (b[1] - a[1]) * M_PER_DEG);
}

/** Point and heading (degrees east of north) at distance ``s`` along a path. */
function along(path, s) {
  for (let i = 1; i < path.length; i += 1) {
    const d = segM(path[i - 1], path[i]);
    if (s <= d || i === path.length - 1) {
      const t = d > 0 ? Math.min(1, s / d) : 0;
      const a = path[i - 1], b = path[i];
      const k = Math.cos((a[1] * Math.PI) / 180);
      const heading = Math.atan2((b[0] - a[0]) * k, b[1] - a[1]);
      return { lon: lerp(a[0], b[0], t), lat: lerp(a[1], b[1], t), heading };
    }
    s -= d;
  }
  const p = path[path.length - 1];
  return { lon: p[0], lat: p[1], heading: 0 };
}

/** A box ``len`` x ``wid`` metres centred on a point, turned to ``heading``. */
function box(lon, lat, heading, len, wid, sideM = 0) {
  const k = Math.cos((lat * Math.PI) / 180);
  const fx = Math.sin(heading), fy = Math.cos(heading);      // forward (east, north)
  const rx = fy, ry = -fx;                                    // right
  const cx = lon + (rx * sideM) / (M_PER_DEG * k), cy = lat + (ry * sideM) / M_PER_DEG;
  const pts = [[len / 2, wid / 2], [len / 2, -wid / 2], [-len / 2, -wid / 2], [-len / 2, wid / 2]];
  return pts.map(([f, rr]) => [cx + (fx * f + rx * rr) / (M_PER_DEG * k),
                               cy + (fy * f + ry * rr) / M_PER_DEG]);
}

const CAR_SPACING_M = 38;
const PERSON_SPACING_M = 22;
const MAX_CARS = 120;
const MAX_PEOPLE = 220;
const endKey = (q) => `${q[0].toFixed(6)},${q[1].toFixed(6)}`;

const CAR_COLOURS = [[200, 202, 206], [40, 42, 48], [168, 32, 36], [30, 64, 140],
  [236, 236, 232], [120, 124, 130], [196, 160, 40], [52, 96, 64]];
const PEOPLE_COLOURS = [[60, 70, 110], [150, 40, 50], [40, 40, 44], [190, 150, 90],
  [70, 120, 90], [200, 200, 205], [120, 80, 140]];

/**
 * People and vehicles moving along the mapped streets near the walker.
 * Illustrative only (see the head of this file): the streets are real,
 * who is on them is not.
 */
export class Ambience {
  constructor() {
    this.cars = [];
    this.people = [];
    this.frame = 0;
    this._seed = 7;
  }

  _rand() {
    this._seed = (this._seed * 16807) % 2147483647;
    return (this._seed - 1) / 2147483646;
  }

  /**
   * Populate the streets within ``radiusM`` of a point: vehicles about
   * every CAR_SPACING_M of vehicular street and people about every
   * PERSON_SPACING_M of footway or pavement, nearest streets first. Map
   * streets come cut into short segments, so segments that share an end
   * are linked, and at the end of one an agent carries on into another --
   * turning back only at a dead end.
   */
  build(roads, lon, lat, radiusM = 280) {
    this.cars = [];
    this.people = [];
    const segs = [];
    for (const r of roads) {
      if (!r.path || r.path.length < 2) continue;
      const ends = [r.path[0], r.path[Math.floor(r.path.length / 2)], r.path[r.path.length - 1]];
      const dist = Math.min(...ends.map((q) => segM([lon, lat], q)));
      if (dist > radiusM) continue;
      const len = pathLength(r.path);
      if (len < 2) continue;
      segs.push({ path: r.path, len, cls: r.class, width: r.width || 7, dist });
    }
    segs.sort((a, b) => a.dist - b.dist);
    this.graph = new Map();
    for (const seg of segs) {
      for (const atStart of [true, false]) {
        const k = endKey(atStart ? seg.path[0] : seg.path[seg.path.length - 1]);
        if (!this.graph.has(k)) this.graph.set(k, []);
        this.graph.get(k).push({ seg, atStart });
      }
    }
    const spawn = (seg, spacing) => {
      const expected = seg.len / spacing;
      return Math.floor(expected) + (this._rand() < expected % 1 ? 1 : 0);
    };
    for (const seg of segs) {
      if (VEHICULAR.has(seg.cls) && this.cars.length < MAX_CARS) {
        const fast = ["motorway", "trunk", "primary"].includes(seg.cls);
        for (let i = spawn(seg, CAR_SPACING_M); i > 0 && this.cars.length < MAX_CARS; i -= 1) {
          this.cars.push({ seg, s: this._rand() * seg.len, dir: this._rand() < 0.5 ? 1 : -1,
                           v: (fast ? 11 : 7) + this._rand() * 4,
                           colour: CAR_COLOURS[Math.floor(this._rand() * CAR_COLOURS.length)] });
        }
      }
      if ((ON_FOOT.has(seg.cls) || VEHICULAR.has(seg.cls)) && this.people.length < MAX_PEOPLE) {
        const pavement = VEHICULAR.has(seg.cls) && !ON_FOOT.has(seg.cls);
        for (let i = spawn(seg, PERSON_SPACING_M); i > 0 && this.people.length < MAX_PEOPLE; i -= 1) {
          const side = this._rand() < 0.5 ? 1 : -1;
          this.people.push({ seg, s: this._rand() * seg.len, dir: this._rand() < 0.5 ? 1 : -1,
                             v: 1.1 + this._rand() * 0.5, pavement,
                             side: pavement ? side * (seg.width / 2 + 1.4)
                               : (this._rand() - 0.5) * 1.6,
                             colour: PEOPLE_COLOURS[Math.floor(this._rand() * PEOPLE_COLOURS.length)] });
        }
      }
    }
  }

  /** At the end of its segment, an agent turns into a linked one if it can. */
  _carryOn(a, vehicular) {
    const over = a.dir > 0 ? a.s - a.seg.len : -a.s;
    const end = a.dir > 0 ? a.seg.path[a.seg.path.length - 1] : a.seg.path[0];
    const options = (this.graph?.get(endKey(end)) || []).filter((o) => o.seg !== a.seg
      && (vehicular ? VEHICULAR.has(o.seg.cls) : (ON_FOOT.has(o.seg.cls) || VEHICULAR.has(o.seg.cls))));
    if (options.length === 0) {                      // a dead end: turn back
      a.dir = -a.dir;
      a.s = Math.max(0, Math.min(a.seg.len, a.dir > 0 ? over : a.seg.len - over));
      return;
    }
    const o = options[Math.floor(this._rand() * options.length)];
    a.seg = o.seg;
    a.dir = o.atStart ? 1 : -1;
    a.s = Math.min(a.seg.len, o.atStart ? over : a.seg.len - over);
  }

  step(dt) {
    for (const c of this.cars) {
      c.s += c.v * dt * c.dir;
      if (c.s > c.seg.len || c.s < 0) this._carryOn(c, true);
    }
    for (const p of this.people) {
      p.s += p.v * dt * p.dir;
      if (p.s > p.seg.len || p.s < 0) this._carryOn(p, false);
    }
    this.frame += 1;
  }

  /** ``ground(lon, lat)``: the ground's height, when the walk has terrain. */
  layers(deck, ground = () => 0) {
    const cars = this.cars.map((c) => {
      const at = along(c.seg.path, c.s);
      const heading = at.heading + (c.dir < 0 ? Math.PI : 0);
      // Each keeps to the right of its own way (right-hand traffic; illustrative).
      const lane = Math.min(3.2, c.seg.width / 4);
      const z = ground(at.lon, at.lat);
      return { polygon: box(at.lon, at.lat, heading, 4.4, 1.8, lane).map((p) => [p[0], p[1], z]),
               colour: c.colour };
    });
    const people = this.people.map((p) => {
      const at = along(p.seg.path, p.s);
      const k = Math.cos((at.lat * Math.PI) / 180);
      const rx = Math.cos(at.heading), ry = -Math.sin(at.heading);
      return { position: [at.lon + (rx * p.side) / (M_PER_DEG * k), at.lat + (ry * p.side) / M_PER_DEG,
                          ground(at.lon, at.lat)],
               colour: p.colour };
    });
    return [
      new deck.SolidPolygonLayer({
        id: "ambience-cars", data: cars, extruded: true,
        getPolygon: (c) => c.polygon, getElevation: 1.45, getFillColor: (c) => c.colour,
        material: { ambient: 0.5, diffuse: 0.6, shininess: 40 },
        updateTriggers: { getPolygon: this.frame },
      }),
      new deck.ColumnLayer({
        id: "ambience-people", data: people, diskResolution: 8, radius: 0.26,
        extruded: true, getPosition: (p) => p.position, getElevation: 1.7,
        getFillColor: (p) => p.colour,
        updateTriggers: { getPosition: this.frame },
      }),
    ];
  }
}
