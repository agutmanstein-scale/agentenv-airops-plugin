// Expert mode: a drawer over the scene with the tail chart and the desk as the agent saw it at the current watch: the
// hub's slot caps as known against the schedule, every tool call with its arguments and result, the situation the env
// gave it and the news as issued.
import { TailChart } from "./chart.js";
import { capsAt, esc, hhmm, watchAt } from "./model.js";
import * as plain from "./plain.js";

export const CREDIT = "Airport © OpenStreetMap contributors (ODbL) · imagery: USGS The National Map · map: Natural Earth, OpenMapTiles via OpenFreeMap · radar: NEXRAD, Iowa Environmental Mesonet · flights: BTS on-time data · aircraft: FAA registry · advisories: FAA ATCSCC · METARs, TAFs and forecast discussions: NWS via IEM";

export class Expert {
  constructor(el, P, ctx) {
    this.el = el;
    this.P = P;
    this.ctx = ctx;
    this.memo = {};
    el.innerHTML = `<div class="x-chart"></div><div class="x-ops"><div data-s="caps"></div><div data-s="calls"></div><div data-s="situation"></div><div data-s="news"></div></div>`;
    this.chart = new TailChart(el.querySelector(".x-chart"), P);
    el.addEventListener("click", (e) => {
      if (!e.target.closest("pre")) e.target.closest(".call")?.classList.toggle("open");
    });
  }

  set(name, key, html) {
    if (this.memo[name] === key) return;
    this.memo[name] = key;
    this.el.querySelector(`[data-s="${name}"]`).innerHTML = html();
  }

  render(t, flights, step) {
    if (this.el.hidden) return;
    const P = this.P, w = watchAt(P, t), watch = P.watches[Math.max(0, w)];
    const changed = new Set(w >= 0 && t - watch.t < 60 ? (watch.changes ?? []).map((c) => c.id) : []);
    this.chart.render(t, flights, { changed });
    this.set("caps", `${w}|${Math.floor(t / 60)}|${t >= P.lastWatch}`, () => this.caps(t, watch));
    this.set("calls", `${w}|${step}`, () => this.calls(watch, step));
    const situation = P.steps.filter((s) => s.t === watch.t && s.tool === "get_situation").at(-1)?.result?.situation;
    this.set("situation", `${w}`, () => (situation ? `<h5>The situation the env gave it at ${plain.clock(watch.t)}</h5><pre class="situation">${esc(situation)}</pre>` : ""));
    this.set("news", `${w}`, () => (watch.news.length ? `<h5>News at this watch, as issued</h5>${watch.news.map((n) => `<pre class="raw"><b>${hhmm(n.at)} ${esc(n.speaker ?? n.kind)}</b>\n${esc(n.text)}</pre>`).join("")}` : ""));
  }

  caps(t, watch) {
    const P = this.P, hub = P.task.hub, caps = capsAt(P, t), nominal = P.ep.nominal ?? {}, h0 = Math.floor(watch.t / 60);
    const hours = Array.from({ length: 8 }, (_, i) => h0 + i);
    const row = (kind) => `<tr><th>${kind === "arr" ? "Arrivals" : "Departures"}</th>${hours.map((h) => {
      const cap = caps[`${hub}:${kind}`]?.[String(h)], nom = nominal[`${hub}:${kind}`]?.[String(h)];
      const cls = cap === 0 ? "stop" : cap != null && nom != null && cap < nom ? "tight" : "";
      return `<td class="${cls}" title="${cap == null ? "no cap" : `cap ${cap}`}${nom != null ? `, JetBlue's schedule ${nom}` : ""}">${cap ?? "–"}<small>/${nom ?? "–"}</small></td>`;
    }).join("")}</tr>`;
    return `<h5>${esc(hub)} slots an hour for JetBlue ${t >= P.lastWatch ? "(the truth)" : `as known at ${plain.clock(watch.t)}`} <small>cap / scheduled</small></h5>
      <table class="caps"><tr><th></th>${hours.map((h) => `<th>${plain.clock(h * 60)}</th>`).join("")}</tr>${row("arr")}${row("dep")}</table>`;
  }

  calls(watch, step) {
    const steps = this.P.steps.filter((s) => s.t === watch.t);
    const item = (s) => `<li class="call${s.k === step ? " sel" : ""}"><div><b>${esc(s.tool)}</b> ${esc(plain.step(s, this.ctx))}</div>
      <pre>${esc(JSON.stringify(s.args ?? {}, null, 1))}\n→ ${esc(JSON.stringify(s.result ?? null, null, 1).slice(0, 6000))}</pre></li>`;
    return `<h5>Tool calls at watch ${watchAt(this.P, watch.t) + 1} <small>click one for its arguments and result</small></h5><ol class="calls">${steps.map(item).join("")}</ol>`;
  }
}
