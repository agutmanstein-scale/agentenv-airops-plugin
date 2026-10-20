import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { buildVan, colored, placeVan, sample, vanAt } from "./crews.js";

const FIG = 1.15;
const CAP = 3200;
const LEGS = 1600;
const VANS = 3;
const SAY = 2;
const NEAR = 1500;
const ROW = 1.05;
const STEP = 0.72;
const SEAT = 0.62;
const DEG = Math.PI / 180;
const SKIN = ["#f0c8a8", "#e0b08c", "#c48a62", "#9a6644", "#6e4630", "#d8a07a"].map((c) => new THREE.Color(c));
const TOPS = ["#2f3d52", "#5b6b7d", "#7a2f35", "#3f5f4a", "#c9c2b5", "#1f2329", "#8a6d4b", "#4a5a8c", "#b5523b", "#d8d4cc", "#2d5d7b", "#6b4f7a", "#e3e1dc", "#394a3c"].map((c) => new THREE.Color(c));
const BOTTOMS = ["#1d2228", "#2b3442", "#3a3f45", "#4a4038", "#22303f", "#5d6670", "#2a2a2e"].map((c) => new THREE.Color(c));
const hash = (s) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
};

/** The glass bands buildings.js leaves see-through for the lounges: segments [x0, y0, x1, y1] with the lounge inside on
 * their left, grouped per lounge, the band's floor and ceiling and the lounges' depth. */
export function cutouts(doc, stage) {
  const { floor, ceil } = levels(doc, stage);
  const lounges = doc.lounges.map((lg) => {
    const segs = [];
    for (let i = 0; i + 1 < lg.p.length; i++) {
      let [a, b] = [lg.p[i], lg.p[i + 1]];
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      if (Math.hypot(dx, dy) < 0.05) continue;
      const nx = lg.n[i][0] + lg.n[i + 1][0];
      const ny = lg.n[i][1] + lg.n[i + 1][1];
      if (-dy * nx + dx * ny < 0) [a, b] = [b, a];
      segs.push([a[0], a[1], b[0], b[1]]);
    }
    const xs = lg.p.map((p) => p[0]);
    const ys = lg.p.map((p) => p[1]);
    const c = [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
    const r = Math.max(...lg.p.map((p) => Math.hypot(p[0] - c[0], p[1] - c[1]))) + doc.depth + 4;
    return { c, r, segs };
  });
  return { lounges, floor, ceil, depth: doc.depth };
}

/** The departure level's floor and the glass band's top on the home terminal, as buildings.js draws its walls. */
function levels(doc, stage) {
  const tall = stage.terminals[doc.terminal]?.height || 20;
  const low = Math.min(tall, Math.max(11, Math.min(15, tall * 0.62)));
  const top = tall > low + 2 ? low : tall;
  return { floor: doc.floor, ceil: Math.max(doc.floor + 3, Math.min(top - 2.2, 11)) };
}

/** The passengers at the hub (traffic.passengers, from occ/paxstage.py): the gate lounges behind the home terminal's
 * glass on the departure level, with their seats, each departure's crowd filling its lounge and boarding, connecting
 * passengers walking the concourse from gate to gate, the line at the service counter and the hotel shuttle at the curb.
 * The lounges, the counter, the concourse and the shuttle are display-only inferences from the stage's geometry. */
export function buildPassengers(doc, stage) {
  const group = new THREE.Group();
  group.name = "passengers";
  const { floor, ceil } = levels(doc, stage);
  const lounges = doc.lounges.map((lg, i) => lounge(lg, doc.depth, i === doc.counter));
  group.add(interiors(lounges, floor, ceil));
  const mat = figureMaterial();
  const kit = figureKit();
  const mesh = (geo, cap) => {
    const m = new THREE.InstancedMesh(geo, mat, cap);
    m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    m.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(cap * 3), 3).setUsage(THREE.DynamicDrawUsage);
    m.frustumCulled = false;
    m.count = 0;
    group.add(m);
    return m;
  };
  const M = {
    top: mesh(kit.top, CAP),
    seatedTop: mesh(kit.seatedTop, CAP),
    head: mesh(kit.head, CAP),
    legs: mesh(kit.legs, CAP),
    lap: mesh(kit.lap, CAP),
    leg: mesh(kit.leg, LEGS),
    bag: mesh(kit.bag, LEGS / 2),
  };
  const crowds = doc.crowds.map((c) => ({ ...c, seed: hash(`${c.flight}:${c.t[0]}`) }));
  const byLounge = lounges.map(() => []);
  for (const c of crowds) byLounge[c.lounge]?.push(c);
  const items = doc.groups.map(prepare).sort((a, b) => a.from - b.from);
  const vans = Array.from({ length: VANS }, () => buildVan());
  group.add(...vans);
  const queue = doc.queue || [];
  const desk = lounges[doc.counter];
  const B = new THREE.Matrix4();
  const X = new THREE.Matrix4();
  const Y = new THREE.Matrix4();
  const out = new THREE.Matrix4();
  const S = new THREE.Matrix4().makeScale(FIG, FIG, FIG);
  const at = [0, 0, 0, 0, 0];
  let n = {};
  let live = [];
  let line = 0;
  let shown = 0;

  const put = (x, y, z, h, look, seated, walking = false, phase = 0) => {
    if (n.head >= CAP) return false;
    B.makeRotationY(-h * DEG).multiply(S).setPosition(x, z + 0.02, -y);
    const top = seated ? M.seatedTop : M.top;
    const k = seated ? n.seatedTop++ : n.top++;
    top.setMatrixAt(k, B);
    top.setColorAt(k, look.top);
    M.head.setMatrixAt(n.head, out.multiplyMatrices(B, X.makeTranslation(0, seated ? 1.3 : 1.72, seated ? 0.06 : 0)));
    M.head.setColorAt(n.head++, look.skin);
    if (seated) {
      M.lap.setMatrixAt(n.lap, B);
      M.lap.setColorAt(n.lap++, look.bottom);
    } else if (walking && n.leg + 2 <= LEGS) {
      const swing = 0.4 * Math.sin(phase);
      for (const side of [-1, 1]) {
        X.makeTranslation(side * 0.1, 0.88, 0).multiply(Y.makeRotationX(side * swing));
        M.leg.setMatrixAt(n.leg, out.multiplyMatrices(B, X));
        M.leg.setColorAt(n.leg++, look.bottom);
      }
      if (look.bag && n.bag < LEGS / 2) {
        X.makeTranslation(0.3, 0, 0.5).multiply(Y.makeRotationX(-0.5));
        M.bag.setMatrixAt(n.bag, out.multiplyMatrices(B, X));
        M.bag.setColorAt(n.bag++, look.case);
      }
    } else {
      M.legs.setMatrixAt(n.legs, B);
      M.legs.setColorAt(n.legs++, look.bottom);
    }
    shown++;
    return true;
  };

  return {
    group,
    get count() {
      return shown;
    },
    get waiting() {
      return line;
    },
    /** Pose the passengers at minute T; wall is the wall clock in seconds, rate the replay's clock rate. */
    update(T, wall, rate = 0) {
      n = { top: 0, seatedTop: 0, head: 0, legs: 0, lap: 0, leg: 0, bag: 0 };
      shown = 0;
      live = [];
      lounges.forEach((lg, i) => {
        const here = byLounge[i].map((c) => ({ c, k: Math.round(crowdAt(c, T)) })).filter((x) => x.k > 0);
        here.sort((a, b) => a.c.t[0] - b.c.t[0]);
        let lo = 0;
        let hi = lg.slots.length - 1;
        here.forEach(({ c, k }, j) => {
          for (let p = 0; p < k && lo <= hi; p++) {
            const slot = lg.slots[lg.order[j % 2 ? hi-- : lo++]];
            const look = lookOf(c.seed, p);
            const sway = slot.seated ? 0 : Math.sin(wall * 0.6 + look.seed * 40) * 4;
            put(slot.x, slot.y, floor, slot.h + look.turn + sway, look, slot.seated);
          }
        });
      });
      line = stepAt(queue, T);
      if (desk) {
        for (let p = 0; p < Math.min(line, desk.queue.length); p++) {
          const q = desk.queue[p];
          const look = lookOf(0.37, p + 1000);
          put(q.x, q.y, floor, q.h + look.turn * 0.4, look, false);
        }
      }
      let v = 0;
      for (const it of items) {
        if (it.from - 3 > T) break;
        if (it.to + 3 < T) continue;
        const g = { it, anchor: null, say: null };
        for (const s of it.segs) {
          if (s.do !== "van" || v >= VANS) continue;
          const pose = vanAt(s, T);
          if (!pose) continue;
          placeVan(vans[v++], s, pose);
          g.anchor = [pose.x, pose.y, 3.4];
          g.say = s.say;
        }
        const k = it.segs.findIndex((s) => s.do !== "van" && T >= s.t[0] && T <= s.t[1]);
        if (k >= 0) {
          const s = it.segs[k];
          let sx = 0;
          let sy = 0;
          let m = 0;
          for (const p of it.people) {
            if (!personAt(it, k, T, p, at)) continue;
            const phase = rate > 20 ? wall * 11 + p.seed * 6.28 : at[2];
            if (put(at[0], at[1], s.z ?? 0, at[3], p.look, false, at[4] === 1, phase)) {
              sx += at[0];
              sy += at[1];
              m++;
            }
          }
          if (m) {
            g.anchor = [sx / m, sy / m, (s.z ?? 0) + 2.2 * FIG];
            g.say = s.say;
          }
        }
        if (g.anchor) live.push(g);
      }
      for (; v < VANS; v++) vans[v].visible = false;
      for (const [k, m] of Object.entries(M)) {
        m.count = n[k];
        m.instanceMatrix.needsUpdate = true;
        m.instanceColor.needsUpdate = true;
      }
    },
    /** Tags over the passengers near the camera: the line at the counter, a group walking or the hotel shuttle, those
     * of the featured aircraft's flights first; at most SAY. */
    tags(camera, flights = new Set()) {
      const cam = camera.position;
      const c = [];
      if (line > 0 && desk) {
        const [x, y] = desk.tagAt;
        c.push({ key: "pax:queue", anchor: [x, y, floor + 2.6], say: `Customer service · ${line} waiting to rebook`, rank: 1 });
      }
      for (const g of live) {
        if (!g.say) continue;
        const rank = g.it.flights.some((f) => flights.has(f)) ? 1 : 0;
        c.push({ key: `pax:${g.it.key}`, anchor: g.anchor, say: g.say, rank });
      }
      return c
        .map((x) => ({ ...x, d: Math.hypot(x.anchor[0] - cam.x, x.anchor[2] - cam.y, -x.anchor[1] - cam.z) }))
        .filter((x) => x.rank || x.d < NEAR)
        .sort((a, b) => b.rank - a.rank || a.d - b.d)
        .slice(0, SAY)
        .map((x) => ({
          key: x.key,
          matrix: new THREE.Matrix4().makeTranslation(x.anchor[0], 0, -x.anchor[1]),
          dims: { height: x.anchor[2], fin: 0 },
          text: x.say,
          tone: "pax",
          priority: 2 - x.rank,
          moving: true,
        }));
    },
    /** Every walking group, for a director: {key, kind, n, flights, from, to, says}. */
    list() {
      return items.map((it) => ({ key: it.key, kind: it.kind, n: it.n, flights: it.flights, from: it.from, to: it.to, says: [...new Set(it.segs.map((s) => s.say))] }));
    },
  };
}

/** A departure's crowd at its lounge at T: arriving from t[0], nearly all there by t[1], boarding from t[2] to t[3];
 * a cancelled one leaves for the counter within minutes of its cancellation. */
function crowdAt(c, T) {
  const [a, f, b, g] = c.t;
  let k = 0;
  if (T > a && T < g) k = T < f ? 0.94 * ((T - a) / (f - a)) ** 1.3 : T < b ? 0.94 + (0.06 * (T - f)) / Math.max(1, b - f) : 1 - (T - b) / Math.max(1, g - b);
  if (c.cancel != null && T >= c.cancel) k *= Math.max(0, 1 - (T - c.cancel) / 6);
  return k * c.n;
}

function stepAt(steps, T) {
  let v = 0;
  for (const [t, n] of steps) {
    if (t > T) break;
    v = n;
  }
  return v;
}

function lookOf(seed, k) {
  const s = hash(`${seed}:${k}`);
  const s2 = (s * 7919) % 1;
  const s3 = (s * 104729) % 1;
  return {
    seed: s,
    top: TOPS[Math.floor(s * TOPS.length)],
    bottom: BOTTOMS[Math.floor(s2 * BOTTOMS.length)],
    skin: SKIN[Math.floor(s3 * SKIN.length)],
    case: BOTTOMS[Math.floor(s3 * BOTTOMS.length)],
    bag: s2 < 0.55,
    turn: (s2 - 0.5) * 50,
  };
}

/** A lounge along its glass: where a point d metres along it and e in from it is, the seats and standing places a
 * crowd fills (seats first, by a stable shuffle), and on the counter's lounge the desk and its line. */
function lounge(raw, depth, counter) {
  const p = raw.p;
  const cum = [0];
  for (let i = 1; i < p.length; i++) cum.push(cum[i - 1] + Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]));
  const len = cum[cum.length - 1];
  const pos = (d, e) => {
    let i = 1;
    while (i < p.length - 1 && cum[i] < d) i++;
    const a = p[i - 1];
    const b = p[i];
    const L = cum[i] - cum[i - 1] || 1;
    const u = Math.min(1, Math.max(0, (d - cum[i - 1]) / L));
    const na = raw.n[i - 1];
    const nb = raw.n[i];
    const nx = na[0] + (nb[0] - na[0]) * u;
    const ny = na[1] + (nb[1] - na[1]) * u;
    const nl = Math.hypot(nx, ny) || 1;
    return [a[0] + (b[0] - a[0]) * u + (nx / nl) * e, a[1] + (b[1] - a[1]) * u + (ny / nl) * e, (Math.atan2(-nx, -ny) * 180) / Math.PI];
  };
  const slots = [];
  const benches = [];
  const r = rng(hash(raw.stand));
  const end = counter ? len - 23 : len - 1.6;
  for (const e of [3.3, 4.9, 6.5, 8.1]) {
    let k = 0;
    for (let d = 1.6; d < end; d += SEAT) {
      if (k++ % 10 === 9) {
        d += 0.9;
        continue;
      }
      const [x, y, h] = pos(d, e);
      slots.push({ x, y, h, seated: true, w: r() });
      benches.push({ x, y, h });
    }
  }
  for (const [e, gap] of [[1.1, 0.78], [1.9, 0.86], [2.5, 1.2]]) {
    for (let d = 1.2 + r() * 0.4; d < end; d += gap + r() * 0.3) {
      const [x, y, h] = pos(d, e + (r() - 0.5) * 0.3);
      slots.push({ x, y, h, seated: false, w: 0.6 + r() });
    }
  }
  const order = slots.map((_, i) => i).sort((a, b) => slots[a].w - slots[b].w);
  const out = { stand: raw.stand, p, n: raw.n, len, pos, slots, order, benches, depth, queue: [], tagAt: pos(len / 2, -1.5) };
  if (counter) {
    out.desk = pos(len - 4.5, depth - 1.6);
    out.tagAt = pos(len - 10, -1.5);
    const lanes = [5.6, 4.4, 3.2, 2.0];
    lanes.forEach((e, j) => {
      const from = len - 8;
      const to = len - 21;
      for (let k = 0; k <= 18; k++) {
        const d = j % 2 ? to + (k * (from - to)) / 18 : from - (k * (from - to)) / 18;
        const [x, y, h] = pos(d, e);
        out.queue.push({ x, y, h: j % 2 ? h + 90 : h - 90 });
      }
    });
    const [x, y, h] = pos(len - 7, 6.6);
    out.queue.unshift({ x, y, h: h + 180 });
  }
  return out;
}

function rng(seed) {
  let s = Math.floor(seed * 4294967296) >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

/** The lounges' rooms behind the glass: carpet, a lit ceiling, back and end walls, the glass itself, rows of seats, a
 * podium by each jet bridge door and the counter with its sign. */
function interiors(lounges, floor, ceil) {
  const g = new THREE.Group();
  const floors = [];
  const ceilings = [];
  const walls = [];
  const glass = [];
  for (const lg of lounges) {
    const front = [];
    const back = [];
    for (let i = 0; i < lg.p.length; i++) {
      const [x, y] = lg.p[i];
      const [nx, ny] = lg.n[i];
      front.push([x + nx * 0.06, y + ny * 0.06]);
      back.push([x + nx * lg.depth, y + ny * lg.depth]);
    }
    floors.push(band(front, back, floor, true));
    ceilings.push(band(front, back, ceil - 0.05, false));
    walls.push(wall(back, floor, ceil, true));
    walls.push(wall([front[0], back[0]], floor, ceil, false), wall([front.at(-1), back.at(-1)], floor, ceil, true));
    glass.push(wall(lg.p.map(([x, y], i) => [x + lg.n[i][0] * 0.03, y + lg.n[i][1] * 0.03]), floor, ceil, false));
  }
  const add = (geos, m) => {
    const mesh = new THREE.Mesh(mergeGeometries(geos), m);
    mesh.receiveShadow = false;
    mesh.castShadow = false;
    g.add(mesh);
    return mesh;
  };
  add(floors, new THREE.MeshLambertMaterial({ color: "#454c57", emissive: "#2a2e35", side: THREE.DoubleSide }));
  add(ceilings, new THREE.MeshBasicMaterial({ color: "#e9e2d4", side: THREE.DoubleSide, toneMapped: true }));
  add(walls, new THREE.MeshLambertMaterial({ color: "#cfc8bb", emissive: "#6b655c", side: THREE.DoubleSide }));
  const pane = add(glass, new THREE.MeshStandardMaterial({ color: "#a9c2d2", transparent: true, opacity: 0.16, roughness: 0.04, metalness: 0.7, depthWrite: false, side: THREE.DoubleSide }));
  pane.renderOrder = 2;
  const seats = lounges.flatMap((lg) => lg.benches);
  const seatGeo = mergeGeometries([colored(new THREE.BoxGeometry(SEAT - 0.06, 0.08, 0.5), "#2b3138", 0, 0.44, 0.02), colored(new THREE.BoxGeometry(SEAT - 0.06, 0.42, 0.06), "#2b3138", 0, 0.7, 0.26), colored(new THREE.BoxGeometry(0.05, 0.42, 0.05), "#8d949b", 0, 0.21, 0.05)]);
  const bench = new THREE.InstancedMesh(seatGeo, new THREE.MeshLambertMaterial({ vertexColors: true }), Math.max(1, seats.length));
  const M = new THREE.Matrix4();
  seats.forEach((s, k) => bench.setMatrixAt(k, M.makeRotationY(-s.h * DEG).setPosition(s.x, floor, -s.y)));
  bench.count = seats.length;
  g.add(bench);
  const podium = [];
  for (const lg of lounges) {
    const [x, y, h] = lg.pos(lg.len / 2 + 3.2, 2.6);
    podium.push(colored(new THREE.BoxGeometry(1.8, 1.05, 0.7), "#3a4a5e").rotateY(-h * DEG).translate(x, floor + 0.52, -y));
    if (lg.desk) {
      const [dx, dy, dh] = lg.desk;
      podium.push(colored(new THREE.BoxGeometry(6.5, 1.1, 0.9), "#2d4f7c").rotateY(-dh * DEG).translate(dx, floor + 0.55, -dy));
      podium.push(colored(new THREE.BoxGeometry(6.5, 0.06, 1.0), "#d9dde1").rotateY(-dh * DEG).translate(dx, floor + 1.12, -dy));
      for (const q of lg.queue.filter((_, k) => k % 6 === 1)) podium.push(colored(new THREE.CylinderGeometry(0.04, 0.05, 0.95, 6), "#9aa3ab").translate(q.x, floor + 0.47, -q.y));
    }
  }
  add(podium, new THREE.MeshLambertMaterial({ vertexColors: true }));
  const d = lounges.find((lg) => lg.desk);
  if (d) {
    const [x, y, h] = d.pos(d.len - 4.5, d.depth - 0.08);
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(6.2, 1.1), new THREE.MeshBasicMaterial({ map: signTexture(), toneMapped: false }));
    sign.position.set(x, floor + 2.9, -y);
    sign.rotation.y = -h * DEG;
    g.add(sign);
  }
  return g;
}

function band(a, b, y, up) {
  const pos = [];
  for (let i = 0; i + 1 < a.length; i++) {
    const q = [a[i], a[i + 1], b[i + 1], b[i]].map(([x, z]) => [x, y, -z]);
    const tri = up ? [0, 1, 2, 0, 2, 3] : [0, 2, 1, 0, 3, 2];
    for (const k of tri) pos.push(...q[k]);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.computeVertexNormals();
  return geo;
}

function wall(line, y0, y1, flip) {
  const pos = [];
  for (let i = 0; i + 1 < line.length; i++) {
    const [ax, ay] = line[i];
    const [bx, by] = line[i + 1];
    const q = [[ax, y0, -ay], [bx, y0, -by], [bx, y1, -by], [ax, y1, -ay]];
    for (const k of flip ? [0, 2, 1, 0, 3, 2] : [0, 1, 2, 0, 2, 3]) pos.push(...q[k]);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geo.computeVertexNormals();
  return geo;
}

function signTexture() {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = 180;
  const g = c.getContext("2d");
  g.fillStyle = "#0f2a4a";
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = "#f4f6f8";
  g.font = "600 92px Inter, 'Helvetica Neue', Arial, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText("Customer service", c.width / 2, c.height / 2 + 4);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function prepare(raw) {
  const segs = raw.segs.map((s) => {
    const p = s.p || [s.at];
    const cum = [0];
    for (let i = 1; i < p.length; i++) cum.push(cum[i - 1] + Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]));
    return { ...s, p, cum, len: cum[cum.length - 1] };
  });
  const people = [];
  const count = Math.min(raw.n, 60);
  for (let i = 0; i < count; i++) {
    const seed = hash(`${raw.key}:${i}`);
    const row = Math.floor(i / 2);
    people.push({ seed, look: lookOf(seed, i), back: row * ROW + (seed - 0.5) * 0.5, side: (count === 1 ? 0 : i % 2 ? 0.36 : -0.36) + (seed - 0.5) * 0.15 });
  }
  return { ...raw, segs, people, tail: Math.max(0, Math.ceil(count / 2) - 1) * ROW + 0.4, from: segs[0].t[0], to: segs[segs.length - 1].t[1] };
}

/** Person p of a group at T in its segment k: [x, y, gait phase, heading, walking] into out; false while not in sight. */
function personAt(it, k, T, p, out) {
  const s = it.segs[k];
  const next = it.segs[k + 1];
  if (s.do === "wait" || (next?.do === "wait" && T >= s.t[1] - 0.05)) {
    huddle(it, next?.do === "wait" ? next : s, p, out);
    return true;
  }
  const u = Math.min(1, Math.max(0, (T - s.t[0]) / Math.max(1e-6, s.t[1] - s.t[0])));
  const d = u * (s.len + it.tail) - p.back;
  if (d < 0 || d > s.len) {
    if (d > s.len && next?.do === "wait") {
      huddle(it, next, p, out);
      return true;
    }
    return false;
  }
  sample(s.p, s.cum, d, p.side, out);
  out[2] = (d / STEP) * Math.PI;
  out[4] = 1;
  return true;
}

function huddle(it, s, p, out) {
  const i = it.people.indexOf(p);
  const cols = Math.max(2, Math.ceil(Math.sqrt(it.people.length * 1.6)));
  const h = (s.h ?? 0) * DEG;
  const fx = Math.sin(h);
  const fy = Math.cos(h);
  const lat = ((i % cols) - (cols - 1) / 2) * 0.7 + (p.seed - 0.5) * 0.25;
  const back = 1.2 + Math.floor(i / cols) * 0.75 + (p.seed - 0.5) * 0.2;
  const at = s.at ?? s.p[0];
  out[0] = at[0] - fx * back + fy * lat;
  out[1] = at[1] - fy * back - fx * lat;
  out[2] = 0;
  out[3] = (s.h ?? 0) + (p.seed - 0.5) * 60;
  out[4] = 0;
}

/** A passenger, low-poly, feet at y = 0 and facing -z: the upper body standing or seated, a head, the legs standing,
 * seated (a lap and shins) or one leg hung from its hip for walking, and a roller bag. White, to take each instance's
 * colours. */
function figureKit() {
  const W = "#ffffff";
  const torso = (y) => [
    colored(new THREE.BoxGeometry(0.4, 0.56, 0.23), W, 0, y + 0.28, 0),
    colored(new THREE.BoxGeometry(0.1, 0.5, 0.11), W, -0.26, y + 0.26, 0),
    colored(new THREE.BoxGeometry(0.1, 0.5, 0.11), W, 0.26, y + 0.26, 0),
  ];
  const top = mergeGeometries(torso(1.04));
  const seatedTop = mergeGeometries(torso(0.62));
  const head = mergeGeometries([colored(new THREE.SphereGeometry(0.105, 10, 7), W, 0, 0, 0).scale(1, 1.12, 1), colored(new THREE.CylinderGeometry(0.045, 0.05, 0.1, 6), W, 0, -0.13, 0)]);
  const legs = mergeGeometries([colored(new THREE.BoxGeometry(0.15, 1.04, 0.17), W, -0.1, 0.52, 0), colored(new THREE.BoxGeometry(0.15, 1.04, 0.17), W, 0.1, 0.52, 0), colored(new THREE.BoxGeometry(0.38, 0.12, 0.2), W, 0, 0.98, 0)]);
  const lap = mergeGeometries([
    colored(new THREE.BoxGeometry(0.36, 0.15, 0.48), W, 0, 0.55, -0.12),
    colored(new THREE.BoxGeometry(0.14, 0.5, 0.14), W, -0.1, 0.25, -0.34),
    colored(new THREE.BoxGeometry(0.14, 0.5, 0.14), W, 0.1, 0.25, -0.34),
  ]);
  const leg = colored(new THREE.BoxGeometry(0.15, 0.88, 0.17), W, 0, -0.44, 0);
  const bag = mergeGeometries([colored(new THREE.BoxGeometry(0.34, 0.5, 0.22), W, 0, 0.3, 0), colored(new THREE.BoxGeometry(0.02, 0.36, 0.02), "#9aa0a6", 0, 0.73, 0.08)]);
  return { top, seatedTop, head, legs, lap, leg, bag };
}

function figureMaterial() {
  const m = new THREE.MeshLambertMaterial({ vertexColors: true });
  m.onBeforeCompile = (sh) => {
    sh.fragmentShader = sh.fragmentShader.replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\ntotalEmissiveRadiance += diffuseColor.rgb * 0.32;");
  };
  m.customProgramCacheKey = () => "pax-lit";
  return m;
}
