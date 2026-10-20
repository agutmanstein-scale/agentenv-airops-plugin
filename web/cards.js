// The cards over the scene: the header (the run, the airport and the day, the local clock, the weather and the FAA's
// programs in plain English), the desk (the watch: its news, what the agent did, its own words when it said any), the
// scoreboard, the departures and arrivals board, and the timeline with the cameras.
import { HUB_NAMES, boardAt, esc, flagged, programsAt, rewardOf, scoreAt, short, utcAt, watchAt, zoneName } from "./model.js";
import * as plain from "./plain.js";

const REFS = [["follower", "Re-planning every hour", "the reference: a MIP re-solved at each watch on the day as known"],
  ["naive", "Doing nothing", "every flight as early as it can"], ["airline", "JetBlue on the day", "the airline's real cancellations and times"]];
const ORDER = { advisory: 0, taf: 1, metar: 2, outlook: 3 };
const ACTS = { cancel: 0, hold: 1, release: 2, restore: 3 };
const fmt = (n) => Math.round(n).toLocaleString("en-US");

export function dayShort(task, m) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: task.tz, weekday: "short", day: "numeric", month: "short" }).format(new Date(utcAt(task, m))).replace(",", "");
}

function dayLong(task, m) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: task.tz, weekday: "short", day: "numeric", month: "short", year: "numeric" }).format(new Date(utcAt(task, m))).replace(",", "");
}

const when = (task, m) => (m < 0 ? `${plain.clock(m)} the day before` : m >= 1440 ? `${plain.clock(m)} ${dayShort(task, m).split(" ")[0]}` : plain.clock(m));

class Card {
  constructor(el) {
    this.el = el;
    this.memo = new Map();
  }

  slot(name, key, html) {
    const node = this.el.querySelector(`[data-s="${name}"]`);
    if (this.memo.get(name) === key) return node;
    this.memo.set(name, key);
    node.innerHTML = typeof html === "function" ? html() : html;
    return node;
  }
}

export class Header extends Card {
  constructor(el, P, { model, weather = null, brand = "AirOpsEnv v1" }) {
    super(el);
    this.P = P;
    const task = P.task;
    el.innerHTML = `<h1><b>${esc(brand)}</b> · ${esc(model)} <span>· ${esc(HUB_NAMES[task.hub] ?? task.hub)}, ${esc(dayLong(task, task.start))}</span></h1>
      <div class="clock" data-s="clock"></div><div class="wx" data-s="wx"></div><div class="faa" data-s="faa"></div>`;
    this.metars = weather?.length ? weather.map((w) => ({ id: w.t, at: w.t, plain: w.plain }))
      : P.news.filter((n) => n.kind === "metar").map((n) => ({ id: n.id, at: n.at, plain: plain.metar(n.text).plain }));
  }

  render(t, { rate = null, quiet = false } = {}) {
    const P = this.P, task = P.task, m = Math.floor(t + 1e-6);
    const r = rate && rate > 1.5 ? `${rate >= 60 ? `${Math.round(rate / 60)} min/s` : `${Math.round(rate)}×`}${quiet ? " · quiet hours, faster" : ""}` : "";
    this.slot("clock", `${m}|${r}`, () => `<span class="day">${esc(dayShort(task, m))}</span> <b>${plain.clock(m)}</b> <small>${esc(zoneName(task, m))}</small>${r ? `<small class="rate">${r}</small>` : ""}`);
    const w = this.metars.findLast((n) => n.at <= t) ?? this.metars[0];
    this.slot("wx", w?.id, () => (w ? esc(w.plain) : ""));
    const { gs, gdp } = programsAt(P, t), where = HUB_NAMES[task.hub] ?? task.hub;
    this.slot("faa", `${gs?.id}|${gdp?.id}`, () => {
      if (gs) {
        const who = /JBU/.test(gs.data.flt_incl ?? "") ? "JetBlue flights" : "Flights";
        return `<span class="chip stop">Ground stop</span> ${who} to ${esc(where)} wait at their departure airports until ${plain.clock(gs.data.gs_period[1])}`;
      }
      if (gdp) {
        const rate = gdp.data.program_rate ?? [], lo = Math.min(...rate), hi = Math.max(...rate);
        return `<span class="chip gdp">Delay program</span> The FAA holds arrivals to ${rate.length ? (lo === hi ? lo : `${lo}–${hi}`) : "fewer"} an hour; flights wait for a slot`;
      }
      return `<span class="chip clear">No FAA program</span> Flights to ${esc(where)} leave on their own times`;
    });
  }
}

export class Desk extends Card {
  constructor(el, P, { model, city }) {
    super(el);
    this.P = P;
    this.model = model;
    this.ctx = { task: P.task, earlier: P.news, city, hub: P.task.hub, sched: P.sched, flightOf: (ref) => P.sched.get(Number(String(ref).replace(/^f/, "")))?.flight };
    el.innerHTML = `<div class="desk-h" data-s="head"></div><div data-s="news"></div><div data-s="acts"></div><div data-s="words"></div>`;
    el.addEventListener("click", (e) => {
      const item = e.target.closest(".news li");
      if (item && !e.target.closest("pre")) item.classList.toggle("open");
      if (e.target.closest(".more")) this.el.classList.toggle("all");
    });
  }

  render(t, step = null) {
    const P = this.P, w = watchAt(P, t), n = P.watches.length, closed = w === n - 1 && t >= P.watches[w].t + 60;
    const watch = P.watches[Math.max(0, w)], next = P.watches[w + 1];
    this.slot("head", `${w}|${closed}`, () => closed
      ? `<b>The desk is closed</b><span>${esc(this.model)} kept the desk for ${n} watches; the rest of the day flies on what really happened.</span>`
      : `<b>Watch ${w + 1} of ${n} · ${plain.clock(watch.t)}</b><span>Every hour ${esc(this.model)} reads the news and may cancel or hold flights${next ? `; flights leaving before ${plain.clock(next.t)} are locked in` : ""}.</span>`);
    this.slot("news", closed ? "closed" : `${w}`, () => (closed ? "" : this.news(watch)));
    this.slot("acts", closed ? "closed" : `${w}|${step}`, () => (closed ? this.day() : this.acts(watch, step)));
    const said = P.steps.filter((s) => s.t === watch.t && typeof s.text === "string" && s.text.trim());
    this.slot("words", `${w}|${closed}`, () => (said.length && !closed ? `<h5>In its own words</h5><blockquote>${esc(said.at(-1).text.trim().slice(0, 420))}</blockquote>` : ""));
  }

  news(watch) {
    const items = [...watch.news].sort((a, b) => b.at - a.at), hub = this.P.task.hub;
    const faa = items.filter((it) => it.kind === "advisory" && it.data?.element === hub)
      .filter((it, i, all) => !all.slice(0, i).some((x) => x.data.kind === it.data.kind && JSON.stringify(x.data.gs_period ?? x.data.program_period) === JSON.stringify(it.data.gs_period ?? it.data.program_period)));
    const first = (kind) => items.find((it) => it.kind === kind);
    const top = [...faa.slice(0, 2), first("taf"), first("metar"), ...faa.slice(2), first("outlook")].filter(Boolean).slice(0, 4)
      .sort((a, b) => (ORDER[a.kind] ?? 9) - (ORDER[b.kind] ?? 9) || b.at - a.at);
    const rest = items.filter((it) => !top.includes(it));
    const li = (it) => {
      const p = plain.news(it, this.ctx);
      return `<li class="tone-${p.tone}" title="Click for the original"><span class="when">${esc(when(this.P.task, it.at))} · ${esc(p.label)}</span>
        <b>${esc(p.title)}</b> ${esc(p.text)}<pre>${esc(it.text)}</pre></li>`;
    };
    if (!items.length) return `<h5>News</h5><p class="quiet">Nothing new since the last watch.</p>`;
    return `<h5>News since the last watch <small>click one for the original</small></h5><ul class="news">${top.map(li).join("")}${rest.map((it) => li(it).replace("<li ", '<li data-more="1" ')).join("")}</ul>
      ${rest.length ? `<button class="more" type="button"><span>${rest.length} more</span><span>fewer</span></button>` : ""}`;
  }

  acts(watch, step) {
    const P = this.P, changes = [...(watch.changes ?? [])].sort((a, b) => ACTS[a.action] - ACTS[b.action] || (P.sched.get(a.id)?.sched_dep ?? 0) - (P.sched.get(b.id)?.sched_dep ?? 0));
    const s = step != null ? P.steps[step] : null;
    const now = s && s.t === watch.t ? `<p class="now"><span>Step ${P.steps.filter((x) => x.t === s.t).indexOf(s) + 1}</span> ${esc(plain.step(s, this.ctx))}</p>` : "";
    const list = changes.slice(0, 7).map((c) => `<li class="act-${c.action}">${esc(plain.action(c, this.ctx))}</li>`).join("");
    const cost = watch.est_cost != null ? `<p class="cost">Its plan on the day as known: <b>${short(watch.est_cost)}</b> passenger-minutes</p>` : "";
    return `<h5>What ${esc(this.model)} did</h5>${now}${changes.length ? `<ul class="acts">${list}</ul>${changes.length > 7 ? `<p class="quiet">and ${changes.length - 7} more changes</p>` : ""}` : `<p class="quiet">No changes at this watch.</p>`}${cost}`;
  }

  day() {
    const ex = this.P.ep.executed, count = (k) => ex.filter((e) => e.status === k).length, held = ex.filter((e) => e.held).length;
    const auto = count("auto_cancelled"), hours = Math.round((this.P.task.dmax ?? 360) / 60);
    return `<h5>Its day</h5><ul class="acts"><li class="act-cancel">Cancelled ${count("cancelled")} flights${auto ? `; ${auto} more could not leave within ${hours} hours of schedule and were cancelled` : ""}</li>
      <li class="act-hold">Held ${held} flights for a later slot</li><li>${count("flown")} of ${ex.length} flights flew</li></ul>`;
  }
}

export class Score extends Card {
  constructor(el, P, { model }) {
    super(el);
    this.P = P;
    const ep = P.ep, ref = ep.reference ?? {};
    const refs = ep.occ?.refs ? ep.occ.refs.map((r) => [r.label, r.reward, false, r.note ?? ""]) : REFS.map(([k, label, note]) => [label, rewardOf(ref, ref[k]), false, note]);
    const rows = [[model, ep.reward, true, "this run"], ...refs].filter((r) => r[1] != null);
    const under = ep.occ ? flagged(ep.cost.total, ep.reference?.anchor, ep.reference?.gap_pct, ep.occ.grade?.reward_L) : null;
    const unit = ep.occ ? "Cost in passenger-minutes: passengers' delay and cancellations (more the later they are decided), and the airline's aircraft and crew minutes at FAA rates."
      : "Cost in passenger-minutes: each minute a passenger lands late counts 1, each cancelled passenger 523.";
    const top = ep.occ ? "Final reward (1 = the best plan found)" : "Final reward (1 = the best plan in hindsight)";
    el.innerHTML = `<h5 data-s="title"></h5>
      <div class="kpis" data-s="kpis"></div><div class="costbar" data-s="bar"></div>
      <p class="unit">${esc(unit)}</p>
      <div class="refs" title="Reward from 0.2 to 1"><div class="refs-h">${esc(top)}</div>${rows.map(([label, r, me, note]) => `<div class="ref${me ? " me" : ""}" title="${esc(note)}"><span>${esc(label)}</span><i><b style="width:${(100 * (r - 0.2)) / 0.8}%"></b></i><em>${r.toFixed(3)}${me && under ? "*" : ""}</em></div>`).join("")}</div>${under ? `<p class="flag">* ${esc(under)}</p>` : ""}`;
  }

  render(t) {
    const P = this.P, s = scoreAt(P, t), best = P.ep.reference?.anchor, top = Math.max(P.ep.cost.total, best ?? 0) * 1.04;
    this.slot("title", s.done, s.done ? "The day's score" : "Score so far");
    this.slot("kpis", `${s.delayedPax}|${s.cancelled}|${Math.round(s.cost / 1000)}`, () => `
      <div><b>${fmt(s.delayedPax)}</b><span>passengers delayed 15 min or more</span></div>
      <div><b>${s.cancelled}</b><span>flights cancelled · ${fmt(s.cancelPax)} passengers</span></div>
      <div><b>${short(s.cost)}</b><span>${s.done ? `the day's cost${best ? `, ${((100 * (s.cost - best)) / best).toFixed(1)}% over the best ${P.ep.occ ? "plan found" : "possible"}` : ""}` : "cost so far"}</span></div>`);
    this.slot("bar", `${Math.round((200 * s.cost) / top)}|${s.done}`, () => `<i class="fill" style="width:${(100 * s.cost) / top}%"></i>${best ? `<i class="best" style="left:${(100 * best) / top}%"><span>${P.ep.occ ? "best plan found" : "best possible day"} ${short(best)}</span></i>` : ""}`);
  }
}

export class Board extends Card {
  constructor(el, P, { city, stands, terminal = null, rows = 6, scene = false, scope = "the task's flights" }) {
    super(el);
    this.P = P;
    this.city = city;
    this.stands = stands;
    this.rows = rows;
    el.innerHTML = `<div class="board-h"><b>${esc(HUB_NAMES[P.task.hub] ?? P.task.hub)}</b> <span>JetBlue${terminal ? ` · Terminal ${esc(terminal)}` : ""} · ${esc(scope)}</span></div>
      <section><h6><span>Departures</span><span>To</span><span>Gate</span><span>Status</span></h6><ol data-s="dep"></ol></section>
      <section><h6><span>Arrivals</span><span>From</span><span>Gate</span><span>Status</span></h6><ol data-s="arr"></ol></section>
      <p class="legend"><i class="ok"></i>on time <i class="delay"></i>delayed <i class="hold"></i>held <i class="cancel"></i>cancelled${P.ep.occ ? ' <i class="crew"></i>crew' : ""}${scene ? `<span>In 3D ${P.ep.occ ? "JetBlue's aircraft carry tags, with their crews' status at the gate" : "the task's aircraft carry tags"}; other airlines are grey</span>` : ""}</p>`;
  }

  render(t, flights) {
    const b = boardAt(this.P, t, flights, { stands: this.stands, rows: this.rows }), before = [this.memo.get("dep"), this.memo.get("arr")];
    const lists = ["dep", "arr"].map((k) => {
      const rows = b[k].map((r) => `<li class="${r.tone}${r.changed ? " changed" : ""}"><span class="t">${plain.clock(r.sched)}</span><span class="f">${esc(r.flight)}</span><span class="c">${esc(this.city(r.other))}</span><span class="g">${esc(r.stand ?? "")}</span><span class="s">${esc(r.status)}${r.time != null && /Departed|Landed/.test(r.status) ? ` ${plain.clock(r.time)}` : ""}</span></li>`);
      return this.slot(k, rows.join(""), () => rows.join("") || `<li class="none">No more ${k === "dep" ? "departures" : "arrivals"} today</li>`);
    });
    if (before[0] !== this.memo.get("dep") || before[1] !== this.memo.get("arr") || this.fitted !== this.el.clientHeight) lists.forEach((ol) => this.cut(ol));
    this.fitted = this.el.clientHeight;
  }

  cut(ol) {
    const bottom = Math.min(ol.getBoundingClientRect().bottom, ol.parentElement.getBoundingClientRect().bottom);
    for (const li of ol.children) li.classList.toggle("cut", li.getBoundingClientRect().bottom > bottom + 0.5);
  }
}

export class Timeline {
  constructor(el, P, { bands = [], speeds, speed, cameras = [] }) {
    this.el = el;
    this.P = P;
    const lo = P.lo, hi = P.hi, pct = (m) => `${(100 * (Math.min(hi, Math.max(lo, m)) - lo)) / (hi - lo)}%`;
    const width = (a, b) => `${(100 * (Math.min(hi, b) - Math.max(lo, a))) / (hi - lo)}%`;
    const ticks = [];
    for (let h = Math.ceil(lo / 180) * 3; h * 60 <= hi; h += 3) ticks.push(`<i class="tick" style="left:${pct(h * 60)}"><b>${plain.clock(h * 60)}</b></i>`);
    const marks = P.watches.map((w, i) => `<i class="wm" style="left:${pct(w.t)}" title="Watch ${i + 1} · ${plain.clock(w.t)}"></i>`).join("");
    const programs = bands.filter((b) => b.to > lo && b.from < hi).map((b) => `<i class="band ${b.kind}" style="left:${pct(b.from)};width:${width(b.from, b.to)}" title="${b.kind === "gs" ? "Ground stop" : "Ground delay program"} ${plain.clock(b.from)}–${plain.clock(b.to)}"></i>`).join("");
    el.innerHTML = `<button class="play" type="button" aria-label="Play" title="Play or pause (space)">▶</button>
      <select class="speed" aria-label="Speed" title="Speed (+ and −)">${speeds.map((v) => `<option value="${v}" ${v === speed ? "selected" : ""}>${v < 1 ? `${v * 60} s/s` : `${v} min/s`}</option>`).join("")}</select>
      <div class="track"><div class="key"><span class="k-gs">ground stop</span><span class="k-gdp">delay program</span><span class="k-wm">watches</span></div>
        <div class="rail"><i class="fill"></i>${programs}${marks}<i class="knob"></i></div>${ticks.join("")}
        <input type="range" min="${lo}" max="${hi}" step="1" value="${lo}" aria-label="Clock"></div>
      <span class="now"></span>
      <div class="cams" role="group" aria-label="Camera">${cameras.map((c) => `<button type="button" data-cam="${c.id}" title="${esc(c.title)}">${esc(c.label)}</button>`).join("")}</div>
      <button class="expert-toggle" type="button" title="Expert mode: the desk as the agent saw it (x)">Expert</button>`;
    this.input = el.querySelector("input");
    this.fill = el.querySelector(".fill");
    this.knob = el.querySelector(".knob");
    this.label = el.querySelector(".now");
    this.playBtn = el.querySelector(".play");
    this.handlers = {};
    this.input.addEventListener("input", () => this.handlers.seek?.(Number(this.input.value)));
    this.playBtn.addEventListener("click", () => this.handlers.play?.());
    el.querySelector(".speed").addEventListener("change", (e) => this.handlers.speed?.(Number(e.target.value)));
    el.querySelector(".expert-toggle").addEventListener("click", () => this.handlers.expert?.());
    for (const b of el.querySelectorAll(".cams button")) b.addEventListener("click", () => this.handlers.camera?.(b.dataset.cam));
    this.pct = pct;
  }

  on(name, fn) {
    this.handlers[name] = fn;
    return this;
  }

  update(t) {
    const p = this.pct(t);
    this.input.value = String(Math.round(t));
    this.fill.style.width = p;
    this.knob.style.left = p;
    const m = Math.floor(t + 1e-6);
    this.label.innerHTML = `${m >= 1440 || m < 0 ? `<small>${dayShort(this.P.task, m).split(" ")[0]}</small> ` : ""}${plain.clock(m)}`;
  }

  playing(on) {
    this.playBtn.textContent = on ? "❚❚" : "▶";
    this.playBtn.setAttribute("aria-label", on ? "Pause" : "Play");
  }

  camera(id) {
    for (const b of this.el.querySelectorAll(".cams button")) b.classList.toggle("on", b.dataset.cam === id);
  }

  expert(on) {
    this.el.querySelector(".expert-toggle").classList.toggle("on", on);
  }
}
