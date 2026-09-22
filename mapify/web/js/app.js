/* Mapify console.
 *
 * Wires the upload, the streaming analysis, the candidate list, the report and
 * the globe together. No framework, no build step -- the whole front end is
 * three modules and a stylesheet, which matters for a tool whose main claim is
 * that you can read it and see what it does. */

import { Globe } from "./globe.js";
import { drawLocator, loadCountries } from "./locator.js";

const $ = (id) => document.getElementById(id);

const el = {
  uploadBtn: $("upload-btn"),
  fileInput: $("file-input"),
  globeCanvas: $("globe-canvas"),
  globeMode: $("globe-mode"),
  flightMode: $("flight-mode"),
  dropzone: $("dropzone"),
  progress: $("progress"),
  progressStage: $("progress-stage"),
  progressSteps: $("progress-steps"),
  candidateList: $("candidate-list"),
  candidateCount: $("candidate-count"),
  report: $("report"),
  verdictPill: $("verdict-pill"),
  hudCoords: $("hud-coords"),
  hudAltitude: $("hud-altitude"),
  hudStatus: $("hud-status"),
  hudCredit: $("hud-credit"),
  engineStatus: $("engine-status"),
  privacyStatus: $("privacy-status"),
  attribution: $("attribution"),
  sourceThumb: $("source-thumb"),
  sourceThumbWrap: $("source-thumb-wrap"),
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

const state = {
  globe: null,
  report: null,
  selected: -1,
  busy: false,
  countries: null,
  status: null,
};

/* ─────────────────────────── formatting ─────────────────────────── */

function dms(value, positive, negative) {
  const hemi = value >= 0 ? positive : negative;
  const a = Math.abs(value);
  const d = Math.floor(a);
  const m = Math.floor((a - d) * 60);
  const s = ((a - d) * 60 - m) * 60;
  return `${d}°${String(m).padStart(2, "0")}'${s.toFixed(1).padStart(4, "0")}"${hemi}`;
}

const fmtCoord = (lat, lon) => `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
const fmtDms = (lat, lon) => `${dms(lat, "N", "S")}  ${dms(lon, "E", "W")}`;

function fmtKm(km) {
  if (km == null || !isFinite(km)) return "—";
  if (km < 1) return `${Math.round(km * 1000)} m`;
  if (km < 10) return `${km.toFixed(1)} km`;
  return `${Math.round(km).toLocaleString()} km`;
}

function esc(text) {
  const d = document.createElement("div");
  d.textContent = text == null ? "" : String(text);
  return d.innerHTML;
}

const CONFIDENCE_WORDS = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence — lead only",
  insufficient: "Insufficient evidence",
};

/* ───────────────────────────── boot ───────────────────────────── */

async function boot() {
  state.globe = new Globe(el.globeCanvas, {
    mode: el.globeMode.value,
    flight: el.flightMode.value,
  });
  state.globe.onHudUpdate = (lat, lon, altKm, flying) => {
    el.hudCoords.textContent = fmtCoord(lat, lon);
    el.hudAltitude.textContent = `alt ${fmtKm(altKm)}`;
    if (flying) el.hudStatus.textContent = "Approach in progress";
  };

  loadCountries().then((geo) => {
    state.countries = geo;
    if (state.report) renderCandidates(state.report);
  });

  el.uploadBtn.addEventListener("click", () => el.fileInput.click());
  el.fileInput.addEventListener("change", () => {
    const file = el.fileInput.files?.[0];
    if (file) analyse(file);
    el.fileInput.value = "";
  });

  el.globeMode.addEventListener("change", async () => {
    el.hudStatus.textContent = "Switching globe…";
    await state.globe.setMode(el.globeMode.value);
    updateCredit();
    el.hudStatus.textContent = state.report ? "Result displayed" : "Idle";
  });
  el.flightMode.addEventListener("change", () =>
    state.globe.setFlightMode(el.flightMode.value)
  );

  installDropzone();
  await refreshStatus();
  updateCredit();

  // Kick model loading off now so the first upload is not the slow one.
  fetch("/api/warmup", { method: "POST" })
    .then((r) => r.json())
    .then(() => refreshStatus())
    .catch(() => {});
}

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
    if (file) analyse(file);
  });
  window.addEventListener("paste", (e) => {
    for (const item of e.clipboardData?.items || []) {
      if (item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) analyse(file);
        return;
      }
    }
  });
}

async function refreshStatus() {
  try {
    const resp = await fetch("/api/status");
    const data = await resp.json();
    state.status = data;
    const e = data.engine || {};
    const bits = [];
    bits.push(e.geoclip_loaded ? "GeoCLIP ready" : "GeoCLIP loading…");
    if (e.ocr_engine && e.ocr_engine !== "none") bits.push(`OCR ${e.ocr_engine}`);
    else bits.push("no OCR");
    bits.push(e.device);
    el.engineStatus.innerHTML =
      `<span class="dot ${e.geoclip_loaded ? "ok" : "warn"}"></span>` +
      esc(bits.join(" · "));

    const offline = e.offline_only;
    el.privacyStatus.innerHTML =
      `<span class="dot ${offline ? "ok" : "warn"}"></span>` +
      esc(
        offline
          ? "offline — no outbound requests"
          : "local analysis; OSM enrichment allowed"
      );
  } catch {
    el.engineStatus.innerHTML = '<span class="dot bad"></span>engine unreachable';
  }
}

function updateCredit() {
  const satellite = el.globeMode.value === "satellite";
  el.hudCredit.textContent = satellite
    ? "Earth imagery: NASA Earth Observatory (Blue Marble, Black Marble, GEBCO). Public domain."
    : "Coastlines: Natural Earth. Public domain.";
}

/* ─────────────────────────── analysis ─────────────────────────── */

async function analyse(file) {
  if (state.busy) return;
  const okType = /\.(jpe?g|png|webp)$/i.test(file.name) ||
    /^image\/(jpeg|png|webp)$/.test(file.type);
  if (!okType) {
    showError(
      "Unsupported file",
      `${esc(file.name)} is not a JPG, PNG or WEBP image.`
    );
    return;
  }

  state.busy = true;
  el.uploadBtn.disabled = true;
  el.report.innerHTML = "";
  el.candidateList.innerHTML = "";
  el.candidateCount.textContent = "—";
  el.verdictPill.textContent = "working";
  el.verdictPill.className = "pill";
  state.globe.clearMarkers();
  showProgress(true);

  // Local preview; the bytes never leave this machine either way.
  const url = URL.createObjectURL(file);
  el.sourceThumb.src = url;
  el.sourceThumbWrap.hidden = false;

  const form = new FormData();
  form.append("image", file, file.name);

  try {
    const resp = await fetch("/api/analyse/stream", {
      method: "POST",
      body: form,
    });
    if (!resp.ok && resp.status !== 422) {
      let detail = `HTTP ${resp.status}`;
      try {
        const j = await resp.json();
        detail = j.detail || detail;
      } catch {}
      throw new Error(detail);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let report = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        const msg = JSON.parse(line);
        if (msg.type === "progress") markStage(msg.stage, msg.message);
        else if (msg.type === "result") report = msg.report;
      }
    }

    if (!report) throw new Error("The server closed without returning a result.");
    if (!report.ok) {
      showError("Analysis failed", esc(report.error || "Unknown error."));
      return;
    }
    state.report = report;
    render(report);
  } catch (err) {
    showError("Analysis failed", esc(err.message || String(err)));
  } finally {
    showProgress(false);
    state.busy = false;
    el.uploadBtn.disabled = false;
    refreshStatus();
  }
}

function showProgress(on) {
  el.progress.hidden = !on;
  if (on) {
    el.progressStage.textContent = "Starting…";
    el.progressSteps.innerHTML = STAGES.map(
      ([key, label]) => `<li data-stage="${key}">${esc(label)}</li>`
    ).join("");
  }
}

function markStage(stage, message) {
  el.progressStage.textContent = message;
  const items = [...el.progressSteps.querySelectorAll("li")];
  let seen = false;
  for (const li of items) {
    if (li.dataset.stage === stage) {
      li.className = "active";
      seen = true;
    } else if (!seen) {
      li.className = "done";
    } else {
      li.className = "";
    }
  }
}

function showError(title, html) {
  el.verdictPill.textContent = "error";
  el.verdictPill.className = "pill error";
  el.report.innerHTML =
    `<div class="callout risk"><strong>${esc(title)}</strong><br>${html}</div>`;
  el.hudStatus.textContent = "Idle";
}

/* ─────────────────────────── rendering ─────────────────────────── */

function render(report) {
  renderCandidates(report);
  renderReport(report);

  const pred = report.prediction || {};
  el.verdictPill.textContent = pred.confidence || "—";
  el.verdictPill.className = `pill ${pred.confidence || ""}`;

  // Drop markers for every candidate, plus the EXIF claim if there is one.
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
      id: "exif",
      lat: gps.latitude,
      lon: gps.longitude,
      label: "EXIF GPS claim",
      kind: "exif",
    });
  }

  if (report.candidates?.length) {
    selectCandidate(0, { fly: true });
  } else if (gps) {
    state.globe.setActiveMarker("exif");
    state.globe.flyTo(gps.latitude, gps.longitude, { altitude: 1.5 });
  }
}

function candidateName(c) {
  const loc = c.location || {};
  const nom = c.nominatim || {};
  return (
    nom.city ||
    loc.nearest_places?.[0]?.name ||
    nom.state ||
    loc.admin1?.name ||
    loc.country?.name ||
    nom.country ||
    "Unnamed"
  );
}

function candidateRegion(c) {
  const loc = c.location || {};
  const nom = c.nominatim || {};
  return [
    nom.state || loc.admin1?.name,
    nom.country || loc.country?.name,
  ]
    .filter(Boolean)
    .join(", ") || (loc.offshore ? "Offshore" : "—");
}

function renderCandidates(report) {
  const list = report.candidates || [];
  el.candidateCount.textContent = String(list.length);
  el.candidateList.innerHTML = "";

  const pred = report.prediction || {};
  const gps = pred.embedded_gps;

  if (!list.length && !gps) {
    el.candidateList.innerHTML =
      '<p class="empty-note">No candidates were produced for this image.</p>';
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
      accent: i === 0 ? "#38bdf8" : "#7dd3fc",
    });

    const main = document.createElement("div");
    main.className = "candidate-main";
    const pct = Math.round((c.probability || 0) * 100);
    const via =
      c.provenance && c.provenance !== "model"
        ? ` · ${esc(c.provenance)}`
        : "";
    main.innerHTML = `
      <span class="candidate-rank">#${i + 1}${via}</span>
      <span class="candidate-name">${esc(candidateName(c))}</span>
      <span class="candidate-region">${esc(candidateRegion(c))}</span>
      <span class="candidate-coords">${esc(fmtCoord(c.latitude, c.longitude))}</span>
      <div class="candidate-meter"><i style="width:${pct}%"></i></div>
      <div class="candidate-foot">
        <span class="candidate-conf">${pct}% of retained mass</span>
        <span class="candidate-conf">${
          c.total_adjustment >= 0 ? "+" : ""
        }${(c.total_adjustment || 0).toFixed(2)}</span>
      </div>`;

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
      accent: "#c084fc",
    });
    const place = gps.place || {};
    const main = document.createElement("div");
    main.className = "candidate-main";
    main.innerHTML = `
      <span class="candidate-rank">EXIF · embedded claim</span>
      <span class="candidate-name">${esc(
        place.nearest_places?.[0]?.name || place.country?.name || "GPS tag"
      )}</span>
      <span class="candidate-region">${esc(
        place.country?.name || "—"
      )}</span>
      <span class="candidate-coords">${esc(
        fmtCoord(gps.latitude, gps.longitude)
      )}</span>
      <div class="candidate-foot">
        <span class="candidate-conf">not an estimate — a tag in the file</span>
      </div>`;
    node.append(canvas, main);
    node.addEventListener("click", () => {
      state.globe.setActiveMarker("exif");
      state.globe.flyTo(gps.latitude, gps.longitude, { altitude: 1.5 });
      el.hudStatus.textContent = "Showing the EXIF GPS claim";
    });
    el.candidateList.appendChild(node);
  }
}

function selectCandidate(index, { fly = false } = {}) {
  const list = state.report?.candidates || [];
  const c = list[index];
  if (!c) return;
  state.selected = index;

  for (const node of el.candidateList.querySelectorAll(".candidate")) {
    node.classList.toggle("active", node.dataset.index === String(index));
  }
  state.globe.setActiveMarker(index);

  if (fly) {
    el.hudStatus.textContent = `Approaching ${candidateName(c)}`;
    const conf = state.report?.prediction?.confidence;
    // Tight results earn a close approach; loose ones stop where the whole
    // uncertainty circle is still on screen.
    const unc = state.report?.prediction?.uncertainty_km || 0;
    const altitude =
      conf === "insufficient" ? 2.4 : Math.max(1.06, Math.min(2.2, 1.05 + unc / 900));
    state.globe.flyTo(c.latitude, c.longitude, { altitude }).then(() => {
      el.hudStatus.textContent = candidateName(c);
    });
  }
}

/* ────────────────────────── the report ────────────────────────── */

function block(title, bodyHtml) {
  return `<div class="report-block"><h3 class="block-title">${esc(
    title
  )}</h3>${bodyHtml}</div>`;
}

function renderReport(report) {
  const pred = report.prediction || {};
  const est = pred.estimated;
  const parts = [];

  /* headline ---------------------------------------------------------- */
  if (!pred.determinate || !est) {
    parts.push(
      block(
        "Assessment",
        `<div class="callout risk"><strong>${esc(
          CONFIDENCE_WORDS[pred.confidence] || "No determination"
        )}</strong><br>${esc(pred.confidence_reason || "")}</div>`
      )
    );
  } else {
    const place = est.place || {};
    const name =
      [place.city, place.region, place.country].filter(Boolean).join(", ") ||
      place.display_name ||
      "Unnamed location";
    parts.push(
      block(
        "Estimated location",
        `<p class="headline-place">${esc(name)}</p>
         <p class="headline-sub">${esc(
           CONFIDENCE_WORDS[pred.confidence] || pred.confidence
         )} · ±${esc(fmtKm(pred.uncertainty_km))}</p>
         <dl class="kv">
           <dt>Latitude</dt><dd class="mono">${est.latitude.toFixed(6)}</dd>
           <dt>Longitude</dt><dd class="mono">${est.longitude.toFixed(6)}</dd>
           <dt>DMS</dt><dd class="mono">${esc(
             fmtDms(est.latitude, est.longitude)
           )}</dd>
           ${place.road ? `<dt>Road</dt><dd>${esc(place.road)}</dd>` : ""}
           ${place.suburb ? `<dt>Suburb</dt><dd>${esc(place.suburb)}</dd>` : ""}
           ${place.city ? `<dt>City</dt><dd>${esc(place.city)}</dd>` : ""}
           ${place.region ? `<dt>Region</dt><dd>${esc(place.region)}</dd>` : ""}
           ${
             place.country
               ? `<dt>Country</dt><dd>${esc(place.country)}${
                   place.country_code ? ` (${esc(place.country_code)})` : ""
                 }</dd>`
               : ""
           }
           ${
             place.postcode
               ? `<dt>Postcode</dt><dd class="mono">${esc(place.postcode)}</dd>`
               : ""
           }
           ${timezoneRow(report)}
           <dt>Named by</dt><dd>${esc(place.source || "—")}</dd>
         </dl>
         <div class="callout info" style="margin-top:9px">${esc(
           pred.confidence_reason || ""
         )}</div>
         ${
           place.boundary_note
             ? `<div class="callout warn">${esc(place.boundary_note)}</div>`
             : ""
         }`
      )
    );
  }

  for (const note of pred.notes || []) {
    parts.push(`<div class="callout warn">${esc(note)}</div>`);
  }

  /* embedded GPS ------------------------------------------------------ */
  const gps = pred.embedded_gps;
  if (gps) {
    const agree = pred.exif_vs_estimate;
    const place = gps.place || {};
    parts.push(
      block(
        "Embedded GPS — a claim, not proof",
        `<div class="callout exif">
           <strong>The file carries a GPS tag.</strong> That is what the writer
           recorded, not what the picture shows. It is trivially editable.
         </div>
         <dl class="kv">
           <dt>Latitude</dt><dd class="mono">${gps.latitude.toFixed(6)}</dd>
           <dt>Longitude</dt><dd class="mono">${gps.longitude.toFixed(6)}</dd>
           ${
             gps.altitude_m != null
               ? `<dt>Altitude</dt><dd class="mono">${gps.altitude_m} m</dd>`
               : ""
           }
           ${
             gps.timestamp_utc
               ? `<dt>GPS time</dt><dd class="mono">${esc(gps.timestamp_utc)}</dd>`
               : ""
           }
           ${
             gps.processing_method
               ? `<dt>Method</dt><dd>${esc(gps.processing_method)}</dd>`
               : ""
           }
           <dt>Resolves to</dt><dd>${esc(
             [
               place.nearest_places?.[0]?.name,
               place.admin1?.name,
               place.country?.name,
             ]
               .filter(Boolean)
               .join(", ") || "—"
           )}</dd>
         </dl>
         ${
           agree
             ? `<div class="callout ${agree.consistent ? "info" : "risk"}"
                  style="margin-top:8px">
                  <strong>${
                    agree.consistent ? "Consistent" : "Disagreement"
                  }</strong> — ${esc(fmtKm(agree.distance_km))} from the image
                  estimate.<br>${esc(agree.note)}
                </div>`
             : ""
         }
         ${(gps.trust_warnings || [])
           .map((w) => `<div class="callout warn">${esc(w)}</div>`)
           .join("")}`
      )
    );
  }

  /* evidence ---------------------------------------------------------- */
  const readings = (report.evidence?.readings || []).filter((r) => r.called);
  const uncalled = (report.evidence?.readings || []).filter(
    (r) => !r.called && r.confidence >= 0.3
  );
  if (readings.length || uncalled.length) {
    const rows = readings
      .map(
        (r) => `
        <div class="evidence-item">
          <span class="evidence-label">${esc(r.label)}</span>
          <span class="evidence-score">${Math.round(r.confidence * 100)}%</span>
          <div class="evidence-bar"><i style="width:${Math.round(
            r.confidence * 100
          )}%"></i></div>
          <span class="evidence-detail">${esc(r.title)}${
          r.runners_up?.length
            ? ` · next: ${esc(r.runners_up[0][0])} ${Math.round(
                r.runners_up[0][1] * 100
              )}%`
            : ""
        }</span>
        </div>`
      )
      .join("");
    const observed = uncalled.length
      ? `<div class="evidence-detail" style="margin-top:8px">
           Observed but not used (below threshold or geographically
           unconstraining): ${uncalled
             .map((r) => `${esc(r.label)} ${Math.round(r.confidence * 100)}%`)
             .join(" · ")}
         </div>`
      : "";
    parts.push(
      block(
        readings.length
          ? `Detected evidence (${readings.length} used)`
          : "Detected evidence",
        (rows ||
          '<p class="empty-note">No visual clue cleared its threshold, so nothing was re-ranked.</p>') +
          observed
      )
    );
  }

  /* how the winner moved ---------------------------------------------- */
  const top = (report.candidates || [])[0];
  if (top?.adjustments?.length) {
    const rows = top.adjustments
      .slice()
      .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
      .slice(0, 8)
      .map(
        (a) => `
        <div class="evidence-item">
          <span class="evidence-label">${esc(a.source.replace(/_/g, " "))}</span>
          <span class="evidence-score ${
            a.delta >= 0 ? "" : ""
          }">${a.delta >= 0 ? "+" : ""}${a.delta.toFixed(2)}</span>
          <span class="evidence-detail">${esc(a.detail)}</span>
        </div>`
      )
      .join("");
    const diag = pred.diagnostics || {};
    parts.push(
      block(
        "How the leading candidate was scored",
        rows +
          `<div class="tag-row" style="margin-top:8px">
             <span class="tag">locator mass ${(
               (diag.locator_mass || 0) * 100
             ).toFixed(1)}%</span>
             <span class="tag">concentration ${Math.round(
               (diag.concentration || 0) * 100
             )}%</span>
             <span class="tag">dispersion ${esc(
               fmtKm(diag.dispersion_km)
             )}</span>
             <span class="tag ${
               (diag.evidence_support || 0) >= 0 ? "pos" : "neg"
             }">evidence ${(diag.evidence_support || 0) >= 0 ? "+" : ""}${(
            diag.evidence_support || 0
          ).toFixed(2)}</span>
           </div>`
      )
    );
  }

  /* text read off the image ------------------------------------------- */
  const ocr = report.ocr || {};
  if (ocr.line_count) {
    const scripts = ocr.scripts?.proportions || {};
    const scriptTags = Object.entries(scripts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(
        ([s, p]) => `<span class="tag">${esc(s)} ${Math.round(p * 100)}%</span>`
      )
      .join("");
    parts.push(
      block(
        `Text read from the image (${ocr.line_count})`,
        `<div class="ocr-text">${esc(ocr.text)}</div>
         ${scriptTags ? `<div class="tag-row">${scriptTags}</div>` : ""}`
      )
    );
  } else if (ocr.error) {
    parts.push(
      block("Text", `<div class="callout warn">${esc(ocr.error)}</div>`)
    );
  }

  if (report.toponyms?.length) {
    const rows = report.toponyms
      .map(
        (t) => `
        <li>
          <span class="fname">${esc(t.text)} → ${esc(t.matched_name)}${
          t.language ? ` <span class="tag">${esc(t.language)}</span>` : ""
        }</span>
          <span class="fdist">${
            t.corroborated_km != null ? esc(fmtKm(t.corroborated_km)) : "—"
          }</span>
        </li>`
      )
      .join("");
    parts.push(
      block(
        "Place names matched in the gazetteer",
        `<ul class="feature-list">${rows}</ul>`
      )
    );
  }

  /* surroundings ------------------------------------------------------- */
  const surr = top?.surroundings;
  if (surr?.features?.length) {
    const rows = surr.features
      .slice(0, 10)
      .map(
        (f) => `
        <li><span class="fname">${esc(f.name)}</span>
        <span class="fdist">${f.distance_m} m ${esc(f.direction)}</span></li>`
      )
      .join("");
    parts.push(
      block(
        "Nearby named features",
        `<ul class="feature-list">${rows}</ul>` +
          (surr.road_refs?.length
            ? `<div class="tag-row">${surr.road_refs
                .slice(0, 10)
                .map((r) => `<span class="tag">${esc(r)}</span>`)
                .join("")}</div>`
            : "")
      )
    );
  }

  /* file facts ---------------------------------------------------------- */
  const exif = report.exif || {};
  const img = report.image || {};
  parts.push(
    block(
      "File",
      `<dl class="kv">
        <dt>Name</dt><dd>${esc(img.filename || "—")}</dd>
        <dt>Format</dt><dd>${esc(img.format || "—")} · ${img.width}×${
        img.height
      }</dd>
        <dt>Size</dt><dd>${((img.bytes || 0) / 1024).toFixed(0)} KB</dd>
        ${
          exif.camera_make || exif.camera_model
            ? `<dt>Camera</dt><dd>${esc(
                [exif.camera_make, exif.camera_model].filter(Boolean).join(" ")
              )}</dd>`
            : ""
        }
        ${exif.lens ? `<dt>Lens</dt><dd>${esc(exif.lens)}</dd>` : ""}
        ${
          exif.captured_at
            ? `<dt>Captured</dt><dd class="mono">${esc(exif.captured_at)}</dd>`
            : ""
        }
        ${exif.software ? `<dt>Software</dt><dd>${esc(exif.software)}</dd>` : ""}
        <dt>EXIF</dt><dd>${exif.has_exif ? "present" : "absent"}</dd>
      </dl>
      ${
        !gps
          ? (exif.trust_warnings || [])
              .map((w) => `<div class="callout warn">${esc(w)}</div>`)
              .join("")
          : ""
      }`
    )
  );

  for (const w of report.warnings || []) {
    parts.push(`<div class="callout warn">${esc(w)}</div>`);
  }

  parts.push(
    `<div class="evidence-detail" style="margin-top:6px">
       Analysed in ${report.elapsed_s}s. Image processed locally; it was not
       uploaded anywhere.
     </div>`
  );

  el.report.innerHTML = parts.join("");
  el.attribution.textContent = (report.attribution || []).join(" · ");
  el.attribution.title = (report.attribution || []).join("\n");
}

function timezoneRow(report) {
  const tz = (report.candidates || [])[0]?.timezone;
  if (!tz) return "";
  if (tz.iana_name) {
    const off =
      tz.utc_offset_hours != null
        ? ` (UTC${tz.utc_offset_hours >= 0 ? "+" : ""}${tz.utc_offset_hours})`
        : "";
    return `<dt>Timezone</dt><dd>${esc(tz.iana_name)}${esc(off)}</dd>`;
  }
  return `<dt>Timezone</dt><dd>estimated UTC${
    tz.utc_offset_hours >= 0 ? "+" : ""
  }${tz.utc_offset_hours} — ${esc(tz.method)}</dd>`;
}

boot();
