// The tail chart (expert mode): one row per aircraft over the day. The schedule as a dashed outline, the flown or planned
// leg as a bar coloured by status (with its label when it fits), the delay as a tail back to the scheduled departure,
// holds as a blue tick, cancellations hatched in lanes of their own, the morning's legs in grey, the end-of-day ferries,
// the now line with the freeze zone, and above the rows the hub's arrival and departure slots by hour against its caps.
import { STATUS, capsAt, css, esc, hhmm, miles } from "./model.js";

const NS = "http://www.w3.org/2000/svg";
const LEFT = 104, TOP = 22, BAND = 15, RIGHT = 10;

export class TailChart {
  constructor(el, P, { onPick } = {}) {
    this.el = el;
    this.P = P;
    this.onPick = onPick;
    this.svg = document.createElementNS(NS, "svg");
    this.svg.setAttribute("class", "tails");
    this.svg.setAttribute("role", "img");
    this.svg.setAttribute("aria-label", "Tail chart: one row per aircraft, flights as bars coloured by status");
    el.appendChild(this.svg);
    this.range = { lo: Math.max(P.view.lo, P.task.start - 300), hi: P.view.hi };
  }

  render(t, flights, { changed = new Set(), focus = null } = {}) {
    const W = this.el.clientWidth, H = this.el.clientHeight;
    if (W < 50 || H < 50) return;
    this.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const P = this.P, { lo, hi } = this.range;
    const x = (m) => LEFT + ((m - lo) / (hi - lo)) * (W - LEFT - RIGHT);
    const rows = P.tailRows, lanes = Math.max(1, P.lanes.count);
    const bodyTop = TOP + 2 * BAND + 8;
    const rowH = Math.max(5, Math.min(18, (H - bodyTop - 16) / (rows.length + lanes + 1.2)));
    const rowY = new Map(rows.map((r, i) => [r.tail, bodyTop + i * rowH]));
    const laneTop = bodyTop + (rows.length + 0.7) * rowH;
    const bh = Math.max(3, rowH * 0.72), text = rowH >= 10.5;
    const out = [];
    const push = (s) => out.push(s);

    push(`<defs>
      <pattern id="hatch-c" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="${css("cancelled", 0.18)}"/><line x1="0" y1="0" x2="0" y2="5" stroke="${css("cancelled", 0.95)}" stroke-width="1.6"/></pattern>
      <pattern id="hatch-a" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><rect width="5" height="5" fill="${css("auto", 0.18)}"/><line x1="0" y1="0" x2="0" y2="5" stroke="${css("auto", 0.95)}" stroke-width="1.6"/></pattern>
      <pattern id="hatch-gs" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="4" height="4" fill="rgba(229,96,90,0.25)"/><line x1="0" y1="0" x2="0" y2="4" stroke="rgba(229,96,90,0.8)" stroke-width="1"/></pattern>
      <pattern id="hatch-f" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="${css("ferry", 0.15)}"/><line x1="0" y1="0" x2="0" y2="5" stroke="${css("ferry", 0.9)}" stroke-width="1.2"/></pattern>
      <clipPath id="plot"><rect x="${LEFT}" y="0" width="${W - LEFT - RIGHT}" height="${H}"/></clipPath>
    </defs>`);

    for (let h = Math.ceil(lo / 60); h * 60 <= hi; h++) {
      const X = x(h * 60), major = h % 2 === 0;
      push(`<line class="grid${major ? " major" : ""}" x1="${X}" x2="${X}" y1="${TOP - 4}" y2="${H - 6}"/>`);
      if (major && Math.abs(X - x(t)) > 44) push(`<text class="ax" x="${X + 3}" y="${TOP - 8}">${hhmm(h * 60)}</text>`);
    }
    push(`<rect class="window" x="${x(P.task.window[0])}" y="${bodyTop - 4}" width="${x(P.task.window[1]) - x(P.task.window[0])}" height="${H - bodyTop - 2}"/>`);
    push(`<text class="ax win" x="${x(P.task.window[0]) + 3}" y="${H - 4}">disruption window</text>`);
    push(this.slots(t, flights, x, lo, hi));

    rows.forEach((r, i) => {
      const y = bodyTop + i * rowH;
      if (i % 2 === 0) push(`<rect class="zebra" x="0" y="${y}" width="${W}" height="${rowH}"/>`);
      if (rowH >= 7) push(`<text class="lbl" x="6" y="${y + rowH * 0.78}" style="font-size:${Math.min(11, rowH * 0.95)}px">${esc(r.tail)} <tspan class="fam">${esc(r.family)}</tspan></text>`);
    });
    push(`<text class="lbl lane-h" x="6" y="${laneTop + rowH * 0.8}" style="font-size:${Math.min(11, rowH * 0.95)}px">cancelled</text>`);
    push(`<line class="sep" x1="0" x2="${W}" y1="${laneTop - rowH * 0.35}" y2="${laneTop - rowH * 0.35}"/>`);

    push(`<g clip-path="url(#plot)">`);
    for (const f of P.frozen) {
      const y = rowY.get(f.tail);
      if (y == null) continue;
      push(`<rect class="bar morning" x="${x(f.dep)}" y="${y + (rowH - bh) / 2}" width="${Math.max(1.5, x(f.arr) - x(f.dep) - 1)}" height="${bh}" rx="2"><title>${esc(f.flight)} ${esc(f.orig)}–${esc(f.dest)} flew ${hhmm(f.dep)}–${hhmm(f.arr)} (before the first watch)</title></rect>`);
    }
    for (const f of flights) {
      const { s, r } = f;
      const pick = changed.has(s.id) ? " changed" : "";
      if (f.cls === "cancelled" || f.cls === "auto") {
        const y = laneTop + (P.lanes.of.get(s.id) ?? 0) * rowH, fill = f.cls === "auto" ? "url(#hatch-a)" : "url(#hatch-c)";
        const decided = f.phase === "cancelled" && P.exec.get(s.id)?.decided != null && P.exec.get(s.id).decided <= t;
        push(`<rect class="bar cx${decided ? "" : " planned"}${pick}" data-id="${s.id}" x="${x(s.sched_dep)}" y="${y + (rowH - bh) / 2}" width="${Math.max(2, x(s.sched_arr) - x(s.sched_dep))}" height="${bh}" rx="2" fill="${fill}" stroke="${css(f.cls)}"><title>${esc(s.flight)} ${esc(s.orig)}–${esc(s.dest)} ${STATUS[f.cls].label}${decided ? "" : " (as planned)"} · ${s.pax} pax · sched ${hhmm(s.sched_dep)}</title></rect>`);
        if (text && x(s.sched_arr) - x(s.sched_dep) > 52) push(`<text class="bt cxt" x="${x(s.sched_dep) + 3}" y="${y + rowH * 0.72}">${esc(s.flight.replace(" ", ""))}</text>`);
        continue;
      }
      const y = rowY.get(r.tail ?? s.tail);
      if (y == null) continue;
      const by = y + (rowH - bh) / 2, X0 = x(r.dep), X1 = x(r.arr);
      if (Math.abs(r.dep - s.sched_dep) >= 5) {
        push(`<rect class="outline" x="${x(s.sched_dep)}" y="${by}" width="${Math.max(1, x(s.sched_arr) - x(s.sched_dep))}" height="${bh}" rx="2"/>`);
        push(`<line class="dtail" x1="${x(s.sched_dep)}" x2="${X0}" y1="${by + bh / 2}" y2="${by + bh / 2}" stroke="${css(f.cls === "held" ? "held" : "delayed", 0.85)}"/>`);
      }
      const op = f.phase === "landed" ? 0.92 : f.phase === "air" ? 1 : f.phase === "scheduled" ? 0.35 : 0.5;
      push(`<rect class="bar ${f.phase}${pick}" data-id="${s.id}" x="${X0}" y="${by}" width="${Math.max(2, X1 - X0 - 1)}" height="${bh}" rx="2" fill="${css(f.cls, op)}" stroke="${css(f.cls, f.phase === "planned" || f.phase === "scheduled" ? 0.95 : 0)}"><title>${esc(s.flight)} ${esc(s.orig)}–${esc(s.dest)} · ${STATUS[f.cls].label}${f.phase === "planned" ? " (as planned)" : ""} · dep ${hhmm(r.dep)} (sched ${hhmm(s.sched_dep)}) · arr ${hhmm(r.arr)}${r.arr_delay ? `, ${r.arr_delay} min late` : ""} · ${s.pax} pax</title></rect>`);
      if (r.held && r.not_before != null) push(`<path class="hold" d="M${x(r.not_before)} ${by - 1} l-3.5 -4.5 h7 z" fill="${css("held")}"/>`);
      if (text && X1 - X0 > 50) push(`<text class="bt" x="${X0 + 3}" y="${by + bh * 0.76}">${esc(s.orig)}–${esc(s.dest)}</text>`);
    }
    if (t >= P.lastWatch) {
      for (const fy of P.ep.ferries ?? []) {
        const y = rowY.get(fy.tail);
        if (y == null) continue;
        const last = Math.max(...P.ep.executed.filter((e) => e.tail === fy.tail && e.arr != null).map((e) => e.arr), P.task.start);
        const d0 = last + 45, d1 = d0 + 30 + miles(P.task.airports[fy.orig], P.task.airports[fy.dest]) / 8;
        push(`<rect class="bar ferry" x="${x(d0)}" y="${y + (rowH - bh) / 2}" width="${Math.max(2, x(d1) - x(d0))}" height="${bh}" rx="2" fill="url(#hatch-f)" stroke="${css("ferry")}"><title>Ferry ${esc(fy.orig)}–${esc(fy.dest)} after the day (${Math.round(fy.cost).toLocaleString()} pax-min)</title></rect>`);
        if (text && x(d1) - x(d0) > 50) push(`<text class="bt" x="${x(d0) + 3}" y="${y + rowH * 0.72}">ferry ${esc(fy.orig)}–${esc(fy.dest)}</text>`);
      }
    }
    const w = P.watches.findLast((v) => v.t <= t);
    if (w && t < P.lastWatch) push(`<rect class="freeze" x="${x(t)}" y="${bodyTop - 4}" width="${Math.max(0, x(t + 60) - x(t))}" height="${H - bodyTop - 2}"><title>Flights planned to leave before ${hhmm(t + 60)} are frozen at the next watch</title></rect>`);
    for (const v of P.watches) push(`<path class="wtick${v.t <= t ? " past" : ""}" d="M${x(v.t)} ${TOP - 4} l-3 -4 h6 z"/>`);
    push(`<line class="now" x1="${x(t)}" x2="${x(t)}" y1="${TOP - 4}" y2="${H - 2}"/>`);
    push(`</g>`);
    push(`<text class="nowt" x="${Math.min(x(t) + 4, W - 46)}" y="${TOP - 8}">${hhmm(t)}</text>`);
    this.svg.innerHTML = out.join("");
  }

  /** Two strips over the rows: the hub's arrivals and departures per hour, used against the cap the planner knew. */
  slots(t, flights, x, lo, hi) {
    const P = this.P, hub = P.task.hub, caps = capsAt(P, t), nominal = P.ep.nominal ?? {};
    const used = { arr: new Map(), dep: new Map() };
    for (const f of flights) {
      if (f.cls === "cancelled" || f.cls === "auto") continue;
      if (f.s.dest === hub) used.arr.set(Math.floor(f.r.arr / 60), (used.arr.get(Math.floor(f.r.arr / 60)) ?? 0) + 1);
      if (f.s.orig === hub) used.dep.set(Math.floor(f.r.dep / 60), (used.dep.get(Math.floor(f.r.dep / 60)) ?? 0) + 1);
    }
    const out = [];
    ["arr", "dep"].forEach((kind, k) => {
      const y = TOP + k * BAND;
      out.push(`<text class="lbl band-l" x="6" y="${y + BAND * 0.72}">${hub} ${kind === "arr" ? "arrivals" : "departures"}</text>`);
      for (let h = Math.floor(lo / 60); h * 60 < hi; h++) {
        const X0 = Math.max(LEFT, x(h * 60)), X1 = x((h + 1) * 60);
        if (X1 <= LEFT) continue;
        const cap = caps[`${hub}:${kind}`]?.[String(h)], nom = nominal[`${hub}:${kind}`]?.[String(h)], n = used[kind].get(h) ?? 0;
        let fill = "rgba(255,255,255,0.03)", cls = "free";
        if (cap === 0) (fill = "url(#hatch-gs)"), (cls = "stop");
        else if (cap != null && nom != null && cap < nom) (fill = `rgba(232,176,74,${0.15 + 0.5 * (1 - cap / nom)})`), (cls = "tight");
        out.push(`<rect class="slot ${cls}" x="${X0 + 0.5}" y="${y}" width="${Math.max(0, X1 - X0 - 1)}" height="${BAND - 2}" fill="${fill}"><title>${hub} ${kind} ${hhmm(h * 60)}–${hhmm(h * 60 + 60)}: ${n} planned${cap != null ? `, cap ${cap}` : ", no cap"}${nom != null ? `, schedule ${nom}` : ""}${cap === 0 ? " (stopped)" : ""}</title></rect>`);
        if (X1 - X0 > 26) out.push(`<text class="slot-t ${cls}${cap != null && n > cap ? " over" : ""}" x="${(X0 + X1) / 2}" y="${y + BAND * 0.68}">${cap === 0 ? `${n}/0 GS` : cap != null ? `${n}/${cap}` : n || ""}</text>`);
      }
    });
    return out.join("");
  }
}
