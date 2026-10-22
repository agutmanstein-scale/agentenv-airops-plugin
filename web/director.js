// The live director: cuts between the control-center room, the airport and the map on the day's events as the clock
// reaches them, as record.py's Center reads a day: the room when a desk asks, answers or decides, the gate of a flight it
// cancels, the terminal at a gate it holds, the tower as an FAA program starts; between them the airport's own
// movements (a landing, a takeoff, an aircraft followed, the terminal's busiest gate, a crew), the room while a desk
// works and now and then the map, each shot held between its least and most wall seconds. Airport shots slow the clock
// (record.py's FAST) and the room and the map let it catch up.
import { HUB_NAMES, cities, hhmm, programsAt } from "./model.js";
import { unitName } from "./room.js";
import { homeOf } from "./stage/cameras.js";

const DESK = { ops: "the ops controller", crew: "the crew scheduler", customer: "the customer desk", one: "the desk" };
const ASKS = { CANCEL: "to cancel", CANCEL_PAIR: "to cancel the round trip of", HOLD: "to hold", COVER: "to find a crew for",
  PROTECT: "not to cancel", SUGGEST_CANCEL: "to cancel early" };
const ASK_PRIO = { CANCEL: 82, CANCEL_PAIR: 82, HOLD: 80, SUGGEST_CANCEL: 74, PROTECT: 72, COVER: 66, TEXT: 34 };
const DECIDE_PRIO = { cancel: 78, cancel_pair: 78, hold: 70, rebook: 52, hotel: 52, assign: 50, call_reserve: 50, deadhead: 46, swap: 46,
  claim: 42, swap_slots: 40, release: 36, rest: 34 };
const CREW_MOVES = new Set(["assign", "call_reserve", "deadhead", "swap"]);
/** The clock's fastest rate for each camera, in minutes of the day a second (record.py FAST); the room and the map none. */
const SLOW = { landing: 0.1, takeoff: 0.1, gate: 0.12, terminal: 0.12, crew: 0.12, follow: 0.3, tower: 0.25, harbour: 0.3, threshold: 0.2 };
const LEN = { ask: [5, 12], answer: [4, 8], decide: [4, 8], cancelled: [4, 7], held: [5, 9], faa: [6, 10], landing: [5, 8],
  takeoff: [5, 8], follow: [6, 14], terminal: [6, 13], crew: [6, 11], tower: [5, 10], harbour: [5, 9], gate: [5, 9], map: [5, 8],
  work: [6, 12], room: [6, 12] };
const REPLY = 5.5;
const MAP_EVERY = 150;
const DAWN = -2;
const LAG = 20;

const upper = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const people = (n, what = "passengers") => `${n} ${n === 1 ? what.replace(/s$/, "") : what}`;

export class Director {
  /** `page` is app.js episodePage's live handle; `pacer` takes the slowest rate a shot wants (cap). */
  constructor(page, { pacer = null, caption = () => {}, log = false } = {}) {
    this.page = page;
    this.pacer = pacer;
    this.caption = caption;
    this.log = log;
    this.used = new Set();
    this.cur = null;
    this.lastMap = -Infinity;
    this.recent = [];
    this.shots = [];
    this.index();
  }

  /** The day's beats from what is decided so far, again after each update; those already cut stay used. */
  index() {
    const { P, ep } = this.page, occ = ep.occ ?? {}, hub = P.task.hub;
    const traffic = this.page.traffic();
    this.city = cities(P.task, traffic);
    this.where = HUB_NAMES[hub] ?? hub;
    this.legs = new Map();
    for (const a of traffic?.aircraft ?? []) {
      if (!a.task) continue;
      for (const v of a.visits) for (const g of [v.arr, v.dep]) if (g) this.legs.set(`${g.flight}|${g.kind}`, { a, v, g });
    }
    const beats = [];
    const one = occ.mode === "one";
    const desk = (d) => (one ? "the desk" : DESK[d] ?? d);
    for (const r of occ.requests ?? []) {
      if (r.shown == null) continue;
      const typed = r.type !== "TEXT", said = this.quote(r.reply_text);
      const ask = r.type === "TEXT" ? `${upper(desk(r.sender))} messages ${desk(r.receiver)}${this.quote(r.text) ? `: “${this.quote(r.text)}”` : ""}`
        : `${upper(desk(r.sender))} asks ${desk(r.receiver)} ${ASKS[r.type] ?? r.type.toLowerCase()} ${this.trip(r.flight)}${r.pax && r.type === "HOLD" ? ` for ${people(r.pax, "connecting passengers")}` : r.pax ? `, ${people(r.pax)} booked` : ""}`;
      const answer = r.answered == null ? "" : `${upper(desk(r.receiver))} ${r.reply === "accept" ? "accepts" : "declines"}${said ? `: “${said}”` : ""}`;
      const end = r.answered != null && r.answered - r.shown < 9 ? r.answered + REPLY : r.shown + 7;
      beats.push({ id: `ask:${r.id}`, kind: "ask", at: r.shown - 0.4, end, prio: ASK_PRIO[r.type] ?? 50, camera: "room-desks",
        spec: { desk: r.sender, ref: r.id, t0: r.shown - 0.5, t1: end }, kicker: `${hhmm(r.shown)} · The control center`, caption: ask,
        subline: (t) => (r.answered != null && t >= r.answered ? answer : ""), flight: r.flight });
      if (typed && r.answered != null && r.answered - r.shown >= 9) {
        beats.push({ id: `answer:${r.id}`, kind: "answer", at: r.answered - 0.4, end: r.answered + REPLY, prio: (ASK_PRIO[r.type] ?? 50) - 12, camera: "room-desks",
          spec: { desk: r.receiver, ref: r.id, t0: r.answered - 0.5, t1: r.answered + REPLY }, kicker: `${hhmm(r.answered)} · The control center`,
          caption: `${upper(desk(r.receiver))} ${r.reply === "accept" ? "accepts" : "declines"} ${r.id}: ${ASKS[r.type] ?? ""} ${this.trip(r.flight)}`.replace(/ {2}/g, " "),
          subline: () => (said ? `“${said}”` : "") });
      }
    }
    const groups = new Map();
    for (const d of occ.decisions ?? []) {
      if (d.shown == null || DECIDE_PRIO[d.action] == null) continue;
      const k = `${d.desk}|${Math.floor(d.shown / 1.5)}|${d.action}`;
      groups.set(k, [...(groups.get(k) ?? []), d]);
    }
    for (const ds of groups.values()) {
      const d = ds[0], at = d.shown - 0.3, asked = d.ref != null;
      const bulk = ds.length >= 3 && ["cancel", "cancel_pair"].includes(d.action);
      const beat = { id: `decide:${d.desk}:${d.shown}:${d.action}`, kind: "decide", at, end: at + 4, prio: DECIDE_PRIO[d.action] - (asked ? 40 : 0),
        camera: bulk ? "room-wall" : "room", spec: { desk: d.desk, t0: at, t1: at + 4 }, kicker: `${hhmm(d.shown)} · The control center`,
        caption: `${upper(desk(d.desk))} ${this.decision(ds)}`, subline: () => (asked ? `As ${d.ref} asked` : "") };
      beats.push(beat);
      const after = this.after(d, asked ? [beat.id, `ask:${d.ref}`, `answer:${d.ref}`] : [beat.id]);
      if (after) beats.push(after);
    }
    for (const n of P.news) {
      const data = n.data ?? {};
      if (n.kind !== "advisory" || data.element !== hub || !["gs", "gdp"].includes(data.kind)) continue;
      const period = data[data.kind === "gs" ? "gs_period" : "program_period"];
      if (!period) continue;
      const start = Math.max(n.at, period[0]), what = this.announce(data);
      beats.push({ id: `faa:${n.id}`, kind: "faa", at: start - 0.5, end: start + 3, prio: 90, camera: "tower", spec: {},
        kicker: `${hhmm(start)} · ${what[0]}`, caption: what[1], subline: () => (period[0] > n.at + 2 ? `Issued at ${hhmm(n.at)}` : "") });
      if (period[0] > n.at + 20) {
        beats.push({ id: `faa-news:${n.id}`, kind: "map", at: n.at, end: n.at + 6, prio: 64, camera: "network", spec: {},
          kicker: `${hhmm(n.at)} · The FAA`, caption: `From ${hhmm(period[0])}, ${what[1][0].toLowerCase()}${what[1].slice(1)}`, subline: () => "" });
      }
    }
    this.beats = beats.sort((a, b) => a.at - b.at);
    this.events = this.page.stage?.events?.() ?? [];
  }

  /** What a decision plays out as at the airport: the cancelled flight at its gate, the held one in the terminal. */
  after(d, parents) {
    const s = this.page.P.sched.get(d.flight);
    if (!s) return null;
    const hub = this.page.P.task.hub, kind = s.orig === hub ? "dep" : s.dest === hub ? "arr" : null;
    const leg = kind && this.legs.get(`${s.flight}|${kind}`);
    if (!leg) return null;
    const at = d.shown + 0.6;
    if (d.action === "cancel" || d.action === "cancel_pair") {
      return { id: `cancelled:${d.flight}:${d.shown}`, kind: "cancelled", after: parents, at, end: at + 2, prio: 74, camera: "gate", outdoor: true,
        spec: { key: leg.a.key }, key: leg.a.key, kicker: hhmm(at), caption: `${s.flight} ${kind === "dep" ? "to" : "from"} ${this.cityOf(kind === "dep" ? s.dest : s.orig)} stays at ${this.gate(leg.v.stand)}`,
        subline: () => `Cancelled by ${DESK[d.desk] ?? "the desk"}` };
    }
    if (d.action === "hold" && kind === "dep") {
      const lounge = this.page.stage?.gates?.()?.find((g) => g.stand === leg.v.stand);
      const p = this.page.traffic()?.passengers?.tags?.[s.flight];
      const sub = () => [d.detail ? `Held until ${d.detail}` : "Held", p?.conn ? `for ${people(p.conn, "connecting passengers")}` : ""].filter(Boolean).join(" ");
      return { id: `held:${d.flight}:${d.shown}`, kind: "held", after: parents, at, end: at + 3, prio: 72, camera: lounge ? "terminal" : "gate",
        outdoor: !lounge, spec: lounge ? { stand: leg.v.stand, flight: s.flight } : { key: leg.a.key }, kicker: hhmm(at),
        caption: `${s.flight} to ${this.cityOf(s.dest)} waits at ${this.gate(leg.v.stand)}`, subline: sub };
    }
    return null;
  }

  /** Called each frame with the day's clock: cuts when a beat comes due past the shot's least length, or the shot runs out. */
  tick(t, wall, speed) {
    if (this.busy) return;
    const cur = this.cur, held = cur ? wall - cur.start : Infinity;
    const lead = Math.max(0.25, speed * 1.2);
    const mine = (b) => !!cur && !!b.after?.includes(cur.beat.id);
    for (const b of this.beats) if (!this.used.has(b.id) && b.at < t - 3 && !mine(b)) this.used.add(b.id);
    const due = this.beats.filter((b) => !this.used.has(b.id) && (mine(b) || (b.at <= t + lead && b.at >= t - 3)) && this.ready(b, t, wall));
    const best = due.sort((a, b) => b.prio - a.prio || a.at - b.at)[0];
    if (cur) this.caption(cur.beat, t);
    const [lo, hi] = cur ? LEN[cur.beat.kind] ?? [5, 10] : [0, 0];
    const over = cur && (held >= hi || (cur.beat.end != null && t > cur.beat.end + 0.5 && held >= lo) || (cur.check && !cur.check(t)));
    if (best && (!cur || (held >= lo && (best.prio > cur.beat.prio || mine(best) || over)))) return this.cut(best, t, wall);
    if (!cur || over) return this.cut(this.filler(t, wall, speed), t, wall);
  }

  /** A follow-up comes once its decision is made, after the shot of it (or of the request it answers) has run its
   * least length; an outdoor one only in light. */
  ready(b, t, wall) {
    if (b.outdoor && !this.light(t)) return false;
    if (!b.after) return true;
    if (b.at > t + 0.2) return false;
    return !b.after.includes(this.cur?.beat.id) || wall - this.cur.start >= (LEN[this.cur.beat.kind]?.[0] ?? 4);
  }

  light(t) {
    const env = this.page.stage?.env;
    return !env || env.sunAt(t).el >= DAWN;
  }

  /** Between beats: the airport's movements in light, the room while a desk works, the terminal, the map now and then. */
  filler(t, wall, speed) {
    const { stage, room } = this.page, light = this.light(t), lag = this.pacer ? this.pacer.lag() : 0;
    const options = [];
    const add = (beat, w) => w > 0 && options.push([beat, w * (beat.camera === this.recent[0] ? 0.02 : this.recent.includes(beat.camera) ? 0.3 : 1)]);
    const up = this.airborne(t);
    if (stage && wall - this.lastMap > MAP_EVERY && up) add({ kind: "map", camera: "network", spec: {}, prio: 15, kicker: `${hhmm(t)} · The network`, caption: up }, 6);
    const active = room?.pods?.active(t);
    if (room && active) add({ kind: "work", camera: "room-desks", spec: { desk: active, t0: t, t1: t + 6 }, prio: 30, kicker: `${hhmm(t)} · The control center`, caption: `${upper(this.page.ep.occ?.mode === "one" ? "the desk" : DESK[active])} at work` }, lag > LAG ? 8 : 4);
    if (room && !active) add({ kind: "room", camera: "room", spec: { t0: t, t1: t + 6 }, prio: 12, kicker: `${hhmm(t)} · The control center`, caption: "" }, lag > LAG ? 5 : 1);
    if (stage && lag < LAG) {
      const soon = this.events.filter((e) => e.t > t + Math.max(0.15, speed * 1.5) && e.t < t + Math.max(0.7, speed * 3) && this.page.stage.plan.tracks.get(e.key)?.task);
      const land = soon.find((e) => e.kind === "landing"), off = soon.find((e) => e.kind === "takeoff");
      if (light && land) add(this.movement(land, "landing"), 7);
      if (light && off) add(this.movement(off, "takeoff"), 6);
      const moving = this.moving(t);
      if (light && moving) add({ kind: "follow", camera: "follow", spec: { key: moving }, prio: 20, kicker: hhmm(t), caption: this.tagOf(moving, t) ?? "", check: (u) => this.phase(moving, u) !== "parked" && this.phase(moving, u) !== "absent" }, 4);
      if (this.page.terminal && this.crowd(t) >= 12) add({ kind: "terminal", camera: "terminal", spec: { auto: true }, prio: 18, kicker: `${hhmm(t)} · ${this.terminalName()}`, caption: "" }, light ? 3 : 6);
      if (light && this.page.crews) add({ kind: "crew", camera: "crew", spec: {}, prio: 16, kicker: hhmm(t), caption: "" }, 2);
      if (light) add({ kind: "harbour", camera: "harbour", spec: {}, prio: 10, kicker: `${this.where} · ${hhmm(t)}`, caption: "" }, 1.5);
      add({ kind: "tower", camera: "tower", spec: {}, prio: 10, kicker: `${this.where} · ${hhmm(t)}`, caption: "" }, light ? 2 : 3);
    }
    if (!options.length) add({ kind: room ? "room" : "map", camera: room ? "room" : "network", spec: { t0: t, t1: t + 6 }, prio: 10, kicker: hhmm(t), caption: "" }, 1);
    const total = options.reduce((s, [, w]) => s + w, 0);
    let r = this.random() * total;
    for (const [beat, w] of options) if ((r -= w) <= 0) return { id: `fill:${beat.kind}:${wall.toFixed(1)}`, ...beat };
    return { id: `fill:${wall}`, ...options.at(-1)[0] };
  }

  /** People in the terminal's busiest gate area at t. */
  crowd(t) {
    const S = this.page.stage, k = S.terminal?.busiest(t);
    return k == null || !S.pax ? 0 : S.pax.at(k, t);
  }

  random() {
    this.seed = ((this.seed ?? 0x2f6b) * 1103515245 + 12345) & 0x7fffffff;
    return this.seed / 0x7fffffff;
  }

  movement(e, kind) {
    const tag = this.tagOf(e.key, e.t);
    return { kind, camera: kind, key: e.key, at: e.t, spec: { key: e.key, at: e.t }, prio: 30, end: e.t + (kind === "landing" ? 0.4 : 0.3), kicker: hhmm(e.t),
      caption: tag ? `${tag.split(" · ")[0]} ${kind === "landing" ? "lands" : "takes off"}` : `${e.flight} ${kind === "landing" ? "lands" : "takes off"}` };
  }

  /** A task aircraft moving on the ground now or about to, the soonest first. */
  moving(t) {
    const stage = this.page.stage;
    for (const e of this.events) {
      if (e.t < t - 4 || e.t > t + 6 || !stage.plan.tracks.get(e.key)?.task) continue;
      const p = this.phase(e.key, t);
      if (["pushback", "taxi", "taxi_in", "hold", "rollout"].includes(p)) return e.key;
    }
    return null;
  }

  phase(key, t) {
    return this.page.stage?.pose(t, key)?.phase ?? "absent";
  }

  tagOf(key, t) {
    const tr = this.page.stage?.plan.tracks.get(key);
    const legs = tr?.visits.flatMap((v) => [v.arr, v.dep]).filter((l) => l?.tag) ?? [];
    const near = legs.sort((a, b) => Math.abs((a.kind === "dep" ? a.out ?? a.sched : a.on ?? a.sched) - t) - Math.abs((b.kind === "dep" ? b.out ?? b.sched : b.on ?? b.sched) - t))[0];
    return near?.tag ?? null;
  }

  async cut(beat, t, wall) {
    this.used.add(beat.id);
    const prev = this.cur;
    this.cur = { beat, start: wall, at: t, check: beat.check ?? null };
    this.recent = [beat.camera, ...this.recent].slice(0, 3);
    if (beat.camera === "network") this.lastMap = wall;
    if (this.pacer) this.pacer.cut(SLOW[beat.camera] ?? Infinity, !SLOW[beat.camera]);
    this.shots.push({ id: beat.id, kind: beat.kind, camera: beat.camera, prio: beat.prio, at: t, wall, caption: beat.caption });
    if (this.shots.length > 400) this.shots.splice(0, 100);
    if (this.log) console.info(`cut ${hhmm(t)} ${beat.camera} ${beat.kind} ${beat.caption ?? ""}`);
    this.busy = true;
    try {
      await this.apply(beat, t);
    } catch (e) {
      console.warn("the director's shot failed:", beat.id, e);
      if (prev?.beat.camera !== "tower") await this.page.setView(this.page.stage ? "tower" : "network").catch(() => {});
    } finally {
      this.busy = false;
    }
    this.caption(beat, t);
  }

  async apply(beat, t) {
    const { setView, stage, room } = this.page;
    if (beat.camera.startsWith("room")) {
      if (!room) return setView("network");
      await setView(beat.camera);
      return room.shot({ name: beat.camera, ...beat.spec });
    }
    if (beat.camera === "network") return setView("network");
    if (!stage) return setView("network");
    if (beat.camera === "landing" || beat.camera === "takeoff") {
      const spec = this.scripted(beat.camera, beat.spec.key, beat.spec.at, t);
      if (!spec) return setView("tower");
      await setView("tower");
      return stage.shot(spec);
    }
    if (beat.camera === "gate" && beat.spec.key && !["parked", "pushback"].includes(this.phase(beat.spec.key, t + 1))) return setView("tower");
    return setView(beat.camera, beat.spec);
  }

  /** A landing or takeoff from beside the runway ahead of the aircraft, as record.py films one (FILM_JS camera()). */
  scripted(kind, key, at, t) {
    const S = this.page.stage, p = S.pose(at, key);
    if (!p || p.phase === "absent") return null;
    const D = Math.PI / 180, dir = (h) => [Math.sin(h * D), Math.cos(h * D)], add = (a, b, k = 1) => [a[0] + b[0] * k, a[1] + b[1] * k];
    const home = homeOf(S.data).centre, l = dir(p.heading - 90);
    const sd = (p.x - home[0]) * l[0] + (p.y - home[1]) * l[1] >= 0 ? l : [-l[0], -l[1]];
    const from = add(add([p.x, p.y], dir(p.heading), kind === "takeoff" ? 260 : 380), sd, 165);
    return { name: kind, from: [from[0], from[1], 5], at: key, fov: 40, frame: 65, t0: t, t1: at + 1 };
  }

  /** The shots of the day's best moments to replay: its biggest beats, those that play out at the airport first and
   * no more than three in the room alone, apart in time, each with its airport shot, in time order. */
  highlights(n = 6) {
    const after = (b) => this.beats.find((x) => x.after?.includes(b.id) && (!x.outdoor || this.light(x.at)));
    const score = (b) => b.prio + (after(b) ? 15 : 0) + (b.kind === "faa" ? 10 : 0);
    const picked = [];
    let room = 0;
    for (const b of this.beats.filter((b) => !b.after && b.prio >= 60 && b.at > this.page.P.lo).sort((a, b) => score(b) - score(a) || a.at - b.at)) {
      const f = after(b);
      if (picked.some((p) => Math.abs(p.at - b.at) < 45) || (!f && b.camera.startsWith("room") && room >= 3)) continue;
      room += !f && b.camera.startsWith("room") ? 1 : 0;
      picked.push(b);
      if (f) picked.push({ ...f, follows: true });
      if (picked.filter((p) => !p.follows).length >= n) break;
    }
    return picked.sort((a, b) => a.at - b.at || (a.follows ? 1 : -1));
  }

  decision(ds) {
    const d = ds[0], f = (x) => this.trip(x.flight), unit = unitName(d.detail);
    const list = ds.length > 1 ? `${ds.slice(0, 2).map((x) => this.flightNo(x.flight)).join(" and ")}${ds.length > 2 ? ` and ${ds.length - 2} more` : ""}` : f(d);
    switch (d.action) {
      case "cancel": return `cancels ${list}`;
      case "cancel_pair": return `cancels ${list} and ${ds.length > 1 ? "their returns" : "its return"}`;
      case "hold": return `holds ${list}${ds.length === 1 && d.detail ? ` until ${d.detail}` : ""}`;
      case "claim": return `takes a slot for ${list}`;
      case "swap_slots": return `swaps slots on ${list}`;
      case "release": return `lets ${list} go when ${ds.length > 1 ? "they can" : "it can"}`;
      case "assign": return `puts ${unit} on ${f(d)}`;
      case "call_reserve": return `calls ${unit} for ${f(d)}`;
      case "deadhead": return `flies ${unit} as passengers on ${f(d)}`;
      case "swap": return `swaps ${unit} onto ${f(d)}`;
      case "rest": return `sends ${unit} to rest`;
      case "rebook": return `rebooks ${ds.reduce((s, x) => s + (x.pax ?? 0), 0) || "passengers"} passengers off ${list}`;
      case "hotel": return `moves ${ds.reduce((s, x) => s + (x.pax ?? 0), 0) || "the"} passengers off ${list} to a later day`;
      default: return `${d.action} ${list}`;
    }
  }

  announce(d) {
    if (d.kind === "gs") {
      const who = /JBU/.test(d.flt_incl ?? "") ? "JetBlue flights" : "flights";
      return ["Ground stop", `The FAA stops ${who} to ${this.where} at their departure airports until ${hhmm(d.gs_period[1])}`];
    }
    const rate = d.program_rate ?? [], cap = rate.length ? (Math.min(...rate) === Math.max(...rate) ? rate[0] : `${Math.min(...rate)}–${Math.max(...rate)}`) : "fewer";
    return ["Ground delay program", `The FAA holds arrivals at ${this.where} to ${cap} an hour until ${hhmm(d.program_period[1])}`];
  }

  /** The JetBlue flights in the air at t in words, or "" with none. */
  airborne(t) {
    const up = this.page.P.ep.executed.filter((e) => e.status === "flown" && e.dep != null && e.dep <= t && t < e.arr).map((e) => this.page.P.sched.get(e.id)).filter(Boolean);
    if (!up.length) return "";
    const bound = up.filter((s) => s.dest === this.page.P.task.hub).length, { gs, gdp } = programsAt(this.page.P, t);
    return `${up.length} JetBlue flight${up.length === 1 ? "" : "s"} in the air${bound ? `, ${bound} of them bound for ${this.where}` : ""}${gs ? " · ground stop" : gdp ? " · delay program" : ""}`;
  }

  trip(fid) {
    const s = this.page.P.sched.get(fid);
    if (!s) return fid == null ? "" : `f${fid}`;
    const hub = this.page.P.task.hub;
    if (s.orig === hub) return `${s.flight} to ${this.cityOf(s.dest)}`;
    if (s.dest === hub) return `${s.flight} from ${this.cityOf(s.orig)}`;
    return `${s.flight} ${this.cityOf(s.orig)}–${this.cityOf(s.dest)}`;
  }

  flightNo(fid) {
    return this.page.P.sched.get(fid)?.flight ?? `f${fid}`;
  }

  cityOf(code) {
    return this.city.get(code) ?? code;
  }

  gate(stand) {
    return stand ? `gate ${String(stand).split("-").at(-1)}` : "its gate";
  }

  terminalName() {
    return { JFK: "JetBlue's Terminal 5", BOS: "JetBlue's Terminal C" }[this.page.P.task.hub] ?? "The terminal";
  }

  /** A desk's words as a short quote: their first clause with flights by number, or "" when it doesn't read as words. */
  quote(text) {
    const words = String(text ?? "").replace(/\bf(\d{1,4})\b/g, (m, id) => this.page.P.sched.get(Number(id))?.flight ?? m).trim();
    let first = words.split(/(?<=[.;])\s|,\s(?=and\b)/)[0].replace(/[.;]$/, "");
    if (first.length > 84) first = first.lastIndexOf(", ", 84) > 0 ? first.slice(0, first.lastIndexOf(", ", 84)) : "";
    return !first || /[a-z]\d|\d[a-z]{2}|\d{2}:\d{2}\+1|\b[A-Z]\d{3,}/.test(first) ? "" : first;
  }

  state() {
    return { shot: this.cur ? { id: this.cur.beat.id, kind: this.cur.beat.kind, camera: this.cur.beat.camera, held: this.cur.start } : null,
      beats: this.beats.length, used: this.used.size, shots: this.shots.slice(-30) };
  }
}
