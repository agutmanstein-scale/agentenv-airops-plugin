// Adapted from PortSimEnv v1 (FineEnvs, Apache-2.0): scene-kit.js
import * as THREE from "three";
import { snowOn } from "./env.js";
import { Lamps, lampPoints, lampUniforms } from "./ground.js";
import { massing } from "./massing.js";

const KIND = { terminal: 0, hangar: 1, house: 2, tower: 3, airport: 4, hall: 5 };
const ROOF = 10;
const smooth = (a, b, x) => {
  const u = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return u * u * (3 - 2 * u);
};

function area(ring) {
  let a = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) a += (ring[j][0] - ring[i][0]) * (ring[j][1] + ring[i][1]);
  return a / 2;
}

function clean(ring) {
  const out = ring.slice();
  if (out.length > 1 && out[0][0] === out[out.length - 1][0] && out[0][1] === out[out.length - 1][1]) out.pop();
  return out;
}

/** Walls carry uv in metres along the wall and up; roofs carry plan metres. */
class Massing {
  constructor() {
    this.pos = [];
    this.nor = [];
    this.uv = [];
    this.info = [];
  }
  vert(x, h, y, nx, ny, nz, u, v, info) {
    this.pos.push(x, h, -y);
    this.nor.push(nx, ny, nz);
    this.uv.push(u, v);
    this.info.push(...info);
  }
  walls(ring, base, top, info, hole = false) {
    const r = clean(ring);
    if (r.length < 3) return;
    const ccw = area(r) > 0;
    const outRight = ccw !== hole;
    let u = 0;
    for (let i = 0; i < r.length; i++) {
      const a = r[i];
      const b = r[(i + 1) % r.length];
      const ex = b[0] - a[0];
      const ey = b[1] - a[1];
      const l = Math.hypot(ex, ey);
      if (l < 0.05) continue;
      const nx = outRight ? ey / l : -ey / l;
      const ny = outRight ? -ex / l : ex / l;
      const sx = nx;
      const sz = -ny;
      const corners = [[a, base, u], [b, base, u + l], [b, top, u + l], [a, top, u]];
      const order = outRight ? [0, 1, 2, 0, 2, 3] : [0, 2, 1, 0, 3, 2];
      for (const k of order) {
        const [p, h, uu] = corners[k];
        this.vert(p[0], h, p[1], sx, 0, sz, uu, h - base, info);
      }
      u += l;
    }
  }
  roof(outer, holes, h, info) {
    const ring = (r) => clean(r).map((p) => new THREE.Vector2(p[0], p[1]));
    const o = ring(outer);
    if (o.length < 3) return;
    const hs = (holes || []).map(ring).filter((r) => r.length >= 3);
    const all = o.concat(...hs);
    for (const tri of THREE.ShapeUtils.triangulateShape(o, hs)) {
      const [a, b, c] = tri.map((i) => all[i]);
      const cross = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
      for (const p of cross < 0 ? [a, c, b] : [a, b, c]) this.vert(p.x, h, p.y, 0, 1, 0, p.x, p.y, info);
    }
  }
  geometry() {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(this.nor, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(this.uv, 2));
    g.setAttribute("aInfo", new THREE.Float32BufferAttribute(this.info, 3));
    g.computeBoundingSphere();
    return g;
  }
}

const CUT_BOXES = 40;
const CUT_SEGS = 15;

/** The lounges' glass (passengers.js cutouts): per lounge its centre and reach, and its first segment and count packed
 * as start * 16 + count; its segments, the lounge on their left. */
function cutUniforms(cut) {
  const boxes = [];
  const segs = [];
  for (const lg of cut.lounges.slice(0, CUT_BOXES)) {
    const s = lg.segs.slice(0, CUT_SEGS);
    boxes.push(new THREE.Vector4(lg.c[0], lg.c[1], lg.r, segs.length * 16 + s.length));
    for (const [x0, y0, x1, y1] of s) segs.push(new THREE.Vector4(x0, y0, x1, y1));
  }
  while (boxes.length < CUT_BOXES) boxes.push(new THREE.Vector4(0, 0, -1, 0));
  if (!segs.length) segs.push(new THREE.Vector4());
  return { uCutBox: { value: boxes }, uCutSeg: { value: segs }, uCutBand: { value: new THREE.Vector3(cut.floor, cut.ceil, cut.depth) } };
}

/** Windows are drawn in the shader and fade to their average coverage below a pixel; with cut, the home terminal's
 * glass on the departure level is left open in front of each lounge (and a wide block's walls behind it). */
function facadeMaterial(U, cut = null) {
  const m = new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.85, metalness: 0 });
  const C = cut ? cutUniforms(cut) : null;
  const segs = C ? C.uCutSeg.value.length : 1;
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, U, C || {});
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", "#include <common>\nattribute vec3 aInfo;\nvarying vec3 vInfo;\nvarying vec2 vM;\nvarying vec2 vQ;")
      .replace("#include <uv_vertex>", "#include <uv_vertex>\nvInfo = aInfo;\nvM = uv;\nvQ = vec2(position.x, -position.z);");
    sh.fragmentShader = sh.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
        ${C ? `#define CUT 1
        uniform vec4 uCutBox[${CUT_BOXES}];
        uniform vec4 uCutSeg[${segs}];
        uniform vec3 uCutBand;
        bool cutAt(vec2 q, bool hall, float win) {
          for (int i = 0; i < ${CUT_BOXES}; i++) {
            vec4 b = uCutBox[i];
            if (b.z < 0.0 || distance(q, b.xy) > b.z) continue;
            int pk = int(b.w + 0.5);
            int s0 = pk / 16;
            int cnt = pk - s0 * 16;
            for (int j = 0; j < ${CUT_SEGS}; j++) {
              if (j >= cnt) break;
              vec4 sg = uCutSeg[s0 + j];
              vec2 d = sg.zw - sg.xy;
              float L = length(d);
              vec2 t = d / max(L, 1e-4);
              vec2 r = q - sg.xy;
              float a = dot(r, t);
              float e = dot(r, vec2(-t.y, t.x));
              if (a < -0.4 || a > L + 0.4) continue;
              if (!hall && abs(e) < 0.9 && win > 0.5) return true;
              if (hall && e > -3.0 && e < uCutBand.z + 0.5) return true;
            }
          }
          return false;
        }` : ""}
        varying vec2 vQ;
        uniform float uNight, uSnow;
        uniform sampler2D uField;
        uniform vec4 uFieldM;
        varying vec3 vInfo;
        varying vec2 vM;
        float bh(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        vec3 pick4(float h, vec3 a, vec3 b, vec3 c, vec3 d) { return h < 0.25 ? a : h < 0.5 ? b : h < 0.75 ? c : d; }`,
      )
      .replace(
        "#include <color_fragment>",
        `#include <color_fragment>
        float kind = vInfo.x;
        float seed = vInfo.y;
        float bRough = 0.85;
        float bMetal = 0.0;
        vec3 bEmit = vec3(0.0);
        bool term = kind < 0.5 || abs(kind - 5.0) < 0.5;
        bool hall = abs(kind - 5.0) < 0.5;
        if (kind >= ${ROOF.toFixed(1)} && (kind - ${ROOF.toFixed(1)} < 0.5 || abs(kind - ${(ROOF + 5).toFixed(1)}) < 0.5)) {
          vec4 F = texture2D(uField, (vM - uFieldM.xy) * uFieldM.zw);
          float edge = F.r * 63.75;
          float width = F.g * 510.0;
          float fw2 = max(fwidth(edge), 0.02);
          float coping = 1.0 - smoothstep(0.55, 0.55 + fw2 * 1.5, edge);
          float gutter = smoothstep(1.0, 1.0 + fw2, edge) * (1.0 - smoothstep(1.7, 1.7 + fw2, edge));
          float spine = smoothstep(0.3, 0.6, F.b) * (1.0 - smoothstep(1.2, 1.2 + fw2 * 2.0, width * 0.5 - edge)) * step(14.0, width) * step(width, 60.0) * step(3.5, edge);
          vec2 g = vM / vec2(16.0, 22.0);
          vec2 gf = fract(g);
          float lights = step(70.0, width) * step(9.0, edge) * step(0.3, gf.x) * step(gf.x, 0.7) * step(0.15, gf.y) * step(gf.y, 0.75) * step(0.35, bh(floor(g) + seed));
          float seam = step(0.965, fract(vM.x / 4.8 + vM.y * 0.002)) * (1.0 - coping) * 0.06;
          float n = bh(floor(vM / 9.0) + seed) * 0.05;
          vec3 rc = (hall ? vec3(0.46, 0.47, 0.48) : vec3(0.4, 0.41, 0.42)) * (0.95 + n) - seam;
          rc = mix(rc, vec3(0.72, 0.73, 0.73), coping);
          rc = mix(rc, vec3(0.22, 0.23, 0.24), gutter * 0.8);
          float glass = max(spine, lights);
          float lie = uSnow * (0.85 + n) * (1.0 - 0.1 * glass);
          diffuseColor.rgb = mix(mix(rc, vec3(0.26, 0.31, 0.36), glass), vec3(0.86, 0.88, 0.92) * (0.92 + n), lie);
          bRough = mix(0.82, 0.08, glass * (1.0 - lie));
          bMetal = mix(0.0, 0.8, glass * (1.0 - lie));
          bEmit = vec3(1.0, 0.88, 0.7) * glass * 0.07 * uNight * (1.0 - lie);
        } else if (kind >= ${ROOF.toFixed(1)}) {
          float k = kind - ${ROOF.toFixed(1)};
          float n = bh(floor(vM / 6.0) + seed) * 0.06;
          vec3 rc = k < 0.5 ? vec3(0.27, 0.26, 0.25) : k < 1.5 ? vec3(0.36, 0.37, 0.38) : pick4(bh(vec2(seed, 3.1)), vec3(0.16), vec3(0.24, 0.23, 0.22), vec3(0.32, 0.31, 0.30), vec3(0.20, 0.21, 0.23));
          diffuseColor.rgb = mix(rc * (0.94 + n), vec3(0.86, 0.88, 0.92) * (0.92 + n), uSnow * (0.85 + n));
          bRough = 0.9;
        } else if (term) {
          float y = vM.y;
          float g0 = hall ? 4.5 : 5.0;
          float g1 = hall ? vInfo.z - 2.2 : max(g0 + 3.0, min(vInfo.z - 2.2, 11.0));
          vec2 pg = vec2(vM.x / 1.6, (y - g0) / ((g1 - g0) / 3.0));
          vec2 pf = fract(pg);
          vec2 fw = fwidth(pg);
          float aa = clamp(1.6 - max(fw.x, fw.y) * 3.0, 0.0, 1.0);
          float pane = step(0.05, pf.x) * step(pf.x, 0.95) * step(0.03, pf.y) * step(pf.y, 0.97);
          float inGlass = step(g0, y) * step(y, g1);
          float column = step(0.93, fract(vM.x / 12.0 + 0.04));
          float win = inGlass * mix(0.86, pane, aa) * (1.0 - column);
          float apron = 1.0 - step(g0, y);
          float door = step(0.84, bh(vec2(floor(vM.x / 4.5), seed))) * step(y, 3.4) * apron;
          vec3 wall = apron > 0.5 ? vec3(0.34, 0.36, 0.38) : vec3(0.60, 0.61, 0.62);
          wall *= 1.0 - 0.45 * step(g1, y) * step(y, g1 + 0.4) - 0.08 * step(0.92, fract((y - g1) / 1.4)) * step(g1, y) * aa;
          wall = mix(wall, vec3(0.70, 0.71, 0.72), step(vInfo.z - 0.6, y));
          #ifdef CUT
          if (y > uCutBand.x + 0.02 && y < uCutBand.y - 0.02 && cutAt(vQ, hall, win)) discard;
          #endif
          float w2 = max(win, door * 0.9);
          diffuseColor.rgb = mix(wall, vec3(0.3, 0.36, 0.42), w2);
          bRough = mix(0.6, 0.07, w2);
          bMetal = mix(0.3, 0.88, w2);
          float zone = (y - g0) / max(g1 - g0, 1.0);
          float cl = smoothstep(0.84, 0.88, zone) * (1.0 - smoothstep(0.92, 0.95, zone));
          float spot = 0.3 + 0.7 * smoothstep(0.3, 0.0, abs(fract(vM.x / 3.2) - 0.5));
          float bay = 0.55 + 0.45 * bh(vec2(floor(vM.x / 24.0), seed));
          float glow = (0.05 + 0.08 * smoothstep(0.4, 0.0, zone) + 1.1 * cl * mix(0.5, spot, aa)) * bay;
          bEmit = vec3(1.0, 0.86, 0.66) * (win * glow + door * 0.1 * step(0.5, bh(vec2(floor(vM.x / 4.5), seed + 3.0)))) * uNight;
        } else {
          vec2 cell = kind < 1.5 ? vec2(4.0, 5.0) : kind < 2.5 ? vec2(3.2, 3.1) : vec2(2.8, 3.6);
          vec2 g = vM / cell;
          vec2 id = floor(g);
          vec2 f = fract(g);
          vec2 fw = fwidth(g);
          float aa = clamp(1.6 - max(fw.x, fw.y) * 3.0, 0.0, 1.0);
          vec2 lo = kind < 1.5 ? vec2(0.05, 0.62) : kind < 2.5 ? vec2(0.28, 0.3) : kind < 3.5 ? vec2(0.06, 0.18) : vec2(0.0, 0.3);
          vec2 hi = kind < 1.5 ? vec2(0.95, 0.85) : kind < 2.5 ? vec2(0.72, 0.82) : kind < 3.5 ? vec2(0.94, 0.86) : vec2(1.0, 0.78);
          float win = step(lo.x, f.x) * step(f.x, hi.x) * step(lo.y, f.y) * step(f.y, hi.y);
          float cover = (hi.x - lo.x) * (hi.y - lo.y);
          if (kind < 1.5) { win *= step(0.5, bh(vec2(id.x, seed))); cover *= 0.5; }
          win = mix(cover, win, aa);
          vec3 wall;
          if (kind < 1.5) wall = mix(vec3(0.62, 0.64, 0.66), vec3(0.52, 0.56, 0.60), bh(vec2(seed, 1.0))) * (0.94 + 0.06 * sin(vM.x * 20.0) * aa);
          else if (kind < 2.5) wall = pick4(bh(vec2(seed, 7.0)), vec3(0.36, 0.21, 0.16), vec3(0.52, 0.49, 0.43), vec3(0.50, 0.52, 0.53), vec3(0.64, 0.63, 0.59));
          else if (kind < 3.5) wall = pick4(bh(vec2(seed, 9.0)), vec3(0.20, 0.25, 0.29), vec3(0.44, 0.44, 0.43), vec3(0.14, 0.17, 0.20), vec3(0.52, 0.50, 0.46));
          else wall = pick4(bh(vec2(seed, 5.0)), vec3(0.55, 0.55, 0.53), vec3(0.48, 0.49, 0.50), vec3(0.62, 0.60, 0.56), vec3(0.40, 0.42, 0.44));
          float gsel = bh(vec2(seed, 9.0));
          float glassTower = kind > 2.5 && kind < 3.5 && (gsel < 0.25 || (gsel > 0.5 && gsel < 0.75)) ? 1.0 : 0.0;
          vec3 glass = vec3(0.06, 0.07, 0.08);
          diffuseColor.rgb = mix(wall, glass, win);
          bRough = mix(0.7, 0.05, win);
          bMetal = mix(0.15, 0.7, win);
          if (glassTower > 0.5) { diffuseColor.rgb = mix(wall, glass, 0.6 + 0.4 * win); bRough = 0.1; bMetal = 0.6; }
          float frac = kind < 1.5 ? 0.6 : kind < 2.5 ? 0.38 : kind < 3.5 ? 0.3 : 0.35;
          float on = step(bh((kind > 3.5 ? vec2(floor(id.x / 5.0), id.y) : id) + seed * 13.1), frac);
          vec3 warm = mix(vec3(1.0, 0.70, 0.40), vec3(1.0, 0.86, 0.68), bh(id * 1.7 + seed));
          if (bh(id * 3.1 + seed) > 0.95) warm = vec3(0.62, 0.72, 1.0);
          float level = kind < 1.5 ? 0.35 : kind < 2.5 ? 0.45 : 0.4;
          float lit = mix(frac, on, aa) * win;
          bEmit = warm * lit * level * uNight;
        }`,
      )
      .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\nroughnessFactor = bRough;")
      .replace("#include <metalnessmap_fragment>", "#include <metalnessmap_fragment>\nmetalnessFactor = bMetal;")
      .replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\ntotalEmissiveRadiance += bEmit;");
  };
  m.customProgramCacheKey = () => (C ? "facade-cut" : "facade");
  return m;
}

class Parts {
  constructor() {
    this.geos = [];
  }
  push(g, color) {
    const c = new THREE.Color(color);
    const n = g.getAttribute("position").count;
    const col = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) col.set([c.r, c.g, c.b], i * 3);
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    if (g.getAttribute("uv")) g.deleteAttribute("uv");
    this.geos.push(g.index ? g.toNonIndexed() : g);
    return this;
  }
  box(x, y, z, sx, sy, sz, color, ry = 0) {
    const g = new THREE.BoxGeometry(sx, sy, sz);
    g.rotateY(ry);
    g.translate(x, y, z);
    return this.push(g, color);
  }
  cyl(x, y, z, r0, r1, h, color, seg = 16) {
    const g = new THREE.CylinderGeometry(r1, r0, h, seg);
    g.translate(x, y + h / 2, z);
    return this.push(g, color);
  }
  geometry() {
    const pos = [];
    const nor = [];
    const col = [];
    for (const g of this.geos) {
      pos.push(...g.getAttribute("position").array);
      nor.push(...g.getAttribute("normal").array);
      col.push(...g.getAttribute("color").array);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    g.computeBoundingSphere();
    return g;
  }
}

function inside(p, ring) {
  let c = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i];
    const b = ring[j];
    if (a[1] > p[1] !== b[1] > p[1] && p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]) c = !c;
  }
  return c;
}

function roofUnits(blocks, body) {
  let s = 41;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  for (const b of blocks) {
    const xs = b.outer.map((p) => p[0]);
    const ys = b.outer.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const n = Math.min(40, Math.round(Math.abs(area(b.outer)) / 1400));
    for (let i = 0, tries = 0; i < n && tries < n * 6; tries++) {
      const p = [x0 + rnd() * (x1 - x0), y0 + rnd() * (y1 - y0)];
      if (!inside(p, b.outer) || (b.holes || []).some((h) => inside(p, h))) continue;
      const w = 2 + rnd() * 4;
      const l = 2 + rnd() * 6;
      const h = 0.8 + rnd() * 1.8;
      const c = rnd() < 0.3 ? "#B4B8BB" : rnd() < 0.5 ? "#A3A7AA" : "#C4C7C9";
      body.box(p[0], b.height + h / 2, -p[1], w, h, l, c, rnd() * Math.PI);
      i++;
    }
  }
}

function controlTower(tw, body, glass, lamps) {
  const [x, y] = tw.xy;
  const z = -y;
  const top = tw.height;
  const cab0 = top - 22;
  body.cyl(x, 0, z, 14, 14, 9, "#9EA1A3", 24);
  body.cyl(x, 0, z, 5.6, 4.6, cab0 - 4, "#C9C9C4", 20);
  body.box(x + 4.2, (cab0 - 4) / 2, z, 2.4, cab0 - 4, 1.6, "#B9B9B4");
  body.cyl(x, cab0 - 4, z, 6.5, 8.6, 4, "#D2D2CC", 16);
  glass.cyl(x, cab0, z, 7.4, 8.8, 6.2, "#1F3A3E", 16);
  body.cyl(x, cab0 + 6.2, z, 9.4, 9.4, 1.2, "#D8D8D2", 16);
  body.cyl(x, cab0 + 7.4, z, 6.0, 4.0, 3.6, "#C2C2BC", 12);
  body.cyl(x, cab0 + 11, z, 0.35, 0.2, top - cab0 - 11, "#8A8A88", 6);
  lamps.add([x, y], top + 0.3, { col: "#FF2410", size: 1.2, intensity: 10, kind: 2, phase: 0 });
  lamps.add([x + 8.5, y], cab0 + 7.6, { col: "#FF2410", size: 0.8, intensity: 6 });
  lamps.add([x - 8.5, y], cab0 + 7.6, { col: "#FF2410", size: 0.8, intensity: 6 });
}

function centroid(ring) {
  let x = 0;
  let y = 0;
  for (const p of ring) {
    x += p[0];
    y += p[1];
  }
  return [x / ring.length, y / ring.length];
}

function fieldTexture(f) {
  const data = new Uint8Array(f.nx * f.ny * 4);
  for (let k = 0; k < f.nx * f.ny; k++) {
    data[k * 4] = Math.min(255, Math.round(f.edge[k] * 4));
    data[k * 4 + 1] = Math.min(255, Math.round(f.width[k] / 2));
    data[k * 4 + 2] = f.ridge[k] * 255;
    data[k * 4 + 3] = 255;
  }
  const t = new THREE.DataTexture(data, f.nx, f.ny, THREE.RGBAFormat);
  t.magFilter = t.minFilter = THREE.LinearFilter;
  t.needsUpdate = true;
  return t;
}

/** update(t, light): light is daylight 0..1; cut opens the home terminal's glass in front of the gate lounges. */
export function buildBuildings(stage, { cut = null } = {}) {
  const group = new THREE.Group();
  group.name = "buildings";
  const terms = massing(stage.terminals);
  const U = { uNight: { value: 0 }, uSnow: { value: 0 }, uField: { value: fieldTexture(terms.field) }, uFieldM: { value: new THREE.Vector4(terms.field.x0, terms.field.y0, 1 / (terms.field.nx * terms.field.res), 1 / (terms.field.ny * terms.field.res)) } };
  const mass = new Massing();
  terms.blocks.forEach((b, i) => {
    const kind = b.kind === "hall" ? KIND.hall : KIND.terminal;
    const info = [kind, i * 0.37 + 0.11, b.top];
    mass.walls(b.outer, 0, b.top, info);
    for (const h of b.holes) mass.walls(h, 0, b.top, info, true);
    mass.roof(b.outer, b.holes, b.top, [ROOF + kind, info[1], b.top]);
  });
  (stage.hangars || []).forEach((t, i) => {
    const info = [KIND.hangar, i * 0.53 + 0.29, t.height];
    mass.walls(t.outer, 0, t.height, info);
    for (const h of t.holes || []) mass.walls(h, 0, t.height, info, true);
    mass.roof(t.outer, t.holes, t.height, [ROOF + KIND.hangar, info[1], t.height]);
  });
  const tc = stage.terminals.map((t) => centroid(t.outer));
  const airportish = (c) => tc.some((p) => Math.hypot(p[0] - c[0], p[1] - c[1]) < 750);
  stage.buildings.forEach((b, i) => {
    const c = centroid(b.outer);
    const kind = b.height >= 38 ? KIND.tower : airportish(c) ? KIND.airport : KIND.house;
    const info = [kind, ((i * 0.6180339) % 1) * 97 + 1, b.height];
    mass.walls(b.outer, -2, b.height, info);
    for (const h of b.holes || []) mass.walls(h, -2, b.height, info, true);
    mass.roof(b.outer, b.holes, b.height, [ROOF + kind, info[1], b.height]);
  });
  const facades = new THREE.Mesh(mass.geometry(), facadeMaterial(U, cut));
  facades.castShadow = true;
  facades.receiveShadow = true;
  group.add(facades);

  const body = new Parts();
  const glass = new Parts();
  const lamps = new Lamps();
  roofUnits([...terms.blocks.map((b) => ({ outer: b.outer, holes: b.holes, height: b.top })), ...(stage.hangars || [])], body);
  if (stage.tower) controlTower(stage.tower, body, glass, lamps);
  for (const b of stage.buildings) {
    if (b.height < 60) continue;
    const c = centroid(b.outer);
    lamps.add(c, b.height + 1, { col: "#FF2410", size: 1.4, intensity: 7, kind: 2, phase: (c[0] * 0.013) % 1 });
  }
  const bodyMat = snowOn(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, metalness: 0.25 }), U.uSnow);
  const glassMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.25, metalness: 0.2, emissive: "#FFD9A0", emissiveIntensity: 0 });
  const bodyMesh = new THREE.Mesh(body.geometry(), bodyMat);
  bodyMesh.castShadow = bodyMesh.receiveShadow = true;
  const glassMesh = new THREE.Mesh(glass.geometry(), glassMat);
  group.add(bodyMesh, glassMesh);
  const LU = lampUniforms();
  LU.uHalo.value = 1.6;
  group.add(lampPoints(lamps, LU));
  function update(t, s, wall = 0) {
    const daylight = s.atm.day;
    U.uNight.value = smooth(0.9, 0.15, daylight);
    U.uSnow.value = s.snow;
    glassMat.emissiveIntensity = 0.07 * U.uNight.value;
    LU.uGain.value = 0.4 + 0.6 * (1 - daylight);
    LU.uTime.value = wall;
  }

  return { group, update };
}
