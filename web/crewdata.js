// The crews of a control-center day (episode.occ) at a minute t: who rests in a hotel away from base, the reserves still
// on call by base, the crews riding as passengers (deadheading) and the duties under way against their legal limits.
// Pure functions over the episode, no DOM.

export const CREW_INK = "#c99bff";
export const BASES = ["BOS", "JFK", "EWR", "FLL", "MCO", "LAX", "SJU"];

/** The crew data of a control-center episode, indexed once, or null for a v1 episode. */
export function crewIndex(P) {
  const occ = P.ep.occ;
  if (!occ?.units) return null;
  const dhByFlight = new Map(), legsOf = new Map();
  for (const d of occ.deadheads ?? []) dhByFlight.set(d.flight, [...(dhByFlight.get(d.flight) ?? []), d]);
  for (const [id, u] of Object.entries(occ.units)) {
    const legs = u.duties.flatMap((d) => [...d.legs.map((l) => ({ flight: l[0], dep: l[1], arr: l[2], dh: false })),
      ...d.deadheads.map((l) => ({ flight: l[0], dep: l[1], arr: l[2], dh: true }))]);
    legsOf.set(id, legs.sort((a, b) => a.dep - b.dep));
  }
  return { occ, units: occ.units, hotels: occ.hotels ?? [], reserves: occ.reserves ?? [], dhByFlight, legsOf };
}

/** Crews resting in a hotel away from their base at t, by station: Map station -> [{unit, to, size}]. */
export function strandedAt(C, t) {
  const out = new Map();
  for (const h of C.hotels) {
    const u = C.units[h.unit];
    if (!(h.from <= t && t < h.to) || !u || u.home === h.station) continue;
    out.set(h.station, [...(out.get(h.station) ?? []), { unit: h.unit, to: h.to, size: u.size }]);
  }
  return out;
}

/** Reserves not yet taken at t, by base: Map base -> {pilot: [r], cabin: [r]}. */
export function reservesAt(C, t) {
  const out = new Map();
  for (const r of C.reserves) {
    if (r.used != null && r.used <= t) continue;
    const b = out.get(r.base) ?? { pilot: [], cabin: [] };
    b[r.kind === "pilot" ? "pilot" : "cabin"].push(r);
    out.set(r.base, b);
  }
  return out;
}

/** The crews riding a flight as passengers. */
export const deadheadsOn = (C, flight) => C.dhByFlight.get(flight) ?? [];

/** The duty a crew is on at t, its end (release, or the limit when it flies nothing more) and the minutes to its limit. */
export function dutiesAt(C, t) {
  const out = [];
  for (const [id, u] of Object.entries(C.units)) {
    for (const d of u.duties) {
      const end = d.release ?? d.limit;
      if (d.report <= t && t < end) out.push({ id, u, d, end, left: d.limit - t });
    }
  }
  return out.sort((a, b) => a.left - b.left || a.id.localeCompare(b.id));
}

/** Where a crew is at t on a duty: in the air on a leg, else the station of its last arrival, else where it reported. */
export function whereAt(P, d, t) {
  const legs = [...d.legs.map((l) => [...l, false]), ...d.deadheads.map((l) => [...l, true])].sort((a, b) => a[1] - b[1]);
  let at = d.at;
  for (const [f, dep, arr, dh] of legs) {
    const s = P.sched.get(f);
    if (dep <= t && t < arr) return { air: true, flight: s?.flight ?? `f${f}`, dh, to: s?.dest };
    if (arr <= t && s) at = s.dest;
  }
  return { air: false, at };
}
