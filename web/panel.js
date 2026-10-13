// The header (task, planner, reward against the references) and the ops panel on the virtual clock: the latest FAA
// advisory and forecast, the counters, the planner's steps and changes at the current watch, and the score card.
import { STATUS, css, dayLabel, esc, fmt, hhmm, hubState, newsAt, short, watchAt, zoneName } from "./model.js";

const CARRIERS = { B6: "JetBlue", AA: "American", DL: "Delta", UA: "United", WN: "Southwest", AS: "Alaska", NK: "Spirit" };
const PROGRAM = { gs: ["ground stop", "stop"], gs_cnx: ["ground stop cancelled", "clear"], gdp: ["ground delay program", "gdp"],
  gdp_proposed: ["proposed ground delay program", "gdp"], gdp_cnx: ["ground delay program cancelled", "clear"], other: ["advisory", "info"] };
const ACTIONS = { cancel: ["cancel", "cancelled"], restore: ["restore", "ontime"], hold: ["hold", "held"], release: ["release", "scheduled"] };
const REFS = [["floor", "floor", "no aircraft routing: a lower bound"], ["anchor", "hindsight", "the MIP on the truth, proven optimal"],
  ["run", "this run", ""], ["follower", "follower", "the MIP re-solved each watch as known"], ["blind", "blind follower", "no forecast"],
  ["naive", "no plan", "every flight as early as it can"], ["airline", "JetBlue", "the airline's real cancellations and times"]];
export const CREDIT = "Basemap © OpenMapTiles © OpenStreetMap contributors, via OpenFreeMap · Hub twin © OpenStreetMap contributors (ODbL) · Radar: NEXRAD n0q composite, Iowa Environmental Mesonet · Flights: BTS on-time data · Advisories: FAA ATCSCC · TAFs, METARs: NWS via IEM";

export function header(P) {
  const ep = P.ep, task = P.task, ref = ep.reference ?? {};
  const gap = ref.anchor ? (100 * (ep.cost.total - ref.anchor)) / ref.anchor : null;
  const carrier = CARRIERS[task.carrier] ?? task.carrier;
  const z = zoneName(task, task.window[0]);
  return `<header class="hd">
    <div class="brand">AirOps<b>Env</b></div>
    <div class="title"><h1>${esc(carrier)} at ${esc(task.hub)} · ${esc(dayLabel(task, task.window[0]))}</h1>
      <div class="sub">disruption ${hhmm(task.window[0])}–${hhmm(task.window[1])} ${z} · ${ep.schedule.length} flights · ${P.tailRows.length} aircraft · ${Object.keys(task.airports).length} airports · <span class="mono">${esc(task.task_id)}</span></div></div>
    <div class="who"><div><span class="k">planner</span> ${esc(ep.model_label ?? ep.model)}</div><div><span class="k">run</span> ${esc(ep.run)}</div></div>
    <div class="score" title="reward = 0.2 + 0.8·exp(−g/0.5), g = (cost − hindsight) / (hindsight − floor + 2% of hindsight)">
      <div class="reward"><span class="k">reward</span> ${ep.reward.toFixed(3)}</div>
      <div class="vs">cost ${short(ep.cost.total)} vs hindsight ${short(ref.anchor)}${gap != null ? ` <b>${gap >= 0 ? "+" : ""}${gap.toFixed(1)}%</b>` : ""}</div>
    </div>
  </header>`;
}

export class OpsPanel {
  constructor(el, P) {
    this.el = el;
    this.P = P;
    this.memo = {};
    el.innerHTML = `
      <section class="clock"><div class="sub" data-s="zone"></div><div class="time mono" data-s="time"></div><div class="watch" data-s="watch"></div><div class="hubstat" data-s="hub"></div></section>
      <section class="card adv" data-s="adv"></section>
      <section class="card wx" data-s="wx"></section>
      <section class="kpis" data-s="kpis"></section>
      <section class="card plan" data-s="plan"></section>
      <section class="card final" data-s="final" hidden></section>
      <section class="legend">${legend()}</section>
      <footer class="credit">${CREDIT}</footer>`;
    this.slots = Object.fromEntries([...el.querySelectorAll("[data-s]")].map((n) => [n.dataset.s, n]));
  }

  set(name, key, html) {
    if (this.memo[name] === key) return;
    this.memo[name] = key;
    this.slots[name].innerHTML = typeof html === "function" ? html() : html;
  }

  render(t, counters, step) {
    const P = this.P, task = P.task, w = watchAt(P, t), end = t >= P.end - 25;
    this.set("zone", zoneName(task, t), `Virtual clock · ${zoneName(task, t)} · ${dayLabel(task, t)}`);
    this.set("time", hhmm(t), hhmm(t));
    const next = P.watches[w + 1];
    this.set("watch", `${w}|${end}`, end ? "Day played out on the truth" : w < 0 ? `First watch at ${hhmm(P.watches[0]?.t ?? task.start)}`
      : `Watch ${w + 1} of ${P.watches.length}${next ? ` · next ${hhmm(next.t)}` : " · the last"}`);
    const hs = hubState(P, t);
    this.set("hub", JSON.stringify(hs), () => ["arr", "dep"].map((k) => {
      const s = hs[k], label = k === "arr" ? "arr" : "dep";
      const state = s.stop ? "stop" : s.tight ? "tight" : "free";
      const text = s.cap == null ? "no cap" : s.stop ? "0/h · stopped" : `cap ${s.cap}/h${s.nominal != null ? ` · ${s.nominal} sched` : ""}`;
      return `<span class="chip ${state}"><b>${task.hub} ${label}</b> ${text}</span>`;
    }).join(""));
    const news = newsAt(P, t);
    const adv = [...news].reverse().find((n) => n.kind === "advisory" && n.data?.airport === task.hub) ?? [...news].reverse().find((n) => n.kind === "advisory");
    const since = w >= 0 ? news.filter((n) => n.at > P.watches[w].t - 60).length : news.length;
    this.slots.adv.hidden = end;
    if (!end) this.set("adv", `${adv?.id}|${since}`, () => advisory(adv, news, since));
    const taf = [...news].reverse().find((n) => n.kind === "taf");
    const metar = [...news].reverse().find((n) => n.kind === "metar");
    this.slots.wx.hidden = end;
    if (!end) this.set("wx", `${taf?.id}|${metar?.id}`, () => weather(taf, metar));
    this.set("kpis", JSON.stringify(counters) + end, () => kpis({ ...counters, final: P.ep.cost.total }, w >= 0 ? P.watches[w] : null, end ? Infinity : t));
    this.slots.plan.hidden = end;
    if (!end) this.set("plan", `${w}|${step}`, () => plan(P, w, step));
    this.slots.final.hidden = !end;
    if (end) this.set("final", "final", () => scorecard(P));
  }
}

function advisory(adv, news, since) {
  if (!adv) return `<h4>FAA Command Center</h4><div class="muted">No advisory yet.</div>`;
  const [label, cls] = PROGRAM[adv.data?.program ?? "other"] ?? PROGRAM.other;
  const body = adv.text.split("\n").slice(1).filter((l) => l.trim() && !/^(ELEMENT TYPE|ADL TIME|DEP SCOPE|CANADIAN|DELAY ASSIGNMENT TABLE|FLT INCL|USER UPDATES)/.test(l)).join("\n");
  const recent = [...news].reverse().slice(0, 4).map((n) => `<li><span class="mono">${hhmm(n.at)}</span> <span class="tag ${n.kind}">${n.kind === "advisory" ? esc(n.data?.airport ?? "FAA") : n.kind.toUpperCase()}</span> ${esc(headline(n))}</li>`).join("");
  return `<h4><span class="pill ${cls}">${esc(label)}</span> ${esc(adv.data?.element ?? "")} <span class="muted">issued ${hhmm(adv.at)}</span></h4>
    <div class="adv-title">${esc(adv.data?.title ?? adv.text.split("\n")[0])}</div>
    <pre class="adv-body">${esc(body)}</pre>
    <ul class="feed">${recent}</ul>
    <div class="muted small">${since} news item${since === 1 ? "" : "s"} since the last watch</div>`;
}

function headline(n) {
  if (n.kind === "advisory") return `${n.data?.title ?? ""}`.toLowerCase().replace(/^cdm /, "");
  const first = n.text.split("\n").find((l) => /^[A-Z]{4} \d{6}Z/.test(l.trim())) ?? n.text.split("\n")[0];
  return first.trim().slice(0, 60);
}

function weather(taf, metar) {
  const lines = taf ? taf.text.split("\n") : [];
  return `<h4>Terminal forecast <span class="muted">${taf ? `${esc(taf.data?.station ?? "")} · issued ${hhmm(taf.at)}` : "none yet"}</span></h4>
    ${taf ? `<pre class="taf">${esc(lines.slice(lines[0].startsWith("TAF") ? 1 : 0, 7).join("\n"))}</pre>` : ""}
    ${metar ? `<div class="metar mono"><span class="muted">${hhmm(metar.at)}</span> ${esc(metar.text.replace(/ RMK .*/, ""))}</div>` : ""}`;
}

function kpis(c, w, t) {
  const box = (v, l, s = "") => `<div class="kpi"><div class="v mono">${v}</div><div class="l">${l}${s ? ` <span class="muted">${s}</span>` : ""}</div></div>`;
  return box(fmt(c.cancelled + c.auto), "flights cancelled", c.auto ? `incl. ${c.auto} auto` : "")
    + box(fmt(c.paxDelay), "passenger-delay minutes")
    + box(fmt(c.disrupted), "passengers disrupted", "≥ 15 min")
    + box(fmt(c.held), "flights on hold")
    + `<div class="kpi-line">${t === Infinity ? "final cost" : "cost so far"} <b class="mono">${short(t === Infinity ? c.final : c.costSoFar)}</b> pax-min${w?.est_cost != null && t !== Infinity ? ` · plan as known at ${hhmm(w.t)} <b class="mono">${short(w.est_cost)}</b>` : t === Infinity ? " incl. ferries" : ""}</div>`;
}

function plan(P, w, step) {
  if (w < 0) return `<h4>Planner</h4><div class="muted">The desk opens at ${hhmm(P.watches[0]?.t ?? P.task.start)}: the planner gets ${P.watches.length} hourly watches.</div>`;
  const watch = P.watches[w], next = P.watches[w + 1]?.t ?? Infinity;
  const steps = P.steps.filter((s) => s.t >= watch.t && s.t < next);
  const items = steps.map((s) => `<li class="${s.k === step ? "sel" : ""}"><span class="tool mono">${esc(s.tool)}</span> ${esc(stepSummary(s))}</li>`).join("");
  const ch = watch.changes ?? [];
  const order = { cancel: 0, restore: 1, hold: 2, release: 3 };
  const shown = [...ch].sort((a, b) => order[a.action] - order[b.action] || (P.sched.get(a.id)?.sched_dep ?? 0) - (P.sched.get(b.id)?.sched_dep ?? 0)).slice(0, 6);
  const changes = shown.map((c) => {
    const [label, key] = ACTIONS[c.action] ?? [c.action, "scheduled"];
    const s = P.sched.get(c.id);
    const tail = c.action === "hold" ? ` until ${hhmm(c.not_before)}` : "";
    return `<li><span class="act" style="--c:${css(key)}">${label}</span> <span class="mono">${esc(c.flight ?? s?.flight)}</span> ${esc(c.orig ?? s?.orig)}→${esc(c.dest ?? s?.dest)}${tail} <span class="muted">${s ? `sched ${hhmm(s.sched_dep)} · ${s.pax} pax` : ""}</span></li>`;
  }).join("");
  const count = Object.entries(ch.reduce((a, c) => ((a[c.action] = (a[c.action] ?? 0) + 1), a), {})).map(([k, v]) => `${v} ${k}`).join(", ");
  return `<h4>Planner · watch ${w + 1} · ${hhmm(watch.t)} <span class="muted">${watch.frozen ?? 0} flights frozen</span></h4>
    <ul class="steps">${items}</ul>
    <div class="changes-h">${ch.length ? `Changes: ${count}` : "No changes at this watch"}</div>
    <ul class="changes">${changes}</ul>${ch.length > shown.length ? `<div class="muted small">+${ch.length - shown.length} more</div>` : ""}`;
}

function stepSummary(s) {
  if (s.tool === "submit_plan" && s.args?.plan) {
    const r = s.result ?? {};
    return `${Object.keys(s.args.plan).length} flights · est. cost ${short(r.est_cost)}`;
  }
  const r = typeof s.result === "string" ? s.result : JSON.stringify(s.result ?? "");
  return r.length > 70 ? `${r.slice(0, 69)}…` : r;
}

function scorecard(P) {
  const ep = P.ep, ref = ep.reference ?? {}, c = ep.cost;
  const rows = REFS.map(([k, label, note]) => [label, k === "run" ? c.total : ref[k], note, k === "run"]).filter((r) => r[1] != null);
  const max = Math.max(...rows.map((r) => r[1]));
  const bars = rows.map(([label, v, note, me]) => `<div class="ref${me ? " me" : ""}" title="${esc(note)}"><span class="rl">${esc(label)}</span><span class="rb"><i style="width:${(100 * v) / max}%"></i></span><span class="rv mono">${short(v)}</span></div>`).join("");
  const gap = ref.anchor ? (100 * (c.total - ref.anchor)) / ref.anchor : null;
  return `<h4>Score card</h4>
    <div class="big"><span class="mono">${ep.reward.toFixed(3)}</span><span class="muted">reward</span></div>
    <div class="breakdown mono">${fmt(c.total)} pax-min = delay ${fmt(c.pax_delay)} + cancellations ${fmt(c.cancel)} + ferries ${fmt(c.ferry)}</div>
    <div class="muted small">${gap != null ? `${gap.toFixed(1)}% above the hindsight optimum` : ""}. Reward 0.2 + 0.8·exp(−g/0.5), g = excess over hindsight ÷ (hindsight − floor + 2%).</div>
    <div class="refs">${bars}</div>`;
}

function legend() {
  const keys = ["ontime", "delayed", "held", "cancelled", "auto", "scheduled", "ferry", "morning"];
  const sw = (k) => k === "cancelled" || k === "auto" || k === "ferry" ? `<i class="sw hatch" style="--c:${css(k)}"></i>` : `<i class="sw" style="background:${css(k)}"></i>`;
  return keys.map((k) => `<span>${sw(k)}${STATUS[k].label}</span>`).join("")
    + `<span><i class="sw dashed"></i>as planned (not yet flown)</span><span><i class="sw gs"></i>slot cap: stopped / reduced</span>`;
}
