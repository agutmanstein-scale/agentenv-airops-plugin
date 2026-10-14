// Adapted from PortSimEnv v1 (FineEnvs, Apache-2.0): scene-kit.js
import * as THREE from "three";
import { WATER_Y, weatherAt, wetness } from "./env.js";

const H = { far: -0.5, land: 0, apron: 0.06, lane: 0.07, taxi: 0.1, runway: 0.14, rubber: 0.17, paint: 0.2 };
const WGS84_A = 6378137.0;
const WGS84_E2 = 6.69437999014e-3;
const smooth = (a, b, x) => {
  const u = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return u * u * (3 - 2 * u);
};
const sub = (a, b) => [a[0] - b[0], a[1] - b[1]];
const add = (a, b, k = 1) => [a[0] + b[0] * k, a[1] + b[1] * k];
const len = (a) => Math.hypot(a[0], a[1]);
const unit = (a) => {
  const l = len(a) || 1;
  return [a[0] / l, a[1] / l];
};
const perp = (d) => [-d[1], d[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1];
const dirOf = (deg) => [Math.sin((deg * Math.PI) / 180), Math.cos((deg * Math.PI) / 180)];

function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (s * 16807) % 2147483647) / 2147483647);
}

/** Non-indexed upward-facing triangles in ENU metres, uv in metres. */
class Flat {
  constructor() {
    this.p = [];
  }
  tri(a, b, c, h) {
    if ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0) [b, c] = [c, b];
    this.p.push(a[0], h, -a[1], b[0], h, -b[1], c[0], h, -c[1]);
  }
  quad(a, b, c, d, h) {
    this.tri(a, b, c, h);
    this.tri(a, c, d, h);
  }
  disc(c, r, h, n = 10) {
    for (let i = 0; i < n; i++) {
      const a0 = (i / n) * Math.PI * 2;
      const a1 = ((i + 1) / n) * Math.PI * 2;
      this.tri(c, [c[0] + Math.cos(a0) * r, c[1] + Math.sin(a0) * r], [c[0] + Math.cos(a1) * r, c[1] + Math.sin(a1) * r], h);
    }
  }
  polygon(outer, holes, h) {
    const ring = (r) => {
      const out = r.map((p) => new THREE.Vector2(p[0], p[1]));
      if (out.length > 1 && out[0].equals(out[out.length - 1])) out.pop();
      return out;
    };
    const o = ring(outer);
    if (o.length < 3) return;
    const hs = (holes || []).map(ring).filter((r) => r.length >= 3);
    const all = o.concat(...hs);
    for (const [i, j, k] of THREE.ShapeUtils.triangulateShape(o, hs)) {
      this.tri([all[i].x, all[i].y], [all[j].x, all[j].y], [all[k].x, all[k].y], h);
    }
  }
  /** A rectangle in a runway frame: origin o, unit direction d, along a0..a1, across c0..c1 (left positive). */
  rect(o, d, a0, a1, c0, c1, h) {
    const p = perp(d);
    const at = (a, c) => [o[0] + d[0] * a + p[0] * c, o[1] + d[1] * a + p[1] * c];
    this.quad(at(a0, c0), at(a1, c0), at(a1, c1), at(a0, c1), h);
  }
  thick(line, w, h, caps = true) {
    for (let i = 0; i + 1 < line.length; i++) {
      const a = line[i];
      const b = line[i + 1];
      const l = len(sub(b, a));
      if (l < 0.01) continue;
      this.rect(a, unit(sub(b, a)), 0, l, -w / 2, w / 2, h);
    }
    for (let i = caps ? 0 : 1; i < (caps ? line.length : line.length - 1); i++) this.disc(line[i], w / 2, h, 12);
  }
  thin(line, w, h) {
    const n = line.length;
    if (n < 2) return;
    const off = [];
    for (let i = 0; i < n; i++) {
      const d0 = i > 0 ? unit(sub(line[i], line[i - 1])) : null;
      const d1 = i < n - 1 ? unit(sub(line[i + 1], line[i])) : null;
      const n0 = d0 ? perp(d0) : perp(d1);
      const n1 = d1 ? perp(d1) : n0;
      let m = unit(add(n0, n1));
      const k = Math.min(2, 1 / Math.max(0.3, dot(m, n1)));
      if (!isFinite(m[0])) m = n1;
      off.push([m[0] * k * w / 2, m[1] * k * w / 2]);
    }
    for (let i = 0; i + 1 < n; i++) {
      const a = line[i];
      const b = line[i + 1];
      this.quad(sub(a, off[i]), sub(b, off[i + 1]), add(b, off[i + 1]), add(a, off[i]), h);
    }
  }
  geometry() {
    const pos = new Float32Array(this.p);
    const n = pos.length / 3;
    const nor = new Float32Array(n * 3);
    const uv = new Float32Array(n * 2);
    for (let i = 0; i < n; i++) {
      nor[i * 3 + 1] = 1;
      uv[i * 2] = pos[i * 3];
      uv[i * 2 + 1] = -pos[i * 3 + 2];
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    g.computeBoundingSphere();
    return g;
  }
}

function sample(line, step) {
  const out = [];
  let carry = 0;
  for (let i = 0; i + 1 < line.length; i++) {
    const a = line[i];
    const v = sub(line[i + 1], a);
    const l = len(v);
    if (l < 1e-6) continue;
    const d = [v[0] / l, v[1] / l];
    let s = carry;
    for (; s <= l; s += step) out.push({ p: add(a, d, s), d });
    carry = s - l;
  }
  const last = line[line.length - 1];
  if (out.length && len(sub(out[out.length - 1].p, last)) > step * 0.3) out.push({ p: last, d: out[out.length - 1].d });
  return out;
}

function runs(samples, keep) {
  const out = [];
  let cur = [];
  for (const s of samples) {
    if (keep(s)) cur.push(s);
    else {
      if (cur.length > 1) out.push(cur);
      cur = [];
    }
  }
  if (cur.length > 1) out.push(cur);
  return out;
}

/* ---------------- textures ---------------- */

function canvas(w, h) {
  const cv = document.createElement("canvas");
  cv.width = w;
  cv.height = h;
  return [cv, cv.getContext("2d", { willReadFrequently: true })];
}

function tex(cv, { repeat = true, srgb = true } = {}) {
  const t = new THREE.CanvasTexture(cv);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  return t;
}

function grain(g, w, h, amp, random) {
  const img = g.getImageData(0, 0, w, h);
  for (let i = 0; i < img.data.length; i += 4) {
    const n = (random() - 0.5) * amp;
    img.data[i] += n;
    img.data[i + 1] += n;
    img.data[i + 2] += n;
  }
  g.putImageData(img, 0, 0);
}

function concreteTexture() {
  const random = rng(97);
  const [cv, g] = canvas(512, 512);
  g.fillStyle = "#B9B7B1";
  g.fillRect(0, 0, 512, 512);
  grain(g, 512, 512, 14, random);
  for (let i = 0; i < 16; i++) {
    const x = Math.floor(random() * 4) * 128;
    const y = Math.floor(random() * 4) * 128;
    g.fillStyle = `rgba(${random() < 0.5 ? "120,115,105" : "200,198,190"},${0.05 + random() * 0.08})`;
    g.fillRect(x, y, 128, 128);
  }
  for (let i = 0; i < 70; i++) {
    g.fillStyle = `rgba(70,64,56,${0.02 + random() * 0.06})`;
    g.beginPath();
    g.ellipse(random() * 512, random() * 512, 8 + random() * 50, 5 + random() * 26, random() * 3, 0, 7);
    g.fill();
  }
  g.strokeStyle = "rgba(50,46,42,0.035)";
  for (let i = 0; i < 10; i++) {
    g.lineWidth = 6 + random() * 10;
    const y = random() * 512;
    g.beginPath();
    g.moveTo(0, y);
    g.bezierCurveTo(170, y + (random() - 0.5) * 80, 340, y + (random() - 0.5) * 80, 512, y + (random() - 0.5) * 40);
    g.stroke();
  }
  g.strokeStyle = "rgba(48,44,40,0.38)";
  g.lineWidth = 1.2;
  for (let k = 0; k <= 512; k += 128) {
    g.beginPath();
    g.moveTo(k, 0);
    g.lineTo(k, 512);
    g.moveTo(0, k);
    g.lineTo(512, k);
    g.stroke();
  }
  const t = tex(cv);
  t.repeat.set(1 / 20, 1 / 20);
  return t;
}

function asphaltTexture(base, seed, size = 24) {
  const random = rng(seed);
  const [cv, g] = canvas(512, 512);
  g.fillStyle = base;
  g.fillRect(0, 0, 512, 512);
  grain(g, 512, 512, 26, random);
  for (let i = 0; i < 50; i++) {
    g.fillStyle = `rgba(${random() < 0.6 ? "30,28,26" : "150,146,140"},${0.03 + random() * 0.06})`;
    g.beginPath();
    g.ellipse(random() * 512, random() * 512, 10 + random() * 60, 6 + random() * 30, random() * 3, 0, 7);
    g.fill();
  }
  g.strokeStyle = "rgba(20,20,20,0.25)";
  for (let i = 0; i < 6; i++) {
    g.lineWidth = 1 + random();
    let x = random() * 512;
    let y = random() * 512;
    g.beginPath();
    g.moveTo(x, y);
    for (let k = 0; k < 8; k++) g.lineTo((x += (random() - 0.5) * 60), (y += (random() - 0.5) * 60));
    g.stroke();
  }
  const t = tex(cv);
  t.repeat.set(1 / size, 1 / size);
  return t;
}

function puddleTexture() {
  const random = rng(31);
  const [cv, g] = canvas(256, 256);
  g.fillStyle = "#000";
  g.fillRect(0, 0, 256, 256);
  g.filter = "blur(7px)";
  for (let i = 0; i < 26; i++) {
    const x = random() * 256;
    const y = random() * 256;
    const rx = 8 + random() * 30;
    const ry = 5 + random() * 18;
    const r = random() * 3;
    g.fillStyle = `rgba(255,255,255,${0.45 + random() * 0.5})`;
    for (const [dx, dy] of [[0, 0], [256, 0], [-256, 0], [0, 256], [0, -256]]) {
      g.beginPath();
      g.ellipse(x + dx, y + dy, rx, ry, r, 0, 7);
      g.fill();
    }
  }
  g.filter = "none";
  return tex(cv, { srgb: false });
}

function rubberTexture() {
  const random = rng(53);
  const [cv, g] = canvas(256, 1024);
  g.fillStyle = "#000";
  g.fillRect(0, 0, 256, 1024);
  for (let i = 0; i < 1500; i++) {
    const x = 128 + (random() + random() + random() - 1.5) * 120;
    const y0 = random() * 1024;
    const l = 30 + random() * 260;
    const a = (0.12 + random() * 0.26) * (1 - Math.min(1, Math.abs(x - 128) / 130));
    g.strokeStyle = `rgba(255,255,255,${a})`;
    g.lineWidth = 1 + random() * 3;
    g.beginPath();
    g.moveTo(x, y0);
    g.lineTo(x + (random() - 0.5) * 6, y0 + l);
    g.stroke();
  }
  const fade = g.createLinearGradient(0, 0, 0, 1024);
  fade.addColorStop(0, "rgba(0,0,0,1)");
  fade.addColorStop(0.18, "rgba(0,0,0,0)");
  fade.addColorStop(0.55, "rgba(0,0,0,0)");
  fade.addColorStop(1, "rgba(0,0,0,1)");
  g.globalCompositeOperation = "source-over";
  g.fillStyle = fade;
  g.fillRect(0, 0, 256, 1024);
  const t = tex(cv, { repeat: false, srgb: false });
  return t;
}

/** Runway designators, one cell per runway end: number above, letter nearest the threshold. */
function designatorAtlas(ids) {
  const cw = 256;
  const ch = 640;
  const [cv, g] = canvas(cw * ids.length, ch);
  g.clearRect(0, 0, cv.width, ch);
  g.fillStyle = "#fff";
  g.textAlign = "center";
  g.textBaseline = "alphabetic";
  ids.forEach((id, i) => {
    const m = id.match(/^(\d+)([LCR]?)$/) || [null, id, ""];
    const x = i * cw + cw / 2;
    g.save();
    g.translate(x, 0);
    g.scale(0.62, 1);
    g.font = "900 300px 'DIN Condensed', 'Arial Narrow', 'Helvetica Neue', Arial, sans-serif";
    g.fillText(m[1], 0, 290);
    if (m[2]) g.fillText(m[2], 0, 620);
    g.restore();
  });
  const t = tex(cv, { repeat: false });
  t.generateMipmaps = true;
  return { t, n: ids.length };
}

/* ---------------- shared ground shading: wetness, apron light pools, city glow ---------------- */

function patchGround(mat, U, { paved = 1, winter = false, city = 0 } = {}) {
  mat.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, U);
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vGW;")
      .replace("#include <project_vertex>", "#include <project_vertex>\nvGW = (modelMatrix * vec4(transformed, 1.0)).xyz;");
    sh.fragmentShader = sh.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
        varying vec3 vGW;
        uniform float uWet, uPool, uCity;
        uniform sampler2D uPuddle, uLightMap;
        uniform vec4 uLightM;
        float gh(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }`,
      )
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>
        vec2 gxy = vec2(vGW.x, -vGW.z);
        float puddle = texture2D(uPuddle, gxy / 41.0).r * texture2D(uPuddle, gxy / 13.0 + 0.37).r;
        float pv = 0.0;
        ${winter ? `{
          vec3 c = diffuseColor.rgb;
          float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
          float green = clamp((c.g - max(c.r, c.b)) * 9.0, 0.0, 1.0);
          pv = smoothstep(0.16, 0.42, l) * (1.0 - green);
          c = mix(c, vec3(l * 1.08, l * 0.98, l * 0.78), green * 0.8);
          c = mix(vec3(l), c, 0.62);
          c = mix(c, vec3(l) * 0.5 + 0.04, pv * 0.7);
          diffuseColor.rgb = c * 0.86;
        }` : ""}
        float wetK = uWet * max(${paved.toFixed(2)}, pv * 0.85);
        diffuseColor.rgb *= 1.0 - wetK * (0.32 + 0.12 * puddle);`,
      )
      .replace(
        "#include <roughnessmap_fragment>",
        `#include <roughnessmap_fragment>
        roughnessFactor = mix(roughnessFactor, mix(0.3, 0.1, puddle), wetK);`,
      )
      .replace(
        "#include <emissivemap_fragment>",
        `#include <emissivemap_fragment>
        vec2 luv = (gxy - uLightM.xy) * uLightM.zw;
        float inL = step(0.0, luv.x) * step(luv.x, 1.0) * step(0.0, luv.y) * step(luv.y, 1.0);
        totalEmissiveRadiance += diffuseColor.rgb * texture2D(uLightMap, luv).rgb * uPool * inL * (1.0 + wetK * 0.6);
        ${city ? `{
          float lum = dot(diffuseColor.rgb, vec3(0.3, 0.55, 0.15));
          float road = smoothstep(0.10, 0.26, lum) * (1.0 - inL);
          vec2 cell = floor(gxy / 7.0);
          vec2 fc = fract(gxy / 7.0) - 0.5;
          float lamp = step(0.78, gh(cell)) * exp(-dot(fc, fc) * 30.0);
          vec3 tint = mix(vec3(1.0, 0.62, 0.30), vec3(1.0, 0.86, 0.70), step(0.5, gh(cell + 7.0)));
          totalEmissiveRadiance += tint * road * uCity * ${city.toFixed(2)} * (0.05 + 2.2 * lamp);
        }` : ""}`,
      );
  };
  mat.customProgramCacheKey = () => `ground-${paved}-${winter}-${city}`;
  return mat;
}

/* ---------------- lights ---------------- */

export class Lamps {
  constructor() {
    this.pos = [];
    this.col = [];
    this.col2 = [];
    this.dir = [];
    this.size = [];
    this.anim = [];
  }
  add(p, h, { col, col2 = col, dir = null, directional = 0, size = 0.6, intensity = 4, kind = 0, phase = 0 }) {
    const c1 = new THREE.Color(col).multiplyScalar(intensity);
    const c2 = new THREE.Color(col2).multiplyScalar(intensity);
    this.pos.push(p[0], h, -p[1]);
    this.col.push(c1.r, c1.g, c1.b);
    this.col2.push(c2.r, c2.g, c2.b);
    this.dir.push(dir ? dir[0] : 0, directional, dir ? -dir[1] : 0);
    this.size.push(size);
    this.anim.push(kind, phase);
  }
  get count() {
    return this.size.length;
  }
}

const LAMP_VERT = /* glsl */ `
  uniform float uScale, uTime, uOn, uHalo, uGain;
  attribute vec3 aCol, aCol2, aDir;
  attribute float aSize;
  attribute vec2 aAnim;
  varying vec3 vCol;
  varying float vCore;
  uniform float fogDensity;
  #include <common>
  #include <logdepthbuf_pars_vertex>
  void main() {
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vec4 mvPosition = viewMatrix * wp;
    gl_Position = projectionMatrix * mvPosition;
    float dist = max(-mvPosition.z, 0.5);
    vec3 toCam = cameraPosition - wp.xyz;
    vec2 tc = normalize(toCam.xz + 1e-4);
    vec3 col = aCol;
    float k = 1.0;
    float dl = length(aDir.xz);
    if (aAnim.x > 2.5) {
      float ang = degrees(atan(toCam.y, length(toCam.xz)));
      col = ang > aAnim.y ? aCol2 : aCol;
    }
    if (dl > 0.0) {
      float f = dot(tc, aDir.xz / dl);
      if (aAnim.x < 2.5) col = f >= 0.0 ? aCol : aCol2;
      k = mix(1.0, smoothstep(-0.05, 1.0, abs(f)), aDir.y);
    }
    if (aAnim.x > 0.5 && aAnim.x < 1.5) {
      float ph = fract(uTime * 2.0) - aAnim.y;
      k *= 0.04 + 3.0 * exp(-ph * ph * 900.0);
    } else if (aAnim.x > 1.5 && aAnim.x < 2.5) {
      k *= 0.1 + 0.9 * smoothstep(0.35, 0.5, abs(fract(uTime * 0.8 + aAnim.y) - 0.5) * 2.0);
    }
    float px = aSize * uScale / dist;
    float far = pow(clamp(px / 0.8, 0.08, 1.0), 0.75);
    float spr = clamp(max(px * 4.0 * uHalo, 3.0 + 2.0 * uHalo), 3.0, 120.0);
    gl_PointSize = spr;
    vCore = clamp(max(px, 0.9) / spr, 0.02, 1.0);
    float fogF = 1.0 - exp(-fogDensity * fogDensity * dist * dist * 0.55);
    vCol = col * k * far * uOn * uGain * (1.0 - fogF * 0.92);
    #include <logdepthbuf_vertex>
  }`;

const LAMP_FRAG = /* glsl */ `
  uniform float uHalo;
  varying vec3 vCol;
  varying float vCore;
  #include <common>
  #include <logdepthbuf_pars_fragment>
  void main() {
    #include <logdepthbuf_fragment>
    vec2 c = gl_PointCoord - 0.5;
    float r = length(c) * 2.0;
    if (r > 1.0) discard;
    float core = exp(-r * r / (vCore * vCore * 0.5));
    float halo = exp(-r * 4.5) * 0.10 * uHalo * (1.0 - r);
    gl_FragColor = vec4(vCol * (core + halo), 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }`;

export function lampUniforms() {
  return { uTime: { value: 0 }, uOn: { value: 1 }, uHalo: { value: 1 }, uGain: { value: 1 } };
}

/** Airfield-style lights as additive point sprites: steady, sequenced flasher (kind 1), wig-wag (2) or PAPI (3). */
export function lampPoints(lamps, U = lampUniforms()) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(lamps.pos, 3));
  g.setAttribute("aCol", new THREE.Float32BufferAttribute(lamps.col, 3));
  g.setAttribute("aCol2", new THREE.Float32BufferAttribute(lamps.col2, 3));
  g.setAttribute("aDir", new THREE.Float32BufferAttribute(lamps.dir, 3));
  g.setAttribute("aSize", new THREE.Float32BufferAttribute(lamps.size, 1));
  g.setAttribute("aAnim", new THREE.Float32BufferAttribute(lamps.anim, 2));
  g.computeBoundingSphere();
  const m = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uScale: { value: 800 } }]),
    vertexShader: LAMP_VERT,
    fragmentShader: LAMP_FRAG,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    fog: true,
  });
  for (const k of ["uTime", "uOn", "uHalo", "uGain"]) m.uniforms[k] = U[k];
  const pts = new THREE.Points(g, m);
  pts.frustumCulled = false;
  pts.renderOrder = 5;
  const v = new THREE.Vector2();
  pts.onBeforeRender = (renderer, scene, camera) => {
    const rt = renderer.getRenderTarget();
    const h = rt ? rt.height : renderer.getDrawingBufferSize(v).y;
    m.uniforms.uScale.value = camera.projectionMatrix.elements[5] * h * 0.5;
  };
  return pts;
}

/** Wet-pavement reflections: a streak on the ground under each light, stretched toward the viewer. */
function lampStreaks(items, U) {
  const base = new THREE.PlaneGeometry(2, 1, 1, 1);
  base.translate(0, 0.5, 0);
  const g = new THREE.InstancedBufferGeometry();
  g.index = base.index;
  g.setAttribute("position", base.getAttribute("position"));
  const b = [];
  const c = [];
  for (const it of items) {
    b.push(it.p[0], it.h, -it.p[1]);
    const col = new THREE.Color(it.col).multiplyScalar(it.intensity);
    c.push(col.r, col.g, col.b);
  }
  g.setAttribute("aBase", new THREE.InstancedBufferAttribute(new Float32Array(b), 3));
  g.setAttribute("aCol", new THREE.InstancedBufferAttribute(new Float32Array(c), 3));
  g.instanceCount = items.length;
  const m = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uY: { value: H.paint + 0.04 } }]),
    vertexShader: /* glsl */ `
      uniform float uY, uWetStreak;
      attribute vec3 aBase, aCol;
      varying vec3 vCol;
      varying vec2 vUv;
      uniform float fogDensity;
      #include <common>
      #include <logdepthbuf_pars_vertex>
      void main() {
        vec2 toCam = cameraPosition.xz - aBase.xz;
        float D = max(length(toCam), 1.0);
        vec2 dir = toCam / D;
        vec2 side = vec2(-dir.y, dir.x);
        float h = aBase.y;
        float camH = max(cameraPosition.y, 0.5);
        float s = D * h / (h + camH);
        float L = min(0.3 * s + 2.5 + h * 0.6, (D - s) * 1.1 + 2.0);
        float w = 0.2 + h * 0.012 + D * 0.0005;
        float along = s + (position.y - 0.45) * L;
        vec2 p = aBase.xz + dir * along + side * position.x * w;
        vec4 mvPosition = viewMatrix * vec4(p.x, uY, p.y, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        vUv = vec2(position.x, position.y);
        float fogF = 1.0 - exp(-fogDensity * fogDensity * D * D * 0.55);
        vCol = aCol * uWetStreak * (1.0 - fogF) * smoothstep(15.0, 160.0, D) * (0.25 + 0.75 * smoothstep(1.5, 10.0, camH));
        #include <logdepthbuf_vertex>
      }`,
    fragmentShader: /* glsl */ `
      varying vec3 vCol;
      varying vec2 vUv;
      #include <common>
      #include <logdepthbuf_pars_fragment>
      void main() {
        #include <logdepthbuf_fragment>
        float x = abs(vUv.x);
        float a = exp(-x * x * 9.0) * exp(-pow((vUv.y - 0.4) * 2.4, 2.0)) * smoothstep(0.0, 0.12, vUv.y);
        float ripple = 0.7 + 0.3 * sin(vUv.y * 53.0 + vUv.x * 5.0) * sin(vUv.y * 17.0);
        gl_FragColor = vec4(vCol * a * ripple, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    fog: true,
  });
  m.uniforms.uWetStreak = U.uWetStreak;
  const mesh = new THREE.Mesh(g, m);
  mesh.frustumCulled = false;
  mesh.renderOrder = 4;
  return mesh;
}

/* ---------------- far field: low-zoom USGS photo beyond the stage's own ortho ---------------- */

function enuProjector(lat0, lon0) {
  const s = Math.sin((lat0 * Math.PI) / 180);
  const w = 1 - WGS84_E2 * s * s;
  const my = (Math.PI / 180) * WGS84_A * (1 - WGS84_E2) / Math.pow(w, 1.5);
  const mx = ((Math.PI / 180) * WGS84_A) / Math.sqrt(w) * Math.cos((lat0 * Math.PI) / 180);
  return { lonlat: (x, y) => [lon0 + x / mx, lat0 + y / my] };
}

const tileXY = (lon, lat, z) => {
  const n = 2 ** z;
  const r = (lat * Math.PI) / 180;
  return [((lon + 180) / 360) * n, ((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * n];
};

function loadImage(url) {
  return new Promise((resolve) => {
    const im = new Image();
    im.crossOrigin = "anonymous";
    im.onload = () => resolve(im);
    im.onerror = () => resolve(null);
    im.src = url;
  });
}

const USGS = "https://basemap.nationalmap.gov/arcgis/rest/services";

/** USGS imagery tiles around the origin, water cut out by the USGS hydrography tiles; hole is left to finer layers. */
async function farField(stage, U, { z, n, half, y, hole, timeout = 20000 }) {
  const [lat0, lon0] = stage.origin;
  const proj = enuProjector(lat0, lon0);
  const [cx, cy] = tileXY(lon0, lat0, z).map(Math.floor);
  const tx0 = cx - (n >> 1);
  const ty0 = cy - (n >> 1);
  const [cv, g] = canvas(n * 256, n * 256);
  const [wcv, wg] = canvas(n * 256, n * 256);
  g.fillStyle = "#000";
  g.fillRect(0, 0, cv.width, cv.height);
  const jobs = [];
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const at = `tile/${z}/${ty0 + j}/${tx0 + i}`;
      jobs.push(loadImage(`${USGS}/USGSImageryOnly/MapServer/${at}`).then((im) => im && g.drawImage(im, i * 256, j * 256)));
      jobs.push(loadImage(`${USGS}/USGSHydroCached/MapServer/${at}`).then((im) => im && wg.drawImage(im, i * 256, j * 256)));
    }
  }
  const ok = await Promise.race([Promise.all(jobs).then(() => true), new Promise((r) => setTimeout(() => r(false), timeout))]);
  if (!ok) return null;
  const t = tex(cv, { repeat: false });
  t.anisotropy = 16;
  const wt = tex(wcv, { repeat: false, srgb: false });
  const seg = 96;
  const geo = new THREE.PlaneGeometry(half * 2, half * 2, seg, seg);
  geo.rotateX(-Math.PI / 2);
  const pos = geo.getAttribute("position");
  const uv = geo.getAttribute("uv");
  for (let i = 0; i < pos.count; i++) {
    const [lon, lat] = proj.lonlat(pos.getX(i), -pos.getZ(i));
    const [fx, fy] = tileXY(lon, lat, z);
    uv.setXY(i, (fx - tx0) / n, 1 - (fy - ty0) / n);
  }
  geo.translate(0, y, 0);
  const [w, s, e, nb] = hole;
  const m = new THREE.MeshStandardMaterial({ map: t, roughness: 0.95, metalness: 0 });
  patchGround(m, U, { paved: 0.25, winter: true, city: 1.1 });
  const prev = m.onBeforeCompile;
  m.onBeforeCompile = (sh) => {
    prev(sh);
    sh.uniforms.uWater = { value: wt };
    sh.fragmentShader = sh.fragmentShader
      .replace("uniform vec4 uLightM;", "uniform vec4 uLightM;\nuniform sampler2D uWater;")
      .replace(
        "#include <map_fragment>",
        `#include <map_fragment>
        {
          vec2 q = vec2(vGW.x, -vGW.z);
          if (q.x > ${w.toFixed(1)} && q.x < ${e.toFixed(1)} && q.y > ${s.toFixed(1)} && q.y < ${nb.toFixed(1)}) discard;
          if (vMapUv.x < 0.0 || vMapUv.x > 1.0 || vMapUv.y < 0.0 || vMapUv.y > 1.0) discard;
          if (texture2D(uWater, vMapUv).a > 0.35 || dot(diffuseColor.rgb, vec3(1.0)) < 0.01) discard;
        }`,
      );
  };
  m.customProgramCacheKey = () => `ground-far-${z}`;
  const mesh = new THREE.Mesh(geo, m);
  mesh.receiveShadow = true;
  return mesh;
}

/* ---------------- the airfield ---------------- */

function rasterFrame(stage, res = 2) {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  const grow = (p) => {
    x0 = Math.min(x0, p[0]);
    y0 = Math.min(y0, p[1]);
    x1 = Math.max(x1, p[0]);
    y1 = Math.max(y1, p[1]);
  };
  for (const r of stage.runways) r.polygon.forEach(grow);
  for (const t of stage.taxiways) t.line.forEach(grow);
  for (const a of stage.aprons) a.outer.forEach(grow);
  x0 -= 200;
  y0 -= 200;
  x1 += 200;
  y1 += 200;
  const W = Math.ceil((x1 - x0) / res);
  const Hh = Math.ceil((y1 - y0) / res);
  return { x0, y0, x1: x0 + W * res, y1: y0 + Hh * res, res, W, H: Hh };
}

function frameCtx(f) {
  const [cv, g] = canvas(f.W, f.H);
  g.setTransform(1 / f.res, 0, 0, -1 / f.res, -f.x0 / f.res, f.y1 / f.res);
  return [cv, g];
}

function trace(g, ring) {
  ring.forEach((p, i) => (i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1])));
  g.closePath();
}

function paveMask(stage, f) {
  const [, g] = frameCtx(f);
  g.globalCompositeOperation = "lighter";
  g.fillStyle = "rgb(0,0,255)";
  for (const a of stage.aprons) {
    g.beginPath();
    trace(g, a.outer);
    for (const h of a.holes || []) trace(g, h);
    g.fill("evenodd");
  }
  g.lineJoin = "round";
  g.lineCap = "round";
  for (const t of stage.taxiways) {
    g.strokeStyle = t.kind === "taxilane" ? "rgb(0,0,255)" : "rgb(0,255,0)";
    g.lineWidth = t.width;
    g.beginPath();
    t.line.forEach((p, i) => (i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1])));
    g.stroke();
  }
  g.fillStyle = "rgb(255,0,0)";
  for (const r of stage.runways) {
    g.beginPath();
    trace(g, r.polygon);
    g.fill();
  }
  const data = g.getImageData(0, 0, f.W, f.H).data;
  return (p) => {
    const i = Math.floor((p[0] - f.x0) / f.res);
    const j = Math.floor((f.y1 - p[1]) / f.res);
    if (i < 0 || j < 0 || i >= f.W || j >= f.H) return 0;
    const k = (j * f.W + i) * 4;
    return (data[k] > 127 ? 1 : 0) | (data[k + 1] > 127 ? 2 : 0) | (data[k + 2] > 127 ? 4 : 0);
  };
}

function runwayFrames(stage) {
  return stage.runways.map((r) => {
    const [A, B] = r.ends;
    const v = sub(B.xy, A.xy);
    const L = len(v);
    return { r, A, B, L, d: [v[0] / L, v[1] / L], w: r.width };
  });
}

function nearRunway(rf, p, zone) {
  for (const f of rf) {
    const q = sub(p, f.A.xy);
    const a = dot(q, f.d);
    if (a < -80 || a > f.L + 80) continue;
    if (Math.abs(dot(q, perp(f.d))) < zone(f)) return f;
  }
  return null;
}

function floodMasts(stage, terminalsHit) {
  const stands = stage.stands;
  const masts = [];
  const tooClose = (p, r) => masts.some((m) => len(sub(m.p, p)) < r);
  for (const s of stands) {
    const hd = dirOf(s.heading);
    let best = null;
    for (const o of stands) {
      if (o === s) continue;
      const dd = len(sub(o.xy, s.xy));
      if (dd > 20 && dd < 95 && (!best || dd < best.dd)) best = { o, dd };
    }
    const mid = best ? add([(s.xy[0] + best.o.xy[0]) / 2, (s.xy[1] + best.o.xy[1]) / 2], hd, -4) : add(add(s.xy, perp(hd), 24), hd, -6);
    if (terminalsHit(mid) || tooClose(mid, 42)) continue;
    masts.push({ p: mid, out: [-hd[0], -hd[1]], sodium: s.terminal === "remote" });
  }
  return masts;
}

function pointInRing(p, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i];
    const b = ring[j];
    if (a[1] > p[1] !== b[1] > p[1] && p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

/** update(t, light, weather): light is daylight 0..1, weather a METAR record or the moment's list. */
export async function buildGround(stage, baseUrl = "", opts = {}) {
  const group = new THREE.Group();
  group.name = "ground";
  const U = {
    uWet: { value: 0 },
    uPool: { value: 0 },
    uCity: { value: 0 },
    uPuddle: { value: puddleTexture() },
    uLightMap: { value: null },
    uLightM: { value: new THREE.Vector4() },
    uTime: { value: 0 },
    uOn: { value: 1 },
    uHalo: { value: 1 },
    uGain: { value: 1 },
    uWetStreak: { value: 0 },
  };
  const f = rasterFrame(stage);
  U.uLightM.value.set(f.x0, f.y0, 1 / (f.x1 - f.x0), 1 / (f.y1 - f.y0));
  const mask = paveMask(stage, f);
  const rf = runwayFrames(stage);
  const terminalsHit = (p) => stage.terminals.some((t) => pointInRing(p, t.outer)) || (stage.hangars || []).some((t) => pointInRing(p, t.outer));

  const ortho = await new THREE.TextureLoader().loadAsync(new URL(stage.ortho.url, new URL(baseUrl || ".", location.href)).href);
  ortho.colorSpace = THREE.SRGBColorSpace;
  ortho.anisotropy = 16;
  const [ow, os, oe, on] = stage.ortho.bounds;

  const land = new Flat();
  for (const l of stage.land) land.polygon(l.outer, l.holes, H.land);
  const landGeo = land.geometry();
  const luv = landGeo.getAttribute("uv");
  for (let i = 0; i < luv.count; i++) luv.setXY(i, (luv.getX(i) - ow) / (oe - ow), (luv.getY(i) - os) / (on - os));
  const landMat = patchGround(new THREE.MeshStandardMaterial({ map: ortho, roughness: 0.93 }), U, { paved: 0.3, winter: true, city: 1 });
  const landMesh = new THREE.Mesh(landGeo, landMat);
  landMesh.receiveShadow = true;
  group.add(landMesh);

  const skirt = [];
  const onEdge = (p) => Math.abs(p[0] - ow) < 1 || Math.abs(p[0] - oe) < 1 || Math.abs(p[1] - os) < 1 || Math.abs(p[1] - on) < 1;
  for (const l of stage.land) {
    for (const ring of [l.outer, ...(l.holes || [])]) {
      for (let i = 0; i < ring.length; i++) {
        const a = ring[i];
        const b = ring[(i + 1) % ring.length];
        if (onEdge(a) && onEdge(b)) continue;
        const y0 = WATER_Y - 1;
        skirt.push(a[0], 0, -a[1], b[0], 0, -b[1], b[0], y0, -b[1], a[0], 0, -a[1], b[0], y0, -b[1], a[0], y0, -a[1]);
      }
    }
  }
  const skirtGeo = new THREE.BufferGeometry();
  skirtGeo.setAttribute("position", new THREE.Float32BufferAttribute(skirt, 3));
  skirtGeo.computeVertexNormals();
  group.add(new THREE.Mesh(skirtGeo, new THREE.MeshStandardMaterial({ color: "#4E4A44", roughness: 0.9, side: THREE.DoubleSide })));

  const apron = new Flat();
  for (const a of stage.aprons) apron.polygon(a.outer, a.holes, H.apron);
  const lane = new Flat();
  const taxi = new Flat();
  for (const t of stage.taxiways) {
    if (t.kind === "taxilane") lane.thick(t.line, t.width, H.lane);
    else taxi.thick(t.line, t.width, H.taxi);
  }
  const runway = new Flat();
  for (const r of stage.runways) runway.polygon(r.polygon, [], H.runway);

  const concrete = concreteTexture();
  const apronMat = patchGround(new THREE.MeshStandardMaterial({ map: concrete, color: "#B0AEA9", roughness: 0.88 }), U, { paved: 0.8 });
  const taxiMat = patchGround(new THREE.MeshStandardMaterial({ map: asphaltTexture("#7C7A76", 127), color: "#9A9894", roughness: 0.9 }), U);
  const runwayMat = patchGround(new THREE.MeshStandardMaterial({ map: asphaltTexture("#686662", 211, 30), color: "#8E8C88", roughness: 0.88 }), U);
  for (const [fl, mat] of [[apron, apronMat], [lane, apronMat], [taxi, taxiMat], [runway, runwayMat]]) {
    const m = new THREE.Mesh(fl.geometry(), mat);
    m.receiveShadow = true;
    group.add(m);
  }

  const white = new Flat();
  const yellow = new Flat();
  const rubber = [];
  const lamps = new Lamps();
  const streaks = [];
  const desigIds = [];
  const desigQuads = [];

  const WHITE = "#FFF1D6";
  const RED = "#FF2A16";
  const GREEN = "#22FF66";
  for (const fr of rf) {
    const { L, w } = fr;
    const precision = w >= 40 && L > 1500;
    const edgeOff = w / 2 - 0.45;
    white.rect(fr.A.xy, fr.d, 0, L, edgeOff - 0.45, edgeOff + 0.45, H.paint);
    white.rect(fr.A.xy, fr.d, 0, L, -edgeOff - 0.45, -edgeOff + 0.45, H.paint);
    const cw = precision ? 0.9 : 0.5;
    for (let a = 112; a + 36 < L - 112; a += 61) white.rect(fr.A.xy, fr.d, a, a + 36.6, -cw / 2, cw / 2, H.paint);
    for (let a = 0; a <= L; a += 60) {
      for (const sgn of [-1, 1]) {
        const p = add(add(fr.A.xy, fr.d, a), perp(fr.d), sgn * (w / 2 + 1.5));
        lamps.add(p, 0.4, { col: WHITE, size: 0.7, intensity: 5 });
        streaks.push({ p, h: 0.4, col: WHITE, intensity: 0.3 });
      }
    }
    if (precision) {
      for (let a = 0, i = 0; a <= L; a += 15, i++) {
        const fromA = L - a;
        const colFor = (rem) => (rem < 300 ? RED : rem < 900 && i % 2 ? RED : WHITE);
        lamps.add(add(fr.A.xy, fr.d, a), 0.05, { col: colFor(a), col2: colFor(fromA), dir: fr.d, directional: 0.85, size: 0.45, intensity: 3.2 });
      }
    }
    for (const [end, o, d] of [[fr.A, fr.A.xy, fr.d], [fr.B, fr.B.xy, [-fr.d[0], -fr.d[1]]]]) {
      const n = w >= 45 ? 12 : w >= 30 ? 8 : 6;
      for (let k = 0; k < n / 2; k++) {
        const c = 1.75 + 0.9 + k * 3.6;
        for (const sgn of [-1, 1]) fr.L > 600 && white.rect(o, d, 6, 51, sgn * c - 0.9, sgn * c + 0.9, H.paint);
      }
      desigIds.push(end.id);
      desigQuads.push({ o, d, a0: 62, a1: 62 + 46, half: 7.5 });
      if (precision || L > 1800) {
        const inner = w >= 40 ? 11 : 7.3;
        const aw = w >= 40 ? 9 : 6;
        for (const sgn of [-1, 1]) white.rect(o, d, 305, 350, sgn * inner, sgn * (inner + aw), H.paint);
        if (precision) {
          for (const [a, nb] of [[152, 3], [457, 2], [610, 2], [762, 1], [914, 1]]) {
            for (let b = 0; b < nb; b++) {
              const c0 = inner + b * 3.3;
              for (const sgn of [-1, 1]) white.rect(o, d, a, a + 22.9, sgn * c0, sgn * (c0 + 1.8), H.paint);
            }
          }
          rubber.push({ o, d, a0: 120, a1: 1150, c0: -w * 0.3, c1: w * 0.3 });
        }
      }
      for (let c = -w / 2; c <= w / 2 + 0.01; c += 3) {
        const p = add(o, perp(d), c);
        lamps.add(p, 0.3, { col: RED, col2: GREEN, dir: d, directional: 0.65, size: 0.6, intensity: 6 });
      }
      if (L > 1200) {
        for (let k = 0; k < 4; k++) {
          const p = add(add(o, d, 300), perp(d), w / 2 + 15 + k * 9);
          lamps.add(p, 0.9, { col: RED, col2: WHITE, dir: [-d[0], -d[1]], directional: 0.9, size: 0.8, intensity: 7, kind: 3, phase: 3.5 - k * 0.33 });
        }
      }
    }
  }

  const approach = new Set(opts.approach || rf.filter((fr) => fr.w >= 40 && fr.L >= 2300).flatMap((fr) => [fr.A.id, fr.B.id]));
  const onLand = (p) => stage.land.some((l) => pointInRing(p, l.outer) && !(l.holes || []).some((h) => pointInRing(p, h)));
  for (const fr of rf) {
    for (const [end, o, d] of [[fr.A, fr.A.xy, fr.d], [fr.B, fr.B.xy, [-fr.d[0], -fr.d[1]]]]) {
      if (!approach.has(end.id) || fr.w < 40) continue;
      const back = [-d[0], -d[1]];
      const p0 = perp(d);
      for (let s = 30, i = 0; s <= 720; s += 30, i++) {
        const c = add(o, back, s);
        for (let k = -2; k <= 2; k++) lamps.add(add(c, p0, k * 1.05), 2.5, { col: WHITE, col2: "#000", dir: back, directional: 0.8, size: 0.7, intensity: 6 });
        if (s === 300) for (let k = -7; k <= 7; k++) Math.abs(k) > 2 && lamps.add(add(c, p0, k * 1.5), 2.5, { col: WHITE, col2: "#000", dir: back, directional: 0.8, size: 0.7, intensity: 6 });
        if (s <= 270) for (const sgn of [-1, 1]) for (let k = 0; k < 3; k++) lamps.add(add(c, p0, sgn * (11 + k * 1.5)), 2.5, { col: RED, col2: "#000", dir: back, directional: 0.8, size: 0.6, intensity: 5 });
        if (s > 300) lamps.add(add(c, p0, 0), 3.2, { col: "#FFFFFF", col2: "#000", dir: back, directional: 0.6, size: 1.2, intensity: 14, kind: 1, phase: 1 - (s - 300) / 420 });
      }
      const pier = new Flat();
      for (let s = 20; s < 735; s += 15) if (!onLand(add(o, back, s + 7.5))) pier.rect(o, back, s, s + 15, -1.6, 1.6, 1.6);
      if (pier.p.length) group.add(new THREE.Mesh(pier.geometry(), new THREE.MeshStandardMaterial({ color: "#5E5A54", roughness: 0.85 })));
    }
  }

  const zone = (fr) => (fr.w >= 40 ? 76 : 60);
  const holds = [];
  const signs = [];
  for (const t of stage.taxiways) {
    const S = sample(t.line, 2);
    const kindLane = t.kind === "taxilane";
    for (const run of runs(S, (s) => !(mask(s.p) & 1))) thinRun(yellow, run, 0.3, H.paint);
    if (kindLane) continue;
    S.forEach((s, i) => {
      if (i % 15 === 7 && !(mask(s.p) & 1)) lamps.add(s.p, 0.05, { col: "#2CFF6E", size: 0.4, intensity: 2.2, dir: s.d, directional: 0.35 });
    });
    for (const sgn of [-1, 1]) {
      const nrm = (s) => perp(s.d).map((v) => v * sgn);
      const isEdge = (s) => !mask(add(s.p, nrm(s), t.width / 2 + 3)) && !(mask(s.p) & 1);
      for (const run of runs(S, isEdge)) {
        for (const off of [t.width / 2 - 0.5, t.width / 2 - 0.95]) {
          thinRun(yellow, run.map((s) => ({ p: add(s.p, nrm(s), off), d: s.d })), 0.18, H.paint);
        }
        run.forEach((s, i) => {
          if (i % 15 === 0) {
            const p = add(s.p, nrm(s), t.width / 2 + 1.5);
            lamps.add(p, 0.35, { col: "#2F55FF", size: 0.55, intensity: 2.6 });
            streaks.push({ p, h: 0.35, col: "#2F55FF", intensity: 0.25 });
          }
        });
      }
    }
    let prev = null;
    S.forEach((s, i) => {
      const inZ = nearRunway(rf, s.p, zone);
      if (i > 0 && !!inZ !== !!prev) {
        const fr = inZ || prev;
        const at = inZ ? S[i - 1] : s;
        const toward = inZ ? at.d : [-at.d[0], -at.d[1]];
        if (Math.abs(dot(at.d, fr.d)) < 0.8 && !(mask(at.p) & 5) && !holds.some((h) => len(sub(h, at.p)) < 20)) {
          holds.push(at.p);
          const n = perp(toward);
          const hw = t.width / 2;
          signs.push({ p: add(add(at.p, n, hw + 6), toward, -1), face: [-toward[0], -toward[1]], label: fr.r.ref.replace("/", "-") });
          for (const [k, dashed] of [[0, false], [0.6, false], [1.2, true], [1.8, true]]) {
            const c = add(at.p, toward, k);
            if (!dashed) yellow.rect(c, toward, -0.15, 0.15, -hw, hw, H.paint + 0.01);
            else for (let q = -hw; q < hw; q += 1.8) yellow.rect(c, toward, -0.15, 0.15, q, Math.min(hw, q + 0.9), H.paint + 0.01);
          }
          for (const sgn of [-1, 1]) lamps.add(add(at.p, n, sgn * (hw + 2.5)), 0.8, { col: "#FFB81A", size: 0.6, intensity: 6, kind: 2, phase: sgn > 0 ? 0 : 0.5 });
        }
      }
      prev = inZ;
    });
  }
  for (const s of stage.stands) {
    if (s.lead && s.lead.length > 1) {
      yellow.thin(s.lead, 0.3, H.paint);
      const hd = dirOf(s.heading);
      yellow.rect(s.xy, hd, -0.2, 0.2, -2.5, 2.5, H.paint);
    }
  }

  const masts = floodMasts(stage, terminalsHit);
  const [lcv, lg] = frameCtx(f);
  lg.fillStyle = "#000";
  lg.fillRect(f.x0, f.y0, f.x1 - f.x0, f.y1 - f.y0);
  lg.globalCompositeOperation = "lighter";
  for (const m of masts) {
    const c = add(m.p, m.out, 26);
    const r = 70;
    const gr = lg.createRadialGradient(c[0], c[1], 0, c[0], c[1], r);
    const col = m.sodium ? "255,170,80" : "255,214,160";
    gr.addColorStop(0, `rgba(${col},0.95)`);
    gr.addColorStop(0.35, `rgba(${col},0.55)`);
    gr.addColorStop(0.7, `rgba(${col},0.15)`);
    gr.addColorStop(1, `rgba(${col},0)`);
    lg.fillStyle = gr;
    lg.beginPath();
    lg.arc(c[0], c[1], r, 0, Math.PI * 2);
    lg.fill();
    lamps.add(m.p, 24, { col: m.sodium ? "#FFB060" : "#FFE6C0", size: 1.9, intensity: 9 });
    streaks.push({ p: m.p, h: 24, col: m.sodium ? "#FFB060" : "#FFE6C0", intensity: 0.55 });
  }
  lg.filter = "blur(8px)";
  lg.strokeStyle = "rgba(255,200,140,0.22)";
  lg.lineWidth = 26;
  for (const t of stage.terminals) {
    lg.beginPath();
    trace(lg, t.outer);
    lg.stroke();
  }
  lg.filter = "none";
  const lightTex = new THREE.CanvasTexture(lcv);
  lightTex.colorSpace = THREE.SRGBColorSpace;
  lightTex.flipY = true;
  U.uLightMap.value = lightTex;

  const mastGeo = (() => {
    const pole = new THREE.CylinderGeometry(0.22, 0.38, 24, 8);
    pole.translate(0, 12, 0);
    const head = new THREE.BoxGeometry(3.4, 0.7, 1.1);
    head.translate(0, 24.2, 0);
    const g = new THREE.BufferGeometry();
    const merge = [pole.toNonIndexed(), head.toNonIndexed()];
    const pos = merge.flatMap((m) => Array.from(m.getAttribute("position").array));
    const nor = merge.flatMap((m) => Array.from(m.getAttribute("normal").array));
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
    return g;
  })();
  const mastMesh = new THREE.InstancedMesh(mastGeo, new THREE.MeshStandardMaterial({ color: "#8A8D90", roughness: 0.55, metalness: 0.5 }), Math.max(1, masts.length));
  const dummy = new THREE.Object3D();
  masts.forEach((m, i) => {
    dummy.position.set(m.p[0], 0, -m.p[1]);
    dummy.rotation.set(0, Math.atan2(m.out[0], m.out[1]), 0);
    dummy.updateMatrix();
    mastMesh.setMatrixAt(i, dummy.matrix);
  });
  mastMesh.count = masts.length;
  mastMesh.castShadow = true;
  group.add(mastMesh);

  const whiteMat = patchGround(new THREE.MeshStandardMaterial({ color: "#D6D5CE", roughness: 0.7 }), U, { paved: 0.8 });
  const yellowMat = patchGround(new THREE.MeshStandardMaterial({ color: "#D8A21C", roughness: 0.7 }), U, { paved: 0.8 });
  const rubberMat = patchGround(new THREE.MeshStandardMaterial({ color: "#121110", roughness: 0.8, transparent: true, opacity: 1, alphaMap: rubberTexture(), depthWrite: false }), U, { paved: 0.15 });
  const rubberMesh = new THREE.Mesh(frameQuads(rubber, H.rubber), rubberMat);
  rubberMesh.renderOrder = 1;
  group.add(rubberMesh, new THREE.Mesh(white.geometry(), whiteMat), new THREE.Mesh(yellow.geometry(), yellowMat));

  const atlas = designatorAtlas(desigIds);
  const dpos = [];
  const duv = [];
  desigQuads.forEach((q, i) => {
    const r = [q.d[1], -q.d[0]];
    const at = (a, c) => add(add(q.o, q.d, a), r, c);
    const corners = [at(q.a0, -q.half), at(q.a0, q.half), at(q.a1, q.half), at(q.a1, -q.half)];
    const u0 = i / atlas.n;
    const u1 = (i + 1) / atlas.n;
    const uvs = [[u0, 0], [u1, 0], [u1, 1], [u0, 1]];
    for (const k of [0, 1, 2, 0, 2, 3]) {
      dpos.push(corners[k][0], H.paint + 0.005, -corners[k][1]);
      duv.push(uvs[k][0], uvs[k][1]);
    }
  });
  const dGeo = new THREE.BufferGeometry();
  dGeo.setAttribute("position", new THREE.Float32BufferAttribute(dpos, 3));
  dGeo.setAttribute("uv", new THREE.Float32BufferAttribute(duv, 2));
  dGeo.setAttribute("normal", new THREE.Float32BufferAttribute(dpos.map((_, i) => (i % 3 === 1 ? 1 : 0)), 3));
  const dMat = patchGround(new THREE.MeshStandardMaterial({ color: "#D6D5CE", map: atlas.t, alphaTest: 0.45, roughness: 0.7, side: THREE.DoubleSide }), U, { paved: 0.8 });
  group.add(new THREE.Mesh(dGeo, dMat));

  const signMesh = holdSigns(signs);
  group.add(signMesh);
  group.add(lampPoints(lamps, U));
  group.add(lampStreaks(streaks.filter((s) => mask(s.p)), U));

  const clock = new THREE.Clock();
  const far = [];
  const [bw, bs, be, bn] = stage.ortho.bounds;
  const ready = Promise.all([
    farField(stage, U, { z: 14, n: 9, half: 7600, y: H.far, hole: [bw + 1, bs + 1, be - 1, bn - 1] }),
    farField(stage, U, { z: 12, n: 7, half: 26000, y: H.far - 0.3, hole: [-7000, -7000, 7000, 7000] }),
  ]).then((ms) => {
    for (const m of ms) if (m) {
      far.push(m);
      group.add(m);
    }
    return far.length;
  }).catch(() => 0);

  function update(t, light = 1, weather = null) {
    const daylight = typeof light === "number" ? light : light?.daylight ?? 1;
    const w = weatherAt(weather, t) || {};
    const wet = wetness(w);
    const vis = w.vis_sm ?? 10;
    const ceil = w.ceiling_ft ?? 99999;
    const mist = (w.wx || []).some((c) => /(BR|FG|DZ)/.test(c)) ? 1 : 0;
    const ifr = vis < 3 || ceil < 1000;
    const dark = 1 - daylight;
    U.uWet.value = wet;
    U.uPool.value = 0.5 * smooth(0.85, 0.2, daylight);
    U.uCity.value = 0.55 * smooth(0.6, 0.1, daylight);
    U.uOn.value = ifr || daylight < 0.85 ? 1 : 0;
    U.uGain.value = 0.55 + 0.6 * dark;
    U.uHalo.value = 1 + mist * 1.1 + wet * 0.4;
    U.uWetStreak.value = wet * (0.25 + 0.75 * smooth(0.85, 0.2, daylight));
    U.uTime.value = clock.getElapsedTime();
    signMesh.userData.face.emissiveIntensity = 0.15 + 0.85 * smooth(0.85, 0.2, daylight);
  }

  return {
    group,
    update,
    ready,
    far,
    masts,
    lampCount: lamps.count,
  };
}

/** Rectangles in runway frames with v along d and u from the left edge to the right. */
function frameQuads(quads, h) {
  const pos = [];
  const uv = [];
  for (const q of quads) {
    const p = perp(q.d);
    const at = (a, c) => [q.o[0] + q.d[0] * a + p[0] * c, q.o[1] + q.d[1] * a + p[1] * c];
    const C = [[at(q.a0, q.c0), 1, 0], [at(q.a1, q.c0), 1, 1], [at(q.a1, q.c1), 0, 1], [at(q.a0, q.c1), 0, 0]];
    for (const k of [0, 1, 2, 0, 2, 3]) {
      pos.push(C[k][0][0], h, -C[k][0][1]);
      uv.push(C[k][1], C[k][2]);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(pos.map((_, i) => (i % 3 === 1 ? 1 : 0)), 3));
  return g;
}

function holdSigns(list) {
  const labels = [...new Set(list.map((s) => s.label))];
  const cw = 256;
  const ch = 72;
  const [cv, g] = canvas(cw, ch * Math.max(1, labels.length));
  labels.forEach((l, i) => {
    g.fillStyle = "#B3121C";
    g.fillRect(0, i * ch, cw, ch);
    g.font = "700 46px 'Helvetica Neue', Arial, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.lineWidth = 6;
    g.strokeStyle = "#111";
    g.strokeText(l, cw / 2, i * ch + ch / 2 + 2);
    g.fillStyle = "#fff";
    g.fillText(l, cw / 2, i * ch + ch / 2 + 2);
  });
  const t = tex(cv, { repeat: false });
  const fpos = [];
  const fuv = [];
  const bpos = [];
  const bnor = [];
  for (const s of list) {
    const i = labels.indexOf(s.label);
    const v0 = 1 - (i + 1) / labels.length;
    const v1 = 1 - i / labels.length;
    for (const side of [1, -1]) {
      const f = [s.face[0] * side, s.face[1] * side];
      const r = [-f[1], f[0]];
      const c = add(s.p, f, 0.19);
      const P = (x, y) => [c[0] + r[0] * x, y, -(c[1] + r[1] * x)];
      const C = [[P(-1.8, 0.45), 0, v0], [P(1.8, 0.45), 1, v0], [P(1.8, 1.45), 1, v1], [P(-1.8, 1.45), 0, v1]];
      for (const k of [0, 1, 2, 0, 2, 3]) {
        fpos.push(...C[k][0]);
        fuv.push(C[k][1], C[k][2]);
      }
    }
    const b = new THREE.BoxGeometry(3.7, 1.1, 0.36).toNonIndexed();
    b.rotateY(Math.atan2(s.face[0], -s.face[1]));
    b.translate(s.p[0], 0.95, -s.p[1]);
    bpos.push(...b.getAttribute("position").array);
    bnor.push(...b.getAttribute("normal").array);
    for (const dx of [-1.2, 1.2]) {
      const leg = new THREE.BoxGeometry(0.12, 0.45, 0.12).toNonIndexed();
      leg.translate(dx, 0.22, 0);
      leg.rotateY(Math.atan2(s.face[0], -s.face[1]));
      leg.translate(s.p[0], 0, -s.p[1]);
      bpos.push(...leg.getAttribute("position").array);
      bnor.push(...leg.getAttribute("normal").array);
    }
  }
  const fg = new THREE.BufferGeometry();
  fg.setAttribute("position", new THREE.Float32BufferAttribute(fpos, 3));
  fg.setAttribute("uv", new THREE.Float32BufferAttribute(fuv, 2));
  fg.computeVertexNormals();
  const bg = new THREE.BufferGeometry();
  bg.setAttribute("position", new THREE.Float32BufferAttribute(bpos, 3));
  bg.setAttribute("normal", new THREE.Float32BufferAttribute(bnor, 3));
  const face = new THREE.MeshStandardMaterial({ map: t, emissiveMap: t, emissive: "#ffffff", emissiveIntensity: 0.2, roughness: 0.5 });
  const group = new THREE.Group();
  group.add(new THREE.Mesh(bg, new THREE.MeshStandardMaterial({ color: "#2A2B2C", roughness: 0.7 })), new THREE.Mesh(fg, face));
  group.userData.face = face;
  return group;
}

function thinRun(flat, run, w, h) {
  flat.thin(run.map((s) => s.p), w, h);
}

/** Camera presets in ENU metres ([x, y, up]) for a stage and moment. */
export function cameraPresets(stage, moment) {
  const tw = stage.tower;
  const feat = moment?.featured && moment.aircraft?.find((a) => a.tail === moment.featured.tail);
  const stand = stage.stands.find((s) => s.ref === feat?.stand) || stage.stands.find((s) => s.terminal === "C") || stage.stands[0];
  const depId = moment?.runways_in_use?.departures;
  const end = stage.runways.flatMap((r) => r.ends).find((e) => e.id === depId) || stage.runways[0].ends[0];
  const cs = stage.stands.filter((s) => s.terminal === stand.terminal);
  const cx = cs.reduce((a, s) => a + s.xy[0], 0) / cs.length;
  const cy = cs.reduce((a, s) => a + s.xy[1], 0) / cs.length;
  const rw = stage.runways.flatMap((r) => r.ends.map((e) => e.xy));
  const rx = rw.reduce((a, p) => a + p[0], 0) / rw.length;
  const ry = rw.reduce((a, p) => a + p[1], 0) / rw.length;
  const toR = unit([rx - tw.xy[0], ry - tw.xy[1]]);
  const hd = dirOf(stand.heading);
  const gateFrom = add(stand.xy, dirOf(stand.heading - 125), 60);
  const ed = dirOf(end.heading);
  const thFrom = add(add(end.xy, ed, -70), perp(ed), -80);
  const hb = add([rx, ry], dirOf(110), 3600);
  return {
    tower: { position: [tw.xy[0] + toR[0] * 6, tw.xy[1] + toR[1] * 6, tw.height * 0.86], target: [cx * 0.35 + rx * 0.65, cy * 0.35 + ry * 0.65, 0], fov: 55 },
    gate: { position: [gateFrom[0], gateFrom[1], 8], target: [stand.xy[0] - hd[0] * 12, stand.xy[1] - hd[1] * 12, 4], fov: 50 },
    threshold: { position: [thFrom[0], thFrom[1], 4.5], target: [end.xy[0] + ed[0] * 1500, end.xy[1] + ed[1] * 1500, 2], fov: 45 },
    harbour: { position: [hb[0], hb[1], 300], target: [cx * 0.6 + rx * 0.4, cy * 0.6 + ry * 0.4 - 300, 0], fov: 40 },
  };
}
