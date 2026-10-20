import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { aircraftLook } from "./aircraft.js";

const CAP = 420;
const FIG = 1.2;
/** Figures are drawn this much over life size so that people read as people from the gate and crew cameras (display
 * only, like the walkway they keep to). */
const VANS = 8;
const SAY = 2;
const NEAR = 1400;
const ROW = 1.7;
const STEP = 0.78;
const FADE = 30;
const SKIN = ["#f0c8a8", "#e0b08c", "#c48a62", "#9a6644", "#6e4630", "#d8a07a"].map((c) => new THREE.Color(c));
const UNIFORM = { vest: "#d2e83a", stripe: "#c3c8cc", navy: "#1a2b58", jacket: "#1f3466", trousers: "#15234a", shirt: "#f6f7f8", tie: "#0e1630", gold: "#dcb45c", scarf: "#6cb2ee", shoe: "#0b0b0c", hair: "#2b2019", bag: "#2a2e35", bagTop: "#3d434c", metal: "#9aa0a6", flightbag: "#151515" };
const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);
const hash = (s) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
};

/** The crews at the hub (traffic.crews, from occ/crewstage.py): pilots and cabin crews in uniform pulling their bags
 * along the terminal's airside face between the crew room, the gates and the curb, standing at the jet bridge until
 * they board, a pilot in a hi-vis vest walking round each departing aircraft, and the white vans at the curb. The crew
 * room, the walkway, the curb and the vans' road are display-only inferences from the stage's geometry. */
export function buildCrews(list = []) {
  const group = new THREE.Group();
  group.name = "crews";
  const items = list.map(prepare).sort((a, b) => a.from - b.from);
  const mat = floodlitMaterial();
  const kit = figureKit();
  const mesh = (geo, cap, shadow = true) => {
    const m = new THREE.InstancedMesh(geo, mat, cap);
    m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    m.frustumCulled = false;
    m.castShadow = shadow;
    m.receiveShadow = true;
    m.count = 0;
    group.add(m);
    return m;
  };
  const M = {
    pilot: mesh(kit.pilot, CAP),
    cabin: mesh(kit.cabin, CAP),
    head: mesh(kit.head, CAP),
    leg: mesh(kit.leg, CAP * 2),
    bag: mesh(kit.bag, CAP),
    flightbag: mesh(kit.flightbag, CAP),
    vest: mesh(kit.vest, CAP),
  };
  M.blob = new THREE.InstancedMesh(new THREE.CircleGeometry(0.5 * FIG, 16).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: "#000", transparent: true, opacity: 0.32, depthWrite: false }), CAP);
  M.blob.frustumCulled = false;
  M.blob.renderOrder = -1;
  M.blob.count = 0;
  group.add(M.blob);
  const byKey = new Map(items.map((it) => [it.key, it]));
  const S = new THREE.Matrix4().makeScale(FIG, FIG, FIG);
  M.head.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(CAP * 3), 3).setUsage(THREE.DynamicDrawUsage);
  const vans = Array.from({ length: VANS }, () => buildVan());
  group.add(...vans);

  const B = new THREE.Matrix4();
  const X = new THREE.Matrix4();
  const Y = new THREE.Matrix4();
  const out = new THREE.Matrix4();
  const at = [0, 0, 0, 0, 0];
  let live = [];
  let drawn = new Map();
  let now = 0;

  return {
    group,
    get count() {
      return live.reduce((n, g) => n + (g.shown || 0), 0);
    },
    /** Pose the crews about at minute T; rate is the replay's clock rate (0 paused), wall the wall clock in seconds;
     * a person inside a building (inside(x, y)), gone in at the end of a walk or passing through a pier's root, isn't
     * drawn, as one through the door at the end of the walk or up the jet bridge isn't. */
    update(T, wall, rate = 0, inside = null) {
      const n = { pilot: 0, cabin: 0, head: 0, leg: 0, bag: 0, flightbag: 0, vest: 0, blob: 0 };
      const huddles = new Map();
      let v = 0;
      live = [];
      drawn = new Map();
      now = T;
      for (const it of items) {
        if (it.from - 3 > T) break;
        if (it.to + 2 < T) continue;
        const g = { it, shown: 0, anchor: null, say: null, van: false };
        for (const s of it.segs) {
          if (s.do !== "van" || v >= VANS) continue;
          const pose = vanAt(s, T);
          if (!pose) continue;
          placeVan(vans[v++], s, pose);
          if (!g.anchor) {
            g.anchor = [pose.x, pose.y, 3.2];
            g.say = s.say;
            g.van = true;
          }
        }
        const k = it.segs.findIndex((s) => s.do !== "van" && T >= s.t[0] && T <= s.t[1]);
        if (k >= 0) {
          const s = it.segs[k];
          const w = s.do === "wait" ? s : it.segs[k + 1]?.do === "wait" ? it.segs[k + 1] : null;
          const spot = w && `${Math.round(w.p[0][0])},${Math.round(w.p[0][1])}`;
          const shift = spot ? huddles.get(spot) || 0 : 0;
          if (spot) huddles.set(spot, shift + Math.min(it.people.length, 3) * 0.85 * FIG + 1);
          const sum = [0, 0, 0];
          for (const p of it.people) {
            if (n.head >= CAP) break;
            if (!personAt(it, k, T, p, at, shift) || inside?.(at[0], at[1])) continue;
            const walking = at[4] === 1;
            sum[0] += at[0];
            sum[1] += at[1];
            sum[2] += walking;
            const phase = rate > 20 ? wall * 11.5 + p.seed * 6.28 : at[2];
            const swing = walking ? 0.42 * Math.sin(phase) : 0;
            const bob = walking ? 0.025 * Math.abs(Math.cos(phase)) : 0;
            B.makeRotationY(-at[3] * (Math.PI / 180)).multiply(S).setPosition(at[0], 0.04 + bob, -at[1]);
            M[p.kind].setMatrixAt(n[p.kind]++, B);
            M.blob.setMatrixAt(n.blob++, X.makeTranslation(at[0], 0.05, -at[1]));
            M.head.setMatrixAt(n.head, out.multiplyMatrices(B, X.makeTranslation(0, 1.76, 0)));
            M.head.setColorAt(n.head++, p.skin);
            for (const side of [-1, 1]) {
              X.makeTranslation(side * 0.1, 0.9, 0).multiply(Y.makeRotationX(side * swing));
              M.leg.setMatrixAt(n.leg++, out.multiplyMatrices(B, X));
            }
            if (it.kind === "walkaround") M.vest.setMatrixAt(n.vest++, B);
            else {
              if (walking) X.makeTranslation(0.3, 0, 0.52).multiply(Y.makeRotationX(-0.52));
              else X.makeTranslation(0.36, 0, 0.06);
              M.bag.setMatrixAt(n.bag++, out.multiplyMatrices(B, X));
              if (p.kind === "pilot") M.flightbag.setMatrixAt(n.flightbag++, out);
            }
            g.shown++;
            if (!g.anchor || g.van) {
              g.anchor = [at[0], at[1], 2.1 * FIG];
              g.say = s.say;
              g.van = false;
            }
          }
          if (g.shown) drawn.set(it.key, { n: g.shown, x: sum[0] / g.shown, y: sum[1] / g.shown, walking: sum[2] > 0 });
        }
        if (g.anchor) live.push(g);
      }
      for (const [k, m] of Object.entries(M)) {
        m.count = n[k];
        m.instanceMatrix.needsUpdate = true;
      }
      M.head.instanceColor.needsUpdate = true;
      for (; v < VANS; v++) vans[v].visible = false;
      return live;
    },
    /** A few tags over the crews near the camera: the one a crew camera follows (focus) as the featured tag, saying
     * when it comes out while it is yet to (over `at`, where the camera waits for it, if given), then the featured
     * aircraft's flights' crews, then the nearest; at most SAY. */
    tags(camera, flights = new Set(), focus = null, at = null) {
      const cam = camera.position;
      const c = [];
      const next = focus && !drawn.has(focus) ? byKey.get(focus) : null;
      const out = next?.segs.find((s) => s.do !== "van" && s.t[0] > now);
      if (out) {
        const f = at ?? (next.segs.some((x) => x.do === "van" && vanAt(x, now)) ? focusOf(next, now) : { x: out.p[0][0], y: out.p[0][1] });
        c.push({ g: { it: next, anchor: [f.x, f.y, 2.1 * FIG], say: `${out.say} · out at ${hhmm(out.t[0])}` }, d: 0, rank: 2 });
      }
      for (const g of live) {
        if (!g.say || (out && g.it === next)) continue;
        const d = Math.hypot(g.anchor[0] - cam.x, g.anchor[2] - cam.y, -g.anchor[1] - cam.z);
        const rank = g.it.key === focus ? 2 : g.it.flights.some((f) => flights.has(f)) ? 1 : 0;
        if (!rank && d > NEAR) continue;
        c.push({ g, d, rank });
      }
      c.sort((a, b) => b.rank - a.rank || a.d - b.d);
      return c.slice(0, SAY).map(({ g, rank }) => ({
        key: `crew:${g.it.key}`,
        matrix: new THREE.Matrix4().makeTranslation(g.anchor[0], 0, -g.anchor[1]),
        dims: { height: g.anchor[2], fin: 0 },
        text: g.say,
        tone: "crew",
        featured: rank === 2,
        priority: 2 - rank,
        moving: true,
      }));
    },
    /** Where group `key` is at T for a camera: {x, y, heading (degrees), kind: walk | wait | van, moving; a van's bus
     * flag and road [curb, far end]}, held at its first place before it appears and its last after it is gone; null
     * for a key it does not know. */
    focus(key, T) {
      const it = byKey.get(key);
      return it ? focusOf(it, T) : null;
    },
    /** The group a crew camera takes at T: of those drawn now (and that `ok` lets pass), the one in sight longest,
     * putting first those in sight for `hold` minutes or more, then the featured aircraft's flights' and those
     * walking; else the next to come into sight within the hour; null when none is. */
    pick(T, flights = new Set(), { ok = null, hold = 0 } = {}) {
      const score = (it) => {
        const left = Math.min(inSightUntil(it, T) - T, 60);
        return (left >= hold ? 0 : 1000) - left - (it.flights.some((f) => flights.has(f)) ? 15 : 0) - (drawn.get(it.key).walking ? 8 : 0);
      };
      const now = items.filter((it) => drawn.has(it.key)).sort((a, b) => score(a) - score(b));
      const best = now.find((it) => !ok || ok(it.key));
      if (best) return best.key;
      let next = null;
      for (const it of items) {
        if (it.from > T + 60) break;
        const t = it.segs.find((s) => s.do !== "van" && s.t[0] > T)?.t[0];
        if (t != null && t <= T + 60 && (!next || t < next[1])) next = [it.key, t];
      }
      return next?.[0] ?? null;
    },
    /** Where group `key`'s people drawn in the last update are: {n, x, y (their middle, ENU metres), walking}; null
     * when none is (not yet out of its door, gone in, boarded or in a van). */
    shown(key) {
      return drawn.get(key) ?? null;
    },
    /** Where group `key`'s people first come out on foot after T, as focus() gives a place: {x, y, heading, kind:
     * walk, moving: false, path: the walk's}; null when they don't, or while the van bringing them is about. */
    comesOut(key, T) {
      const it = byKey.get(key);
      const s = it?.segs.find((x) => x.do !== "van" && x.t[0] > T);
      if (!s || it.segs.some((x) => x.do === "van" && vanAt(x, T))) return null;
      const [a, b] = [s.p[0], s.p[1] ?? s.p[0]];
      return { x: a[0], y: a[1], heading: (Math.atan2(b[0] - a[0], b[1] - a[1]) * 180) / Math.PI, kind: "walk", moving: false, path: s.p };
    },
    /** The vans and buses in the scene now as [x0, y0, x1, y1, half width] ENU metres, for a camera to keep clear of. */
    vehicles() {
      return vans.filter((g) => g.visible).map((g) => {
        const h = -g.rotation.y;
        const half = g.userData.looks.bus.visible ? 6.6 : g.userData.looks.shuttle.visible ? 4.3 : 3.3;
        const [dx, dy] = [Math.sin(h) * half, Math.cos(h) * half];
        return [g.position.x - dx, -g.position.z - dy, g.position.x + dx, -g.position.z + dy, 1.5];
      });
    },
    /** Every group, for a director: {key, kind, crew, flights, stand, from, to, says}. */
    list() {
      return items.map((it) => ({ key: it.key, kind: it.kind, crew: it.crew, flights: it.flights, stand: it.stand, from: it.from, to: it.to, says: [...new Set(it.segs.map((s) => s.say))] }));
    },
  };
}

const hhmm = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(Math.floor(m % 60)).padStart(2, "0")}`;

function prepare(raw) {
  const segs = raw.segs.map((s) => {
    const p = s.p || [s.at];
    const cum = [0];
    for (let i = 1; i < p.length; i++) cum.push(cum[i - 1] + Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]));
    return { ...s, p, cum, len: cum[cum.length - 1] };
  });
  const people = [];
  const [pilots, cabin] = raw.crew;
  for (let i = 0; i < pilots + cabin; i++) {
    const seed = hash(`${raw.key}:${i}`);
    people.push({ kind: i < pilots ? "pilot" : "cabin", seed, skin: SKIN[Math.floor(seed * SKIN.length)] });
  }
  const rows = Math.ceil(people.length / 2);
  people.forEach((p, i) => {
    const row = Math.floor(i / 2);
    const alone = i === people.length - 1 && people.length % 2 === 1;
    p.back = row * ROW + (p.seed - 0.5) * 0.3;
    p.side = (alone ? 0 : (i % 2 ? 0.4 : -0.4) + (p.seed - 0.5) * 0.12) * FIG;
  });
  return { ...raw, segs, people, tail: (rows - 1) * ROW + 0.3, from: segs[0].t[0], to: segs[segs.length - 1].t[1] };
}

/** The minute until which a group's people stay in sight from T on: the end of the walks and waits it is in. */
function inSightUntil(it, T) {
  let k = it.segs.findIndex((s) => s.do !== "van" && T >= s.t[0] && T <= s.t[1]);
  if (k < 0) return T;
  let end = it.segs[k].t[1];
  while (it.segs[k + 1] && it.segs[k + 1].do !== "van" && it.segs[k + 1].t[0] <= end + 0.5) end = it.segs[++k].t[1];
  return end;
}

function focusOf(it, T) {
  const segs = it.segs;
  let k = segs.findIndex((s) => T >= s.t[0] && T <= s.t[1]);
  let u = null;
  if (k < 0) k = segs.findIndex((s) => s.do === "van" && T >= s.t[0] - (s.dir !== "in" ? s.park : 0) && T <= s.t[1] + (s.dir !== "out" ? s.park : 0));
  if (k < 0) {
    k = T < it.from ? 0 : Math.max(0, segs.findLastIndex((s) => s.t[1] <= T));
    u = T < it.from && !(segs[0].do === "van" && segs[0].dir === "out") ? 0 : 1;
  }
  const s = segs[k];
  const tt = u == null ? T : s.t[0] + u * (s.t[1] - s.t[0]);
  const o = [0, 0, 0, 0];
  if (s.do === "wait") {
    const h = (s.h ?? 0) * (Math.PI / 180);
    return { x: s.p[0][0] - Math.sin(h) * 1.6, y: s.p[0][1] - Math.cos(h) * 1.6, heading: s.h ?? 0, kind: "wait", moving: false };
  }
  if (s.do === "van") {
    const v = vanAt(s, Math.min(s.t[1], Math.max(s.t[0], tt)));
    const ends = [s.p[0], s.p[s.p.length - 1]];
    const road = s.dir === "via" ? null : s.dir === "out" ? ends : ends.reverse();
    return { x: v.x, y: v.y, heading: v.heading, kind: "van", bus: s.v === "bus", road, moving: tt > s.t[0] && tt < s.t[1], fade: v.fade };
  }
  const f = Math.min(1, Math.max(0, (tt - s.t[0]) / Math.max(1e-6, s.t[1] - s.t[0])));
  const d = Math.min(s.len, Math.max(0, f * (s.len + it.tail) - it.tail / 2));
  sample(s.p, s.cum, d, 0, o);
  return { x: o[0], y: o[1], heading: o[3], kind: "walk", moving: f > 0 && f < 1 };
}

/** Person p of a group at T in its segment k: [x, y, gait phase, heading] into out; false while it is not in sight
 * (not yet out of the door it comes from, or through the one it goes in at). */
function personAt(it, k, T, p, out, shift = 0) {
  const s = it.segs[k];
  const next = it.segs[k + 1];
  if (s.do === "wait" || (next?.do === "wait" && T >= s.t[1] - 0.05)) {
    huddle(it, next?.do === "wait" ? next : s, p, out, shift);
    return true;
  }
  const u = Math.min(1, Math.max(0, (T - s.t[0]) / Math.max(1e-6, s.t[1] - s.t[0])));
  let d = u * (s.len + it.tail) - p.back;
  if (d < 0) return false;
  if (d > s.len) {
    if (next?.do !== "wait") return false;
    huddle(it, next, p, out, shift);
    return true;
  }
  sample(s.p, s.cum, d, p.side, out);
  out[2] = (d / STEP) * Math.PI;
  out[4] = 1;
  return true;
}

/** Where person p of a waiting group stands: a loose huddle a little out from the door it boards at, facing it, moved
 * along by `shift` metres when another group already waits there. */
function huddle(it, s, p, out, shift = 0) {
  const n = it.people.length;
  const i = it.people.indexOf(p);
  const front = Math.ceil(n / 2);
  const row = n > 3 && i >= front ? 1 : 0;
  const inRow = row ? n - front : n > 3 ? front : n;
  const j = row ? i - front : i;
  const lat = ((j - (inRow - 1) / 2) * 0.72 + (p.seed - 0.5) * 0.2) * FIG + shift;
  const back = (1.1 + row * 0.85 + (p.seed - 0.5) * 0.25) * FIG;
  const h = (s.h ?? 0) * (Math.PI / 180);
  const fx = Math.sin(h);
  const fy = Math.cos(h);
  out[0] = s.p[0][0] - fx * back + fy * lat;
  out[1] = s.p[0][1] - fy * back - fx * lat;
  out[2] = 0;
  out[3] = (s.h ?? 0) - (lat - shift) * 14 + (p.seed - 0.5) * 30;
  out[4] = 0;
}

function sample(p, cum, d, side, out) {
  let i = 1;
  while (i < p.length - 1 && cum[i] < d) i++;
  if (p.length < 2) {
    out[0] = p[0][0];
    out[1] = p[0][1];
    out[3] = 0;
    return;
  }
  const a = p[i - 1];
  const b = p[i];
  const L = cum[i] - cum[i - 1] || 1;
  const u = Math.min(1, Math.max(0, (d - cum[i - 1]) / L));
  const dx = (b[0] - a[0]) / L;
  const dy = (b[1] - a[1]) / L;
  out[0] = a[0] + (b[0] - a[0]) * u + dy * side;
  out[1] = a[1] + (b[1] - a[1]) * u - dx * side;
  out[3] = (Math.atan2(dx, dy) * 180) / Math.PI;
}

/** A van's pose at T: driving its road over [t0, t1] (eased), standing at the curb `park` minutes after it drops a
 * crew off or before it picks one up; null when it is not there. */
function vanAt(s, T) {
  const [t0, t1] = s.t;
  const via = s.dir === "via";
  if (T < t0 - (s.dir !== "in" ? s.park : 0) || T > t1 + (s.dir !== "out" ? s.park : 0)) return null;
  const u = Math.min(1, Math.max(0, (T - t0) / Math.max(1e-6, t1 - t0)));
  const e = via ? u * u * (3 - 2 * u) : s.dir === "out" ? u * u * (1.6 - 0.6 * u) : 1 - (1 - u) * (1 - u) * (1.6 - 0.6 * (1 - u));
  const d = e * s.len;
  const o = [0, 0, 0, 0];
  sample(s.p, s.cum, d, 0, o);
  const far = via ? FADE : s.dir === "out" ? s.len - d : d;
  return { x: o[0], y: o[1], heading: o[3], fade: Math.min(1, far / FADE) };
}

function placeVan(g, s, pose) {
  g.visible = pose.fade > 0.02;
  g.position.set(pose.x, 0.02, -pose.y);
  g.rotation.set(0, -pose.heading * (Math.PI / 180), 0);
  const kind = s.v === "hotel" || s.v === "bus" ? s.v : "shuttle";
  for (const [name, m] of Object.entries(g.userData.looks)) m.visible = name === kind;
  g.userData.mat.opacity = pose.fade;
  g.userData.mat.transparent = pose.fade < 1;
  g.userData.mat.depthWrite = pose.fade >= 1;
}

function colored(geo, col, x = 0, y = 0, z = 0) {
  const c = new THREE.Color(col);
  geo.translate(x, y, z);
  const g = geo.index ? geo.toNonIndexed() : geo;
  g.deleteAttribute("uv");
  g.setAttribute("color", new THREE.Float32BufferAttribute(Array.from({ length: g.getAttribute("position").count }, () => [c.r, c.g, c.b]).flat(), 3));
  return g;
}

/** A pilot (navy jacket with gold stripes, white shirt and tie, cap) and a cabin crew member (navy, blue scarf) above
 * the hips (feet at y = 0, facing -z), one leg hanging from its hip, a head at its centre, a roller bag standing on its
 * wheels, the pilot's flight bag riding on it, and the high-visibility vest a pilot walking round the aircraft wears
 * on the ramp. */
function figureKit() {
  const U = UNIFORM;
  const torso = (col) => [
    colored(box(0.42, 0.58, 0.24), col, 0, 1.33, 0),
    colored(box(0.38, 0.16, 0.22), U.trousers, 0, 0.98, 0),
    colored(box(0.1, 0.52, 0.12), col, -0.27, 1.34, 0),
    colored(box(0.1, 0.52, 0.12), col, 0.27, 1.34, 0),
    colored(box(0.08, 0.08, 0.08), "#d6a684", -0.27, 1.04, 0),
    colored(box(0.08, 0.08, 0.08), "#d6a684", 0.27, 1.04, 0),
    colored(new THREE.CylinderGeometry(0.05, 0.055, 0.1, 8), "#d6a684", 0, 1.66, 0),
  ];
  const pilot = mergeGeometries([
    ...torso(U.navy),
    colored(box(0.13, 0.24, 0.02), U.shirt, 0, 1.5, -0.122),
    colored(box(0.035, 0.2, 0.025), U.tie, 0, 1.49, -0.128),
    colored(box(0.11, 0.025, 0.125), U.gold, -0.27, 1.12, 0),
    colored(box(0.11, 0.025, 0.125), U.gold, 0.27, 1.12, 0),
    colored(box(0.13, 0.03, 0.26), U.gold, -0.15, 1.62, 0),
    colored(box(0.13, 0.03, 0.26), U.gold, 0.15, 1.62, 0),
    colored(new THREE.CylinderGeometry(0.122, 0.116, 0.05, 14), U.gold, 0, 1.885, 0.005),
    colored(new THREE.CylinderGeometry(0.14, 0.124, 0.06, 14), U.navy, 0, 1.94, 0.01),
    colored(box(0.2, 0.018, 0.1), U.shoe, 0, 1.87, -0.12),
    colored(box(0.06, 0.03, 0.012), U.gold, 0, 1.925, -0.13),
  ]);
  const cabin = mergeGeometries([
    ...torso(U.jacket),
    colored(box(0.15, 0.08, 0.04), U.scarf, 0, 1.6, -0.115),
    colored(box(0.06, 0.12, 0.02), U.scarf, 0.03, 1.53, -0.13),
    colored(new THREE.SphereGeometry(0.118, 12, 8, 0, Math.PI * 2, 0, Math.PI * 0.55), U.hair, 0, 1.78, 0.012),
    colored(box(0.15, 0.12, 0.08), U.hair, 0, 1.73, 0.08),
  ]);
  const head = colored(new THREE.SphereGeometry(0.108, 12, 9), "#ffffff", 0, 0, 0).scale(1, 1.12, 1);
  const leg = mergeGeometries([colored(box(0.15, 0.86, 0.17), U.trousers, 0, -0.43, 0), colored(box(0.12, 0.08, 0.26), U.shoe, 0, -0.86, -0.05)]);
  const bag = mergeGeometries([
    colored(box(0.38, 0.56, 0.24), U.bag, 0, 0.34, 0),
    colored(box(0.34, 0.04, 0.2), U.bagTop, 0, 0.64, 0),
    colored(box(0.39, 0.03, 0.25), U.bagTop, 0, 0.34, 0),
    colored(new THREE.CylinderGeometry(0.04, 0.04, 0.05, 8).rotateZ(Math.PI / 2), "#0a0a0a", -0.14, 0.04, 0.08),
    colored(new THREE.CylinderGeometry(0.04, 0.04, 0.05, 8).rotateZ(Math.PI / 2), "#0a0a0a", 0.14, 0.04, 0.08),
    colored(box(0.018, 0.4, 0.018), U.metal, -0.1, 0.8, 0.08),
    colored(box(0.018, 0.4, 0.018), U.metal, 0.1, 0.8, 0.08),
    colored(box(0.22, 0.03, 0.03), "#111", 0, 1.0, 0.08),
  ]);
  const flightbag = mergeGeometries([colored(box(0.4, 0.27, 0.17), U.flightbag, 0, 0.8, -0.01), colored(box(0.16, 0.03, 0.04), U.metal, 0, 0.95, -0.01)]);
  const vest = mergeGeometries([
    colored(box(0.45, 0.46, 0.27), U.vest, 0, 1.36, 0),
    colored(box(0.46, 0.04, 0.28), U.stripe, 0, 1.24, 0),
    colored(box(0.46, 0.04, 0.28), U.stripe, 0, 1.44, 0),
  ]);
  return { pilot, cabin, head, leg, bag, flightbag, vest };
}

function buildVan() {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.45, metalness: 0.15 });
  const wheel = (x, z) => colored(new THREE.CylinderGeometry(0.36, 0.36, 0.26, 14).rotateZ(Math.PI / 2), "#121314", x, 0.36, z);
  const hotel = mergeGeometries([
    colored(box(2.02, 1.95, 5.6), "#f3f3f0", 0, 1.33, 0.25),
    colored(box(2.0, 0.95, 0.9), "#f3f3f0", 0, 0.95, -2.85),
    colored(box(1.96, 0.72, 0.1), "#1c252c", 0, 1.78, -2.36),
    colored(box(2.05, 0.55, 4.2), "#232c33", 0, 1.86, 0.55),
    colored(box(2.04, 0.18, 0.2), "#2a2c2e", 0, 0.48, -3.28),
    colored(box(1.9, 0.12, 0.08), "#ffd9a0", 0, 0.98, -3.31),
    wheel(-0.92, -2.0),
    wheel(0.92, -2.0),
    wheel(-0.92, 2.1),
    wheel(0.92, 2.1),
  ]);
  const shuttle = mergeGeometries([
    colored(box(2.4, 2.55, 8.6), "#eef0f1", 0, 1.62, 0),
    colored(box(2.45, 0.85, 7.4), "#1f2a32", 0, 2.1, 0.5),
    colored(box(2.3, 1.0, 0.1), "#1f2a32", 0, 2.15, -4.31),
    colored(box(2.45, 0.22, 8.6), "#3b5f86", 0, 1.1, 0),
    colored(box(2.2, 0.12, 0.08), "#ffd9a0", 0, 0.82, -4.32),
    wheel(-1.08, -2.9),
    wheel(1.08, -2.9),
    wheel(-1.08, 2.9),
    wheel(1.08, 2.9),
  ]);
  const bus = mergeGeometries([
    colored(box(2.9, 0.5, 13.2), "#d9dcdf", 0, 0.55, 0),
    colored(box(2.92, 1.75, 13.0), "#a9c4d4", 0, 1.68, 0),
    colored(box(2.95, 0.3, 13.2), "#e9ecee", 0, 2.7, 0),
    colored(box(2.75, 0.22, 12.8), "#cfd3d6", 0, 2.95, 0),
    colored(box(2.96, 1.75, 0.6), "#e9ecee", 0, 1.68, -3.0),
    colored(box(2.96, 1.75, 0.6), "#e9ecee", 0, 1.68, 1.6),
    colored(box(2.96, 1.75, 0.5), "#e9ecee", 0, 1.68, 5.5),
    colored(box(2.96, 0.14, 13.22), "#2d5f9a", 0, 0.86, 0),
    colored(box(2.6, 0.12, 0.08), "#ffd9a0", 0, 0.62, -6.62),
    wheel(-1.25, -4.8),
    wheel(1.25, -4.8),
    wheel(-1.25, 4.8),
    wheel(1.25, 4.8),
  ]);
  const looks = {};
  for (const [name, geo] of Object.entries({ hotel, shuttle, bus })) {
    const m = new THREE.Mesh(geo, mat);
    m.castShadow = true;
    m.receiveShadow = true;
    g.add(m);
    looks[name] = m;
  }
  g.userData = { looks, mat };
  g.visible = false;
  return g;
}

/** The figures' material: vertex colours, lit at night by the apron floodlights' pools as the aircraft are. */
function floodlitMaterial() {
  const m = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.8, metalness: 0.0 });
  m.onBeforeCompile = (sh) => {
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
        totalEmissiveRadiance += diffuseColor.rgb * (texture2D(uLightMap, luv).rgb * uPool * inL * 1.6 + 0.06 * uPool);
      }`,
    );
  };
  m.customProgramCacheKey = () => "crew-lit";
  return m;
}
