// node tests/viewer/stage.test.mjs STAGE TRAFFIC: the whole-day tracks web/stage/motion.js builds from a traffic sidecar, checked.
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { buildTracks, poseAt } from "../../web/stage/motion.js";

const load = (f) => {
  const b = readFileSync(f);
  return JSON.parse((b[0] === 0x1f && b[1] === 0x8b ? gunzipSync(b) : b).toString("utf8"));
};
const [stage, traffic] = process.argv.slice(2, 4).map(load);
const asked = (process.argv[4] || "").split(",").filter(Boolean).map((s) => s.split("@"));
const KT = 1852 / 3600;
const TAXI = new Set(["pushback", "taxi", "hold", "lineup", "taxi_in"]);
const hhmm = (t) => {
  const s = Math.round(t * 60);
  return `${String(Math.floor(s / 3600) % 24).padStart(2, "0")}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};
const fails = [];
const check = (ok, msg) => ok || fails.push(msg);

const t0 = performance.now();
const T = buildTracks(stage, traffic);
const ms = performance.now() - t0;
const { tracks, report } = T;
console.log(`${traffic.hub} ${traffic.date}: ${tracks.size} aircraft, ${report.movements} movements planned in ${ms.toFixed(0)} ms · ${report.waits} give-way stops, ${report.clashes} movements left in conflict, ${report.late.length} late`);
check(tracks.size === traffic.aircraft.length, `${tracks.size} tracks for ${traffic.aircraft.length} aircraft`);

const [w0, w1] = traffic.span;
let nan = 0;
let maxTaxi = 0;
let maxTaxiAt = "";
let poses = 0;
for (let t = w0, i = 0; t <= w1; t += 0.25, i++) {
  const near = T.near(t, 0.25);
  for (const [key, tr] of i % 8 ? [...near].map((k) => [k, tracks.get(k)]) : tracks) {
    const p = poseAt(tr, t);
    poses++;
    if (p.phase !== "absent" && !near.has(key)) fails.push(`${key} ${p.phase} at ${hhmm(t)} but not in near(t)`);
    for (const f of ["x", "y", "heading", "alt", "pitch", "speed", "gear"]) if (!Number.isFinite(p[f])) nan++;
    if (TAXI.has(p.phase) && p.speed / KT > maxTaxi) {
      maxTaxi = p.speed / KT;
      maxTaxiAt = `${key} ${p.phase} ${hhmm(t)}`;
    }
  }
}
check(nan === 0, `${nan} non-finite pose values`);
check(maxTaxi <= 30, `max taxi speed ${maxTaxi.toFixed(1)} kt (${maxTaxiAt})`);
console.log(`${poses} poses every 15 s of the aircraft about: ${nan} non-finite · max taxi speed ${maxTaxi.toFixed(1)} kt (${maxTaxiAt})`);

const thr = new Map(stage.runways.flatMap((r) => r.ends.map((e) => [e.id, e.xy])));
const stands = new Map(stage.stands.map((s) => [s.ref, s]));
let nDep = 0;
let nArr = 0;
let worstDep = 0;
let worstArr = 0;
let worstPush = 0;
const late = [];
for (const [key, tr] of tracks) {
  for (const v of tr.visits) {
    const d = v.dep;
    if (d && d.status === "flown" && v.to != null && d.off != null) {
      nDep++;
      const event = (kind, t) => tr.events.filter((e) => e.flight === d.flight && e.kind === kind).sort((x, y) => Math.abs(x.t - t) - Math.abs(y.t - t))[0];
      const push = event("pushback", d.out);
      const off = event("takeoff", d.off);
      check(push && off, `${key} ${d.flight}: no pushback or takeoff event`);
      if (!push || !off) continue;
      check(push.t >= d.out - 0.01, `${key} ${d.flight}: pushback ${hhmm(push.t)} before out ${hhmm(d.out)}`);
      worstDep = Math.max(worstDep, Math.abs(off.t - d.off));
      check(Math.abs(off.t - d.off) <= (tr.task ? 2.5 : 4), `${key} ${d.flight}: airborne ${hhmm(off.t)} vs wheels-off ${hhmm(d.off)}`);
      const roll = poseAt(tr, off.t - 0.3);
      const end = thr.get(T.config(d.off).departures);
      const rw = stage.runways.find((r) => r.ends.some((e) => e.id === T.config(d.off).departures));
      const left = Math.hypot(rw.ends[0].xy[0] - rw.ends[1].xy[0], rw.ends[0].xy[1] - rw.ends[1].xy[1]) - Math.hypot(roll.x - end[0], roll.y - end[1]);
      check(roll.phase === "roll" && left > 300, `${key} ${d.flight}: ${roll.phase} with ${left.toFixed(0)} m of ${T.config(d.off).departures} left just before liftoff`);
      const up = poseAt(tr, off.t + 0.3);
      check(up.phase === "climb" && up.gear < 0.05, `${key} ${d.flight}: gear ${up.gear?.toFixed(2)} 18 s after liftoff (${up.phase})`);
      const st = stands.get(v.stand);
      let far = 0;
      for (let t = push.t; t <= push.t + 6; t += 1 / 60) {
        const p = poseAt(tr, t);
        if (p.phase !== "pushback") break;
        far = Math.max(far, Math.hypot(p.x - st.xy[0], p.y - st.xy[1]));
      }
      worstPush = Math.max(worstPush, far / (tr.spec.length || 38));
      check(far <= 1.6 * (tr.spec.length || 38) + 20, `${key} ${d.flight}: pushed ${far.toFixed(0)} m from ${v.stand}`);
    }
    const a = v.arr;
    if (a && a.status === "flown" && v.from != null && a.on != null) {
      nArr++;
      const land = tr.events.filter((e) => e.flight === a.flight && e.kind === "landing").sort((x, y) => Math.abs(x.t - a.on) - Math.abs(y.t - a.on))[0];
      check(land && Math.abs(land.t - a.on) <= 2.5, `${key} ${a.flight}: lands ${land ? hhmm(land.t) : "never"} vs wheels-on ${hhmm(a.on)}`);
      if (!land) continue;
      worstArr = Math.max(worstArr, Math.abs(land.t - a.on));
      const pOn = poseAt(tr, land.t + 0.5 / 60);
      check(pOn.phase === "rollout", `${key} ${a.flight}: ${pOn.phase} just after touchdown ${hhmm(land.t)}`);
      const gate = tr.events.filter((e) => e.flight === a.flight && e.kind === "gate_in").sort((x, y) => Math.abs(x.t - a.in) - Math.abs(y.t - a.in))[0];
      if (gate && gate.t > a.in + 0.1) late.push(`${a.flight} +${(gate.t - a.in).toFixed(1)}`);
      const p = poseAt(tr, Math.max(a.in, gate?.t ?? a.in) + 0.5 / 60);
      const st = stands.get(v.stand);
      check(p.phase === "parked" && Math.hypot(p.x - st.xy[0], p.y - st.xy[1]) < 1, `${key} ${a.flight}: ${p.phase} at in, ${Math.hypot(p.x - st.xy[0], p.y - st.xy[1]).toFixed(0)} m from ${v.stand}`);
      const down = poseAt(tr, land.t - 1.5);
      check(down.phase === "approach" && down.gear > 0.99, `${key} ${a.flight}: gear ${down.gear?.toFixed(2)} 90 s before landing`);
      const up = poseAt(tr, land.t - 3.2);
      check(up.phase === "approach" && up.gear < 0.01, `${key} ${a.flight}: ${up.phase} with gear ${up.gear?.toFixed(2)} 3.2 min out`);
    }
  }
}
console.log(`${nDep} departures: worst |liftoff - wheels-off| ${(worstDep * 60).toFixed(0)} s, longest pushback ${worstPush.toFixed(2)} lengths · ${nArr} arrivals: worst |touchdown - wheels-on| ${(worstArr * 60).toFixed(0)} s, ${late.length} at the stand more than 6 s after in-time`);

let overlap = 0;
for (const [ref, list] of T.stays) {
  for (let i = 1; i < list.length; i++) {
    if (list[i].a < list[i - 1].b - 1 && list[i].key !== list[i - 1].key) {
      overlap++;
      if (overlap <= 5) fails.push(`${ref}: ${list[i - 1].key} until ${hhmm(list[i - 1].b / 60)} and ${list[i].key} from ${hhmm(list[i].a / 60)}`);
    }
  }
}
console.log(`stays: ${[...T.stays.values()].reduce((n, l) => n + l.length, 0)} at ${T.stays.size} stands, ${overlap} overlapping`);

const kinds = new Set(T.events.map((e) => e.kind));
check(["pushback", "takeoff", "landing", "gate_in"].every((k) => kinds.has(k) || !T.events.length), `event kinds ${[...kinds]}`);
check(T.events.every((e, i) => !i || e.t >= T.events[i - 1].t), "events out of order");

let jumps = 0;
let turns = 0;
for (const [key, tr] of tracks) {
  for (const iv of tr.life) {
    const a = Math.max(iv.a / 60, w0);
    const b = Math.min(iv.b / 60, w1);
    let prev = null;
    for (let t = a; t <= b; t += 1 / 60) {
      const p = poseAt(tr, t);
      if (p.phase === "parked") {
        const seg = tr.segs.find((g) => g.a <= t * 60 && t * 60 < g.b);
        if (seg && poseAt(tr, Math.min(seg.b / 60, b) - 1e-4).phase === "parked") t = Math.max(t, Math.min(seg.b / 60, b) - 2 / 60);
        prev = null;
        continue;
      }
      if (prev && prev.phase !== "absent" && p.phase !== "absent") {
        const d = Math.hypot(p.x - prev.x, p.y - prev.y);
        if (d > Math.max(p.speed, prev.speed) * 1.05 + 1.5) {
          jumps++;
          if (jumps <= 5) console.log(`  jump ${key} ${hhmm(t)} ${prev.phase}->${p.phase} ${d.toFixed(1)} m in 1 s`);
        }
        const dh = Math.abs(((p.heading - prev.heading + 540) % 360) - 180);
        if (dh > 25 && (TAXI.has(p.phase) || TAXI.has(prev.phase))) turns++;
      }
      prev = p;
    }
  }
}
check(jumps === 0, `${jumps} position jumps`);
console.log(`position jumps: ${jumps} · heading changes over 25 deg/s on the ground: ${turns}`);

const keys = [...tracks.keys()];
const len = new Map(keys.map((k) => [k, tracks.get(k).spec.length || 38]));
const centre = (p, l) => [p.x - Math.sin((p.heading * Math.PI) / 180) * l * 0.45, p.y - Math.cos((p.heading * Math.PI) / 180) * l * 0.45];
let close = 0;
const pairs = new Set();
for (let t = w0; t <= w1; t += 5 / 60) {
  const live = [...T.near(t, 0.1)].map((k) => [k, poseAt(tracks.get(k), t)]).filter(([, p]) => p.phase !== "absent" && p.alt <= 0);
  for (let i = 0; i < live.length; i++) {
    for (let j = i + 1; j < live.length; j++) {
      const [ka, pa] = live[i];
      const [kb, pb] = live[j];
      if (!TAXI.has(pa.phase) || !TAXI.has(pb.phase)) continue;
      const ca = centre(pa, len.get(ka));
      const cb = centre(pb, len.get(kb));
      if (Math.hypot(ca[0] - cb[0], ca[1] - cb[1]) < 40) {
        close++;
        pairs.add(`${ka}~${kb}`);
      }
    }
  }
}
console.log(`taxiing pairs closer than 40 m centre to centre: ${pairs.size} pairs, ${close} samples of 5 s`);

const again = buildTracks(stage, traffic);
let same = true;
for (let t = w0; t <= w1; t += 7.3) for (const [key, tr] of tracks) same &&= JSON.stringify(poseAt(tr, t)) === JSON.stringify(poseAt(again.tracks.get(key), t));
check(same, "a second build differs");

if (asked.length) {
  const out = {};
  for (const [key, t] of asked) {
    const p = poseAt(tracks.get(key), +t);
    const stay = tracks.get(key).stays.find((s) => s.a <= +t * 60 && +t * 60 < s.b);
    out[`${key}@${t}`] = { phase: p.phase, x: p.x, y: p.y, heading: p.heading, gear: p.gear, stand: stay?.stand ?? null };
  }
  console.log(`POSES ${JSON.stringify(out)}`);
}
console.log(`EVENTS ${JSON.stringify(T.events)}`);

if (fails.length) {
  console.log(`FAIL (${fails.length})`);
  for (const f of fails.slice(0, 40)) console.log(`  ${f}`);
  process.exit(1);
}
console.log("PASS");
