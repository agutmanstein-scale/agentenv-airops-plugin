// The passengers of a v3 day (episode.passengers) at a minute t: how many have missed a connection, been rebooked
// onto a later flight or been stranded overnight, and where, the departures held for them and the customer desk's
// requests to ops. Pure functions over the episode, no DOM.

export const PAX_INK = "#e9c27a";
const RISE = 8;
const LINGER = 45;
const FADE = 25;

/** The passenger data of an episode, indexed once, or null for one without passengers. */
export function paxIndex(P) {
  const pax = P.ep.passengers;
  if (!pax) return null;
  const events = [...pax.events].sort((a, b) => a.t - b.t);
  const flights = new Map(pax.flights.map((r) => [r.id, r]));
  const asks = (P.ep.occ?.requests ?? []).filter((r) => r.sender === "customer" && r.type !== "TEXT");
  const holds = asks.filter((r) => r.type === "HOLD");
  return { pax, events, flights, holds, asks, end: P.hi ?? P.end };
}

/** The day's passengers up to t: missed connections, rebooked, at hotels overnight, gone home, and the departures the
 * ops desk held for connecting passengers (with how many). */
export function paxAt(X, t) {
  const n = { misconnect: 0, rebook: 0, hotel: 0, home: 0 };
  for (const e of X.events) {
    if (e.t > t) break;
    n[e.kind] += e.pax;
  }
  const held = X.holds.filter((r) => r.reply === "accept" && (r.answered ?? r.replied) <= t);
  return { missed: n.misconnect, rebooked: n.rebook, stranded: n.hotel, home: n.home, held: held.length, heldPax: held.reduce((a, r) => a + (r.pax ?? 0), 0) };
}

/** Passengers at hotels at t, by station: Map station -> passengers, from each hotel booking until they leave. */
export function strandedPaxAt(X, t) {
  const out = new Map();
  for (const e of X.events) {
    if (e.t > t) break;
    if (e.kind !== "hotel" || (e.until != null && e.until <= t)) continue;
    out.set(e.at, (out.get(e.at) ?? 0) + e.pax);
  }
  return out;
}

/** The flights whose passengers had their day changed, by scheduled departure: each with its row and its events. */
export function disrupted(X, P) {
  const by = new Map();
  for (const e of X.events) by.set(e.flight, [...(by.get(e.flight) ?? []), e]);
  return [...by.entries()].map(([id, evs]) => ({ s: P.sched.get(id), r: X.flights.get(id), evs }))
    .filter((x) => x.s).sort((a, b) => a.s.sched_dep - b.s.sched_dep || a.s.flight.localeCompare(b.s.flight));
}

const departs = (P, id) => {
  const x = P.exec?.get(id);
  return x?.status === "flown" && x.dep != null ? x.dep : P.sched.get(id)?.sched_dep ?? null;
};

/** The passengers rebooked by t onto a later flight today and still waiting for it, by that flight, the soonest first:
 * [{to, at, pax, dep}]. */
export function waitingAt(X, P, t) {
  const by = new Map();
  for (const e of X.events) {
    if (e.t > t) break;
    if (e.kind !== "rebook" || !P.sched.has(e.to)) continue;
    const dep = departs(P, e.to);
    if (dep != null && dep <= t) continue;
    const w = by.get(e.to) ?? { to: e.to, at: e.at, pax: 0, dep };
    w.pax += e.pax;
    by.set(e.to, w);
  }
  return [...by.values()].sort((a, b) => (a.dep ?? Infinity) - (b.dep ?? Infinity) || a.to - b.to);
}

/** Where the passengers off a cancelled flight or a missed connection were rebooked, as flows from their station to
 * the new flight's destination: each rebooking shows from when it is made until its new flight has been gone a while,
 * easing in and out; [{at, dest, pax, alpha}] by station and destination. */
export function flowsAt(X, P, t) {
  const by = new Map();
  for (const e of X.events) {
    if (e.t > t) break;
    const s = e.kind === "rebook" ? P.sched.get(e.to) : null;
    if (!s) continue;
    const end = Math.max(departs(P, e.to) ?? e.t, e.t) + LINGER;
    if (t >= end) continue;
    const k = `${e.at}-${s.dest}`, f = by.get(k) ?? { at: e.at, dest: s.dest, pax: 0, alpha: 0 };
    f.pax += e.pax;
    f.alpha = Math.max(f.alpha, Math.min(1, (t - e.t) / RISE, (end - t) / FADE));
    by.set(k, f);
  }
  return [...by.values()].filter((f) => f.at !== f.dest);
}

/** The customer desk's requests to ops by t: sent, still waiting for an answer, and accepted. */
export function asksAt(X, t) {
  const sent = X.asks.filter((r) => (r.shown ?? r.sent) <= t), answered = sent.filter((r) => (r.answered ?? r.replied ?? Infinity) <= t);
  return { sent: sent.length, open: sent.length - answered.length, accepted: answered.filter((r) => r.reply === "accept").length };
}

/** The customer desk's connections at risk as its page lists them: each departure with its connecting passengers, how
 * many miss it as projected and how many of those have no later seat today; and how many more the page left out. */
export function atRisk(text) {
  const rows = [];
  let on = false, more = 0;
  for (const line of String(text ?? "").split("\n").map((l) => l.trim())) {
    if (line.startsWith("## ")) {
      on = /^## Connections at risk/i.test(line);
      continue;
    }
    if (!on) continue;
    const m = /^f(\d+)\s+\S+\s+([A-Z]{3})-([A-Z]{3})\s+(\d{1,2}:\d\d)(?:\s+committed)?:\s*(.*)$/.exec(line);
    if (!m) {
      more += Number(/^(\d+) more\b/.exec(line)?.[1] ?? 0);
      continue;
    }
    const [conns, tail = ""] = m[5].split(";");
    const pax = conns.split(",").reduce((a, c) => a + Number(/(\d+)\s*$/.exec(c.trim())?.[1] ?? 0), 0);
    rows.push({ id: Number(m[1]), orig: m[2], dest: m[3], dep: m[4], pax, misses: Number(/misses (\d+)/.exec(tail)?.[1] ?? 0),
      noSeat: Number(/(\d+) have no later seat/.exec(tail)?.[1] ?? 0) });
  }
  return { rows, more };
}
