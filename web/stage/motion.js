const KT = 1852 / 3600;
const D2R = Math.PI / 180;
const STEP = 3;
const GRID = 3;
const SEP = 52;
const HORIZON = 1200;
const TAXI = 15 * KT;
const TAXI_MAX = 25 * KT;
const TAXI_HARD = 29 * KT;
const TAXI_SLOW = 9 * KT;
const LINEUP = 10 * KT;
const LEAD = 5 * KT;
const PUSH = 1.3;
const EXIT = 16 * KT;
const ACC = 0.45;
const DEC = 0.7;
const LAT_ACC = 0.9;
const PAUSE = 40;
const LUAW = 15;
const HOLD_SHORT = 75;
const STRIP = 62;
const PUSH_R = 15;
const TURN_R = 40;
const QUEUE = 150;
const PUSH_DELAY = 420;
const DEP_SEP = 60;
const ARR_SEP = 60;
const X_SEP = 30;
const V_APP = 138 * KT;
const V_TD = 130 * KT;
const V_ROT = 145 * KT;
const V_CLIMB = 175 * KT;
const TD_PAST = 400;
const APP_D = 14000;
const FLARE = 260;
const GLIDE = Math.tan(3 * D2R);
const CLIMB = Math.tan(7 * D2R);
const ROLL = 1800;
const CLIMB_R = 300;
const GONE = 240;
const GEAR_UP = [4, 12];
const GEAR_DOWN = [9500, 8000];
const BEACON = 300;
const BUCKET = 600;
const COST = { taxiway: 1, taxilane: 1.25, lead: 2, runway: 8 };
const ROLL_T = V_ROT / ((V_ROT * V_ROT) / (2 * ROLL));
const ABSENT = Object.freeze({ x: 0, y: 0, heading: 0, alt: 0, pitch: 0, speed: 0, gear: 1, phase: "absent" });

const hdgOf = (dx, dy) => (Math.atan2(dx, dy) / D2R + 360) % 360;
const dirOf = (h) => [Math.sin(h * D2R), Math.cos(h * D2R)];
const angDiff = (a, b) => ((((b - a) % 360) + 540) % 360) - 180;
const lerpAng = (a, b, f) => (a + angDiff(a, b) * f + 360) % 360;
const ease = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
const unitv = (a, b) => {
  const d = dist(a, b) || 1;
  return [(b[0] - a[0]) / d, (b[1] - a[1]) / d];
};

/** Every aircraft's day at the hub from the traffic sidecar: tracks by key, the moments worth filming, who is at which stand when, and which aircraft are about near any minute. */
export function buildTracks(stage, traffic) {
  const G = graphOf(stage);
  const rws = runwaysOf(stage);
  const strips = stage.runways.map((r) => rws.get(r.ends[0].id));
  const configs = (traffic.runways?.length ? traffic.runways : [{ t: -Infinity }]).map((c, i) => {
    const dep = rws.get(c.departures) || strips[0];
    return { i, t: c.t, dep, arr: rws.get(c.arrivals) || dep, depDir: new Set(), spots: [], spotRes: new Map() };
  });
  const configAt = (min) => configs.reduce((c, x) => (x.t <= min ? x : c), configs[0]);
  G.depCost = (e) => (G.standSet.has(e.b) ? Infinity : e.len * COST[e.kind]);
  const memo = new Map();
  const once = (key, f) => (memo.has(key) ? memo.get(key) : memo.set(key, f()).get(key));
  G.once = once;
  G.holds = G.nodes.map((p, i) => i).filter((i) => {
    const a = G.adj[i];
    if (a.length !== 2 || !a.every((e) => e[2] === "taxiway" || e[2] === "taxilane")) return false;
    const p = G.nodes[i];
    const u = unitv(G.nodes[a[0][0]], p);
    const v = unitv(p, G.nodes[a[1][0]]);
    return u[0] * v[0] + u[1] * v[1] > Math.cos(25 * D2R) && !strips.some((rw) => inStrip(rw, p, STRIP + 70)) && !stage.stands.some((st) => dist(st.xy, p) < 90);
  });
  const stands = new Map(stage.stands.map((s) => [s.ref, s]));
  const night = nightOf(traffic, stage.origin);
  const deps = [];
  const arrs = [];
  for (const ac of traffic.aircraft) {
    for (const v of ac.visits) {
      const st = stands.get(v.stand);
      if (!st) continue;
      const len = ac.length || 38;
      const a = v.arr;
      if (a && a.status === "flown" && v.from != null && (a.on ?? a.in) != null) {
        const on = (a.on ?? a.in - 7) * 60;
        arrs.push({ kind: "arr", ac, v, leg: a, st, on, in: Math.max(on + 90, (a.in ?? a.on + 7) * 60), len, cfg: configAt(on / 60) });
      }
      const d = v.dep;
      if (d && d.status === "flown" && v.to != null && d.out != null) {
        const out = d.out * 60;
        deps.push({ kind: "dep", ac, v, leg: d, st, out, off: Math.max(out + 150, (d.off ?? d.out + 15) * 60 - ROLL_T), len, cfg: configAt(d.off ?? d.out + 15) });
      }
    }
  }
  sequence(deps, arrs);
  const byRw = new Map();
  for (const m of deps) (byRw.get(m.cfg.dep.id) || byRw.set(m.cfg.dep.id, []).get(m.cfg.dep.id)).push(m);
  for (const list of byRw.values()) {
    list.sort((a, b) => a.off - b.off);
    list.forEach((m, i) => (m.queue = i ? Math.max(20, Math.min(QUEUE, m.off - list[i - 1].off - 50)) : QUEUE));
  }
  for (const m of deps) {
    const rw = m.cfg.dep;
    const entries = once(`entries:${rw.id}`, () => {
      const out = G.nodes.map((p, i) => i).filter((i) => onRunway(rw, G.nodes[i]) && along(rw, G.nodes[i]) < rw.len - ROLL - 700 && G.adj[i].some((e) => e[2] !== "runway"));
      return out.length ? out.map((i) => [i, Math.max(0, along(rw, G.nodes[i])), rw.heading]) : G.thresholds[rw.id];
    });
    m.B = once(`dep:${rw.id}`, () => sweepBack(G, entries, G.depCost, depFree));
    if (!G.out[G.stand_nodes[m.st.ref]].some((e) => isFinite(m.B.d[e]))) m.B = once(`dep~:${rw.id}`, () => sweepBack(G, entries, G.depCost, depFree, softTurn));
    routeDep(G, m.cfg.dep, m);
    for (let i = 1; i < m.nodes.length; i++) m.cfg.depDir.add(m.nodes[i - 1] * G.n + m.nodes[i]);
  }
  for (const m of arrs) {
    const rw = m.cfg.arr;
    const exits = once(`exits:${rw.id}`, () => G.nodes.map((p, i) => i).filter((i) => onRunway(rw, G.nodes[i]) && G.adj[i].some((e) => e[2] !== "runway")));
    routeArr(G, rw, m, m.cfg.depDir, exits, once);
  }
  const all = [...deps, ...arrs];
  for (const cfg of configs) {
    const near = new Map();
    const cell = (p) => `${Math.floor(p[0] / 100)},${Math.floor(p[1] / 100)}`;
    for (const m of all) {
      if (m.cfg !== cfg) continue;
      for (let i = 0; i < m.nodes.length; i++) {
        const p = G.nodes[m.nodes[i]];
        const [cx, cy] = [Math.floor(p[0] / 100), Math.floor(p[1] / 100)];
        for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
          const k = `${cx + dx},${cy + dy}`;
          if (!near.has(k)) near.set(k, []);
          near.get(k).push(p);
        }
      }
    }
    for (const i of G.holds) {
      const p = G.nodes[i];
      if ((near.get(cell(p)) || []).some((q) => dist(p, q) < 90)) continue;
      if (cfg.spots.some((j) => dist(G.nodes[j], p) < 120)) continue;
      cfg.spots.push(i);
    }
    cfg.spotRes = new Map(cfg.spots.map((i) => [i, []]));
  }
  const occ = new Map(strips.map((r) => [r.ref, []]));
  for (const m of arrs) {
    timeArr(m);
    occ.get(m.cfg.arr.ref).push({ a: m.on - 75, b: m.tExit + 20, key: m.ac.key });
  }
  for (const m of all) m.coarse = coarse(m);
  const plans = timeline((S) => [S.k0 * GRID, S.k1 * GRID]);
  const moves = timeline((m) => [m.coarse.a - 600, m.coarse.b + 900]);
  for (const m of all) moves.add(m);
  const reach = (m) => (m.kind === "dep" ? [m.out - 300, m.off + 1200] : [m.on - 300, m.in + 1500]);
  const occNear = (m) => {
    const [a, b] = reach(m);
    return new Map([...occ].map(([k, list]) => [k, list.filter((iv) => iv.b >= a && iv.a <= b)]));
  };
  deps.sort((a, b) => a.off - b.off);
  arrs.sort((a, b) => a.on - b.on);
  const order = [...arrs, ...deps].sort((a, b) => (a.ac.task ? 0 : 1) - (b.ac.task ? 0 : 1));
  for (const m of order) {
    const committed = plans.near(...reach(m));
    const nearby = moves.near(...reach(m));
    if (m.kind === "arr") chooseGate(G, m, committed, nearby, strips);
    else chooseStage(G, m, committed, nearby, strips);
    m.S = settle(m, committed, occNear(m), strips);
    plans.add(m.S);
    m.done = true;
    if (m.kind === "dep") occ.get(m.cfg.dep.ref).push({ a: m.off - 45, b: m.off + 40, key: m.ac.key });
  }
  for (const m of order) {
    const others = plans.near(...reach(m)).filter((x) => x.m !== m);
    const o = occNear(m);
    const bad = clashes(m, m.S, others, o, strips);
    if (!bad) continue;
    const save = { stops: m.stops.map((x) => ({ ...x })), w0: m.w0, pause: m.pause, S: m.S };
    const S = settle(m, others, o, strips);
    if (clashes(m, S, others, o, strips) < bad) {
      plans.swap(m.S, S);
      m.S = S;
    } else {
      Object.assign(m, save);
      time(m);
    }
  }
  const report = { movements: all.length, waits: 0, late: [], clashes: 0 };
  for (const m of order) {
    report.clashes += firstConflict(m, m.S, plans.near(...reach(m)).filter((x) => x.m !== m), occNear(m), strips, new Set()) ? 1 : 0;
    report.waits += m.stops.length;
    if (m.late > 6) report.late.push({ key: m.ac.key, flight: m.leg.flight, late: m.late });
  }
  const byAc = new Map();
  for (const m of all) (byAc.get(m.ac.key) || byAc.set(m.ac.key, []).get(m.ac.key)).push(m);
  const tracks = new Map();
  const stays = new Map();
  const events = [];
  const [s0, s1] = (traffic.span || [0, 1440]).map((m) => m * 60);
  const present = timeline((iv) => [Math.max(iv.a, s0 - 3600), Math.min(iv.b, s1 + 3600)]);
  for (const ac of traffic.aircraft) {
    const tr = trackOf(ac, byAc.get(ac.key) || [], stands, night);
    tracks.set(ac.key, tr);
    for (const s of tr.stays) (stays.get(s.stand) || stays.set(s.stand, []).get(s.stand)).push(s);
    for (const iv of tr.life) present.add({ ...iv, key: ac.key });
    events.push(...tr.events);
  }
  for (const list of stays.values()) list.sort((a, b) => a.a - b.a);
  events.sort((a, b) => a.t - b.t);
  const rolls = new Map();
  for (const [id, list] of byRw) {
    const xs = list.map((m) => m.roll0[0]).sort((a, b) => a - b);
    const ys = list.map((m) => m.roll0[1]).sort((a, b) => a - b);
    rolls.set(id, [xs[xs.length >> 1], ys[ys.length >> 1]]);
  }
  return {
    tracks,
    events,
    stays,
    rolls,
    report,
    night,
    config: (t) => {
      const c = configAt(t);
      return { departures: c.dep.id, arrivals: c.arr.id };
    },
    near(t, pad = 1) {
      const keys = new Set();
      for (const iv of present.near((t - pad) * 60, (t + pad) * 60)) keys.add(iv.key);
      return keys;
    },
  };
}

export function poseAt(track, t) {
  const ts = t * 60;
  const segs = track.segs;
  let lo = 0;
  let hi = segs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ts >= segs[mid].b) lo = mid + 1;
    else hi = mid;
  }
  const p = { ...segs[lo].pose(ts) };
  p.lights = lightsOf(p.phase, track.night(t), p.beacon);
  delete p.beacon;
  return p;
}

/** Items over seconds, binned so that the ones overlapping an interval come back without a scan of the day. */
function timeline(span) {
  const bins = new Map();
  const where = new Map();
  return {
    add(it) {
      const [a, b] = span(it);
      const ks = [];
      for (let k = Math.floor(a / BUCKET); k <= Math.floor(b / BUCKET); k++) {
        if (!bins.has(k)) bins.set(k, []);
        bins.get(k).push(it);
        ks.push(k);
      }
      where.set(it, ks);
    },
    swap(old, it) {
      for (const k of where.get(old) || []) {
        const list = bins.get(k);
        list.splice(list.indexOf(old), 1);
      }
      where.delete(old);
      this.add(it);
    },
    near(a, b) {
      const out = new Set();
      for (let k = Math.floor(a / BUCKET); k <= Math.floor(b / BUCKET); k++) for (const it of bins.get(k) || []) out.add(it);
      return [...out].filter((it) => {
        const [x, y] = span(it);
        return y >= a && x <= b;
      });
    },
  };
}

function sequence(deps, arrs) {
  const pinned = (m) => !!m.ac.task;
  const spread = (list, key, gap) => {
    let moved = false;
    list.sort((a, b) => a[key] - b[key]);
    for (let i = 1; i < list.length; i++) {
      const a = list[i - 1];
      const b = list[i];
      const d = gap - (b[key] - a[key]);
      if (d <= 0.01) continue;
      if (pinned(a) && !pinned(b)) b[key] += d;
      else if (pinned(b) && !pinned(a)) a[key] -= d;
      else {
        a[key] -= d / 2;
        b[key] += d / 2;
      }
      moved = true;
    }
    return moved;
  };
  const groups = (list, rw) => {
    const out = new Map();
    for (const m of list) (out.get(rw(m).id) || out.set(rw(m).id, []).get(rw(m).id)).push(m);
    return [...out.values()];
  };
  const depGroups = groups(deps, (m) => m.cfg.dep);
  const arrGroups = groups(arrs, (m) => m.cfg.arr);
  const pairs = [];
  const acc = (V_ROT * V_ROT) / (2 * ROLL);
  for (const cfg of new Set([...deps, ...arrs].map((m) => m.cfg))) {
    const X = crossing(cfg.dep, cfg.arr);
    if (!X) continue;
    const xd = along(cfg.dep, X) - 110;
    if (!(xd > 0 && xd < ROLL)) continue;
    pairs.push({ cfg, dT: Math.sqrt((2 * xd) / acc), aT: (along(cfg.arr, X) - TD_PAST) / V_TD });
  }
  for (let it = 0; it < 40; it++) {
    let moved = false;
    for (const g of depGroups) moved = spread(g, "off", DEP_SEP) || moved;
    for (const g of arrGroups) moved = spread(g, "on", ARR_SEP) || moved;
    for (const { cfg, dT, aT } of pairs) {
      const A = arrs.filter((a) => a.cfg === cfg).sort((a, b) => a.on - b.on);
      for (const d of deps) {
        if (d.cfg !== cfg) continue;
        const want = d.off + dT - aT;
        let lo = 0;
        let hi = A.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (A[mid].on < want - X_SEP - 1) lo = mid + 1;
          else hi = mid;
        }
        for (let j = lo; j < A.length && A[j].on <= want + X_SEP + 1; j++) {
          const a = A[j];
          const gap = d.off + dT - (a.on + aT);
          if (Math.abs(gap) >= X_SEP) continue;
          const shift = gap >= 0 ? X_SEP - gap : -(X_SEP + gap);
          if (pinned(d) && !pinned(a)) a.on -= shift;
          else d.off += shift;
          moved = true;
        }
      }
    }
    if (!moved) break;
  }
  for (const m of arrs) m.in = Math.max(m.in, m.on + 90);
  for (const m of deps) m.off = Math.max(m.off, m.out + 150);
}

function crossing(r1, r2) {
  if (r1.ref === r2.ref) return null;
  const den = r1.u[0] * r2.u[1] - r1.u[1] * r2.u[0];
  if (Math.abs(den) < 1e-6) return null;
  const t = ((r2.xy[0] - r1.xy[0]) * r2.u[1] - (r2.xy[1] - r1.xy[1]) * r2.u[0]) / den;
  const p = [r1.xy[0] + r1.u[0] * t, r1.xy[1] + r1.u[1] * t];
  return t > 0 && t < r1.len && along(r2, p) > 0 && along(r2, p) < r2.len ? p : null;
}

function lightsOf(phase, dark, beacon) {
  const air = phase === "climb" || phase === "approach" || phase === "flare";
  const rwy = phase === "lineup" || phase === "roll" || phase === "rollout";
  return {
    nav: phase !== "absent" && (dark || phase !== "parked"),
    beacon: phase !== "absent" && (phase !== "parked" || !!beacon),
    strobe: air || rwy,
    landing: air || rwy,
    taxi: phase === "taxi" || phase === "taxi_in",
  };
}

/** One aircraft's day, visit by visit: parked at each stand, flown in and out where BTS saw it move, and appearing or leaving where the record has a gap. */
function trackOf(ac, moves, stands, night) {
  const segs = [];
  const stays = [];
  const life = [];
  const events = [];
  const paths = [];
  let t = -Infinity;
  let at = null;
  let since = -Infinity;
  let beaconTo = -Infinity;
  const parked = (st, from, to, out) => {
    const pose = { x: st.xy[0], y: st.xy[1], heading: st.heading, alt: 0, pitch: 0, speed: 0, gear: 1, phase: "parked" };
    const lit = [from, to, out];
    return (ts) => ({ ...pose, beacon: ts < lit[1] || ts >= lit[2] - BEACON, since: lit[0] });
  };
  const hold = (b, pose) => {
    if (b <= t) return;
    segs.push({ a: t, b, pose: pose || (() => ABSENT) });
    t = b;
  };
  const stay = (end, out, leave) => {
    if (!at) return;
    hold(end, parked(at, since, beaconTo, out));
    stays.push({ key: ac.key, stand: at.ref, a: since, b: end, leave });
    life.push({ a: since, b: end });
    at = null;
  };
  const ev = (leg, kind, ts) => events.push({ t: ts / 60, key: ac.key, kind, flight: leg.flight });
  const visits = ac.visits.slice().sort((a, b) => (a.from ?? -Infinity) - (b.from ?? -Infinity));
  visits.forEach((v, i) => {
    const st = stands.get(v.stand);
    if (!st) return;
    const arr = moves.find((m) => m.v === v && m.kind === "arr");
    const dep = moves.find((m) => m.v === v && m.kind === "dep");
    if (arr) {
      if (at) stay(arr.tApp, Infinity, false);
      hold(arr.tApp);
      segs.push({ a: arr.tApp, b: arr.on, pose: (ts) => approachPose(arr, arr.rw, ts) });
      segs.push({ a: arr.on, b: arr.tExit, pose: (ts) => rolloutPose(arr, arr.rw, ts) });
      segs.push({ a: arr.tExit, b: arr.tPark, pose: (ts) => groundPose(arr, ts) });
      paths.push({ kind: "taxi_in", pts: thin(arr.taxi) });
      ev(arr.leg, "landing", arr.on);
      ev(arr.leg, "gate_in", arr.tPark);
      life.push({ a: arr.tApp, b: arr.tPark });
      t = arr.tPark;
      at = st;
      since = t;
      beaconTo = Math.max(t, arr.in) + 60;
    } else if (v.from != null && at !== st) {
      if (at) stay(v.from * 60, Infinity, false);
      hold(v.from * 60);
      at = st;
      since = t;
      beaconTo = -Infinity;
    } else if (!at) {
      at = st;
      since = v.from != null ? v.from * 60 : -Infinity;
    }
    if (dep) {
      stay(dep.tPush, dep.out, true);
      segs.push({ a: dep.tPush, b: dep.tTaxi, pose: (ts) => groundPose(dep, ts) });
      segs.push({ a: dep.tTaxi, b: dep.tRoll, pose: (ts) => groundPose(dep, ts) });
      segs.push({ a: dep.tRoll, b: dep.tRoll + GONE, pose: (ts) => climbPose(dep, dep.rw, ts) });
      paths.push({ kind: "push", pts: thin(dep.push) }, { kind: "taxi", pts: thin(dep.taxi) });
      ev(dep.leg, "pushback", dep.tPush);
      ev(dep.leg, "takeoff", dep.tRoll + ROLL_T);
      life.push({ a: dep.tPush, b: dep.tRoll + GONE });
      t = dep.tRoll + GONE;
    } else if (v.to != null) {
      const next = visits[i + 1];
      if (!(next && next.stand === v.stand && next.from != null && Math.abs(next.from - v.to) < 1)) stay(v.to * 60, Infinity, false);
    }
  });
  stay(Infinity, Infinity, false);
  hold(Infinity);
  const merged = [];
  for (const iv of life.sort((a, b) => a.a - b.a)) {
    const last = merged[merged.length - 1];
    if (last && iv.a <= last.b + 1) last.b = Math.max(last.b, iv.b);
    else merged.push({ ...iv });
  }
  return { key: ac.key, tail: ac.tail, carrier: ac.carrier, jetblue: !!ac.jetblue, task: !!ac.task, spec: { class: ac.class, length: ac.length, span: ac.span, jetblue: !!ac.jetblue }, visits: ac.visits, segs, stays, life: merged, events, paths, night };
}

function thin(P) {
  const out = [];
  for (let i = 0; i < P.n; i += 4) out.push([P.x[i], P.y[i]]);
  out.push([P.x[P.n - 1], P.y[P.n - 1]]);
  return out;
}

function groundPose(m, t) {
  if (m.kind === "dep") {
    if (t < m.tPush) return { x: m.st.xy[0], y: m.st.xy[1], heading: m.st.heading, alt: 0, pitch: 0, speed: 0, gear: 1, phase: "parked", beacon: true, sub: -1, s: 0 };
    if (t < m.tTaxi) return { ...slide(m.push, m.pushProf, t - m.tPush, true), phase: "pushback", sub: 0 };
    const q = slide(m.taxi, m.taxiProf, t - m.tTaxi, false);
    const phase = q.stopped ? q.label : q.s >= m.sHold ? "lineup" : "taxi";
    return { ...q, phase, sub: 1 };
  }
  const q = slide(m.taxi, m.taxiProf, t - m.tExit, false);
  return { ...q, phase: q.stopped ? "hold" : "taxi_in", sub: 1 };
}

function slide(P, pr, t, reverse) {
  const q = profAt(P, pr, t);
  const p = at(P, q.s);
  return { x: p.x, y: p.y, heading: reverse ? (p.h + 180) % 360 : p.h, alt: 0, pitch: 0, speed: q.v, gear: 1, stopped: q.stopped, label: q.label, s: q.s };
}

function approachPose(m, rw, ts) {
  const d = Math.max(0, (m.on - ts) * V_APP);
  const along = TD_PAST - d;
  const flare = d < FLARE;
  const alt = flare ? GLIDE * FLARE * hermite(d / FLARE) : GLIDE * d;
  const pitch = flare ? 2.5 + 3 * ease(1 - d / FLARE) : 2.5;
  const gear = 1 - ease((d - GEAR_DOWN[1]) / (GEAR_DOWN[0] - GEAR_DOWN[1]));
  return { x: rw.xy[0] + rw.u[0] * along, y: rw.xy[1] + rw.u[1] * along, heading: rw.heading, alt, pitch, speed: V_APP, gear, phase: flare ? "flare" : "approach" };
}

function hermite(u) {
  return -0.9 * u * u * u + 1.8 * u * u + 0.1 * u;
}

function rolloutPose(m, rw, ts) {
  const T = m.tExit - m.on;
  const tau = Math.min(Math.max(ts - m.on, 0), T);
  const a = (V_TD - m.vx) / T;
  const along = TD_PAST + V_TD * tau - 0.5 * a * tau * tau;
  return { x: rw.xy[0] + rw.u[0] * along, y: rw.xy[1] + rw.u[1] * along, heading: rw.heading, alt: 0, pitch: 5.5 * (1 - ease(tau / 4)), speed: V_TD - a * tau, gear: 1, phase: "rollout" };
}

function climbPose(m, rw, ts) {
  const acc = (V_ROT * V_ROT) / (2 * ROLL);
  const tRoll = V_ROT / acc;
  const tau = Math.max(0, ts - m.tRoll);
  const [ox, oy] = m.roll0;
  if (tau < tRoll) {
    const s = 0.5 * acc * tau * tau;
    return { x: ox + rw.u[0] * s, y: oy + rw.u[1] * s, heading: rw.heading, alt: 0, pitch: 8 * ease((s - (ROLL - 260)) / 260), speed: acc * tau, gear: 1, phase: "roll" };
  }
  const t2 = tau - tRoll;
  const ka = 0.6;
  const tc = (V_CLIMB - V_ROT) / ka;
  const x = t2 < tc ? V_ROT * t2 + 0.5 * ka * t2 * t2 : V_ROT * tc + 0.5 * ka * tc * tc + V_CLIMB * (t2 - tc);
  const alt = x < CLIMB_R ? (CLIMB * x * x) / (2 * CLIMB_R) : CLIMB * (x - CLIMB_R / 2);
  const s = ROLL + x;
  const gear = 1 - ease((t2 - GEAR_UP[0]) / (GEAR_UP[1] - GEAR_UP[0]));
  return { x: ox + rw.u[0] * s, y: oy + rw.u[1] * s, heading: rw.heading, alt, pitch: 8 + 5 * ease(t2 / 6), speed: Math.min(V_CLIMB, V_ROT + ka * t2), gear, phase: "climb" };
}

function graphOf(stage) {
  const g = stage.taxi_graph;
  const adj = g.nodes.map(() => []);
  for (const [a, b, len, kind] of g.edges) {
    adj[a].push([b, len, kind]);
    adj[b].push([a, len, kind]);
  }
  const dir = [];
  const out = g.nodes.map(() => []);
  const inn = g.nodes.map(() => []);
  for (const [a, b, len, kind] of g.edges) {
    for (const [x, y] of [[a, b], [b, a]]) {
      out[x].push(dir.length);
      inn[y].push(dir.length);
      dir.push({ a: x, b: y, len, kind, h: hdgOf(g.nodes[y][0] - g.nodes[x][0], g.nodes[y][1] - g.nodes[x][1]) });
    }
  }
  return { ...g, adj, dir, out, inn, n: g.nodes.length, standSet: new Set(Object.values(g.stand_nodes)) };
}

function runwaysOf(stage) {
  const ends = new Map();
  for (const r of stage.runways) {
    const [a, b] = r.ends;
    const len = dist(a.xy, b.xy);
    for (const [e, o] of [[a, b], [b, a]]) {
      const u = unitv(e.xy, o.xy);
      const pad = STRIP + 60;
      const box = [Math.min(a.xy[0], b.xy[0]) - pad, Math.min(a.xy[1], b.xy[1]) - pad, Math.max(a.xy[0], b.xy[0]) + pad, Math.max(a.xy[1], b.xy[1]) + pad];
      ends.set(e.id, { id: e.id, ref: r.ref, xy: e.xy, u, heading: hdgOf(u[0], u[1]), len, width: r.width, box });
    }
  }
  return ends;
}

const along = (rw, p) => (p[0] - rw.xy[0]) * rw.u[0] + (p[1] - rw.xy[1]) * rw.u[1];
const lateral = (rw, p) => Math.abs((p[0] - rw.xy[0]) * rw.u[1] - (p[1] - rw.xy[1]) * rw.u[0]);
const inStrip = (rw, p, w) => {
  const a = along(rw, p);
  return a > -60 && a < rw.len + 60 && lateral(rw, p) < w;
};
const onRunway = (rw, p) => {
  const a = along(rw, p);
  return a > -5 && a < rw.len + 5 && lateral(rw, p) < 4;
};

function sweep(G, src, cost, free, h0) {
  const E = G.dir;
  const d = new Float64Array(E.length).fill(Infinity);
  const prev = new Int32Array(E.length).fill(-1);
  const h = [];
  for (const e of G.out[src]) {
    const c = cost(E[e]) + (h0 == null ? 0 : turnCost(h0, E[e].h));
    if (c < d[e]) {
      d[e] = c;
      hpush(h, [c, e]);
    }
  }
  while (h.length) {
    const [dd, e] = hpop(h);
    if (dd > d[e]) continue;
    for (const f of G.out[E[e].b]) {
      if (E[f].b === E[e].a) continue;
      const c = dd + cost(E[f]) + (free(E[e], E[f]) ? 0 : turnCost(E[e].h, E[f].h));
      if (c < d[f]) {
        d[f] = c;
        prev[f] = e;
        hpush(h, [c, f]);
      }
    }
  }
  return { d, prev };
}

/** Cheapest way on to dst, a node or [node, base cost, heading to turn to] entries, from every directed edge. */
function sweepBack(G, dst, cost, free, turn = turnCost) {
  const E = G.dir;
  const d = new Float64Array(E.length).fill(Infinity);
  const next = new Int32Array(E.length).fill(-1);
  const h = [];
  for (const [node, base, hd] of Array.isArray(dst) ? dst : [[dst, 0, null]]) {
    for (const f of G.inn[node]) {
      if (hd != null && E[f].kind === "runway") continue;
      const c = base + cost(E[f]) + (hd == null ? 0 : turn(E[f].h, hd));
      if (c < d[f]) {
        d[f] = c;
        hpush(h, [c, f]);
      }
    }
  }
  while (h.length) {
    const [dd, f] = hpop(h);
    if (dd > d[f]) continue;
    for (const e of G.inn[E[f].a]) {
      if (E[e].a === E[f].b) continue;
      const c = dd + cost(E[e]) + (free(E[e], E[f]) ? 0 : turn(E[e].h, E[f].h));
      if (c < d[e]) {
        d[e] = c;
        next[e] = f;
        hpush(h, [c, e]);
      }
    }
  }
  return { d, next };
}

function follow(G, B, f) {
  const out = [G.dir[f].a];
  for (; f >= 0; f = B.next[f]) out.push(G.dir[f].b);
  return out;
}

function holdRoute(G, m, W, t0, committed) {
  const dep = m.kind === "dep";
  const src = m.nodes[0];
  const cost = dep ? G.depCost : m.cost;
  const free = dep ? depFree : arrFree;
  const F = G.once(dep ? `F:${src}` : `F:${m.cfg.i}:${m.st.ref}:${src}`, () => sweep(G, src, cost, free, dep ? null : m.rw.heading));
  const B = dep ? m.B : m.back;
  const E = G.dir;
  const cands = [];
  const { spots, spotRes } = m.cfg;
  for (const H of spots) {
    let best = null;
    for (const e of G.inn[H]) {
      if (!isFinite(F.d[e])) continue;
      for (const f of G.out[H]) {
        if (E[f].b === E[e].a || !isFinite(B.d[f])) continue;
        const tot = F.d[e] + (free(E[e], E[f]) ? 0 : turnCost(E[e].h, E[f].h)) + B.d[f];
        if (!best || tot < best.tot) best = { tot, e, f };
      }
    }
    if (!best) continue;
    const detour = best.tot - m.base;
    if (detour / TAXI > W - 90) continue;
    cands.push({ H, ...best, detour, tR: t0 + F.d[best.e] / (TAXI * 0.8) });
  }
  cands.sort((a, b) => a.detour - b.detour);
  const nodesOf = (c) => {
    const out = [];
    for (let e = c.e; e >= 0; e = F.prev[e]) out.push(E[e].b);
    out.push(src);
    return [...out.reverse(), ...follow(G, B, c.f).slice(1)];
  };
  const busy = (H, a, b) => spotRes.get(H).some(([x, y]) => x < b && y > a);
  const crowded = (H, a, b) => {
    const [x, y] = G.nodes[H];
    return committed.some((o) => {
      for (let k = Math.max(o.k0, Math.ceil(a / GRID)); k <= Math.min(o.k1, Math.floor(b / GRID)); k += 2) if (Math.abs(o.cx[k - o.k0] - x) < 100 && Math.abs(o.cy[k - o.k0] - y) < 100) return true;
      return false;
    });
  };
  for (const c of cands) {
    if (busy(c.H, c.tR - 90, c.tR + W + 90) || crowded(c.H, c.tR - 60, c.tR + W + 60)) continue;
    const nodes = nodesOf(c);
    const pass = [];
    for (let i = 0, d = 0, after = false; i < nodes.length; i++) {
      if (i) d += dist(G.nodes[nodes[i - 1]], G.nodes[nodes[i]]);
      after ||= nodes[i] === c.H;
      for (const H of spots) if (H !== c.H && dist(G.nodes[nodes[i]], G.nodes[H]) < 100) pass.push([H, t0 + d / (TAXI * 0.8) + (after ? W : 0)]);
    }
    if (pass.some(([H, t]) => busy(H, t - 90, t + 90))) continue;
    spotRes.get(c.H).push([c.tR - 60, c.tR + W + 60]);
    for (const [H, t] of pass) spotRes.get(H).push([t - 90, t + 90]);
    return { nodes, H: c.H };
  }
  return null;
}

function nearestS(P, p) {
  let best = 0;
  let bd = Infinity;
  for (let i = 0; i < P.n; i++) {
    const d = Math.hypot(P.x[i] - p[0], P.y[i] - p[1]);
    if (d < bd) {
      bd = d;
      best = i;
    }
  }
  return P.cum[best];
}

function turnCost(h1, h2) {
  const t = Math.abs(angDiff(h1, h2));
  return t <= 45 ? 0 : t <= 90 ? 30 : t <= 120 ? 300 : t <= 150 ? 3000 : Infinity;
}

const softTurn = (h1, h2) => Math.min(turnCost(h1, h2), 20000);

function hpush(h, it) {
  h.push(it);
  let i = h.length - 1;
  while (i > 0) {
    const p = (i - 1) >> 1;
    if (h[p][0] <= h[i][0]) break;
    [h[p], h[i]] = [h[i], h[p]];
    i = p;
  }
}

function hpop(h) {
  const top = h[0];
  const last = h.pop();
  if (h.length) {
    h[0] = last;
    let i = 0;
    for (;;) {
      const l = 2 * i + 1;
      const r = l + 1;
      let m = i;
      if (l < h.length && h[l][0] < h[m][0]) m = l;
      if (r < h.length && h[r][0] < h[m][0]) m = r;
      if (m === i) break;
      [h[m], h[i]] = [h[i], h[m]];
      i = m;
    }
  }
  return top;
}

const kindOf = (G, a, b) => (G.adj[a].find((e) => e[0] === b) || [0, 0, "taxiway"])[2];

const depFree = (e) => e.kind === "lead";
const arrFree = (e, f) => e.kind === "runway" && f.kind === "runway";

function routeDep(G, rw, m) {
  const S = G.stand_nodes[m.st.ref];
  const B = m.B;
  const f = G.out[S].reduce((a, e) => (B.d[e] < B.d[a] ? e : a), G.out[S][0]);
  m.rw = rw;
  m.base = B.d[f];
  depGeometry(G, rw, m, isFinite(B.d[f]) ? follow(G, B, f) : [S, G.thresholds[rw.id]]);
  m.w0 = m.wBase = 0;
  m.stops = [];
  m.pause = PAUSE;
  m.sStage = null;
}

function depGeometry(G, rw, m, nodes) {
  m.nodes = nodes;
  const xy = (i) => G.nodes[nodes[i]];
  const kinds = nodes.slice(1).map((b, i) => kindOf(G, nodes[i], b));
  let e = nodes.length - 1;
  while (e > 1 && kinds[e - 1] === "runway" && onRunway(rw, xy(e - 1))) e--;
  const L = [rw.xy[0] + rw.u[0] * (Math.max(along(rw, xy(e)), 0) + 60), rw.xy[1] + rw.u[1] * (Math.max(along(rw, xy(e)), 0) + 60)];
  let j = 0;
  while (j < kinds.length && kinds[j] === "lead") j++;
  j = Math.max(1, Math.min(j, e - 1));
  let ahead = xy(j);
  for (let i = j, acc = 0; i < e && acc < 40; i++) {
    acc += dist(xy(i), xy(i + 1));
    ahead = xy(i + 1);
  }
  const pb = pushback(m.st.xy, m.st.heading, xy(j), unitv(xy(j), ahead), m.len);
  const F = pb.pts[pb.pts.length - 1];
  const nd = dirOf(pb.nose);
  let k = j;
  while (k < e && (xy(k)[0] - F[0]) * nd[0] + (xy(k)[1] - F[1]) * nd[1] < 10 && dist(xy(k), xy(j)) < 120) k++;
  m.push = pathOf(pb.pts, 1);
  m.taxi = pathOf(rounded([F, [F[0] + nd[0] * 12, F[1] + nd[1] * 12], ...nodes.slice(k, e + 1).map((n) => G.nodes[n]), L], TURN_R), 2);
  let ih = m.taxi.n - 1;
  while (ih > 0 && inStrip(rw, [m.taxi.x[ih], m.taxi.y[ih]], HOLD_SHORT)) ih--;
  m.sHold = m.taxi.cum[ih];
  m.roll0 = L;
}

/** Back about a length off the stand, then a turn onto the line out: the tug lets go with the nose along d. */
function pushback(A, hs, J, d, len) {
  const hq = (hs + 180) % 360;
  const q = dirOf(hq);
  const phi = angDiff(hq, hdgOf(d[0], d[1]) + 180);
  const cap = 1.2 * len + 8;
  const R = Math.max(PUSH_R, 0.4 * len);
  if (Math.abs(phi) < 15) {
    const s = Math.min(cap, Math.max(5, (J[0] - A[0]) * q[0] + (J[1] - A[1]) * q[1]));
    return { pts: [A, [A[0] + q[0] * s, A[1] + q[1] * s]], nose: hs };
  }
  const ph = Math.sign(phi) * Math.min(Math.abs(phi), 110);
  const tan = Math.tan((Math.abs(ph) / 2) * D2R);
  const den = q[0] * d[1] - q[1] * d[0];
  let straight = Math.min(cap, len * 0.8);
  let r = R;
  if (Math.abs(phi) <= 110 && Math.abs(den) > 1e-6) {
    const tX = ((J[0] - A[0]) * d[1] - (J[1] - A[1]) * d[0]) / den;
    r = Math.min(R, Math.max(6, (tX - 5) / tan));
    straight = Math.min(cap, Math.max(5, tX - r * tan));
  }
  const B = [A[0] + q[0] * straight, A[1] + q[1] * straight];
  return { pts: [A, B, ...arc(B, hq, ph, r)], nose: hs + ph };
}

function arc(B, hq, ph, r) {
  const side = ph > 0 ? 90 : -90;
  const o = dirOf(hq + side);
  const c = [B[0] + r * o[0], B[1] + r * o[1]];
  const n = Math.max(2, Math.ceil((Math.abs(ph) * D2R * r) / 2));
  const out = [];
  for (let i = 1; i <= n; i++) {
    const v = dirOf(hq + (ph * i) / n - side);
    out.push([c[0] + r * v[0], c[1] + r * v[1]]);
  }
  return out;
}

function routeArr(G, rw, m, depDir, exits, once) {
  const S = G.stand_nodes[m.st.ref];
  const ban = (e) => e.kind === "runway" && onRunway(rw, G.nodes[e.a]) && onRunway(rw, G.nodes[e.b]);
  const cost = (e) => (ban(e) || (G.standSet.has(e.b) && e.b !== S) ? Infinity : e.len * COST[e.kind] * (depDir.has(e.b * G.n + e.a) ? 2.5 : 1));
  let best = null;
  for (const soft of [false, true]) {
    const memo = once(`arr${soft ? "~" : ""}:${m.cfg.i}:${rw.id}:${S}`, () => ({ cost, back: sweepBack(G, S, cost, arrFree, soft ? softTurn : turnCost) }));
    m.cost = memo.cost;
    m.back = memo.back;
    for (const lo of [1300, 900, 700]) {
      for (const i of exits) {
        const a = along(rw, G.nodes[i]);
        if (a < lo || a > Math.min(2300, rw.len - 150)) continue;
        for (const f of G.out[i]) {
          const c = turnCost(rw.heading, G.dir[f].h) + m.back.d[f];
          if (isFinite(c) && (!best || c + 0.4 * Math.abs(a - 1700) < best.score)) best = { f, c, score: c + 0.4 * Math.abs(a - 1700) };
        }
      }
      if (best) break;
    }
    if (best) break;
  }
  m.rw = rw;
  m.base = best ? best.c : 0;
  arrGeometry(G, rw, m, best ? follow(G, m.back, best.f) : [G.thresholds[rw.id], S]);
  m.stops = [];
  m.sGate = null;
  m.tApp = m.on - (APP_D + TD_PAST) / V_APP;
  m.vx = EXIT;
  m.tExit = m.on + (2 * (m.xAlong - TD_PAST)) / (V_TD + EXIT);
}

function arrGeometry(G, rw, m, nodes) {
  m.nodes = nodes;
  const aX = along(rw, G.nodes[nodes[0]]);
  m.xAlong = Math.max(aX - 30, TD_PAST + 250);
  const Xp = [rw.xy[0] + rw.u[0] * m.xAlong, rw.xy[1] + rw.u[1] * m.xAlong];
  m.taxi = pathOf(rounded([Xp, ...nodes.map((n) => G.nodes[n])], TURN_R), 2);
  let lead = 0;
  for (let i = nodes.length - 1; i > 0 && kindOf(G, nodes[i - 1], nodes[i]) === "lead"; i--) lead += dist(G.nodes[nodes[i - 1]], G.nodes[nodes[i]]);
  m.leadLen = Math.max(lead, 25);
  let ic = 0;
  while (ic < m.taxi.n - 1 && inStrip(rw, [m.taxi.x[ic], m.taxi.y[ic]], HOLD_SHORT + 10)) ic++;
  m.sClear = m.taxi.cum[ic];
}

function rounded(raw, R) {
  const v = simplify(raw.filter((p, i) => i === 0 || dist(p, raw[i - 1]) > 0.2), 0.8);
  const n = v.length;
  if (n < 3) return v;
  const need = new Float64Array(n);
  const turn = new Float64Array(n);
  for (let i = 1; i < n - 1; i++) {
    const a = unitv(v[i - 1], v[i]);
    const b = unitv(v[i], v[i + 1]);
    turn[i] = angDiff(hdgOf(a[0], a[1]), hdgOf(b[0], b[1]));
    need[i] = Math.abs(turn[i]) < 2 || Math.abs(turn[i]) > 175 ? 0 : R * Math.tan((Math.abs(turn[i]) / 2) * D2R);
  }
  const t = new Float64Array(n).fill(Infinity);
  for (let i = 0; i < n - 1; i++) {
    const L = dist(v[i], v[i + 1]);
    const sum = need[i] + need[i + 1];
    const k = sum > L ? L / sum : 1;
    t[i] = Math.min(t[i], need[i] * k);
    t[i + 1] = Math.min(t[i + 1], need[i + 1] * k);
  }
  const out = [v[0]];
  for (let i = 1; i < n - 1; i++) {
    if (!(t[i] > 0.05)) {
      out.push(v[i]);
      continue;
    }
    const a = unitv(v[i - 1], v[i]);
    const P1 = [v[i][0] - a[0] * t[i], v[i][1] - a[1] * t[i]];
    out.push(P1, ...arc(P1, hdgOf(a[0], a[1]), turn[i], t[i] / Math.tan((Math.abs(turn[i]) / 2) * D2R)));
  }
  out.push(v[n - 1]);
  return out;
}

function simplify(p, tol) {
  if (p.length < 3) return p;
  const keep = new Uint8Array(p.length);
  keep[0] = keep[p.length - 1] = 1;
  const stack = [[0, p.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop();
    const u = unitv(p[a], p[b]);
    const L = dist(p[a], p[b]);
    let best = -1;
    let bd = tol;
    for (let i = a + 1; i < b; i++) {
      const dx = p[i][0] - p[a][0];
      const dy = p[i][1] - p[a][1];
      const along = dx * u[0] + dy * u[1];
      const d = along < 0 ? Math.hypot(dx, dy) : along > L ? dist(p[i], p[b]) : Math.abs(dx * u[1] - dy * u[0]);
      if (d > bd) {
        bd = d;
        best = i;
      }
    }
    if (best > 0) {
      keep[best] = 1;
      stack.push([a, best], [best, b]);
    }
  }
  return p.filter((_, i) => keep[i]);
}

function pathOf(raw, win) {
  let p = resample(raw.length > 1 ? raw : [raw[0], [raw[0][0] + 0.1, raw[0][1]]]);
  const q = p.slice();
  for (let i = 1; i < p.length - 1; i++) {
    const w = Math.min(win, i, p.length - 1 - i);
    let sx = 0;
    let sy = 0;
    for (let j = i - w; j <= i + w; j++) {
      sx += p[j][0];
      sy += p[j][1];
    }
    q[i] = [sx / (2 * w + 1), sy / (2 * w + 1)];
  }
  p = q;
  p = p.filter((v, i) => i === 0 || i === p.length - 1 || dist(v, p[i - 1]) > 0.05);
  const n = p.length;
  const x = new Float64Array(n);
  const y = new Float64Array(n);
  const cum = new Float64Array(n);
  const hdg = new Float64Array(n);
  const cap = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    x[i] = p[i][0];
    y[i] = p[i][1];
    if (i) cum[i] = cum[i - 1] + dist(p[i - 1], p[i]);
  }
  for (let i = 0; i < n; i++) {
    const a = p[Math.max(0, i - 1)];
    const b = p[Math.min(n - 1, i + 1)];
    hdg[i] = hdgOf(b[0] - a[0], b[1] - a[1]);
  }
  const raw2 = new Float64Array(n).fill(Infinity);
  for (let i = 1; i < n - 1; i++) {
    const k = (Math.abs(angDiff(hdg[i - 1], hdg[i + 1])) * D2R) / Math.max(cum[i + 1] - cum[i - 1], 1e-3);
    if (k > 1e-4) raw2[i] = Math.sqrt(LAT_ACC / k);
  }
  for (let i = 0; i < n; i++) {
    let c = Infinity;
    for (let j = Math.max(0, i - 3); j <= Math.min(n - 1, i + 3); j++) c = Math.min(c, raw2[j]);
    cap[i] = c;
  }
  return { x, y, cum, hdg, cap, n, len: cum[n - 1] };
}

function resample(raw) {
  const out = [raw[0]];
  let carry = 0;
  for (let i = 1; i < raw.length; i++) {
    const a = raw[i - 1];
    const b = raw[i];
    const L = dist(a, b);
    if (L < 1e-6) continue;
    let d = STEP - carry;
    while (d < L) {
      out.push([a[0] + ((b[0] - a[0]) * d) / L, a[1] + ((b[1] - a[1]) * d) / L]);
      d += STEP;
    }
    carry = L - (d - STEP);
  }
  const last = raw[raw.length - 1];
  if (dist(out[out.length - 1], last) > 0.3 || out.length === 1) out.push(last);
  else out[out.length - 1] = last;
  return out;
}

function idxAt(P, s) {
  let lo = 0;
  let hi = P.n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (P.cum[mid] <= s) lo = mid;
    else hi = mid;
  }
  return lo;
}

function at(P, s) {
  if (s <= 0) return { x: P.x[0], y: P.y[0], h: P.hdg[0] };
  if (s >= P.len) return { x: P.x[P.n - 1], y: P.y[P.n - 1], h: P.hdg[P.n - 1] };
  const i = idxAt(P, s);
  const f = (s - P.cum[i]) / (P.cum[i + 1] - P.cum[i] || 1);
  return { x: P.x[i] + (P.x[i + 1] - P.x[i]) * f, y: P.y[i] + (P.y[i + 1] - P.y[i]) * f, h: lerpAng(P.hdg[i], P.hdg[i + 1], f) };
}

function nearIdx(P, s) {
  const i = idxAt(P, s);
  return i < P.n - 1 && P.cum[i + 1] - s < s - P.cum[i] ? i + 1 : i;
}

function profileOf(P, { t0 = 0, v0 = 0, fixV0 = false, vEnd = 0, vc, capFrom = Infinity, capV = Infinity, stops = [], acc = ACC, dec = DEC }) {
  const n = P.n;
  const v = new Float64Array(n);
  const w = new Float64Array(n);
  const un = new Float64Array(n).fill(-Infinity);
  const stop = new Uint8Array(n);
  const label = new Array(n);
  for (const s of stops) {
    const i = nearIdx(P, s.s);
    stop[i] = 1;
    w[i] += s.w || 0;
    if (s.until != null) un[i] = Math.max(un[i], s.until - t0);
    label[i] = label[i] === "lineup" ? "lineup" : s.label || "hold";
  }
  const cap = (i) => Math.min(vc, P.cap[i], P.cum[i] >= capFrom ? capV : Infinity);
  v[0] = stop[0] ? 0 : v0;
  for (let i = 1; i < n; i++) v[i] = stop[i] ? 0 : Math.min(cap(i), Math.sqrt(v[i - 1] * v[i - 1] + 2 * acc * (P.cum[i] - P.cum[i - 1])));
  v[n - 1] = Math.min(v[n - 1], vEnd);
  for (let i = n - 2; i >= 0; i--) if (!stop[i] && !(i === 0 && fixV0)) v[i] = Math.min(v[i], Math.sqrt(v[i + 1] * v[i + 1] + 2 * dec * (P.cum[i + 1] - P.cum[i])));
  const tA = new Float64Array(n);
  const tD = new Float64Array(n);
  const extra = new Float64Array(n);
  tD[0] = Math.max(w[0], un[0]);
  extra[0] = tD[0] - w[0];
  for (let i = 1; i < n; i++) {
    tA[i] = tD[i - 1] + (2 * (P.cum[i] - P.cum[i - 1])) / Math.max(v[i - 1] + v[i], 0.2);
    tD[i] = Math.max(tA[i] + w[i], un[i]);
    extra[i] = tD[i] - tA[i] - w[i];
  }
  for (let i = 0; i < n; i++) w[i] = tD[i] - tA[i];
  return { v, tA, tD, w, extra, label, dur: tD[n - 1] };
}

function slackAfter(P, pr, s) {
  let u = 0;
  for (let i = nearIdx(P, s) + 1; i < P.n; i++) u += pr.extra[i];
  return u;
}

function fastest(prof, vmin, vmax, fits) {
  let p = prof(vmax);
  if (!fits(p)) return { p, vc: vmax, ok: false };
  let a = vmin;
  let b = vmax;
  let vc = vmax;
  for (let it = 0; it < 7; it++) {
    const c = (a + b) / 2;
    const q = prof(c);
    if (fits(q)) {
      b = c;
      p = q;
      vc = c;
    } else a = c;
  }
  return { p, vc, ok: true };
}

function profAt(P, pr, t) {
  const { tA, tD, v, w } = pr;
  const n = P.n;
  if (t <= 0) return { s: 0, v: w[0] > 0 && t < tD[0] ? 0 : v[0], stopped: w[0] > 0, label: pr.label[0] };
  if (t >= tA[n - 1]) return { s: P.len, v: 0, stopped: w[n - 1] > 0 && t < tD[n - 1], label: pr.label[n - 1] };
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (tA[mid] <= t) lo = mid;
    else hi = mid;
  }
  const i = lo;
  if (t < tD[i]) return { s: P.cum[i], v: 0, stopped: true, label: pr.label[i] };
  const dur = tA[i + 1] - tD[i];
  const tau = t - tD[i];
  if (v[i] + v[i + 1] < 0.3) return { s: P.cum[i] + ((P.cum[i + 1] - P.cum[i]) * tau) / dur, v: (P.cum[i + 1] - P.cum[i]) / dur, stopped: false };
  const a = (v[i + 1] - v[i]) / dur;
  const s = Math.min(P.cum[i] + v[i] * tau + 0.5 * a * tau * tau, P.cum[i + 1]);
  return { s, v: Math.max(0, v[i] + a * tau), stopped: false };
}

function tReach(m, s) {
  const pr = m.taxiProf;
  return (m.kind === "dep" ? m.tTaxi : m.tExit) + pr.tA[nearIdx(m.taxi, s)];
}

function timeDep(m) {
  const cfgs = [
    [m.pause, LUAW, TAXI_MAX],
    [Math.min(m.pause, 10), 5, TAXI_MAX],
    [Math.min(m.pause, 10), 5, TAXI_HARD],
  ];
  for (const [k, [pause, luaw, vmax]] of cfgs.entries()) {
    m.tPush = m.out + m.w0;
    m.pushProf = profileOf(m.push, { vc: PUSH, acc: 0.25, dec: 0.35, stops: [{ s: m.push.len, w: pause, label: "pushback" }] });
    m.tTaxi = m.tPush + m.pushProf.dur;
    const prof = (vc, ws, hw) =>
      profileOf(m.taxi, { t0: m.tTaxi, vc, capFrom: m.sHold + 1, capV: LINEUP, stops: [...m.stops, ...(m.sStage != null ? [{ s: m.sStage, w: ws }] : []), { s: m.sHold, w: hw, label: "hold" }, { s: m.taxi.len, w: luaw, label: "lineup" }] });
    const fits = (q) => m.tTaxi + q.dur <= m.off;
    let vc = TAXI;
    let p = prof(vc, 0, 0);
    if (!fits(p)) {
      const f = fastest((c) => prof(c, 0, 0), TAXI, vmax, fits);
      if (!f.ok && k < cfgs.length - 1) continue;
      p = f.p;
      vc = f.vc;
    }
    let ws = 0;
    m.slack = m.off - m.tTaxi - p.dur;
    if (m.slack > 0 && m.sStage != null) ws = slackAfter(m.taxi, p, m.sStage) + Math.max(0, m.slack - m.queue);
    else if (m.slack > m.queue) vc = fastest((c) => prof(c, 0, m.queue), TAXI_SLOW, vc, fits).vc;
    p = prof(vc, ws, 0);
    const hw = Math.max(0, m.off - m.tTaxi - p.dur);
    m.taxiProf = prof(vc, ws, hw);
    m.late = Math.max(0, m.tTaxi + m.taxiProf.dur - m.off);
    m.tRoll = m.tTaxi + m.taxiProf.dur;
    return;
  }
}

function timeArr(m) {
  const prof = (vc, gw) => profileOf(m.taxi, { t0: m.tExit, v0: EXIT, fixV0: true, vc, capFrom: m.taxi.len - m.leadLen, capV: LEAD, stops: m.sGate != null ? [...m.stops, { s: m.sGate, w: gw, label: "hold" }] : m.stops });
  const fits = (q) => m.tExit + q.dur <= m.in;
  let p = prof(TAXI, 0);
  if (!fits(p)) p = fastest((c) => prof(c, 0), TAXI, TAXI_HARD, fits).p;
  else if (m.sGate != null) p = prof(TAXI, slackAfter(m.taxi, p, m.sGate) + m.in - m.tExit - p.dur);
  else p = fastest((c) => prof(c, 0), TAXI_SLOW, TAXI, fits).p;
  m.taxiProf = p;
  m.tPark = m.tExit + p.dur;
  m.late = Math.max(0, m.tPark - m.in);
}

function time(m) {
  if (m.kind === "dep") timeDep(m);
  else timeArr(m);
}

function centreOf(x, y, h, len) {
  const [ux, uy] = dirOf(h);
  return [x - ux * len * 0.45, y - uy * len * 0.45];
}

function sampleMove(m) {
  const dep = m.kind === "dep";
  const t0 = dep ? m.tPush : m.tExit;
  const t1 = dep ? m.tRoll : m.tPark;
  const k0 = Math.ceil(t0 / GRID);
  const k1 = Math.max(k0, Math.floor(t1 / GRID));
  const n = k1 - k0 + 1;
  const S = { key: m.ac.key, m, k0, k1, len: m.len, cx: new Float32Array(n), cy: new Float32Array(n), nx: new Float32Array(n), ny: new Float32Array(n), h: new Float32Array(n), s: new Float32Array(n), sub: new Int8Array(n) };
  let k = 0;
  if (dep) k = walk(S, k, Math.ceil(m.tTaxi / GRID) - 1 - k0, m.push, m.pushProf, m.tPush, true, 0, m.len);
  walk(S, k, n - 1, m.taxi, m.taxiProf, dep ? m.tTaxi : m.tExit, false, 1, m.len);
  S.box = [Infinity, Infinity, -Infinity, -Infinity];
  S.hash = new Map();
  for (let i = 0; i < n; i++) {
    S.box[0] = Math.min(S.box[0], S.cx[i]);
    S.box[1] = Math.min(S.box[1], S.cy[i]);
    S.box[2] = Math.max(S.box[2], S.cx[i]);
    S.box[3] = Math.max(S.box[3], S.cy[i]);
    const c = cellOf(S.cx[i], S.cy[i]);
    if (!S.hash.has(c)) S.hash.set(c, []);
    S.hash.get(c).push(i);
  }
  return S;
}

const CELL = 100;
const cellOf = (x, y) => (Math.floor(x / CELL) + 512) * 1024 + Math.floor(y / CELL) + 512;
const apart = (b, x0, y0, x1, y1) => b[0] > x1 || b[2] < x0 || b[1] > y1 || b[3] < y0;

function walk(S, k, kEnd, P, pr, tb, rev, sub, len) {
  const { tA, tD, v } = pr;
  const n = P.n;
  let i = 0;
  let j = 0;
  for (; k <= kEnd; k++) {
    const t = (S.k0 + k) * GRID - tb;
    while (i < n - 1 && tA[i + 1] <= t) i++;
    let s;
    if (t <= 0) s = 0;
    else if (i >= n - 1 || t < tD[i]) s = P.cum[i];
    else {
      const dur = tA[i + 1] - tD[i];
      const tau = t - tD[i];
      s = v[i] + v[i + 1] < 0.3 ? P.cum[i] + ((P.cum[i + 1] - P.cum[i]) * tau) / dur : Math.min(P.cum[i] + v[i] * tau + 0.5 * ((v[i + 1] - v[i]) / dur) * tau * tau, P.cum[i + 1]);
    }
    while (j < n - 1 && P.cum[j + 1] <= s) j++;
    const f = j < n - 1 ? (s - P.cum[j]) / (P.cum[j + 1] - P.cum[j] || 1) : 0;
    const x = j < n - 1 ? P.x[j] + (P.x[j + 1] - P.x[j]) * f : P.x[n - 1];
    const y = j < n - 1 ? P.y[j] + (P.y[j + 1] - P.y[j]) * f : P.y[n - 1];
    const h = j < n - 1 ? lerpAng(P.hdg[j], P.hdg[j + 1], f) : P.hdg[n - 1];
    const hh = rev ? (h + 180) % 360 : h;
    const r = hh * D2R;
    S.nx[k] = x;
    S.ny[k] = y;
    S.h[k] = hh;
    S.cx[k] = x - Math.sin(r) * len * 0.45;
    S.cy[k] = y - Math.cos(r) * len * 0.45;
    S.s[k] = s;
    S.sub[k] = sub;
  }
  return k;
}

function req(ax, ay, ah, al, bx, by, bh, bl) {
  if (Math.abs(angDiff(ah, bh)) < 35) {
    const [ux, uy] = dirOf(ah);
    if (Math.abs((bx - ax) * uy - (by - ay) * ux) < 20) return Math.max(SEP, (al + bl) / 2 + 25);
  }
  return SEP;
}

function clashes(m, S, others, occ, strips) {
  let n = 0;
  for (let k = 0, cf; (cf = firstConflict(m, S, others, occ, strips, new Set(), k)); k = cf.k + 1) n++;
  return n;
}

function firstConflict(m, S, committed, occ, strips, ignored, from = 0) {
  const n = S.k1 - S.k0 + 1;
  const pad = SEP + 80;
  committed = committed.filter((c) => c.k1 >= S.k0 + from && c.k0 <= S.k1 && !apart(c.box, S.box[0] - pad, S.box[1] - pad, S.box[2] + pad, S.box[3] + pad));
  strips = strips.filter((rw) => !apart(rw.box, S.box[0] - m.len, S.box[1] - m.len, S.box[2] + m.len, S.box[3] + m.len) && occ.get(rw.ref).length);
  for (let k = from; k < n; k++) {
    const kk = S.k0 + k;
    const t = kk * GRID;
    const nose = [S.nx[k], S.ny[k]];
    for (const rw of strips) {
      if (m.kind === "dep" && rw.ref === m.rw.ref && S.sub[k] === 1 && S.s[k] >= m.sHold - 1) continue;
      if (m.kind === "arr" && rw.ref === m.rw.ref && S.s[k] < m.sClear) continue;
      if (!inStrip(rw, nose, STRIP) && !inStrip(rw, [S.cx[k], S.cy[k]], STRIP)) continue;
      for (const iv of occ.get(rw.ref)) {
        if (iv.key === S.key || t < iv.a || t > iv.b) continue;
        const id = `rwy:${rw.ref}:${iv.key}`;
        if (!ignored.has(id)) return { k, kind: "rwy", rw, until: iv.b, id };
      }
    }
    for (const c of committed) {
      if (kk < c.k0 || kk > c.k1 || ignored.has(c.key)) continue;
      const j = kk - c.k0;
      const r = req(S.cx[k], S.cy[k], S.h[k], S.len, c.cx[j], c.cy[j], c.h[j], c.len);
      if (Math.hypot(S.cx[k] - c.cx[j], S.cy[k] - c.cy[j]) < r) return { k, kind: "sep", c, id: c.key };
    }
  }
  return null;
}

/** The first and last grid step in ka..kb at which c comes near any of pts, through c's cell hash. */
function presence(c, pts, h, len, ka, kb) {
  const lo = Math.max(ka, c.k0);
  const hi = Math.min(kb, c.k1);
  if (hi < lo) return { first: -1, last: -1 };
  const groups = new Map();
  for (const p of pts) {
    const k = cellOf(p[0], p[1]);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(p);
  }
  const near = new Uint8Array(hi - lo + 1);
  const seen = new Set();
  for (const k of groups.keys()) {
    for (const dx of [-1024, 0, 1024]) {
      for (const dy of [-1, 0, 1]) {
        const ck = k + dx + dy;
        if (seen.has(ck)) continue;
        seen.add(ck);
        const list = c.hash.get(ck);
        if (!list) continue;
        const local = [];
        for (const ex of [-1024, 0, 1024]) for (const ey of [-1, 0, 1]) for (const p of groups.get(ck + ex + ey) || []) local.push(p);
        let i = 0;
        let j0 = lo - c.k0;
        let b = list.length;
        while (i < b) {
          const mid = (i + b) >> 1;
          if (list[mid] < j0) i = mid + 1;
          else b = mid;
        }
        for (; i < list.length && list[i] <= hi - c.k0; i++) {
          const j = list[i];
          if (near[j + c.k0 - lo]) continue;
          for (const p of local) {
            const d = Math.hypot(p[0] - c.cx[j], p[1] - c.cy[j]);
            if (d < SEP + 4 || (d < 90 && d < req(p[0], p[1], p[2] ?? h, len, c.cx[j], c.cy[j], c.h[j], c.len) + 4)) {
              near[j + c.k0 - lo] = 1;
              break;
            }
          }
        }
      }
    }
  }
  let first = -1;
  let last = -1;
  for (let kk = lo; kk <= hi; kk++) {
    if (near[kk - lo]) {
      if (first < 0) first = kk;
      last = kk;
    } else if (first >= 0 && kk - last > 15) break;
  }
  return { first, last };
}

function stretch(m, s0, s1) {
  const out = [];
  for (let s = s0; s <= s1; s += 8) {
    const p = at(m.taxi, s);
    const c = centreOf(p.x, p.y, p.h, m.len);
    out.push([c[0], c[1], p.h]);
  }
  return out;
}

function settle(m, committed, occ, strips) {
  const ignored = new Set();
  for (let it = 0; it < 40; it++) {
    time(m);
    const S = sampleMove(m);
    const cf = firstConflict(m, S, committed, occ, strips, ignored);
    if (!cf) return S;
    const save = { stops: m.stops.map((x) => ({ ...x })), w0: m.w0, pause: m.pause };
    if (resolve(m, S, cf, committed)) time(m);
    if (m.late > 3 || m.stops.length === save.stops.length && m.w0 === save.w0 && m.stops.every((x, i) => x.until === save.stops[i].until)) {
      Object.assign(m, save);
      ignored.add(cf.id);
    }
  }
  time(m);
  return sampleMove(m);
}

function resolve(m, S, cf, committed) {
  const k = cf.k;
  const sc = S.s[k];
  if (S.sub[k] !== 1) return m.kind === "dep" && delayPush(m, cf, sc, S.k0 + k);
  if (cf.kind === "rwy") {
    let sw = sc;
    while (sw > 0 && inStrip(cf.rw, [at(m.taxi, sw).x, at(m.taxi, sw).y], HOLD_SHORT)) sw -= 4;
    sw = Math.max(0, sw);
    return addStop(m, sw, Math.max(cf.until + 3, tReach(m, sw) + 5));
  }
  const c = cf.c;
  const H = HORIZON / GRID;
  const end = Math.min(m.taxi.len, sc + 150, m.kind === "dep" ? Math.max(sc, m.sHold + 10) : Infinity);
  const box = [Infinity, Infinity, -Infinity, -Infinity];
  for (let s = Math.max(0, sc - 900); s <= end; s += 16) {
    const p = at(m.taxi, s);
    box[0] = Math.min(box[0], p.x - 150);
    box[1] = Math.min(box[1], p.y - 150);
    box[2] = Math.max(box[2], p.x + 150);
    box[3] = Math.max(box[3], p.y + 150);
  }
  const k0 = Math.ceil(tReach(m, Math.max(0, sc - 900)) / GRID) - 2;
  const k1 = Math.ceil(tReach(m, sc) / GRID) + 2 * H;
  committed = committed.filter((o) => o.k1 >= k0 && o.k0 <= k1 && !apart(o.box, ...box));
  for (let sw = sc - 12; sw > Math.max(-16, sc - 900); sw -= 16) {
    const s = Math.max(0, sw);
    const p = at(m.taxi, s);
    const ctr = centreOf(p.x, p.y, p.h, m.len);
    const kR = Math.ceil(tReach(m, s) / GRID);
    const pr = presence(c, stretch(m, s, end), p.h, m.len, kR, kR + H);
    const kE = pr.first < 0 ? kR + 3 : pr.last + 2;
    if (committed.some((o) => presence(o, [[ctr[0], ctr[1], p.h]], p.h, m.len, kR - 2, kE).first >= 0)) continue;
    return addStop(m, s, Math.max(kE * GRID, tReach(m, s) + 5));
  }
  return m.kind === "dep" && delayPush(m, cf, sc, S.k0 + k);
}

function delayPush(m, cf, sc, kc) {
  if (cf.kind !== "sep") return false;
  const pts = [];
  for (let s = 0; s <= m.push.len; s += 6) {
    const p = at(m.push, s);
    const h = (p.h + 180) % 360;
    const c = centreOf(p.x, p.y, h, m.len);
    pts.push([c[0], c[1], h]);
  }
  pts.push(...stretch(m, 0, Math.min(m.taxi.len, Math.max(sc, 0) + 150)));
  const ka = Math.max(Math.ceil(m.tPush / GRID), kc - 60);
  const pr = presence(cf.c, pts, 0, m.len, ka, ka + HORIZON / GRID);
  if (pr.first < 0) return false;
  const w0 = Math.max(m.w0, (pr.last + 2) * GRID - m.out);
  if (w0 - m.wBase > PUSH_DELAY) return false;
  m.w0 = w0;
  return true;
}

function addStop(m, s, until) {
  const ex = m.stops.find((x) => Math.abs(x.s - s) < 8);
  if (ex) ex.until = Math.max(ex.until, until);
  else m.stops.push({ s, until, label: "hold" });
  m.stops.sort((a, b) => a.s - b.s);
  return true;
}

function coarse(m) {
  const pts = [];
  for (const P of [m.push, m.taxi].filter(Boolean)) for (let s = 0; s <= P.len; s += 20) pts.push([at(P, s).x, at(P, s).y]);
  const a = m.kind === "dep" ? m.out : m.on;
  const b = m.kind === "dep" ? m.off : m.in;
  return { pts, a, b };
}

function chooseGate(G, m, committed, all, strips) {
  m.sGate = null;
  const p = profileOf(m.taxi, { t0: m.tExit, v0: EXIT, fixV0: true, vc: TAXI, capFrom: m.taxi.len - m.leadLen, capV: LEAD, stops: m.stops });
  m.taxiProf = p;
  const W = m.in - m.tExit - p.dur;
  const hr = W >= 150 ? holdRoute(G, m, W, m.tExit, committed) : null;
  if (hr) {
    arrGeometry(G, m.rw, m, hr.nodes);
    m.sGate = nearestS(m.taxi, G.nodes[hr.H]);
    m.coarse = coarse(m);
  } else if (W >= 90) m.sGate = chooseSpot(m, committed, all, strips, m.sClear + 20, m.taxi.len - m.leadLen - 60, W);
}

function chooseStage(G, m, committed, all, strips) {
  m.sStage = null;
  timeDep(m);
  const W = m.slack - m.queue;
  const hr = W >= 180 ? holdRoute(G, m, W, m.tTaxi, committed) : null;
  if (hr) {
    depGeometry(G, m.rw, m, hr.nodes);
    m.sStage = nearestS(m.taxi, G.nodes[hr.H]);
    m.coarse = coarse(m);
  } else if (W >= 150) m.sStage = chooseSpot(m, committed, all, strips, 25, m.sHold - 250, W);
}

function chooseSpot(m, committed, all, strips, s0, s1, W) {
  let best = null;
  for (let s = s0; s <= s1; s += 25) {
    const q = at(m.taxi, s);
    if (strips.some((rw) => inStrip(rw, [q.x, q.y], STRIP + 20))) continue;
    const c = centreOf(q.x, q.y, q.h, m.len);
    const tR = tReach(m, s);
    const ka = Math.ceil(tR / GRID);
    const kb = Math.floor((tR + W) / GRID);
    let score = 0;
    const k = cellOf(c[0], c[1]);
    for (const o of committed) {
      if (o.k1 < ka || o.k0 > kb) continue;
      for (const dx of [-1024, 0, 1024]) {
        for (const dy of [-1, 0, 1]) {
          for (const j of o.hash.get(k + dx + dy) || []) {
            const kk = j + o.k0;
            if (kk >= ka && kk <= kb && kk % 3 === 0 && Math.hypot(o.cx[j] - c[0], o.cy[j] - c[1]) < 80) score += 3;
          }
        }
      }
    }
    for (const o of all) {
      if (o === m || o.done || o.coarse.b < tR || o.coarse.a > tR + W) continue;
      if (o.coarse.pts.some((r) => dist(r, c) < 70)) score += 40;
    }
    if (!best || score <= best.score) best = { s, score };
  }
  return best ? best.s : null;
}

function nightOf(traffic, origin) {
  const off = tzOffset(traffic.date, traffic.tz);
  const t0 = Math.floor((traffic.span || [0])[0]) - 240;
  const n = Math.ceil((traffic.span || [0, 1440])[1] - t0) + 480;
  const el = new Float32Array(n);
  for (let i = 0; i < n; i++) el[i] = sunElevation(traffic.date, t0 + i, origin[0], origin[1], off);
  return (t) => el[Math.max(0, Math.min(n - 1, Math.round(t - t0)))] < 3;
}

function tzOffset(date, tz) {
  const s = new Intl.DateTimeFormat("en-US", { timeZone: tz || "UTC", timeZoneName: "longOffset" }).formatToParts(new Date(`${date}T12:00:00Z`)).find((p) => p.type === "timeZoneName").value;
  const m = /GMT([+-])(\d\d):?(\d\d)?/.exec(s);
  return m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3] || 0)) : 0;
}

function sunElevation(date, tMin, lat, lon, offMin) {
  const [Y, M, D] = date.split("-").map(Number);
  const n = Date.UTC(Y, M - 1, D) / 86400000 + 2440587.5 + (tMin - offMin) / 1440 - 2451545.0;
  const L = (280.46 + 0.9856474 * n) % 360;
  const g = ((357.528 + 0.9856003 * n) % 360) * D2R;
  const lam = (L + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * D2R;
  const eps = (23.439 - 0.0000004 * n) * D2R;
  const dec = Math.asin(Math.sin(eps) * Math.sin(lam));
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam));
  const gmst = (280.46061837 + 360.98564736629 * n) % 360;
  const ha = (gmst + lon) * D2R - ra;
  return Math.asin(Math.sin(lat * D2R) * Math.sin(dec) + Math.cos(lat * D2R) * Math.cos(dec) * Math.cos(ha)) / D2R;
}
