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

const DECK_SRC = "vendor/deck/dist.min.js";
let deckLoading = null;

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
          <label class="g3d-imagery-label">Ground
            <select class="g3d-imagery" aria-label="Ground imagery"></select>
          </label>
          <label class="g3d-toggle"><input type="checkbox" class="g3d-overlay">
            Settlements</label>
          <button type="button" data-act="close" class="g3d-close">Back to globe</button>
        </div>
      </div>
      <div class="g3d-legend" aria-label="Legend"></div>
      <div class="g3d-notes"></div>
      <div class="g3d-status" role="status"></div>
      <div class="g3d-hint">Drag to pan &middot; right-drag or Ctrl-drag to rotate and tilt &middot; scroll to zoom &middot; zoom out to return to the globe</div>
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
      orbit: this.host.querySelector('[data-act="orbit"]'),
    };
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
    this._keys = (e) => {
      if (this.host.hidden) return;
      if (e.target.closest("input, select, textarea")) return;
      const map = { q: "left", e: "right", t: "tilt", n: "north", o: "orbit",
                    Escape: "close" };
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
    if (!this.deck) this._createDeck(deck);
    else this.deck.setProps({ viewState: this.viewState });

    await this._loadImageryChoices(target);
    this._render();
    if (how.fromAltitudeKm) {
      // The dive: down to street level, tilting as it goes.
      this._status("Descending…");
      this._animateTo({ zoom: 15.6, pitch: 52, bearing: -18 }, 2600);
    } else {
      this._scheduleLoad(0);
    }
  }

  close(handBack = true) {
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
    const { Deck, MapView, LightingEffect, AmbientLight, DirectionalLight } = deck;
    // Fixed, readable daylight. Lighting by the sun at the moment the
    // photograph was taken is a verification tool in its own right and
    // belongs with the solar techniques, where the time is established.
    const lighting = new LightingEffect({
      ambient: new AmbientLight({ color: [255, 255, 255], intensity: 1.15 }),
      key: new DirectionalLight({ color: [255, 246, 232], intensity: 1.6,
                                  direction: [-2, -3, -4] }),
      fill: new DirectionalLight({ color: [200, 214, 255], intensity: 0.55,
                                   direction: [3, 2, -1] }),
    });
    this.deck = new Deck({
      parent: this.el.canvas,
      views: new MapView({ repeat: false }),
      viewState: this.viewState,
      controller: { dragRotate: true, touchRotate: true, keyboard: false,
                    inertia: 300 },
      onViewStateChange: ({ viewState, interactionState }) => {
        if (interactionState?.isDragging || interactionState?.isZooming
            || interactionState?.isRotating) {
          this._stopOrbit();
          this._cancelAnimation();
        }
        this.viewState = viewState;
        this.deck.setProps({ viewState });
        this._onMove();
      },
      effects: [lighting],
      getTooltip: ({ object, layer }) => this._tooltip(object, layer),
      layers: [],
    });
  }

  _onMove() {
    const v = this.viewState;
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

  async _maybeLoad() {
    if (!this.isOpen || !this.viewState) return;
    const v = this.viewState;
    if (v.zoom < ZOOM_BUILDINGS) {
      if (!this.data.buildings.size) this._status("Zoom in to see buildings.");
      return;
    }
    const center = [v.longitude, v.latitude];
    const radius = radiusForZoom(v.zoom);
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
      if (!this._orbitedOnce && n && this.target?.origin !== "camera") {
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
    const deck = window.deck;
    const d = this.data;
    const layers = [];

    const src = this.imagery.find((i) => i.key === this.imageryKey);
    if (src && !(src.dated && !this.hlsDate)) layers.push(this._tileLayer(deck, src, 1.0));
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

    this.deck.setProps({ layers });
    this._attrib();
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
    if (layer?.id === "pois") {
      const poi = object.p || object;          // labels carry {p, dist}
      return { text: `${poi.name}\n${poi.kind}` };
    }
    return null;
  }

  /* ──────────────────────────── controls ──────────────────────────── */

  _act(act) {
    if (act === "close") return this.close(true);
    if (!this.deck || !this.viewState) return;
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
