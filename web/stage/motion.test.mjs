import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { buildTracks, poseAt } from "./motion.js";

const here = dirname(fileURLToPath(import.meta.url));
const dir = join(here, "../../cache/stage/BOS");
const load = (f) => {
  const b = readFileSync(join(dir, f));
  return JSON.parse((b[0] === 0x1f && b[1] === 0x8b ? gunzipSync(b) : b).toString("utf8"));
};
const stage = load("stage.json.gz");
const moment = load("moment-bos-20250216.json");
const KT = 1852 / 3600;
const TAXI = new Set(["pushback", "taxi", "hold", "lineup", "taxi_in"]);
const hhmm = (t) => {
  const s = Math.round(t * 60);
  return `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};
const fails = [];
const check = (ok, msg) => ok || fails.push(msg);

const t0 = performance.now();
const tracks = buildTracks(stage, moment);
const ms = performance.now() - t0;
console.log(`built ${tracks.size} tracks in ${ms.toFixed(0)} ms · nav lights from ${hhmm(tracks.dusk)} · resolver: ${tracks.report.waits} waits, ${tracks.report.clashes} clash samples left, ${tracks.report.late.length} late`);

const [w0, w1] = moment.window;
const dt = 5 / 60;
let nan = 0;
let maxTaxi = 0;
let maxTaxiKey = "";
const samples = new Map();
for (const [key, tr] of tracks) {
  const row = [];
  for (let t = w0; t <= w1 + 1e-9; t += dt) {
    const p = poseAt(tr, t);
    for (const f of ["x", "y", "heading", "alt", "pitch", "speed"]) if (!Number.isFinite(p[f])) nan++;
    if (TAXI.has(p.phase) && p.speed / KT > maxTaxi) {
      maxTaxi = p.speed / KT;
      maxTaxiKey = `${key} ${p.phase} ${hhmm(t)}`;
    }
    row.push(p);
  }
  samples.set(key, row);
}
check(nan === 0, `${nan} NaN values`);
check(maxTaxi <= 30, `max taxi speed ${maxTaxi.toFixed(1)} kt`);
console.log(`NaN values: ${nan} · max taxi speed ${maxTaxi.toFixed(1)} kt (${maxTaxiKey})`);

const thr = new Map(stage.runways.flatMap((r) => r.ends.map((e) => [e.id, e.xy])));
const depThr = thr.get(moment.runways_in_use.departures);
let nDep = 0;
let worstDep = 0;
const moved = [];
for (const [key, tr] of tracks) {
  for (const leg of tr.legs) {
    if (leg.kind !== "dep" || leg.status !== "flown" || leg.off == null || leg.off < w0 || leg.off > w1) continue;
    nDep++;
    let start = null;
    for (let t = leg.off - 3; t <= leg.off + 3; t += 1 / 60) {
      if (poseAt(tr, t).phase === "roll") {
        start = t;
        break;
      }
    }
    const slot = tr.events.find((e) => e.flight === leg.flight && e.kind === "roll");
    const err = start == null ? Infinity : Math.abs(start - leg.off);
    if (err > 0.2 && slot && Math.abs(start - slot.t) <= 0.2 && Math.abs(slot.t - leg.off) <= 1.5) moved.push(`${leg.flight} ${hhmm(leg.off)}->${hhmm(slot.t)}`);
    else {
      worstDep = Math.max(worstDep, err);
      check(err <= 0.2, `${key} ${leg.flight}: roll starts ${start == null ? "never" : hhmm(start)} vs off ${hhmm(leg.off)}`);
    }
    const p = poseAt(tr, start + 0.5 / 60);
    const d = Math.hypot(p.x - depThr[0], p.y - depThr[1]);
    check(d < 200, `${key} ${leg.flight}: ${d.toFixed(0)} m from threshold at roll start`);
  }
}
console.log(`departures with off in window: ${nDep} · worst |roll start - off| ${(worstDep * 60).toFixed(1)} s · ${moved.length} spaced to keep 60 s between takeoffs on one runway: ${moved.join(", ")}`);

const late = [];
for (const tr of tracks.values()) {
  for (const e of tr.events) {
    if (e.kind !== "pushback") continue;
    const leg = tr.legs.find((l) => l.flight === e.flight && l.kind === "dep");
    if (e.t - leg.out > 0.1) late.push(`${leg.flight} +${(e.t - leg.out).toFixed(1)}`);
  }
}
console.log(`pushbacks held at the gate after out: ${late.length}${late.length ? " (min) " + late.join(", ") : ""}`);

const stands = new Map(stage.stands.map((s) => [s.ref, s]));
let nArr = 0;
for (const [key, tr] of tracks) {
  for (const leg of tr.legs) {
    if (leg.kind !== "arr" || leg.status !== "flown" || leg.in == null || leg.in < w0 || leg.in > w1) continue;
    nArr++;
    const p = poseAt(tr, leg.in + 0.5 / 60);
    const st = stands.get(tr.stand);
    const d = Math.hypot(p.x - st.xy[0], p.y - st.xy[1]);
    check(p.phase === "parked" && d < 1, `${key} ${leg.flight}: at in ${hhmm(leg.in)} phase ${p.phase}, ${d.toFixed(1)} m from ${tr.stand}`);
    const pOn = poseAt(tr, leg.on);
    check(pOn.phase === "flare" || pOn.phase === "rollout", `${key} ${leg.flight}: phase at on is ${pOn.phase}`);
  }
}
console.log(`arrivals with in in window: ${nArr}`);

const keys = [...tracks.keys()];
const len = new Map(keys.map((k) => [k, tracks.get(k).spec.length || 38]));
const centre = (p, l) => [p.x - Math.sin((p.heading * Math.PI) / 180) * l * 0.45, p.y - Math.cos((p.heading * Math.PI) / 180) * l * 0.45];
const pairs = new Map();
let close = 0;
let parkedClose = 0;
const nearParked = new Map();
const nS = samples.get(keys[0]).length;
for (let i = 0; i < nS; i++) {
  for (let a = 0; a < keys.length; a++) {
    const pa = samples.get(keys[a])[i];
    if (pa.phase === "absent" || pa.alt > 0) continue;
    for (let b = a + 1; b < keys.length; b++) {
      const pb = samples.get(keys[b])[i];
      if (pb.phase === "absent" || pb.alt > 0) continue;
      const ta = TAXI.has(pa.phase);
      const tb = TAXI.has(pb.phase);
      if (!ta && !tb) continue;
      const ca = centre(pa, len.get(keys[a]));
      const cb = centre(pb, len.get(keys[b]));
      const d = Math.hypot(ca[0] - cb[0], ca[1] - cb[1]);
      if (ta && tb && d < 40) {
        close++;
        const id = `${keys[a]}~${keys[b]}`;
        if (!pairs.has(id)) pairs.set(id, { t: w0 + i * dt, d, pa: pa.phase, pb: pb.phase, n: 0 });
        const e = pairs.get(id);
        e.n++;
        e.d = Math.min(e.d, d);
      } else if (!(ta && tb) && d < 25) {
        parkedClose++;
        const id = `${keys[a]}~${keys[b]}`;
        if (!nearParked.has(id)) nearParked.set(id, `${id} at ${hhmm(w0 + i * dt)} ${pa.phase}/${pb.phase} ${d.toFixed(0)} m`);
      }
    }
  }
}
console.log(`taxiing pairs closer than 40 m (centre to centre): ${pairs.size} pairs, ${close} samples of 5 s`);
for (const [id, e] of pairs) console.log(`  ${id} from ${hhmm(e.t)} for ${e.n} samples, min ${e.d.toFixed(0)} m (${e.pa}/${e.pb})`);
console.log(`taxiing aircraft within 25 m of a parked aircraft centre: ${parkedClose} samples${nearParked.size ? ": " + [...nearParked.values()].join("; ") : ""}`);

let jumps = 0;
let headJumps = 0;
for (const [key, tr] of tracks) {
  let prev = null;
  for (let t = w0; t <= w1; t += 1 / 60) {
    const p = poseAt(tr, t);
    if (prev && prev.phase !== "absent" && p.phase !== "absent") {
      const d = Math.hypot(p.x - prev.x, p.y - prev.y);
      const lim = Math.max(p.speed, prev.speed) * 1.05 + 1.5;
      if (d > lim) {
        jumps++;
        if (jumps <= 5) console.log(`  jump ${key} ${hhmm(t)} ${prev.phase}->${p.phase} ${d.toFixed(1)} m in 1 s`);
      }
      const dh = Math.abs(((p.heading - prev.heading + 540) % 360) - 180);
      if (dh > 25 && (TAXI.has(p.phase) || TAXI.has(prev.phase))) {
        headJumps++;
        if (headJumps <= 5) console.log(`  heading jump ${key} ${hhmm(t)} ${prev.phase}->${p.phase} ${dh.toFixed(0)} deg in 1 s`);
      }
    }
    prev = p;
  }
}
check(jumps === 0, `${jumps} position jumps`);
console.log(`position jumps: ${jumps} · heading jumps > 25 deg/s on the ground: ${headJumps}`);

const arrThr = thr.get(moment.runways_in_use.arrivals);
const depRw = stage.runways.find((r) => r.ends.some((e) => e.id === moment.runways_in_use.departures));
const arrRw = stage.runways.find((r) => r.ends.some((e) => e.id === moment.runways_in_use.arrivals));
const inter = intersect(depRw.ends[0].xy, depRw.ends[1].xy, arrRw.ends[0].xy, arrRw.ends[1].xy);
if (inter && depRw !== arrRw) {
  const cross = (phases) => {
    const out = [];
    for (const [key, tr] of tracks) {
      let inside = false;
      for (let t = w0; t <= w1; t += 1 / 60) {
        const p = poseAt(tr, t);
        const near = phases.has(p.phase) && Math.hypot(p.x - inter[0], p.y - inter[1]) < 60;
        if (near && !inside) out.push({ key, t });
        inside = near;
      }
    }
    return out;
  };
  const a = cross(new Set(["rollout", "flare"]));
  const d = cross(new Set(["roll", "climb"]));
  let n = 0;
  for (const x of a) for (const y of d) if (Math.abs(x.t - y.t) < 0.5) n++;
  console.log(`runway ${depRw.ref} x ${arrRw.ref} intersection: ${a.length} arrival and ${d.length} departure passes, ${n} within 30 s of each other`);
}

const feat = [...tracks.values()].find((t) => t.featured);
if (feat) {
  const leg = feat.legs.find((l) => l.flight === moment.featured.flight) || {};
  console.log(`featured ${feat.tail} ${leg.flight} from ${feat.stand}: out ${hhmm(leg.out)} off ${hhmm(leg.off)}`);
  let last = null;
  let maxV = 0;
  for (let t = w0; t <= w1; t += 1 / 60) {
    const p = poseAt(feat, t);
    if (p.phase !== last) {
      console.log(`  ${hhmm(t)}  ${p.phase.padEnd(9)} x ${p.x.toFixed(0).padStart(6)} y ${p.y.toFixed(0).padStart(6)} hdg ${p.heading.toFixed(0).padStart(3)} alt ${p.alt.toFixed(0).padStart(4)} ${(p.speed / KT).toFixed(0).padStart(3)} kt  ${Object.entries(p.lights).filter(([, v]) => v).map(([k]) => k).join(",")}`);
      last = p.phase;
    }
    if (TAXI.has(p.phase)) maxV = Math.max(maxV, p.speed / KT);
  }
  console.log(`  max taxi speed ${maxV.toFixed(1)} kt`);
}

const out = process.env.MOTION_OUT || join(tmpdir(), "airops-motion");
mkdirSync(out, { recursive: true });
writeFileSync(join(out, "paths.svg"), svg(stage, tracks));
console.log(`svg: ${join(out, "paths.svg")}`);

if (fails.length) {
  console.log(`FAIL (${fails.length})`);
  for (const f of fails.slice(0, 40)) console.log(`  ${f}`);
  process.exit(1);
}
console.log("PASS");

function intersect(a, b, c, d) {
  const r = [b[0] - a[0], b[1] - a[1]];
  const s = [d[0] - c[0], d[1] - c[1]];
  const den = r[0] * s[1] - r[1] * s[0];
  if (Math.abs(den) < 1e-9) return null;
  const t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den;
  const u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den;
  return t >= 0 && t <= 1 && u >= 0 && u <= 1 ? [a[0] + t * r[0], a[1] + t * r[1]] : null;
}

function svg(stage, tracks) {
  const xs = stage.taxi_graph.nodes.map((p) => p[0]);
  const ys = stage.taxi_graph.nodes.map((p) => p[1]);
  const pad = 150;
  const [x0, x1, y0, y1] = [Math.min(...xs) - pad, Math.max(...xs) + pad, Math.min(...ys) - pad, Math.max(...ys) + pad];
  const P = (p) => `${p[0].toFixed(1)},${(-p[1]).toFixed(1)}`;
  const poly = (pts, attr) => `<polygon points="${pts.map(P).join(" ")}" ${attr}/>`;
  const line = (pts, attr) => `<polyline points="${pts.map(P).join(" ")}" fill="none" ${attr}/>`;
  const dot = (p, r, attr) => (p ? `<circle cx="${p.x.toFixed(1)}" cy="${(-p.y).toFixed(1)}" r="${r}" ${attr}/>` : "");
  const parts = [`<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x0} ${-y1} ${x1 - x0} ${y1 - y0}" width="${Math.round((x1 - x0) / 2)}" height="${Math.round((y1 - y0) / 2)}">`, `<rect x="${x0}" y="${-y1}" width="${x1 - x0}" height="${y1 - y0}" fill="#cfe0ea"/>`];
  for (const l of stage.land) parts.push(poly(l.outer, `fill="#e9e6dc"`));
  for (const a of stage.aprons) parts.push(poly(a.outer, `fill="#c9c9c9"`));
  for (const r of stage.runways) parts.push(poly(r.polygon, `fill="#8a8a8a"`));
  for (const t of stage.taxiways) parts.push(line(t.line, `stroke="${t.kind === "taxiway" ? "#b0b0b0" : "#bdbdbd"}" stroke-width="${t.kind === "taxiway" ? 18 : 8}"`));
  for (const t of stage.terminals) parts.push(poly(t.outer, `fill="#6d6a64"`));
  for (const s of stage.stands) parts.push(`<circle cx="${s.xy[0]}" cy="${-s.xy[1]}" r="4" fill="#444"/>`);
  for (const r of stage.runways) for (const e of r.ends) parts.push(`<text x="${e.xy[0]}" y="${-e.xy[1]}" font-size="60" font-family="sans-serif" fill="#000">${e.id}</text>`);
  for (const tr of tracks.values()) {
    for (const p of tr.paths) {
      const col = p.kind === "push" ? "#ff9800" : p.kind === "taxi" ? "#d7301f" : "#1f62d7";
      parts.push(line(p.pts, `stroke="${tr.featured ? "#ffd400" : col}" stroke-width="${tr.featured ? 9 : 3}" stroke-opacity="0.8"`));
      parts.push(dot(p.hold, 9, `fill="#000"`));
      parts.push(dot(p.gate, 9, `fill="#0a2a8a" stroke="#fff" stroke-width="2"`));
      for (const s of p.stops || []) parts.push(dot(s, 6, `fill="#9c27b0"`));
    }
  }
  parts.push(`<text x="${x0 + 40}" y="${-y1 + 90}" font-size="70" font-family="sans-serif">red: taxi out · orange: pushback · blue: taxi in · yellow: featured · black: runway hold · navy: gate hold · purple: give-way stops</text>`);
  parts.push("</svg>");
  return parts.join("\n");
}
