/* Mapify — the globe.
 *
 * An Earth that tries to look like a photograph rather than a map on a ball.
 * The things that get it there, roughly in order of how much they matter:
 *
 *   1. A real sun. The subsolar point is computed from a date, so there is an
 *      actual terminator in an actual place. Lighting the planet from behind
 *      the camera is what makes a globe look like a sticker.
 *   2. Atmosphere in two parts: aerial perspective on the surface, which
 *      washes the limb out and goes orange near the terminator, and a
 *      separate shell for the glow outside the silhouette, with an optical
 *      depth that falls off exponentially the way air does.
 *   3. Clouds as their own lit shell, with a soft shadow cast onto the
 *      surface and a warm edge where they catch low sun.
 *   4. Night lights with the colour of sodium lamps, occluded by cloud, and
 *      only on the dark side.
 *   5. Bloom, so the lights and the limb bleed the way a camera's do.
 *
 * Two looks: "satellite" is the above on NASA imagery; "recon" is a vector
 * Earth drawn at runtime from Natural Earth outlines, caged in orbital
 * traces, for when you want to read the geometry rather than admire it.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { DetailPatch, zoomForAltitude } from "./detail.js";

const R = 1.0;                    // globe radius in scene units
const ATMO_R = 1.13;             // atmosphere shell, in units of R
const CLOUD_R = 1.0035;
const EARTH_RADIUS_KM = 6371.0088;
const DEG = Math.PI / 180;

/* ─────────────────────────── math helpers ─────────────────────────── */

export function latLonToVec3(lat, lon, radius = R) {
  const phi = (90 - lat) * DEG;
  const theta = (lon + 180) * DEG;
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta)
  );
}

export function vec3ToLatLon(v) {
  const n = v.clone().normalize();
  const lat = 90 - Math.acos(THREE.MathUtils.clamp(n.y, -1, 1)) / DEG;
  let lon = Math.atan2(n.z, -n.x) / DEG - 180;
  while (lon < -180) lon += 360;
  while (lon > 180) lon -= 360;
  return { lat, lon };
}

/**
 * Where the sun is directly overhead, for a given instant.
 *
 * The low-precision solar position algorithm (NOAA / Astronomical Almanac).
 * Good to about a hundredth of a degree, which is several orders of magnitude
 * better than a terminator drawn on a screen needs.
 */
export function subsolarPoint(date = new Date()) {
  const jd = date.getTime() / 86400000 + 2440587.5;
  const n = jd - 2451545.0;

  const meanLon = ((280.46 + 0.9856474 * n) % 360 + 360) % 360;
  const meanAnom = (((357.528 + 0.9856003 * n) % 360 + 360) % 360) * DEG;
  const eclipticLon =
    (meanLon + 1.915 * Math.sin(meanAnom) + 0.02 * Math.sin(2 * meanAnom)) * DEG;
  const obliquity = (23.439 - 0.0000004 * n) * DEG;

  const declination = Math.asin(Math.sin(obliquity) * Math.sin(eclipticLon));

  // Equation of time, from the difference between mean and true sun.
  const rightAsc = Math.atan2(
    Math.cos(obliquity) * Math.sin(eclipticLon),
    Math.cos(eclipticLon)
  );
  let eot = meanLon * DEG - rightAsc;
  while (eot > Math.PI) eot -= 2 * Math.PI;
  while (eot < -Math.PI) eot += 2 * Math.PI;
  const eotMinutes = (4 * eot) / DEG;

  const utcHours =
    date.getUTCHours() +
    date.getUTCMinutes() / 60 +
    date.getUTCSeconds() / 3600;
  let lon = -15 * (utcHours - 12 + eotMinutes / 60);
  lon = ((lon + 540) % 360) - 180;

  return { lat: declination / DEG, lon };
}

const easeInOutCubic = (t) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

/* ───────────────────────────── shaders ───────────────────────────── */

const EARTH_VERT = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vWorldPos;
  varying vec3 vTangent;
  varying vec3 vBitangent;

  void main() {
    vUv = uv;
    vec3 n = normalize(mat3(modelMatrix) * normal);
    vNormalW = n;
    // East and north in world space. Degenerate exactly at the poles, which
    // is one vertex out of fifteen thousand.
    vec3 east = normalize(cross(vec3(0.0, 1.0, 0.0), n) + vec3(1e-6, 0.0, 0.0));
    vTangent = east;
    vBitangent = normalize(cross(n, east));
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorldPos = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const EARTH_FRAG = /* glsl */ `
  uniform sampler2D dayMap;
  uniform sampler2D nightMap;
  uniform sampler2D cloudMap;
  uniform sampler2D elevMap;
  uniform sampler2D oceanMap;

  uniform vec3  sunDir;
  uniform float hasNight;
  uniform float hasClouds;
  uniform float hasElev;
  uniform float hasOcean;
  uniform float nightGain;
  uniform float reliefGain;
  uniform float cloudShadow;
  uniform float ambient;
  uniform float specular;
  uniform float airStrength;
  uniform vec2  texel;

  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vWorldPos;
  varying vec3 vTangent;
  varying vec3 vBitangent;

  void main() {
    vec3 Ng = normalize(vNormalW);                      // geometric normal
    vec3 V  = normalize(cameraPosition - vWorldPos);
    vec3 L  = normalize(sunDir);
    vec3 N  = Ng;

    // ---- relief -------------------------------------------------------
    // Central differences on the elevation map, perturbing the normal in the
    // tangent frame. Divided by cos(latitude) because an equirectangular
    // texel covers less ground east-west as you approach the poles, and
    // without that correction Greenland grows mountains it does not have.
    if (hasElev > 0.5) {
      float hL = texture2D(elevMap, vUv - vec2(texel.x, 0.0)).r;
      float hR = texture2D(elevMap, vUv + vec2(texel.x, 0.0)).r;
      float hD = texture2D(elevMap, vUv - vec2(0.0, texel.y)).r;
      float hU = texture2D(elevMap, vUv + vec2(0.0, texel.y)).r;
      float cosLat = max(0.2, sqrt(max(0.0, 1.0 - Ng.y * Ng.y)));
      N = normalize(
        Ng
        - vTangent   * ((hR - hL) * reliefGain / cosLat)
        - vBitangent * ((hU - hD) * reliefGain)
      );
    }

    float geoNdotL = dot(Ng, L);
    float NdotL = dot(N, L);

    // ---- cloud shadow --------------------------------------------------
    // Offset the cloud lookup along the sun's direction projected into the
    // tangent plane, which is a cheap stand-in for the real parallax of a
    // cloud deck a few kilometres up.
    float shade = 1.0;
    if (hasClouds > 0.5 && cloudShadow > 0.0) {
      vec3 sunT = L - Ng * geoNdotL;
      vec2 uvOff = vec2(dot(sunT, vTangent), dot(sunT, vBitangent)) * 0.0035;
      float c = texture2D(cloudMap, vUv + uvOff).r;
      shade = 1.0 - c * cloudShadow;
    }

    vec3 albedo = texture2D(dayMap, vUv).rgb;

    // ---- diffuse -------------------------------------------------------
    // A little light wraps past the terminator, because the atmosphere
    // scatters it there.
    const float WRAP = 0.14;
    float diff = clamp((NdotL + WRAP) / (1.0 + WRAP), 0.0, 1.0);
    diff = diff * diff * (3.0 - 2.0 * diff);            // soften the falloff
    vec3 lit = albedo * (ambient + (1.0 - ambient) * diff * shade);

    // ---- sun glint off water -------------------------------------------
    float water = hasOcean > 0.5 ? texture2D(oceanMap, vUv).r : 0.0;
    vec3 H = normalize(L + V);
    float spec = pow(max(dot(Ng, H), 0.0), 1400.0);
    lit += vec3(1.0, 0.94, 0.80) * spec * water * specular
         * smoothstep(0.05, 0.40, geoNdotL) * shade;

    // ---- night side ------------------------------------------------------
    vec3 night = vec3(0.0);
    if (hasNight > 0.5) {
      vec3 raw = texture2D(nightMap, vUv).rgb;
      float lum = dot(raw, vec3(0.299, 0.587, 0.114));
      // The Black Marble composite carries a faint blue wash over water and
      // ice; keep only what is actually a light.
      raw *= smoothstep(0.06, 0.32, lum);
      night = raw * nightGain * vec3(1.25, 0.95, 0.60);
      if (hasClouds > 0.5) {
        night *= 1.0 - 0.75 * texture2D(cloudMap, vUv).r;
      }
    }
    float nightAmt = 1.0 - smoothstep(-0.20, 0.04, geoNdotL);
    vec3 color = lit + night * nightAmt;

    // ---- aerial perspective ----------------------------------------------
    // Looking through more air toward the limb: the surface washes out
    // towards sky blue, and towards orange where the sun is low.
    float limb = 1.0 - max(dot(Ng, V), 0.0);
    float airmass = pow(limb, 3.0);
    float dayAmt = smoothstep(-0.28, 0.20, geoNdotL);
    float sunset = (1.0 - smoothstep(0.02, 0.38, geoNdotL))
                 * smoothstep(-0.26, 0.03, geoNdotL);
    vec3 air = mix(vec3(0.30, 0.52, 0.92), vec3(1.00, 0.48, 0.20), sunset);
    color = mix(color, color * 0.45 + air * 0.85,
                clamp(airmass * dayAmt * airStrength, 0.0, 0.92));

    gl_FragColor = vec4(color, 1.0);
  }
`;

const CLOUD_VERT = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vWorldPos;
  void main() {
    vUv = uv;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorldPos = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const CLOUD_FRAG = /* glsl */ `
  uniform sampler2D cloudMap;
  uniform vec3  sunDir;
  uniform float opacity;
  uniform float ambient;
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vWorldPos;

  void main() {
    float c = texture2D(cloudMap, vUv).r;
    if (c < 0.02) discard;

    vec3 N = normalize(vNormalW);
    vec3 V = normalize(cameraPosition - vWorldPos);
    float NdotL = dot(N, normalize(sunDir));

    // Cloud tops are bright and scatter forward, so they stay lit a little
    // past the terminator and pick up the sunset colour there.
    float lit = clamp((NdotL + 0.22) / 1.22, 0.0, 1.0);
    lit = lit * lit * (3.0 - 2.0 * lit);
    float sunset = (1.0 - smoothstep(0.0, 0.34, NdotL))
                 * smoothstep(-0.28, 0.0, NdotL);

    vec3 tint = mix(vec3(1.0), vec3(1.0, 0.62, 0.34), sunset * 0.8);
    vec3 col = tint * (ambient + (1.0 - ambient) * lit);

    // Thicker where we look through them edge-on.
    float grazing = 1.0 - max(dot(N, V), 0.0);
    float a = c * opacity * (0.75 + 0.45 * grazing);
    // Fade out on the night side rather than leaving grey smears.
    a *= smoothstep(-0.32, -0.02, NdotL) * 0.88 + 0.12;

    gl_FragColor = vec4(col, clamp(a, 0.0, 1.0));
  }
`;

const ATMO_VERT = /* glsl */ `
  varying vec3 vWorldPos;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorldPos = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

/* The glow outside the planet's silhouette.
 *
 * A Fresnel term on a back-side sphere peaks at the shell's own edge, which
 * draws a hard ring in exactly the wrong place. Instead: work out how far the
 * fragment is from the view axis, turn that into a height above the surface,
 * and give it the exponential density profile air actually has. Near the
 * surface all wavelengths scatter, so it is pale; higher up only the short
 * ones survive, so it goes blue and then to nothing. */
const ATMO_FRAG = /* glsl */ `
  uniform vec3  sunDir;
  uniform float innerRadius;
  uniform float outerRadius;
  uniform float strength;
  varying vec3 vWorldPos;

  void main() {
    vec3 axis = normalize(cameraPosition);
    vec3 P = vWorldPos;
    float perp = length(P - axis * dot(P, axis));
    float h = clamp((perp - innerRadius) / max(outerRadius - innerRadius, 1e-4),
                    0.0, 1.0);

    // Optical depth: exponential with height, and zero at the shell edge so
    // there is nothing to see a boundary at.
    // Two layers: a dense, bright band hugging the surface, and a much
    // broader thin haze above it. One exponential alone is either a
    // hairline or a fog bank.
    float dense = exp(-h * 7.0);
    float haze  = exp(-h * 1.9) * 0.45;
    float density = (dense + haze) * (1.0 - h) * (1.0 - h);

    vec3 dir = normalize(P);
    float sunDot = dot(dir, normalize(sunDir));
    float dayside = smoothstep(-0.62, 0.10, sunDot);
    float terminator = exp(-pow(sunDot * 4.0, 2.0));

    vec3 pale   = vec3(0.68, 0.85, 1.00);
    vec3 blue   = vec3(0.16, 0.42, 1.00);
    vec3 sunset = vec3(1.00, 0.42, 0.14);

    vec3 tint = mix(pale, blue, smoothstep(0.0, 0.5, h));
    tint = mix(tint, sunset, terminator * 0.9);

    float a = density * strength * (0.06 + 0.94 * dayside) * 2.6;
    gl_FragColor = vec4(tint, clamp(a, 0.0, 1.0));
  }
`;

/* ─────────────────────────── the globe ─────────────────────────── */

export class Globe {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.opts = {
      mode: "satellite",
      flight: "cinematic",
      lighting: "target",     // "target" | "realtime" | "flat"
      bloom: true,
      textureBase: "textures/",
      countriesUrl: "data/ne_110m_countries.geojson",
      maxPixelRatio: 2,
      ...options,
    };

    this.markers = [];
    this.activeMarker = null;
    this._flight = null;
    this._idleSince = performance.now();
    this._userInteracting = false;
    this._disposed = false;
    this._geojson = null;
    this._texCache = new Map();
    this._detailAt = 0;
    this._sunTarget = new THREE.Vector3(1, 0.25, 0.4).normalize();
    this.onHudUpdate = null;
    this.texturesMissing = false;

    this._initRenderer();
    this._initScene();
    this._initComposer();
    this._initControls();
    this._bindEvents();
    this.setLighting(this.opts.lighting);
    this._animate();
  }

  /* ---------------------------------------------------------- setup */

  _initRenderer() {
    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
    this.renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, this.opts.maxPixelRatio)
    );
    this.renderer.setClearColor(0x01030a, 1);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.08;
    this.maxAnisotropy = this.renderer.capabilities.getMaxAnisotropy();
  }

  _initScene() {
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(34, 1, 0.0008, 400);
    this.camera.position.set(0.95, 0.58, 3.55);

    this.root = new THREE.Group();
    this.scene.add(this.root);

    this.sunDir = new THREE.Vector3(1, 0.25, 0.4).normalize();

    const blankDark = this._blankTexture(0x0a1626);
    const blankBlack = this._blankTexture(0x000000);
    const blankGrey = this._blankTexture(0x808080);

    this.earthUniforms = {
      dayMap: { value: blankDark },
      nightMap: { value: blankBlack },
      cloudMap: { value: blankBlack },
      elevMap: { value: blankGrey },
      oceanMap: { value: blankBlack },
      sunDir: { value: this.sunDir.clone() },
      hasNight: { value: 0 },
      hasClouds: { value: 0 },
      hasElev: { value: 0 },
      hasOcean: { value: 0 },
      nightGain: { value: 1.9 },
      reliefGain: { value: 0.0 },
      cloudShadow: { value: 0.0 },
      ambient: { value: 0.045 },
      specular: { value: 0.55 },
      airStrength: { value: 1.0 },
      texel: { value: new THREE.Vector2(1 / 8192, 1 / 4096) },
    };
    this.earthMat = new THREE.ShaderMaterial({
      vertexShader: EARTH_VERT,
      fragmentShader: EARTH_FRAG,
      uniforms: this.earthUniforms,
    });
    this.earth = new THREE.Mesh(
      new THREE.SphereGeometry(R, 256, 128),
      this.earthMat
    );
    this.root.add(this.earth);

    this.cloudUniforms = {
      cloudMap: { value: blankBlack },
      sunDir: { value: this.sunDir.clone() },
      opacity: { value: 0.92 },
      ambient: { value: 0.04 },
    };
    this.clouds = new THREE.Mesh(
      new THREE.SphereGeometry(R * CLOUD_R, 160, 96),
      new THREE.ShaderMaterial({
        vertexShader: CLOUD_VERT,
        fragmentShader: CLOUD_FRAG,
        uniforms: this.cloudUniforms,
        transparent: true,
        depthWrite: false,
      })
    );
    this.clouds.visible = false;
    this.clouds.renderOrder = 1;
    this.root.add(this.clouds);

    this.atmoUniforms = {
      sunDir: { value: this.sunDir.clone() },
      innerRadius: { value: R * 0.999 },
      outerRadius: { value: R * ATMO_R },
      strength: { value: 1.0 },
    };
    this.atmosphere = new THREE.Mesh(
      new THREE.SphereGeometry(R * ATMO_R, 160, 96),
      new THREE.ShaderMaterial({
        vertexShader: ATMO_VERT,
        fragmentShader: ATMO_FRAG,
        uniforms: this.atmoUniforms,
        side: THREE.BackSide,
        blending: THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
      })
    );
    this.atmosphere.renderOrder = 2;
    this.root.add(this.atmosphere);

    this.overlay = new THREE.Group();
    this.root.add(this.overlay);
    this.markerGroup = new THREE.Group();
    this.markerGroup.renderOrder = 4;
    this.root.add(this.markerGroup);

    this.stars = this._buildStarfield();
    this.scene.add(this.stars);

    this.detail = this.opts.tileUrl
      ? new DetailPatch(this.root, {
          tileUrl: this.opts.tileUrl,
          attribution: this.opts.tileAttribution,
          radius: R,
        })
      : null;

    this.setMode(this.opts.mode);
  }

  _initComposer() {
    if (!this.opts.bloom) {
      this.composer = null;
      return;
    }
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    // Threshold high enough that only city lights, sun glint and the bright
    // part of the limb bloom; the daylit surface must not.
    this.bloom = new UnrealBloomPass(
      new THREE.Vector2(1, 1), 0.55, 0.70, 1.05
    );
    this.composer.addPass(this.bloom);
    // OutputPass applies tone mapping and the colour-space conversion, so the
    // shaders above deliberately leave their output linear.
    this.composer.addPass(new OutputPass());
  }

  _initControls() {
    const c = new OrbitControls(this.camera, this.renderer.domElement);
    c.enableDamping = true;
    c.dampingFactor = 0.06;
    c.rotateSpeed = 0.40;
    c.zoomSpeed = 0.7;
    c.enablePan = false;
    c.minDistance = R * 1.004;
    c.maxDistance = 16;
    c.target.set(0, 0, 0);
    c.addEventListener("start", () => {
      this._userInteracting = true;
      this.cancelFlight();
    });
    c.addEventListener("end", () => {
      this._userInteracting = false;
      this._idleSince = performance.now();
    });
    this.controls = c;
  }

  _bindEvents() {
    this._onResize = () => this.resize();
    window.addEventListener("resize", this._onResize);
    this.resize();
  }

  /* --------------------------------------------------------- textures */

  _blankTexture(hex) {
    const c = document.createElement("canvas");
    c.width = c.height = 2;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#" + hex.toString(16).padStart(6, "0");
    ctx.fillRect(0, 0, 2, 2);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  async _loadTexture(url, { srgb = true } = {}) {
    if (this._texCache.has(url)) return this._texCache.get(url);
    const promise = new Promise((resolve) => {
      new THREE.TextureLoader().load(
        url,
        (tex) => {
          tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
          tex.anisotropy = Math.min(16, this.maxAnisotropy);
          tex.wrapS = THREE.RepeatWrapping;
          tex.wrapT = THREE.ClampToEdgeWrapping;
          tex.generateMipmaps = true;
          tex.minFilter = THREE.LinearMipmapLinearFilter;
          tex.needsUpdate = true;
          resolve(tex);
        },
        undefined,
        () => resolve(null)
      );
    });
    this._texCache.set(url, promise);
    return promise;
  }

  /* ------------------------------------------------------------ modes */

  async setMode(mode) {
    this.opts.mode = mode;
    if (mode === "satellite") await this._applySatellite();
    else await this._applyRecon();
  }

  async _applySatellite() {
    const base = this.opts.textureBase;
    const [day, night, clouds, elev, ocean] = await Promise.all([
      this._loadTexture(base + "earth_day.jpg"),
      this._loadTexture(base + "earth_night.jpg"),
      this._loadTexture(base + "earth_clouds.jpg", { srgb: false }),
      this._loadTexture(base + "earth_elevation.jpg", { srgb: false }),
      this._loadTexture(base + "earth_ocean_mask.jpg", { srgb: false }),
    ]);

    if (!day) {
      // Textures were never fetched. Say so, and show the vector Earth, which
      // is honest rather than a grey ball.
      this.texturesMissing = true;
      await this._applyRecon();
      return;
    }
    this.texturesMissing = false;

    const u = this.earthUniforms;
    u.dayMap.value = day;
    u.texel.value.set(1 / day.image.width, 1 / day.image.height);
    // 40,075 km of equator across the texture's width.
    this.imageryKmPerTexel = 40075.0 / day.image.width;
    this.imageryLabel = `NASA ${day.image.width}px`;
    if (night) { u.nightMap.value = night; u.hasNight.value = 1; }
    if (elev) { u.elevMap.value = elev; u.hasElev.value = 1; u.reliefGain.value = 2.4; }
    if (ocean) { u.oceanMap.value = ocean; u.hasOcean.value = 1; }
    if (clouds) {
      u.cloudMap.value = clouds;
      u.hasClouds.value = 1;
      u.cloudShadow.value = 0.55;
      this.cloudUniforms.cloudMap.value = clouds;
      this.clouds.visible = true;
    }
    u.ambient.value = 0.045;
    u.specular.value = 0.55;
    u.airStrength.value = 1.0;
    u.nightGain.value = 1.9;
    this.earthMat.needsUpdate = true;

    this.atmoUniforms.strength.value = 1.0;
    this._setOverlay(false);
    this.starMaterial.opacity = 0.55;
    if (this.bloom) { this.bloom.strength = 0.55; this.bloom.threshold = 1.05; }
  }

  async _applyRecon() {
    const tex = await this._buildVectorTexture();
    const u = this.earthUniforms;
    u.dayMap.value = tex;
    u.texel.value.set(1 / 4096, 1 / 2048);
    this.imageryKmPerTexel = 40075.0 / 4096;
    this.imageryLabel = "vector";
    u.hasNight.value = 0;
    u.hasElev.value = 0;
    u.hasClouds.value = 0;
    u.hasOcean.value = 0;
    u.cloudShadow.value = 0.0;
    u.reliefGain.value = 0.0;
    u.specular.value = 0.0;
    // A display, not a photograph: light it almost flatly so the whole planet
    // stays readable, and drop the aerial perspective that would fog it.
    u.ambient.value = 0.86;
    u.airStrength.value = 0.25;
    this.earthMat.needsUpdate = true;

    this.clouds.visible = false;
    this.atmoUniforms.strength.value = 1.15;
    this._setOverlay(true);
    this.starMaterial.opacity = 0.9;
    if (this.bloom) { this.bloom.strength = 0.6; this.bloom.threshold = 0.85; }
  }

  /** Draw the Natural Earth outlines into an equirectangular canvas. */
  async _buildVectorTexture() {
    if (this._vectorTexture) return this._vectorTexture;

    const W = 4096;
    const H = 2048;
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");

    const ocean = ctx.createLinearGradient(0, 0, 0, H);
    ocean.addColorStop(0.0, "#050f1d");
    ocean.addColorStop(0.5, "#0a2440");
    ocean.addColorStop(1.0, "#050f1d");
    ctx.fillStyle = ocean;
    ctx.fillRect(0, 0, W, H);

    ctx.strokeStyle = "rgba(90, 170, 240, 0.085)";
    ctx.lineWidth = 1.4;
    for (let lon = -180; lon <= 180; lon += 15) {
      const x = ((lon + 180) / 360) * W;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    }
    for (let lat = -75; lat <= 75; lat += 15) {
      const y = ((90 - lat) / 180) * H;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }

    const gj = await this._loadCountries();
    if (gj) {
      const project = (lon, lat) => [
        ((lon + 180) / 360) * W,
        ((90 - lat) / 180) * H,
      ];
      const tracePoly = (rings) => {
        for (const ring of rings) {
          if (ring.length < 2) continue;
          ctx.beginPath();
          for (let i = 0; i < ring.length; i++) {
            const [x, y] = project(ring[i][0], ring[i][1]);
            i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
          }
          ctx.closePath();
          ctx.fill();
          ctx.stroke();
        }
      };

      ctx.fillStyle = "rgba(9, 26, 45, 0.96)";
      ctx.strokeStyle = "rgba(102, 200, 255, 0.72)";
      ctx.lineWidth = 2.0;
      ctx.lineJoin = "round";
      ctx.shadowColor = "rgba(56, 189, 248, 0.5)";
      ctx.shadowBlur = 6;

      for (const f of gj.features) {
        const g = f.geometry;
        if (!g) continue;
        if (g.type === "Polygon") tracePoly(g.coordinates);
        else if (g.type === "MultiPolygon") {
          for (const poly of g.coordinates) tracePoly(poly);
        }
      }
      ctx.shadowBlur = 0;
    }

    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = Math.min(16, this.maxAnisotropy);
    tex.wrapS = THREE.RepeatWrapping;
    tex.needsUpdate = true;
    this._vectorTexture = tex;
    return tex;
  }

  async _loadCountries() {
    if (this._geojson !== null) return this._geojson;
    try {
      const resp = await fetch(this.opts.countriesUrl);
      this._geojson = resp.ok ? await resp.json() : false;
    } catch {
      this._geojson = false;
    }
    return this._geojson;
  }

  /* ----------------------------------------------------- decorations */

  _buildStarfield() {
    const COUNT = 6500;
    const pos = new Float32Array(COUNT * 3);
    const col = new Float32Array(COUNT * 3);
    const siz = new Float32Array(COUNT);
    const tint = new THREE.Color();
    for (let i = 0; i < COUNT; i++) {
      const u = Math.random() * 2 - 1;
      const t = Math.random() * Math.PI * 2;
      const s = Math.sqrt(1 - u * u);
      const r = 120 + Math.random() * 60;
      pos[i * 3] = r * s * Math.cos(t);
      pos[i * 3 + 1] = r * u;
      pos[i * 3 + 2] = r * s * Math.sin(t);
      // Most stars are faint; a handful are not. A uniform field looks fake.
      const mag = Math.pow(Math.random(), 3.2);
      const hue = Math.random() < 0.8 ? 0.58 : 0.08;
      tint.setHSL(hue, 0.22, 0.35 + mag * 0.6);
      col[i * 3] = tint.r; col[i * 3 + 1] = tint.g; col[i * 3 + 2] = tint.b;
      siz[i] = 0.35 + mag * 1.5;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    this.starMaterial = new THREE.PointsMaterial({
      size: 0.55,
      sizeAttenuation: true,
      vertexColors: true,
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
      map: this._dotSprite(),
      blending: THREE.AdditiveBlending,
    });
    return new THREE.Points(geo, this.starMaterial);
  }

  _dotSprite() {
    const s = 64;
    const c = document.createElement("canvas");
    c.width = c.height = s;
    const ctx = c.getContext("2d");
    const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.28, "rgba(255,255,255,0.55)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, s, s);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  _setOverlay(on) {
    if (on && this.overlay.children.length === 0) {
      const mat = new THREE.LineBasicMaterial({
        color: 0x8fd4ff,
        transparent: true,
        opacity: 0.13,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      for (let k = 0; k < 48; k++) {
        const radius = R * (1.04 + Math.random() * 0.28);
        const pts = [];
        const SEG = 200;
        for (let i = 0; i <= SEG; i++) {
          const a = (i / SEG) * Math.PI * 2;
          pts.push(new THREE.Vector3(Math.cos(a) * radius, 0, Math.sin(a) * radius));
        }
        const line = new THREE.LineLoop(
          new THREE.BufferGeometry().setFromPoints(pts), mat
        );
        line.rotation.set(
          Math.random() * Math.PI,
          Math.random() * Math.PI,
          Math.random() * Math.PI
        );
        line.userData.spin = (Math.random() - 0.5) * 0.010;
        this.overlay.add(line);
      }
    }
    this.overlay.visible = on;
  }

  /* ---------------------------------------------------------- lighting */

  /**
   * @param {"target"|"realtime"|"flat"} mode
   *   target   — sun placed for good light on the selected coordinate, with a
   *              real declination, so the terminator is still in a real shape
   *   realtime — the actual subsolar point for right now
   *   flat     — sun behind the camera; a map, not a photograph
   */
  setLighting(mode) {
    this.opts.lighting = mode;
    this._refreshSun(true);
  }

  /** Where the analysis says we are looking; used by "target" lighting. */
  setFocus(lat, lon) {
    this._focus = { lat, lon };
    if (this.opts.lighting === "target") this._refreshSun(false);
  }

  _refreshSun(snap) {
    const mode = this.opts.lighting;
    let dir;

    if (mode === "realtime") {
      const p = subsolarPoint(new Date());
      dir = latLonToVec3(p.lat, p.lon, 1).normalize();
      this.sunInfo = { ...p, mode };
    } else if (mode === "flat") {
      dir = this.camera.position.clone().normalize();
      this.sunInfo = { mode };
    } else {
      // Real declination for today, but the sun's longitude is placed so the
      // focus sits in mid-morning light: low enough to throw relief, high
      // enough to see by.
      const now = subsolarPoint(new Date());
      const focus = this._focus || { lat: 20, lon: -30 };
      const lon = ((focus.lon + 34 + 540) % 360) - 180;
      dir = latLonToVec3(now.lat, lon, 1).normalize();
      this.sunInfo = { lat: now.lat, lon, mode };
    }

    this._sunTarget.copy(dir);
    if (snap) this.sunDir.copy(dir);
  }

  /* ---------------------------------------------------------- markers */

  clearMarkers() {
    for (const m of this.markers) {
      this.markerGroup.remove(m.group);
      if (m.uncertainty) {
        this.markerGroup.remove(m.uncertainty);
        m.uncertainty.geometry.dispose();
        m.uncertainty.material.dispose();
      }
      m.group.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) {
          if (o.material.map && o.material.map.isCanvasTexture) {
            o.material.map.dispose();
          }
          o.material.dispose();
        }
      });
    }
    this.markers = [];
    this.activeMarker = null;
  }

  addMarker(spec) {
    const kind = spec.kind || "candidate";
    const palette = {
      primary: 0x4dd4ff,
      candidate: 0x7fb4d6,
      exif: 0xb98cff,
    };
    const color = palette[kind] ?? palette.candidate;
    const isPrimary = kind !== "candidate";

    const group = new THREE.Group();
    const normal = latLonToVec3(spec.lat, spec.lon, 1).normalize();

    // A thin instrument spike rather than a map pin.
    const beamLen = isPrimary ? 0.13 : 0.075;
    const beam = new THREE.Mesh(
      new THREE.CylinderGeometry(0.0009, 0.0028, beamLen, 8, 1, true),
      new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: isPrimary ? 0.9 : 0.45,
        depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false,
      })
    );
    beam.position.copy(normal).multiplyScalar(R + beamLen / 2);
    beam.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), normal);
    group.add(beam);

    const bead = new THREE.Mesh(
      new THREE.SphereGeometry(isPrimary ? 0.0088 : 0.0055, 16, 12),
      new THREE.MeshBasicMaterial({ color, toneMapped: false })
    );
    bead.position.copy(normal).multiplyScalar(R + beamLen);
    group.add(bead);

    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.011, 0.0135, 64),
      new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: 0.8, side: THREE.DoubleSide,
        depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false,
      })
    );
    ring.position.copy(normal).multiplyScalar(R * 1.0012);
    ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
    group.add(ring);

    // The uncertainty circle is a real distance on the ground, so it is kept
    // out of the group that gets scaled for constant apparent size.
    let uncertainty = null;
    if (spec.radiusKm && spec.radiusKm > 1) {
      uncertainty = this._surfaceCircle(
        spec.lat, spec.lon, spec.radiusKm, color, isPrimary ? 0.45 : 0.2
      );
      this.markerGroup.add(uncertainty);
    }

    if (spec.label) {
      const sprite = this._labelSprite(spec.label, color, isPrimary);
      sprite.position.copy(normal).multiplyScalar(R + beamLen + 0.042);
      group.add(sprite);
      group.userData.sprite = sprite;
    }

    this.markerGroup.add(group);
    const marker = {
      group, ring, bead, uncertainty, normal,
      lat: spec.lat, lon: spec.lon, kind,
      label: spec.label, id: spec.id ?? this.markers.length,
      phase: Math.random() * Math.PI * 2,
    };
    this.markers.push(marker);
    return marker;
  }

  _surfaceCircle(lat, lon, radiusKm, color, opacity) {
    const ang = Math.min(radiusKm / EARTH_RADIUS_KM, Math.PI * 0.48);
    const centre = latLonToVec3(lat, lon, 1).normalize();
    const tmp = Math.abs(centre.y) > 0.92
      ? new THREE.Vector3(1, 0, 0)
      : new THREE.Vector3(0, 1, 0);
    const e1 = new THREE.Vector3().crossVectors(tmp, centre).normalize();
    const e2 = new THREE.Vector3().crossVectors(centre, e1).normalize();

    const SEG = 160;
    const pts = [];
    for (let i = 0; i <= SEG; i++) {
      const t = (i / SEG) * Math.PI * 2;
      pts.push(
        centre.clone().multiplyScalar(Math.cos(ang))
          .addScaledVector(e1, Math.sin(ang) * Math.cos(t))
          .addScaledVector(e2, Math.sin(ang) * Math.sin(t))
          .multiplyScalar(R * 1.0008)
      );
    }
    return new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({
        color, transparent: true, opacity, depthWrite: false, toneMapped: false,
      })
    );
  }

  _labelSprite(text, color, isPrimary) {
    const dpr = 2;
    const fontSize = (isPrimary ? 30 : 25) * dpr;
    const padX = 13 * dpr;
    const padY = 7 * dpr;
    const measure = document.createElement("canvas").getContext("2d");
    const font = `600 ${fontSize}px "Inter", "Segoe UI", system-ui, sans-serif`;
    measure.font = font;
    const w = Math.ceil(measure.measureText(text).width) + padX * 2;
    const h = fontSize + padY * 2;

    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d");
    const hex = "#" + new THREE.Color(color).getHexString();

    ctx.fillStyle = "rgba(4, 8, 16, 0.82)";
    ctx.strokeStyle = hex;
    ctx.lineWidth = 1.5 * dpr;
    ctx.beginPath();
    ctx.roundRect(1, 1, w - 2, h - 2, 5 * dpr);
    ctx.fill();
    ctx.stroke();

    // A tick on the leading edge, so it reads as a callout rather than a pill.
    ctx.fillStyle = hex;
    ctx.fillRect(1, h * 0.28, 2.5 * dpr, h * 0.44);

    ctx.font = font;
    ctx.fillStyle = "#eaf3ff";
    ctx.textBaseline = "middle";
    ctx.fillText(text, padX, h / 2 + 1);

    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: tex, transparent: true, depthWrite: false, depthTest: false,
        toneMapped: false,
      })
    );
    const scale = (isPrimary ? 0.00052 : 0.00044);
    sprite.scale.set(w * scale, h * scale, 1);
    sprite.renderOrder = 10;
    return sprite;
  }

  setActiveMarker(id) {
    this.activeMarker = this.markers.find((m) => m.id === id) || null;
    for (const m of this.markers) {
      const on = m === this.activeMarker;
      if (m.group.userData.sprite) {
        m.group.userData.sprite.material.opacity = on ? 1 : 0.5;
      }
      m.bead.material.opacity = on ? 1 : 0.65;
    }
    if (this.activeMarker) {
      this.setFocus(this.activeMarker.lat, this.activeMarker.lon);
    }
  }

  /* ----------------------------------------------------------- flight */

  flyTo(lat, lon, opts = {}) {
    const mode = opts.mode || this.opts.flight;
    const target = latLonToVec3(lat, lon, 1).normalize();
    // Never fly to an altitude the imagery cannot support. The operator can
    // still zoom in by hand; the HUD says when they have gone past it.
    const endRadius = Math.max(opts.altitude ?? 1.4, this.imageryFloor());

    this.setFocus(lat, lon);

    if (mode === "direct" || opts.duration === 0) {
      this.camera.position.copy(target).multiplyScalar(endRadius);
      this.camera.lookAt(0, 0, 0);
      this.controls.update();
      this._idleSince = performance.now();
      this._flight = null;
      return Promise.resolve();
    }

    const from = this.camera.position.clone();
    const startRadius = from.length();
    const fromDir = from.clone().normalize();
    const angle = Math.acos(THREE.MathUtils.clamp(fromDir.dot(target), -1, 1));

    const base = opts.duration ?? 4600;
    const duration = Math.max(1800, base * (0.42 + angle / Math.PI));
    const apogee =
      Math.max(startRadius, endRadius) * (1.32 + 0.6 * (angle / Math.PI));

    this._flight = {
      t0: performance.now(), duration, fromDir, toDir: target, angle,
      startRadius, endRadius, apogee, resolve: null,
    };
    this.controls.enabled = false;
    return new Promise((resolve) => { this._flight.resolve = resolve; });
  }

  cancelFlight() {
    if (this._flight) {
      this._flight.resolve?.();
      this._flight = null;
      this.controls.enabled = true;
    }
  }

  _stepFlight(now) {
    const f = this._flight;
    const raw = Math.min(1, (now - f.t0) / f.duration);
    const s = easeInOutCubic(raw);

    let dir;
    if (f.angle < 1e-4) {
      dir = f.toDir.clone();
    } else {
      const sinA = Math.sin(f.angle);
      const a = Math.sin((1 - s) * f.angle) / sinA;
      const b = Math.sin(s * f.angle) / sinA;
      dir = f.fromDir.clone().multiplyScalar(a).addScaledVector(f.toDir, b)
        .normalize();
    }

    const TURN = 0.36;
    let radius;
    if (raw < TURN) {
      radius = f.startRadius +
        (f.apogee - f.startRadius) * easeInOutCubic(raw / TURN);
    } else {
      radius = f.apogee +
        (f.endRadius - f.apogee) * easeInOutCubic((raw - TURN) / (1 - TURN));
    }

    this.camera.position.copy(dir).multiplyScalar(radius);
    this.camera.lookAt(0, 0, 0);

    if (raw >= 1) {
      this._flight.resolve?.();
      this._flight = null;
      this.controls.enabled = true;
      this.controls.update();
      this._idleSince = now;
    }
  }

  /* ------------------------------------------------------------- loop */

  resize() {
    const w = this.canvas.clientWidth || this.canvas.parentElement?.clientWidth;
    const h = this.canvas.clientHeight || this.canvas.parentElement?.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.composer?.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  /**
   * The lowest altitude the current surface data actually supports.
   *
   * Descending past the point where one texel covers several screen pixels
   * just magnifies blur. With ground tiles enabled there is real detail to
   * descend into, so the floor drops away.
   */
  imageryFloor() {
    if (this.detail?.enabled) return R * 1.02;
    const kmPerTexel = this.imageryKmPerTexel || 9.8;
    const px = this.canvas.clientHeight || 700;
    // Ground width we want in view for roughly one texel per screen pixel.
    const spanKm = px * kmPerTexel;
    const halfFov = (this.camera.fov * DEG) / 2;
    const altKm = spanKm / (2 * Math.tan(halfFov));
    return R * (1 + altKm / EARTH_RADIUS_KM);
  }

  /** True when the camera is closer than the imagery can justify. */
  beyondImagery() {
    return this.camera.position.length() < this.imageryFloor() * 0.92;
  }

  /** Atmosphere strength for the current camera distance. */
  _atmosphereStrength() {
    const base = this.opts.mode === "recon" ? 1.15 : 1.0;
    const d = this.camera.position.length();
    const shell = R * ATMO_R;
    // Once the camera is inside the shell, a back-side sphere fills the
    // screen with additive haze; fade it out across the boundary.
    const outside = THREE.MathUtils.smoothstep(d, shell * 0.96, shell * 1.06);
    // Thicker as we approach, the way real limb haze deepens.
    const dive = THREE.MathUtils.clamp((3.0 - d) / 1.6, 0, 1);
    return base * (1 + dive * 0.55) * outside;
  }

  _updateDetail(now) {
    if (!this.detail || now - this._detailAt < 400) return;
    this._detailAt = now;
    const altKm = (this.camera.position.length() - R) * EARTH_RADIUS_KM;
    const target = this.activeMarker || this.markers[0];
    if (!target || altKm > 2000) { this.detail.clear(); return; }
    const zoom = zoomForAltitude(altKm);
    if (zoom <= 0) { this.detail.clear(); return; }
    this.detail.show(target.lat, target.lon, zoom);
  }

  _animate() {
    if (this._disposed) return;
    requestAnimationFrame(() => this._animate());
    const now = performance.now();
    const dt = Math.min(0.05, (now - (this._last || now)) / 1000);
    this._last = now;

    if (this._flight) {
      this._stepFlight(now);
    } else {
      const idleFor = now - this._idleSince;
      if (!this._userInteracting && idleFor > 5000) {
        const ramp = Math.min(1, (idleFor - 5000) / 3000);
        this.camera.position.applyAxisAngle(
          new THREE.Vector3(0, 1, 0), 0.014 * dt * ramp
        );
        this.camera.lookAt(0, 0, 0);
      }
      this.controls.update();
    }

    // The sun eases to its target rather than snapping, so switching lighting
    // mode or candidate reads as the planet turning into the light.
    if (this.opts.lighting === "flat") {
      this._sunTarget.copy(this.camera.position).normalize();
    } else if (this.opts.lighting === "realtime" && (this._sunTick || 0) < now) {
      this._sunTick = now + 5000;
      this._refreshSun(false);
    }
    this.sunDir.lerp(this._sunTarget, 1 - Math.pow(0.001, dt)).normalize();
    this.earthUniforms.sunDir.value.copy(this.sunDir);
    this.cloudUniforms.sunDir.value.copy(this.sunDir);
    this.atmoUniforms.sunDir.value.copy(this.sunDir);
    this.atmoUniforms.strength.value = this._atmosphereStrength();

    if (this.clouds.visible) this.clouds.rotation.y += dt * 0.0045;
    for (const line of this.overlay.children) {
      line.rotation.z += (line.userData.spin || 0) * dt;
    }

    // Markers keep a constant apparent size: their geometry is in world
    // units, so without this a ring 70 km across swallows the screen on a
    // close approach.
    const markerScale = THREE.MathUtils.clamp((d - R) / 2.2, 0.06, 1.0);
    for (const m of this.markers) {
      const k = 1 + 0.30 * Math.sin(now * 0.0026 + m.phase);
      m.group.scale.setScalar(markerScale);
      // The spike and bead sit on the surface, so the group scale would sink
      // them into it; put them back where they belong.
      m.group.position.copy(m.normal).multiplyScalar(R * (1 - markerScale));
      m.ring.scale.setScalar(k);
      m.ring.material.opacity =
        (m === this.activeMarker ? 0.9 : 0.4) * (1.3 - 0.5 * k);
    }

    this._updateDetail(now);

    // Close in, the visible surface is much brighter than a full disc, so
    // pull the exposure back the way a camera would.
    const d = this.camera.position.length();
    this.renderer.toneMappingExposure =
      THREE.MathUtils.lerp(0.88, 1.12, THREE.MathUtils.clamp((d - 1.05) / 1.6, 0, 1));

    if (this.composer) this.composer.render();
    else this.renderer.render(this.scene, this.camera);

    if (this.onHudUpdate) {
      const p = vec3ToLatLon(this.camera.position);
      const altKm = (d - R) * EARTH_RADIUS_KM;
      this.onHudUpdate(p.lat, p.lon, altKm, !!this._flight, this.sunInfo, {
        label: this.imageryLabel,
        kmPerTexel: this.imageryKmPerTexel,
        beyond: this.beyondImagery(),
        tiles: Boolean(this.detail?.enabled),
      });
    }
  }

  setFlightMode(mode) { this.opts.flight = mode; }

  setBloom(on) {
    this.opts.bloom = on;
    if (on && !this.composer) this._initComposer();
    if (!on) this.composer = null;
    this.resize();
  }

  setTileSource(url, attribution) {
    if (url && !this.detail) {
      this.detail = new DetailPatch(this.root, {
        tileUrl: url, attribution, radius: R,
      });
    } else if (!url && this.detail) {
      this.detail.clear();
      this.detail = null;
    } else if (this.detail) {
      this.detail.tileUrl = url;
      this.detail.attribution = attribution;
      this.detail.clear();
    }
  }

  dispose() {
    this._disposed = true;
    window.removeEventListener("resize", this._onResize);
    this.controls.dispose();
    this.renderer.dispose();
  }
}
