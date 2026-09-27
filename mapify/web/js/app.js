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
  folderBtn: $("folder-btn"),
  cancelBtn: $("cancel-btn"),
  saveBtn: $("save-btn"),
  exportBtn: $("export-btn"),
  leftTitle: $("left-title"),
  batchBar: $("batch-bar"),
  batchBarFill: $("batch-bar-fill"),
  batchProgress: $("batch-progress"),
  batchRate: $("batch-rate"),
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
  hudMoon: $("hud-moon"),
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
  finding: $("finding"),
  findingCard: null,      // resolved after the DOM is parsed
  findingOrigin: $("finding-origin"),
  findingPlace: $("finding-place"),
  findingChip: $("finding-chip"),
  findingCoord: $("finding-coord"),
  findingSpread: $("finding-spread"),
  findingWhy: $("finding-why"),
  askMore: $("ask-more"),
  askLede: $("ask-lede"),
  askQuestion: $("ask-question"),
  askList: $("ask-list"),
  askCount: $("ask-count"),
  askAdd: $("ask-add"),
  askDecline: $("ask-decline"),
  firstrun: $("firstrun"),
  frSize: $("fr-size"),
  frComponents: $("fr-components"),
  frOrigin: $("fr-origin"),
  frCache: $("fr-cache"),
  frThen: $("fr-then"),
  frProgress: $("fr-progress"),
  frStage: $("fr-stage"),
  frRate: $("fr-rate"),
  frBar: $("fr-bar"),
  frDetail: $("fr-detail"),
  frError: $("fr-error"),
  frDefer: $("fr-defer"),
  frGo: $("fr-go"),
};

const STAGES = [
  ["decode", "Decode and normalise"],
  ["exif", "Read embedded metadata"],
  ["locate", "Estimate coordinates"],
  ["evidence", "Read visual clues"],
  ["ocr", "Read signage text"],
  ["readplace", "Look up the address"],
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
  high: "Measured over 163 samples: all 26 high-confidence calls landed "
      + "within 47 km of the true location.",
  medium: "Measured over 163 samples: 88% of medium calls landed within "
        + "1500 km, median 16 km. A region to check, not a location.",
  low: "Measured over 163 samples: 63% within 1500 km, median 728 km. "
     + "A lead, nothing more.",
  insufficient: "Measured over 163 samples: only 31% within 1500 km. "
              + "The evidence does not support naming a location.",
};

// What the tool decided to do about a GPS tag, rather than what it
// measured. "They are 9,714 km apart" leaves the decision with the reader
// at the point where the tool knows more than they do.
const EXIF_VERDICT = {
  corroborated: "Corroborated by the image — used as the coordinate",
  rejected: "Rejected — the image says otherwise",
  unresolved: "Unresolved — neither can be trusted",
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
  sourcePath: null,
  home: null,           // where the globe opens, from the system timezone
  session: null,        // { counted, observations } while collecting images
  batch: null,          // { items, clusters, selected } while a batch exists
  setup: null,          // first-run status, once /api/firstrun has answered
  warming: false,       // a warmup stream is open
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

/**
 * The pywebview bridge, if there is one.
 *
 * Resolved at boot, but re-checked on every use: pywebviewready can fire
 * later than the page finishes loading, and a user who clicks in that window
 * should not be told the desktop app is a browser.
 */
function nativeApi() {
  if (!state.native && window.pywebview?.api) {
    state.native = window.pywebview.api;
    document.body.classList.add("is-native");
  }
  return state.native;
}

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
  el.folderBtn.addEventListener("click", openFolder);
  el.cancelBtn.addEventListener("click", cancelBatch);
  el.saveBtn.addEventListener("click", saveReport);
  el.exportBtn.addEventListener("click", exportHtml);
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
  await goHome();
  await refreshStatus();

  const tiles = state.status?.client;
  if (tiles?.basemap_tiles_enabled && tiles.basemap_tile_url) {
    state.globe.setTileSource(tiles.basemap_tile_url, tiles.basemap_attribution);
  }
  updateCredit();

  el.askAdd.addEventListener("click", addToSession);
  el.askDecline.addEventListener("click", declineMore);
  el.frGo.addEventListener("click", runSetup);
  el.frDefer.addEventListener("click", deferFirstRun);
  el.firstrun.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !state.warming) deferFirstRun();
  });

  // Ask what the first launch will cost before spending any of it. When
  // there is nothing to fetch this warms up silently, as it always did.
  await checkFirstRun();
}


/* ═══════════════════════════ first run ═══════════════════════════
   The first launch costs about 1.6 GB and several minutes. Saying so
   afterwards, in the status bar, is not telling someone -- it is
   informing them of a decision already taken on their behalf.

   So: state the size before anything starts, show real progress while it
   runs, and let it be declined. A declined download is remembered, and
   the offer is repeated only when the user tries to do something that
   actually needs it.
*/

function fmtBytes(n) {
  if (!n && n !== 0) return "—";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GB";
  if (n >= 1e6) return Math.round(n / 1e6) + " MB";
  if (n >= 1e3) return Math.round(n / 1e3) + " kB";
  return n + " B";
}

function fmtDuration(seconds) {
  if (seconds == null || !isFinite(seconds)) return "";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m " + String(s % 60).padStart(2, "0") + "s";
  return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
}

async function checkFirstRun() {
  try {
    state.setup = await (await fetch("/api/firstrun")).json();
  } catch {
    return;                       // service not up yet; boot() will carry on
  }
  if (state.setup.ready) {
    warmUpQuietly();              // nothing to fetch: just load, in silence
    return;
  }
  if (state.setup.deferred) {
    setHudState("Setup deferred — open an image to finish it");
    return;                       // they already said no; do not nag on boot
  }
  showFirstRun();
}

function showFirstRun() {
  const setup = state.setup;
  if (!setup) return;

  // Three different situations reach this screen and they are not the same
  // sentence. Weights missing is a download; weights present with no
  // encoded gallery is a minute of local computation and no network at all.
  // Saying "needs to download" in the second case would be a plain untruth.
  const needsDownload = Boolean(setup.download_required);
  document.getElementById("fr-title").textContent = needsDownload
    ? "Mapify needs to download its model"
    : "Mapify needs a minute to finish setting up";
  document.getElementById("fr-lede").innerHTML = needsDownload
    ? 'The application ships without model weights, so the first launch '
      + 'fetches them. This happens <strong>once</strong>. Every launch after '
      + 'it starts in a couple of seconds, and every analysis runs on this '
      + 'machine with nothing leaving it.'
    : 'The model is already here. What is left is a one-off computation on '
      + 'this machine — encoding the reference grid the locator searches '
      + 'against — which makes <strong>every</strong> later analysis about '
      + 'twenty times faster. No network is involved.';
  el.frGo.textContent = needsDownload ? "Download and set up" : "Set up now";
  document.getElementById("fr-foot-tail").textContent = needsDownload
    ? "Nothing is sent anywhere — this is a download, not an upload."
    : "No network is involved in this step at all.";

  el.frSize.textContent = needsDownload ? fmtBytes(setup.total_bytes)
                                        : "Nothing";

  el.frComponents.innerHTML = (setup.components || []).map((c) => `
    <li>
      <span class="fr-item-head">
        ${esc(c.label)}
        <span class="lic">${esc(c.licence)}</span>
        <span class="fr-item-size">${
          c.present ? '<span class="have">already here</span>'
                    : esc(fmtBytes(c.bytes))
        }</span>
      </span>
      ${esc(c.why)}
    </li>`).join("");

  const origins = [...new Set((setup.components || [])
    .filter((c) => !c.present).map((c) => c.origin))];
  el.frOrigin.textContent = origins.length ? origins.join(", ")
                                           : "nothing — it is already here";
  el.frCache.textContent = setup.cache_dir || "—";
  el.frThen.textContent = setup.gallery_encode_required
    ? `usually about ${fmtDuration(setup.gallery_encode_seconds)} of one-off `
      + "computation, on this machine, with no network — longer if memory "
      + "is short"
    : "ready immediately";

  el.frError.hidden = true;
  el.frProgress.hidden = true;
  el.firstrun.hidden = false;
  el.frGo.focus();
}

function hideFirstRun() {
  el.firstrun.hidden = true;
}

async function deferFirstRun() {
  hideFirstRun();
  try {
    await fetch("/api/firstrun/defer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deferred: true }),
    });
  } catch { /* the marker is a convenience, not a requirement */ }
  if (state.setup) state.setup.deferred = true;
  setHudState("Setup deferred — open an image to finish it");
}

/** Warm up with no screen: everything is already on disk. */
function warmUpQuietly() {
  if (state.warming) return;
  state.warming = true;
  fetch("/api/warmup", { method: "POST" })
    .then((r) => r.json())
    .then(() => refreshStatus())
    .catch(() => {})
    .finally(() => { state.warming = false; });
}

/** Warm up in front of the user, narrating it. */
async function runSetup() {
  if (state.warming) return;
  state.warming = true;
  el.frGo.disabled = true;
  el.frDefer.disabled = true;
  el.frError.hidden = true;
  el.frProgress.hidden = false;
  el.frStage.textContent = "Starting";
  el.frDetail.textContent = "";
  el.frRate.textContent = "";
  el.frBar.style.width = "0%";

  const STAGE_TEXT = {
    starting: "Starting",
    locator: "Downloading and loading the geolocation model",
    gallery: "Encoding the GPS gallery",
    evidence: "Loading the evidence encoder",
    geodata: "Indexing the offline gazetteer",
    ready: "Finishing",
  };

  let indeterminate = false;
  let galleryStarted = 0;
  let ticker = null;

  const showGalleryElapsed = () => {
    const took = (Date.now() - galleryStarted) / 1000;
    const total = state.setup?.gallery_encode_seconds || 90;
    if (took <= total) {
      el.frBar.parentElement.classList.remove("indeterminate");
      el.frBar.style.width = ((took / total) * 100).toFixed(1) + "%";
      el.frDetail.textContent =
        `${fmtDuration(took)} of about ${fmtDuration(total)} — it is `
        + "encoding 100,000 reference points. This happens once; the result "
        + "is cached and every launch after this skips it.";
    } else {
      // Past the estimate, a bar pinned near the end is a lie that looks
      // like a hang. Stop pretending to know how far through it is and
      // say what is actually true: it is still working, for this long.
      el.frBar.parentElement.classList.add("indeterminate");
      el.frBar.style.width = "100%";
      el.frDetail.textContent =
        `${fmtDuration(took)} elapsed — longer than usual. It is still `
        + "working. The encode is memory-hungry, so a machine that is short "
        + "on RAM takes several minutes.";
    }
  };

  try {
    const resp = await fetch("/api/warmup/stream", { method: "POST" });
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const ev = JSON.parse(line);

        if (ev.type === "stage") {
          el.frStage.textContent = STAGE_TEXT[ev.stage] || ev.message || ev.stage;
          if (ev.stage === "gallery") {
            indeterminate = true;
            galleryStarted = Date.now();
            el.frRate.textContent = "";
            if (ticker) clearInterval(ticker);
            ticker = setInterval(showGalleryElapsed, 500);
            showGalleryElapsed();
          } else if (ev.stage !== "locator" && ev.stage !== "starting") {
            indeterminate = true;
            if (ticker) { clearInterval(ticker); ticker = null; }
            el.frBar.parentElement.classList.remove("indeterminate");
            el.frRate.textContent = "";
            el.frDetail.textContent = "Nearly there.";
            el.frBar.style.width = "97%";
          }
        } else if (ev.type === "download" && !indeterminate) {
          const pct = Math.max(0, Math.min(1, ev.fraction || 0)) * 100;
          el.frBar.style.width = pct.toFixed(1) + "%";
          el.frRate.textContent = ev.bytes_per_second
            ? fmtBytes(ev.bytes_per_second) + "/s" : "";
          el.frDetail.textContent =
            `${fmtBytes(ev.bytes)} of about ${fmtBytes(ev.total)}`
            + (ev.eta_s != null ? `  ·  ${fmtDuration(ev.eta_s)} remaining` : "");
        } else if (ev.type === "done") {
          if (ticker) clearInterval(ticker);
          el.frBar.parentElement.classList.remove("indeterminate");
          el.frBar.style.width = "100%";
          el.frStage.textContent = "Ready";
          el.frDetail.textContent = `Set up in ${fmtDuration(ev.elapsed_s)}.`;
          await refreshStatus();
          setTimeout(hideFirstRun, 700);
          if (state.setup) { state.setup.ready = true; state.setup.deferred = false; }
          return;
        } else if (ev.type === "failed") {
          throw new Error(ev.error || "setup failed");
        }
      }
    }
    throw new Error("The setup stream ended without finishing.");
  } catch (err) {
    if (ticker) clearInterval(ticker);
    el.frBar.parentElement.classList.remove("indeterminate");
    el.frProgress.hidden = true;
    el.frError.hidden = false;
    el.frError.textContent =
      `Setup did not finish: ${err.message}. `
      + "The download resumes where it stopped, so trying again does not "
      + "start over. If it keeps failing, check the connection to "
      + `${state.setup?.components?.[0]?.origin || "huggingface.co"}.`;
    el.frGo.textContent = "Try again";
  } finally {
    state.warming = false;
    el.frGo.disabled = false;
    el.frDefer.disabled = false;
  }
}

/**
 * Called before an analysis. Returns false if the models are not there and
 * the user has to decide about that first.
 */
function setupBlocks() {
  if (!state.setup || state.setup.ready) return false;
  const e = state.status?.engine || {};
  if (e.geoclip_loaded && e.clip_loaded) return false;
  showFirstRun();
  return true;
}

/* ════════════════════════ the finding ════════════════════════
   The one thing on screen that is the answer, placed next to the globe
   it is an answer about rather than buried in a rail of detail.
*/

function hideFinding() {
  el.finding.hidden = true;
}

function showFinding(report) {
  const pred = report.prediction || {};
  const est = pred.estimated;
  const card = el.finding.querySelector(".finding-card");
  card.classList.remove("indeterminate", "read");

  if (!pred.determinate || !est) {
    el.findingOrigin.className = "origin none";
    el.findingOrigin.textContent = "No location determined";
    el.findingPlace.textContent = "Not placed";
    el.findingChip.className = `chip ${pred.confidence || ""}`;
    el.findingChip.textContent =
      CONFIDENCE[pred.confidence] || pred.confidence || "—";
    el.findingCoord.textContent = "";
    el.findingSpread.textContent = "";
    el.findingWhy.textContent = pred.confidence_reason || "";
    card.classList.add("indeterminate");
    el.finding.hidden = false;
    return;
  }

  // Was this read off the image, or inferred from what the image looks
  // like? They are different kinds of claim and fail in different ways --
  // a photograph of a poster carries a real address that is not where the
  // picture was taken -- so the card says which, before it says where.
  const leader = (report.candidates || [])[0] || {};
  const wasRead = String(leader.provenance || "").includes("text");
  const readPlace = (report.read_places || [])[0];

  el.findingOrigin.className = `origin ${wasRead ? "read" : "estimated"}`;
  el.findingOrigin.textContent = wasRead
    ? "Read from the image"
    : "Estimated from the image";
  if (wasRead) card.classList.add("read");

  const place = est.place || {};
  el.findingPlace.textContent =
    [place.city, place.region, place.country].filter(Boolean).join(", ") ||
    place.display_name || "Unnamed location";

  el.findingChip.className = `chip ${esc(pred.confidence)}`;
  el.findingChip.title = CONFIDENCE_MEASURED[pred.confidence] || "";
  el.findingChip.textContent =
    CONFIDENCE[pred.confidence] || pred.confidence;

  el.findingCoord.textContent =
    `${est.latitude.toFixed(5)}, ${est.longitude.toFixed(5)}`;

  // Two different questions, and they used to be answered with one
  // number. "How precise is this coordinate" is the size of the thing
  // that was matched. "How sure are we it is the right place" is the
  // spread of the candidates. Printing the second as a tolerance beside
  // a five-decimal coordinate said something false about both.
  const precision = pred.precision_km;
  el.findingSpread.textContent = precision != null
    ? `±${fmtKm(precision)}`
    : (pred.uncertainty_km ? `±${fmtKm(pred.uncertainty_km)}` : "");
  el.findingSpread.title = precision != null
    ? `The matched feature is about ${fmtKm(precision * 2)} across. `
      + `Separately, the candidates span ${fmtKm(pred.uncertainty_km)} — `
      + `that is how sure this is the right place, not how precise the `
      + `coordinate is.`
    : "How far apart the candidates are.";

  el.findingWhy.textContent = wasRead && readPlace
    ? `Matched "${readPlace.display_name.split(",")[0]}" in OpenStreetMap `
      + `from text in the image.`
    : "";

  el.finding.hidden = false;
}

/**
 * Open the globe over the user's own part of the world, lit for now.
 *
 * The alternative is opening over the mid-Atlantic at an arbitrary hour,
 * which is what it did, and which quietly says the thing on screen is a
 * picture rather than a model of a planet that is currently turning.
 *
 * Where "here" comes from is the interesting part: the system timezone,
 * which is already on the machine, needs no permission and leaves no
 * network trace. Not IP geolocation -- that sends the user's address to a
 * stranger to decide a camera angle. Not the browser's geolocation
 * prompt -- that asks for a precise fix to do something that needs none.
 */
async function goHome() {
  try {
    const resp = await fetch("/api/home", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
        utc_offset_minutes: -new Date().getTimezoneOffset(),
      }),
    });
    if (!resp.ok) return;
    const home = await resp.json();
    state.home = home;

    // Real sun, because the whole point is that it matches outside.
    el.lightMode.value = "realtime";
    state.globe.setLighting("realtime");
    // In Earth radii, not kilometres: 3.72 R is the ~17,000 km the globe
    // already opened at, far enough out that the Moon is in frame.
    await state.globe.flyTo(home.latitude, home.longitude, {
      altitude: 3.72,
      duration: 0,
    });
    state.globe.resume();      // it may turn; nothing has been found yet

    const where = home.city
      ? `${home.city} — from this computer's timezone`
      : "your approximate longitude — from this computer's clock";
    setHudState(`Showing ${where}`);
    el.hudState.title = home.note || "";
  } catch {
    /* A camera angle is not worth failing a launch over. */
  }
}

/* ══════════════════ more than one photograph ══════════════════
   A single image that will not place itself is not the end of the
   conversation. The person holding the phone usually has another
   picture of the same place, and a second angle is *independent*
   evidence in a way that no amount of further analysis of the first
   one can be.
*/

function hideAsk() { el.askMore.hidden = true; }

/** One file, chosen through the ordinary input, as a promise. */
function pickFile() {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".jpg,.jpeg,.png,.webp,image/*";
    input.addEventListener("change", () => resolve(input.files?.[0] || null),
                           { once: true });
    input.click();
  });
}

function showAsk(ask) {
  el.askLede.textContent = ask.lede || "";
  el.askQuestion.textContent = ask.question || "";
  el.askList.innerHTML = (ask.helps_most || [])
    .map((h) => `<li>${esc(h)}</li>`).join("");
  el.askCount.textContent = ask.counted > 1
    ? `${ask.counted} photographs combined so far.` : "";
  el.askDecline.textContent = ask.counted > 1
    ? "That is all of them" : "That is the only picture";
  hideFinding();
  el.askMore.hidden = false;
}

/** Pick another photograph of the same place and fold it in. */
async function addToSession() {
  const native = nativeApi();
  let chosen = null;
  let file = null;

  if (native) {
    try {
      chosen = await native.open_image();
    } catch {
      return;
    }
    if (!chosen || !chosen.path) return;
  } else {
    // Same pair as the analysis endpoints: a path where the desktop
    // dialog approved one, the bytes otherwise.
    file = await pickFile();
    if (!file) return;
  }

  hideAsk();
  state.busy = true;
  el.openBtn.disabled = true;
  setHudState("Combining with the previous photographs…");
  showProgress(true);
  try {
    let resp;
    if (chosen) {
      resp = await fetch("/api/session/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: chosen.path }),
      });
    } else {
      const form = new FormData();
      form.append("image", file, file.name);
      resp = await fetch("/api/session/add/upload", {
        method: "POST", body: form,
      });
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const out = await resp.json();
    state.session = out;

    // render() draws the rails, the finding card and the fly-to.
    if (out.combined) {
      state.report = out.combined;
      render(out.combined);
      el.saveBtn.disabled = false;
      el.exportBtn.disabled = false;
    }
    if (out.observation && !out.observation.counted) {
      // Said plainly rather than silently: the user chose that file and
      // is entitled to know it did not count, and why.
      el.report.insertAdjacentHTML("afterbegin",
        '<div class="note warn"><div><strong>That looked like the same '
        + 'picture.</strong> It is too similar to one already added to '
        + 'count as separate evidence, so it was left out rather than '
        + 'counted twice.</div></div>');
    }
    if (out.ask) showAsk(out.ask);
    setHudState(out.combined ? "Result displayed" : "Idle");
  } catch (err) {
    fail("Could not add that photograph", String(err.message || err));
  } finally {
    showProgress(false);
    state.busy = false;
    el.openBtn.disabled = false;
  }
}

/** The user has no more pictures. Give what there is, as possibilities. */
async function declineMore() {
  hideAsk();
  try {
    await fetch("/api/session/end", { method: "POST" });
  } catch { /* ending a session is a courtesy, not a requirement */ }
  state.session = null;
  if (state.report) showFinding(state.report);
  setHudState("Showing the possibilities");
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
  // Where the Moon is, whether or not it happens to be on this side of
  // the planet. Without this, a Moon that is astronomically behind the
  // Earth reads as a feature that does not work.
  const moon = imagery?.moon;
  if (moon) {
    el.hudMoon.textContent =
      `${moon.lat >= 0 ? "N" : "S"}${Math.abs(moon.lat).toFixed(1)} ` +
      `${moon.lon >= 0 ? "E" : "W"}${Math.abs(moon.lon).toFixed(1)}  ` +
      `${Math.round(moon.illuminated * 100)}%`;
    el.hudMoon.title =
      `${moon.phase}, ${Math.round(moon.illuminated * 100)}% lit, ` +
      `${Math.round(moon.distanceKm).toLocaleString()} km away. Its `
      + `direction and phase are computed from this computer's clock; it `
      + `is drawn closer than it really is so that it fits in frame.`;
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
  // Said out loud, in a tool about not overclaiming. Drawing objects in
  // orbit around the Earth in a reconnaissance application invites the
  // reading that it knows where they are. It does not: the altitudes,
  // inclinations and periods are the real ones, the objects are not.
  parts.push("Orbits: representative regimes at true periods — not tracked objects.");
  el.credit.textContent = parts.join("   ");
}

/* ─────────────────────────── file in/out ─────────────────────────── */

async function openImage() {
  if (state.busy) return;
  if (!nativeApi()) { el.fileInput.click(); return; }
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
  if (!nativeApi()) {
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

/* ──────────────────────────── batch ──────────────────────────── */

async function openFolder() {
  if (state.busy) return;
  if (!nativeApi()) {
    fail("Folder analysis needs the desktop app",
         "Open Mapify as an application rather than through a browser.");
    return;
  }
  let picked;
  try {
    picked = await state.native.open_folder(false);
  } catch (err) {
    fail("Could not open the folder dialog", String(err));
    return;
  }
  if (!picked || picked.cancelled) return;
  if (picked.error) { fail("Cannot analyse that folder", picked.error); return; }
  runBatch(picked);
}

async function cancelBatch() {
  el.cancelBtn.disabled = true;
  setHudState("Stopping after the current image…");
  try {
    await fetch("/api/batch/cancel", { method: "POST" });
  } catch {}
}

async function runBatch(folder) {
  if (setupBlocks()) return;
  state.busy = true;
  hideFinding();
  state.batch = { items: [], clusters: [], selected: -1, folder: folder.folder };
  state.report = null;
  el.openBtn.disabled = true;
  el.folderBtn.disabled = true;
  el.saveBtn.disabled = true;
  el.exportBtn.disabled = true;
  el.cancelBtn.hidden = false;
  el.cancelBtn.disabled = false;
  el.leftTitle.textContent = "Batch";
  el.batchBar.hidden = false;
  el.sourceFoot.hidden = true;
  el.report.innerHTML = "";
  el.verdict.textContent = "batch";
  el.verdict.className = "chip working";
  state.globe.clearMarkers();
  renderBatch();

  const started = Date.now();
  if (folder.truncated) {
    setHudState(`Folder capped at ${folder.files.length} images`);
  }

  try {
    const resp = await fetch("/api/batch/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: folder.files.map((f) => f.path) }),
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch {}
      throw new Error(detail);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
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
        handleBatchEvent(msg, started);
      }
    }
  } catch (err) {
    fail("Batch failed", err.message || String(err));
  } finally {
    state.busy = false;
    el.openBtn.disabled = false;
    el.folderBtn.disabled = false;
    el.cancelBtn.hidden = true;
    refreshStatus();
  }
}

function handleBatchEvent(msg, started) {
  const b = state.batch;
  if (!b) return;

  if (msg.type === "start") {
    b.items = Array.from({ length: msg.total }, () => null);
  } else if (msg.type === "item") {
    b.items[msg.index] = msg.item;
    if (msg.done) {
      const frac = msg.done / msg.total;
      el.batchBarFill.style.width = `${frac * 100}%`;
      el.batchProgress.textContent = `${msg.done}/${msg.total}`;
      const perImage = (Date.now() - started) / 1000 / msg.done;
      const left = Math.round(perImage * (msg.total - msg.done));
      el.batchRate.textContent = msg.done < msg.total
        ? `${perImage.toFixed(1)}s each · ~${left}s left` : "";
    }
    renderBatch();
  } else if (msg.type === "phase") {
    setHudState(msg.phase === "clustering"
      ? "Grouping results by place…"
      : `Naming ${msg.clusters} place${msg.clusters === 1 ? "" : "s"}…`);
  } else if (msg.type === "cluster") {
    const i = b.clusters.findIndex((c) => c.index === msg.cluster.index);
    if (i >= 0) b.clusters[i] = msg.cluster;
    else b.clusters.push(msg.cluster);
    for (const m of msg.cluster.members) {
      if (b.items[m]) b.items[m].place = msg.cluster.place;
    }
    renderBatch();
  } else if (msg.type === "complete") {
    b.items = msg.items;
    b.clusters = msg.clusters;
    finishBatch(msg);
  } else if (msg.type === "error") {
    fail("Batch failed", msg.error);
  }
}

function finishBatch(summary) {
  el.batchBarFill.style.width = "100%";
  el.batchProgress.textContent = `${summary.analysed}/${summary.total}`;
  el.batchRate.textContent = summary.seconds_per_image
    ? `${summary.seconds_per_image}s each` : "";
  el.verdict.textContent = summary.cancelled ? "stopped" : "done";
  el.verdict.className = `chip ${summary.cancelled ? "low" : "high"}`;
  el.saveBtn.disabled = false;
  el.exportBtn.disabled = false;
  state.report = summary;          // Save report exports the whole batch
  setHudState(summary.cancelled
    ? `Stopped — ${summary.analysed} of ${summary.total} analysed`
    : `${summary.analysed} images, ${summary.clusters.length} distinct places`);

  renderBatch();
  renderBatchSummary(summary);

  // One marker per place, labelled with how many images landed there.
  state.globe.clearMarkers();
  summary.clusters.forEach((c, i) => {
    state.globe.addMarker({
      id: `c${c.index}`,
      lat: c.latitude,
      lon: c.longitude,
      label: `${c.place} (${c.size})`,
      kind: i === 0 ? "primary" : "candidate",
      radiusKm: c.radius_km > 1 ? c.radius_km : 0,
    });
  });
  if (summary.clusters.length) {
    const c = summary.clusters[0];
    state.globe.setActiveMarker("c0");
    state.globe.flyTo(c.latitude, c.longitude, { altitude: 1.6 });
  }
}

function renderBatch() {
  const b = state.batch;
  if (!b) return;
  const done = b.items.filter((i) => i && i.state === "done").length;
  el.candidateCount.textContent = String(done);

  // Group by cluster once naming has happened; keep file order before that.
  const byCluster = new Map();
  const loose = [];
  b.items.forEach((item, index) => {
    if (!item) return;
    if (item.cluster != null) {
      if (!byCluster.has(item.cluster)) byCluster.set(item.cluster, []);
      byCluster.get(item.cluster).push([index, item]);
    } else {
      loose.push([index, item]);
    }
  });

  const out = [];
  for (const c of b.clusters) {
    const members = byCluster.get(c.index) || [];
    if (!members.length) continue;
    out.push(`<div class="cluster-head">
        <span class="cluster-name">${esc(c.place || "Naming…")}</span>
        <span class="cluster-meta">${c.size} · ±${esc(fmtKm(c.radius_km))}</span>
      </div>`);
    for (const [index, item] of members) out.push(batchRow(index, item));
  }
  if (loose.length) {
    if (out.length) {
      out.push(`<div class="cluster-head">
        <span class="cluster-name">Not grouped</span>
        <span class="cluster-meta">${loose.length}</span></div>`);
    }
    for (const [index, item] of loose) out.push(batchRow(index, item));
  }

  el.candidateList.innerHTML = out.join("") ||
    `<div class="empty"><p><strong>Queued</strong>Waiting to start.</p></div>`;

  for (const node of el.candidateList.querySelectorAll(".batch-row")) {
    node.addEventListener("click", () => selectBatchItem(Number(node.dataset.i)));
  }
}

function batchRow(index, item) {
  const side = item.state === "done"
    ? (item.confidence || "")
    : item.state === "failed" ? "failed"
    : item.state === "running" ? "…" : "";
  const active = state.batch.selected === index ? " active" : "";
  return `<button type="button" class="batch-row ${esc(item.state)}${active}"
      data-i="${index}" title="${esc(item.place || item.name)}">
      <span class="dot"></span>
      <span class="batch-name">${esc(item.name)}</span>
      <span class="batch-side">${esc(side)}</span>
    </button>`;
}

function selectBatchItem(index) {
  const b = state.batch;
  const item = b && b.items ? b.items[index] : null;
  if (!item) return;
  b.selected = index;
  renderBatch();

  if (item.latitude == null) {
    el.report.innerHTML = `<div class="note warn"><div><strong>${
      esc(item.name)}</strong><br>${esc(
      item.error || "No location was produced for this image.")}</div></div>`;
    return;
  }
  if (item.report) {
    renderReport(item.report);
    el.verdict.textContent = item.confidence || "—";
    el.verdict.className = `chip ${item.confidence || "bare"}`;
  }
  if (item.cluster != null) state.globe.setActiveMarker(`c${item.cluster}`);
  state.globe.flyTo(item.latitude, item.longitude, { altitude: 1.5 });
  setHudState(item.place || item.name);
}

function renderBatchSummary(summary) {
  const rows = summary.clusters.map((c, i) => `<li>
      <span class="a">${i + 1}. ${esc(c.place)}</span>
      <span class="b">${c.size} img · ±${esc(fmtKm(c.radius_km))}</span>
    </li>`).join("");

  el.report.innerHTML = block("Batch result", `
    <div class="verdict determinate">
      <p class="place">${summary.clusters.length} distinct place${
        summary.clusters.length === 1 ? "" : "s"}</p>
      <p class="place-sub">${summary.analysed} of ${summary.total} images
        analysed${summary.cancelled ? " before stopping" : ""}</p>
      <dl class="rows">
        ${row("Folder", state.batch.folder)}
        ${row("Analysed", `${summary.analysed}`)}
        ${row("Failed", summary.failed ? `${summary.failed}` : null)}
        ${row("Skipped", summary.skipped ? `${summary.skipped}` : null)}
        ${row("Time", `${summary.elapsed_s}s · ${
          summary.seconds_per_image || "—"}s per image`)}
        ${row("Geocoding requests", `${summary.geocode_requests}`)}
      </dl>
      <p class="measured">One geocoding request per distinct place, not per
        image: ${summary.geocode_requests} for ${summary.analysed} images.
        Nominatim allows one per second, so this is the difference between
        seconds and minutes.</p>
    </div>`)
    + block("Places found", `<ul class="list">${rows}</ul>`)
    + `<p class="footnote">Select any image on the left for its full
       assessment. Save report exports the whole batch.</p>`;
}

/**
 * Export the current result as a standalone HTML document.
 *
 * Rendered by the service rather than here, so the source image can be
 * inlined from the path the native dialog approved, and so the layout has
 * one implementation rather than two.
 */
async function exportHtml() {
  if (!state.report) return;
  el.exportBtn.disabled = true;
  try {
    const resp = await fetch("/api/report/html", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        report: state.report,
        path: state.sourcePath || null,
      }),
    });
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const html = await resp.text();
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const suggested = `mapify-${stamp}.html`;

    if (!nativeApi()) {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
      a.download = suggested;
      a.click();
      URL.revokeObjectURL(a.href);
      return;
    }
    const res = await state.native.save_html(html, suggested);
    if (res?.error) fail("Could not save", res.error);
    else if (res?.path) setHudState(`Report saved — ${res.path}`);
  } catch (err) {
    fail("Could not export", err.message || String(err));
  } finally {
    el.exportBtn.disabled = false;
  }
}

/* ─────────────────────────── analysis ─────────────────────────── */

async function analyse(source) {
  if (state.busy) return;
  // A deferred setup is not a refusal forever. This is the moment it
  // actually matters, so this is the moment to ask again.
  if (setupBlocks()) return;
  const name = source.name || source.file?.name || "image";
  const typeOk =
    /\.(jpe?g|png|webp)$/i.test(name) ||
    /^image\/(jpeg|png|webp)$/.test(source.file?.type || "");
  if (!typeOk) {
    fail("Unsupported file", `${name} is not a JPG, PNG or WEBP image.`);
    return;
  }

  state.busy = true;
  state.batch = null;
  state.sourcePath = source.path || null;
  // A new image means the previous answer is gone, so the globe is free
  // to turn again until this one lands.
  state.globe?.resume();
  hideFinding();
  hideAsk();
  state.session = null;
  // Every single-image analysis is also the first observation of a
  // place session, in case it turns out not to be enough on its own.
  fetch("/api/session/start", { method: "POST" }).catch(() => {});
  el.leftTitle.textContent = "Candidates";
  el.batchBar.hidden = true;
  el.openBtn.disabled = true;
  el.folderBtn.disabled = true;
  el.saveBtn.disabled = true;
  el.exportBtn.disabled = true;
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
    el.exportBtn.disabled = false;

    // A result that will not place itself is not the end of it. Only
    // offered in the desktop app, where there is a file dialog to add
    // another photograph with, and only for a file we know the path of.
    const band = report.prediction?.confidence;
    if (band === "insufficient" || band === "low") {
      showAsk({
        lede: band === "insufficient"
          ? "Nothing in this photograph places it."
          : "This photograph narrows it down but will not place it.",
        question: "Do you have another picture of the same place?",
        helps_most: [
          "a sign, a shopfront or a street name plate — anything with words on it",
          "a wider view showing the street, with traffic or parked cars in it",
          "the horizon, or whatever the tallest thing around is",
          "the ground and the buildings together, rather than one close up",
        ],
        counted: 1,
      });
    }
  } catch (err) {
    fail("Analysis failed", err.message || String(err));
  } finally {
    showProgress(false);
    state.busy = false;
    el.openBtn.disabled = false;
    el.folderBtn.disabled = false;
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
  hideFinding();
  setHudState("Idle");
}

/* ─────────────────────────── rendering ─────────────────────────── */

function render(report) {
  const pred = report.prediction || {};
  renderCandidates(report);
  renderReport(report);
  showFinding(report);

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

  /* The place name, the confidence and the coordinate are on the globe
     now, in the finding card, at a size that says they are the answer.
     Repeating them here at body-text weight was most of why the screen
     had nothing to look at first: everything was said twice, and the
     second telling was the same size as the timezone.

     So the rail's job is the detail behind the answer, and it starts
     with what the band was measured to mean -- the one part of the
     headline that needs more room than a tooltip. ------------------- */
  if (!pred.determinate || !est) {
    out.push(block("Why nothing was named", `
      <div class="verdict indeterminate">
        <p class="measured">${esc(
          CONFIDENCE_MEASURED[pred.confidence] || "")}</p>
        <div class="note risk"><div>${esc(pred.confidence_reason || "")}</div></div>
      </div>`));
  } else {
    const place = est.place || {};
    out.push(block("The location in detail", `
      <div class="verdict determinate">
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
    const verdictClass = {
      corroborated: "ok", rejected: "warn", unresolved: "risk",
    }[agree?.verdict] || "exif";
    const place = gps.place || {};
    const heading = {
      corroborated: "Embedded GPS — corroborated by the image",
      rejected: "Embedded GPS — rejected",
      unresolved: "Embedded GPS — unresolved",
    }[agree?.verdict] || "Embedded GPS — a claim, not proof";
    const lede = {
      corroborated:
        "<strong>The file carries a GPS tag, and the picture agrees with "
        + "it.</strong> The tag is the more precise of the two, so it is the "
        + "coordinate reported above.",
      rejected:
        "<strong>The file carries a GPS tag and it was rejected.</strong> "
        + "The picture points somewhere else and the image evidence is strong "
        + "enough to say which is wrong. It is still shown, because it is a "
        + "fact about the file even when it is not a fact about the place.",
      unresolved:
        "<strong>The tag disagrees with the picture and neither can be "
        + "trusted.</strong> The image evidence is too weak to adjudicate.",
    }[agree?.verdict]
      || "<strong>The file carries a GPS tag.</strong> That is what the "
       + "writer recorded, not what the picture shows, and it is trivially "
       + "editable.";

    out.push(block(heading, `
      <div class="note ${verdictClass}"><div>${lede}</div></div>
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
          <div><strong>${esc(EXIF_VERDICT[agree.verdict] ||
            (agree.consistent ? "Consistent" : "Disagreement"))}</strong>
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
