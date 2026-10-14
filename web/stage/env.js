// Adapted from PortSimEnv v1 (FineEnvs, Apache-2.0): scene-env.js
import * as THREE from "three";
import { Water } from "three/addons/objects/Water.js";

export const WATER_Y = -1.6;
const DEG = Math.PI / 180;
const COVER = { FEW: 0.2, SCT: 0.45, BKN: 0.78, OVC: 1, VV: 1 };
const POINTS = { N: 0, NNE: 22.5, NE: 45, ENE: 67.5, E: 90, ESE: 112.5, SE: 135, SSE: 157.5, S: 180, SSW: 202.5, SW: 225, WSW: 247.5, W: 270, WNW: 292.5, NW: 315, NNW: 337.5 };
const DIRS = String.raw`(ALQDS|[NSEW]{1,3}(?:-[NSEW]{1,3})?(?:\s+AND\s+[NSEW]{1,3}(?:-[NSEW]{1,3})?)*)`;
const LTG = new RegExp(String.raw`\b(?:(OCNL|FRQ|CONS)\s+)?LTG[A-Z]*\s+(?:(DSNT|VC|OHD)\s+)?` + DIRS, "g");
const CELL = new RegExp(String.raw`\b(CB|TCU|TS)\s+(?:(DSNT|VC|OHD)\s+)?` + DIRS, "g");
const STALE = 90;
const smooth = (a, b, x) => {
  const u = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return u * u * (3 - 2 * u);
};

/** Sun elevation and azimuth (degrees, azimuth clockwise from north) for a UTC instant. */
function sunPosition(utcMs, lat, lon) {
  const d = utcMs / 86400000 + 2440587.5 - 2451545.0;
  const g = (357.529 + 0.98560028 * d) * DEG;
  const q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * DEG;
  const e = (23.439 - 0.00000036 * d) * DEG;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const gmst = (18.697374558 + 24.06570982441908 * d) * 15;
  const H = (gmst + lon) * DEG - ra;
  const la = lat * DEG;
  const el = Math.asin(Math.sin(la) * Math.sin(dec) + Math.cos(la) * Math.cos(dec) * Math.cos(H));
  const az = Math.atan2(-Math.sin(H), Math.tan(dec) * Math.cos(la) - Math.sin(la) * Math.cos(H));
  const refr = el > -0.035 ? 0.0167 / Math.tan(el + 0.0031 / (el + 0.089)) : 0;
  return { el: el / DEG + refr, az: ((az / DEG) + 360) % 360 };
}

function tzOffsetMinutes(date, tz) {
  const part = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "shortOffset" })
    .formatToParts(new Date(`${date}T12:00:00Z`)).find((p) => p.type === "timeZoneName")?.value || "GMT";
  const m = part.match(/GMT([+-])(\d+)(?::(\d+))?/);
  return m ? (m[1] === "-" ? -1 : 1) * (+m[2] * 60 + +(m[3] || 0)) : 0;
}

function sectors(s) {
  if (s === "ALQDS") return [[0, 360]];
  return s.split(/\s+AND\s+/).map((part) => {
    const [a, b] = part.split("-").map((p) => POINTS[p]);
    if (b == null) return [a - 25, a + 25];
    return [a, b < a ? b + 360 : b];
  });
}

const DECODED = new WeakMap();

/** Precipitation (0 none .. 1.5 heavy), thunder at the field or near it, mist, cloud cover and the storm cells the remarks place on the horizon. */
export function decode(w) {
  if (!w) return { rain: 0, drizzle: 0, snow: 0, ts: 0, vcts: 0, mist: 0, cover: 0, overcast: 0, cells: [] };
  if (DECODED.has(w)) return DECODED.get(w);
  const out = { rain: 0, drizzle: 0, snow: 0, ts: 0, vcts: 0, mist: 0, cover: 0, overcast: 0, cells: [] };
  for (const c of w.wx || []) {
    const k = c.startsWith("+") ? 1.5 : c.startsWith("-") ? 0.5 : 1;
    if (c.startsWith("VC")) {
      if (/TS/.test(c)) out.vcts = 1;
      continue;
    }
    if (/TS/.test(c)) out.ts = 1;
    if (/RA/.test(c)) out.rain = Math.max(out.rain, /SN/.test(c) ? k * 0.5 : k);
    if (/DZ/.test(c)) out.drizzle = Math.max(out.drizzle, k);
    if (/(SN|SG|PL)/.test(c)) out.snow = Math.max(out.snow, /RA/.test(c) ? k * 0.6 : k);
    if (/(BR|FG|HZ)/.test(c)) out.mist = Math.max(out.mist, /FG/.test(c) ? 1 : 0.6);
  }
  for (const [c, ft] of w.clouds || []) {
    out.cover = Math.max(out.cover, (COVER[c] ?? 0) * (ft < 12000 ? 1 : 0.35));
    out.overcast = Math.max(out.overcast, COVER[c] ?? 0);
  }
  const rmk = (w.metar || "").split(" RMK ")[1] || "";
  const ltg = [];
  for (const m of rmk.matchAll(LTG)) for (const [a, b] of sectors(m[3])) ltg.push({ a, b, ltg: m[1] === "FRQ" || m[1] === "CONS" ? 2 : 1 });
  for (const m of rmk.matchAll(CELL)) for (const [a, b] of sectors(m[3])) out.cells.push({ a, b, cb: m[1] !== "TCU", ltg: m[1] === "TS" ? 1 : 0, far: m[2] !== "VC" && m[2] !== "OHD" });
  for (const l of ltg) {
    const c = out.cells.find((x) => x.a < l.b && x.b > l.a);
    if (c) c.ltg = Math.max(c.ltg, l.ltg);
    else out.cells.push({ ...l, cb: true, far: true });
  }
  if ((out.ts || out.vcts) && !out.cells.length) out.cells.push({ a: (w.wind_dir ?? 270) - 40, b: (w.wind_dir ?? 270) + 40, cb: true, ltg: 1, far: !out.ts });
  DECODED.set(w, out);
  return out;
}

/** The METAR in force at t, eased into the next report over the BLEND minutes before it is observed; a report older than STALE minutes with none after it lets its precipitation go. */
export function weatherAt(list, t, BLEND = 20) {
  if (!Array.isArray(list)) return list || {};
  if (!list.length) return { ...decode(null) };
  let i = 0;
  while (i + 1 < list.length && list[i + 1].t <= t) i++;
  const w = list[i];
  const n = list[i + 1];
  const fw = decode(w);
  let u = 0;
  if (n && t >= w.t && t >= n.t - BLEND) u = (t - (n.t - BLEND)) / BLEND;
  const fn = n ? decode(n) : fw;
  const fade = n || t < w.t + STALE ? 1 : Math.max(0, 1 - (t - w.t - STALE) / 30);
  const mix = (a, b) => (a == null || b == null ? (u < 0.5 ? a : b) : a + (b - a) * u);
  const base = u < 0.5 ? w : n;
  return {
    ...base,
    plain: w.plain,
    vis_sm: Math.exp(mix(Math.log(w.vis_sm ?? 10), Math.log((n || w).vis_sm ?? 10))),
    ceiling_ft: mix(w.ceiling_ft, (n || w).ceiling_ft),
    wind_kt: mix(w.wind_kt ?? 0, (n || w).wind_kt ?? 0),
    rain: mix(fw.rain, fn.rain) * fade,
    drizzle: mix(fw.drizzle, fn.drizzle) * fade,
    snow: mix(fw.snow, fn.snow) * fade,
    ts: mix(fw.ts, fn.ts) * fade,
    vcts: mix(fw.vcts, fn.vcts) * fade,
    mist: mix(fw.mist, fn.mist),
    cover: mix(fw.cover, fn.cover),
    overcast: mix(fw.overcast, fn.overcast),
    cells: (u < 0.5 ? fw : fn).cells,
    cellsK: Math.min(1, (u < 0.5 ? 1 - 2 * u : 2 * u - 1) * 1.5) * fade,
  };
}

/** Snow on the upward faces of a material while snow lies (uSnow 0..1). */
export function snowOn(mat, uSnow) {
  const prev = mat.onBeforeCompile;
  const base = mat.customProgramCacheKey();
  mat.onBeforeCompile = (sh, r) => {
    prev?.call(mat, sh, r);
    sh.uniforms.uSnowLie = uSnow;
    sh.fragmentShader = "uniform float uSnowLie;\n" + sh.fragmentShader.replace(
      "#include <normal_fragment_maps>",
      `#include <normal_fragment_maps>
      {
        float up = dot(normal, normalize((viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz));
        float lie = uSnowLie * smoothstep(0.3, 0.75, up);
        diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.88, 0.9, 0.94), lie);
      }`,
    );
  };
  mat.customProgramCacheKey = () => `${base}-snow`;
  return mat;
}

export function wetness(w) {
  if (!w) return 0;
  return Math.min(1, Math.max(w.rain ?? 0, w.ts ?? 0) * 1.4 + (w.drizzle ?? 0) * 1.2 + (w.snow ?? 0) * 0.9 + (w.mist ?? 0) * 0.5);
}

/** Lying snow 0..1 by minute: light snow covers the grass in an hour or two, flurries in good visibility hardly settle, a thaw above freezing takes it off. */
function snowCover(list, span) {
  const t0 = Math.floor(Math.min(list[0]?.t ?? span[0], span[0]));
  const n = Math.ceil(span[1] + 60 - t0);
  const out = new Float32Array(n);
  let acc = 0;
  for (let i = 0; i < n; i++) {
    const w = weatherAt(list, t0 + i);
    acc += ((w.snow || 0) * Math.min(1, 3 / Math.max(0.25, w.vis_sm ?? 10))) / 60;
    const temp = w.temp_c ?? 0;
    if (temp > 1 && (w.snow || 0) < 0.05) acc *= Math.exp(-((temp - 1) * (1 + 3 * (w.rain || 0))) / 600);
    out[i] = 1 - Math.exp(-acc / 1.4);
  }
  return (t) => out[Math.max(0, Math.min(n - 1, Math.round(t - t0)))];
}

function daylightFor(el) {
  return smooth(-10, 9, el);
}

const KEYS = [-14, -8, -4, 0, 6, 20];
const CLEAR = [
  ["#04070D", "#080C16", "#10141E", "#14161A"],
  ["#0A111E", "#18213A", "#2E3044", "#1C1F28"],
  ["#16233D", "#3D4A66", "#8A6A66", "#3F4250"],
  ["#2B4670", "#7C8DA6", "#E3A57A", "#6F7480"],
  ["#365E92", "#96B2C6", "#ECD3AE", "#899DAF"],
  ["#2369AC", "#77B1D0", "#C5D8DC", "#8AA8B8"],
];
const OVERCAST = [
  ["#0B0D11", "#0F1115", "#15161A", "#0E0F12"],
  ["#161B24", "#1D222B", "#262A33", "#171A20"],
  ["#303848", "#3C4454", "#48505E", "#2D323C"],
  ["#535B68", "#646C78", "#767C87", "#51565E"],
  ["#727982", "#858B93", "#979BA1", "#6E7277"],
  ["#8A9096", "#9EA4AA", "#B0B4B8", "#83878B"],
];
const _a = new THREE.Color();
const _b = new THREE.Color();

function paletteAt(table, el, i, out) {
  let k = 0;
  while (k < KEYS.length - 2 && el > KEYS[k + 1]) k++;
  const u = Math.min(1, Math.max(0, (el - KEYS[k]) / (KEYS[k + 1] - KEYS[k])));
  _a.set(table[k][i]);
  _b.set(table[k + 1][i]);
  return out.copy(_a).lerp(_b, u);
}

function atmosphereFor(el, cover, gloom, cityGlow = 1) {
  const day = daylightFor(el);
  const sky = {};
  ["zenith", "mid", "horizon", "below"].forEach((key, i) => {
    const c = paletteAt(CLEAR, el, i, new THREE.Color());
    c.lerp(paletteAt(OVERCAST, el, i, new THREE.Color()), cover);
    c.multiplyScalar(1 - 0.22 * gloom * day);
    sky[key] = c;
  });
  const night = 1 - smooth(-9, 1, el);
  const glow = new THREE.Color("#4F3F3C").multiplyScalar(night * cover * cityGlow * 0.42);
  sky.mid.add(glow.clone().multiplyScalar(0.55));
  sky.horizon.add(glow);
  sky.below.add(glow.clone().multiplyScalar(0.4));
  sky.zenith.add(glow.clone().multiplyScalar(0.25));
  const sunCol = new THREE.Color().setHSL(0.07 + 0.06 * smooth(0, 25, el), 0.85 - 0.5 * smooth(0, 30, el), 0.62 + 0.25 * smooth(0, 30, el));
  return {
    day,
    night,
    sky,
    sunCol,
    sunI: 3.0 * smooth(-1, 12, el) * (1 - 0.94 * cover),
    sunGlow: smooth(-8, 2, el) * (1 - smooth(4, 30, el)) * (1 - 0.6 * cover),
    hemi: 1.0 + 4.0 * Math.pow(1 - day, 1.3),
    environment: 1.0 + 0.25 * (1 - day),
    exposure: 1.05 + 0.7 * smooth(0.6, 0.1, day),
  };
}

function buildSky(radius = 30000) {
  const mat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
    toneMapped: false,
    uniforms: {
      uZenith: { value: new THREE.Color() },
      uMid: { value: new THREE.Color() },
      uHorizon: { value: new THREE.Color() },
      uBelow: { value: new THREE.Color() },
      uSun: { value: new THREE.Vector3(0, 1, 0) },
      uSunCol: { value: new THREE.Color() },
      uSunGlow: { value: 0 },
      uCover: { value: 0 },
      uDisc: { value: 1 },
      uTime: { value: 0 },
      uWind: { value: new THREE.Vector2() },
      uStars: { value: 0 },
      uFlash: { value: 0 },
      uFlashDir: { value: new THREE.Vector3(0, 1, 0) },
    },
    vertexShader: /* glsl */ `
      varying vec3 vDir;
      void main() {
        vDir = normalize(position);
        vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        gl_Position = p.xyww;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uZenith, uMid, uHorizon, uBelow, uSun, uSunCol, uFlashDir;
      uniform float uSunGlow, uCover, uDisc, uTime, uStars, uFlash;
      uniform vec2 uWind;
      varying vec3 vDir;
      float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float hash3(vec3 p) { return fract(sin(dot(p, vec3(12.9898, 78.233, 37.719))) * 43758.5453); }
      float noise(vec2 p) {
        vec2 i = floor(p), f = fract(p);
        vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
      }
      float fbm(vec2 p) {
        float v = 0.0, a = 0.5;
        for (int k = 0; k < 5; k++) { v += a * noise(p); p = p * 2.03 + vec2(17.0, 9.0); a *= 0.5; }
        return v;
      }
      void main() {
        vec3 d = normalize(vDir);
        float h = d.y;
        vec3 col;
        if (h >= 0.0) {
          col = mix(uHorizon, uMid, smoothstep(0.0, 0.18, h));
          col = mix(col, uZenith, smoothstep(0.12, 0.9, h));
        } else {
          col = mix(uHorizon, uBelow, smoothstep(0.0, 0.08, -h));
        }
        vec3 sd = normalize(uSun);
        float s = max(dot(d, sd), 0.0);
        vec2 hz = normalize(d.xz + 1e-5), sz = normalize(sd.xz + 1e-5);
        float side = pow(max(dot(hz, sz), 0.0), 2.0) * (1.0 - smoothstep(0.0, 0.35, h));
        col += uSunCol * side * uSunGlow * 0.16;
        float clouds = 0.0;
        if (h > 0.0) {
          vec2 cp = d.xz / (h + 0.12) * 1.6;
          float c = fbm(cp + vec2(3.0, 1.0) + uWind * uTime);
          float t0 = 0.74 - 0.42 * uCover;
          clouds = smoothstep(t0, t0 + 0.16, c) * smoothstep(0.015, 0.12, h) * (1.0 - smoothstep(0.85, 1.0, uCover));
          float lit = smoothstep(0.45, 0.95, fbm(cp * 1.7 + 5.0 - sd.xz * 0.6));
          vec3 cloud = mix(vec3(0.62, 0.65, 0.71), vec3(1.0, 0.99, 0.97), lit) * (uMid + uHorizon) * 0.66 + uSunCol * uSunGlow * lit * 0.25;
          col = mix(col, cloud, clouds * 0.85);
          if (uStars > 0.0) {
            vec3 cell = floor(d * 380.0);
            float r = hash3(cell);
            vec3 c3 = (cell + 0.5) / 380.0;
            float star = step(0.9965, r) * exp(-dot(d - normalize(c3), d - normalize(c3)) * 6.0e5) * (0.5 + 0.5 * hash3(cell + 7.0));
            col += vec3(0.9, 0.93, 1.0) * star * uStars * (1.0 - clouds) * (1.0 - uCover) * smoothstep(0.02, 0.2, h) * 1.6;
          }
          float base = fbm(cp * 2.6 + uWind * uTime * 1.6);
          float wisp = fbm(cp * 7.0 - uWind * uTime * 2.5);
          col *= mix(1.0, 0.86 + 0.2 * base + 0.06 * wisp, smoothstep(0.7, 1.0, uCover) * smoothstep(0.0, 0.25, h));
        }
        col += uSunCol * (smoothstep(0.999984, 0.999992, s) * 16.0 * uDisc + pow(s, 900.0) * 0.6 + pow(s, 60.0) * 0.14 + pow(s, 6.0) * 0.07) * (1.0 - uCover) * (1.0 - clouds) * step(-0.02, sd.y);
        float fl = uFlash * (0.25 + 0.75 * pow(max(dot(d, normalize(uFlashDir)), 0.0), 3.0)) * smoothstep(-0.05, 0.12, h);
        col += vec3(0.55, 0.6, 0.78) * fl;
        gl_FragColor = vec4(col, 1.0);
        #include <colorspace_fragment>
      }`,
  });
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 48, 24), mat);
  mesh.frustumCulled = false;
  mesh.renderOrder = -10;
  return mesh;
}

function copySky(dst, src) {
  for (const k of Object.keys(dst.material.uniforms)) {
    const a = dst.material.uniforms[k].value;
    const b = src.material.uniforms[k].value;
    if (a && a.copy) a.copy(b);
    else dst.material.uniforms[k].value = b;
  }
}

function skyEnvironment(pm, sky, groundColor) {
  const sc = new THREE.Scene();
  const small = buildSky(100);
  copySky(small, sky);
  small.material.uniforms.uDisc.value = 0;
  sc.add(small);
  const ground = new THREE.Mesh(new THREE.CircleGeometry(90, 24), new THREE.MeshBasicMaterial({ color: groundColor, side: THREE.DoubleSide }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -6;
  sc.add(ground);
  const target = pm.fromScene(sc, 0.04);
  small.geometry.dispose();
  small.material.dispose();
  ground.geometry.dispose();
  ground.material.dispose();
  return target;
}

function waterNormals(size = 256) {
  const h = new Float32Array(size * size);
  const waves = [];
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 90; i++) {
    const kmag = 3 + Math.pow(rnd(), 1.6) * 34;
    const ang = rnd() * Math.PI * 2;
    const kx = Math.round(Math.cos(ang) * kmag);
    const ky = Math.round(Math.sin(ang) * kmag);
    if (!kx && !ky) continue;
    waves.push({ kx, ky, a: 1 / Math.pow(Math.hypot(kx, ky), 1.15), p: rnd() * Math.PI * 2 });
  }
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let v = 0;
      for (const w of waves) v += w.a * Math.sin((2 * Math.PI * (w.kx * x + w.ky * y)) / size + w.p);
      h[y * size + x] = v;
    }
  }
  const data = new Uint8Array(size * size * 4);
  const S = 2.4;
  const n = new THREE.Vector3();
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const hx = h[y * size + ((x + 1) % size)] - h[y * size + ((x - 1 + size) % size)];
      const hy = h[((y + 1) % size) * size + x] - h[((y - 1 + size) % size) * size + x];
      n.set(-hx * S, -hy * S, 1).normalize();
      const i = (y * size + x) * 4;
      data[i] = Math.round((n.x * 0.5 + 0.5) * 255);
      data[i + 1] = Math.round((n.y * 0.5 + 0.5) * 255);
      data[i + 2] = Math.round((n.z * 0.5 + 0.5) * 255);
      data[i + 3] = 255;
    }
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.generateMipmaps = true;
  tex.needsUpdate = true;
  return tex;
}

function buildWater({ lite = false } = {}) {
  const water = new Water(new THREE.PlaneGeometry(80000, 80000), {
    textureWidth: lite ? 512 : 1024,
    textureHeight: lite ? 512 : 1024,
    waterNormals: waterNormals(),
    sunDirection: new THREE.Vector3(0, 1, 0),
    sunColor: new THREE.Color(0),
    waterColor: new THREE.Color("#2E3C3E"),
    distortionScale: 3.2,
    fog: true,
  });
  water.rotation.x = -Math.PI / 2;
  water.position.y = WATER_Y;
  water.renderOrder = -1;
  const m = water.material;
  m.uniforms.size.value = 2.6;
  m.uniforms.uRough = { value: 0 };
  m.uniforms.uRain = { value: 0 };
  m.fragmentShader = m.fragmentShader
    .replace("float rf0 = 0.3;", "float rf0 = 0.025;")
    .replace("noise.xzy * vec3( 1.5, 1.0, 1.5 )", "noise.xzy * vec3( 0.8 + uRough * 0.9, 1.0, 0.8 + uRough * 0.9 )")
    .replace(
      "vec3 diffuseLight = vec3(0.0);",
      "float dEye = length(eye - worldPosition.xyz);\n surfaceNormal = normalize(mix(surfaceNormal, vec3(0.0, 1.0, 0.0), smoothstep(400.0, 4000.0, dEye) * 0.5));\n vec3 diffuseLight = vec3(0.0);",
    )
    .replace(
      "vec3 albedo = mix( ( sunColor * diffuseLight * 0.3 + scatter ) * getShadowMask(), ( vec3( 0.1 ) + reflectionSample * 0.9 + reflectionSample * specularLight ), reflectance);",
      "vec3 albedo = mix(waterColor * (0.55 + diffuseLight * 0.65), reflectionSample * 0.92, reflectance) + specularLight * reflectance * 0.7;",
    )
    .replace("uniform vec3 waterColor;", "uniform vec3 waterColor;\nuniform float uRough;\nuniform float uRain;")
    .replace(
      "gl_FragColor = vec4( outgoingLight, alpha );",
      /* glsl */ `
      float current = sin(worldPosition.x * 0.006 + sin(worldPosition.z * 0.011) * 2.0 + time * 0.025);
      outgoingLight *= 0.97 + current * 0.03;
      outgoingLight = mix(outgoingLight, outgoingLight * vec3(0.92, 0.97, 1.0) + waterColor * 0.25, uRough * 0.5);
      vec2 wp = worldPosition.xz;
      float c1 = sin(wp.x * 0.11 + time * 1.3 + sin(wp.y * 0.06 + time * 0.7) * 3.0) * sin(wp.y * 0.19 - time * 1.1 + wp.x * 0.023);
      float caps = smoothstep(0.985 - uRough * 0.04, 0.999, c1);
      outgoingLight = mix(outgoingLight, waterColor * 2.6 + 0.04, clamp(caps, 0.0, 1.0) * uRough * 0.6);
      float pit = fract(sin(dot(floor(wp * 1.7 + time * 3.0), vec2(12.9898, 78.233))) * 43758.5453);
      outgoingLight *= 1.0 - uRain * 0.12 * step(0.93, pit);
      gl_FragColor = vec4( outgoingLight, alpha );`,
    );
  m.needsUpdate = true;
  water.userData.tick = (t) => {
    const r = m.uniforms.uRough.value;
    m.uniforms.time.value = t * (0.25 + r * 0.65);
    m.uniforms.distortionScale.value = 2.6 + r * 3.0;
  };
  return water;
}

/** update(t, wall) applies the sky, sun, shadows, haze and water for minute t and returns the state; focus(camera, target) keeps the sun's shadows on what the camera looks at. */
export function buildEnvironment(renderer, scene, stage, traffic) {
  const [lat, lon] = stage.origin;
  const [Y, M, D] = traffic.date.split("-").map(Number);
  const offset = tzOffsetMinutes(traffic.date, traffic.tz || "UTC");
  const utc = (t) => Date.UTC(Y, M - 1, D) + (t - offset) * 60000;
  const weather = traffic.weather || [];
  const snowAt = snowCover(weather, traffic.span || [0, 1440]);

  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const sky = buildSky(30000);
  scene.add(sky);
  scene.background = new THREE.Color();
  scene.fog = new THREE.FogExp2(0x888888, 0.0001);
  const water = buildWater();
  scene.add(water);
  const hemi = new THREE.HemisphereLight("#9aa3ad", "#3a3a38", 0.5);
  const sun = new THREE.DirectionalLight("#fff", 0);
  sun.position.set(0, 1000, 0);
  sun.castShadow = true;
  sun.shadow.mapSize.set(4096, 4096);
  sun.shadow.bias = -0.0003;
  sun.shadow.normalBias = 0.4;
  scene.add(hemi, sun, sun.target);

  const pm = new THREE.PMREMGenerator(renderer);
  let envTarget = null;
  let envKey = "";
  let last = null;
  const sunDir = new THREE.Vector3();
  const groundCol = new THREE.Color();
  const sunAt = (t) => sunPosition(utc(t), lat, lon);
  const daylight = (t) => daylightFor(sunAt(t).el);

  function stateAt(t) {
    const w = weatherAt(weather, t);
    const { el, az } = sunAt(t);
    const cover = w.cover ?? 0;
    const ceil = w.ceiling_ft ?? 99999;
    const wet = wetness(w);
    const gloom = Math.min(1, cover * (ceil < 1000 ? 1 : ceil < 3000 ? 0.7 : 0.4) + (wet > 0.8 ? 0.15 : 0) + 0.25 * (w.ts || 0));
    const vis = Math.max(0.1, w.vis_sm ?? 10) * 1609.34;
    const mist = Math.max(w.mist || 0, w.drizzle ? 0.6 : 0, Math.min(1, (w.snow || 0) * 0.8));
    return { t, w, el, az, cover, gloom, vis, mist, wet, snow: snowAt(t), atm: atmosphereFor(el, cover, gloom) };
  }

  function update(t, wall = 0) {
    const s = stateAt(t);
    const { atm } = s;
    const u = sky.material.uniforms;
    u.uZenith.value.copy(atm.sky.zenith);
    u.uMid.value.copy(atm.sky.mid);
    u.uHorizon.value.copy(atm.sky.horizon);
    u.uBelow.value.copy(atm.sky.below);
    sunDir.set(Math.sin(s.az * DEG) * Math.cos(s.el * DEG), Math.sin(s.el * DEG), -Math.cos(s.az * DEG) * Math.cos(s.el * DEG)).normalize();
    u.uSun.value.copy(sunDir);
    u.uSunCol.value.copy(atm.sunCol);
    u.uSunGlow.value = atm.sunGlow;
    u.uCover.value = s.cover;
    u.uStars.value = smooth(-6, -12, s.el) * (1 - 0.92 * (s.w.overcast ?? 0));
    const wind = s.w.wind_kt || 0;
    const wd = ((s.w.wind_dir || 0) + 180) * DEG;
    u.uWind.value.set(Math.sin(wd), -Math.cos(wd)).multiplyScalar(0.002 * wind);
    u.uTime.value = wall;

    const fogCol = atm.sky.horizon.clone().lerp(atm.sky.mid, 0.25).multiplyScalar(0.92 + 0.05 * s.mist);
    if (s.snow > 0 || s.w.snow) fogCol.lerp(new THREE.Color(fogCol.r, fogCol.g, fogCol.b).addScalar(0.03).multiplyScalar(1.12), Math.min(1, s.w.snow || 0));
    scene.background.copy(fogCol);
    scene.fog.color.copy(fogCol);
    scene.fog.density = (1.4 + 0.35 * s.mist) / s.vis;
    s.fogCol = fogCol;

    sun.color.copy(atm.sunCol);
    sun.intensity = atm.sunI;
    s.sunDir = sunDir;
    hemi.color.copy(atm.sky.mid);
    groundCol.copy(atm.sky.below).multiplyScalar(0.6).lerp(atm.sky.mid.clone().multiplyScalar(0.75), 0.45 * atm.day).lerp(atm.sky.mid.clone().multiplyScalar(0.9), 0.6 * s.snow);
    hemi.groundColor.copy(groundCol);
    hemi.intensity = atm.hemi * (1 + 0.25 * s.snow);
    renderer.toneMappingExposure = atm.exposure;

    const key = [atm.day.toFixed(2), s.cover.toFixed(2), s.gloom.toFixed(2), s.snow.toFixed(1)].join("|");
    if (key !== envKey) {
      envKey = key;
      if (envTarget) envTarget.dispose();
      const flash = u.uFlash.value;
      u.uFlash.value = 0;
      envTarget = skyEnvironment(pm, sky, groundCol);
      u.uFlash.value = flash;
      scene.environment = envTarget.texture;
    }
    scene.environmentIntensity = atm.environment;

    const wu = water.material.uniforms;
    wu.sunDirection.value.copy(sunDir);
    wu.sunColor.value.copy(atm.sunCol).multiplyScalar(atm.sunI * 0.35);
    wu.waterColor.value.set("#34464A").multiplyScalar(0.1 + 0.9 * atm.day * (1 - 0.35 * s.gloom)).lerp(fogCol, 0.12);
    wu.uRough.value = Math.min(1, (wind + (s.w.gust_kt || 0) * 0.3) / 30);
    wu.uRain.value = Math.min(1, (s.w.rain || 0) + (s.w.drizzle || 0) * 0.5);
    water.userData.tick(wall);
    last = s;
    return s;
  }

  const _t = new THREE.Vector3();
  function focus(camera, target) {
    const on = last && last.atm.sunI > 0.18 && last.el > 1;
    sun.castShadow = !!on;
    if (!on || !target) return;
    const r = Math.min(2600, Math.max(140, camera.position.distanceTo(target) * 0.85));
    _t.copy(target);
    _t.y = 0;
    sun.target.position.copy(_t);
    sun.position.copy(_t).addScaledVector(sunDir, 4000);
    const c = sun.shadow.camera;
    if (c.right !== r) {
      c.left = -r;
      c.right = r;
      c.top = r;
      c.bottom = -r;
      c.near = 1000;
      c.far = 7000;
      c.updateProjectionMatrix();
    }
    sun.target.updateMatrixWorld();
  }

  function flash(f) {
    const u = sky.material.uniforms;
    u.uFlash.value = f ? f.local * 1.1 + f.far * 0.3 : 0;
    if (f) u.uFlashDir.value.set(...f.dir);
  }

  return {
    update,
    focus,
    flash,
    daylight,
    weatherAt: (t) => weatherAt(weather, t),
    snowAt,
    sunAt,
    get state() {
      return last;
    },
    sky,
    water,
    sun,
    hemi,
  };
}
