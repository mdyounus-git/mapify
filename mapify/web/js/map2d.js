/* The 2D map the globe hands over to.
 *
 * Scrolling in at the globe's closest zoom, or choosing "View on the map",
 * opens a flat, top-down map of the same place at the same scale: real
 * satellite or aerial imagery, OpenStreetMap's roads, boundaries and place
 * names, and building footprints drawn as outlines -- nothing extruded and
 * nothing generated. The analysis pin and its uncertainty circle sit in the
 * same Web Mercator coordinates as the tiles, so a coordinate is drawn where
 * it is. Zooming out past the handback level returns to the globe, which is
 * not touched by any of this.
 *
 * Everything comes through Mapify's own server (the page may talk to no
 * one else): imagery from /api/tiles -- the owner's keyed provider when he
 * has set one, else the open sources for the place -- streets and names
 * from /api/streets, fonts from /api/fonts, footprints from
 * /api/geodata/around. MapLibre GL JS (BSD-3-Clause) draws it.
 */

const MAPLIBRE_JS = "/vendor/maplibre/maplibre-gl.js";
const MAPLIBRE_CSS = "/vendor/maplibre/maplibre-gl.css";
export const ZOOM_HANDBACK = 9.6;      // zooming out past this returns to the globe
const FOOTPRINTS_MIN_ZOOM = 15;        // footprints are worth drawing from here
const MAX_FOOTPRINTS = 60000;          // held in memory before the farthest go
const FONT = ["Noto Sans Regular"];
const FONT_BOLD = ["Noto Sans Bold"];
// Labels in a script MapLibre can shape: Indic and other complex scripts are
// drawn wrongly by it, so the English or Latin name is preferred.
const NAME = ["coalesce", ["get", "name_en"], ["get", "name:latin"],
              ["get", "name_int"], ["get", "name"]];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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

function metresBetween(a, b) {
  const R = 6371008.8, d = Math.PI / 180;
  const dLat = (b[1] - a[1]) * d, dLon = (b[0] - a[0]) * d;
  const s = Math.sin(dLat / 2) ** 2
    + Math.cos(a[1] * d) * Math.cos(b[1] * d) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

/** A circle of `radiusM` metres round a point, as a GeoJSON ring. */
export function circleRing(lon, lat, radiusM, steps = 72) {
  const R = 6371008.8, d = Math.PI / 180, ang = radiusM / R;
  const la = lat * d, lo = lon * d, ring = [];
  for (let i = 0; i <= steps; i += 1) {
    const brg = (2 * Math.PI * i) / steps;
    const lat2 = Math.asin(Math.sin(la) * Math.cos(ang)
      + Math.cos(la) * Math.sin(ang) * Math.cos(brg));
    const lon2 = lo + Math.atan2(Math.sin(brg) * Math.sin(ang) * Math.cos(la),
      Math.cos(ang) - Math.sin(la) * Math.sin(lat2));
    ring.push([lon2 / d, lat2 / d]);
  }
  return ring;
}

/** Buildings as the server sends them, as GeoJSON features. */
export function footprintFeatures(buildings) {
  const out = [];
  for (const b of buildings) {
    if (!b?.polygon?.length) continue;
    out.push({ type: "Feature", id: b.id,
               properties: { name: b.name || "", source: b.source || "",
                             type: b.type || "" },
               geometry: { type: "Polygon", coordinates: b.polygon } });
  }
  return out;
}

let loading = null;
function loadMapLibre() {
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      if (!document.querySelector(`link[href="${MAPLIBRE_CSS}"]`)) {
        const css = document.createElement("link");
        css.rel = "stylesheet";
        css.href = MAPLIBRE_CSS;
        document.head.appendChild(css);
      }
      const s = document.createElement("script");
      s.src = MAPLIBRE_JS;
      s.async = true;
      s.onload = () => (window.maplibregl ? resolve(window.maplibregl)
        : reject(new Error("MapLibre loaded but did not register")));
      s.onerror = () => { loading = null; reject(new Error("MapLibre could not be loaded")); };
      document.head.appendChild(s);
    });
  }
  return loading;
}

const EMPTY = { type: "FeatureCollection", features: [] };
// MapLibre fetches tiles in workers started from blob: URLs, where a path
// like "/api/..." has nothing to resolve against: every URL is absolute.
const origin = () => window.location.origin;

export class Map2D {
  /**
   * @param {HTMLElement} host   element to fill (positioned, full-size)
   * @param {{onClose?: (view: {lat:number, lon:number, zoom:number,
   *          viewportPx:number}) => void}} opts
   */
  constructor(host, opts = {}) {
    this.host = host;
    this.onClose = opts.onClose || null;
    this.map = null;
    this.target = null;
    this.imagery = [];
    this.imageryKey = null;
    this.statusAt = null;
    this.streetsAttribution = "";
    this.footprints = new Map();
    this.loadedAreas = [];
    this.loadingFootprints = false;
    this._build();
  }

  get isOpen() { return !this.host.hidden; }

  _build() {
    this.host.innerHTML = `
      <div class="m2d-canvas"></div>
      <div class="m2d-top">
        <div class="m2d-title">
          <span class="m2d-eyebrow">Map</span>
          <h2 class="m2d-place">—</h2>
          <p class="m2d-sub mono"></p>
        </div>
        <div class="m2d-tools" role="toolbar" aria-label="Map controls">
          <label class="m2d-imagery">Imagery
            <select data-act="imagery" aria-label="Imagery source"></select></label>
          <label class="m2d-check"><input type="checkbox" data-act="labels" checked> Roads &amp; names</label>
          <label class="m2d-check"><input type="checkbox" data-act="footprints" checked> Buildings</label>
          <button type="button" data-act="key" title="Use your own imagery key for building-level imagery worldwide">Imagery key…</button>
          <button type="button" data-act="back" class="m2d-back">Back to globe</button>
        </div>
      </div>
      <p class="m2d-status" aria-live="polite"></p>
      <p class="m2d-cursor mono" aria-hidden="true"></p>
      <div class="m2d-dialog" hidden role="dialog" aria-label="Imagery key">
        <form class="m2d-card">
          <h3>Building-level imagery, worldwide</h3>
          <p>Without a key, Mapify shows the open imagery: aerial photographs where a
             country publishes them (France, the Netherlands, Switzerland, Spain, Japan,
             the United States) and 30 m Landsat elsewhere -- districts, not buildings.</p>
          <p>With a free key of your own, the map shows sub-metre satellite imagery
             anywhere. Create one at <span class="mono">location.arcgis.com</span>
             (ArcGIS Location Platform: an API key with the basemaps privilege; free
             up to 2,000,000 tiles a month) or at <span class="mono">mapbox.com</span>
             (an access token). The key stays in your config file on this computer;
             tiles are fetched by Mapify and not stored.</p>
          <label>Provider
            <select name="provider">
              <option value="esri">Esri World Imagery (ArcGIS API key)</option>
              <option value="mapbox">Mapbox Satellite (access token)</option>
            </select></label>
          <label>Key <input name="key" type="password" autocomplete="off" spellcheck="false"></label>
          <p class="m2d-keystate"></p>
          <div class="m2d-buttons">
            <button type="submit">Save</button>
            <button type="button" data-act="key-remove">Remove key</button>
            <button type="button" data-act="key-close">Close</button>
          </div>
        </form>
      </div>`;
    const q = (s) => this.host.querySelector(s);
    this.el = {
      canvas: q(".m2d-canvas"), place: q(".m2d-place"), sub: q(".m2d-sub"),
      imagery: q('[data-act="imagery"]'), labels: q('[data-act="labels"]'),
      footprints: q('[data-act="footprints"]'), status: q(".m2d-status"),
      cursor: q(".m2d-cursor"), dialog: q(".m2d-dialog"), form: q(".m2d-card"),
      keystate: q(".m2d-keystate"),
    };
    q('[data-act="back"]').addEventListener("click", () => this.close(true));
    q('[data-act="key"]').addEventListener("click", () => this._openKeyDialog());
    q('[data-act="key-close"]').addEventListener("click", () => { this.el.dialog.hidden = true; });
    q('[data-act="key-remove"]').addEventListener("click", () => this._saveKey(""));
    this.el.form.addEventListener("submit", (e) => {
      e.preventDefault();
      this._saveKey(this.el.form.elements.key.value.trim());
    });
    this.el.imagery.addEventListener("change", () => this._useImagery(this.el.imagery.value));
    this.el.labels.addEventListener("change", () => this._setLabels(this.el.labels.checked));
    this.el.footprints.addEventListener("change", () => this._setFootprints(this.el.footprints.checked));
  }

  /**
   * Open over a location.
   *
   * @param {{lat:number, lon:number, label?:string, uncertaintyKm?:number,
   *          precisionKm?:number, origin?:string,
   *          matches?:{lat:number, lon:number, label:string}[]}} target
   * @param {{fromAltitudeKm?:number, viewportPx?:number}} how
   *   From the globe: start at the scale the globe was showing.
   *   Otherwise: as close as the target's own uncertainty allows.
   */
  async open(target, how = {}) {
    this.target = target;
    this.host.hidden = false;
    this.el.place.textContent = target.label || "Location";
    this.el.sub.textContent = `${target.lat.toFixed(6)}, ${target.lon.toFixed(6)}`;
    this._status("Loading the map…");
    let maplibregl;
    try {
      maplibregl = await loadMapLibre();
    } catch (err) {
      this._status(`The map could not start: ${err.message}`, true);
      return;
    }
    const px = how.viewportPx || this.host.clientHeight || 800;
    let zoom;
    if (how.fromAltitudeKm) {
      // A camera h km up with a ~45° field of view sees about 0.83 h of
      // ground from top to bottom: start at that scale, as the globe was.
      zoom = zoomForSpan(how.fromAltitudeKm * 1000 * 0.83, target.lat, px);
    } else if (target.precisionKm) {
      zoom = zoomForSpan(Math.max(250, target.precisionKm * 4000), target.lat, px);
    } else if (target.uncertaintyKm) {
      zoom = zoomForSpan(Math.max(600, target.uncertaintyKm * 2600), target.lat, px);
    } else {
      zoom = 16;
    }
    zoom = Math.max(ZOOM_HANDBACK + 0.6, Math.min(19, zoom));

    await this._loadStatus(target.lat, target.lon);
    if (!this.map) {
      this._create(maplibregl, target, zoom);
    } else {
      this.map.resize();
      this.map.jumpTo({ center: [target.lon, target.lat], zoom });
      this._drawTarget();
    }
  }

  close(handBack = true) {
    this.host.hidden = true;
    this.el.dialog.hidden = true;
    if (handBack && this.map) {
      const c = this.map.getCenter();
      this.onClose?.({ lat: c.lat, lon: c.lng, zoom: this.map.getZoom(),
                       viewportPx: this.host.clientHeight || 800 });
    }
  }

  /** A new target (a different candidate, a pinpointed spot) while open. */
  setTarget(target) {
    this.target = target;
    if (!target) return;
    this.el.place.textContent = target.label || "Location";
    this.el.sub.textContent = `${target.lat.toFixed(6)}, ${target.lon.toFixed(6)}`;
    this._drawTarget();
  }

  /* ───────────────────────────── the map ───────────────────────────── */

  _style() {
    const src = this._source();
    return {
      version: 8,
      glyphs: `${origin()}/api/fonts/{fontstack}/{range}.pbf`,
      sources: {
        imagery: this._imagerySource(src),
        streets: { type: "vector", tiles: [`${origin()}/api/streets/{z}/{x}/{y}`], maxzoom: 14,
                   attribution: this.streetsAttribution },
        footprints: { type: "geojson", data: EMPTY,
                      attribution: "Buildings: OpenStreetMap; Google Open Buildings (CC BY 4.0)" },
        target: { type: "geojson", data: EMPTY },
        matches: { type: "geojson", data: EMPTY },
      },
      layers: [
        { id: "background", type: "background", paint: { "background-color": "#0b1018" } },
        { id: "imagery", type: "raster", source: "imagery",
          paint: { "raster-fade-duration": 0 } },
        { id: "boundaries", type: "line", source: "streets", "source-layer": "boundary",
          filter: ["<=", ["get", "admin_level"], 4],
          paint: { "line-color": "rgba(255,240,200,0.75)",
                   "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.6, 12, 1.6],
                   "line-dasharray": [3, 2] } },
        { id: "roads", type: "line", source: "streets", "source-layer": "transportation",
          filter: ["match", ["get", "class"],
                   ["motorway", "trunk", "primary", "secondary", "tertiary",
                    "minor", "service", "track", "path", "rail"], true, false],
          minzoom: 6,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": ["match", ["get", "class"],
              ["motorway", "trunk"], "rgba(255,206,92,0.85)",
              ["primary", "secondary"], "rgba(255,236,170,0.8)",
              "rail", "rgba(220,220,230,0.6)",
              "rgba(255,255,255,0.55)"],
            "line-width": ["interpolate", ["exponential", 1.6], ["zoom"],
              8, ["match", ["get", "class"], ["motorway", "trunk"], 1.2, 0.4],
              14, ["match", ["get", "class"], ["motorway", "trunk"], 3, ["primary", "secondary"], 2.2, 1],
              18, ["match", ["get", "class"], ["motorway", "trunk"], 9, ["primary", "secondary"], 7, 3]],
          } },
        { id: "footprints-fill", type: "fill", source: "footprints",
          minzoom: FOOTPRINTS_MIN_ZOOM,
          paint: { "fill-color": "rgba(255,255,255,0.06)" } },
        { id: "footprints-line", type: "line", source: "footprints",
          minzoom: FOOTPRINTS_MIN_ZOOM,
          paint: { "line-color": ["match", ["get", "source"], "gob",
                                  "rgba(160,220,255,0.85)", "rgba(255,255,255,0.85)"],
                   "line-width": ["interpolate", ["linear"], ["zoom"], 15, 0.5, 19, 1.4] } },
        { id: "road-names", type: "symbol", source: "streets",
          "source-layer": "transportation_name", minzoom: 13,
          layout: { "text-field": NAME, "text-font": FONT,
                    "symbol-placement": "line", "text-size": 12 },
          paint: { "text-color": "#ffffff", "text-halo-color": "rgba(0,0,0,0.85)",
                   "text-halo-width": 1.4 } },
        { id: "water-names", type: "symbol", source: "streets", "source-layer": "water_name",
          layout: { "text-field": NAME, "text-font": FONT, "text-size": 12 },
          paint: { "text-color": "#cfe8ff", "text-halo-color": "rgba(0,0,0,0.8)",
                   "text-halo-width": 1.2 } },
        { id: "pois", type: "symbol", source: "streets", "source-layer": "poi",
          minzoom: 16, filter: ["<=", ["get", "rank"], 20],
          layout: { "text-field": NAME, "text-font": FONT, "text-size": 11,
                    "text-max-width": 9 },
          paint: { "text-color": "#fff6e0", "text-halo-color": "rgba(0,0,0,0.85)",
                   "text-halo-width": 1.2 } },
        { id: "places", type: "symbol", source: "streets", "source-layer": "place",
          layout: {
            "text-field": NAME,
            "text-font": ["match", ["get", "class"], ["country", "state", "city"],
                          ["literal", FONT_BOLD], ["literal", FONT]],
            "text-size": ["match", ["get", "class"], "country", 15, "state", 13, "city", 15,
                          "town", 13, 12],
            "text-max-width": 8,
          },
          paint: { "text-color": "#ffffff", "text-halo-color": "rgba(0,0,0,0.9)",
                   "text-halo-width": 1.6 } },
        { id: "target-area", type: "fill", source: "target",
          filter: ["==", ["get", "kind"], "area"],
          paint: { "fill-color": "rgba(90,184,240,0.12)" } },
        { id: "target-edge", type: "line", source: "target",
          filter: ["==", ["get", "kind"], "area"],
          paint: { "line-color": "rgba(90,184,240,0.95)", "line-width": 1.6,
                   "line-dasharray": [2, 1.5] } },
        { id: "matches", type: "circle", source: "matches",
          paint: { "circle-radius": 5, "circle-color": "#f0a03c",
                   "circle-stroke-color": "#1a1206", "circle-stroke-width": 1.5 } },
        { id: "target-pin", type: "circle", source: "target",
          filter: ["==", ["get", "kind"], "pin"],
          paint: { "circle-radius": 7, "circle-color": "#ff4d4d",
                   "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5 } },
      ],
    };
  }

  _create(maplibregl, target, zoom) {
    this.map = new maplibregl.Map({
      container: this.el.canvas,
      style: this._style(),
      center: [target.lon, target.lat],
      zoom,
      minZoom: ZOOM_HANDBACK - 0.4,
      maxZoom: 21,
      maxPitch: 0,
      dragRotate: false,
      pitchWithRotate: false,
      attributionControl: { compact: true },
      fadeDuration: 0,
    });
    this.map.touchZoomRotate.disableRotation();
    this.map.keyboard.disableRotation();
    this.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    this.map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    this.map.on("load", () => {
      this._drawTarget();
      this._afterMove();
    });
    this.map.on("moveend", () => this._afterMove());
    this.map.on("zoomend", () => {
      if (this.isOpen && this.map.getZoom() < ZOOM_HANDBACK) this.close(true);
    });
    this.map.on("mousemove", (e) => {
      this.el.cursor.textContent = `${e.lngLat.lat.toFixed(6)}, ${e.lngLat.lng.toFixed(6)}`;
    });
    this.map.on("click", "footprints-fill", (e) => {
      const f = e.features?.[0];
      if (!f) return;
      const what = f.properties.source === "gob" ? "Google Open Buildings footprint"
        : "OpenStreetMap building";
      this._status(`${f.properties.name || what}${f.properties.name ? ` (${what})` : ""}`);
    });
  }

  _source() {
    return this.imagery.find((s) => s.key === this.imageryKey) || this.imagery[0] || null;
  }

  _imagerySource(src) {
    if (!src) return { type: "raster", tiles: [`${origin()}/api/tiles/weld/{z}/{x}/{y}`], tileSize: 256, maxzoom: 12 };
    return { type: "raster", tiles: [`${origin()}/api/tiles/${src.key}/{z}/{x}/{y}`], tileSize: 256,
             maxzoom: src.max_zoom, attribution: esc(src.attribution) };
  }

  async _loadStatus(lat, lon) {
    try {
      const r = await fetch(`/api/geodata/status?lat=${lat}&lon=${lon}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      this.statusAt = [lon, lat];
      this.streetsAttribution = j.streets_attribution || "";
      const list = j.imagery || [];
      const had = this.imageryKey;
      this.imagery = list;
      // Keep the choice while it is still on offer here; else the best.
      if (!list.some((s) => s.key === had)) this.imageryKey = list[0]?.key || null;
      this.el.imagery.innerHTML = list.map((s) => {
        const res = s.metres_per_pixel_equator < 1 ? `${Math.round(s.metres_per_pixel_equator * 100)} cm`
          : `${Math.round(s.metres_per_pixel_equator)} m`;
        return `<option value="${esc(s.key)}">${esc(s.name)} (~${res})</option>`;
      }).join("");
      this.el.imagery.value = this.imageryKey || "";
      if (this.map && had !== this.imageryKey) this._useImagery(this.imageryKey);
    } catch (err) {
      this._status(`Imagery choices could not be loaded: ${err.message}`, true);
    }
  }

  _useImagery(key) {
    this.imageryKey = key;
    if (!this.map) return;
    const src = this._source();
    if (this.map.getLayer("imagery")) this.map.removeLayer("imagery");
    if (this.map.getSource("imagery")) this.map.removeSource("imagery");
    this.map.addSource("imagery", this._imagerySource(src));
    this.map.addLayer({ id: "imagery", type: "raster", source: "imagery",
                        paint: { "raster-fade-duration": 0 } }, "boundaries");
    this._describeImagery();
  }

  _describeImagery() {
    const src = this._source();
    if (!src || !this.map) return;
    const zoom = this.map.getZoom();
    let note = `Imagery: ${src.name}. ${src.note || ""}`;
    if (zoom > src.max_zoom + 1) {
      note += " You are closer than this imagery resolves: it is enlarged, not sharper.";
    }
    if (!src.keyed && !src.shows_buildings) {
      note += " For building-level imagery here, add an imagery key.";
    }
    this._status(note.trim());
  }

  _setLabels(on) {
    if (!this.map) return;
    for (const id of ["roads", "boundaries", "road-names", "water-names", "pois", "places"]) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
    }
  }

  _setFootprints(on) {
    if (!this.map) return;
    for (const id of ["footprints-fill", "footprints-line"]) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
    }
    if (on) this._afterMove();
  }

  _drawTarget() {
    if (!this.map || !this.map.getSource("target")) return;
    const t = this.target;
    if (!t) {
      this.map.getSource("target").setData(EMPTY);
      this.map.getSource("matches").setData(EMPTY);
      return;
    }
    const features = [{ type: "Feature", properties: { kind: "pin" },
                        geometry: { type: "Point", coordinates: [t.lon, t.lat] } }];
    const radiusKm = t.precisionKm || t.uncertaintyKm;
    if (radiusKm && radiusKm > 0.01) {
      features.unshift({ type: "Feature", properties: { kind: "area" },
                         geometry: { type: "Polygon",
                                     coordinates: [circleRing(t.lon, t.lat, radiusKm * 1000)] } });
    }
    this.map.getSource("target").setData({ type: "FeatureCollection", features });
    this.map.getSource("matches").setData({ type: "FeatureCollection",
      features: (t.matches || []).map((m) => ({ type: "Feature", properties: { label: m.label },
        geometry: { type: "Point", coordinates: [m.lon, m.lat] } })) });
  }

  async _afterMove() {
    if (!this.map || !this.isOpen) return;
    const c = this.map.getCenter();
    // Far from where the imagery list was made: the national sources on
    // offer change at borders.
    if (this.statusAt && metresBetween(this.statusAt, [c.lng, c.lat]) > 50000) {
      await this._loadStatus(c.lat, c.lng);
    }
    this._describeImagery();
    if (this.el.footprints.checked && this.map.getZoom() >= FOOTPRINTS_MIN_ZOOM) {
      this._loadFootprints();
    }
  }

  async _loadFootprints() {
    if (this.loadingFootprints) { this._footprintsPending = true; return; }
    const c = this.map.getCenter();
    const center = [c.lng, c.lat];
    const b = this.map.getBounds();
    const radius = Math.min(2500, Math.max(300,
      metresBetween([b.getWest(), b.getSouth()], [b.getEast(), b.getNorth()]) / 2));
    if (this.loadedAreas.some((a) => metresBetween(a.center, center) + radius * 0.6 <= a.radius)) return;
    this.loadingFootprints = true;
    try {
      const r = await fetch(`/api/geodata/around?lat=${c.lat}&lon=${c.lng}&radius=${Math.round(radius)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const around = await r.json();
      for (const f of footprintFeatures(around.buildings || [])) this.footprints.set(f.id, f);
      if (this.footprints.size > MAX_FOOTPRINTS) {
        const far = [...this.footprints.values()]
          .map((f) => [metresBetween(center, f.geometry.coordinates[0][0]), f.id])
          .sort((x, y) => x[0] - y[0]).slice(MAX_FOOTPRINTS);
        for (const [, id] of far) this.footprints.delete(id);
        this.loadedAreas = [];
      }
      this.loadedAreas.push({ center, radius });
      this.map.getSource("footprints")?.setData({ type: "FeatureCollection",
                                                   features: [...this.footprints.values()] });
      if (!(around.buildings || []).length) {
        this._status("No building footprints are recorded here in OpenStreetMap or Google Open Buildings.");
      }
    } catch (err) {
      this._status(`Building footprints could not be loaded: ${err.message}`, true);
    } finally {
      this.loadingFootprints = false;
      if (this._footprintsPending) { this._footprintsPending = false; this._loadFootprints(); }
    }
  }

  /* ───────────────────────────── the key ───────────────────────────── */

  async _openKeyDialog() {
    this.el.dialog.hidden = false;
    this.el.form.elements.key.value = "";
    try {
      const j = await (await fetch("/api/settings/imagery")).json();
      const have = [j.esri && "Esri", j.mapbox && "Mapbox"].filter(Boolean);
      this.el.keystate.textContent = have.length
        ? `A key is set for ${have.join(" and ")}.` : "No key is set: open imagery only.";
    } catch {
      this.el.keystate.textContent = "";
    }
  }

  async _saveKey(key) {
    const provider = this.el.form.elements.provider.value;
    try {
      const r = await fetch("/api/settings/imagery", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, key }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      this.el.form.elements.key.value = "";
      this.el.keystate.textContent = key ? "Saved. The map now uses it." : "Removed.";
      const c = this.map ? this.map.getCenter() : { lat: this.target?.lat, lng: this.target?.lon };
      this.imageryKey = null;
      await this._loadStatus(c.lat, c.lng);
      this._useImagery(this.imageryKey);
    } catch (err) {
      this.el.keystate.textContent = `Not saved: ${err.message}`;
    }
  }

  _status(text, isError = false) {
    this.el.status.textContent = text;
    this.el.status.classList.toggle("error", isError);
  }
}
