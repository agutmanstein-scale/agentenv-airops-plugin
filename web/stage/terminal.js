import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { colored } from "./crews.js";
import { local as along } from "./passengers.js";

const DEG = Math.PI / 180;
const EYE = 3.8;
const BACK = 0.5;
const KEEP = 2.6;
const FOV = 56;
const LOOK = 20;
const REACH = 16;
const DRESS = 34;
const DRIFT = 1.8;
const SIGN = 4.5;
/** The camera's eye over the floor and its place in from the hall's back wall, the figures about it it keeps out of
 * sight, its field of view, the bearing off the glass it prefers, how far either side of the gate's door it may stand,
 * how far either side of it the hall is dressed, how far a director's shot drifts along the hall and how near it a
 * screen or gate sign is left out. */
const ARRIVE = 110;
const KNOWN = 120;
const HELD = 30;
const BOARD = 35;
const LIT = 10;
/** Minutes before pushback a departure's crowd starts to arrive (occ/paxstage.py ARRIVE), its delay is known
 * (core/known.py LOOK), its gate shows a hold for connecting passengers and starts boarding; the sun's least elevation
 * for a moment in daylight. */
const TONES = { ok: "#2fae6b", board: "#2fae6b", delay: "#e59a1a", hold: "#3d8fe6", cancel: "#e0463a", gone: "#6d7c88" };
const FONT = "Inter, 'Helvetica Neue', Arial, sans-serif";
const pad2 = (n) => String(n).padStart(2, "0");
const hhmm = (m) => `${pad2(Math.floor((((Math.round(m) % 1440) + 1440) % 1440) / 60))}:${pad2(((Math.round(m) % 60) + 60) % 60)}`;

/** Inside the home terminal at its gates (the Terminal view, a day with passengers): each gate's departure on a screen
 * over its podium and a gate sign by its jet bridge door, the glass's mullions and rails and the ceiling's lights,
 * drawn about the gate in view; the camera at the back of the hall looking out past the crowd through the glass at the
 * aircraft on the stand; the busiest gate and a moment to open on. JetBlue's gate areas aren't public: the layout is
 * generic, for display. */
export function buildTerminal(pax, stage, traffic, { home, bridges }) {
  const group = new THREE.Group();
  group.name = "terminal";
  group.visible = false;
  const { floor, hall, lounges } = pax;
  const stands = new Map(stage.stands.map((s) => [s.ref, s]));
  const gateOf = (ref) => (ref.startsWith(`${home}-`) ? ref.slice(home.length + 1) : ref.replace("-", " "));
  const deps = lounges.map((lg) => traffic.aircraft.filter((a) => a.task)
    .flatMap((a) => a.visits.filter((v) => v.stand === lg.stand && v.dep?.tag).map((v) => ({ leg: v.dep, key: a.key })))
    .sort((a, b) => leaves(a.leg) - leaves(b.leg)));
  const runOf = new Map();
  const own = [];
  for (const run of pax.halls) run.ks.forEach((k, i) => runOf.set(k, { run, at: run.at[i] }));
  const ray = new THREE.Raycaster();
  const solid = new THREE.MeshLambertMaterial({ vertexColors: true });
  const glow = new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false });
  const screens = [0, 1, 2].map(() => sign(1.92, 1.08, 768, 432));
  const signs = [0, 1, 2].map(() => sign(1.5, 0.62, 600, 248));
  for (const x of [...screens, ...signs]) group.add(x.mesh);
  const cameras = new Map();
  let dressed = null;

  function departure(k, T, flight) {
    const end = (l) => (l.status === "cancelled" ? (l.decided ?? l.sched) + 45 : l.out + 6);
    const list = deps[k] || [];
    const now = (d) => leaves(d.leg) - ARRIVE <= T && T < end(d.leg);
    return list.find((d) => d.leg.flight === flight && T < end(d.leg)) ?? list.find(now) ?? list.find((d) => leaves(d.leg) - ARRIVE > T) ?? null;
  }

  /** The departures screen's status for a leg at T: CANCELLED once decided, HELD for its connecting passengers from
   * half an hour before it would have left, BOARDING, DELAYED once known, else ON TIME; DEPARTED after pushback. */
  function status(l, T) {
    if (l.status === "cancelled") return T >= (l.decided ?? l.sched) ? ["CANCELLED", "cancel"] : ["ON TIME", "ok"];
    if (T >= l.out) return ["DEPARTED", "gone"];
    const p = l.pax;
    if (p?.held && T >= Math.min(l.out - p.held, l.sched) - HELD) return [p.conn ? `HELD for ${p.conn} connecting` : "HELD", "hold"];
    if (T >= l.out - BOARD) return ["BOARDING", "board"];
    if (l.out - l.sched > 10 && T >= l.sched - KNOWN) return [`DELAYED ${hhmm(l.out)}`, "delay"];
    return ["ON TIME", "ok"];
  }

  /** What gate area k shows at T: its gate, people and line, and its departure's (`flight` when it is one of the gate's
   * yet to leave) flight, city, time and status. */
  function info(k, T, flight = null) {
    const lg = lounges[k];
    const d = departure(k, T, flight);
    const [text, tone] = d ? status(d.leg, T) : ["", "ok"];
    return { lounge: k, stand: lg.stand, gate: gateOf(lg.stand), counter: k === pax.counter, people: pax.at(k, T), line: k === pax.counter ? pax.line(T) : 0,
      flight: d?.leg.flight ?? null, city: d?.leg.city ?? null, time: d ? hhmm(d.leg.sched) : null, status: text, tone, key: d?.key ?? null };
  }

  /** Where a point (x, y) is in gate k's hall: d along it and e in from its glass. */
  const local = (k, x, y) => along(runOf.get(k).run, x, y);

  /** Whether (x, y) is in gate k's lounge or just outside its glass. */
  function within(k, x, y) {
    const lg = lounges[k];
    own[k] ??= { marks: Array.from({ length: Math.floor(lg.len / 0.5) + 1 }, (_, i) => [...lg.pos(i * 0.5, 0), i * 0.5]) };
    const [d, e] = along(own[k], x, y);
    return e > -2.5 && e < lg.depth && d > 0 && d < lg.len;
  }

  /** Where the camera stands for gate k, as {position, target} ([x, y, up] ENU metres), fov, the figures it keeps
   * clear of and where across the page's clear part the target goes: at the back of the hall, its eye over the crowd,
   * looking a little along the glass at the gate's screen with the aircraft on the stand beside it, clear of the jet
   * bridges' rotundas and of the halls' walls; with focus "counter" on the counter's lounge, from the glass across the
   * line to the desk. u (0 to 1) drifts it along the hall through a director's shot. */
  function camera(k, { focus = null, u = null } = {}) {
    const lg = lounges[k];
    const { run, at } = runOf.get(k);
    const z = floor + Math.min(EYE, hall - floor - 0.8);
    const shift = u == null ? 0 : DRIFT * (u - 0.5);
    const along = (d) => Math.min(run.len - 1.2, Math.max(1.2, d));
    if (focus === "counter" && lg.desk) {
      const [x, y] = run.pos(along(at + lg.len - 15 + shift), 1.2);
      const [dx, dy] = lg.desk;
      return { position: [x, y, z], target: [dx, dy, floor + 1.4], fov: FOV, keep: [x, y, KEEP], frame: 0.5 };
    }
    if (!cameras.has(k)) cameras.set(k, place(k, z));
    const c = cameras.get(k);
    const [x, y] = run.pos(along(c.d + shift), c.e);
    return { position: [x, y, z], target: [...c.at, floor + 2.2], fov: FOV, keep: [x, y, KEEP], frame: c.frame };
  }

  /** The best place in gate k's hall to look from at its screen, {d along it, e in, at, frame}: from the back of the
   * hall, else from its middle; with the halls' walls across neither that line of sight nor the one to the gate's crowd,
   * scored for the jet bridges' rotundas clear of it, the bearing off the glass and the aircraft on the stand in sight
   * beside the screen, near the podium; `frame` where across the page's clear part the screen goes (the aircraft's
   * side left the wider). */
  function place(k, z) {
    const lg = lounges[k];
    const { run, at } = runOf.get(k);
    const mid = lg.len / 2 + 3.2;
    const T = lg.pos(mid, -4).slice(0, 2);
    const st = stands.get(lg.stand);
    const N = st ? st.xy : lg.pos(lg.door, -30);
    const [cx, cy] = lg.pos(lg.len / 2, 4);
    const posts = bridges.list.map((b) => b.P).filter((P) => Math.hypot(P[0] - T[0], P[1] - T[1]) < 60);
    for (const e of [lg.depth - BACK, lg.depth * 0.6]) {
      let best = null;
      for (let d = Math.max(1.2, at + mid - REACH); d <= Math.min(run.len - 1.2, at + mid + REACH); d += 0.5) {
        const [x, y, a] = run.pos(d, e);
        if (local(k, x, y)[1] < 1.5 || walled([x, y, z], [T[0], T[1], floor + 2.2]) || walled([x, y, z], [cx, cy, floor + 1.2])) continue;
        const toT = Math.atan2(T[0] - x, T[1] - y) / DEG;
        const side = angDiff(toT, Math.atan2(N[0] - x, N[1] - y) / DEG);
        const clear = Math.min(6, ...posts.map((P) => segDist(P, x, y, N[0], N[1])));
        const off = Math.abs(Math.abs(angDiff(a, toT)) - LOOK);
        const score = clear * 2 - off * 0.3 - Math.max(0, Math.abs(side) - 30) * 0.8 - Math.abs(d - at - mid) * 0.05;
        if (!best || score > best.score) best = { score, d, e, at: T, frame: side > 0 ? 0.36 : 0.64 };
      }
      if (best) return best;
    }
    return { d: at + mid, e: lg.depth - BACK, at: T, frame: 0.5 };
  }

  /** Whether the halls' walls stand between a and b ([x, y, up] ENU metres). */
  function walled(a, b) {
    const from = new THREE.Vector3(a[0], a[2], -a[1]);
    const to = new THREE.Vector3(b[0], b[2], -b[1]).sub(from);
    ray.set(from, to.clone().normalize());
    ray.far = to.length();
    return ray.intersectObject(pax.walls, false).length > 0;
  }

  /** The hall about gate k dressed: the glass's mullions and rails, the ceiling's light panels, and over each podium
   * of its own and the next gates a screen on its pole, with each one's gate sign by its door. */
  function dress(k) {
    if (dressed?.k === k) return;
    if (dressed) {
      for (const m of dressed.meshes) {
        group.remove(m);
        m.geometry.dispose();
      }
    }
    const { run, at } = runOf.get(k);
    const lg = lounges[k];
    const mid = at + lg.door;
    const [d0, d1] = [Math.max(0, mid - DRESS), Math.min(run.len, mid + DRESS)];
    const h = hall - floor;
    const frame = [];
    const lights = [];
    for (let d = Math.ceil(d0 / 2.4) * 2.4; d <= d1; d += 2.4) {
      const [x, y, a] = run.pos(d, 0.1);
      frame.push(colored(new THREE.BoxGeometry(0.09, h, 0.16), "#30363d").rotateY(-a * DEG).translate(x, floor + h / 2, -y));
    }
    for (let d = d0; d < d1; d += 1.2) {
      const [ax, ay] = run.pos(d, 0.1);
      const [bx, by] = run.pos(Math.min(d1, d + 1.2), 0.1);
      const L = Math.hypot(bx - ax, by - ay);
      if (L < 0.05) continue;
      const a = Math.atan2(bx - ax, by - ay) / DEG;
      for (const [zz, t] of [[floor + 0.08, 0.16], [floor + 2.6, 0.08], [floor + h * 0.72, 0.08], [hall - 0.12, 0.24]]) {
        frame.push(colored(new THREE.BoxGeometry(0.12, t, L + 0.02), "#30363d").rotateY(-a * DEG).translate((ax + bx) / 2, zz, -(ay + by) / 2));
      }
    }
    for (const e of [2.2, 5.0, 7.8].filter((e) => e < lg.depth - 0.8)) {
      for (let d = Math.ceil(d0 / 3.1) * 3.1 + 1.2; d < d1; d += 3.1) {
        const [x, y, a] = run.pos(d, e);
        lights.push(colored(new THREE.PlaneGeometry(1.3, 0.55).rotateX(Math.PI / 2), "#fffaf0").rotateY(-a * DEG).translate(x, hall - 0.07, -y));
      }
    }
    const near = run.ks.filter((j) => Math.abs(j - k) <= 1).sort((a, b) => Math.abs(a - k) - Math.abs(b - k));
    for (const [i, s] of [...screens.entries(), ...signs.entries()]) s.used = i < near.length;
    near.forEach((j, i) => {
      const o = lounges[j];
      const [px, py, pa] = o.pos(o.len / 2 + 3.2, 2.6);
      const zc = floor + Math.min(3.4, h - 0.5) - 0.6;
      frame.push(colored(new THREE.CylinderGeometry(0.05, 0.05, zc - floor - 1.05, 8), "#5d656d").translate(px, (zc + floor + 1.05) / 2, -py));
      put(screens[i].mesh, px, py, zc, pa, 0.2);
      const [gx, gy, ga] = o.pos(o.door, 1.6);
      put(signs[i].mesh, gx, gy, floor + Math.min(4.2, h - 0.6), ga, 0);
      screens[i].k = signs[i].k = j;
      screens[i].sig = signs[i].sig = null;
    });
    const meshes = [new THREE.Mesh(mergeGeometries(frame), solid), new THREE.Mesh(mergeGeometries(lights), glow)];
    group.add(...meshes);
    dressed = { k, meshes };
  }

  function put(mesh, x, y, z, a, inward) {
    mesh.position.set(x - Math.sin(a * DEG) * inward, z, -(y - Math.cos(a * DEG) * inward));
    mesh.rotation.set(0, -a * DEG, 0);
  }

  function paint(T, flight) {
    for (const s of screens) {
      if (!s.mesh.visible) continue;
      const it = info(s.k, T, flight);
      const sig = `${it.gate}|${it.flight}|${it.status}|${hhmm(T)}`;
      if (sig !== s.sig) {
        s.sig = sig;
        s.draw((g, W, H) => board(g, W, H, it, T));
      }
    }
    for (const s of signs) {
      if (!s.mesh.visible || s.sig === s.k) continue;
      s.sig = s.k;
      s.draw((g, W, H) => gateSign(g, W, H, gateOf(lounges[s.k].stand)));
    }
  }

  /** A moment to open the Terminal view on, in daylight: "busy", the busiest lounge with an aircraft at its gate;
   * "passengers", a held departure's gate as its connecting passengers board (the most of them), else the line at the
   * service counter at its longest. {t, lounge, focus} or null. */
  function moment(kind, { span, sun, parked }) {
    const day = (t) => sun(t) >= LIT;
    let best = null;
    const take = (score, t, lounge, focus = null) => {
      if (!best || score > best.score) best = { score, t: Math.round(t), lounge, focus };
    };
    if (kind === "passengers") {
      lounges.forEach((lg, k) => {
        for (const { leg } of deps[k]) {
          const p = leg.pax;
          if (leg.status !== "flown" || !p?.held || !p.conn) continue;
          const t = Math.max(Math.min(leg.out - p.held, leg.sched) - HELD + 6, leg.out - BOARD + 4);
          if (t >= span[0] && t <= span[1] && day(t) && pax.at(k, t) >= 15) take(1000 + p.conn + p.held / 2, t, k);
        }
      });
      if (!best) {
        for (const [t, n] of pax.queue) if (n >= 8 && t >= span[0] && day(t + 0.4)) take(n, t + 0.4, pax.counter, "counter");
      }
      if (best) return best;
    }
    for (let t = span[0]; t <= span[1]; t += 5) {
      if (!day(t)) continue;
      const k = pax.busiest(t);
      take(pax.at(k, t) + (parked(lounges[k].stand, t) ? 40 : 0), t, k);
    }
    return best;
  }

  return {
    group,
    gates: lounges.map((lg, k) => ({ lounge: k, stand: lg.stand, gate: gateOf(lg.stand) })),
    info,
    within,
    camera,
    moment,
    /** Show the hall about gate k at T seen from (x, y), its signs nearer than SIGN hidden and `flight` on its screen
     * when it leaves from there then, or hide it all (k null). */
    update(T, k, [x, y] = [0, 0], flight = null) {
      group.visible = k != null;
      if (k == null) return;
      dress(k);
      for (const s of [...screens, ...signs]) s.mesh.visible = s.used && Math.hypot(s.mesh.position.x - x, -s.mesh.position.z - y) > SIGN;
      paint(T, flight);
    },
  };
}

/** The minute a departure leaves or would have: its pushback, or its schedule when cancelled. */
function leaves(l) {
  return l.status === "cancelled" ? l.sched : l.out;
}

function board(g, W, H, it, T) {
  g.fillStyle = "#0b1f3a";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "#16345c";
  g.fillRect(0, 0, W, 78);
  g.textBaseline = "middle";
  g.fillStyle = "#ffffff";
  g.font = `600 44px ${FONT}`;
  g.fillText(`Gate ${it.gate}`, 30, 41);
  g.textAlign = "right";
  g.fillStyle = "#b9cbe0";
  g.fillText(hhmm(T), W - 30, 41);
  g.textAlign = "left";
  if (!it.flight) {
    g.font = `500 44px ${FONT}`;
    g.fillText(it.counter ? "Customer service" : "No departures", 30, 210);
    return;
  }
  g.fillStyle = "#ffffff";
  g.font = `700 66px ${FONT}`;
  g.fillText(fit(g, it.city ?? "", W - 60), 30, 152);
  g.fillStyle = "#d6e2ef";
  g.font = `600 42px ${FONT}`;
  g.fillText(it.flight, 30, 232);
  g.textAlign = "right";
  g.fillText(`Departs ${it.time}`, W - 30, 232);
  g.textAlign = "left";
  g.fillStyle = TONES[it.tone];
  g.fillRect(0, H - 128, W, 128);
  g.fillStyle = "#ffffff";
  g.font = `700 52px ${FONT}`;
  g.fillText(fit(g, it.status, W - 60), 30, H - 64);
}

function gateSign(g, W, H, gate) {
  g.fillStyle = "#0b1f3a";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "#ffffff";
  g.textBaseline = "middle";
  g.font = `500 54px ${FONT}`;
  g.fillText("Gate", 34, H / 2 + 4);
  g.textAlign = "right";
  g.font = `700 150px ${FONT}`;
  g.fillText(fit(g, gate, W - 210), W - 34, H / 2 + 8);
}

/** A sign w × h metres facing +z, in a dark frame, its face a canvas of W × H pixels that draw(fn) paints. */
function sign(w, h, W, H) {
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  const mesh = new THREE.Group();
  mesh.add(new THREE.Mesh(colored(new THREE.BoxGeometry(w + 0.08, h + 0.08, 0.08), "#1b2128", 0, 0, -0.05), new THREE.MeshLambertMaterial({ vertexColors: true })));
  mesh.add(new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false })));
  const g = c.getContext("2d");
  return {
    mesh,
    draw(fn) {
      g.save();
      fn(g, W, H);
      g.restore();
      tex.needsUpdate = true;
    },
  };
}

function fit(g, text, w) {
  if (g.measureText(text).width <= w) return text;
  let s = text;
  while (s.length > 1 && g.measureText(`${s}…`).width > w) s = s.slice(0, -1);
  return `${s}…`;
}

function angDiff(a, b) {
  return ((((b - a) % 360) + 540) % 360) - 180;
}

function segDist([px, py], x0, y0, x1, y1) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const u = Math.max(0, Math.min(1, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(x0 + dx * u - px, y0 + dy * u - py);
}
