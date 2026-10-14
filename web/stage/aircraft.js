// Adapted from PortSimEnv v1 (FineEnvs, Apache-2.0): scene-kit.js
// Airliners built in code, in metres: nose along -z, wings along ±x, origin on the ground under the nose-gear contact.
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

const DEG = Math.PI / 180;
const NAVY = "#0b2a5c";

export const aircraftLook = { cabin: { value: 0 }, wet: { value: 0 }, beam: { value: 1 }, pool: { value: 0 }, lightMap: { value: null }, lightM: { value: new THREE.Vector4() } };

const LIVERIES = {
  jetblue: { body: "#f2f3f4", belly: "#e2e4e6", fin: NAVY, cowl: NAVY, wing: "#c3c7cb", strut: "#d6d9dc", pylon: "#e6e8ea" },
  other: { body: "#d6d8da", belly: "#c8cbcd", fin: "#d0d2d4", cowl: "#c2c5c8", wing: "#aaaeb2", strut: "#c6c9cc", pylon: "#c9ccce" },
};

const NOSES = {
  airbus: {
    s: [0, 0.15, 0.4, 0.8, 1.3, 1.9, 2.4, 3.0, 3.6, 4.3, 5.0, 5.6],
    top: [0.4, 0.47, 0.52, 0.575, 0.63, 0.68, 0.735, 0.83, 0.91, 0.965, 0.993, 1],
    bot: [0.4, 0.33, 0.27, 0.205, 0.14, 0.085, 0.05, 0.025, 0.01, 0, 0, 0],
    hw: [0, 0.21, 0.33, 0.45, 0.57, 0.68, 0.76, 0.84, 0.9, 0.95, 0.985, 1],
    w: 3.95,
    ck: [
      [[2.3, 0.04], [2.44, 0.98], [3.08, 0.86], [3.0, 0.04]],
      [[2.58, 1.22], [3.42, 1.96], [3.36, 1.1], [3.14, 0.98]],
      [[3.52, 2.04], [4.02, 2.28], [3.9, 1.32], [3.48, 1.14]],
    ],
  },
  b737: {
    s: [0, 0.15, 0.4, 0.8, 1.3, 1.9, 2.5, 3.1, 3.7, 4.4, 5.1, 5.8],
    top: [0.33, 0.385, 0.43, 0.49, 0.555, 0.625, 0.7, 0.8, 0.89, 0.95, 0.985, 1],
    bot: [0.33, 0.285, 0.24, 0.185, 0.13, 0.08, 0.045, 0.02, 0.005, 0, 0, 0],
    hw: [0, 0.16, 0.27, 0.38, 0.49, 0.6, 0.7, 0.79, 0.86, 0.92, 0.97, 1],
    w: 3.76,
    ck: [
      [[2.28, 0.04], [2.4, 0.88], [3.02, 0.8], [2.96, 0.04]],
      [[2.52, 1.16], [3.35, 1.84], [3.28, 1.02], [3.06, 0.92]],
      [[3.45, 1.92], [3.85, 2.08], [3.98, 1.25], [3.42, 1.06]],
    ],
  },
  ejet: {
    s: [0, 0.15, 0.4, 0.8, 1.3, 1.8, 2.3, 2.9, 3.5, 4.2, 5.0],
    top: [0.38, 0.44, 0.49, 0.55, 0.615, 0.68, 0.76, 0.86, 0.93, 0.98, 1],
    bot: [0.38, 0.32, 0.26, 0.19, 0.13, 0.08, 0.045, 0.02, 0.005, 0, 0],
    hw: [0, 0.2, 0.32, 0.44, 0.56, 0.67, 0.76, 0.85, 0.92, 0.97, 1],
    w: 3.01,
    ck: [
      [[1.98, 0.035], [2.08, 0.72], [2.62, 0.64], [2.56, 0.035]],
      [[2.18, 0.94], [2.95, 1.58], [2.88, 0.86], [2.68, 0.74]],
      [[3.05, 1.64], [3.45, 1.8], [3.35, 1.02], [3.0, 0.9]],
    ],
  },
  crj: {
    s: [0, 0.2, 0.6, 1.2, 1.9, 2.6, 3.3, 4.0, 4.8, 5.6],
    top: [0.36, 0.42, 0.48, 0.56, 0.64, 0.73, 0.84, 0.93, 0.98, 1],
    bot: [0.36, 0.31, 0.24, 0.16, 0.1, 0.055, 0.025, 0.008, 0, 0],
    hw: [0, 0.17, 0.3, 0.45, 0.6, 0.72, 0.83, 0.91, 0.97, 1],
    w: 2.69,
    ck: [
      [[2.5, 0.03], [2.62, 0.64], [3.2, 0.56], [3.12, 0.03]],
      [[2.78, 0.86], [3.6, 1.32], [3.5, 0.78], [3.3, 0.68]],
      [[3.7, 1.37], [4.15, 1.5], [4.05, 0.92], [3.68, 0.82]],
    ],
  },
  b787: {
    s: [0, 0.2, 0.6, 1.2, 2.0, 2.9, 3.8, 4.8, 5.9, 7.0, 8.0],
    top: [0.42, 0.48, 0.535, 0.6, 0.67, 0.75, 0.835, 0.91, 0.965, 0.993, 1],
    bot: [0.42, 0.36, 0.29, 0.21, 0.135, 0.075, 0.035, 0.012, 0, 0, 0],
    hw: [0, 0.2, 0.34, 0.48, 0.62, 0.74, 0.83, 0.9, 0.95, 0.985, 1],
    w: 5.77,
    ck: [
      [[3.15, 0.05], [3.32, 1.25], [4.1, 1.1], [4.0, 0.05]],
      [[3.55, 1.45], [4.85, 2.3], [4.75, 1.42], [4.25, 1.24]],
    ],
  },
};

function monotone(xs, ys) {
  const n = xs.length, d = [], m = [];
  for (let i = 0; i < n - 1; i++) d.push((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]));
  m[0] = d[0];
  m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (!d[i]) { m[i] = m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], q = a * a + b * b;
    if (q > 9) { m[i] = (3 / Math.sqrt(q)) * a * d[i]; m[i + 1] = (3 / Math.sqrt(q)) * b * d[i]; }
  }
  return (x) => {
    if (x <= xs[0]) return ys[0];
    if (x >= xs[n - 1]) return ys[n - 1];
    let i = 0;
    while (x > xs[i + 1]) i++;
    const h = xs[i + 1] - xs[i], t = (x - xs[i]) / h, t2 = t * t, t3 = t2 * t;
    return (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i] + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1];
  };
}

function noseProfile(kind, len, w) {
  const N = NOSES[kind], ref = N.s[N.s.length - 1];
  const u = N.s.map((s) => Math.sqrt(s / ref));
  const [top, bot, hw] = [N.top, N.bot, N.hw].map((ys) => monotone(u, ys));
  return { len, top: (s) => top(Math.sqrt(s / len)), bot: (s) => bot(Math.sqrt(s / len)), hw: (s) => hw(Math.sqrt(s / len)), ck: N.ck.map((q) => q.map(([s, a]) => new THREE.Vector2((s * len) / ref, (a * w) / N.w))) };
}

const A320 = {
  L: 37.57, span: 35.8, w: 3.95, h: 4.14, belly: 1.85,
  nose: { len: 5.6, kind: "airbus" },
  tail: { bot: 14.5, top: 7.6, wid: 12.5, end: 0.7, r: 0.3, exp: 1.8 },
  wing: { le: 12.0, root: 6.4, kink: 0.35, tip: 1.5, sweep: 27, teIn: 3, dih: 5.1, y: 0.62, t: [0.15, 0.105], tipType: "sharklet" },
  fair: [-1.8, 0.6, 7.6, 10.4, 0.15, 0.24],
  eng: { x: 5.75, r: 1.07, len: 4.9, fwd: 2.6, clear: 0.56, core: false },
  fin: { e: 8.5, root: 5.6, tip: 2.25, h: 5.9, sweep: 38, dorsal: 2.2 },
  htp: { e: 5.6, span: 12.45, root: 3.6, tip: 1.3, sweep: 33, dih: 6, y: 0.62 },
  gear: { nose: 5.05, main: 5.69, track: 7.59, mr: 0.585, mw: 0.42, mgap: 0.93, nr: 0.38, nw: 0.23, ngap: 0.55 },
  win: { first: 6.5, last: 8.8, pitch: 0.533, w: 0.23, h: 0.33, y: 0.6 },
  doors: [{ s: 5.3 }, { wing: 2.7, ow: 1 }, { wing: 3.35, ow: 1 }, { e: 8.0 }],
  door: [0.81, 1.85],
};

const E190 = {
  L: 36.24, span: 28.72, w: 3.01, h: 3.35, belly: 1.05,
  nose: { len: 5.0, kind: "ejet" },
  tail: { bot: 13.5, top: 7.4, wid: 12.0, end: 0.72, r: 0.18, exp: 1.8 },
  wing: { le: 13.4, root: 5.3, kink: 0.37, tip: 1.1, sweep: 26, teIn: 2, dih: 5.5, y: 0.42, t: [0.15, 0.105], tipType: "canted" },
  fair: [-1.4, 0.5, 6.2, 8.6, 0.14, 0.18],
  eng: { x: 4.55, r: 0.86, len: 3.9, fwd: 2.0, clear: 0.42, core: true },
  fin: { e: 8.2, root: 5.0, tip: 2.2, h: 6.0, sweep: 40, dorsal: 2.2 },
  htp: { e: 5.2, span: 10.0, root: 3.1, tip: 1.1, sweep: 32, dih: 5, y: 0.6 },
  gear: { nose: 3.6, main: 4.6, track: 5.94, mr: 0.5, mw: 0.33, mgap: 0.7, nr: 0.31, nw: 0.18, ngap: 0.42 },
  win: { first: 5.4, last: 7.6, pitch: 0.55, w: 0.25, h: 0.36, y: 0.6 },
  doors: [{ s: 4.4 }, { wing: 2.6, ow: 1 }, { e: 7.0 }],
  door: [0.8, 1.83],
};

const TEMPLATES = {
  a320: A320,
  a321: { ...A320, L: 44.51, wing: { ...A320.wing, le: 16.25 }, gear: { ...A320.gear, main: 5.7 },
    doors: [{ s: 5.3 }, { wing: -2.3 }, { wing: 8.9 }, { e: 8.0 }] },
  a220: {
    L: 38.7, span: 35.1, w: 3.7, h: 3.75, belly: 1.5,
    nose: { len: 5.4, kind: "airbus" },
    tail: { bot: 14.8, top: 8.0, wid: 13.0, end: 0.72, r: 0.2, exp: 1.8 },
    wing: { le: 13.3, root: 6.0, kink: 0.33, tip: 1.25, sweep: 28, teIn: 2, dih: 5, y: 0.5, t: [0.145, 0.1], tipType: "rakedS" },
    fair: [-1.6, 0.5, 7.2, 9.8, 0.14, 0.2],
    eng: { x: 5.3, r: 1.17, len: 4.3, fwd: 2.3, clear: 0.48, core: true },
    fin: { e: 9.0, root: 5.8, tip: 2.6, h: 6.2, sweep: 38, dorsal: 2.0 },
    htp: { e: 6.1, span: 11.6, root: 3.5, tip: 1.3, sweep: 32, dih: 6, y: 0.6 },
    gear: { nose: 3.9, main: 5.5, track: 6.7, mr: 0.53, mw: 0.38, mgap: 0.85, nr: 0.34, nw: 0.2, ngap: 0.5 },
    win: { first: 6.3, last: 8.4, pitch: 0.53, w: 0.27, h: 0.39, y: 0.6 },
    doors: [{ s: 5.2 }, { wing: 2.9, ow: 1 }, { e: 7.6 }],
    door: [0.8, 1.85],
  },
  e190: E190,
  regional: { ...E190, L: 31.68, span: 26.0, wing: { ...E190.wing, le: 11.3, root: 5.0 }, eng: { ...E190.eng, r: 0.76, len: 3.4, x: 4.2 },
    fin: { ...E190.fin, h: 5.4 }, htp: { ...E190.htp, span: 9.2 }, gear: { ...E190.gear, main: 4.4 } },
  regional_t: {
    L: 36.4, span: 24.85, w: 2.69, h: 2.69, belly: 0.8,
    nose: { len: 5.6, kind: "crj" },
    tail: { bot: 11.0, top: 5.5, wid: 10.0, end: 0.62, r: 0.16, exp: 1.6 },
    wing: { le: 16.2, root: 4.6, kink: 0.36, tip: 1.2, sweep: 28, teIn: 4, dih: 4.5, y: 0.3, t: [0.14, 0.1], tipType: "crj" },
    fair: [-1.2, 0.4, 5.0, 7.2, 0.14, 0.14],
    rear: { e: 10.6, r: 0.72, len: 3.7, gap: 0.5, dy: 0.18 },
    fin: { e: 7.6, root: 4.4, tip: 2.7, h: 4.0, sweep: 42, dorsal: 1.0, ttail: true },
    htp: { span: 8.5, root: 2.7, tip: 1.3, sweep: 33, dih: -2 },
    gear: { nose: 3.1, main: 3.9, track: 4.1, mr: 0.45, mw: 0.3, mgap: 0.62, nr: 0.29, nw: 0.17, ngap: 0.38 },
    win: { first: 6.6, last: 11.4, pitch: 0.508, w: 0.23, h: 0.32, y: 0.55 },
    doors: [{ s: 5.6 }, { wing: 1.6, ow: 1 }],
    door: [0.86, 1.78],
  },
  narrowbody: {
    L: 39.47, span: 35.79, w: 3.76, h: 4.01, belly: 1.3,
    nose: { len: 5.8, kind: "b737" },
    tail: { bot: 15.0, top: 8.0, wid: 13.5, end: 0.72, r: 0.2, exp: 1.8 },
    wing: { le: 13.2, root: 7.0, kink: 0.33, tip: 1.3, sweep: 28, teIn: 2, dih: 6, y: 0.5, t: [0.15, 0.105], tipType: "blended" },
    fair: [-1.8, 0.5, 8.0, 10.6, 0.14, 0.2],
    eng: { x: 4.95, r: 1.06, len: 4.3, fwd: 2.6, clear: 0.48, core: true, flat: true },
    fin: { e: 9.4, root: 6.6, tip: 2.4, h: 7.0, sweep: 36, dorsal: 4.0 },
    htp: { e: 6.0, span: 14.35, root: 4.2, tip: 1.3, sweep: 30, dih: 7, y: 0.58 },
    gear: { nose: 3.9, main: 6.3, track: 5.72, mr: 0.565, mw: 0.4, mgap: 0.86, nr: 0.34, nw: 0.2, ngap: 0.5 },
    win: { first: 6.4, last: 7.4, pitch: 0.508, w: 0.25, h: 0.36, y: 0.6 },
    doors: [{ s: 5.0 }, { wing: 2.6, ow: 1 }, { wing: 3.3, ow: 1 }, { e: 6.6 }],
    door: [0.86, 1.83],
  },
  widebody: {
    L: 62.8, span: 60.12, w: 5.77, h: 5.97, belly: 2.25,
    nose: { len: 8.0, kind: "b787" },
    tail: { bot: 21.0, top: 11.0, wid: 19.0, end: 0.72, r: 0.3, exp: 1.8 },
    wing: { le: 23.0, root: 11.2, kink: 0.32, tip: 2.0, sweep: 33, teIn: 4, dih: 6, y: 0.85, t: [0.14, 0.1], tipType: "raked" },
    fair: [-2.6, 0.8, 12.8, 16.8, 0.13, 0.3],
    eng: { x: 9.9, r: 1.72, len: 7.4, fwd: 4.4, clear: 0.95, core: true },
    fin: { e: 14.0, root: 9.4, tip: 3.6, h: 8.6, sweep: 40, dorsal: 3.0 },
    htp: { e: 8.4, span: 19.5, root: 6.0, tip: 1.9, sweep: 36, dih: 7, y: 0.62 },
    gear: { nose: 5.5, main: 7.8, track: 9.8, mr: 0.66, mw: 0.52, mgap: 1.4, nr: 0.53, nw: 0.33, ngap: 0.72, bogie: 1.5 },
    win: { first: 9.8, last: 11.0, pitch: 0.61, w: 0.28, h: 0.47, y: 0.58 },
    doors: [{ s: 8.3 }, { wing: -2.0 }, { wing: 14.0 }, { e: 10.2 }],
    door: [1.07, 1.93],
  },
};

const TIPS = {
  sharklet: { r: 0.6, phi: 80, h: 2.0, sweep: 40, c: 0.35 },
  blended: { r: 1.5, phi: 75, h: 1.3, sweep: 50, c: 0.5 },
  canted: { r: 0.35, phi: 62, h: 1.15, sweep: 42, c: 0.45 },
  crj: { r: 0.3, phi: 68, h: 1.0, sweep: 45, c: 0.45 },
  rakedS: { r: 7, phi: 10, h: 0, sweep: 50, c: 0.45 },
  raked: { r: 14, phi: 16, h: 0, sweep: 52, c: 0.5 },
};

const clamp01 = (t) => Math.min(1, Math.max(0, t));
const smooth = (a, b, x) => { const t = clamp01((x - a) / (b - a)); return t * t * (3 - 2 * t); };
const lerp = (a, b, t) => a + (b - a) * t;

function design(spec) {
  const T = TEMPLATES[spec.class] || TEMPLATES.narrowbody;
  const L = spec.length || T.L;
  const span = spec.span || T.span;
  const nl = T.nose.len, tl = T.tail.bot;
  const k = (L - nl - tl) / (T.L - nl - tl);
  const D = { ...T, L, span, sx: span / T.span, liv: spec.jetblue ? LIVERIES.jetblue : LIVERIES.other, np: noseProfile(T.nose.kind, nl, T.w) };
  D.wingLE = nl + (T.wing.le - nl) * k;
  D.at = (p) => (p.s ?? (p.wing !== undefined ? D.wingLE + p.wing : L - p.e));
  return D;
}

function section(D, s) {
  const { L, w, h, belly, nose: n, tail: t } = D;
  let top = belly + h, bot = belly, hw = w / 2;
  if (s < n.len) {
    top = belly + h * D.np.top(s);
    bot = belly + h * D.np.bot(s);
    hw = (w / 2) * D.np.hw(s);
  }
  const endY = belly + h * t.end;
  if (s > L - t.bot) bot = belly + (endY - t.r - belly) * Math.pow((s - L + t.bot) / t.bot, t.exp);
  if (s > L - t.top) top = belly + h - (belly + h - endY - t.r) * Math.pow((s - L + t.top) / t.top, 2);
  if (s > L - t.wid) hw = w / 2 - (w / 2 - t.r) * Math.pow((s - L + t.wid) / t.wid, 1.5);
  const f = D.fair, le = D.wingLE;
  const fair = smooth(le + f[0], le + f[1], s) * (1 - smooth(le + f[2], le + f[3], s));
  return { yc: (top + bot) / 2, hh: (top - bot) / 2, hw, fair, top, bot };
}

function ringXY(D, sec, th) {
  const c = Math.cos(th);
  let x = sec.hw * Math.sin(th), y = sec.yc + sec.hh * c;
  if (sec.fair > 0) {
    x *= 1 + sec.fair * D.fair[4] * smooth(0.3, -0.55, c);
    y -= sec.fair * D.fair[5] * smooth(-0.2, -1, c);
  }
  return [x, y];
}

const NR = 36;

function halfRing(D, s) {
  const sec = section(D, s);
  const pts = [];
  for (let k = 0; k <= NR / 2; k++) pts.push(ringXY(D, sec, (k / (NR / 2)) * Math.PI));
  const arc = [0];
  for (let k = 1; k < pts.length; k++) arc.push(arc[k - 1] + Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]));
  return { sec, pts, arc };
}

function arcWhere(D, s, f) {
  const { pts, arc } = halfRing(D, s);
  for (let k = 1; k < pts.length; k++) {
    const a = f(pts[k - 1]), b = f(pts[k]);
    if (a <= 0 && b > 0) return lerp(arc[k - 1], arc[k], -a / (b - a));
  }
  return 0;
}

function stations(D) {
  const { L, nose, tail } = D;
  const out = [];
  for (let i = 0; i < 28; i++) out.push(nose.len * Math.pow(i / 28, 2));
  const a = nose.len, b = L - tail.bot;
  const n = Math.max(6, Math.ceil((b - a) / 1.3));
  for (let i = 0; i < n; i++) out.push(a + ((b - a) * i) / n);
  for (let i = 0; i <= 22; i++) out.push(b + ((L - b) * i) / 22);
  return out;
}

function vcol(geo, color, skin) {
  const n = geo.attributes.position.count;
  const col = geo.attributes.color;
  if (!col || color) {
    const c = new THREE.Color(color);
    const out = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) out.set(col ? [c.r * col.getX(i), c.g * col.getY(i), c.b * col.getZ(i)] : [c.r, c.g, c.b], i * 3);
    geo.setAttribute("color", new THREE.BufferAttribute(out, 3));
  }
  if (skin && !geo.attributes.aSkin) geo.setAttribute("aSkin", new THREE.BufferAttribute(new Float32Array(n * 2).fill(-1), 2));
  for (const k of Object.keys(geo.attributes)) if (!["position", "normal", "color", "aSkin"].includes(k)) geo.deleteAttribute(k);
  if (!geo.index) geo.setIndex([...Array(n).keys()]);
  return geo;
}

class Parts {
  constructor(skin = false) {
    this.skin = skin;
    this.geos = [];
  }
  add(geo, color) {
    if (!geo.attributes.normal) geo.computeVertexNormals();
    this.geos.push(vcol(geo, color, this.skin));
    return this;
  }
  both(geo, color) {
    const m = mirrorX(geo);
    this.add(geo, color);
    return this.add(m, color);
  }
  merged() {
    const g = mergeGeometries(this.geos, false);
    for (const x of this.geos) x.dispose();
    this.geos = [];
    return g;
  }
}

function mirrorX(geo) {
  const g = geo.clone();
  const p = g.attributes.position, nm = g.attributes.normal;
  for (let i = 0; i < p.count; i++) {
    p.setX(i, -p.getX(i));
    if (nm) nm.setX(i, -nm.getX(i));
  }
  const idx = g.index.array;
  for (let i = 0; i < idx.length; i += 3) [idx[i + 1], idx[i + 2]] = [idx[i + 2], idx[i + 1]];
  return g;
}

function fuselage(D) {
  const S = stations(D);
  const pos = [], skin = [], col = [], idx = [];
  const body = new THREE.Color(D.liv.body), belly = new THREE.Color(D.liv.belly), c = new THREE.Color();
  const wy = D.belly + D.wing.y + D.w * 0.5 * Math.tan(D.wing.dih * DEG);
  for (const s of S) {
    const { sec, pts, arc } = halfRing(D, s);
    for (let k = 0; k < NR; k++) {
      const m = k <= NR / 2 ? k : NR - k;
      const sx = k <= NR / 2 ? 1 : -1;
      pos.push(sx * pts[m][0], pts[m][1], s);
      skin.push(Math.max(s, 0.001), arc[m]);
      const cth = Math.cos((m / (NR / 2)) * Math.PI);
      c.copy(body).lerp(belly, smooth(-0.15, -0.7, cth) * (0.35 + 0.65 * sec.fair));
      const ao = (1 - 0.07 * smooth(0, -1, cth)) * (1 - 0.22 * sec.fair * Math.exp(-(((pts[m][1] - wy) / 0.8) ** 2)) * smooth(0.2, 0.6, Math.abs(pts[m][0]) / D.w));
      col.push(c.r * ao, c.g * ao, c.b * ao);
    }
  }
  for (let i = 0; i < S.length - 1; i++) for (let k = 0; k < NR; k++) {
    const a = i * NR + k, b = i * NR + ((k + 1) % NR), cc = (i + 1) * NR + k, d = (i + 1) * NR + ((k + 1) % NR);
    idx.push(a, cc, b, b, cc, d);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("aSkin", new THREE.Float32BufferAttribute(skin, 2));
  g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

function foil(n, t, cam) {
  const up = [], lo = [];
  for (let i = 0; i < n; i++) {
    const x = (1 - Math.cos((Math.PI * i) / (n - 1))) / 2;
    const yt = 5 * t * (0.2969 * Math.sqrt(x) - 0.126 * x - 0.3516 * x * x + 0.2843 * x ** 3 - 0.1036 * x ** 4);
    const yc = cam * x * (1 - x) * (1.2 - 0.6 * x) * 2.4;
    up.push([x, yc + yt]);
    lo.push([x, yc - yt * 0.86]);
  }
  return [...up.slice().reverse(), ...lo.slice(1)];
}

function sect(le, c, t, { roll = 0, inc = 0, cam = 0, dir = null, up = null } = {}) {
  const d = dir ? new THREE.Vector3(...dir).normalize() : new THREE.Vector3(0, -Math.sin(inc), Math.cos(inc));
  const u0 = up ? new THREE.Vector3(...up) : new THREE.Vector3(-Math.sin(roll), Math.cos(roll), 0);
  const u = u0.sub(d.clone().multiplyScalar(u0.dot(d))).normalize();
  return { le: new THREE.Vector3(...le), c, t, cam, d, u };
}

function loft(secs, n = 13, cap = true, tone = null) {
  const M = 2 * n - 1;
  const pos = [], idx = [], col = [];
  for (const sc of secs) foil(n, sc.t, sc.cam).forEach(([px, py], j) => {
    const p = sc.le.clone().addScaledVector(sc.d, px * sc.c).addScaledVector(sc.u, py * sc.c);
    pos.push(p.x, p.y, p.z);
    const k = tone ? tone(px, j < n) : 1;
    col.push(k, k, k);
  });
  const span = secs[1].le.clone().sub(secs[0].le);
  const flip = span.clone().cross(secs[0].d).dot(secs[0].u) > 0;
  const tri = (a, b, c) => (flip ? idx.push(a, c, b) : idx.push(a, b, c));
  for (let i = 0; i < secs.length - 1; i++) for (let j = 0; j < M - 1; j++) {
    const a = i * M + j, b = a + 1, c = a + M, d = c + 1;
    tri(a, c, b);
    tri(b, c, d);
  }
  if (cap) {
    const base = (secs.length - 1) * M;
    const ctr = new THREE.Vector3();
    for (let j = 0; j < M; j++) ctr.add(new THREE.Vector3(pos[(base + j) * 3], pos[(base + j) * 3 + 1], pos[(base + j) * 3 + 2]));
    ctr.multiplyScalar(1 / M);
    pos.push(ctr.x, ctr.y, ctr.z);
    col.push(1, 1, 1);
    const ci = base + M;
    for (let j = 0; j < M - 1; j++) tri(base + j, ci, base + j + 1);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  if (tone) g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

const wingTone = (px, upper) => (upper ? (px < 0.11 ? 1.07 : px > 0.73 ? 0.9 : 1) : px > 0.76 ? 0.93 : 0.97);

function wingPlan(D) {
  const W = D.wing, sx = D.sx, b = D.span / 2;
  const tip = TIPS[W.tipType];
  const ts = { ...tip, r: tip.r * sx, h: tip.h * sx };
  const ext = ts.r * Math.sin(ts.phi * DEG) + ts.h * Math.cos(ts.phi * DEG);
  const xs = (D.w / 2) * 0.97, xt = b - ext, xk = W.kink * b;
  const tanS = Math.tan(W.sweep * DEG), tanD = Math.tan(W.dih * DEG);
  const root = W.root * Math.sqrt(sx), tipC = W.tip * sx;
  const leZ = (x) => D.wingLE + (x - xs) * tanS;
  const teR = D.wingLE + root, teK = teR + (xk - xs) * Math.tan(W.teIn * DEG), teT = leZ(xt) + tipC;
  const teZ = (x) => (x <= xk ? teR + (x - xs) * Math.tan(W.teIn * DEG) : lerp(teK, teT, (x - xk) / (xt - xk)));
  const yAt = (x) => D.belly + W.y + x * tanD;
  const inc = (x) => lerp(2.5, -0.5, x / xt) * DEG;
  const tAt = (x) => lerp(W.t[0], W.t[1], x / xt);
  return { xs, xt, xk, leZ, teZ, yAt, inc, tAt, tip: ts, chord: (x) => teZ(x) - leZ(x) };
}

function wing(D, P) {
  const secs = [];
  const add = (x) => secs.push(sect([x, P.yAt(x), P.leZ(x)], P.chord(x), P.tAt(x), { roll: D.wing.dih * DEG, inc: P.inc(x), cam: 0.022 }));
  for (const x of [0, P.xs, P.xk, lerp(P.xk, P.xt, 0.5), P.xt]) add(x);
  const tp = P.tip, x0 = P.xt, y0 = P.yAt(x0), z0 = P.leZ(x0), c0 = P.chord(x0);
  const total = (tp.phi * DEG) * tp.r + tp.h;
  const tanW = Math.tan(tp.sweep * DEG);
  let prev = [x0, y0], run = 0;
  const cen = [x0, y0 + tp.r];
  const pathAt = (q) => {
    const arcLen = (tp.phi * DEG) * tp.r;
    const l = q * total;
    if (l <= arcLen) {
      const a = l / tp.r;
      return { p: [cen[0] + tp.r * Math.sin(a), cen[1] - tp.r * Math.cos(a)], a };
    }
    const a = tp.phi * DEG, e = [cen[0] + tp.r * Math.sin(a), cen[1] - tp.r * Math.cos(a)];
    return { p: [e[0] + Math.cos(a) * (l - arcLen), e[1] + Math.sin(a) * (l - arcLen)], a };
  };
  for (const q of tp.h > 0 ? [0.12, 0.25, 0.4, 0.62, 1] : [0.35, 0.7, 1]) {
    const { p, a } = pathAt(q);
    run += Math.hypot(p[0] - prev[0], p[1] - prev[1]);
    prev = p;
    const c = lerp(c0, tp.c * D.sx, Math.pow(q, tp.h > 0 ? 0.8 : 1.3));
    secs.push(sect([p[0], p[1], z0 + run * tanW + (c0 - c) * (tp.h > 0 ? 0.15 : 0.6)], c, lerp(0.1, 0.08, q), { roll: a + D.wing.dih * DEG * (1 - q), inc: -0.5 * DEG }));
  }
  return loft(secs, 14, true, wingTone);
}

function lathe(profile, R, len, seg = 26) {
  return new THREE.LatheGeometry(profile.map(([r, a]) => new THREE.Vector2(Math.max(r * R, 0.0005), a * len)), seg).rotateX(Math.PI / 2);
}

function nacelle(E, paint, metal, dark, fan, liv, at) {
  const R = E.r, Ln = E.len;
  const place = (g) => {
    if (E.flat) {
      const p = g.attributes.position;
      for (let i = 0; i < p.count; i++) if (p.getY(i) < 0) { p.setY(i, p.getY(i) * 0.84); p.setX(i, p.getX(i) * 1.03); }
      g.computeVertexNormals();
    }
    return g.translate(at[0], at[1], at[2]);
  };
  const exit = E.core ? 0.66 : 1;
  metal.add(place(lathe([[0.82, 0.03], [0.84, 0.008], [0.87, 0.0], [0.91, 0.003], [0.945, 0.016], [0.97, 0.04], [0.985, 0.075]], R, Ln)), "#c9ccd0");
  paint.add(place(lathe([[0.985, 0.075], [0.997, 0.14], [1.0, 0.26], [0.99, 0.42], [0.955, 0.58 * exit + 0.04], [0.9, 0.8 * exit], [0.84, 0.94 * exit], [0.8, exit]], R, Ln)), liv.cowl);
  dark.add(place(lathe([[0.79, 0.15], [0.8, 0.09], [0.82, 0.03]], R, Ln)), "#1a1c20");
  const fd = new THREE.CircleGeometry(0.8 * R, 28).rotateY(Math.PI);
  fan.push(place(fd.translate(0, 0, 0.15 * Ln)));
  metal.add(place(lathe([[0.3, 0.155], [0.27, 0.12], [0.2, 0.085], [0.1, 0.062], [0.0, 0.055]], R, Ln, 16)), "#8d9196");
  if (E.core) {
    dark.add(place(lathe([[0.78, exit - 0.005], [0.66, exit + 0.01]], R, Ln, 20)), "#141518");
    metal.add(place(lathe([[0.66, exit - 0.02], [0.63, exit + 0.08], [0.57, exit + 0.2], [0.5, exit + 0.29]], R, Ln, 20)), "#73777c");
    dark.add(place(lathe([[0.49, exit + 0.29], [0.4, exit + 0.3]], R, Ln, 18)), "#16171a");
    metal.add(place(lathe([[0.4, exit + 0.28], [0.34, exit + 0.38], [0.18, exit + 0.46], [0.0, exit + 0.5]], R, Ln, 18)), "#5d6166");
  } else {
    dark.add(place(lathe([[0.8, 1.0], [0.62, 1.01]], R, Ln, 20)), "#141518");
    metal.add(place(lathe([[0.6, 0.98], [0.5, 1.05], [0.3, 1.12], [0.0, 1.17]], R, Ln, 18)), "#5d6166");
  }
}

function underwingEngines(D, P, paint, metal, dark, fan) {
  const E = D.eng, x = E.x * D.sx;
  const lz = P.leZ(x), y = E.clear + E.r, z = lz - E.fwd;
  for (const sx of [1, -1]) nacelle(E, paint, metal, dark, fan, D.liv, [sx * x, y, z]);
  const wy = P.yAt(x) - P.chord(x) * 0.06;
  const pyl = loft([
    sect([x, y + E.r * 0.82, z + E.len * 0.22], E.len * 0.95, 0.5 / (E.len * 0.95), { up: [-1, 0, 0], dir: [0, 0, 1] }),
    sect([x, wy, lz - 0.9], P.chord(x) * 0.55 + 0.9, 0.5 / (P.chord(x) * 0.55 + 0.9), { up: [-1, 0, 0], dir: [0, 0.06, 1] }),
  ], 9, false);
  paint.both(pyl, D.liv.pylon);
}

function rearEngines(D, paint, metal, dark, fan) {
  const E = D.rear, z = D.L - E.e;
  const sec = section(D, z + E.len * 0.5);
  const x = sec.hw + E.gap + E.r, y = sec.yc + D.h * E.dy;
  for (const sx of [1, -1]) nacelle({ ...E, core: true }, paint, metal, dark, fan, D.liv, [sx * x, y, z]);
  const st = loft([
    sect([sec.hw * 0.6, y, z + E.len * 0.3], E.len * 0.55, 0.36 / (E.len * 0.55), { roll: 0 }),
    sect([x - E.r * 0.7, y, z + E.len * 0.32], E.len * 0.5, 0.3 / (E.len * 0.5), { roll: 0 }),
  ], 9, false);
  paint.both(st, D.liv.pylon);
}

function fin(D) {
  const F = D.fin, sLE = D.L - F.e;
  const sTE = sLE + F.root;
  const y0 = section(D, sLE).top - 0.3, y1 = section(D, sTE).top - 0.3;
  const tanS = Math.tan(F.sweep * DEG);
  const at = (hgt) => [sLE + hgt * tanS, lerp(F.root, F.tip, hgt / F.h)];
  const up = [-1, 0, 0];
  const [z1, c1] = at(0.9);
  const [z2, c2] = at(F.h * 0.55);
  const [z3, c3] = at(F.h);
  const secs = [
    sect([0, y0, sLE - F.dorsal], F.root + F.dorsal, 0.1 * F.root / (F.root + F.dorsal), { up, dir: [0, y1 - y0, F.root + F.dorsal] }),
    sect([0, y0 + 0.9, z1], c1, Math.min(0.13, (0.11 * F.root) / c1), { up }),
    sect([0, y0 + F.h * 0.55, z2], c2, 0.1, { up }),
    sect([0, y0 + F.h, z3], c3, 0.09, { up }),
  ];
  return { geo: loft(secs, 12), tip: { y: y0 + F.h, z: z3, c: c3 } };
}

function htp(D, finTip) {
  const H = D.htp;
  const tanS = Math.tan(H.sweep * DEG), tanD = Math.tan(H.dih * DEG);
  let sLE, y;
  if (D.fin.ttail) {
    sLE = finTip.z - 0.25;
    y = finTip.y + 0.05;
  } else {
    sLE = D.L - H.e;
    const sec = section(D, sLE + H.root * 0.4);
    y = D.belly + D.h * H.y;
    y = Math.min(Math.max(y, sec.bot + 0.3), sec.top - 0.3);
  }
  const b = H.span / 2;
  const secs = [0, 0.35 * b, b].map((x, i) => sect([x, y + x * tanD, sLE + x * tanS], lerp(H.root, H.tip, x / b), [0.11, 0.1, 0.085][i], { roll: H.dih * DEG }));
  return loft(secs, 12, true, wingTone);
}

function antennas(D, paint) {
  const c = new THREE.Color(D.liv.body).multiplyScalar(0.9);
  const k = D.w / 3.95;
  for (const z of [D.nose.len + 3, D.wingLE + 5]) paint.add(new THREE.BoxGeometry(0.03, 0.24 * k, 0.3 * k).rotateX(0.5).translate(0, section(D, z).top + 0.08 * k, z), c);
  paint.add(new THREE.BoxGeometry(0.03, 0.22 * k, 0.3 * k).rotateX(-0.5).translate(0, section(D, D.nose.len + 2).bot - 0.07 * k, D.nose.len + 2), c);
}

function gear(D, P, metal, dark) {
  const G = D.gear, liv = D.liv;
  const sNG = G.nose, sMG = D.wingLE + G.main;
  const wheel = (x, y, z, r, w) => {
    dark.add(new THREE.CylinderGeometry(r, r, w, 18, 1).rotateZ(Math.PI / 2).translate(x, y, z), "#17181a");
    metal.add(new THREE.CylinderGeometry(r * 0.56, r * 0.56, w + 0.03, 12, 1).rotateZ(Math.PI / 2).translate(x, y, z), "#8f9397");
  };
  const ny = section(D, sNG).bot + 0.25;
  const k = G.nr / 0.38, len = ny - G.nr;
  metal.add(new THREE.CylinderGeometry(0.1 * k, 0.1 * k, len, 10).translate(0, (ny + G.nr) / 2, sNG), liv.strut);
  metal.add(new THREE.CylinderGeometry(0.15 * k, 0.15 * k, len * 0.5, 10).translate(0, ny - len * 0.25, sNG - 0.04), liv.strut);
  metal.add(new THREE.BoxGeometry(0.07 * k, len * 0.32, 0.3 * k).rotateX(0.35).translate(0, G.nr + len * 0.42, sNG + 0.16 * k), liv.strut);
  metal.add(new THREE.BoxGeometry(0.06 * k, 0.06 * k, 0.5 * k).rotateX(-0.6).translate(0, ny - 0.1, sNG - 0.3 * k), "#8f9397");
  for (const s of [-1, 1]) metal.add(new THREE.BoxGeometry(0.03, 0.55 * k, 1.1 * k).translate(s * 0.36 * k, ny - 0.22 * k, sNG + 0.65 * k), liv.body);
  metal.add(new THREE.BoxGeometry(G.ngap + G.nw * 0.6, 0.1, 0.12).translate(0, G.nr, sNG), "#8f9397");
  for (const s of [-1, 1]) wheel(s * G.ngap / 2, G.nr, sNG, G.nr, G.nw);
  const axles = G.bogie ? [-G.bogie / 2, G.bogie / 2] : [0];
  for (const s of [-1, 1]) {
    const x = (s * G.track) / 2;
    const top = P.yAt(Math.abs(x)) - 0.05;
    const r = G.mr * 0.24;
    metal.add(new THREE.CylinderGeometry(r * 0.7, r * 0.7, top - G.mr, 10).translate(x, (top + G.mr) / 2, sMG), liv.strut);
    metal.add(new THREE.CylinderGeometry(r, r, (top - G.mr) * 0.55, 10).translate(x, top - (top - G.mr) * 0.27, sMG), liv.strut);
    metal.add(new THREE.BoxGeometry(0.05, (top - G.mr) * 0.7, G.mr * 2).translate(x + s * (G.mgap / 2 + G.mw * 0.9), top - (top - G.mr) * 0.4, sMG + 0.15), liv.belly);
    if (G.bogie) metal.add(new THREE.BoxGeometry(0.22, 0.2, G.bogie + 0.3).translate(x, G.mr, sMG), "#8f9397");
    for (const dz of axles) {
      metal.add(new THREE.BoxGeometry(G.mgap + G.mw * 0.6, 0.12, 0.14).translate(x, G.mr, sMG + dz), "#8f9397");
      for (const w of [-1, 1]) wheel(x + (w * G.mgap) / 2, G.mr, sMG + dz, G.mr, G.mw);
    }
  }
  return sNG;
}

function flapTracks(D, P, metal) {
  for (const f of [0.42, 0.6, 0.78]) {
    const x = P.xs + (P.xt - P.xs) * f;
    if (D.eng && Math.abs(x - D.eng.x * D.sx) < 1.2) continue;
    const c = P.chord(x);
    const g = new THREE.SphereGeometry(1, 10, 6).scale(0.16 * Math.sqrt(D.sx), 0.26 * Math.sqrt(D.sx), c * 0.42);
    g.translate(x, P.yAt(x) - c * Math.sin(P.inc(x)) * 0.6 - 0.16, P.teZ(x) - c * 0.12);
    metal.both(g, D.liv.wing);
  }
}

function contactShadow(D, P, sNG) {
  const W = D.span + 8, H = D.L + 10, z0 = D.L / 2 - sNG;
  const cv = document.createElement("canvas");
  cv.width = 256;
  cv.height = Math.round((256 * H) / W / 4) * 4;
  const g = cv.getContext("2d");
  const k = cv.width / W;
  const X = (x) => (x + W / 2) * k, Y = (z) => (z - sNG - z0 + H / 2) * k;
  g.fillStyle = "#000";
  g.fillRect(0, 0, cv.width, cv.height);
  g.filter = `blur(${(2.2 * k).toFixed(1)}px)`;
  g.lineCap = "round";
  g.strokeStyle = "rgba(255,255,255,0.8)";
  g.lineWidth = D.w * 1.15 * k;
  g.beginPath();
  g.moveTo(X(0), Y(1));
  g.lineTo(X(0), Y(D.L - 2));
  g.stroke();
  g.fillStyle = "rgba(255,255,255,0.55)";
  for (const sx of [1, -1]) {
    g.beginPath();
    g.moveTo(X(0), Y(P.leZ(0)));
    g.lineTo(X(sx * P.xt), Y(P.leZ(P.xt)));
    g.lineTo(X(sx * P.xt), Y(P.teZ(P.xt)));
    g.lineTo(X(sx * P.xk), Y(P.teZ(P.xk)));
    g.lineTo(X(0), Y(P.teZ(0)));
    g.fill();
  }
  g.filter = `blur(${(0.8 * k).toFixed(1)}px)`;
  g.fillStyle = "rgba(255,255,255,0.5)";
  g.fillRect(X(-D.gear.track / 2 - 1), Y(D.wingLE + D.gear.main - 0.8), (D.gear.track + 2) * k, 1.6 * k);
  g.fillRect(X(-0.6), Y(D.gear.nose - 0.5), 1.2 * k, 1 * k);
  const tex = new THREE.CanvasTexture(cv);
  const geo = new THREE.PlaneGeometry(W, H).rotateX(-Math.PI / 2).translate(0, 0.05, z0);
  return { geo, mat: new THREE.MeshBasicMaterial({ color: 0x000000, alphaMap: tex, transparent: true, opacity: 0.62, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, toneMapped: false }) };
}

function fanTexture() {
  const cv = document.createElement("canvas");
  cv.width = cv.height = 256;
  const g = cv.getContext("2d");
  g.fillStyle = "#0d0e10";
  g.fillRect(0, 0, 256, 256);
  g.translate(128, 128);
  for (let i = 0; i < 24; i++) {
    g.rotate((Math.PI * 2) / 24);
    const grd = g.createLinearGradient(0, 0, 120, 0);
    grd.addColorStop(0, "#45494f");
    grd.addColorStop(1, "#2a2d31");
    g.fillStyle = grd;
    g.beginPath();
    g.moveTo(28, -3);
    g.quadraticCurveTo(80, -14, 127, -2);
    g.lineTo(127, 5);
    g.quadraticCurveTo(80, -4, 28, 4);
    g.fill();
  }
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

let SHARED = null;
function shared() {
  if (SHARED) return SHARED;
  const metal = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.42, metalness: 0.35, side: THREE.DoubleSide });
  const dark = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.78, metalness: 0.15, side: THREE.DoubleSide });
  const fan = new THREE.MeshStandardMaterial({ map: fanTexture(), roughness: 0.45, metalness: 0.5 });
  const gearMetal = metal.clone();
  const gearDark = dark.clone();
  for (const [m, snow] of [[metal, true], [dark, true], [gearMetal, false], [gearDark, false]]) wetten(m, snow);
  SHARED = { metal, dark, fan, gearMetal, gearDark, glow: glowMaterial(), beam: beamMaterial(), cone: beamCone() };
  return SHARED;
}

function wetten(m, snow) {
  m.onBeforeCompile = (sh) => {
    sh.uniforms.uWet = aircraftLook.wet;
    sh.fragmentShader = "uniform float uWet;\n" + sh.fragmentShader.replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\n roughnessFactor *= 1.0 - 0.45 * uWet;");
    if (snow) snowy(sh);
    floodlit(sh);
  };
  m.customProgramCacheKey = () => `ac-wet-${snow}-lit`;
}

/** The apron floodlights' pools (the ground's light map) on whatever stands in them. */
function floodlit(sh) {
  Object.assign(sh.uniforms, { uPool: aircraftLook.pool, uLightMap: aircraftLook.lightMap, uLightM: aircraftLook.lightM });
  sh.vertexShader = "varying vec3 vWP;\n" + sh.vertexShader.replace(
    "#include <begin_vertex>",
    `#include <begin_vertex>
    {
      vec4 wp4 = vec4(transformed, 1.0);
      #ifdef USE_INSTANCING
        wp4 = instanceMatrix * wp4;
      #endif
      vWP = (modelMatrix * wp4).xyz;
    }`,
  );
  sh.fragmentShader = "varying vec3 vWP;\nuniform float uPool;\nuniform sampler2D uLightMap;\nuniform vec4 uLightM;\n" + sh.fragmentShader.replace(
    "#include <emissivemap_fragment>",
    `#include <emissivemap_fragment>
    {
      vec2 luv = (vec2(vWP.x, -vWP.z) - uLightM.xy) * uLightM.zw;
      float inL = step(0.0, luv.x) * step(luv.x, 1.0) * step(0.0, luv.y) * step(luv.y, 1.0);
      float up = dot(normal, normalize((viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz));
      totalEmissiveRadiance += diffuseColor.rgb * texture2D(uLightMap, luv).rgb * uPool * inL * (0.55 + 0.45 * up) * 1.5 * step(vWP.y, 30.0);
    }`,
  );
}

/** Snow lying on the upward faces of a parked aircraft, per instance (aSnow 0..1). */
function snowy(sh) {
  sh.vertexShader = "attribute float aSnow;\nvarying float vSnow;\n" + sh.vertexShader.replace("#include <begin_vertex>", "#include <begin_vertex>\n vSnow = aSnow;");
  sh.fragmentShader = "varying float vSnow;\n" + sh.fragmentShader.replace(
    "#include <normal_fragment_maps>",
    `#include <normal_fragment_maps>
    {
      float up = dot(normal, normalize((viewMatrix * vec4(0.0, 1.0, 0.0, 0.0)).xyz));
      float lie = vSnow * smoothstep(0.25, 0.75, up);
      diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.93, 0.95, 0.98), lie);
      roughnessFactor = mix(roughnessFactor, 0.85, lie);
    }`,
  );
}

function skinMaterial(D) {
  const W = D.win;
  const m = new THREE.MeshPhysicalMaterial({ vertexColors: true, roughness: 0.34, metalness: 0.0, clearcoat: 0.7, clearcoatRoughness: 0.18 });
  const mid = D.wingLE - 1;
  const winArc = arcWhere(D, mid, ([, y]) => D.belly + D.h * W.y - y);
  const doors = D.doors.map((p) => {
    const ow = !!p.ow, s = D.at(p);
    const [w, h] = ow ? [0.51, 0.92] : D.door;
    const top = ow ? winArc - 0.36 : winArc - W.h / 2 - 0.3;
    return new THREE.Vector4(s, w, top, ow ? -h : h);
  });
  while (doors.length < 6) doors.push(new THREE.Vector4(-100, 0, 0, 0));
  const ck = D.np.ck.map((quad) => {
    let area = 0;
    for (let i = 0; i < 4; i++) area += quad[i].x * quad[(i + 1) % 4].y - quad[(i + 1) % 4].x * quad[i].y;
    return area < 0 ? quad.slice().reverse() : quad;
  }).flat();
  while (ck.length < 16) ck.push(new THREE.Vector2(-100, -100));
  const u = {
    uWin: { value: new THREE.Vector4(W.first, D.L - W.last, W.pitch, winArc) },
    uWinSize: { value: new THREE.Vector3(W.w, W.h, Math.min(W.w, W.h) * 0.42) },
    uDoors: { value: doors },
    uCk: { value: ck },
    uCabin: aircraftLook.cabin,
    uWet: aircraftLook.wet,
  };
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, u);
    sh.vertexShader = "attribute vec2 aSkin;\nvarying vec2 vSkin;\n" + sh.vertexShader.replace("#include <begin_vertex>", "#include <begin_vertex>\n vSkin = aSkin;");
    snowy(sh);
    floodlit(sh);
    sh.fragmentShader = SKIN_PARS + sh.fragmentShader
      .replace("#include <color_fragment>", "#include <color_fragment>\n" + SKIN_COLOR)
      .replace("#include <roughnessmap_fragment>", "#include <roughnessmap_fragment>\n roughnessFactor = mix(roughnessFactor * (1.0 - 0.45 * uWet), 0.08, gls);")
      .replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\n totalEmissiveRadiance += uCabin * cab * vec3(1.0, 0.74, 0.45) * 0.9;");
  };
  m.customProgramCacheKey = () => "ac-skin";
  return m;
}

const SKIN_PARS = `
varying vec2 vSkin;
uniform vec4 uWin;
uniform vec3 uWinSize;
uniform vec4 uDoors[6];
uniform vec2 uCk[16];
uniform float uCabin;
uniform float uWet;
float sdBox(vec2 p, vec2 b, float r) { vec2 q = abs(p) - b + r; return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r; }
float sdQuad(vec2 p, int o) {
  float d = -1e5;
  for (int i = 0; i < 4; i++) {
    vec2 a = uCk[o + i], b = uCk[o + (i + 1) % 4];
    vec2 e = b - a;
    vec2 n = normalize(vec2(e.y, -e.x));
    d = max(d, dot(p - a, n));
  }
  return d;
}
`;

const SKIN_COLOR = `
float gls = 0.0, cab = 0.0;
if (vSkin.x > 0.0) {
  vec2 p = vSkin;
  vec2 fw = max(fwidth(p), vec2(1e-4));
  float aa = max(fw.x, fw.y);
  float inRange = step(uWin.x - uWin.z * 0.5, p.x) * step(p.x, uWin.y + uWin.z * 0.5);
  float frame = 0.0;
  for (int i = 0; i < 6; i++) {
    vec4 dr = uDoors[i];
    if (dr.x < 0.0) continue;
    float h = abs(dr.w);
    vec2 q = vec2(p.x - dr.x, p.y - (dr.z + h * 0.5));
    float d = sdBox(q, vec2(dr.y, h) * 0.5, dr.w > 0.0 ? 0.12 : 0.07);
    frame = max(frame, (1.0 - smoothstep(0.012, 0.012 + aa * 1.5, abs(d))) * (1.0 - smoothstep(0.03, 0.09, aa)));
    if (dr.w > 0.0 && abs(q.x) < dr.y * 0.5 + uWin.z * 0.55) inRange = 0.0;
  }
  float k = floor((p.x - uWin.x) / uWin.z + 0.5);
  vec2 c = vec2(p.x - uWin.x - k * uWin.z, p.y - uWin.w);
  float dw = sdBox(c, uWinSize.xy * 0.5, uWinSize.z);
  float win = (1.0 - smoothstep(-aa * 0.5, aa * 0.5, dw)) * inRange;
  float near = 1.0 - smoothstep(0.02, 0.06, aa);
  float rim = (1.0 - smoothstep(0.0, 0.03, abs(dw - 0.018))) * inRange * near;
  vec2 cell = floor(vec2(p.x / 1.9, p.y / 1.05));
  float pv = fract(sin(dot(cell, vec2(12.9898, 78.233))) * 43758.5453);
  diffuseColor.rgb *= mix(1.0, 0.986 + 0.028 * pv, 1.0 - smoothstep(0.05, 0.2, aa));
  diffuseColor.rgb *= 1.0 - rim * 0.22;
  float band = (1.0 - smoothstep(uWinSize.y * 0.5 - fw.y, uWinSize.y * 0.5 + fw.y, abs(c.y))) * inRange * (uWinSize.x / uWin.z);
  win = mix(win, band, smoothstep(0.25, 0.75, fw.x / uWin.z));
  float ck = 0.0;
  for (int q = 0; q < 4; q++) if (uCk[q * 4].x > 0.0) ck = max(ck, 1.0 - smoothstep(-aa * 0.6, aa * 0.6, sdQuad(p, q * 4)));
  diffuseColor.rgb *= 1.0 - frame * 0.32;
  gls = max(win, ck);
  cab = win;
  diffuseColor.rgb = mix(diffuseColor.rgb, mix(vec3(0.05, 0.06, 0.075), vec3(0.025, 0.03, 0.04), ck), gls);
}
`;

function glowMaterial() {
  return new THREE.ShaderMaterial({
    uniforms: { ...THREE.UniformsUtils.clone(THREE.UniformsLib.fog), uPx: { value: 540 } },
    vertexShader: `
      attribute float aSize;
      attribute vec3 aColor;
      attribute vec3 aDir;
      uniform float uPx;
      varying vec3 vColor;
      varying float vGlare;
      #include <common>
      #include <fog_pars_vertex>
      #include <logdepthbuf_pars_vertex>
      void main() {
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        float px = aSize * projectionMatrix[1][1] * uPx / max(-mvPosition.z, 0.1);
        float on = step(0.001, aColor.r + aColor.g + aColor.b);
        gl_PointSize = on * clamp(px, 4.0, 64.0);
        vColor = aColor * clamp(px / 4.0, 0.75, 1.0);
        vGlare = 0.0;
        if (dot(aDir, aDir) > 0.5) {
          float k = max(dot(normalize((modelViewMatrix * vec4(aDir, 0.0)).xyz), normalize(-mvPosition.xyz)), 0.0);
          float f = 0.03 + 0.97 * pow(k, 8.0);
          vColor *= f;
          vGlare = on * pow(k, 14.0) * smoothstep(1.5, 12.0, px);
          gl_PointSize = min(gl_PointSize * (0.35 + 0.65 * f) * (1.0 + 3.0 * vGlare), 180.0);
        }
        #include <logdepthbuf_vertex>
        #include <fog_vertex>
      }`,
    fragmentShader: `
      varying vec3 vColor;
      varying float vGlare;
      #include <fog_pars_fragment>
      #include <logdepthbuf_pars_fragment>
      void main() {
        #include <logdepthbuf_fragment>
        float d = length(gl_PointCoord - 0.5) * 2.0;
        float g = 1.0 + 3.0 * vGlare;
        float a = (1.0 - smoothstep(0.0, 0.32 / g, d)) * 1.3 + exp(-d * d * 9.0 * g * g) * 0.35 + vGlare * exp(-d * d * 5.0) * 0.12;
        a *= 1.0 - smoothstep(0.8, 1.0, d);
        vec3 c = vColor * a;
        #ifdef USE_FOG
          #ifdef FOG_EXP2
            float ff = 1.0 - exp(-fogDensity * fogDensity * vFogDepth * vFogDepth);
          #else
            float ff = smoothstep(fogNear, fogFar, vFogDepth);
          #endif
          c *= 1.0 - ff * 0.6;
        #endif
        gl_FragColor = vec4(c, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
    fog: true,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
}

function beamMaterial() {
  return new THREE.ShaderMaterial({
    uniforms: { uBeam: aircraftLook.beam, uColor: { value: new THREE.Color(1, 0.95, 0.86) } },
    vertexShader: `
      varying float vAlong;
      varying vec3 vN;
      varying vec3 vV;
      varying float vY;
      #include <common>
      #include <logdepthbuf_pars_vertex>
      void main() {
        vAlong = -position.z;
        vY = (modelMatrix * vec4(position, 1.0)).y;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vV = -mv.xyz;
        vN = normalMatrix * normal;
        gl_Position = projectionMatrix * mv;
        #include <logdepthbuf_vertex>
      }`,
    fragmentShader: `
      uniform float uBeam;
      uniform vec3 uColor;
      varying float vAlong;
      varying vec3 vN;
      varying vec3 vV;
      varying float vY;
      #include <logdepthbuf_pars_fragment>
      void main() {
        #include <logdepthbuf_fragment>
        if (vY < 0.0) discard;
        float f = abs(dot(normalize(vN), normalize(vV)));
        float a = pow(f, 3.0) * pow(1.0 - clamp(vAlong, 0.0, 1.0), 2.2) * smoothstep(0.0, 0.05, vAlong) * 0.03 * uBeam;
        gl_FragColor = vec4(uColor * a, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    side: THREE.DoubleSide,
  });
}

function beamCone() {
  return new THREE.ConeGeometry(1, 1, 20, 1, true).rotateX(Math.PI / 2).translate(0, 0, -0.5);
}

const KIT = new Map();

function kit(spec) {
  const key = `${spec.class}|${(+spec.length || 0).toFixed(1)}|${(+spec.span || 0).toFixed(1)}|${spec.jetblue ? 1 : 0}`;
  if (KIT.has(key)) return KIT.get(key);
  const D = design(spec);
  const P = wingPlan(D);
  const paint = new Parts(true), metal = new Parts(), dark = new Parts(), fanGeos = [];
  paint.add(fuselage(D));
  const endY = D.belly + D.h * D.tail.end;
  dark.add(new THREE.CircleGeometry(D.tail.r * 0.85, 14).translate(0, endY, D.L + 0.01), "#202226");
  metal.both(wing(D, P), D.liv.wing);
  const f = fin(D);
  paint.add(f.geo, D.liv.fin);
  metal.both(htp(D, f.tip), D.fin.ttail ? D.liv.fin : D.liv.wing);
  if (D.rear) rearEngines(D, paint, metal, dark, fanGeos);
  else underwingEngines(D, P, paint, metal, dark, fanGeos);
  flapTracks(D, P, metal);
  const gearMetal = new Parts(), gearDark = new Parts();
  const sNG = gear(D, P, gearMetal, gearDark);
  antennas(D, paint);
  const geos = { paint: paint.merged(), metal: metal.merged(), dark: dark.merged(), fan: mergeGeometries(fanGeos), gearMetal: gearMetal.merged(), gearDark: gearDark.merged() };
  for (const g of Object.values(geos)) {
    g.translate(0, 0, -sNG);
    g.computeBoundingSphere();
  }
  const shadow = contactShadow(D, P, sNG);
  const tipX = D.span / 2 - P.tip.r * Math.sin(P.tip.phi * DEG) - P.tip.h * Math.cos(P.tip.phi * DEG);
  const tipY = P.yAt(tipX), tipZ = P.leZ(tipX) - sNG;
  const top = section(D, D.wingLE + 1);
  const low = halfRing(D, D.wingLE - 1.5);
  const lx = P.xs + 1.2;
  const lights = [
    ["beacon", [0, top.top + 0.12, D.wingLE + 1 - sNG], 0.7],
    ["beacon", [0, Math.min(...low.pts.map((p) => p[1])) - 0.1, D.wingLE - 1.5 - sNG], 0.7],
    ["navL", [-tipX - 0.08, tipY, tipZ + 0.35], 0.32],
    ["navR", [tipX + 0.08, tipY, tipZ + 0.35], 0.32],
    ["navT", [0, endY, D.L - sNG + 0.15], 0.3],
    ["strobe", [-tipX - 0.1, tipY, tipZ + 0.75], 1.7],
    ["strobe", [tipX + 0.1, tipY, tipZ + 0.75], 1.7],
    ["strobe", [0, endY + 0.12, D.L - sNG + 0.2], 1.4],
    ["landing", [-lx, P.yAt(lx) - 0.4, P.leZ(lx) + 0.5 - sNG], 1.5],
    ["landing", [lx, P.yAt(lx) - 0.4, P.leZ(lx) + 0.5 - sNG], 1.5],
    ["landing", [0, D.gear.nr * 2 + 0.55, -0.2], 1.3],
    ["taxi", [0, D.gear.nr * 2 + 0.3, -0.25], 1.0],
  ];
  const lightPos = new THREE.Float32BufferAttribute(lights.flatMap((l) => l[1]), 3);
  const lightSize = new THREE.Float32BufferAttribute(lights.map((l) => l[2]), 1);
  const lightDir = new THREE.Float32BufferAttribute(lights.flatMap((l) => (l[0] === "landing" || l[0] === "taxi" ? [0, -0.05, -1] : [0, 0, 0])), 3);
  const tris = Object.values(geos).reduce((n, g) => n + g.index.count / 3, 0);
  const K = {
    key, geos, lights, lightPos, lightSize, lightDir, tris, shadow, skin: skinMaterial(D),
    dims: { length: D.L, span: D.span, nose: sNG, height: f.tip.y + 0.2, fin: f.tip.z - sNG, lx, ly: P.yAt(lx) - 0.4, lz: P.leZ(lx) + 0.5 - sNG, ng: D.gear.nr * 2 + 0.4, main: D.wingLE + D.gear.main - sNG, belly: D.belly, width: D.w, doors: forwardDoors(D, sNG) },
  };
  KIT.set(key, K);
  return K;
}

const LIGHT_COLORS = {
  beacon: [7, 0.35, 0.18], navL: [3.6, 0.12, 0.08], navR: [0.15, 3.4, 1.1], navT: [3, 3, 3], strobe: [14, 14, 16], landing: [12, 11.4, 10.4], taxi: [9, 8.6, 7.8],
};

/** The kit of an aircraft spec ({class, length, span, jetblue}): geometries in metres about the nose-gear contact, the skin, the lights and the dimensions the stage needs. */
export function aircraftKit(spec) {
  return kit(spec);
}

export function aircraftMaterials() {
  return shared();
}

export { LIGHT_COLORS };

/** The sill of each passenger door ahead of the wing on the left side, [x, y, z] about the nose-gear contact. */
function forwardDoors(D, sNG) {
  const W = D.win;
  const winArc = arcWhere(D, D.wingLE - 1, ([, y]) => D.belly + D.h * W.y - y);
  return D.doors.filter((p) => !p.ow && D.at(p) < D.wingLE + 2).map((p) => {
    const s = D.at(p);
    const sill = winArc - W.h / 2 - 0.3 + D.door[1];
    const { pts, arc } = halfRing(D, s);
    let k = 1;
    while (k < arc.length - 1 && arc[k] < sill) k++;
    const f = (sill - arc[k - 1]) / (arc[k] - arc[k - 1] || 1);
    const x = lerp(pts[k - 1][0], pts[k][0], f);
    const y = lerp(pts[k - 1][1], pts[k][1], f);
    return [-x, y, s - sNG];
  });
}
