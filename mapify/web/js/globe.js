/* Mapify — the globe.
 *
 * A real 3D Earth rendered with three.js (MIT): lit sphere, day/night
 * terminator, elevation relief, an animated cloud shell, Rayleigh-ish
 * atmospheric rim, and a star field. Two looks:
 *
 *   satellite — NASA Blue Marble / Black Marble imagery (public domain)
 *   recon     — a vector Earth drawn at runtime from the Natural Earth
 *               outlines, with orbital trace lines, in blue on black
 *
 * The camera flight is the point of the thing: when a location is picked we
 * pull back to orbit, slew around the planet along a great circle, then
 * descend onto the coordinate.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/OrbitControls.js";

const R = 1.0;                    // globe radius in scene units
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

const easeInOutCubic = (t) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

/* ───────────────────────────── shaders ───────────────────────────── */

const EARTH_VERT = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  void main() {
    vUv = uv;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vViewDir = normalize(cameraPosition - world.xyz);
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

/* Day/night blend with a soft terminator, relief shading derived from the
 * elevation map, a specular sheen on water, and a view-dependent rim. */
const EARTH_FRAG = /* glsl */ `
  uniform sampler2D dayMap;
  uniform sampler2D nightMap;
  uniform sampler2D elevMap;
  uniform vec3  sunDir;
  uniform vec3  rimColor;
  uniform float hasNight;
  uniform float hasElev;
  uniform float nightGain;
  uniform float reliefGain;
  uniform float ambient;
  uniform vec2  texel;

  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vViewDir;

  void main() {
    vec3 N = normalize(vNormalW);
    vec3 L = normalize(sunDir);
    float lambert = dot(N, L);

    vec3 day = texture2D(dayMap, vUv).rgb;

    // Relief: central differences on the elevation map perturb the lighting
    // so mountain ranges catch the light.
    float relief = 0.0;
    if (hasElev > 0.5) {
      float hL = texture2D(elevMap, vUv - vec2(texel.x, 0.0)).r;
      float hR = texture2D(elevMap, vUv + vec2(texel.x, 0.0)).r;
      float hD = texture2D(elevMap, vUv - vec2(0.0, texel.y)).r;
      float hU = texture2D(elevMap, vUv + vec2(0.0, texel.y)).r;
      relief = ((hR - hL) + (hU - hD)) * reliefGain;
    }

    float lit = clamp(lambert + relief, -1.0, 1.0);
    float dayAmt = smoothstep(-0.12, 0.28, lit);

    vec3 lambertColor = day * (ambient + (1.0 - ambient) * max(lit, 0.0));

    // City lights on the dark side.
    vec3 nightColor = vec3(0.0);
    if (hasNight > 0.5) {
      nightColor = texture2D(nightMap, vUv).rgb * nightGain;
      // Suppress the faint blue haze the Black Marble has over the oceans.
      float lum = dot(nightColor, vec3(0.299, 0.587, 0.114));
      nightColor *= smoothstep(0.02, 0.16, lum);
    }

    vec3 color = mix(nightColor, lambertColor, dayAmt);

    // Specular sheen where the surface is dark and smooth (i.e. water).
    float water = 1.0 - smoothstep(0.10, 0.34, dot(day, vec3(0.333)));
    vec3 H = normalize(L + normalize(vViewDir));
    float spec = pow(max(dot(N, H), 0.0), 48.0) * water * max(lambert, 0.0);
    color += vec3(0.32, 0.45, 0.62) * spec * 0.7;

    // Rim light, strongest on the sunlit limb.
    float rim = pow(1.0 - max(dot(N, normalize(vViewDir)), 0.0), 3.0);
    color += rimColor * rim * (0.30 + 0.70 * smoothstep(-0.45, 0.35, lambert));

    gl_FragColor = vec4(color, 1.0);
    #include <colorspace_fragment>
  }
`;

const ATMO_VERT = /* glsl */ `
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  void main() {
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vec4 world = modelMatrix * vec4(position, 1.0);
    vViewDir = normalize(cameraPosition - world.xyz);
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const ATMO_FRAG = /* glsl */ `
  uniform vec3  glowColor;
  uniform vec3  sunDir;
  uniform float power;
  uniform float strength;
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  void main() {
    vec3 N = normalize(vNormalW);
    // Back-side sphere, so the normal points away from the camera.
    float fres = pow(clamp(1.0 + dot(N, normalize(vViewDir)), 0.0, 1.0), power);
    float sun  = smoothstep(-0.60, 0.45, dot(-N, normalize(sunDir)));
    float a = fres * strength * (0.22 + 0.78 * sun);
    gl_FragColor = vec4(glowColor, a);
  }
`;

/* ─────────────────────────── the globe ─────────────────────────── */

export class Globe {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.opts = {
      mode: "satellite",
      flight: "cinematic",
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
    this.onHudUpdate = null;

    this._initRenderer();
    this._initScene();
    this._initControls();
    this._bindEvents();
    this._animate();
  }

  /* ---------------------------------------------------------- setup */

  _initRenderer() {
    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
      logarithmicDepthBuffer: false,
    });
    // 4K-class rendering where the display and GPU allow it, capped so a
    // laptop does not melt on a 3x-DPR screen.
    this.renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, this.opts.maxPixelRatio)
    );
    this.renderer.setClearColor(0x03050a, 1);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.06;
  }

  _initScene() {
    this.scene = new THREE.Scene();

    this.camera = new THREE.PerspectiveCamera(38, 1, 0.001, 200);
    this.camera.position.set(0, 0.55, 3.0);

    this.root = new THREE.Group();
    this.scene.add(this.root);

    this.sunDir = new THREE.Vector3(1, 0.32, 0.55).normalize();

    // Earth -------------------------------------------------------------
    this.earthUniforms = {
      dayMap: { value: this._blankTexture(0x0a1626) },
      nightMap: { value: this._blankTexture(0x000000) },
      elevMap: { value: this._blankTexture(0x808080) },
      sunDir: { value: this.sunDir.clone() },
      rimColor: { value: new THREE.Color(0x2f6fb5) },
      hasNight: { value: 0 },
      hasElev: { value: 0 },
      nightGain: { value: 1.35 },
      reliefGain: { value: 0.0 },
      ambient: { value: 0.07 },
      texel: { value: new THREE.Vector2(1 / 4096, 1 / 2048) },
    };
    this.earthMat = new THREE.ShaderMaterial({
      vertexShader: EARTH_VERT,
      fragmentShader: EARTH_FRAG,
      uniforms: this.earthUniforms,
    });
    this.earth = new THREE.Mesh(
      new THREE.SphereGeometry(R, 160, 96),
      this.earthMat
    );
    this.earth.renderOrder = 0;
    this.root.add(this.earth);

    // Clouds ------------------------------------------------------------
    this.cloudMat = new THREE.MeshLambertMaterial({
      transparent: true,
      opacity: 0.42,
      depthWrite: false,
      blending: THREE.NormalBlending,
    });
    this.clouds = new THREE.Mesh(
      new THREE.SphereGeometry(R * 1.006, 96, 64),
      this.cloudMat
    );
    this.clouds.visible = false;
    this.clouds.renderOrder = 1;
    this.root.add(this.clouds);

    this.cloudLight = new THREE.DirectionalLight(0xffffff, 2.4);
    this.cloudLight.position.copy(this.sunDir).multiplyScalar(10);
    this.scene.add(this.cloudLight);
    this.scene.add(new THREE.AmbientLight(0x30507a, 0.55));

    // Atmosphere ---------------------------------------------------------
    this.atmoUniforms = {
      glowColor: { value: new THREE.Color(0x4ea8ff) },
      sunDir: { value: this.sunDir.clone() },
      power: { value: 2.6 },
      strength: { value: 1.0 },
    };
    this.atmosphere = new THREE.Mesh(
      new THREE.SphereGeometry(R * 1.16, 96, 64),
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

    // Overlays ------------------------------------------------------------
    this.overlay = new THREE.Group();      // recon-mode orbital traces
    this.root.add(this.overlay);
    this.markerGroup = new THREE.Group();
    this.markerGroup.renderOrder = 4;
    this.root.add(this.markerGroup);

    this.stars = this._buildStarfield();
    this.scene.add(this.stars);

    this.setMode(this.opts.mode);
  }

  _initControls() {
    const c = new OrbitControls(this.camera, this.renderer.domElement);
    c.enableDamping = true;
    c.dampingFactor = 0.055;
    c.rotateSpeed = 0.42;
    c.zoomSpeed = 0.75;
    c.panSpeed = 0.4;
    c.enablePan = false;
    c.minDistance = R * 1.012;
    c.maxDistance = 14;
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
          tex.anisotropy = Math.min(
            8,
            this.renderer.capabilities.getMaxAnisotropy()
          );
          tex.wrapS = THREE.RepeatWrapping;
          tex.wrapT = THREE.ClampToEdgeWrapping;
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
    const [day, night, clouds, elev] = await Promise.all([
      this._loadTexture(base + "earth_day.jpg"),
      this._loadTexture(base + "earth_night.jpg"),
      this._loadTexture(base + "earth_clouds.jpg"),
      this._loadTexture(base + "earth_elevation.jpg", { srgb: false }),
    ]);

    if (!day) {
      // Textures were never fetched; the vector globe is the honest fallback.
      this.texturesMissing = true;
      await this._applyRecon();
      return;
    }
    this.texturesMissing = false;

    this.earthUniforms.dayMap.value = day;
    this.earthUniforms.texel.value.set(1 / day.image.width, 1 / day.image.height);
    if (night) {
      this.earthUniforms.nightMap.value = night;
      this.earthUniforms.hasNight.value = 1;
    }
    if (elev) {
      this.earthUniforms.elevMap.value = elev;
      this.earthUniforms.hasElev.value = 1;
      this.earthUniforms.reliefGain.value = 1.15;
    }
    this.earthUniforms.ambient.value = 0.075;
    this.earthUniforms.rimColor.value.setHex(0x2f6fb5);
    this.earthMat.needsUpdate = true;

    if (clouds) {
      this.cloudMat.map = clouds;
      this.cloudMat.alphaMap = clouds;
      this.cloudMat.transparent = true;
      this.cloudMat.color.setHex(0xffffff);
      this.cloudMat.opacity = 0.46;
      this.cloudMat.needsUpdate = true;
      this.clouds.visible = true;
    }

    this.atmoUniforms.glowColor.value.setHex(0x4ea8ff);
    this.atmoUniforms.strength.value = 1.0;
    this.atmoUniforms.power.value = 2.6;

    this._setOverlay(false);
    this.starMaterial.opacity = 0.85;
  }

  async _applyRecon() {
    const tex = await this._buildVectorTexture();
    this.earthUniforms.dayMap.value = tex;
    this.earthUniforms.texel.value.set(1 / 4096, 1 / 2048);
    this.earthUniforms.hasNight.value = 0;
    this.earthUniforms.hasElev.value = 0;
    this.earthUniforms.reliefGain.value = 0.0;
    // Recon mode is a display, not a photograph: light it almost flatly so
    // the whole planet stays readable.
    this.earthUniforms.ambient.value = 0.82;
    this.earthUniforms.rimColor.value.setHex(0x1d5f96);
    this.earthMat.needsUpdate = true;

    this.clouds.visible = false;
    this.atmoUniforms.glowColor.value.setHex(0x2a9df4);
    this.atmoUniforms.strength.value = 1.25;
    this.atmoUniforms.power.value = 3.1;

    this._setOverlay(true);
    this.starMaterial.opacity = 1.0;
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

    // Ocean
    const ocean = ctx.createLinearGradient(0, 0, 0, H);
    ocean.addColorStop(0.0, "#071a30");
    ocean.addColorStop(0.5, "#0d2d4e");
    ocean.addColorStop(1.0, "#071a30");
    ctx.fillStyle = ocean;
    ctx.fillRect(0, 0, W, H);

    // Graticule
    ctx.strokeStyle = "rgba(90, 170, 240, 0.10)";
    ctx.lineWidth = 1.5;
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

      ctx.fillStyle = "rgba(10, 28, 48, 0.95)";
      ctx.strokeStyle = "rgba(96, 196, 255, 0.78)";
      ctx.lineWidth = 2.1;
      ctx.lineJoin = "round";
      ctx.shadowColor = "rgba(56, 189, 248, 0.55)";
      ctx.shadowBlur = 7;

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
    tex.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
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
    const COUNT = 9000;
    const pos = new Float32Array(COUNT * 3);
    const col = new Float32Array(COUNT * 3);
    const tint = new THREE.Color();
    for (let i = 0; i < COUNT; i++) {
      // Uniform on a sphere shell well outside the camera's reach.
      const u = Math.random() * 2 - 1;
      const t = Math.random() * Math.PI * 2;
      const s = Math.sqrt(1 - u * u);
      const r = 60 + Math.random() * 40;
      pos[i * 3] = r * s * Math.cos(t);
      pos[i * 3 + 1] = r * u;
      pos[i * 3 + 2] = r * s * Math.sin(t);
      const warm = Math.random();
      tint.setHSL(warm < 0.82 ? 0.58 : 0.09, 0.28, 0.62 + Math.random() * 0.38);
      col[i * 3] = tint.r; col[i * 3 + 1] = tint.g; col[i * 3 + 2] = tint.b;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    this.starMaterial = new THREE.PointsMaterial({
      size: 0.42,
      sizeAttenuation: true,
      vertexColors: true,
      transparent: true,
      opacity: 0.9,
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
    g.addColorStop(0.35, "rgba(255,255,255,0.65)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, s, s);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  /** Orbital trace lines: the cage of arcs in the recon reference look. */
  _setOverlay(on) {
    if (on && this.overlay.children.length === 0) {
      const mat = new THREE.LineBasicMaterial({
        color: 0x9ad8ff,
        transparent: true,
        opacity: 0.16,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      for (let k = 0; k < 44; k++) {
        const radius = R * (1.05 + Math.random() * 0.30);
        const pts = [];
        const SEG = 180;
        for (let i = 0; i <= SEG; i++) {
          const a = (i / SEG) * Math.PI * 2;
          pts.push(new THREE.Vector3(Math.cos(a) * radius, 0, Math.sin(a) * radius));
        }
        const line = new THREE.LineLoop(
          new THREE.BufferGeometry().setFromPoints(pts),
          mat
        );
        line.rotation.set(
          Math.random() * Math.PI,
          Math.random() * Math.PI,
          Math.random() * Math.PI
        );
        line.userData.spin = (Math.random() - 0.5) * 0.012;
        this.overlay.add(line);
      }
    }
    this.overlay.visible = on;
  }

  /* ---------------------------------------------------------- markers */

  clearMarkers() {
    for (const m of this.markers) {
      this.markerGroup.remove(m.group);
      m.group.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) {
          if (o.material.map && o.material.map.isCanvasTexture) o.material.map.dispose();
          o.material.dispose();
        }
      });
    }
    this.markers = [];
    this.activeMarker = null;
  }

  /**
   * @param {{lat:number, lon:number, label:string, kind?:string,
   *          radiusKm?:number, rank?:number}} spec
   */
  addMarker(spec) {
    const kind = spec.kind || "candidate";
    const palette = {
      primary: 0x38bdf8,
      candidate: 0x7dd3fc,
      exif: 0xc084fc,
    };
    const color = palette[kind] ?? palette.candidate;
    const isPrimary = kind !== "candidate";

    const group = new THREE.Group();
    const surface = latLonToVec3(spec.lat, spec.lon, R);
    const normal = surface.clone().normalize();

    // Beam standing off the surface.
    const beamLen = isPrimary ? 0.16 : 0.10;
    const beam = new THREE.Mesh(
      new THREE.CylinderGeometry(0.0022, 0.0055, beamLen, 10, 1, true),
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: isPrimary ? 0.85 : 0.5,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      })
    );
    beam.position.copy(normal).multiplyScalar(R + beamLen / 2);
    beam.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), normal);
    group.add(beam);

    // Bead at the tip.
    const bead = new THREE.Mesh(
      new THREE.SphereGeometry(isPrimary ? 0.014 : 0.009, 16, 12),
      new THREE.MeshBasicMaterial({ color, toneMapped: false })
    );
    bead.position.copy(normal).multiplyScalar(R + beamLen);
    group.add(bead);

    // Pulsing ring flat on the surface.
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.016, 0.022, 48),
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0.8,
        side: THREE.DoubleSide,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      })
    );
    ring.position.copy(normal).multiplyScalar(R * 1.0015);
    ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
    group.add(ring);

    // Uncertainty circle, drawn at the true angular radius on the sphere.
    let uncertainty = null;
    if (spec.radiusKm && spec.radiusKm > 1) {
      uncertainty = this._surfaceCircle(
        spec.lat, spec.lon, spec.radiusKm, color, isPrimary ? 0.5 : 0.26
      );
      group.add(uncertainty);
    }

    // Label sprite.
    if (spec.label) {
      const sprite = this._labelSprite(spec.label, color, isPrimary);
      sprite.position.copy(normal).multiplyScalar(R + beamLen + 0.055);
      group.add(sprite);
      group.userData.sprite = sprite;
    }

    this.markerGroup.add(group);
    const marker = {
      group, ring, bead, uncertainty,
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
    // Build an orthonormal basis in the tangent plane.
    const tmp = Math.abs(centre.y) > 0.92
      ? new THREE.Vector3(1, 0, 0)
      : new THREE.Vector3(0, 1, 0);
    const e1 = new THREE.Vector3().crossVectors(tmp, centre).normalize();
    const e2 = new THREE.Vector3().crossVectors(centre, e1).normalize();

    const SEG = 128;
    const pts = [];
    for (let i = 0; i <= SEG; i++) {
      const t = (i / SEG) * Math.PI * 2;
      const p = centre.clone().multiplyScalar(Math.cos(ang))
        .addScaledVector(e1, Math.sin(ang) * Math.cos(t))
        .addScaledVector(e2, Math.sin(ang) * Math.sin(t));
      pts.push(p.multiplyScalar(R * 1.001));
    }
    return new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({
        color, transparent: true, opacity, depthWrite: false,
      })
    );
  }

  _labelSprite(text, color, isPrimary) {
    const pad = 16;
    const fontSize = isPrimary ? 44 : 34;
    const measure = document.createElement("canvas").getContext("2d");
    measure.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
    const w = Math.ceil(measure.measureText(text).width) + pad * 2;
    const h = fontSize + pad * 1.4;

    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "rgba(6, 10, 20, 0.80)";
    ctx.strokeStyle = "#" + new THREE.Color(color).getHexString();
    ctx.lineWidth = 2;
    const r = 10;
    ctx.beginPath();
    ctx.roundRect(1, 1, w - 2, h - 2, r);
    ctx.fill();
    ctx.stroke();
    ctx.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
    ctx.fillStyle = "#e7f1ff";
    ctx.textBaseline = "middle";
    ctx.fillText(text, pad, h / 2 + 1);

    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: tex, transparent: true, depthWrite: false, depthTest: false,
      })
    );
    const scale = isPrimary ? 0.00105 : 0.00085;
    sprite.scale.set(w * scale, h * scale, 1);
    sprite.renderOrder = 10;
    return sprite;
  }

  setActiveMarker(id) {
    this.activeMarker = this.markers.find((m) => m.id === id) || null;
    for (const m of this.markers) {
      const on = m === this.activeMarker;
      if (m.group.userData.sprite) {
        m.group.userData.sprite.material.opacity = on ? 1 : 0.55;
      }
      m.bead.material.opacity = on ? 1 : 0.7;
    }
  }

  /* ----------------------------------------------------------- flight */

  /**
   * Fly the camera to a coordinate.
   * @param {number} lat
   * @param {number} lon
   * @param {{altitude?:number, duration?:number, mode?:string}} opts
   */
  flyTo(lat, lon, opts = {}) {
    const mode = opts.mode || this.opts.flight;
    const target = latLonToVec3(lat, lon, 1).normalize();
    const endRadius = opts.altitude ?? 1.42;

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
    const angle = Math.acos(
      THREE.MathUtils.clamp(fromDir.dot(target), -1, 1)
    );

    // Longer slews earn a longer flight, and a higher apogee.
    const base = opts.duration ?? 4200;
    const duration = Math.max(1600, base * (0.45 + angle / Math.PI));
    const apogee = Math.max(startRadius, endRadius) * (1.35 + 0.55 * (angle / Math.PI));

    this._flight = {
      t0: performance.now(),
      duration,
      fromDir,
      toDir: target,
      angle,
      startRadius,
      endRadius,
      apogee,
      resolve: null,
    };
    this.controls.enabled = false;
    return new Promise((resolve) => {
      this._flight.resolve = resolve;
    });
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

    // Orientation: slerp along the great circle, eased both ends.
    const s = easeInOutCubic(raw);
    let dir;
    if (f.angle < 1e-4) {
      dir = f.toDir.clone();
    } else {
      const sinA = Math.sin(f.angle);
      const a = Math.sin((1 - s) * f.angle) / sinA;
      const b = Math.sin(s * f.angle) / sinA;
      dir = f.fromDir.clone().multiplyScalar(a).addScaledVector(f.toDir, b).normalize();
    }

    // Radius: climb to apogee, then descend. The descent is eased out so the
    // last part of the approach slows into the landing.
    const TURN = 0.38;
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

    // Thicken the atmosphere as we drop into it.
    const dive = THREE.MathUtils.clamp((2.2 - radius) / 1.1, 0, 1);
    this.atmoUniforms.strength.value =
      (this.opts.mode === "recon" ? 1.25 : 1.0) * (1 + dive * 0.85);

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
    const w = this.canvas.clientWidth || this.canvas.parentElement.clientWidth;
    const h = this.canvas.clientHeight || this.canvas.parentElement.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
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
      // Drift back to a slow spin once the operator stops touching it.
      const idleFor = now - this._idleSince;
      if (!this._userInteracting && idleFor > 4500) {
        const ramp = Math.min(1, (idleFor - 4500) / 2500);
        const ang = 0.018 * dt * ramp;
        this.camera.position.applyAxisAngle(new THREE.Vector3(0, 1, 0), ang);
        this.camera.lookAt(0, 0, 0);
      }
      this.controls.update();
      const dive = THREE.MathUtils.clamp(
        (2.2 - this.camera.position.length()) / 1.1, 0, 1
      );
      this.atmoUniforms.strength.value =
        (this.opts.mode === "recon" ? 1.25 : 1.0) * (1 + dive * 0.85);
    }

    // Clouds drift a little faster than the planet turns.
    if (this.clouds.visible) this.clouds.rotation.y += dt * 0.0075;
    for (const line of this.overlay.children) {
      line.rotation.z += (line.userData.spin || 0) * dt;
    }

    // Marker pulse.
    for (const m of this.markers) {
      const k = 1 + 0.22 * Math.sin(now * 0.0028 + m.phase);
      m.ring.scale.setScalar(k);
      m.ring.material.opacity =
        (m === this.activeMarker ? 0.85 : 0.45) * (1.25 - 0.45 * k);
    }

    // Keep the sun roughly behind the viewer so the subject stays lit, but
    // let it lag so the terminator visibly moves.
    const camDir = this.camera.position.clone().normalize();
    this.sunDir.lerp(
      camDir.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), -0.55)
        .add(new THREE.Vector3(0, 0.22, 0)).normalize(),
      0.02
    ).normalize();
    this.earthUniforms.sunDir.value.copy(this.sunDir);
    this.atmoUniforms.sunDir.value.copy(this.sunDir);
    this.cloudLight.position.copy(this.sunDir).multiplyScalar(10);

    this.renderer.render(this.scene, this.camera);

    if (this.onHudUpdate) {
      const p = vec3ToLatLon(this.camera.position);
      const altKm = (this.camera.position.length() - R) * EARTH_RADIUS_KM;
      this.onHudUpdate(p.lat, p.lon, altKm, !!this._flight);
    }
  }

  setFlightMode(mode) { this.opts.flight = mode; }

  dispose() {
    this._disposed = true;
    window.removeEventListener("resize", this._onResize);
    this.controls.dispose();
    this.renderer.dispose();
  }
}
