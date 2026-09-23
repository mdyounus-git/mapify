/* Small orthographic globe thumbnails for the candidate list.
 *
 * Each candidate gets a miniature Earth rendered from the same Natural Earth
 * outlines the main globe uses, centred on that candidate with a marker at the
 * middle. Drawn on a canvas at device resolution, so no tiles, no requests,
 * and nothing to load. */

const DEG = Math.PI / 180;

let geojsonPromise = null;

export function loadCountries(url = "data/ne_110m_countries.geojson") {
  if (!geojsonPromise) {
    geojsonPromise = fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }
  return geojsonPromise;
}

/** Orthographic projection. Returns null for points on the far hemisphere. */
function project(lat, lon, lat0, lon0, radius, cx, cy) {
  const p = lat * DEG;
  const p0 = lat0 * DEG;
  const dl = (lon - lon0) * DEG;
  const cosc =
    Math.sin(p0) * Math.sin(p) + Math.cos(p0) * Math.cos(p) * Math.cos(dl);
  if (cosc < 0) return null; // behind the globe
  const x = Math.cos(p) * Math.sin(dl);
  const y = Math.cos(p0) * Math.sin(p) - Math.sin(p0) * Math.cos(p) * Math.cos(dl);
  return [cx + x * radius, cy - y * radius];
}

/**
 * Draw a locator thumbnail.
 * @param {HTMLCanvasElement} canvas
 * @param {number} lat
 * @param {number} lon
 * @param {object|null} geo  GeoJSON FeatureCollection, or null for a bare globe
 * @param {{accent?:string, size?:number}} opts
 */
export function drawLocator(canvas, lat, lon, geo, opts = {}) {
  const accent = opts.accent || "#5ab8f0";
  const size = opts.size || 62;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  canvas.width = size * dpr;
  canvas.height = size * dpr;
  canvas.style.width = size + "px";
  canvas.style.height = size + "px";

  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, size, size);

  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 2;

  // Ocean disc with a little depth.
  const grad = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.1, cx, cy, r);
  grad.addColorStop(0, "#14395c");
  grad.addColorStop(1, "#050d18");
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = grad;
  ctx.fill();

  // Land.
  if (geo && geo.features) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.clip();
    ctx.fillStyle = "rgba(26, 52, 78, 0.95)";
    ctx.strokeStyle = "rgba(128, 190, 235, 0.52)";
    ctx.lineWidth = 0.6;

    const ring = (coords) => {
      let started = false;
      ctx.beginPath();
      for (const [plon, plat] of coords) {
        const pt = project(plat, plon, lat, lon, r, cx, cy);
        if (!pt) {
          started = false;
          continue;
        }
        if (!started) {
          ctx.moveTo(pt[0], pt[1]);
          started = true;
        } else {
          ctx.lineTo(pt[0], pt[1]);
        }
      }
      ctx.fill();
      ctx.stroke();
    };

    for (const f of geo.features) {
      const g = f.geometry;
      if (!g) continue;
      if (g.type === "Polygon") for (const c of g.coordinates) ring(c);
      else if (g.type === "MultiPolygon")
        for (const poly of g.coordinates) for (const c of poly) ring(c);
    }
    ctx.restore();
  }

  // Limb.
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(148, 173, 209, 0.30)";
  ctx.lineWidth = 1;
  ctx.stroke();

  // Marker dead centre, because the projection is centred on it.
  ctx.beginPath();
  ctx.arc(cx, cy, 3.6, 0, Math.PI * 2);
  ctx.fillStyle = accent;
  ctx.fill();
  ctx.beginPath();
  ctx.arc(cx, cy, 6.6, 0, Math.PI * 2);
  ctx.strokeStyle = accent;
  ctx.globalAlpha = 0.6;
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.globalAlpha = 1;
}
