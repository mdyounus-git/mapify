/* The 3D ground view: buildings and streets anywhere on Earth, to walk a
 * camera around.
 *
 * The globe answers "where on Earth". This answers the next question --
 * "does it look like this there?" -- by putting the footprints of the
 * actual buildings in front of the analyst, extruded to whatever heights
 * are known, over whatever imagery exists, from any angle.
 *
 * It takes over from the globe rather than living inside it. The globe's
 * closest zoom is about 25 km up; scrolling in past that dives into this
 * view at the same spot and scale, and zooming back out past city scale
 * hands back. The globe's own rendering is untouched.
 *
 * Data loads for wherever the camera goes: the local OpenStreetMap store
 * where a region is imported, OpenStreetMap vector tiles everywhere else,
 * Google Open Buildings where OSM is thin. What has been loaded stays
 * loaded, so panning back is instant.
 *
 * It is built on deck.gl (MIT, vis.gl), loaded on first use.
 *
 * What it will not do is pretend. Most buildings in the world have no
 * recorded height; they are drawn at a nominal one, in a colour of their
 * own, and the legend says so. Imagery outside the United States is 30 m
 * a pixel and the strip at the bottom says so. And when the point being
 * examined is an estimate with kilometres of uncertainty, the view says
 * the buildings are around the estimate, not necessarily where the
 * photograph was taken.
 */

import { BuildingIndex, EYE_HEIGHT_M, Walker, floorLines, groundLayers,
         nearestRoadPoint, streetLabels } from "./walk.js";
import { AMBIENCE_NOTE, Ambience, SceneFx, describeWeather, lightFor, readWeather,
         skyFor } from "./scene.js";
import { SAMPLE_Z, TERRAIN_CREDIT, TERRAIN_URL, TERRARIUM, TerrainMeshLoader,
         TerrainSampler, onTerrain, tileBounds, tileOf } from "./terrain.js";

const DECK_SRC = "vendor/deck/dist.min.js";
let deckLoading = null;

/** The sun's elevation in degrees (NOAA's approximation, good to ~1°). */
function sunElevation(lat, lon, ms) {
  const d = (ms / 86400000) - 10957.5;             // days since J2000.0
  const g = ((357.529 + 0.98560028 * d) % 360) * Math.PI / 180;
  const q = (280.459 + 0.98564736 * d) % 360;
  const L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * Math.PI / 180;
  const e = (23.439 - 0.00000036 * d) * Math.PI / 180;
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
  const gmst = (18.697374558 + 24.06570982441908 * d) % 24;
  const ha = ((gmst * 15 + lon) * Math.PI / 180) - ra;
  const la = lat * Math.PI / 180;
  return Math.asin(Math.sin(la) * Math.sin(dec)
    + Math.cos(la) * Math.cos(dec) * Math.cos(ha)) * 180 / Math.PI;
}

/** Today at about 10:30 local solar time at a longitude, in UTC ms. */
function lateMorning(lon) {
  const now = new Date();
  const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return midnight + (10.5 - lon / 15) * 3600000;
}

function loadDeck() {
  if (window.deck) return Promise.resolve(window.deck);
  if (!deckLoading) {
    deckLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = DECK_SRC;
      s.async = true;
      s.onload = () => (window.deck ? resolve(window.deck)
        : reject(new Error("deck.gl loaded but did not register")));
      s.onerror = () => {
        deckLoading = null;
        reject(new Error("deck.gl could not be loaded"));
      };
      document.head.appendChild(s);
    });
  }
  return deckLoading;
}

/* How a building's height is known decides its colour. The eye should
   tell data from assumption at a glance, without reading the legend. */
const BUILDING_COLOUR = {
  measured: [242, 236, 224, 255],     // a height tag: warm white
  levels: [214, 222, 234, 255],       // a floor count: cool white
  published: [226, 228, 232, 255],    // OpenMapTiles' number: near white
  assumed: [120, 134, 152, 235],      // nothing recorded: muted slate
  gob: [196, 160, 112, 235],          // a Google detection: sand
};

/* At street level the same distinction in daylight colours: stone and
   render where a height is known, plain concrete -- still the darker --
   where it is nominal. */
const WALK_COLOUR = {
  measured: [236, 226, 208, 255], levels: [222, 226, 232, 255],
  published: [228, 228, 230, 255], assumed: [184, 186, 190, 255],
  gob: [210, 186, 150, 255],
};

const ROAD_COLOUR = {
  motorway: [236, 148, 96], trunk: [236, 170, 104], primary: [238, 202, 124],
  secondary: [232, 222, 164], tertiary: [222, 222, 212],
  footway: [168, 176, 188], path: [168, 176, 188], cycleway: [150, 190, 210],
  steps: [168, 176, 188], pedestrian: [190, 186, 176], track: [180, 164, 140],
};
const ROAD_DEFAULT = [206, 210, 218];

// Zoom thresholds, in deck.gl's Web Mercator zoom.
const ZOOM_BUILDINGS = 13.5;   // closer than this, buildings are worth loading
const ZOOM_HANDBACK = 9.6;     // further than this, the globe does it better
const MAX_BUILDINGS = 90000;   // held in memory before the farthest go
const WALK_FOVY = 72;
// Zooming in past one of these levels (district, street, close up) settles
// the camera into a steeper view, with a pulse where it landed.
const ZOOM_BANDS = [14, 16, 18];
const BAND_PITCH = [45, 58, 66];          // degrees top to bottom: a phone camera held upright

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

function fmtKm(km) {
  if (km == null) return "";
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(km < 10 ? 1 : 0)} km`;
}

function metresBetween(a, b) {
  const R = 6371008.8, d = Math.PI / 180;
  const dLat = (b[1] - a[1]) * d, dLon = (b[0] - a[0]) * d;
  const s = Math.sin(dLat / 2) ** 2
    + Math.cos(a[1] * d) * Math.cos(b[1] * d) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

/** Web Mercator zoom at which a viewport shows about `spanM` metres tall. */
export function zoomForSpan(spanM, lat, viewportPx) {
  const C = 40075016.686;
  return Math.log2((C * Math.cos((lat * Math.PI) / 180) * viewportPx)
                   / (512 * Math.max(1, spanM)));
}

/** The inverse: how many metres tall a viewport is at a zoom. */
export function spanForZoom(zoom, lat, viewportPx) {
  const C = 40075016.686;
  return (C * Math.cos((lat * Math.PI) / 180) * viewportPx) / (512 * 2 ** zoom);
}

function radiusForZoom(zoom) {
  if (zoom >= 17) return 500;
  if (zoom >= 16) return 800;
  if (zoom >= 15) return 1300;
  if (zoom >= 14) return 2000;
  return 2600;
}

export class Ground3D {
  /**
   * @param {HTMLElement} host   element to fill (positioned, full-size)
   * @param {{onClose?: (view: {lat:number, lon:number, zoom:number,
   *          viewportPx:number}) => void}} opts
   */
  constructor(host, opts = {}) {
    this.host = host;
    this.onClose = opts.onClose || null;
    this.deck = null;
    this.imagery = [];
    this.imageryKey = null;
    this.hlsDate = null;
    this.overlay = false;
    this.orbiting = false;
    this.viewState = null;
    this.target = null;
    this.walking = false;
    this.index = null;
    this._floors = null;
    this.photoOn = false;
    this.photoOpacity = 0.5;
    this.fovy = WALK_FOVY;
    this.weather = null;          // Open-Meteo's reading for the walk, as served
    this._reading = null;         // ...and what it means for the drawing
    this.ambience = new Ambience();
    this.ambienceOn = false;
    this._walkLayers = [];
    this.terrainOn = true;        // the ground's real height (terrain.js)
    this.sampler = new TerrainSampler();
    this._groundZ = 0;
    this.walker = new Walker({
      getState: () => this.viewState,
      setState: (v) => {
        this.viewState = this._onGround(v);
        this.deck?.setProps({ viewState: this.viewState });
      },
      index: () => this._buildingIndex(),
      onMove: (lon, lat) => this._walkMoved(lon, lat),
      onExit: () => this.exitWalk(),
    });
    this._reset();
    this._build();
  }

  _reset() {
    this.data = { buildings: new Map(), roads: new Map(), water: new Map(),
                  waterways: new Map(), green: new Map(), pois: new Map() };
    this.sources = new Map();
    this.notes = new Set();
    this.loaded = [];              // {center:[lon,lat], radius}
    this.loading = false;
    this.pendingLoad = null;
  }

  _build() {
    this.host.innerHTML = `
      <div class="g3d-canvas"></div>
      <canvas class="g3d-fx" aria-hidden="true"></canvas>
      <div class="g3d-photo" hidden aria-hidden="true"><img alt=""></div>
      <div class="g3d-top">
        <div class="g3d-title">
          <span class="g3d-eyebrow">3D ground view</span>
          <h2 class="g3d-place">—</h2>
          <p class="g3d-sub"></p>
        </div>
        <div class="g3d-tools" role="toolbar" aria-label="3D view controls">
          <button type="button" data-act="left" title="Rotate left (Q)">&#8634;</button>
          <button type="button" data-act="right" title="Rotate right (E)">&#8635;</button>
          <button type="button" data-act="tilt" title="Change tilt (T)">Tilt</button>
          <button type="button" data-act="north" title="Face north (N)">N</button>
          <button type="button" data-act="orbit" title="Orbit the centre (O)">Orbit</button>
          <button type="button" data-act="walk" class="g3d-walk"
                  title="Walk around at eye level (F)">Walk</button>
          <button type="button" data-act="photo" class="g3d-photo-btn" hidden
                  title="Lay your photograph over the view (P)">Photo</button>
          <button type="button" data-act="ambience" class="g3d-amb-btn" hidden
                  title="Illustrative people, vehicles and birds -- not observed at this place"
                  >Ambience</button>
          <label class="g3d-imagery-label">Ground
            <select class="g3d-imagery" aria-label="Ground imagery"></select>
          </label>
          <label class="g3d-toggle"><input type="checkbox" class="g3d-overlay">
            Settlements</label>
          <label class="g3d-toggle" title="The ground's real height (hills, valleys)">
            <input type="checkbox" class="g3d-terrain" checked> Terrain</label>
          <button type="button" data-act="close" class="g3d-close">Back to globe</button>
        </div>
      </div>
      <div class="g3d-legend" aria-label="Legend"></div>
      <div class="g3d-notes"></div>
      <div class="g3d-status" role="status"></div>
      <div class="g3d-hint">Drag to pan &middot; right-drag or Ctrl-drag to rotate and tilt &middot; scroll to zoom &middot; zoom out to return to the globe &middot; F to walk</div>
      <div class="g3d-walkhud" hidden>
        <div class="g3d-walk-where">
          <span class="g3d-eyebrow">Walking &middot; eye height 1.7 m</span>
          <strong class="g3d-walk-street">—</strong>
          <span class="g3d-walk-meta"></span>
        </div>
        <div class="g3d-walk-keys">
          <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> or arrows to move &middot;
          <kbd>Q</kbd><kbd>E</kbd> turn &middot; drag to look &middot;
          <kbd>Shift</kbd> run &middot; <kbd>Esc</kbd> stop walking
        </div>
        <div class="g3d-walk-keys g3d-walk-photokeys" hidden>
          <kbd>P</kbd> your photograph over the view &middot;
          <kbd>[</kbd><kbd>]</kbd> fade &middot; <kbd>-</kbd><kbd>=</kbd> lens
        </div>
        <div class="g3d-walk-lens"></div>
        <div class="g3d-walk-weather"></div>
        <div class="g3d-walk-amb" hidden></div>
        <div class="g3d-walk-light"></div>
      </div>
      <div class="g3d-attrib"></div>`;
    this.el = {
      canvas: this.host.querySelector(".g3d-canvas"),
      place: this.host.querySelector(".g3d-place"),
      sub: this.host.querySelector(".g3d-sub"),
      legend: this.host.querySelector(".g3d-legend"),
      notes: this.host.querySelector(".g3d-notes"),
      status: this.host.querySelector(".g3d-status"),
      attrib: this.host.querySelector(".g3d-attrib"),
      imagery: this.host.querySelector(".g3d-imagery"),
      overlay: this.host.querySelector(".g3d-overlay"),
      terrain: this.host.querySelector(".g3d-terrain"),
      orbit: this.host.querySelector('[data-act="orbit"]'),
      walk: this.host.querySelector('[data-act="walk"]'),
      hint: this.host.querySelector(".g3d-hint"),
      walkHud: this.host.querySelector(".g3d-walkhud"),
      walkStreet: this.host.querySelector(".g3d-walk-street"),
      walkMeta: this.host.querySelector(".g3d-walk-meta"),
      walkLight: this.host.querySelector(".g3d-walk-light"),
      walkLens: this.host.querySelector(".g3d-walk-lens"),
      photo: this.host.querySelector(".g3d-photo"),
      photoImg: this.host.querySelector(".g3d-photo img"),
      photoBtn: this.host.querySelector(".g3d-photo-btn"),
      photoKeys: this.host.querySelector(".g3d-walk-photokeys"),
      fx: this.host.querySelector(".g3d-fx"),
      ambBtn: this.host.querySelector(".g3d-amb-btn"),
      walkWeather: this.host.querySelector(".g3d-walk-weather"),
      walkAmb: this.host.querySelector(".g3d-walk-amb"),
    };
    this.fx = new SceneFx(this.el.fx);
    this.host.querySelector(".g3d-tools").addEventListener("click", (e) => {
      const act = e.target.closest("button")?.dataset.act;
      if (act) this._act(act);
    });
    this.el.imagery.addEventListener("change", () => {
      this.imageryKey = this.el.imagery.value;
      this._ensureHlsDate().then(() => this._render());
    });
    this.el.overlay.addEventListener("change", () => {
      this.overlay = this.el.overlay.checked;
      this._render();
    });
    this.el.terrain.addEventListener("change", () => {
      this.terrainOn = this.el.terrain.checked;
      if (this.walking) this.viewState = this._onGround(this.viewState);
      this.deck?.setProps({ viewState: this.viewState });
      this._render();
    });
    this._keys = (e) => {
      if (this.host.hidden) return;
      if (e.target.closest?.("input, select, textarea")) return;
      if (this.walking) {                               // the walker has the rest
        const k = e.key;
        if (k === "p" || k === "P") this._act("photo");
        else if (k === "[" || k === "]") this._fade(k === "]" ? 0.1 : -0.1);
        else if (k === "-" || k === "_") this._lens(3);
        else if (k === "=" || k === "+") this._lens(-3);
        else return;
        e.preventDefault();
        return;
      }
      const map = { q: "left", e: "right", t: "tilt", n: "north", o: "orbit",
                    f: "walk", Escape: "close" };
      const act = map[e.key] || map[e.key?.toLowerCase?.()];
      if (act) { e.preventDefault(); this._act(act); }
    };
    window.addEventListener("keydown", this._keys);
  }

  get isOpen() { return !this.host.hidden; }

  /**
   * Open over a location.
   *
   * @param {{lat:number, lon:number, label?:string, uncertaintyKm?:number,
   *          precisionKm?:number, origin?:string}} target
   * @param {{fromAltitudeKm?:number, viewportPx?:number}} how
   *   When coming from the globe, start at the scale the globe was showing
   *   and dive; otherwise open close in on the target.
   */
  async open(target, how = {}) {
    if (this.walking && this.target?.photoUrl !== target.photoUrl) this._setPhoto(false);
    this.target = target;
    this.host.hidden = false;
    this.el.place.textContent = target.label || "Location";
    this.el.sub.textContent = `${target.lat.toFixed(5)}, ${target.lon.toFixed(5)}`;
    this._status("Loading the 3D renderer…");
    let deck;
    try {
      deck = await loadDeck();
    } catch (err) {
      this._status(`The 3D view could not start: ${err.message}`, true);
      return;
    }

    const px = how.viewportPx || this.host.clientHeight || 800;
    let start;
    if (how.fromAltitudeKm) {
      // Match what the globe was showing: a camera h km up with a ~45°
      // field of view sees about 0.83 h of ground from top to bottom.
      const spanM = how.fromAltitudeKm * 1000 * 0.83;
      start = { longitude: target.lon, latitude: target.lat,
                zoom: Math.max(ZOOM_HANDBACK + 0.6, zoomForSpan(spanM, target.lat, px)),
                pitch: 0, bearing: 0 };
    } else {
      start = { longitude: target.lon, latitude: target.lat, zoom: 16.2,
                pitch: 55, bearing: -25 };
    }
    const limits = { minZoom: ZOOM_HANDBACK - 0.4, maxZoom: 20, maxPitch: 80 };
    this.viewState = { ...start, ...limits };
    this._band = ZOOM_BANDS.filter((z) => start.zoom >= z).length;
    if (!this.deck) this._createDeck(deck);
    else this.deck.setProps({ viewState: this.viewState });

    await this._loadImageryChoices(target);
    this._render();
    if (how.walk) {
      // Straight into the street: load around the point, then stand in it.
      await this._maybeLoad(true);
      await this.enterWalk({ lon: target.lon, lat: target.lat });
    } else if (how.fromAltitudeKm) {
      // The dive: down to street level, tilting as it goes.
      this._status("Descending…");
      this._animateTo({ zoom: 15.6, pitch: 52, bearing: -18 }, 2600);
    } else {
      this._scheduleLoad(0);
    }
  }

  close(handBack = true) {
    if (this.walking) this.exitWalk(false);
    this._stopOrbit();
    this.host.hidden = true;
    const v = this.viewState || {};
    if (handBack) {
      this.onClose?.({ lat: v.latitude, lon: v.longitude, zoom: v.zoom,
                       viewportPx: this.host.clientHeight || 800 });
    }
  }

  dispose() {
    window.removeEventListener("keydown", this._keys);
    this._stopOrbit();
    this.deck?.finalize();
    this.deck = null;
  }

  /* ───────────────────────────── deck ───────────────────────────── */

  _createDeck(deck) {
    const { Deck, MapView } = deck;
    // Fixed, readable daylight from above. Walking is lit by the sun where
    // and when the photograph was taken (_walkLighting).
    const lighting = this._overviewLighting(deck);
    this.deck = new Deck({
      parent: this.el.canvas,
      views: new MapView({ repeat: false }),
      viewState: this.viewState,
      controller: { dragRotate: true, touchRotate: true, keyboard: false,
                    inertia: 300, scrollZoom: { smooth: true, speed: 0.012 } },
      onViewStateChange: ({ viewState, interactionState }) => {
        if (interactionState?.isDragging || interactionState?.isZooming
            || interactionState?.isRotating) {
          this._stopOrbit();
          this._cancelAnimation();
        }
        const zoomed = !this.walking && this.viewState
          && Math.abs((viewState.zoom ?? 0) - (this.viewState.zoom ?? 0)) > 1e-6;
        this.viewState = viewState;
        this.deck.setProps({ viewState });
        this._onMove();
        if (zoomed && !this._animating) {
          clearTimeout(this._zoomTimer);
          this._zoomTimer = setTimeout(() => this._zoomSettled(), 260);
        }
      },
      effects: [lighting],
      getTooltip: ({ object, layer }) => this._tooltip(object, layer),
      layers: [],
    });
  }

  _onMove() {
    const v = this.viewState;
    if (this.walking) {                 // looking around: nothing to load or hand back
      this._walkMoved(v.longitude, v.latitude);
      return;
    }
    if (v.zoom < ZOOM_HANDBACK && !this._animating) {
      // Zoomed out past city scale: the globe is the better instrument.
      this.close(true);
      return;
    }
    this._scheduleLoad(450);
  }

  /* ─────────────────────────── loading ─────────────────────────── */

  _scheduleLoad(delayMs) {
    clearTimeout(this._loadTimer);
    this._loadTimer = setTimeout(() => this._maybeLoad(), delayMs);
  }

  async _maybeLoad(force = false) {
    if (!this.isOpen || !this.viewState) return;
    const v = this.viewState;
    if (!this.walking && !force && v.zoom < ZOOM_BUILDINGS) {
      if (!this.data.buildings.size) this._status("Zoom in to see buildings.");
      return;
    }
    const center = [v.longitude, v.latitude];
    const radius = this.walking || force ? 700 : radiusForZoom(v.zoom);
    // Already covered: the new view sits well inside an area loaded before.
    const covered = this.loaded.some((a) =>
      metresBetween(a.center, center) + radius * 0.55 <= a.radius);
    if (covered) return;
    if (this.loading) { this.pendingLoad = true; return; }

    this.loading = true;
    this._status("Loading buildings, streets and water for this area…");
    const t0 = performance.now();
    try {
      const url = `/api/geodata/around?lat=${center[1]}&lon=${center[0]}`
        + `&radius=${radius}`;
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const around = await r.json();
      if (!this.isOpen) return;
      this._merge(around, center);
      this.loaded.push({ center, radius });
      this._legend();
      this._notes();
      this._render();
      const n = around.buildings.length;
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      this._status(n
        ? `${this.data.buildings.size.toLocaleString()} buildings loaded (${secs} s)`
        : "No buildings are recorded for this area in any source.", !n);
      if (!this._orbitedOnce && n && this.target?.origin !== "camera"
          && !this.walking && !force) {
        this._orbitedOnce = true;
        this._startOrbit(9000);    // one slow look around, then hands off
      }
    } catch (err) {
      this._status(`Could not load this area: ${err.message}`, true);
    } finally {
      this.loading = false;
      if (this.pendingLoad) {
        this.pendingLoad = false;
        this._scheduleLoad(0);
      }
    }
  }

  _merge(around, center) {
    const put = (map, items) => { for (const it of items) map.set(it.id, it); };
    put(this.data.buildings, around.buildings);
    put(this.data.roads, around.roads);
    put(this.data.water, around.water);
    put(this.data.waterways, around.waterways);
    put(this.data.green, around.green);
    put(this.data.pois, around.pois);
    for (const s of around.sources || []) this.sources.set(s.name, s);
    for (const n of around.notes || []) this.notes.add(n);
    this.index = null;                  // walls changed: rebuild on next step
    this._floors = null;
    // Ambience switched on before the streets nearby had loaded has next to
    // nobody in it: populate it again now that they are here.
    if (this.ambienceOn && this.walking
        && this.ambience.cars.length + this.ambience.people.length < 20) {
      const v = this.viewState;
      this.ambience.build(this.data.roads.values(), v.longitude, v.latitude);
      this._ambienceAt = [v.longitude, v.latitude];
    }

    // Bound memory: past the cap, forget the buildings farthest from here.
    if (this.data.buildings.size > MAX_BUILDINGS) {
      const all = [...this.data.buildings.values()]
        .map((b) => [metresBetween(center, b.centroid), b.id])
        .sort((a, b) => a[0] - b[0]);
      for (const [, id] of all.slice(MAX_BUILDINGS)) this.data.buildings.delete(id);
      this.loaded = [];      // coverage no longer guaranteed; reload on demand
    }
  }

  async _loadImageryChoices(target) {
    try {
      const r = await fetch(`/api/geodata/status?lat=${target.lat}&lon=${target.lon}`);
      const status = await r.json();
      this.imagery = status.imagery || [];
    } catch {
      this.imagery = [];
    }
    this._fillImagery();
  }

  _fillImagery() {
    const sel = this.el.imagery;
    sel.innerHTML = "";
    for (const s of this.imagery.filter((i) => !i.overlay)) {
      const opt = document.createElement("option");
      opt.value = s.key;
      const res = s.metres_per_pixel_equator < 10
        ? `${s.metres_per_pixel_equator.toFixed(1)} m` : "30 m";
      opt.textContent = `${s.name} (${res})`;
      sel.appendChild(opt);
    }
    const none = document.createElement("option");
    none.value = "none";
    none.textContent = "No imagery";
    sel.appendChild(none);
    if (!this.imagery.some((i) => i.key === this.imageryKey)) {
      this.imageryKey = this.imagery.find((i) => !i.overlay)?.key || "none";
    }
    sel.value = this.imageryKey;
    this.el.overlay.parentElement.hidden = !this.imagery.some((i) => i.overlay);
  }

  async _ensureHlsDate() {
    const src = this.imagery.find((i) => i.key === this.imageryKey);
    if (!src?.dated || this.hlsDate) return;
    this._status("Finding the most recent Landsat pass over this spot…");
    try {
      const v = this.viewState;
      const r = await fetch(`/api/imagery/hls-latest?lat=${v.latitude}&lon=${v.longitude}`);
      const j = await r.json();
      this.hlsDate = j.date || null;
      this._status(j.date
        ? `Landsat imagery from ${j.date} (${j.days_ago} days ago)`
        : (j.note || "No recent Landsat pass found."), !j.date);
    } catch {
      this._status("Could not find a recent Landsat pass.", true);
    }
  }

  /* ─────────────────────────── drawing ─────────────────────────── */

  _render() {
    if (!this.deck) return;
    if (this.walking) {
      this._renderWalk();
      return;
    }
    const deck = window.deck;
    const d = this.data;
    const layers = [];

    const src = this.imagery.find((i) => i.key === this.imageryKey);
    if (this._terrainExt(deck)) layers.push(this._terrainLayer(deck, src));
    else if (src && !(src.dated && !this.hlsDate)) layers.push(this._tileLayer(deck, src, 1.0));
    if (this.overlay) {
      const ov = this.imagery.find((i) => i.overlay);
      if (ov) layers.push(this._tileLayer(deck, ov, 0.55));
    }

    layers.push(new deck.SolidPolygonLayer({
      id: "green", data: [...d.green.values()],
      getPolygon: (f) => f.polygon, getFillColor: [74, 128, 84, 120],
    }));
    layers.push(new deck.SolidPolygonLayer({
      id: "water", data: [...d.water.values()],
      getPolygon: (f) => f.polygon, getFillColor: [62, 116, 170, 190],
    }));
    layers.push(new deck.PathLayer({
      id: "waterways", data: [...d.waterways.values()],
      getPath: (f) => f.path, widthUnits: "meters",
      getWidth: (f) => (f.kind === "river" ? 18 : f.kind === "canal" ? 8 : 3),
      getColor: [72, 128, 186, 220], capRounded: true, jointRounded: true,
    }));
    layers.push(new deck.PathLayer({
      id: "roads", data: [...d.roads.values()],
      getPath: (f) => f.path, widthUnits: "meters", widthMinPixels: 1,
      getWidth: (f) => f.width,
      getColor: (f) => ROAD_COLOUR[f.class] || ROAD_DEFAULT,
      capRounded: true, jointRounded: true, pickable: true,
      autoHighlight: true, highlightColor: [90, 184, 240, 220],
    }));
    layers.push(new deck.SolidPolygonLayer({
      id: "buildings", data: [...d.buildings.values()], extruded: true,
      getPolygon: (b) => b.polygon.map((ring) =>
        ring.map((p) => [p[0], p[1], b.base || 0])),
      getElevation: (b) => Math.max(0.6, (b.height || 3) - (b.base || 0)),
      getFillColor: (b) => (b.source === "gob" ? BUILDING_COLOUR.gob
        : BUILDING_COLOUR[b.height_source] || BUILDING_COLOUR.assumed),
      material: { ambient: 0.32, diffuse: 0.62, shininess: 22,
                  specularColor: [48, 52, 58] },
      pickable: true, autoHighlight: true, highlightColor: [90, 184, 240, 235],
    }));

    // The point being examined, and how sure we are of it. Not drawn when
    // the view was opened on whatever was under the globe's camera.
    const t = this.target;
    if (t && t.origin !== "camera") {
      const ringKm = t.precisionKm ?? (t.uncertaintyKm && t.uncertaintyKm < 3
        ? t.uncertaintyKm : null);
      layers.push(new deck.ScatterplotLayer({
        id: "target", data: [t], getPosition: (p) => [p.lon, p.lat, 0.5],
        getRadius: ringKm ? Math.max(12, ringKm * 1000) : 14,
        radiusUnits: "meters", stroked: true, filled: true,
        getFillColor: [90, 184, 240, ringKm ? 28 : 90],
        getLineColor: [90, 184, 240, 255], lineWidthMinPixels: 2,
      }));
      layers.push(new deck.ColumnLayer({
        id: "target-beam", data: [t], diskResolution: 12, radius: 2.2,
        extruded: true, getPosition: (p) => [p.lon, p.lat],
        getElevation: 60, getFillColor: [90, 184, 240, 200],
      }));
    }
    layers.push(...this._matchLayers(deck, false));

    // Place names, nearest first, and never on top of one another: the
    // collision filter drops a label that would overlap a nearer one.
    const v = this.viewState;
    const named = [...d.pois.values()].filter((p) => p.name)
      .map((p) => ({ p, dist: metresBetween([v.longitude, v.latitude], p.position) }))
      .sort((a, b) => a.dist - b.dist).slice(0, 60);
    layers.push(new deck.TextLayer({
      id: "pois", data: named, characterSet: "auto",
      getPosition: (n) => [n.p.position[0], n.p.position[1], 12],
      getText: (n) => n.p.name, getSize: 11, sizeUnits: "pixels",
      getColor: [236, 242, 250, 235], background: true,
      getBackgroundColor: [12, 16, 24, 170], backgroundPadding: [3, 1],
      fontFamily: '"Segoe UI", "Nirmala UI", "Microsoft YaHei", sans-serif',
      getTextAnchor: "middle", getAlignmentBaseline: "bottom",
      billboard: true, pickable: true, visible: v.zoom >= 15.5,
      extensions: deck.CollisionFilterExtension
        ? [new deck.CollisionFilterExtension()] : [],
      collisionEnabled: true,
      getCollisionPriority: (n) => -n.dist,
      collisionTestProps: { sizeScale: 1.6 },
    }));

    layers.splice(0, layers.length, ...onTerrain(layers, this._terrainExt(deck)));
    this._overviewLayers = layers;
    this.deck.setProps({ layers });
    this._attrib();
  }

  /* ─────────────────────────── walking ─────────────────────────── */

  _buildingIndex() {
    if (!this.index) this.index = new BuildingIndex(this.data.buildings.values());
    return this.index;
  }

  /**
   * Stand in the street at a point and walk.
   *
   * If the point is inside a building -- an estimate often is -- start on
   * the nearest street instead, facing along it; failing a street, start
   * where asked and let the walker out of that one building.
   */
  async enterWalk(at = null) {
    const deck = window.deck;
    if (!deck || !this.deck) return;
    this._stopOrbit();
    this._cancelAnimation();
    const v = this.viewState;
    let lon = at?.lon ?? v.longitude, lat = at?.lat ?? v.latitude;
    let bearing = v.bearing || 0;
    const index = this._buildingIndex();
    const inside = index.hit(lon, lat);
    this.walker.ghostOf = null;
    if (inside) {
      const road = nearestRoadPoint(this.data.roads.values(), lon, lat, 160);
      if (road && !index.hit(road.lon, road.lat)) {
        ({ lon, lat, bearing } = road);
      } else {
        this.walker.ghostOf = inside.id;
      }
    }
    this.walking = true;
    this.host.classList.add("walking");
    this.el.walk.textContent = "Stop walking";
    this.el.walk.classList.add("on");
    this.el.walkHud.hidden = false;
    this.el.hint.hidden = true;
    const photo = Boolean(this.target?.photoUrl);
    this.el.photoBtn.hidden = !photo;
    this.el.photoKeys.hidden = !photo;
    this.el.ambBtn.hidden = false;
    this.fovy = WALK_FOVY;
    if (this._terrainExt(deck)) {
      await this.sampler.load(lon, lat);
      this._groundZ = this.sampler.heightAt(lon, lat) ?? 0;
    }
    this.viewState = this._onGround({ longitude: lon, latitude: lat,
                                      position: [0, 0, EYE_HEIGHT_M],
                                      bearing, pitch: 0, minPitch: -80, maxPitch: 80 });
    this._walkOrigin = [lon, lat];
    this._lastLoadAt = [lon, lat];
    this.deck.setProps({
      views: this._walkView(deck),
      controller: { dragRotate: true, dragPan: false, scrollZoom: false,
                    keyboard: false, inertia: false, touchRotate: true },
      viewState: this.viewState,
      effects: [this._walkLighting(deck, lon, lat)],
    });
    this._render();
    this._walkMoved(lon, lat, true);
    this.walker.start();
    this._status("");
    this._loadWeather(lon, lat);
  }

  exitWalk(render = true) {
    if (!this.walking) return;
    this._setPhoto(false);
    this.el.photoBtn.hidden = true;
    this.el.photoKeys.hidden = true;
    this._setAmbience(false);
    this.el.ambBtn.hidden = true;
    this.fx.stop();
    this.host.style.background = "";
    this.el.walkWeather.textContent = "";
    this.walker.stop();
    this.walking = false;
    this.host.classList.remove("walking");
    this.el.walk.textContent = "Walk";
    this.el.walk.classList.remove("on");
    this.el.walkHud.hidden = true;
    this.el.hint.hidden = false;
    const v = this.viewState;
    const deck = window.deck;
    this.viewState = { longitude: v.longitude, latitude: v.latitude, zoom: 17.6,
                       pitch: 60, bearing: v.bearing || 0,
                       minZoom: ZOOM_HANDBACK - 0.4, maxZoom: 20, maxPitch: 80 };
    if (deck && this.deck) {
      this.deck.setProps({
        views: new deck.MapView({ repeat: false }),
        controller: { dragRotate: true, touchRotate: true, keyboard: false,
                      inertia: 300 },
        viewState: this.viewState,
        effects: [this._overviewLighting(deck)],
      });
    }
    if (render) this._render();
  }

  _walkView(deck) {
    return new deck.FirstPersonView({ id: "walk", fovy: this.fovy, near: 0.1, far: 6000 });
  }

  /**
   * The photograph laid over the street, to line the two up by eye.
   *
   * It fills the view from top to bottom, so with the view's lens set to
   * the photograph's (its EXIF focal length gives the angle it covers
   * top to bottom) the two agree in scale: standing where the camera
   * stood and facing where it faced, the buildings fall on the
   * buildings. The lens can be changed when the EXIF does not say.
   */
  _setPhoto(on) {
    const url = this.target?.photoUrl;
    const was = this.photoOn;
    this.photoOn = Boolean(on && url && this.walking);
    this.el.photo.hidden = !this.photoOn;
    this.el.photoBtn.classList.toggle("on", this.photoOn);
    if (this.photoOn) {
      if (this.el.photoImg.getAttribute("src") !== url) this.el.photoImg.src = url;
      this.el.photoImg.style.opacity = String(this.photoOpacity);
      const vfov = this.target?.fovDeg?.[1];
      this._lens(0, Number.isFinite(vfov) ? vfov : null);
    } else if (was && this.walking) {
      this._lens(0, WALK_FOVY);
    }
    if (!this.photoOn) this.el.walkLens.textContent = "";
  }

  _fade(delta) {
    if (!this.photoOn) return;
    this.photoOpacity = Math.max(0.1, Math.min(0.9, this.photoOpacity + delta));
    this.el.photoImg.style.opacity = String(this.photoOpacity);
  }

  _lens(delta, absolute = null) {
    const deck = window.deck;
    if (!this.walking || !deck || !this.deck) return;
    this.fovy = Math.max(15, Math.min(100, absolute ?? this.fovy + delta));
    this.deck.setProps({ views: this._walkView(deck) });
    if (!this.photoOn) return;
    const vfov = this.target?.fovDeg?.[1];
    const own = Number.isFinite(vfov) && Math.abs(this.fovy - vfov) < 0.5;
    this.el.walkLens.textContent = `Lens ${Math.round(this.fovy)}° top to bottom`
      + (own ? ", the photograph's own (from its EXIF focal length)"
        : Number.isFinite(vfov) ? ` (the photograph's EXIF says ${Math.round(vfov)}°)`
          : " (the photograph does not record its lens; adjust with - and =)")
      + ". Turn and step until the buildings line up.";
  }

  /** Where the matching reference photographs were taken. */
  _matchLayers(deck, walking) {
    const m = this.target?.matches;
    if (!m?.length) return [];
    return [
      new deck.ColumnLayer({
        id: "matches-pole", data: m, diskResolution: 10,
        radius: walking ? 0.22 : 1.4, extruded: true,
        getPosition: (p) => [p.lon, p.lat], getElevation: walking ? 2.4 : 26,
        getFillColor: [240, 160, 60, 235],
      }),
      new deck.TextLayer({
        id: "matches-label", data: m, characterSet: "auto",
        getPosition: (p) => [p.lon, p.lat, walking ? 2.7 : 28],
        getText: (p) => p.label, getSize: 12, sizeUnits: "pixels", billboard: true,
        getColor: [24, 18, 8, 255], background: true,
        getBackgroundColor: [244, 176, 76, 235], backgroundPadding: [4, 2],
        fontFamily: '"Segoe UI", "Nirmala UI", "Microsoft YaHei", sans-serif',
        getTextAnchor: "middle", getAlignmentBaseline: "bottom",
      }),
    ];
  }

  /** The sun where and when the photograph was taken, if that is known. */
  _walkLighting(deck, lon, lat) {
    const { LightingEffect, AmbientLight } = deck;
    const SunLight = deck._SunLight || deck.SunLight;
    const asked = this.target?.capturedAt ? Date.parse(this.target.capturedAt) : NaN;
    let ts = Number.isFinite(asked) ? asked : lateMorning(lon);
    let note;
    if (Number.isFinite(asked) && sunElevation(lat, lon, asked) < 5) {
      ts = lateMorning(lon);
      note = "The photograph was taken with the sun down; lit at 10:30 local solar time instead.";
    } else if (Number.isFinite(asked)) {
      note = `Lit by the sun as it was when the photograph was taken (${
        new Date(asked).toISOString().slice(0, 16).replace("T", " ")} UTC).`;
    } else {
      note = "Lit by the sun at 10:30 local solar time today; the photograph's time is not known.";
    }
    this._lightNote = note;
    if (!SunLight) return this._overviewLighting(deck);
    // No cast shadows: deck.gl's shadow maps are experimental, and over a
    // few kilometres of city they darkened every wall rather than casting.
    // The sun's direction still lights each face by the hour.
    const k = lightFor(this._reading);       // cloud dims the sun, lifts the diffuse
    return new LightingEffect({
      ambient: new AmbientLight({ color: [255, 255, 255], intensity: 1.55 * k.ambient }),
      sun: new SunLight({ timestamp: ts, color: [255, 246, 230], intensity: 1.6 * k.sun }),
    });
  }

  /**
   * The weather the walk is drawn in, from Open-Meteo: at the hour the
   * photograph was taken when that is known, otherwise now. The sky, the
   * light, rain, snow, fog and haze follow the reading; nothing is drawn
   * that it does not say. With no reading the walk stays as it was.
   */
  async _loadWeather(lon, lat) {
    const at = this.target?.capturedAt;
    const key = `${lat.toFixed(2)},${lon.toFixed(2)},${at || "now"}`;
    if (this._weatherKey !== key) {
      this._weatherKey = key;
      this.weather = null;
      try {
        const q = `lat=${lat.toFixed(5)}&lon=${lon.toFixed(5)}`
          + (at ? `&at=${encodeURIComponent(at)}` : "");
        const resp = await fetch(`/api/weather/scene?${q}`);
        this.weather = resp.ok ? await resp.json() : null;
      } catch (_) {
        this.weather = null;
      }
    }
    if (!this.walking) return;
    this._reading = readWeather(this.weather);
    this.host.style.background = this._reading ? skyFor(this._reading) : "";
    this.el.walkWeather.textContent = describeWeather(this.weather);
    this.fx.set(this._reading);
    this.fx.bearing = this.viewState?.bearing || 0;
    if (this.fx.active) this.fx.start(); else this.fx.stop();
    const deck = window.deck;
    if (deck && this.deck) {
      const v = this.viewState;
      this.deck.setProps({ effects: [this._walkLighting(deck, v.longitude, v.latitude)] });
    }
    this._attrib();
  }

  /**
   * Illustrative people, vehicles and birds (scene.js). Off by default;
   * while on, the walk says they are illustrative.
   */
  _setAmbience(on) {
    this.ambienceOn = Boolean(on && this.walking);
    this.el.ambBtn.classList.toggle("on", this.ambienceOn);
    this.el.walkAmb.hidden = !this.ambienceOn;
    this.el.walkAmb.textContent = this.ambienceOn ? AMBIENCE_NOTE : "";
    this.fx.setBirds(this.ambienceOn);
    if (this.ambienceOn) {
      const v = this.viewState;
      this.ambience.build(this.data.roads.values(), v.longitude, v.latitude);
      this._ambienceAt = [v.longitude, v.latitude];
      this.fx.start();
      if (!this._ambRaf) {
        let last = performance.now(), drawn = 0;
        const tick = (now) => {
          this._ambRaf = requestAnimationFrame(tick);
          this.ambience.step(Math.min(0.1, (now - last) / 1000));
          last = now;
          if (now - drawn > 50 && this.deck && window.deck) {   // ~20 frames a second
            drawn = now;
            this.deck.setProps({ layers: [...this._walkLayers,
              ...onTerrain(this.ambience.layers(window.deck), this._terrainExt(window.deck))] });
          }
        };
        this._ambRaf = requestAnimationFrame(tick);
      }
    } else {
      if (this._ambRaf) cancelAnimationFrame(this._ambRaf);
      this._ambRaf = null;
      if (!this.fx.active) this.fx.stop();
      if (this.walking && this.deck) this.deck.setProps({ layers: this._walkLayers });
    }
  }

  _overviewLighting(deck) {
    const { LightingEffect, AmbientLight, DirectionalLight } = deck;
    return new LightingEffect({
      ambient: new AmbientLight({ color: [255, 255, 255], intensity: 1.15 }),
      key: new DirectionalLight({ color: [255, 246, 232], intensity: 1.6,
                                  direction: [-2, -3, -4] }),
      fill: new DirectionalLight({ color: [200, 214, 255], intensity: 0.55,
                                   direction: [3, 2, -1] }),
    });
  }

  /** As the walker moves: the HUD, floor lines, and more of the city. */
  _walkMoved(lon, lat, force = false) {
    const now = performance.now();
    if (!force && now - (this._hudAt || 0) < 180) return;
    this._hudAt = now;
    // The ground under the walker comes in tiles: a new one, a new render.
    const t = tileOf(lon, lat, SAMPLE_Z);
    if (this._terrainExt(window.deck) && this._walkTile !== `${t.x}/${t.y}`) {
      this._walkTile = `${t.x}/${t.y}`;
      this._render();
    }
    this.fx.bearing = this.viewState?.bearing || 0;
    if (this.ambienceOn && this._ambienceAt
        && metresBetween(this._ambienceAt, [lon, lat]) > 300) {
      this.ambience.build(this.data.roads.values(), lon, lat);
      this._ambienceAt = [lon, lat];
    }
    const road = nearestRoadPoint(this.data.roads.values(), lon, lat, 40);
    const v = this.viewState;
    const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    const b = ((v.bearing % 360) + 360) % 360;
    this.el.walkStreet.textContent = road?.road?.name
      || (road ? `an unnamed ${road.road.class || "street"}` : "off the street network");
    this.el.walkMeta.textContent = `facing ${dirs[Math.round(b / 45) % 8]} ${Math.round(b)}°`
      + ` · ${lat.toFixed(5)}, ${lon.toFixed(5)}`
      + (this.walker.running ? " · running" : "");
    this.el.walkLight.textContent = this._lightNote || "";

    // Floor lines follow the walker; recomputed every ~120 m.
    const moved = this._floorsAt ? metresBetween(this._floorsAt, [lon, lat]) : Infinity;
    if (moved > 120 || !this._floors) {
      this._floorsAt = [lon, lat];
      this._floors = null;
      this._render();
    }
    // More of the city as the walker leaves what has been loaded.
    if (metresBetween(this._lastLoadAt || [lon, lat], [lon, lat]) > 280) {
      this._lastLoadAt = [lon, lat];
      this._scheduleLoad(0);
    }
  }

  _renderWalk() {
    const deck = window.deck;
    const d = this.data;
    const v = this.viewState;
    const here = [v.longitude, v.latitude];
    const buildings = [...d.buildings.values()];
    if (!this._floors) this._floors = floorLines(buildings, here[0], here[1]);
    const layers = [...(this._terrainExt(deck) ? this._walkTerrain(deck, here) : []),
                    ...groundLayers(deck, d, this._walkOrigin || here)];
    layers.push(new deck.SolidPolygonLayer({
      id: "buildings", data: buildings, extruded: true,
      getPolygon: (b) => b.polygon.map((ring) =>
        ring.map((p) => [p[0], p[1], b.base || 0])),
      getElevation: (b) => Math.max(0.6, (b.height || 3) - (b.base || 0)),
      getFillColor: (b) => (b.source === "gob" ? WALK_COLOUR.gob
        : WALK_COLOUR[b.height_source] || WALK_COLOUR.assumed),
      material: { ambient: 0.42, diffuse: 0.58, shininess: 10,
                  specularColor: [40, 44, 50] },
      pickable: true,
    }));
    layers.push(new deck.PathLayer({
      id: "walk-floors", data: this._floors,
      getPath: (f) => f.path, widthUnits: "pixels", getWidth: 2,
      getColor: [44, 50, 62, 190],
    }));
    const labels = streetLabels(d.roads.values(), here[0], here[1]);
    layers.push(new deck.TextLayer({
      id: "walk-streets", data: labels, characterSet: "auto",
      getPosition: (l) => l.position, getText: (l) => l.name,
      getSize: 13, sizeUnits: "pixels", billboard: true,
      getColor: [250, 252, 255, 240], background: true,
      getBackgroundColor: [28, 74, 132, 215], backgroundPadding: [5, 2],
      fontFamily: '"Segoe UI", "Nirmala UI", "Microsoft YaHei", sans-serif',
      getTextAnchor: "middle", getAlignmentBaseline: "bottom",
    }));
    const named = [...d.pois.values()].filter((p) => p.name)
      .map((p) => ({ p, dist: metresBetween(here, p.position) }))
      .filter((n) => n.dist < 350).sort((a, b) => a.dist - b.dist).slice(0, 30);
    // Its own id: the overview's "pois" carries collision props this one
    // does not, and deck.gl reusing one layer's state for the other failed
    // ("getCollisionPriority is not a function").
    layers.push(new deck.TextLayer({
      id: "walk-pois", data: named, characterSet: "auto",
      getPosition: (n) => [n.p.position[0], n.p.position[1], 4.5],
      getText: (n) => n.p.name, getSize: 12, sizeUnits: "pixels",
      getColor: [236, 242, 250, 235], background: true,
      getBackgroundColor: [12, 16, 24, 175], backgroundPadding: [3, 1],
      fontFamily: '"Segoe UI", "Nirmala UI", "Microsoft YaHei", sans-serif',
      getTextAnchor: "middle", getAlignmentBaseline: "bottom",
      billboard: true, pickable: true,
    }));
    const t = this.target;
    if (t && t.origin !== "camera") {
      layers.push(new deck.ColumnLayer({
        id: "target-beam", data: [t], diskResolution: 16, radius: 0.6,
        extruded: true, getPosition: (p) => [p.lon, p.lat],
        getElevation: 40, getFillColor: [90, 184, 240, 170],
      }));
    }
    layers.push(...this._matchLayers(deck, true));
    const ext = this._terrainExt(deck);
    this._walkLayers = onTerrain(layers, ext);
    this.deck.setProps({ layers: this.ambienceOn
      ? [...this._walkLayers, ...onTerrain(this.ambience.layers(deck), ext)]
      : this._walkLayers });
    this._attrib();
  }

  /** deck.gl's terrain extension while terrain is on, else null. */
  _terrainExt(deck) {
    if (!this.terrainOn || !deck || !deck._TerrainExtension) return null;
    if (!this._terrainExtension) this._terrainExtension = new deck._TerrainExtension();
    return this._terrainExtension;
  }

  /** The walker's eye 1.7 m above the ground under it (sea level when the
   *  ground's height is not known yet, or terrain is off). */
  _onGround(v) {
    if (!this.walking || !v) return v;
    if (this._terrainExt(window.deck)) {
      const h = this.sampler.heightAt(v.longitude, v.latitude);
      if (h != null && Number.isFinite(h)) this._groundZ = h;
    } else {
      this._groundZ = 0;
    }
    return { ...v, position: [0, 0, EYE_HEIGHT_M + this._groundZ] };
  }

  /** The overview's ground: the terrain, with the chosen imagery on it. */
  _terrainLayer(deck, src) {
    const usable = src && !(src.dated && !this.hlsDate);
    const date = usable && src.dated ? `?date=${encodeURIComponent(this.hlsDate)}` : "";
    return new deck.TerrainLayer({
      id: `terrain-${usable ? src.key : "plain"}-${usable && src.dated ? this.hlsDate : "x"}`,
      minZoom: 0, maxZoom: usable ? Math.min(14, src.max_zoom) : 14,
      elevationDecoder: TERRARIUM, elevationData: TERRAIN_URL,
      texture: usable ? `/api/tiles/${src.key}/{z}/{x}/{y}${date}` : null,
      color: [158, 156, 146], meshMaxError: 4, operation: "terrain+draw",
      loaders: [TerrainMeshLoader],
      loadOptions: { worker: false, terrain: { skirtHeight: 40 } },
      onTileError: () => {},
    });
  }

  /** The walk's ground: the nine elevation tiles around the walker, each
   *  one mesh (a first-person view does not pick tiles the way a map does). */
  _walkTerrain(deck, here) {
    const t = tileOf(here[0], here[1], SAMPLE_Z);
    const out = [];
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dx = -1; dx <= 1; dx += 1) {
        const x = t.x + dx, y = t.y + dy;
        out.push(new deck.TerrainLayer({
          id: `walk-terrain-${SAMPLE_Z}-${x}-${y}`,
          elevationData: TERRAIN_URL.replace("{z}", SAMPLE_Z).replace("{x}", x).replace("{y}", y),
          bounds: tileBounds(x, y, SAMPLE_Z),
          elevationDecoder: TERRARIUM, texture: null, color: [150, 148, 138],
          meshMaxError: 1.5, operation: "terrain+draw",
          loaders: [TerrainMeshLoader],
          loadOptions: { worker: false, terrain: { skirtHeight: 20, meshStep: 2 } },
        }));
      }
    }
    return out;
  }

  _tileLayer(deck, src, opacity) {
    const date = src.dated ? `?date=${encodeURIComponent(this.hlsDate)}` : "";
    return new deck.TileLayer({
      id: `imagery-${src.key}-${src.dated ? this.hlsDate : "x"}`,
      data: `/api/tiles/${src.key}/{z}/{x}/{y}${date}`,
      minZoom: 0, maxZoom: src.max_zoom, tileSize: 256, maxRequests: 6,
      opacity,
      onTileError: () => {},        // "no tile here" is empty ground, not an error
      renderSubLayers: (props) => {
        const bb = props.tile.boundingBox;
        return new deck.BitmapLayer(props, {
          data: null, image: props.data,
          bounds: [bb[0][0], bb[0][1], bb[1][0], bb[1][1]],
        });
      },
    });
  }

  /* ───────────────────────────── panels ───────────────────────────── */

  _legend() {
    const all = [...this.data.buildings.values()];
    const count = (fn) => all.filter(fn).length;
    const sw = (c) => `<i style="background:rgba(${c[0]},${c[1]},${c[2]},${
      (c[3] ?? 255) / 255})"></i>`;
    const rows = [
      [BUILDING_COLOUR.measured, "Height measured",
        count((b) => b.source !== "gob" && b.height_source === "measured")],
      [BUILDING_COLOUR.levels, "Height from floor count",
        count((b) => b.source !== "gob" && b.height_source === "levels")],
      [BUILDING_COLOUR.published, "Height published by OpenMapTiles",
        count((b) => b.source !== "gob" && b.height_source === "published")],
      [BUILDING_COLOUR.assumed, "Height not recorded — drawn nominal",
        count((b) => b.source !== "gob" && b.height_source === "assumed")],
      [BUILDING_COLOUR.gob, "Google detection — outline only",
        count((b) => b.source === "gob")],
    ].filter((r) => r[2] > 0);
    this.el.legend.innerHTML = rows.length
      ? `<p class="g3d-legend-head">Buildings</p>` + rows.map(([c, label, n]) =>
        `<div class="g3d-legend-row">${sw(c)}<span>${esc(label)}</span>
         <b>${n.toLocaleString()}</b></div>`).join("")
      : "";
  }

  _notes() {
    const notes = [];
    const t = this.target;
    if (t && t.origin !== "camera" && t.uncertaintyKm && t.uncertaintyKm > 1.5
        && t.origin !== "exif") {
      notes.push(
        `This location is an estimate with about ${fmtKm(t.uncertaintyKm)} `
        + "of uncertainty. These are the buildings around the estimate, "
        + "not necessarily where the photograph was taken.");
    }
    const all = [...this.data.buildings.values()];
    const assumed = all.filter((b) => b.height_source === "assumed").length;
    if (all.length && assumed / all.length > 0.3) {
      notes.push(`${Math.round((100 * assumed) / all.length)}% of these buildings `
        + "have no recorded height and are drawn at a nominal one, shaded "
        + "darker. Only the other heights are data.");
    }
    for (const n of this.notes) {
      if (!n.includes("no recorded height")) notes.push(n);
    }
    this.el.notes.innerHTML = notes.slice(0, 2).map((n) =>
      `<div class="g3d-note">${esc(n)}</div>`).join("");
  }

  _attrib() {
    const parts = [];
    for (const s of this.sources.values()) {
      parts.push(`${esc(s.attribution)} — ${esc(s.licence)}`);
    }
    const src = this.imagery.find((i) => i.key === this.imageryKey);
    if (src) {
      const when = src.dated && this.hlsDate ? ` (${this.hlsDate})` : "";
      parts.push(`Imagery: ${esc(src.attribution)}${esc(when)}`
        + (src.shows_buildings ? "" : ` <span class="g3d-warn">${esc(src.note)}</span>`));
    }
    if (this.overlay) {
      const ov = this.imagery.find((i) => i.overlay);
      if (ov) parts.push(esc(ov.attribution));
    }
    if (this.walking && this.weather?.available) parts.push(esc(this.weather.attribution));
    if (this._terrainExt(window.deck)) parts.push(esc(TERRAIN_CREDIT));
    parts.push("Rendered with deck.gl (MIT)");
    this.el.attrib.innerHTML = parts.join(" &middot; ");
  }

  _status(text, warn = false) {
    this.el.status.textContent = text || "";
    this.el.status.classList.toggle("warn", Boolean(warn));
  }

  _tooltip(object, layer) {
    if (!object) return null;
    if (layer?.id === "buildings") {
      const how = {
        measured: "height measured",
        levels: `height from ${object.levels ?? "?"} floors`,
        published: "height as published by OpenMapTiles",
        assumed: "height not recorded (drawn nominal)",
      }[object.height_source] || "";
      const lines = [
        object.name || (object.source === "gob" ? "Detected building"
          : `Building${object.type && !["yes", "building"].includes(object.type)
            ? ` (${object.type})` : ""}`),
        `${(object.height ?? 0).toFixed(1)} m — ${how}`,
        object.source === "gob"
          ? `Google Open Buildings, confidence ${(object.confidence ?? 0).toFixed(2)}`
          : object.source === "ofm" ? "OpenStreetMap (vector tiles)"
            : "OpenStreetMap (local import)",
      ];
      return { text: lines.join("\n") };
    }
    if (layer?.id === "roads") {
      return { text: `${object.name || "Unnamed street"}\n${object.class}` };
    }
    if (layer?.id === "pois" || layer?.id === "walk-pois") {
      const poi = object.p || object;          // labels carry {p, dist}
      return { text: `${poi.name}\n${poi.kind}` };
    }
    return null;
  }

  /* ──────────────────────────── controls ──────────────────────────── */

  _act(act) {
    if (act === "close") return this.close(true);
    if (!this.deck || !this.viewState) return;
    if (act === "walk") {
      if (this.walking) this.exitWalk();
      else this.enterWalk();
      return;
    }
    if (act === "photo") {
      this._setPhoto(!this.photoOn);
      return;
    }
    if (act === "ambience") {
      this._setAmbience(!this.ambienceOn);
      return;
    }
    const v = { ...this.viewState };
    if (act === "left") v.bearing -= 30;
    if (act === "right") v.bearing += 30;
    if (act === "north") { v.bearing = 0; v.pitch = 50; }
    if (act === "tilt") v.pitch = v.pitch < 20 ? 45 : v.pitch < 60 ? 72 : 0;
    if (act === "orbit") {
      if (this.orbiting) this._stopOrbit();
      else this._startOrbit();
      return;
    }
    this._stopOrbit();
    this._animateTo(v);
  }

  _cancelAnimation() {
    if (this._animFrame) cancelAnimationFrame(this._animFrame);
    this._animFrame = null;
    this._animating = false;
  }

  _animateTo(target, ms = 650) {
    this._cancelAnimation();
    const from = { ...this.viewState };
    const t0 = performance.now();
    this._animating = true;
    const step = (now) => {
      const k = Math.min(1, (now - t0) / ms);
      const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      const v = { ...from };
      for (const key of ["bearing", "pitch", "zoom", "longitude", "latitude"]) {
        if (target[key] != null) v[key] = from[key] + (target[key] - from[key]) * e;
      }
      this.viewState = v;
      this.deck.setProps({ viewState: v });
      if (k < 1) {
        this._animFrame = requestAnimationFrame(step);
      } else {
        this._animating = false;
        this._animFrame = null;
        this._scheduleLoad(0);
      }
    };
    this._animFrame = requestAnimationFrame(step);
  }

  /**
   * After a zoom comes to rest: if it went in past a level it was not at
   * before, ease into that level's steeper view with a slight turn, and
   * send a ring out from where it landed. Zooming out plays nothing.
   */
  _zoomSettled() {
    if (this.walking || this.orbiting || this._animating || !this.viewState) return;
    const v = this.viewState;
    const band = ZOOM_BANDS.filter((z) => v.zoom >= z).length;
    const before = this._band ?? 0;
    this._band = band;
    if (band <= before || band === 0) return;
    this._pulse([v.longitude, v.latitude]);
    const pitch = Math.max(v.pitch || 0, BAND_PITCH[band - 1]);
    this._animateTo({ pitch, bearing: (v.bearing || 0) + 14 }, 1100);
  }

  /** A ring expanding and fading from a point, over the view's own layers. */
  _pulse(position) {
    const deck = window.deck;
    if (!deck || !this.deck) return;
    cancelAnimationFrame(this._pulseFrame);
    const t0 = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - t0) / 950);
      const e = 1 - Math.pow(1 - k, 3);
      const ring = new deck.ScatterplotLayer({
        id: "zoom-pulse", data: [position], getPosition: (p) => p,
        radiusUnits: "pixels", getRadius: 8 + 120 * e, stroked: true, filled: false,
        getLineColor: [120, 200, 250, Math.round(230 * (1 - k))], lineWidthMinPixels: 2,
        updateTriggers: { getRadius: k, getLineColor: k },
      });
      if (!this.walking) {
        this.deck.setProps({ layers: [...(this._overviewLayers || []),
                                      ...onTerrain([ring], this._terrainExt(deck))] });
      }
      if (k < 1) this._pulseFrame = requestAnimationFrame(step);
      else if (!this.walking) this.deck.setProps({ layers: this._overviewLayers || [] });
    };
    this._pulseFrame = requestAnimationFrame(step);
  }

  _startOrbit(durationMs = Infinity) {
    this._stopOrbit();
    this.orbiting = true;
    this.el.orbit.classList.add("on");
    const t0 = performance.now();
    let last = t0;
    const step = (now) => {
      if (!this.orbiting || this.host.hidden) return;
      const dt = now - last;
      last = now;
      const v = { ...this.viewState, bearing: this.viewState.bearing + dt * 0.012 };
      this.viewState = v;
      this.deck.setProps({ viewState: v });
      if (now - t0 < durationMs) this._orbitFrame = requestAnimationFrame(step);
      else this._stopOrbit();
    };
    this._orbitFrame = requestAnimationFrame(step);
  }

  _stopOrbit() {
    this.orbiting = false;
    this.el?.orbit?.classList.remove("on");
    if (this._orbitFrame) cancelAnimationFrame(this._orbitFrame);
    this._orbitFrame = null;
  }
}
