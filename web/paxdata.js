// The passengers of a v3 day (episode.passengers) at a minute t: how many have missed a connection, been rebooked
// onto a later flight or been put up in a hotel overnight, and where, and the departures held for them. Pure functions
// over the episode, no DOM.

export const PAX_INK = "#e9c27a";

/** The passenger data of an episode, indexed once, or null for one without passengers. */
export function paxIndex(P) {
  const pax = P.ep.passengers;
  if (!pax) return null;
  const events = [...pax.events].sort((a, b) => a.t - b.t);
  const flights = new Map(pax.flights.map((r) => [r.id, r]));
  const holds = (P.ep.occ?.requests ?? []).filter((r) => r.sender === "customer" && r.type === "HOLD");
  return { pax, events, flights, holds, end: P.hi ?? P.end };
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
