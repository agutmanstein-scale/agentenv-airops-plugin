// The expert drawer of a control-center day, in tabs: each crew on duty against its legal limit (the duty clocks), the
// full log of requests between the desks, on a day with passengers the flights whose passengers' day changed, and the
// page each desk read at its latest turn; v1's tail chart and tool calls stay as the last tab. A crew picked in the duty clocks is announced as "airops:crew" for the map and the chart,
// a request picked in the log as "airops:seek" for the timeline.
import { CREW_INK, crewIndex, dutiesAt, whereAt } from "./crewdata.js";
import { css, esc } from "./model.js";
import { PAX_INK, disrupted, paxAt, paxIndex } from "./paxdata.js";
import { clock } from "./plain.js";
import { unitName } from "./room.js";

const DESKS = { ops: "Ops controller", crew: "Crew scheduler", customer: "Customer desk", one: "Both desks" };
const SHORT = { ops: "Ops", crew: "Crew", customer: "Customer", one: "Both" };
const TYPES = { CANCEL: "Cancel", CANCEL_PAIR: "Cancel round trip", HOLD: "Hold", COVER: "Cover", TEXT: "Message" };
const VERBS = { cancel: "cancelled", cancel_pair: "cancelled the round trip of", hold: "held", claim: "took a slot for", release: "released",
  assign: "put a crew on", call_reserve: "called a reserve for", deadhead: "flew a crew as passengers on", swap: "swapped a crew onto", rest: "rested the crew of",
  rebook: "rebooked passengers off", hotel: "booked hotels for passengers off" };
const ROW = 22, BACK = 13 * 60, AHEAD = 9 * 60, PAD = 6;

const left = (m) => {
  const a = Math.round(Math.abs(m)), h = Math.floor(a / 60), r = a % 60;
  return h ? `${h} h ${String(r).padStart(2, "0")}` : `${r} min`;
};

export class OccDrawer {
  constructor(el, P, { v1, tab = null }) {
    this.el = el;
    this.P = P;
    this.occ = P.ep.occ;
    this.C = crewIndex(P);
    this.memo = {};
    this.filter = "all";
    this.sel = null;
    this.t = null;
    const reqs = this.occ.requests?.length > 0 || this.occ.mode !== "one";
    this.X = paxIndex(P);
    this.tabs = [["duty", "Duty clocks"], ...(reqs ? [["reqs", "Requests"]] : []), ...(this.X ? [["pax", "Passengers"]] : []), ["pages", "Pages"], ["v1", "Aircraft and tool calls"]];
    this.tab = this.tabs.some(([k]) => k === tab) ? tab : "duty";
    el.classList.add("xo");
    el.innerHTML = `<nav class="xo-tabs">${this.tabs.map(([k, label]) => `<button type="button" data-tab="${k}">${label}</button>`).join("")}<span class="xo-note"></span></nav>
      <div class="xo-pane xo-duty" data-p="duty"><div class="dc-main"><div class="dc-head"></div><div class="dc-axis"></div><div class="dc-list"><div class="dc-spacer"><div class="dc-rows"></div></div></div></div>
        <aside class="dc-side"></aside></div>
      <div class="xo-pane xo-reqs" data-p="reqs"></div>
      <div class="xo-pane xo-pax" data-p="pax"></div>
      <div class="xo-pane xo-pages" data-p="pages"></div>
      <div class="xo-pane xo-v1" data-p="v1">${v1}</div>`;
    this.list = el.querySelector(".dc-list");
    this.list.addEventListener("scroll", () => this.t != null && this.duty(this.t, this.flights));
    el.addEventListener("click", (e) => this.click(e));
    this.show(this.tab);
    injectStyle();
  }

  show(tab) {
    this.tab = tab;
    for (const b of this.el.querySelectorAll(".xo-tabs button")) b.classList.toggle("on", b.dataset.tab === tab);
    for (const p of this.el.querySelectorAll(".xo-pane")) p.hidden = p.dataset.p !== tab;
    this.memo = {};
    this.scrolled = false;
  }

  click(e) {
    const tab = e.target.closest("[data-tab]"), filter = e.target.closest("[data-filter]"), row = e.target.closest(".dc-row"), req = e.target.closest("tr[data-t]");
    if (tab) {
      this.show(tab.dataset.tab);
      this.render(this.t, this.flights);
    } else if (filter) {
      this.filter = filter.dataset.filter;
      this.list.scrollTop = 0;
      this.render(this.t, this.flights);
    } else if (row || e.target.closest(".dc-clear")) {
      this.pick(row && row.dataset.u !== this.sel ? row.dataset.u : null);
    } else if (req) {
      this.el.dispatchEvent(new CustomEvent("airops:seek", { detail: { t: Number(req.dataset.t) }, bubbles: true }));
    }
  }

  /** Pick a crew (null clears): its row and its flights stand out, and the map draws its legs. */
  pick(unit) {
    this.sel = unit;
    this.focus = new Set(unit ? (this.C.legsOf.get(unit) ?? []).map((l) => l.flight) : []);
    this.el.dispatchEvent(new CustomEvent("airops:crew", { detail: { unit, flights: [...this.focus] }, bubbles: true }));
    this.memo = {};
    this.render(this.t, this.flights);
  }

  render(t, flights) {
    if (t == null) return;
    this.t = t;
    this.flights = flights;
    if (this.tab === "duty") this.duty(t, flights);
    else if (this.tab === "reqs") this.requests(t);
    else if (this.tab === "pages") this.pages(t);
    else if (this.tab === "pax") this.passengers(t);
  }

  set(name, key, fn) {
    if (this.memo[name] === key) return false;
    this.memo[name] = key;
    fn();
    return true;
  }

  // Duty clocks

  duty(t, flights) {
    const m = Math.floor(t + 1e-6), C = this.C;
    if (this.memo.dutyAt !== m) {
      this.memo.dutyAt = m;
      this.all = dutiesAt(C, m);
      this.cls = new Map(flights.map((f) => [f.s.id, f.cls]));
    }
    const rows = this.filter === "all" ? this.all : this.all.filter((r) => r.u.kind === this.filter);
    const n = (k) => this.all.filter((r) => r.u.kind === k).length;
    this.set("head", `${m}|${this.filter}|${this.all.length}`, () => {
      const chip = (k, label, c) => `<button type="button" data-filter="${k}" class="${this.filter === k ? "on" : ""}">${label} <b>${c}</b></button>`;
      this.el.querySelector(".dc-head").innerHTML = `<div class="dc-filter">${chip("all", "All", this.all.length)}${chip("pilot", "Pilots", n("pilot"))}${chip("cabin", "Cabin", n("cabin"))}</div>
        <span>crews on duty at ${clock(m)}, by time left to their limit</span>`;
    });
    const span = BACK + AHEAD, lo = m - BACK, x = (v) => Math.max(0, Math.min(100, ((v - lo) / span) * 100));
    this.set("axis", `${m}`, () => {
      const ticks = [];
      for (let h = Math.ceil(lo / 60); h * 60 <= m + AHEAD; h++) {
        if (h % 2) continue;
        ticks.push(`<span style="left:${x(h * 60)}%">${clock(h * 60)}</span>`);
      }
      this.el.querySelector(".dc-axis").innerHTML = `<div>Crew</div><div>Now</div><div class="dc-ticks">${ticks.join("")}<span class="now" style="left:${x(m)}%">${clock(m)}</span></div><div>To limit</div>`;
    });
    const H = this.list.clientHeight || 300, top = this.list.scrollTop, first = Math.max(0, Math.floor(top / ROW) - PAD), last = Math.min(rows.length, Math.ceil((top + H) / ROW) + PAD);
    this.set("rows", `${m}|${this.filter}|${first}|${last}|${this.sel}`, () => {
      this.el.querySelector(".dc-spacer").style.height = `${rows.length * ROW}px`;
      const box = this.el.querySelector(".dc-rows");
      box.style.transform = `translateY(${first * ROW}px)`;
      box.innerHTML = rows.slice(first, last).map((r) => this.row(r, m, x)).join("") || `<p class="xo-quiet">No crew on duty at ${clock(m)}.</p>`;
    });
    this.set("side", `${this.sel}|${this.sel ? m : ""}`, () => (this.el.querySelector(".dc-side").innerHTML = this.sel ? this.crew(this.sel, m) : RULES));
  }

  row({ id, u, d, end, left: rest }, m, x) {
    const P = this.P, seg = (a, b, cls, title, style = "") => `<i class="${cls}" style="left:${x(a)}%;width:${Math.max(0.25, x(b) - x(a))}%${style}" title="${esc(title)}"></i>`;
    const name = (f) => {
      const s = P.sched.get(f);
      return s ? `${s.flight} ${s.orig}–${s.dest}` : `f${f}`;
    };
    const parts = [seg(d.report, end, `span${d.release == null ? " idle" : ""}`, `${unitName(id)}: on duty from ${clock(d.report)} at ${d.at}${d.release != null ? `, released ${clock(d.release)}` : ", no flying left"}`)];
    for (const [f, dep, arr] of d.legs) {
      const cls = this.cls.get(f) ?? "scheduled";
      parts.push(seg(dep, arr, `leg${arr <= m ? " done" : dep <= m ? " now" : ""}`, `${name(f)} ${clock(dep)}–${clock(arr)}`, `;background:${css(cls, dep <= m ? 0.95 : 0.5)}`));
    }
    for (const [f, dep, arr] of d.deadheads) parts.push(seg(dep, arr, `dh${arr <= m ? " done" : ""}`, `Riding ${name(f)} as passengers ${clock(dep)}–${clock(arr)}`));
    parts.push(`<i class="lim" style="left:${x(d.limit)}%" title="Limit: latest legal block-in ${clock(d.limit)}"></i>`);
    if (d.ext != null) parts.push(`<i class="ext" style="left:${x(d.ext)}%" title="With the captain's 2 h extension: ${clock(d.ext)}"></i>`);
    parts.push(`<i class="now" style="left:${x(m)}%"></i>`);
    const w = whereAt(P, d, m), where = w.air ? `<span class="${w.dh ? "dh-t" : "air"}">${w.dh ? "riding" : "flying"} ${esc(w.flight)}</span>` : `at ${esc(w.at)}`;
    const over = d.release != null && d.release > d.limit, tone = rest < 60 ? "red" : rest < 120 ? "amber" : "";
    const lims = `Limit ${clock(d.limit)}${d.ext != null ? `, ${clock(d.ext)} with the extension` : ""}${d.release != null ? `; released ${clock(d.release)}${over ? `, ${left(d.release - d.limit)} into the extension` : ""}` : ""}`;
    const tag = u.reserve ? `<em>${u.reserve === "standby" ? "standby" : "short call"}</em>` : "";
    return `<div class="dc-row${this.sel === id ? " sel" : ""}${this.sel && this.sel !== id ? " dim" : ""}" data-u="${esc(id)}">
      <div class="dc-id"><b>${esc(id)}</b>${tag}<small>${u.home}</small></div><div class="dc-where">${where}</div>
      <div class="dc-track">${parts.join("")}</div>
      <div class="dc-left ${tone}" title="${esc(lims)}">${rest >= 0 ? left(rest) : `−${left(-rest)}`}${rest < 0 || over ? `<small>${d.ext == null ? "over" : rest < 0 ? "in ext" : "needs ext"}</small>` : ""}</div></div>`;
  }

  crew(id, m) {
    const P = this.P, u = this.C.units[id];
    if (!u) return RULES;
    const name = (f) => {
      const s = P.sched.get(f);
      return s ? `<b>${esc(s.flight)}</b> ${esc(s.orig)}–${esc(s.dest)}` : `f${f}`;
    };
    const legs = (d) => [...d.legs.map((l) => [...l, false]), ...d.deadheads.map((l) => [...l, true])].sort((a, b) => a[1] - b[1])
      .map(([f, dep, arr, dh]) => `<li class="${dh ? "dh" : ""}${arr <= m ? " done" : ""}">${clock(dep)}–${clock(arr)} ${name(f)}${dh ? " <i>as passengers</i>" : ""}</li>`).join("");
    const duties = u.duties.map((d) => `<div class="dc-duty${d.report <= m && m < (d.release ?? d.limit) ? " cur" : ""}">
      <p>Reports ${clock(d.report)} at ${esc(d.at)}${d.begins === "rest" ? " after rest" : d.begins === "call" ? " when called" : ""}${d.release != null ? ` · released ${clock(d.release)}` : ""}</p>
      <p class="lims">Limit <b>${clock(d.limit)}</b>${d.ext != null ? ` · with extension <b>${clock(d.ext)}</b>` : ""}</p>
      ${legs(d) ? `<ul>${legs(d)}</ul>` : `<p class="xo-quiet">No legs.</p>`}</div>`).join("");
    const hotels = u.hotels.length ? `<p>Hotel: ${u.hotels.map((h) => `${esc(h.station)} ${clock(h.from)}–${clock(h.to)}`).join(", ")}</p>` : "";
    return `<h5>${esc(unitName(id))} <button type="button" class="dc-clear" title="Clear">Clear</button></h5>
      <p class="dc-who">${esc(id)} · ${u.kind === "pilot" ? "pilots" : "cabin crew"} of the ${esc(u.group)} fleet, ${u.size} people · based ${esc(u.home)}${u.reserve ? ` · ${u.reserve === "standby" ? "standby" : "short-call"} reserve${u.call != null ? `, called ${clock(u.call)}` : ""}` : ""}${u.end ? ` · ends the day at ${esc(u.end)}` : ""}</p>
      ${duties}${hotels}<p class="xo-quiet">${u.kind === "pilot" ? "Limit: FAR 117 Table B; extension: 117.19, up to 2 h." : "Limit: 14 CFR 121.467 scheduled duty."} Its legs are drawn on the map.</p>`;
  }

  // Requests

  requests(t) {
    const reqs = [...(this.occ.requests ?? [])].sort((a, b) => (a.shown ?? a.sent) - (b.shown ?? b.sent));
    const past = reqs.filter((r) => (r.shown ?? r.sent) <= t).length, answered = reqs.filter((r) => r.answered != null && r.answered <= t).length;
    const pane = this.el.querySelector(".xo-reqs");
    const fresh = this.set("reqs", `${past}|${answered}`, () => {
      if (!reqs.length) {
        pane.innerHTML = `<p class="xo-quiet">${this.occ.mode === "one" ? "One agent runs both desks this day, so nothing passes between them." : "No requests between the desks this day."}</p>`;
        return;
      }
      const words = (s) => esc(String(s ?? "").replace(/\bf(\d{1,4})\b/g, (x, id) => this.P.sched.get(Number(id))?.flight ?? x));
      const flight = (id) => {
        const s = this.P.sched.get(id);
        return s ? `<b>${esc(s.flight)}</b><small>${esc(s.orig)}–${esc(s.dest)}</small>` : "";
      };
      const then = (a) => `${SHORT[a.desk] ?? esc(a.desk)} ${VERBS[a.action] ?? esc(a.action)} ${esc(this.P.sched.get(a.flight)?.flight ?? `f${a.flight}`)} at ${clock(a.t)}`;
      const rows = reqs.map((r, i) => {
        const shown = r.shown ?? r.sent, later = shown > t, typed = r.type !== "TEXT", replyLater = r.answered == null || r.answered > t;
        const status = !typed ? `<span class="st delivered">delivered</span>` : r.reply ? `<span class="st ${r.reply}">${r.reply === "accept" ? "Accepted" : "Declined"}</span> ${clock(r.replied)}` : `<span class="st open">No answer</span>`;
        const divider = i === past && past > 0 && past < reqs.length ? `<tr class="rq-now"><td colspan="7"><span>now ${clock(t)}</span></td></tr>` : "";
        return `${divider}<tr class="${later ? "later" : "past"} t-${esc(r.type)}" data-t="${shown}" title="Go to ${clock(shown)}, when it was sent">
          <td class="id">${esc(r.id)}</td><td class="when">${clock(r.sent)}</td><td class="who">${SHORT[r.sender] ?? esc(r.sender)} → ${SHORT[r.receiver] ?? esc(r.receiver)}</td>
          <td class="type">${TYPES[r.type] ?? esc(r.type)}${r.time != null ? `<small>by ${clock(r.time)}</small>` : ""}</td><td class="fl">${r.flight != null ? flight(r.flight) : ""}</td>
          <td class="txt">${words(r.text)}</td>
          <td class="reply${!later && replyLater && typed ? " pending" : ""}">${status}${r.reply_text ? `<div>${words(r.reply_text)}</div>` : ""}${(r.actions ?? [])
            .map((a) => `<div class="then">→ ${then(a)}</div>`).join("")}</td></tr>`;
      }).join("");
      pane.innerHTML = `<table class="rq"><thead><tr><th>Id</th><th>Sent</th><th>From → to</th><th>Type</th><th>Flight</th><th>Request</th><th>Reply and what followed</th></tr></thead><tbody>${rows}</tbody></table>`;
    });
    if (fresh && !this.scrolled) {
      this.scrolled = true;
      const row = pane.querySelectorAll("tr.past").item(past - 1);
      if (row) pane.scrollTop = Math.max(0, row.offsetTop - pane.clientHeight / 2);
    }
  }

  // Passengers

  passengers(t) {
    const X = this.X, P = this.P, a = paxAt(X, t), rows = this.rows ??= disrupted(X, P);
    const past = rows.filter((x) => x.evs[0].t <= t).length;
    const pane = this.el.querySelector(".xo-pax");
    const fresh = this.set("pax", `${Math.floor(t)}|${past}|${a.missed}|${a.rebooked}|${a.stranded}|${a.home}`, () => {
      const fl = (id) => P.sched.get(id)?.flight ?? `f${id}`;
      const body = rows.map((x, i) => {
        const r = x.r ?? {}, first = x.evs[0].t, later = first > t, sum = (k) => x.evs.filter((e) => e.kind === k && e.t <= t).reduce((s, e) => s + e.pax, 0);
        const onto = x.evs.filter((e) => e.kind === "rebook" && e.t <= t).map((e) => `${esc(fl(e.to))} <small>${e.pax}</small>`).join(", ");
        const hotel = sum("hotel"), stations = [...new Set(x.evs.filter((e) => e.kind === "hotel").map((e) => e.at))].join(", ");
        const cancelled = P.exec.get(x.s.id)?.status !== "flown";
        const divider = i === past && past > 0 && past < rows.length ? `<tr class="rq-now"><td colspan="9"><span>now ${clock(t)}</span></td></tr>` : "";
        return `${divider}<tr class="${later ? "later" : "past"}" data-t="${first}" title="Go to ${clock(first)}">
          <td class="fl"><b>${esc(x.s.flight)}</b><small>${esc(x.s.orig)}–${esc(x.s.dest)} · ${clock(x.s.sched_dep)}${cancelled ? " · cancelled" : ""}</small></td>
          <td class="n">${r.booked ?? x.s.pax}${r.added ? `<small>+${r.added} rebooked on</small>` : ""}</td><td class="n">${r.conn_in ?? 0} / ${r.conn_out ?? 0}</td>
          <td class="n">${cancelled ? "–" : r.flown ?? ""}</td><td class="n">${sum("misconnect") || ""}</td><td class="onto">${onto}</td>
          <td class="n">${hotel || ""}${hotel ? `<small>${esc(stations)}</small>` : ""}</td><td class="n">${sum("home") || ""}</td><td class="n">${r.hold ? `${r.hold} min` : ""}</td></tr>`;
      }).join("");
      pane.innerHTML = `<div class="px-head"><span><b>${a.missed.toLocaleString("en-US")}</b> missed connections</span><span><b>${a.rebooked.toLocaleString("en-US")}</b> rebooked onto later flights</span>
        <span><b>${a.stranded.toLocaleString("en-US")}</b> stranded overnight, in hotels</span><span><b>${a.home.toLocaleString("en-US")}</b> went home or took a refund</span>
        ${a.held ? `<span><b>${a.held}</b> departures held for ${a.heldPax} connecting</span>` : ""}<em>by ${clock(t)}</em></div>
        <table class="rq px"><thead><tr><th>Flight</th><th>Booked</th><th>Connecting in / out</th><th>Flew it</th><th>Missed connection</th><th>Rebooked onto</th><th>Hotels</th><th>Home</th><th>Held for them</th></tr></thead><tbody>${body}</tbody></table>`;
    });
    if (fresh && !this.scrolled) {
      this.scrolled = true;
      const row = pane.querySelectorAll("tr.past").item(past - 1);
      if (row) pane.scrollTop = Math.max(0, row.offsetTop - pane.clientHeight / 2);
    }
  }

  // Pages

  pages(t) {
    const occ = this.occ, desks = occ.mode === "one" ? ["one"] : occ.desks?.length ? occ.desks : ["crew", "ops"];
    const latest = desks.map((d) => {
      const turn = (occ.turns ?? []).filter((x) => x.desk === d && (x.from ?? x.t) <= t).at(-1);
      const page = turn ? occ.pages.findLast((p) => p.desk === d && p.watch === turn.watch) ?? occ.pages.findLast((p) => p.desk === d && p.t <= turn.t) : null;
      return { d, turn, page };
    });
    this.set("pages", latest.map((x) => `${x.d}:${x.page?.t}`).join("|"), () => {
      this.el.querySelector(".xo-pages").innerHTML = latest.map(({ d, turn, page }) => `<section>
        <h5>${DESKS[d] ?? esc(d)} ${page ? `<small>the page it read at ${clock(turn.from ?? turn.t)} · watch ${turn.watch + 1}</small>` : ""}</h5>
        ${page ? `<pre>${esc(page.text)}</pre>` : `<p class="xo-quiet">Not at its desk yet${(occ.turns ?? []).find((x) => x.desk === d) ? `: its first turn is at ${clock(occ.turns.find((x) => x.desk === d).from)}` : ""}.</p>`}</section>`).join("");
    });
  }

  /** Marks the picked crew's flights on v1's tail chart. */
  markChart(svg) {
    const on = this.focus?.size > 0;
    svg.classList.toggle("xo-focus", on);
    if (on) for (const r of svg.querySelectorAll("rect.bar[data-id]")) r.classList.toggle("xo-hl", this.focus.has(Number(r.dataset.id)));
  }
}

const RULES = `<h5>The limits</h5>
  <p><b>Pilots</b>: the flight duty period, by report time and number of legs, FAR 117 Table B. The red tick is the latest legal block-in;
  the amber tick, up to 2 h later, is the captain's extension (117.19).</p>
  <p><b>Cabin crew</b>: scheduled duty, 14 CFR 121.467 (14 h on a domestic day). The red tick is the latest legal block-in.</p>
  <p class="dc-key"><i class="k-span"></i>on duty <i class="k-leg"></i>a leg, by status <i class="k-dh"></i>riding as passengers <i class="k-lim"></i>limit <i class="k-ext"></i>extension</p>
  <p class="xo-quiet">Click a crew for its day; its legs are drawn on the map and marked on the tail chart.</p>`;

const STYLE = `
.expert.xo { display: flex; flex-direction: column; }
.xo-tabs { flex: none; display: flex; align-items: center; gap: 2px; padding: 6px 10px 0; border-bottom: 1px solid var(--line); }
.xo-tabs button { border: 0; border-bottom: 2px solid transparent; background: none; padding: 6px 11px 7px; font-size: 12px; color: #9fb5c0; cursor: pointer; }
.xo-tabs button:hover { color: #fff; }
.xo-tabs button.on { color: #fff; border-bottom-color: ${CREW_INK}; font-weight: 650; }
.xo-pane { flex: 1; min-height: 0; }
.xo-quiet { color: var(--faint); font-size: 11.5px; }
.xo-v1 { display: grid; grid-template-columns: minmax(0, 1fr) 450px; }
.xo-duty { display: grid; grid-template-columns: minmax(0, 1fr) 330px; }
.dc-main { display: flex; flex-direction: column; min-height: 0; min-width: 0; }
.dc-head { flex: none; display: flex; align-items: center; gap: 12px; padding: 7px 12px; font-size: 11.5px; color: var(--muted); }
.dc-filter { display: flex; gap: 2px; padding: 2px; border-radius: 6px; background: rgba(255, 255, 255, 0.06); }
.dc-filter button { border: 0; border-radius: 4px; background: none; padding: 3px 9px; font-size: 11.5px; color: #c9d6dc; cursor: pointer; }
.dc-filter button b { font-weight: 650; font-variant-numeric: tabular-nums; color: var(--faint); }
.dc-filter button.on { background: #fff; color: #10202c; }
.dc-filter button.on b { color: #10202c; }
.dc-axis, .dc-row { display: grid; grid-template-columns: 178px 118px minmax(0, 1fr) 104px; align-items: center; }
.dc-axis { flex: none; height: 20px; font-size: 10px; color: var(--faint); text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 1px solid var(--line); }
.dc-axis > div { padding: 0 8px; }
.dc-axis > div:last-child { text-align: right; padding-right: 12px; }
.dc-ticks { position: relative; height: 100%; padding: 0 !important; }
.dc-ticks span { position: absolute; top: 4px; transform: translateX(-50%); font: 10px var(--mono); letter-spacing: 0; text-transform: none; }
.dc-ticks span.now { color: #fff; font-weight: 700; background: rgba(10, 18, 26, 0.97); padding: 0 3px; }
.dc-list { flex: 1; min-height: 0; overflow-y: auto; scrollbar-width: thin; }
.dc-spacer { position: relative; }
.dc-rows { position: absolute; left: 0; right: 0; top: 0; }
.dc-row { height: ${ROW}px; cursor: pointer; font-size: 11px; border-bottom: 1px solid rgba(255, 255, 255, 0.035); }
.dc-row:hover { background: rgba(255, 255, 255, 0.04); }
.dc-row.sel { background: rgba(201, 155, 255, 0.16); box-shadow: inset 3px 0 0 ${CREW_INK}; }
.dc-row.dim { opacity: 0.55; }
.dc-id { padding: 0 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dc-id b { font: 600 11px var(--mono); }
.dc-id em { margin-left: 4px; font-style: normal; font-size: 9.5px; color: #8fd6c4; }
.dc-id small { margin-left: 5px; color: var(--faint); }
.dc-where { padding: 0 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--muted); }
.dc-where .air { color: #fff; }
.dc-where .dh-t { color: ${CREW_INK}; }
.dc-track { position: relative; height: 100%; }
.dc-track i { position: absolute; display: block; }
.dc-track .span { top: 9px; height: 4px; border-radius: 2px; background: rgba(176, 194, 203, 0.32); }
.dc-track .span.idle { background: repeating-linear-gradient(90deg, rgba(176, 194, 203, 0.35) 0 4px, transparent 4px 7px); }
.dc-track .leg { top: 5px; height: 12px; border-radius: 2px; }
.dc-track .leg.now { box-shadow: 0 0 0 1px #fff; }
.dc-track .dh { top: 7px; height: 8px; border-radius: 2px; background: repeating-linear-gradient(135deg, ${CREW_INK} 0 3px, rgba(201, 155, 255, 0.45) 3px 6px); }
.dc-track .lim { top: 2px; bottom: 2px; width: 2px; margin-left: -1px; background: var(--cancel); }
.dc-track .ext { top: 4px; bottom: 4px; width: 0; margin-left: -1px; border-left: 2px dotted var(--delay); }
.dc-track .now { top: 0; bottom: 0; width: 1px; background: rgba(255, 255, 255, 0.75); }
.dc-left { padding-right: 12px; text-align: right; font: 600 11px var(--mono); color: #c9d6dc; white-space: nowrap; }
.dc-left.amber { color: var(--delay); }
.dc-left.red { color: var(--cancel); }
.dc-left small { margin-left: 5px; font: 600 9.5px var(--sans); color: var(--delay); }
.dc-side { min-height: 0; overflow: auto; padding: 10px 14px; border-left: 1px solid var(--line); font-size: 11.5px; color: #c9d6dc; scrollbar-width: thin; }
.dc-side h5, .xo-pages h5 { display: flex; align-items: center; gap: 8px; margin: 0 0 6px; font-size: 10.5px; font-weight: 650; letter-spacing: 0.07em; text-transform: uppercase; color: #9fb5c0; }
.dc-side h5 small, .xo-pages h5 small { text-transform: none; letter-spacing: 0; font-weight: 400; color: var(--faint); }
.dc-side p { margin: 0 0 7px; line-height: 1.45; }
.dc-side b { color: #fff; font-weight: 600; }
.dc-clear { margin-left: auto; border: 1px solid var(--line); border-radius: 4px; background: none; padding: 1px 7px; font-size: 10.5px; color: #c9d6dc; cursor: pointer; text-transform: none; letter-spacing: 0; }
.dc-who { color: var(--muted); }
.dc-duty { margin: 0 0 8px; padding: 6px 8px; border-radius: 5px; background: rgba(255, 255, 255, 0.035); }
.dc-duty.cur { box-shadow: inset 2px 0 0 ${CREW_INK}; background: rgba(201, 155, 255, 0.08); }
.dc-duty p { margin: 0 0 3px; }
.dc-duty .lims { color: var(--muted); }
.dc-duty ul { list-style: none; margin: 4px 0 0; padding: 0; font: 10.5px/1.6 var(--mono); }
.dc-duty li.done { color: var(--faint); }
.dc-duty li.dh { color: ${CREW_INK}; }
.dc-duty li i { font: italic 10.5px var(--sans); }
.dc-key { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; color: var(--muted); }
.dc-key i { display: inline-block; width: 16px; height: 8px; border-radius: 2px; margin-left: 4px; }
.dc-key .k-span { height: 4px; background: rgba(176, 194, 203, 0.4); }
.dc-key .k-leg { background: ${css("ontime", 0.95)}; }
.dc-key .k-dh { background: repeating-linear-gradient(135deg, ${CREW_INK} 0 3px, rgba(201, 155, 255, 0.45) 3px 6px); }
.dc-key .k-lim { width: 2px; height: 12px; background: var(--cancel); }
.dc-key .k-ext { width: 0; height: 12px; border-left: 2px dotted var(--delay); border-radius: 0; }
.xo-reqs, .xo-pax { overflow: auto; scrollbar-width: thin; }
.px-head { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 18px; padding: 8px 10px; font-size: 12px; color: #c9d6dc;
  background: #0d1720; border-bottom: 1px solid var(--line); }
.px-head b { font-size: 15px; color: ${PAX_INK}; font-variant-numeric: tabular-nums; }
.px-head em { margin-left: auto; font-style: normal; color: var(--faint); }
table.px th { top: 37px; }
table.px td.n { white-space: nowrap; font: 600 11.5px var(--mono); }
table.px td small { display: block; font: 400 10.5px var(--sans); color: var(--faint); }
table.px td.onto { font-family: var(--mono); font-size: 11px; }
table.px td.onto small { display: inline; }
table.rq { width: 100%; border-collapse: collapse; font-size: 12px; }
table.rq th { position: sticky; top: 0; z-index: 1; padding: 6px 8px; text-align: left; font-size: 10px; font-weight: 650; letter-spacing: 0.07em; text-transform: uppercase;
  color: #9fb5c0; background: #0d1720; border-bottom: 1px solid var(--line); }
table.rq td { padding: 7px 8px; vertical-align: top; border-bottom: 1px solid rgba(255, 255, 255, 0.06); line-height: 1.45; }
table.rq tr[data-t] { cursor: pointer; }
table.rq tr[data-t]:hover td { background: rgba(255, 255, 255, 0.04); }
table.rq tr.later td { opacity: 0.42; }
table.rq td.id, table.rq td.when { font: 600 11px var(--mono); white-space: nowrap; }
table.rq td.who, table.rq td.type { white-space: nowrap; }
table.rq td.type small, table.rq td.fl small { display: block; color: var(--faint); }
table.rq td.fl { white-space: nowrap; font-family: var(--mono); }
table.rq td.txt { width: 46%; min-width: 300px; color: #e6eef1; }
table.rq td.reply { width: 38%; min-width: 260px; color: #b9c9d0; }
table.rq td.reply div { margin-top: 3px; }
table.rq td.reply div.then { color: #fff; font-weight: 600; }
table.rq td.reply.pending { opacity: 0.45; }
table.rq tr.t-CANCEL td.type, table.rq tr.t-CANCEL_PAIR td.type { color: #ffb0a8; }
table.rq tr.t-HOLD td.type { color: #a9d0ff; }
table.rq tr.t-COVER td.type { color: ${CREW_INK}; }
table.rq .st { display: inline-block; padding: 0 5px; border-radius: 3px; font-size: 10.5px; font-weight: 650; }
table.rq .st.accept { background: rgba(90, 208, 143, 0.2); color: var(--ok); }
table.rq .st.decline { background: rgba(255, 106, 92, 0.2); color: var(--cancel); }
table.rq .st.delivered { color: var(--faint); font-weight: 500; padding: 0; }
table.rq .st.open { background: rgba(243, 177, 63, 0.2); color: var(--delay); }
table.rq tr.rq-now td { padding: 3px 8px; border-bottom: 0;
  background: linear-gradient(transparent calc(50% - 1px), rgba(255, 255, 255, 0.85) calc(50% - 1px), rgba(255, 255, 255, 0.85) calc(50% + 1px), transparent calc(50% + 1px)); }
table.rq tr.rq-now span { display: inline-block; padding: 1px 6px; font: 700 10px var(--mono); background: #fff; color: #10202c; border-radius: 3px; }
.xo-pages { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr); }
.xo-pages section { display: flex; flex-direction: column; min-height: 0; min-width: 0; padding: 10px 12px 0; border-right: 1px solid var(--line); }
.xo-pages section:last-child { border-right: 0; }
.xo-pages pre { flex: 1; min-height: 0; overflow: auto; margin: 0; padding: 0 0 10px; font: 11px/1.45 var(--mono); white-space: pre-wrap; color: #d3dee3; scrollbar-width: thin; }
svg.tails.xo-focus rect.bar:not(.xo-hl) { opacity: 0.3; }
svg.tails rect.bar.xo-hl { stroke: ${CREW_INK}; stroke-width: 2; opacity: 1; }
`;

function injectStyle() {
  if (document.getElementById("xo-style")) return;
  const el = document.createElement("style");
  el.id = "xo-style";
  el.textContent = STYLE;
  document.head.appendChild(el);
}
