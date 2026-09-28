/* High-resolution ground detail for the landing.
 *
 * The NASA base texture is 4096 px around the equator, which is about 10 km
 * per texel: fine from orbit, useless at the end of an approach. When the
 * operator has enabled basemap tiles, this stitches a small grid of raster
 * tiles around the target and lays it on the globe as a curved patch, so the
 * final descent lands on something real.
 *
 * The default source is NASA's Global Imagery Browse Services, which
 * states its own terms as Fees: none, AccessConstraints: none. NASA
 * imagery is not copyrighted. That is what allows this to be on by
 * default; the previous default was OpenStreetMap's own tile servers,
 * donated capacity whose usage policy a desktop application fetching on
 * every landing would breach, so it had to ship switched off and never
 * ran for anybody.
 *
 * Restraint is kept anyway, because it is somebody's bandwidth either
 * way: one landing fetches at most a 4x4 grid, the result is cached per
 * coordinate, and the patch is only built when the camera is close
 * enough to need it.
 */

import * as THREE from "three";

const DEG = Math.PI / 180;

/** Web Mercator tile index for a coordinate. */
function tileIndex(lat, lon, zoom) {
  const n = 2 ** zoom;
  const x = ((lon + 180) / 360) * n;
  const latRad = lat * DEG;
  const y = ((1 - Math.asinh(Math.tan(latRad)) / Math.PI) / 2) * n;
  return { x: Math.floor(x), y: Math.floor(y), n };
}

const tileLon = (x, n) => (x / n) * 360 - 180;
const tileLat = (y, n) =>
  Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) / DEG;

function loadImage(url) {
  return new Promise((resolve) => {
    const img = new Image();
    // Required, or drawing the tile would taint the canvas and WebGL would
    // refuse to upload it.
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

export class DetailPatch {
  /**
   * @param {THREE.Object3D} parent  the globe root to attach the patch to
   * @param {{tileUrl:string, attribution:string, radius:number}} opts
   */
  constructor(parent, opts) {
    this.parent = parent;
    this.tileUrl = opts.tileUrl;
    this.attribution = opts.attribution || "";
    this.R = opts.radius ?? 1.0;
    this.grid = opts.grid ?? 4;         // grid x grid tiles
    this.mesh = null;
    this.currentZoom = null;
    this.currentLat = 0;
    this.key = null;
    this.busy = false;
    this.onAttribution = null;
  }

  get enabled() {
    return Boolean(this.tileUrl);
  }

  /** Remove the current patch, if any. */
  clear() {
    if (!this.mesh) return;
    this.parent.remove(this.mesh);
    this.mesh.geometry.dispose();
    if (this.mesh.material.map) this.mesh.material.map.dispose();
    this.mesh.material.dispose();
    this.mesh = null;
    this.currentZoom = null;
    this.key = null;
    if (this.onAttribution) this.onAttribution(null);
  }

  /**
   * Build (or reuse) the patch for a coordinate.
   * @param {number} lat
   * @param {number} lon
   * @param {number} zoom  Web Mercator zoom, clamped to a sane range
   */
  async show(lat, lon, zoom) {
    if (!this.enabled || this.busy) return;
    zoom = Math.max(4, Math.min(this.maxZoom ?? 16, Math.round(zoom)));
    const key = `${lat.toFixed(4)}/${lon.toFixed(4)}/${zoom}`;
    if (key === this.key) return;

    this.busy = true;
    try {
      const g = this.grid;
      const { x: cx, y: cy, n } = tileIndex(lat, lon, zoom);
      const half = Math.floor(g / 2);
      const x0 = cx - half;
      const y0 = cy - half;

      const SIZE = 256;
      const canvas = document.createElement("canvas");
      canvas.width = g * SIZE;
      canvas.height = g * SIZE;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#0b1626";
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      const jobs = [];
      for (let dy = 0; dy < g; dy++) {
        for (let dx = 0; dx < g; dx++) {
          const tx = ((x0 + dx) % n + n) % n;   // wrap across the antimeridian
          const ty = y0 + dy;
          if (ty < 0 || ty >= n) continue;
          const url = this.tileUrl
            .replace("{z}", String(zoom))
            .replace("{x}", String(tx))
            .replace("{y}", String(ty));
          jobs.push(
            loadImage(url).then((img) => {
              if (img) ctx.drawImage(img, dx * SIZE, dy * SIZE, SIZE, SIZE);
            })
          );
        }
      }
      const loaded = await Promise.all(jobs);
      if (!loaded.length) return;

      // Geographic extent of the stitched grid.
      const lonMin = tileLon(x0, n);
      const lonMax = tileLon(x0 + g, n);
      const latMax = tileLat(y0, n);
      const latMin = tileLat(y0 + g, n);

      // A sphere patch over exactly that extent. Mercator is not linear in
      // latitude, but over a patch this small (well under a degree at the
      // zooms this is used at) the error is far below a pixel.
      const geo = new THREE.SphereGeometry(
        this.R * 1.0006,
        96, 96,
        (lonMin + 180) * DEG,
        (lonMax - lonMin) * DEG,
        (90 - latMax) * DEG,
        (latMax - latMin) * DEG
      );

      const tex = new THREE.CanvasTexture(canvas);
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = 8;
      tex.needsUpdate = true;

      const mesh = new THREE.Mesh(
        geo,
        new THREE.MeshBasicMaterial({
          map: tex,
          transparent: true,
          opacity: 0,
          depthWrite: false,
          toneMapped: false,
        })
      );
      mesh.renderOrder = 3;

      this.clear();
      this.mesh = mesh;
      this.key = key;
      this.currentZoom = zoom;
      this.currentLat = lat;
      this.parent.add(mesh);
      if (this.onAttribution) this.onAttribution(this.attribution);

      // Fade in, so the swap from base texture to tiles is not a hard cut.
      const t0 = performance.now();
      const fade = () => {
        if (this.mesh !== mesh) return;
        const k = Math.min(1, (performance.now() - t0) / 420);
        mesh.material.opacity = k;
        if (k < 1) requestAnimationFrame(fade);
      };
      requestAnimationFrame(fade);
    } finally {
      this.busy = false;
    }
  }

  /**
   * What the patch is actually showing, for the readout. Null when there
   * is no patch.
   *
   * Otherwise the HUD goes on describing the base texture, and once
   * tiles are up that is no longer what the operator is looking at.
   * Reporting 4.9 km per pixel while displaying 25 would be the one
   * readout whose entire job is honesty about resolution being wrong
   * about resolution.
   */
  activeResolution() {
    if (!this.enabled || !this.mesh || this.currentZoom == null) return null;
    return {
      label: `ground z${this.currentZoom}`,
      kmPerTexel: metresPerPixel(this.currentZoom, this.currentLat || 0) / 1000,
    };
  }
}

/** Pick a tile zoom that roughly matches the camera altitude. */
export function zoomForAltitude(altitudeKm, maxZoom = 19) {
  let zoom;
  if (altitudeKm > 2500) return 0;     // nothing worth loading
  else if (altitudeKm > 1200) zoom = 6;
  else if (altitudeKm > 600) zoom = 8;
  else if (altitudeKm > 250) zoom = 10;
  else if (altitudeKm > 90) zoom = 12;
  else if (altitudeKm > 30) zoom = 14;
  else zoom = 15;
  // Never ask a source for detail it does not have. A grid of 404s reads
  // as a broken renderer rather than as the end of the available detail.
  return Math.min(zoom, maxZoom);
}

/** Ground resolution of a tile zoom, in metres per pixel. */
export function metresPerPixel(zoom, lat = 0) {
  return (156543.03392 * Math.cos(lat * DEG)) / 2 ** zoom;
}
