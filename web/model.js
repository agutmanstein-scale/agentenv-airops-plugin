// The episode as the viewer reads it at a minute t: what had flown, what the planner had decided and planned by then,
// what it knew (caps, news), and the running counters. Pure functions over the prepared episode, no DOM.

export const LATE = 15;
export const STATUS = {
  ontime: { label: "on time", rgb: [95, 191, 127] },
  delayed: { label: "delayed", rgb: [232, 176, 74] },
  held: { label: "held", rgb: [157, 140, 240] },
  cancelled: { label: "cancelled", rgb: [229, 96, 90] },
  auto: { label: "auto-cancelled", rgb: [236, 121, 185] },
  scheduled: { label: "scheduled", rgb: [125, 138, 153] },
  morning: { label: "flown before the first watch", rgb: [88, 96, 108] },
  ferry: { label: "ferry", rgb: [106, 167, 216] },
};
export const css = (key, a = 1) => `rgba(${STATUS[key].rgb.join(",")},${a})`;

const pad = (n) => String(n).padStart(2, "0");

export function hhmm(m) {
  const d = Math.floor(m / 1440), r = ((Math.round(m) % 1440) + 1440) % 1440;
  return `${pad(Math.floor(r / 60))}:${pad(r % 60)}${d > 0 ? `+${d}` : d < 0 ? `${d}` : ""}`;
}

export function utcAt(task, m) {
  const [y, mo, d] = task.date.split("-").map(Number);
  const guess = Date.UTC(y, mo - 1, d) + m * 60e3;
  return guess - offsetMinutes(task.tz, guess) * 60e3;
}

function offsetMinutes(tz, utcMs) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric",
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).formatToParts(new Date(utcMs)).map((p) => [p.type, p.value]));
  const local = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute);
  return Math.round((local - utcMs) / 60e3);
}

export function zoneName(task, m) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: task.tz, timeZoneName: "short" }).formatToParts(new Date(utcAt(task, m)));
  return parts.find((p) => p.type === "timeZoneName")?.value ?? "";
}

export function dayLabel(task, m = 0) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: task.tz, weekday: "short", day: "numeric", month: "short", year: "numeric" })
    .format(new Date(utcAt(task, m)));
}

export const fmt = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-US"));
export const short = (n) => (n == null ? "–" : Math.abs(n) >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : Math.abs(n) >= 1e4 ? `${(n / 1e3).toFixed(0)}k` : fmt(n));
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const rad = (d) => (d * Math.PI) / 180, deg = (r) => (r * 180) / Math.PI;

export function gcPath(a, b, n = 48) {
  const p1 = [rad(a.lat), rad(a.lon)], p2 = [rad(b.lat), rad(b.lon)];
  const h = Math.sin((p2[0] - p1[0]) / 2) ** 2 + Math.cos(p1[0]) * Math.cos(p2[0]) * Math.sin((p2[1] - p1[1]) / 2) ** 2;
  const d = 2 * Math.asin(Math.sqrt(h));
  if (d < 1e-9) return [[a.lon, a.lat], [b.lon, b.lat]];
  const out = [];
  for (let i = 0; i <= n; i++) {
    const f = i / n, A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(p1[0]) * Math.cos(p1[1]) + B * Math.cos(p2[0]) * Math.cos(p2[1]);
    const y = A * Math.cos(p1[0]) * Math.sin(p1[1]) + B * Math.cos(p2[0]) * Math.sin(p2[1]);
    const z = A * Math.sin(p1[0]) + B * Math.sin(p2[0]);
    out.push([deg(Math.atan2(y, x)), deg(Math.atan2(z, Math.hypot(x, y)))]);
  }
  return out;
}

export function miles(a, b) {
  if (!a || !b) return 300;
  const c = Math.sin(rad(a.lat)) * Math.sin(rad(b.lat)) + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.cos(rad(b.lon - a.lon));
  return 3958.8 * Math.acos(Math.max(-1, Math.min(1, c)));
}

export function bearing([lon1, lat1], [lon2, lat2]) {
  const y = Math.sin(rad(lon2 - lon1)) * Math.cos(rad(lat2));
  const x = Math.cos(rad(lat1)) * Math.sin(rad(lat2)) - Math.sin(rad(lat1)) * Math.cos(rad(lat2)) * Math.cos(rad(lon2 - lon1));
  return (deg(Math.atan2(y, x)) + 360) % 360;
}

/** Index the episode once: flights by id, the projections by watch, rows of the tail chart, the time range. */
export function prepare(ep, base) {
  const task = ep.task;
  const sched = new Map(ep.schedule.map((s) => [s.id, s]));
  const exec = new Map(ep.executed.map((e) => [e.id, e]));
  const watches = [...ep.watches].sort((a, b) => a.t - b.t);
  const projected = watches.map((w) => (w.projected ? new Map(w.projected.map((r) => [r.id, r])) : null));
  const news = (ep.news ?? watches.flatMap((w) => w.news)).map((n, i) => ({ ...n, id: n.id ?? `n${i}` })).sort((a, b) => a.at - b.at);
  const steps = ep.steps.map((s, k) => ({ ...s, k }));
  const frozen = (ep.frozen ?? []).map((f, i) => ({ ...f, id: `m${i}` }));
  const paths = new Map();
  const path = (o, d) => {
    const key = `${o}-${d}`;
    if (!paths.has(key)) paths.set(key, task.airports[o] && task.airports[d] ? gcPath(task.airports[o], task.airports[d]) : null);
    return paths.get(key);
  };
  for (const s of ep.schedule) s._path = path(s.orig, s.dest);
  for (const f of frozen) f._path = path(f.orig, f.dest);
  const arrs = [...ep.executed.map((e) => e.arr ?? 0), ...ep.schedule.map((s) => s.sched_arr)];
  const lastWatch = watches.length ? watches[watches.length - 1].t : task.start;
  const t0 = Math.min(task.start - 60, ...frozen.map((f) => f.dep));
  const end = Math.min(Math.max(lastWatch, ...ep.executed.filter((e) => e.arr != null).map((e) => e.arr)) + 20, lastWatch + 180);
  const view = { lo: Math.max(t0, task.start - 300), hi: end };
  const P = { ep, task, base, sched, exec, watches, projected, news, steps, frozen, lastWatch, t0: task.start - 60, end, view,
    maxArr: Math.max(...arrs) };
  P.tailRows = tailRows(P);
  P.lanes = cancelLanes(P);
  P.radar = ep.radar && ep.radar.frames?.length ? { ...ep.radar, frames: [...ep.radar.frames].sort((a, b) => a.t - b.t) } : null;
  return P;
}

function tailRows(P) {
  const first = new Map();
  const touch = (tail, t) => tail && first.set(tail, Math.min(first.get(tail) ?? Infinity, t));
  for (const e of P.ep.executed) touch(e.tail, e.dep ?? Infinity);
  for (const s of P.ep.schedule) touch(s.tail, s.sched_dep);
  for (const f of P.frozen) touch(f.tail, f.dep);
  for (const w of P.watches) for (const r of w.projected ?? []) touch(r.tail, r.dep ?? Infinity);
  const info = new Map(P.task.tails.map((t) => [t.tail, t]));
  const fam = (tail) => info.get(tail)?.family ?? P.ep.schedule.find((s) => s.tail === tail)?.family ?? "?";
  const order = ["A32X", "A220", "E190"];
  return [...first.keys()].sort((a, b) => (order.indexOf(fam(a)) - order.indexOf(fam(b))) || fam(a).localeCompare(fam(b))
    || first.get(a) - first.get(b) || a.localeCompare(b)).map((tail) => ({ tail, family: fam(tail), seats: info.get(tail)?.seats }));
}

/** Cancelled flights fly on no aircraft: they get lanes of their own, packed by scheduled time. */
function cancelLanes(P) {
  const ids = [...P.exec.values()].filter((e) => e.status !== "flown").map((e) => e.id);
  for (const w of P.watches) for (const r of w.projected ?? []) if (r.status !== "flown") ids.push(r.id);
  const uniq = [...new Set(ids)].map((id) => P.sched.get(id)).sort((a, b) => a.sched_dep - b.sched_dep);
  const ends = [], lane = new Map();
  for (const s of uniq) {
    let k = ends.findIndex((e) => e <= s.sched_dep - 5);
    if (k < 0) k = ends.push(0) - 1;
    ends[k] = s.sched_arr;
    lane.set(s.id, k);
  }
  return { count: ends.length, of: lane };
}

export function watchAt(P, t) {
  let k = -1;
  for (let i = 0; i < P.watches.length; i++) if (P.watches[i].t <= t) k = i;
  return k;
}

/** Every flight at minute t: flown legs and decided cancellations from the outcome, the rest as the planner's latest
 * plan stood on the day as known (or the schedule before the first watch). */
export function flightsAt(P, t) {
  const w = watchAt(P, t);
  const proj = w >= 0 ? P.projected[w] : null;
  const out = [];
  for (const s of P.ep.schedule) {
    const ex = P.exec.get(s.id);
    let r, phase;
    if (ex && ex.status === "flown" && ex.dep <= t) {
      r = ex;
      phase = ex.arr <= t ? "landed" : "air";
    } else if (ex && ex.status !== "flown" && ex.decided != null && ex.decided <= t) {
      r = ex;
      phase = "cancelled";
    } else if (t >= P.lastWatch && ex) {
      r = ex;
      phase = ex.status === "flown" ? "planned" : "cancelled";
    } else if (proj && proj.get(s.id)) {
      r = proj.get(s.id);
      phase = r.status === "flown" ? "planned" : "cancelled";
    } else if (w >= 0 && ex) {
      r = ex;
      phase = ex.status === "flown" ? "planned" : "cancelled";
    } else {
      r = { id: s.id, status: "flown", tail: s.tail, dep: s.sched_dep, arr: s.sched_arr, arr_delay: 0, held: false };
      phase = "scheduled";
    }
    out.push({ s, r, phase, cls: classOf(r, phase) });
  }
  return out;
}

export function classOf(r, phase) {
  if (phase === "scheduled") return "scheduled";
  if (r.status === "cancelled") return "cancelled";
  if (r.status === "auto_cancelled") return "auto";
  if (r.held) return "held";
  return (r.arr_delay ?? 0) >= LATE ? "delayed" : "ontime";
}

/** Caps as the planner knew them at t: the latest watch's view, the truth once the day is played out. */
export function capsAt(P, t) {
  if (t >= P.lastWatch) return P.ep.caps;
  const w = watchAt(P, t);
  return w >= 0 && P.watches[w].caps_known ? P.watches[w].caps_known : P.ep.nominal ?? {};
}

export function hubState(P, t) {
  const caps = capsAt(P, t), hub = P.task.hub, h = String(Math.floor(t / 60));
  const nominal = P.ep.nominal ?? {};
  const one = (kind) => {
    const cap = caps[`${hub}:${kind}`]?.[h], nom = nominal[`${hub}:${kind}`]?.[h];
    return { cap: cap ?? null, nominal: nom ?? null, stop: cap === 0, tight: cap != null && nom != null && cap < nom };
  };
  return { arr: one("arr"), dep: one("dep") };
}

/** Running counters at t, from the outcome up to t: delay accrues as the clock passes each scheduled arrival. */
export function countersAt(P, t, flights) {
  let cancelled = 0, auto = 0, paxDelay = 0, disrupted = 0, cancelPax = 0;
  for (const e of P.ep.executed) {
    const s = P.sched.get(e.id);
    if (e.status !== "flown") {
      if (e.decided != null && e.decided <= t) {
        e.status === "cancelled" ? cancelled++ : auto++;
        disrupted += s.pax;
        cancelPax += s.pax;
      }
      continue;
    }
    const late = Math.max(0, Math.min(t, e.arr) - s.sched_arr);
    paxDelay += s.pax * late;
    if (late >= LATE) disrupted += s.pax;
  }
  const held = flights.filter((f) => f.r.held && f.r.dep > t && f.phase !== "cancelled").length;
  return { cancelled, auto, paxDelay, disrupted, held, costSoFar: paxDelay + 523 * cancelPax };
}

export function newsAt(P, t) {
  return P.news.filter((n) => n.at <= t);
}

export function stepAt(P, t) {
  let k = -1;
  for (const s of P.steps) if (s.t <= t && (s.tool !== "advance" || k < 0 || P.steps[k].t !== s.t)) k = s.k;
  return k;
}

/** Where each tail is at t: at a station between legs, or in the air along a leg. */
export function positionsAt(P, flights, t) {
  const air = flights.filter((f) => f.phase === "air" && f.s._path);
  return air.map((f) => {
    const p = f.s._path, x = Math.min(0.9999, Math.max(0, (t - f.r.dep) / Math.max(1, f.r.arr - f.r.dep))) * (p.length - 1);
    const i = Math.floor(x), a = p[i], b = p[Math.min(p.length - 1, i + 1)], u = x - i;
    return { f, pos: [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u], heading: bearing(a, b), trail: [...p.slice(0, i + 1), [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u]] };
  });
}

/** Ground visits at the hub from the outcome and the morning's legs: [{tail, from, to, next}] by arrival. */
export function hubVisits(P) {
  const hub = P.task.hub, legs = new Map(), fam = new Map(P.tailRows.map((r) => [r.tail, r.family]));
  const add = (tail, leg) => tail && (legs.get(tail) ?? legs.set(tail, []).get(tail)).push(leg);
  for (const f of P.frozen) add(f.tail, { orig: f.orig, dest: f.dest, dep: f.dep, arr: f.arr, id: null });
  for (const e of P.ep.executed) if (e.status === "flown") add(e.tail, { ...P.sched.get(e.id), dep: e.dep, arr: e.arr, id: e.id });
  const visits = [];
  for (const t of P.task.tails) if (t.pos === hub && !(legs.get(t.tail) ?? []).some((l) => l.dep < P.task.start)) add(t.tail, { orig: "", dest: hub, dep: -Infinity, arr: -Infinity, id: null, start: true });
  for (const [tail, ls] of legs) {
    ls.sort((a, b) => a.dep - b.dep);
    for (let i = 0; i < ls.length; i++) {
      if (ls[i].dest !== hub) continue;
      const next = ls.slice(i + 1).find((l) => l.orig === hub);
      visits.push({ tail, from: ls[i].arr, to: next ? next.dep : Infinity, next: next?.id ?? null, family: fam.get(tail) });
    }
  }
  return visits.sort((a, b) => a.from - b.from);
}
