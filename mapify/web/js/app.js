/* Mapify — application controller.
 *
 * Owns the toolbar, the two rails, the progress card and the globe's
 * relationship to the analysis. No framework: the whole front end is four
 * modules and a stylesheet, which matters for a tool whose central claim is
 * that you can read it and see exactly what it does.
 */

import { Globe } from "./globe.js";
import { drawLocator, loadCountries } from "./locator.js";

const $ = (id) => document.getElementById(id);

const el = {
  openBtn: $("open-btn"),
  saveBtn: $("save-btn"),
  fileInput: $("file-input"),
  globeMode: $("globe-mode"),
  lightMode: $("light-mode"),
  flightMode: $("flight-mode"),
  canvas: $("globe-canvas"),
  dropzone: $("dropzone"),
  progress: $("progress"),
  progressStage: $("progress-stage"),
  progressCount: $("progress-count"),
  progressBar: $("progress-bar"),
  steps: $("steps"),
  candidateList: $("candidate-list"),
  candidateCount: $("candidate-count"),
  report: $("report"),
  verdict: $("verdict"),
  hudCoords: $("hud-coords"),
  hudAlt: $("hud-alt"),
  hudSun: $("hud-sun"),
  hudImagery: $("hud-imagery"),
  hudState: $("hud-state"),
  credit: $("credit"),
  engineStatus: $("engine-status"),
  privacyStatus: $("privacy-status"),
  attribution: $("attribution"),
  sourceFoot: $("source-foot"),
  sourceThumb: $("source-thumb"),
  sourceName: $("source-name"),
  sourceDims: $("source-dims"),
};

const STAGES = [
  ["decode", "Decode and normalise"],
  ["exif", "Read embedded metadata"],
  ["locate", "Estimate coordinates"],
  ["evidence", "Read visual clues"],
  ["ocr", "Read signage text"],
  ["fuse", "Weigh the evidence"],
  ["enrich", "Name the location"],
];

const CONFIDENCE = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
  insufficient: "Insufficient evidence",
};

// What each band was measured to do, over the 163-sample benchmark. Shown on
// hover so the wording on screen can be checked against the evidence for it
// rather than taken on trust. See PHASES/Implemented Phases/PHASE-13.
const CONFIDENCE_MEASURED = {
  high: "Measured over 163 samples: all 20 high-confidence calls landed "
      + "within 106 km of the true location.",
  medium: "Measured over 163 samples: 89% of medium calls landed within "
        + "1500 km, median 16 km. A region to check, not a location.",
  low: "Measured over 163 samples: 63% within 1500 km, median 658 km. "
     + "A lead, nothing more.",
  insufficient: "Measured over 163 samples: only 16% within 1500 km. "
              + "The evidence does not support naming a location.",
};

const state = {
  globe: null,
  report: null,
  selected: -1,
  busy: false,
  countries: null,
  status: null,
  native: null,
  previewUrl: null,
};

/* ─────────────────────────── formatting ─────────────────────────── */

function esc(text) {
  const d = document.createElement("div");
  d.textContent = text == null ? "" : String(text);
  return d.innerHTML;
}

function dms(value, pos, neg) {
  const hemi = value >= 0 ? pos : neg;
  const a = Math.abs(value);
  const d = Math.floor(a);
  const m = Math.floor((a - d) * 60);
  const s = ((a - d) * 60 - m) * 60;
  return `${d}°${String(m).padStart(2, "0")}′${s.toFixed(1).padStart(4, "0")}″${hemi}`;
}

const fmtLatLon = (lat, lon) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`;

function fmtKm(km) {
  if (km == null || !isFinite(km)) return "—";
  if (km < 1) return `${Math.round(km * 1000)} m`;
  if (km < 10) return `${km.toFixed(1)} km`;
  if (km < 10000) return `${Math.round(km)} km`;
  return `${(km / 1000).toFixed(1)}k km`;
}

/* ───────────────────────────── boot ───────────────────────────── */

function whenNative(timeoutMs = 4000) {
  if (window.pywebview?.api) return Promise.resolve(window.pywebview.api);
  return new Promise((resolve) => {
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      resolve(window.pywebview?.api || null);
    };
    window.addEventListener("pywebviewready", done, { once: true });
    setTimeout(done, timeoutMs);
  });
}

async function boot() {
  state.globe = new Globe(el.canvas, {
    mode: el.globeMode.value,
    flight: el.flightMode.value,
    lighting: el.lightMode.value,
  });
  state.globe.onHudUpdate = onHud;

  loadCountries().then((geo) => {
    state.countries = geo;
    if (state.report) renderCandidates(state.report);
  });

  state.native = await whenNative();
  document.body.classList.toggle("is-native", Boolean(state.native));

  el.openBtn.addEventListener("click", openImage);
  el.saveBtn.addEventListener("click", saveReport);
  el.fileInput.addEventListener("change", () => {
    const file = el.fileInput.files?.[0];
    if (file) analyse({ file });
    el.fileInput.value = "";
  });

  el.globeMode.addEventListener("change", async () => {
    setHudState("Switching globe…");
    await state.globe.setMode(el.globeMode.value);
    updateCredit();
    setHudState(state.report ? "Result displayed" : "Idle");
  });
  el.lightMode.addEventListener("change", () =>
    state.globe.setLighting(el.lightMode.value)
  );
  el.flightMode.addEventListener("change", () =>
    state.globe.setFlightMode(el.flightMode.value)
  );

  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === "o") { e.preventDefault(); openImage(); }
    if (e.ctrlKey && e.key.toLowerCase() === "s") { e.preventDefault(); saveReport(); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const n = state.report?.candidates?.length || 0;
      if (!n) return;
      e.preventDefault();
      const next = (state.selected + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
      selectCandidate(next, { fly: true });
    }
  });

  installDropzone();
  await refreshStatus();

  const tiles = state.status?.client;
  if (tiles?.basemap_tiles_enabled && tiles.basemap_tile_url) {
    state.globe.setTileSource(tiles.basemap_tile_url, tiles.basemap_attribution);
  }
  updateCredit();

  // Load the models now so the first analysis is not the slow one.
  fetch("/api/warmup", { method: "POST" })
    .then((r) => r.json())
    .then(() => refreshStatus())
    .catch(() => {});
}

function onHud(lat, lon, altKm, flying, sun, imagery) {
  el.hudCoords.textContent = fmtLatLon(lat, lon);
  el.hudAlt.textContent = fmtKm(altKm);
  if (sun && sun.lat != null) {
    el.hudSun.textContent =
      `${sun.lat >= 0 ? "N" : "S"}${Math.abs(sun.lat).toFixed(1)} ` +
      `${sun.lon >= 0 ? "E" : "W"}${Math.abs(sun.lon).toFixed(1)}`;
  } else {
    el.hudSun.textContent = "camera";
  }
  if (imagery) {
    const scale = imagery.kmPerTexel
      ? `${imagery.kmPerTexel.toFixed(1)} km/px` : "";
    el.hudImagery.textContent = `${imagery.label || "—"}  ${scale}`;
    el.hudImagery.classList.toggle("over", Boolean(imagery.beyond));
    el.hudImagery.title = imagery.beyond
      ? "Closer than the imagery resolves. Enable ground tiles in config.toml "
        + "for real detail at this altitude."
      : "Resolution of the surface imagery currently loaded.";
  }
  if (flying) setHudState("Approach in progress");
}

function setHudState(text) { el.hudState.textContent = text; }

function installDropzone() {
  let depth = 0;
  const show = (on) => el.dropzone.classList.toggle("visible", on);
  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    depth++;
    if (!state.busy) show(true);
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("dragleave", (e) => {
    e.preventDefault();
    depth = Math.max(0, depth - 1);
    if (depth === 0) show(false);
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    show(false);
    const file = e.dataTransfer?.files?.[0];
    if (file) analyse({ file });
  });
  window.addEventListener("paste", (e) => {
    for (const item of e.clipboardData?.items || []) {
      if (item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) analyse({ file });
        return;
      }
    }
  });
}

async function refreshStatus() {
  try {
    const data = await (await fetch("/api/status")).json();
    state.status = data;
    const e = data.engine || {};
    const ready = e.geoclip_loaded && e.clip_loaded;
    const bits = [
      ready ? "models ready" : "loading models…",
      e.ocr_engine && e.ocr_engine !== "none" ? `OCR ${e.ocr_engine}` : "no OCR",
      String(e.device || "").toUpperCase(),
    ];
    el.engineStatus.innerHTML =
      `<span class="dot ${ready ? "ok" : "warn"}"></span>` +
      esc(bits.join("  ·  "));

    const offline = e.offline_only;
    el.privacyStatus.innerHTML =
      `<span class="dot ${offline ? "ok" : "warn"}"></span>` +
      esc(offline
        ? "offline — nothing leaves this machine"
        : "image analysed locally; place names via OpenStreetMap");
  } catch {
    el.engineStatus.innerHTML =
      '<span class="dot bad"></span>analysis service unreachable';
  }
}

function updateCredit() {
  const parts = [
    el.globeMode.value === "satellite"
      ? "Earth imagery: NASA Earth Observatory — Blue Marble, Black Marble, GEBCO. Public domain."
      : "Coastlines: Natural Earth. Public domain.",
  ];
  const t = state.status?.client;
  if (t?.basemap_tiles_enabled && t.basemap_attribution) {
    parts.push("Ground detail: " + t.basemap_attribution);
  }
  el.credit.textContent = parts.join("   ");
}

/* ─────────────────────────── file in/out ─────────────────────────── */

async function openImage() {
  if (state.busy) return;
  if (!state.native) { el.fileInput.click(); return; }
  let picked;
  try {
    picked = await state.native.open_image();
  } catch (err) {
    fail("Could not open the file dialog", String(err));
    return;
  }
  if (!picked || picked.cancelled) return;
  if (picked.error) { fail("Cannot analyse that file", picked.error); return; }
  analyse({ path: picked.path, name: picked.name, bytes: picked.bytes });
}

async function saveReport() {
  if (!state.report) return;
  const json = JSON.stringify(state.report, null, 2);
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const suggested = `mapify-${stamp}.json`;
  if (!state.native) {
    const blob = new Blob([json], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = suggested;
    a.click();
    URL.revokeObjectURL(a.href);
    return;
  }
  const res = await state.native.save_report(json, suggested);
  if (res?.error) fail("Could not save", res.error);
  else if (res?.path) setHudState(`Saved — ${res.path}`);
}

/* ─────────────────────────── analysis ─────────────────────────── */

async function analyse(source) {
  if (state.busy) return;
  const name = source.name || source.file?.name || "image";
  const typeOk =
    /\.(jpe?g|png|webp)$/i.test(name) ||
    /^image\/(jpeg|png|webp)$/.test(source.file?.type || "");
  if (!typeOk) {
    fail("Unsupported file", `${name} is not a JPG, PNG or WEBP image.`);
    return;
  }

  state.busy = true;
  el.openBtn.disabled = true;
  el.saveBtn.disabled = true;
  el.report.innerHTML = "";
  el.candidateList.innerHTML = "";
  el.candidateCount.textContent = "—";
  el.candidateCount.className = "chip bare";
  el.verdict.textContent = "analysing";
  el.verdict.className = "chip working";
  state.globe.clearMarkers();
  state.selected = -1;
  showProgress(true);
  showSource(source);

  try {
    const resp = source.path
      ? await fetch("/api/analyse/local/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: source.path }),
        })
      : await (() => {
          const form = new FormData();
          form.append("image", source.file, name);
          return fetch("/api/analyse/stream", { method: "POST", body: form });
        })();

    if (!resp.ok && resp.status !== 422) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch {}
      throw new Error(detail);
    }

    const report = await readStream(resp);
    if (!report) throw new Error("The analysis ended without a result.");
    if (!report.ok) { fail("Analysis failed", report.error || "Unknown error."); return; }

    state.report = report;
    render(report);
    el.saveBtn.disabled = false;
  } catch (err) {
    fail("Analysis failed", err.message || String(err));
  } finally {
    showProgress(false);
    state.busy = false;
    el.openBtn.disabled = false;
    refreshStatus();
  }
}

async function readStream(resp) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let report = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      let msg;
      try { msg = JSON.parse(line); } catch { continue; }
      if (msg.type === "progress") markStage(msg.stage, msg.message);
      else if (msg.type === "result") report = msg.report;
    }
  }
  return report;
}

function showSource(source) {
  if (state.previewUrl) {
    URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
  }
  el.sourceFoot.hidden = false;
  el.sourceDims.textContent = source.bytes
    ? `${(source.bytes / 1024).toFixed(0)} KB` : "";
  if (source.file) {
    state.previewUrl = URL.createObjectURL(source.file);
    el.sourceThumb.src = state.previewUrl;
    el.sourceName.textContent = source.file.name;
    el.sourceDims.textContent = `${(source.file.size / 1024).toFixed(0)} KB`;
  } else {
    // The WebView cannot read file:// under this policy, so name it instead
    // of showing a broken thumbnail.
    el.sourceThumb.removeAttribute("src");
    el.sourceName.textContent = source.name || source.path;
  }
}

function showProgress(on) {
  el.progress.hidden = !on;
  if (!on) return;
  el.progressStage.textContent = "Starting";
  el.progressBar.style.width = "0%";
  el.progressCount.textContent = `0/${STAGES.length}`;
  el.steps.innerHTML = STAGES
    .map(([k, label]) => `<li data-stage="${k}">${esc(label)}</li>`)
    .join("");
}

function markStage(stage, message) {
  el.progressStage.textContent = message;
  const items = [...el.steps.querySelectorAll("li")];
  let index = items.findIndex((li) => li.dataset.stage === stage);
  if (stage === "done") index = items.length;
  items.forEach((li, i) => {
    li.className = i < index ? "done" : i === index ? "active" : "";
  });
  const done = Math.max(0, index);
  el.progressBar.style.width = `${(done / STAGES.length) * 100}%`;
  el.progressCount.textContent = `${done}/${STAGES.length}`;
}

function fail(title, detail) {
  el.verdict.textContent = "error";
  el.verdict.className = "chip error";
  el.report.innerHTML =
    `<div class="note risk"><div><strong>${esc(title)}</strong><br>${esc(detail)}</div></div>`;
  setHudState("Idle");
}

/* ─────────────────────────── rendering ─────────────────────────── */

function render(report) {
  const pred = report.prediction || {};
  renderCandidates(report);
  renderReport(report);

  el.verdict.textContent = pred.confidence || "—";
  el.verdict.className = `chip ${pred.confidence || "bare"}`;
  el.verdict.title = CONFIDENCE_MEASURED[pred.confidence] || "";

  state.globe.clearMarkers();
  (report.candidates || []).forEach((c, i) => {
    state.globe.addMarker({
      id: i,
      lat: c.latitude,
      lon: c.longitude,
      label: candidateName(c),
      kind: i === 0 ? "primary" : "candidate",
      radiusKm: i === 0 ? pred.uncertainty_km : 0,
    });
  });
  const gps = pred.embedded_gps;
  if (gps) {
    state.globe.addMarker({
      id: "exif", lat: gps.latitude, lon: gps.longitude,
      label: "EXIF claim", kind: "exif",
    });
  }

  if (report.candidates?.length) selectCandidate(0, { fly: true });
  else if (gps) {
    state.globe.setActiveMarker("exif");
    state.globe.flyTo(gps.latitude, gps.longitude, { altitude: 1.5 });
  }
}

function candidateName(c) {
  const loc = c.location || {};
  const nom = c.nominatim || {};
  return (
    nom.city || loc.nearest_places?.[0]?.name || nom.state ||
    loc.admin1?.name || loc.country?.name || nom.country || "Unnamed"
  );
}

function candidateRegion(c) {
  const loc = c.location || {};
  const nom = c.nominatim || {};
  const parts = [nom.state || loc.admin1?.name, nom.country || loc.country?.name];
  return parts.filter(Boolean).join(", ") || (loc.offshore ? "Offshore" : "—");
}

function renderCandidates(report) {
  const list = report.candidates || [];
  const pred = report.prediction || {};
  const gps = pred.embedded_gps;

  el.candidateCount.textContent = String(list.length);
  el.candidateList.innerHTML = "";

  if (!list.length && !gps) {
    el.candidateList.innerHTML =
      `<div class="empty"><p><strong>No candidates</strong>
       The locator produced nothing for this image.</p></div>`;
    return;
  }

  list.forEach((c, i) => {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "candidate" + (i === state.selected ? " active" : "");
    node.dataset.index = String(i);

    const canvas = document.createElement("canvas");
    canvas.className = "candidate-thumb";
    drawLocator(canvas, c.latitude, c.longitude, state.countries, {
      accent: i === 0 ? "#5ab8f0" : "#7fb4d6",
      size: 56,
    });

    const pct = Math.round((c.probability || 0) * 100);
    const adj = c.total_adjustment || 0;
    const via = c.provenance && c.provenance !== "model"
      ? `<span class="tagline">${esc(c.provenance)}</span>` : "";

    const main = document.createElement("div");
    main.className = "candidate-main";
    main.innerHTML = `
      <span class="candidate-rank">${String(i + 1).padStart(2, "0")}${via}</span>
      <span class="candidate-name">${esc(candidateName(c))}</span>
      <span class="candidate-region">${esc(candidateRegion(c))}</span>
      <span class="candidate-coords">${esc(fmtLatLon(c.latitude, c.longitude))}</span>
      <span class="candidate-foot">
        <span class="meter"><i style="width:${pct}%"></i></span>
        <span class="candidate-pct">${pct}%</span>
        <span class="candidate-pct" style="color:${
          adj >= 0 ? "var(--ok)" : "var(--risk)"}">${
          adj >= 0 ? "+" : ""}${adj.toFixed(2)}</span>
      </span>`;

    node.append(canvas, main);
    node.addEventListener("click", () => selectCandidate(i, { fly: true }));
    el.candidateList.appendChild(node);
  });

  if (gps) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "candidate is-exif";
    const canvas = document.createElement("canvas");
    canvas.className = "candidate-thumb";
    drawLocator(canvas, gps.latitude, gps.longitude, state.countries, {
      accent: "#a78bfa", size: 56,
    });
    const place = gps.place || {};
    const main = document.createElement("div");
    main.className = "candidate-main";
    main.innerHTML = `
      <span class="candidate-rank">EXIF<span class="tagline"
        style="color:var(--exif)">file claim</span></span>
      <span class="candidate-name">${esc(
        place.nearest_places?.[0]?.name || place.country?.name || "GPS tag")}</span>
      <span class="candidate-region">${esc(place.country?.name || "—")}</span>
      <span class="candidate-coords">${esc(
        fmtLatLon(gps.latitude, gps.longitude))}</span>
      <span class="candidate-foot">
        <span class="candidate-pct">not an estimate — a tag in the file</span>
      </span>`;
    node.append(canvas, main);
    node.addEventListener("click", () => {
      state.globe.setActiveMarker("exif");
      state.globe.flyTo(gps.latitude, gps.longitude, { altitude: 1.5 });
      setHudState("Showing the EXIF GPS claim");
      for (const n of el.candidateList.querySelectorAll(".candidate")) {
        n.classList.remove("active");
      }
      node.classList.add("active");
    });
    el.candidateList.appendChild(node);
  }
}

function selectCandidate(index, { fly = false } = {}) {
  const c = state.report?.candidates?.[index];
  if (!c) return;
  state.selected = index;
  for (const node of el.candidateList.querySelectorAll(".candidate")) {
    node.classList.toggle("active", node.dataset.index === String(index));
  }
  state.globe.setActiveMarker(index);

  if (fly) {
    const name = candidateName(c);
    setHudState(`Approaching ${name}`);
    const pred = state.report?.prediction || {};
    const unc = pred.uncertainty_km || 0;
    // Ask to come in as close as the uncertainty justifies; the globe will
    // refuse to go below what the imagery can actually show.
    const altitude = pred.confidence === "insufficient"
      ? 2.6
      : Math.max(1.06, Math.min(2.4, 1.05 + unc / 700));
    state.globe.flyTo(c.latitude, c.longitude, { altitude })
      .then(() => setHudState(name));
  }
}

/* ────────────────────────── the report ────────────────────────── */

const block = (title, body) =>
  `<section class="block"><h3 class="block-head">${esc(title)}</h3>${body}</section>`;

function renderReport(report) {
  const pred = report.prediction || {};
  const est = pred.estimated;
  const out = [];

  /* headline ---------------------------------------------------------- */
  if (!pred.determinate || !est) {
    out.push(block("Assessment", `
      <div class="verdict indeterminate">
        <p class="place">No location determined</p>
        <p class="place-sub">${esc(CONFIDENCE[pred.confidence] || pred.confidence)}</p>
        <p class="measured">${esc(
          CONFIDENCE_MEASURED[pred.confidence] || "")}</p>
        <div class="note risk"><div>${esc(pred.confidence_reason || "")}</div></div>
      </div>`));
  } else {
    const place = est.place || {};
    const name =
      [place.city, place.region, place.country].filter(Boolean).join(", ") ||
      place.display_name || "Unnamed location";
    out.push(block("Estimated location", `
      <div class="verdict determinate">
        <p class="place">${esc(name)}</p>
        <p class="place-sub">
          <span class="chip ${esc(pred.confidence)}" title="${esc(
            CONFIDENCE_MEASURED[pred.confidence] || "")}">${esc(
            CONFIDENCE[pred.confidence] || pred.confidence)}</span>
          <span>±${esc(fmtKm(pred.uncertainty_km))}</span>
        </p>
        <p class="measured">${esc(
          CONFIDENCE_MEASURED[pred.confidence] || "")}</p>
        <div class="coord-pair">
          <div class="coord"><span class="k">Latitude</span>
            <span class="v">${est.latitude.toFixed(6)}</span></div>
          <div class="coord"><span class="k">Longitude</span>
            <span class="v">${est.longitude.toFixed(6)}</span></div>
        </div>
        <dl class="rows">
          <div class="row"><dt>DMS</dt><dd class="mono">${esc(
            dms(est.latitude, "N", "S"))} ${esc(dms(est.longitude, "E", "W"))}</dd></div>
          ${row("Road", place.road)}
          ${row("Suburb", place.suburb)}
          ${row("City", place.city)}
          ${row("Region", place.region)}
          ${row("Country", place.country
              ? `${place.country}${place.country_code ? ` (${place.country_code})` : ""}`
              : null)}
          ${row("Postcode", place.postcode, true)}
          ${timezoneRow(report)}
          ${row("Named by", place.source)}
        </dl>
        <div class="note info" style="margin-top:9px"><div>${esc(
          pred.confidence_reason || "")}</div></div>
        ${place.boundary_note
          ? `<div class="note warn"><div>${esc(place.boundary_note)}</div></div>` : ""}
      </div>`));
  }

  for (const note of pred.notes || []) {
    out.push(`<div class="note warn"><div>${esc(note)}</div></div>`);
  }

  /* embedded GPS ------------------------------------------------------ */
  const gps = pred.embedded_gps;
  if (gps) {
    const agree = pred.exif_vs_estimate;
    const place = gps.place || {};
    out.push(block("Embedded GPS — a claim, not proof", `
      <div class="note exif"><div><strong>The file carries a GPS tag.</strong>
        That is what the writer recorded, not what the picture shows, and it is
        trivially editable.</div></div>
      <dl class="rows">
        <div class="row"><dt>Latitude</dt><dd class="mono">${
          gps.latitude.toFixed(6)}</dd></div>
        <div class="row"><dt>Longitude</dt><dd class="mono">${
          gps.longitude.toFixed(6)}</dd></div>
        ${row("Altitude", gps.altitude_m != null ? `${gps.altitude_m} m` : null, true)}
        ${row("GPS time", gps.timestamp_utc, true)}
        ${row("Method", gps.processing_method)}
        ${row("Resolves to", [place.nearest_places?.[0]?.name,
                              place.admin1?.name,
                              place.country?.name].filter(Boolean).join(", "))}
      </dl>
      ${agree ? `
        <div class="note ${agree.consistent ? "ok" : "risk"}" style="margin-top:8px">
          <div><strong>${agree.consistent ? "Consistent" : "Disagreement"}</strong>
          — ${esc(fmtKm(agree.distance_km))} from the image estimate.<br>
          ${esc(agree.note)}</div></div>` : ""}
      ${(gps.trust_warnings || [])
        .map((w) => `<div class="note warn"><div>${esc(w)}</div></div>`).join("")}`));
  }

  /* evidence ---------------------------------------------------------- */
  const readings = (report.evidence?.readings || []);
  const used = readings.filter((r) => r.called);
  const observed = readings.filter((r) => !r.called && r.confidence >= 0.30);
  if (used.length || observed.length) {
    const rows = used.map((r) => `
      <div class="ev">
        <span class="ev-label">${esc(r.label)}</span>
        <span class="ev-value">${Math.round(r.confidence * 100)}%</span>
        <span class="ev-bar"><i style="width:${Math.round(r.confidence * 100)}%"></i></span>
        <span class="ev-detail">${esc(r.title)}${
          r.runners_up?.length
            ? ` · next best: ${esc(r.runners_up[0][0])} ${
                Math.round(r.runners_up[0][1] * 100)}%` : ""}</span>
      </div>`).join("");
    const tail = observed.length
      ? `<p class="ev-detail" style="margin-top:9px">Observed but not used —
         below threshold, or geographically unconstraining:<br>${
           observed.map((r) => `${esc(r.label)} ${
             Math.round(r.confidence * 100)}%`).join(" · ")}</p>`
      : "";
    out.push(block(
      used.length ? `Detected evidence · ${used.length} used` : "Detected evidence",
      (rows || `<p class="ev-detail">No visual clue cleared its threshold, so
        nothing was re-ranked.</p>`) + tail));
  }

  /* scoring ----------------------------------------------------------- */
  const top = (report.candidates || [])[0];
  if (top?.adjustments?.length) {
    const rows = top.adjustments
      .slice().sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 8)
      .map((a) => `
        <div class="ev">
          <span class="ev-label">${esc(a.source.replace(/_/g, " "))}</span>
          <span class="ev-value ${a.delta >= 0 ? "pos" : "neg"}">${
            a.delta >= 0 ? "+" : ""}${a.delta.toFixed(2)}</span>
          <span class="ev-detail">${esc(a.detail)}</span>
        </div>`).join("");
    const d = pred.diagnostics || {};
    out.push(block("How the leading candidate scored", rows + `
      <div class="tags">
        <span class="tag">locator mass ${((d.locator_mass || 0) * 100).toFixed(1)}%</span>
        <span class="tag">concentration ${Math.round((d.concentration || 0) * 100)}%</span>
        <span class="tag">dispersion ${esc(fmtKm(d.dispersion_km))}</span>
        <span class="tag ${(d.evidence_support || 0) >= 0 ? "pos" : "neg"}">evidence ${
          (d.evidence_support || 0) >= 0 ? "+" : ""}${
          (d.evidence_support || 0).toFixed(2)}</span>
      </div>`));
  }

  /* text -------------------------------------------------------------- */
  const ocr = report.ocr || {};
  if (ocr.line_count) {
    const scripts = ocr.scripts?.proportions || {};
    const tags = Object.entries(scripts).sort((a, b) => b[1] - a[1]).slice(0, 5)
      .map(([s, p]) => `<span class="tag">${esc(s)} ${Math.round(p * 100)}%</span>`)
      .join("");
    out.push(block(`Text read from the image · ${ocr.line_count}`,
      `<div class="ocr">${esc(ocr.text)}</div>` +
      (tags ? `<div class="tags">${tags}</div>` : "")));
  } else if (ocr.error) {
    out.push(block("Text", `<div class="note warn"><div>${esc(ocr.error)}</div></div>`));
  }

  if (report.toponyms?.length) {
    out.push(block("Place names matched in the gazetteer", `<ul class="list">${
      report.toponyms.map((t) => `<li>
        <span class="a">${esc(t.text)} → ${esc(t.matched_name)}${
          t.language ? ` <span class="tag">${esc(t.language)}</span>` : ""}</span>
        <span class="b">${t.corroborated_km != null
          ? esc(fmtKm(t.corroborated_km)) : "—"}</span></li>`).join("")}</ul>`));
  }

  /* surroundings ------------------------------------------------------- */
  const surr = top?.surroundings;
  if (surr?.features?.length) {
    out.push(block("Nearby named features", `<ul class="list">${
      surr.features.slice(0, 10).map((f) => `<li>
        <span class="a">${esc(f.name)}</span>
        <span class="b">${f.distance_m} m ${esc(f.direction)}</span></li>`).join("")
      }</ul>` + (surr.road_refs?.length
        ? `<div class="tags">${surr.road_refs.slice(0, 10)
            .map((r) => `<span class="tag">${esc(r)}</span>`).join("")}</div>` : "")));
  }

  /* file --------------------------------------------------------------- */
  const exif = report.exif || {};
  const img = report.image || {};
  out.push(block("File", `
    <dl class="rows">
      ${row("Name", img.filename)}
      ${row("Format", `${img.format || "—"} · ${img.width}×${img.height}`)}
      ${row("Size", `${((img.bytes || 0) / 1024).toFixed(0)} KB`)}
      ${row("Camera", [exif.camera_make, exif.camera_model].filter(Boolean).join(" "))}
      ${row("Lens", exif.lens)}
      ${row("Captured", exif.captured_at, true)}
      ${row("Software", exif.software)}
      ${row("EXIF", exif.has_exif ? "present" : "absent")}
    </dl>
    ${!gps ? (exif.trust_warnings || [])
      .map((w) => `<div class="note warn" style="margin-top:8px"><div>${
        esc(w)}</div></div>`).join("") : ""}`));

  for (const w of report.warnings || []) {
    out.push(`<div class="note warn"><div>${esc(w)}</div></div>`);
  }

  out.push(`<p class="footnote">Analysed in ${report.elapsed_s}s on this
    machine. The image was not uploaded anywhere.</p>`);

  el.report.innerHTML = out.join("");
  const attrib = (report.attribution || []).join("   ·   ");
  el.attribution.textContent = attrib;
  el.attribution.title = (report.attribution || []).join("\n");
}

function row(label, value, mono = false) {
  if (value == null || value === "" || value === "—") return "";
  return `<div class="row"><dt>${esc(label)}</dt><dd${
    mono ? ' class="mono"' : ""}>${esc(value)}</dd></div>`;
}

function timezoneRow(report) {
  const tz = (report.candidates || [])[0]?.timezone;
  if (!tz) return "";
  if (tz.iana_name) {
    const off = tz.utc_offset_hours != null
      ? ` (UTC${tz.utc_offset_hours >= 0 ? "+" : ""}${tz.utc_offset_hours})` : "";
    const local = tz.local_time ? ` · ${tz.local_time.slice(11, 16)} local` : "";
    return row("Timezone", `${tz.iana_name}${off}${local}`);
  }
  return row("Timezone",
    `estimated UTC${tz.utc_offset_hours >= 0 ? "+" : ""}${tz.utc_offset_hours}`);
}

boot();
