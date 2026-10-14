const KT = 1852 / 3600;
const D2R = Math.PI / 180;
const STEP = 2;
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
const APP_D = 6000;
const FLARE = 260;
const GLIDE = Math.tan(3 * D2R);
const CLIMB = Math.tan(7 * D2R);
const ROLL = 1800;
const CLIMB_R = 300;
const GONE = 120;
const COST = { taxiway: 1, taxilane: 1.25, lead: 2, runway: 8 };

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

export function buildTracks(stage, moment) {
  const G = graphOf(stage);
  const rws = runwaysOf(stage);
  const strips = stage.runways.map((r) => rws.get(r.ends[0].id));
  const use = moment.runways_in_use || {};
  const depRw = rws.get(use.departures) || strips[0];
  const arrRw = rws.get(use.arrivals) || depRw;
  G.depCost = (e) => (G.standSet.has(e.b) ? Infinity : e.len * COST[e.kind]);
  G.depBack = sweepBack(G, G.thresholds[depRw.id], G.depCost, depFree);
  G.holds = G.nodes.map((p, i) => i).filter((i) => {
    const a = G.adj[i];
    if (a.length !== 2 || !a.every((e) => e[2] === "taxiway" || e[2] === "taxilane")) return false;
    const p = G.nodes[i];
    const u = unitv(G.nodes[a[0][0]], p);
    const v = unitv(p, G.nodes[a[1][0]]);
    return u[0] * v[0] + u[1] * v[1] > Math.cos(25 * D2R) && !strips.some((rw) => inStrip(rw, p, STRIP + 70)) && !stage.stands.some((st) => dist(st.xy, p) < 90);
  });
  const stands = new Map(stage.stands.map((s) => [s.ref, s]));
  const [w0, w1] = moment.window.map((m) => m * 60);
  const dusk = duskOf(moment, stage.origin);
  const deps = [];
  const arrs = [];
  for (const ac of moment.aircraft) {
    const st = stands.get(ac.stand);
    for (const leg of ac.legs) {
      if (leg.status !== "flown" || !st) continue;
      if (leg.kind === "dep" && leg.out != null) {
        const out = leg.out * 60;
        const off = (leg.off ?? leg.out + 15) * 60;
        if (off + GONE >= w0 && out <= w1) deps.push({ kind: "dep", ac, leg, st, out, off, len: ac.length || 38 });
      } else if (leg.kind === "arr" && leg.on != null) {
        const on = leg.on * 60;
        const inn = (leg.in ?? leg.on + 10) * 60;
        if (inn >= w0 && on - 120 <= w1) arrs.push({ kind: "arr", ac, leg, st, on, in: inn, len: ac.length || 38 });
      }
    }
  }
  for (const m of deps) {
    const next = moment.aircraft.flatMap((a) => (a.stand === m.ac.stand ? a.legs : [])).filter((l) => l.kind === "arr" && l.status === "flown" && l.in != null && l.in * 60 > m.out);
    m.free = Math.min(Infinity, ...next.map((l) => l.in * 60));
  }
  sequence(deps, arrs, depRw, arrRw, moment.featured || {});
  deps.forEach((m, i) => (m.queue = i ? Math.max(20, Math.min(QUEUE, m.off - deps[i - 1].off - 50)) : QUEUE));
  const depDir = new Set();
  for (const m of deps) {
    routeDep(G, depRw, m);
    for (let i = 1; i < m.nodes.length; i++) depDir.add(m.nodes[i - 1] * G.n + m.nodes[i]);
  }
  const exits = G.nodes.map((p, i) => i).filter((i) => onRunway(arrRw, G.nodes[i]) && G.adj[i].some((e) => e[2] !== "runway"));
  for (const m of arrs) routeArr(G, arrRw, m, depDir, exits);
  const cell = (p) => `${Math.floor(p[0] / 100)},${Math.floor(p[1] / 100)}`;
  const near = new Map();
  for (const m of [...deps, ...arrs]) {
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
  G.spots = [];
  for (const i of G.holds) {
    const p = G.nodes[i];
    if ((near.get(cell(p)) || []).some((q) => dist(p, q) < 90)) continue;
    if (G.spots.some((j) => dist(G.nodes[j], p) < 120)) continue;
    G.spots.push(i);
  }
  G.spotRes = new Map(G.spots.map((i) => [i, []]));
  const occ = new Map(strips.map((r) => [r.ref, []]));
  for (const m of arrs) {
    timeArr(m);
    occ.get(arrRw.ref).push({ a: m.on - 75, b: m.tExit + 20, key: m.ac.key });
  }
  const plans = [];
  const report = { waits: 0, late: [], clashes: 0 };
  const all = [...deps, ...arrs];
  for (const m of all) m.coarse = coarse(m);
  deps.sort((a, b) => a.off - b.off);
  arrs.sort((a, b) => a.on - b.on);
  const feat = moment.featured || {};
  const first = (m) => (m.ac.tail === feat.tail && m.leg.flight === feat.flight ? 0 : 1);
  const order = [...arrs, ...deps].sort((a, b) => first(a) - first(b));
  for (const m of order) {
    if (m.kind === "arr") chooseGate(G, m, plans, all, strips);
    else chooseStage(G, m, plans, all, strips);
    m.S = settle(m, plans, occ, strips);
    plans.push(m.S);
    m.done = true;
    if (m.kind === "dep") occ.get(depRw.ref).push({ a: m.off - 45, b: m.off + 40, key: m.ac.key });
  }
  for (const m of order) {
    const others = plans.filter((x) => x.m !== m);
    const bad = clashes(m, m.S, others, occ, strips);
    if (!bad) continue;
    const save = { stops: m.stops.map((x) => ({ ...x })), w0: m.w0, pause: m.pause, S: m.S };
    const S = settle(m, others, occ, strips);
    if (clashes(m, S, others, occ, strips) < bad) {
      plans[plans.indexOf(m.S)] = S;
      m.S = S;
    } else {
      Object.assign(m, save);
      time(m);
    }
  }
  for (const m of order) {
    report.clashes += clashes(m, m.S, plans.filter((x) => x.m !== m), occ, strips);
    report.waits += m.stops.length;
    if (m.late > 6) report.late.push({ key: m.ac.key, flight: m.leg.flight, late: m.late });
  }
  const byAc = new Map();
  for (const m of all) {
    if (!byAc.has(m.ac.key)) byAc.set(m.ac.key, []);
    byAc.get(m.ac.key).push(m);
  }
  const tracks = new Map();
  for (const ac of moment.aircraft) {
    const tr = trackOf(ac, stands.get(ac.stand), byAc.get(ac.key) || [], depRw, arrRw, dusk / 60, w0);
    tr.featured = ac.tail === feat.tail || ac.key === feat.tail;
    tracks.set(ac.key, tr);
  }
  tracks.report = report;
  tracks.dusk = dusk / 60;
  return tracks;
}

export function poseAt(track, t) {
  const ts = t * 60;
  const segs = track.segs;
  let i = 0;
  while (i < segs.length - 1 && ts >= segs[i].b) i++;
  const p = segs[i].pose(ts);
  p.lights = lightsOf(p.phase, t, track.dusk, p.beacon);
  delete p.beacon;
  return p;
}

function sequence(deps, arrs, depRw, arrRw, feat) {
  const pinned = (m) => m.ac.tail === feat.tail && m.leg.flight === feat.flight;
  const X = crossing(depRw, arrRw);
  const acc = (V_ROT * V_ROT) / (2 * ROLL);
  const xd = X ? along(depRw, X) - 110 : -1;
  const dT = xd > 0 && xd < ROLL ? Math.sqrt((2 * xd) / acc) : null;
  const aT = X ? (along(arrRw, X) - TD_PAST) / V_TD : null;
  const spread = (list, key, gap) => {
    let moved = false;
    list.sort((a, b) => a[key] - b[key]);
    for (let i = 1; i < list.length; i++) {
      const a = list[i - 1];
      const b = list[i];
      const d = gap - (b[key] - a[key]);
      if (d <= 0.01) continue;
      if (pinned(a)) b[key] += d;
      else if (pinned(b)) a[key] -= d;
      else {
        a[key] -= d / 2;
        b[key] += d / 2;
      }
      moved = true;
    }
    return moved;
  };
  for (let it = 0; it < 40; it++) {
    const a = spread(deps, "off", DEP_SEP);
    let moved = spread(arrs, "on", ARR_SEP) || a;
    if (dT != null && aT != null) {
      for (const d of deps) {
        for (const a of arrs) {
          const gap = d.off + dT - (a.on + aT);
          if (Math.abs(gap) >= X_SEP) continue;
          const shift = gap >= 0 ? X_SEP - gap : -(X_SEP + gap);
          if (pinned(d)) a.on -= shift;
          else d.off += shift;
          moved = true;
        }
      }
    }
    if (!moved) break;
  }
}

function crossing(r1, r2) {
  if (r1.ref === r2.ref) return null;
  const den = r1.u[0] * r2.u[1] - r1.u[1] * r2.u[0];
  if (Math.abs(den) < 1e-6) return null;
  const t = ((r2.xy[0] - r1.xy[0]) * r2.u[1] - (r2.xy[1] - r1.xy[1]) * r2.u[0]) / den;
  const p = [r1.xy[0] + r1.u[0] * t, r1.xy[1] + r1.u[1] * t];
  return t > 0 && t < r1.len && along(r2, p) > 0 && along(r2, p) < r2.len ? p : null;
}

function lightsOf(phase, t, dusk, beacon) {
  const air = phase === "climb" || phase === "approach" || phase === "flare";
  const rwy = phase === "lineup" || phase === "roll" || phase === "rollout";
  return {
    nav: phase !== "absent" && (t >= dusk || phase !== "parked"),
    beacon: phase !== "absent" && (phase !== "parked" || !!beacon),
    strobe: air || rwy,
    landing: air || rwy,
    taxi: phase === "taxi" || phase === "taxi_in",
  };
}

function trackOf(ac, st, moves, depRw, arrRw, dusk, w0) {
  const spec = { class: ac.class, length: ac.length, span: ac.span, jetblue: !!ac.jetblue };
  const park = st ? { x: st.xy[0], y: st.xy[1], heading: st.heading } : { x: 0, y: 0, heading: 0 };
  const flown = ac.legs.filter((l) => l.status === "flown");
  const key = (l) => (l.kind === "dep" ? l.out : l.on) ?? 0;
  flown.sort((a, b) => key(a) - key(b));
  const segs = [];
  let t = -Infinity;
  let state = flown.length && flown[0].kind === "arr" ? "absent" : "parked";
  let last = park;
  let beaconTo = -Infinity;
  let beaconFrom = Infinity;
  const hold = (b) => {
    if (b <= t) return;
    const s = state;
    const at = { ...last };
    const bt = beaconTo;
    const bf = beaconFrom;
    segs.push({
      a: t,
      b,
      pose: (ts) => ({ x: at.x, y: at.y, heading: at.heading, alt: 0, pitch: 0, speed: 0, phase: s, beacon: s === "parked" && (ts < bt || ts >= bf) }),
    });
    t = b;
  };
  const paths = [];
  const events = [];
  const ev = (leg, kind, ts) => events.push({ flight: leg.flight, kind, t: ts / 60 });
  for (const leg of flown) {
    const m = moves.find((x) => x.leg === leg);
    if (leg.kind === "dep") {
      if (!m) {
        if ((leg.off ?? leg.out) * 60 + GONE < w0) {
          hold((leg.off ?? leg.out) * 60 + GONE);
          state = "absent";
        }
        continue;
      }
      beaconFrom = m.out;
      hold(m.tPush);
      beaconFrom = Infinity;
      segs.push({ a: m.tPush, b: m.tTaxi, pose: (ts) => groundPose(m, ts) });
      segs.push({ a: m.tTaxi, b: m.tRoll, pose: (ts) => groundPose(m, ts) });
      segs.push({ a: m.tRoll, b: m.tRoll + GONE, pose: (ts) => climbPose(m, depRw, ts) });
      ev(leg, "pushback", m.tPush);
      ev(leg, "taxi", m.tTaxi);
      ev(leg, "roll", m.tRoll);
      ev(leg, "airborne", m.tRoll + V_ROT / ((V_ROT * V_ROT) / (2 * ROLL)));
      ev(leg, "gone", m.tRoll + GONE);
      t = m.tRoll + GONE;
      const end = climbPose(m, depRw, t);
      last = { x: end.x, y: end.y, heading: end.heading };
      state = "absent";
      paths.push({ kind: "push", pts: thin(m.push) }, { kind: "taxi", pts: thin(m.taxi), hold: at(m.taxi, m.sHold), gate: m.sStage != null ? at(m.taxi, m.sStage) : null, stops: m.stops.map((s) => at(m.taxi, s.s)) });
    } else {
      if (!m) {
        if ((leg.in ?? leg.on) * 60 < w0) {
          hold(Math.max(t, (leg.in ?? leg.on) * 60 - 1));
          state = "parked";
          last = park;
        }
        continue;
      }
      last = approachPose(m, arrRw, m.tApp);
      hold(m.tApp);
      segs.push({ a: m.tApp, b: m.on, pose: (ts) => approachPose(m, arrRw, ts) });
      segs.push({ a: m.on, b: m.tExit, pose: (ts) => rolloutPose(m, arrRw, ts) });
      segs.push({ a: m.tExit, b: m.tPark, pose: (ts) => groundPose(m, ts) });
      ev(leg, "approach", m.tApp);
      ev(leg, "touchdown", m.on);
      ev(leg, "vacate", m.tExit);
      ev(leg, "parked", m.tPark);
      t = m.tPark;
      const end = at(m.taxi, m.taxi.len);
      last = { x: end.x, y: end.y, heading: end.h };
      state = "parked";
      beaconTo = m.in + 60;
      paths.push({ kind: "taxi_in", pts: thin(m.taxi), gate: m.sGate != null ? at(m.taxi, m.sGate) : null, stops: m.stops.map((s) => at(m.taxi, s.s)) });
    }
  }
  hold(Infinity);
  return { key: ac.key, tail: ac.tail, carrier: ac.carrier, jetblue: !!ac.jetblue, spec, stand: ac.stand, legs: ac.legs, segs, dusk, paths, events };
}

function thin(P) {
  const out = [];
  for (let i = 0; i < P.n; i += 4) out.push([P.x[i], P.y[i]]);
  out.push([P.x[P.n - 1], P.y[P.n - 1]]);
  return out;
}

function groundPose(m, t) {
  if (m.kind === "dep") {
    if (t < m.tPush) return { x: m.st.xy[0], y: m.st.xy[1], heading: m.st.heading, alt: 0, pitch: 0, speed: 0, phase: "parked", beacon: true, sub: -1, s: 0 };
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
  return { x: p.x, y: p.y, heading: reverse ? (p.h + 180) % 360 : p.h, alt: 0, pitch: 0, speed: q.v, stopped: q.stopped, label: q.label, s: q.s };
}

function approachPose(m, rw, ts) {
  const d = Math.max(0, (m.on - ts) * V_APP);
  const along = TD_PAST - d;
  const flare = d < FLARE;
  const alt = flare ? GLIDE * FLARE * hermite(d / FLARE) : GLIDE * d;
  const pitch = flare ? 2.5 + 3 * ease(1 - d / FLARE) : 2.5;
  return { x: rw.xy[0] + rw.u[0] * along, y: rw.xy[1] + rw.u[1] * along, heading: rw.heading, alt, pitch, speed: V_APP, phase: flare ? "flare" : "approach" };
}

function hermite(u) {
  return -0.9 * u * u * u + 1.8 * u * u + 0.1 * u;
}

function rolloutPose(m, rw, ts) {
  const T = m.tExit - m.on;
  const tau = Math.min(Math.max(ts - m.on, 0), T);
  const a = (V_TD - m.vx) / T;
  const along = TD_PAST + V_TD * tau - 0.5 * a * tau * tau;
  return { x: rw.xy[0] + rw.u[0] * along, y: rw.xy[1] + rw.u[1] * along, heading: rw.heading, alt: 0, pitch: 5.5 * (1 - ease(tau / 4)), speed: V_TD - a * tau, phase: "rollout" };
}

function climbPose(m, rw, ts) {
  const acc = (V_ROT * V_ROT) / (2 * ROLL);
  const tRoll = V_ROT / acc;
  const tau = Math.max(0, ts - m.tRoll);
  const [ox, oy] = m.roll0;
  if (tau < tRoll) {
    const s = 0.5 * acc * tau * tau;
    return { x: ox + rw.u[0] * s, y: oy + rw.u[1] * s, heading: rw.heading, alt: 0, pitch: 8 * ease((s - (ROLL - 260)) / 260), speed: acc * tau, phase: "roll" };
  }
  const t2 = tau - tRoll;
  const ka = 0.6;
  const tc = (V_CLIMB - V_ROT) / ka;
  const x = t2 < tc ? V_ROT * t2 + 0.5 * ka * t2 * t2 : V_ROT * tc + 0.5 * ka * tc * tc + V_CLIMB * (t2 - tc);
  const alt = x < CLIMB_R ? (CLIMB * x * x) / (2 * CLIMB_R) : CLIMB * (x - CLIMB_R / 2);
  const s = ROLL + x;
  return { x: ox + rw.u[0] * s, y: oy + rw.u[1] * s, heading: rw.heading, alt, pitch: 8 + 5 * ease(t2 / 6), speed: Math.min(V_CLIMB, V_ROT + ka * t2), phase: "climb" };
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
      ends.set(e.id, { id: e.id, ref: r.ref, xy: e.xy, u, heading: hdgOf(u[0], u[1]), len, width: r.width });
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

function sweepBack(G, dst, cost, free) {
  const E = G.dir;
  const d = new Float64Array(E.length).fill(Infinity);
  const next = new Int32Array(E.length).fill(-1);
  const h = [];
  for (const f of G.inn[dst]) {
    const c = cost(E[f]);
    if (c < d[f]) {
      d[f] = c;
      hpush(h, [c, f]);
    }
  }
  while (h.length) {
    const [dd, f] = hpop(h);
    if (dd > d[f]) continue;
    for (const e of G.inn[E[f].a]) {
      if (E[e].a === E[f].b) continue;
      const c = dd + cost(E[e]) + (free(E[e], E[f]) ? 0 : turnCost(E[e].h, E[f].h));
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
  const F = sweep(G, src, cost, free, dep ? null : m.rw.heading);
  const B = dep ? G.depBack : m.back;
  const E = G.dir;
  const cands = [];
  for (const H of G.spots) {
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
  const busy = (H, a, b) => G.spotRes.get(H).some(([x, y]) => x < b && y > a);
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
      for (const H of G.spots) if (H !== c.H && dist(G.nodes[nodes[i]], G.nodes[H]) < 100) pass.push([H, t0 + d / (TAXI * 0.8) + (after ? W : 0)]);
    }
    if (pass.some(([H, t]) => busy(H, t - 90, t + 90))) continue;
    G.spotRes.get(c.H).push([c.tR - 60, c.tR + W + 60]);
    for (const [H, t] of pass) G.spotRes.get(H).push([t - 90, t + 90]);
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
  const S = G.stand_nodes[m.ac.stand];
  const B = G.depBack;
  const f = G.out[S].reduce((a, e) => (B.d[e] < B.d[a] ? e : a), G.out[S][0]);
  m.rw = rw;
  m.base = B.d[f];
  depGeometry(G, rw, m, isFinite(B.d[f]) ? follow(G, B, f) : [S]);
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
  const q = dirOf(m.st.heading + 180);
  let run = 0;
  for (let i = 1; i <= j; i++) run += dist(xy(i - 1), xy(i));
  while (j < e - 1 && run < 250 && G.adj[nodes[j]].length < 3) {
    const d = unitv(xy(j), xy(j + 1));
    if (d[0] * q[0] + d[1] * q[1] < Math.cos(25 * D2R)) break;
    run += dist(xy(j), xy(j + 1));
    j++;
  }
  let ahead = xy(j);
  for (let i = j, acc = 0; i < e && acc < 40; i++) {
    acc += dist(xy(i), xy(i + 1));
    ahead = xy(i + 1);
  }
  const pb = pushback(m.st.xy, m.st.heading, xy(j), unitv(xy(j), ahead), run);
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

function pushback(A, hs, J, d, lead) {
  const hq = (hs + 180) % 360;
  const q = dirOf(hq);
  const phi = angDiff(hq, hdgOf(d[0], d[1]) + 180);
  if (Math.abs(phi) < 15) return { pts: [A, J], nose: hs + phi };
  if (Math.abs(phi) <= 110) {
    const den = q[0] * d[1] - q[1] * d[0];
    if (Math.abs(den) > 1e-6) {
      const tX = ((J[0] - A[0]) * d[1] - (J[1] - A[1]) * d[0]) / den;
      const tan = Math.tan((Math.abs(phi) / 2) * D2R);
      const r = Math.min(PUSH_R, (tX - 5) / tan);
      if (r >= 6) {
        const B = [A[0] + q[0] * (tX - r * tan), A[1] + q[1] * (tX - r * tan)];
        return { pts: [A, B, ...arc(B, hq, phi, r)], nose: hs + phi };
      }
    }
  }
  const ph = Math.sign(phi) * Math.min(Math.abs(phi), 110);
  const B = [A[0] + q[0] * Math.max(5, lead - PUSH_R), A[1] + q[1] * Math.max(5, lead - PUSH_R)];
  return { pts: [A, B, ...arc(B, hq, ph, PUSH_R)], nose: hs + ph };
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

function routeArr(G, rw, m, depDir, exits) {
  const S = G.stand_nodes[m.ac.stand];
  const ban = (e) => e.kind === "runway" && onRunway(rw, G.nodes[e.a]) && onRunway(rw, G.nodes[e.b]);
  m.cost = (e) => (ban(e) || (G.standSet.has(e.b) && e.b !== S) ? Infinity : e.len * COST[e.kind] * (depDir.has(e.b * G.n + e.a) ? 2.5 : 1));
  m.back = sweepBack(G, S, m.cost, arrFree);
  let best = null;
  for (const lo of [1300, 900]) {
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
  m.xAlong = aX - 30;
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
  for (let it = 0; it < 12; it++) {
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
  return S;
}

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

function presence(c, pts, h, len, ka, kb) {
  let first = -1;
  let lastNear = -1;
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const p of pts) {
    x0 = Math.min(x0, p[0] - 90);
    y0 = Math.min(y0, p[1] - 90);
    x1 = Math.max(x1, p[0] + 90);
    y1 = Math.max(y1, p[1] + 90);
  }
  for (let kk = Math.max(ka, c.k0); kk <= Math.min(kb, c.k1); kk++) {
    const j = kk - c.k0;
    let near = false;
    if (c.cx[j] > x0 && c.cx[j] < x1 && c.cy[j] > y0 && c.cy[j] < y1) {
      for (const p of pts) {
        const d = Math.hypot(p[0] - c.cx[j], p[1] - c.cy[j]);
        if (d < SEP + 4 || (d < 90 && d < req(p[0], p[1], p[2] ?? h, len, c.cx[j], c.cy[j], c.h[j], c.len) + 4)) {
          near = true;
          break;
        }
      }
    }
    if (near) {
      if (first < 0) first = kk;
      lastNear = kk;
    } else if (first >= 0 && kk - lastNear > 15) break;
  }
  return { first, last: lastNear };
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
  } else if (W >= 120) {
    m.w0 = m.wBase = Math.max(0, Math.min(W - 30, m.free - m.out - 480));
    if (W - m.w0 >= 150) {
      timeDep(m);
      m.sStage = chooseSpot(m, committed, all, strips, 25, m.sHold - 250, W - m.w0);
    }
  }
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
    for (const o of committed) {
      for (let kk = Math.max(ka, o.k0); kk <= Math.min(kb, o.k1); kk += 3) {
        const j = kk - o.k0;
        if (Math.hypot(o.cx[j] - c[0], o.cy[j] - c[1]) < 80) score += 3;
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

function duskOf(moment, origin) {
  const off = tzOffset(moment.date, moment.tz);
  for (let t = 15 * 60; t < 21 * 60; t += 0.25) if (sunElevation(moment.date, t, origin[0], origin[1], off) < -0.833) return t * 60;
  return 18 * 3600;
}

function tzOffset(date, tz) {
  const s = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "longOffset" }).formatToParts(new Date(`${date}T12:00:00Z`)).find((p) => p.type === "timeZoneName").value;
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
