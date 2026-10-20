// The control-center card of a two- or three-desk day (episode.occ): the requests between the ops controller, the crew
// scheduler and the customer desk as they are sent and answered, each desk's decisions in time order and the note it left at the end of its
// last turn, as the room shows them (each watch's turns spread over the hour after it). Flights read as their numbers; crews by their ids, said in words.
import { esc } from "./model.js";
import { clock } from "./plain.js";

const DESKS = { ops: "Ops controller", crew: "Crew scheduler", customer: "Customer desk", one: "Both desks" };
const SHORT = { ops: "Ops", crew: "Crew", customer: "Customer", one: "Both" };
const TYPES = { CANCEL: "Cancel", CANCEL_PAIR: "Cancel round trip", HOLD: "Hold", COVER: "Cover", TEXT: "Message" };
const FRESH = 30;
const REQUESTS = 3;
const DECISIONS = 3;

/** A crew unit in words: "pilots 0413", "cabin crew 0341", "BOS standby reserve pilots", "BOS short-call reserve cabin crew". */
export function unitName(id) {
  if (!id) return "a crew";
  const r = /^R([PC])-[A-Z0-9]+-([A-Z]{3})-([AS])\d+$/.exec(id);
  if (r) return `${r[2]} ${r[3] === "A" ? "standby" : "short-call"} reserve ${r[1] === "P" ? "pilots" : "cabin crew"}`;
  const u = /^([PC])-[A-Z0-9]+-(\d+)$/.exec(id);
  return u ? `${u[1] === "P" ? "pilots" : "cabin crew"} ${u[2]}` : id;
}

/** Whether a request has its answer by t: when the room shows it, else at the minute it was given. */
const done = (r, t) => (r.answered != null ? r.answered <= t : r.replied != null && r.replied <= t);

export class Room {
  constructor(el, P, { model }) {
    this.el = el;
    this.P = P;
    this.occ = P.ep.occ;
    this.model = model;
    this.memo = new Map();
    const pax = !!P.ep.passengers, three = this.occ.mode !== "one" && (this.occ.desks ?? []).includes("customer");
    this.desks = this.occ.mode === "one" ? ["one"] : three ? ["ops", "crew", "customer"] : ["ops", "crew"];
    this.rows = three ? 2 : DECISIONS;
    this.asks = three ? 2 : REQUESTS;
    el.classList.add("room");
    el.innerHTML = `<div class="room-h"><b>The ops control center</b><span>${esc(this.occ.mode === "one"
      ? pax ? `${model} runs every desk: flights, crews and passengers` : `${model} runs both desks: flights and crews`
      : three ? `${model} at three desks: the ops controller runs the flights, the crew scheduler the crews, the customer desk the passengers`
        : `${model} at two desks: the ops controller runs the flights, the crew scheduler the crews`)}</span></div>
      <div data-s="reqs"></div>${this.desks.map((d) => `<div data-s="desk-${d}"></div>`).join("")}`;
    el.addEventListener("click", (e) => e.target.closest(".reqs li")?.classList.toggle("open"));
  }

  slot(name, key, html) {
    if (this.memo.get(name) === key) return;
    this.memo.set(name, key);
    this.el.querySelector(`[data-s="${name}"]`).innerHTML = html();
  }

  flight(id) {
    const s = this.P.sched.get(id);
    return s ? `${s.flight} ${s.orig}–${s.dest}` : `f${id}`;
  }

  words(text) {
    return String(text ?? "").replace(/\bf(\d{1,4})\b/g, (m, id) => (this.P.sched.has(Number(id)) ? this.P.sched.get(Number(id)).flight : m));
  }

  render(t) {
    const m = Math.floor(t + 1e-6), occ = this.occ;
    if (occ.mode !== "one") {
      const sent = occ.requests.filter((r) => (r.shown ?? r.sent) <= t), answered = sent.filter((r) => done(r, t)).length;
      this.slot("reqs", `${sent.length}|${answered}|${Math.floor(m / FRESH)}`, () => this.requests(sent, t));
    } else this.slot("reqs", "one", () => "");
    for (const d of this.desks) {
      const made = occ.decisions.filter((c) => (c.shown ?? c.t) <= t && (d === "one" || c.desk === d));
      const note = (occ.notes ?? []).filter((n) => (n.shown ?? n.t) <= t && (d === "one" || n.desk === d)).at(-1);
      this.slot(`desk-${d}`, `${made.length}|${note?.t}|${note?.desk}|${Math.floor(m / FRESH)}`, () => this.decisions(d, made, note, t));
    }
  }

  requests(sent, t) {
    if (!sent.length) return `<h5>Between the desks</h5><p class="quiet">No requests yet.</p>`;
    const items = [...sent].sort((a, b) => (a.shown ?? a.sent) - (b.shown ?? b.sent)).slice(-this.asks).reverse().map((r) => {
      const typed = r.type !== "TEXT", answered = done(r, t);
      const reply = !typed ? "" : answered
        ? `<div class="reply ${esc(r.reply)}"><b>${r.reply === "accept" ? "Accepted" : "Declined"}</b> by ${SHORT[r.receiver]} at ${clock(r.replied)}${r.reply_text ? ` · ${esc(this.words(r.reply_text))}` : ""}</div>`
        : `<div class="reply wait">Waiting for ${SHORT[r.receiver]}'s answer</div>`;
      const what = r.flight != null ? ` · ${esc(this.flight(r.flight))}` : "";
      const when = `${r.time != null ? ` by ${clock(r.time)}` : ""}${typed && r.pax ? ` · ${r.pax} connecting` : ""}`;
      return `<li class="t-${esc(r.type)}${t - (r.shown ?? r.sent) < FRESH ? " fresh" : ""}" title="Click for the whole message">
        <span class="when">${clock(r.sent)} · ${SHORT[r.sender]} → ${SHORT[r.receiver]} · ${esc(r.id)}</span>
        <b>${TYPES[r.type] ?? esc(r.type)}${what}${when}</b><div class="txt">${esc(this.words(r.text))}</div>${reply}</li>`;
    }).join("");
    const open = sent.filter((r) => r.type !== "TEXT" && !done(r, t)).length;
    return `<h5>Between the desks <small>${sent.length} sent${open ? ` · ${open} waiting` : ""}</small></h5><ul class="reqs">${items}</ul>`;
  }

  decisions(d, made, note, t) {
    const items = made.slice(-this.rows).reverse().map((c) => `<li class="d-${esc(c.action)}${t - (c.shown ?? c.t) < FRESH ? " fresh" : ""}"><span class="t">${clock(c.t)}</span>${esc(this.decision(c))}</li>`).join("");
    const said = note ? `<blockquote><span>${clock(note.t)}</span> ${esc(this.words(note.note).slice(0, 240))}</blockquote>` : "";
    return `<h5>${DESKS[d]} <small>${made.length ? `${made.length} decision${made.length === 1 ? "" : "s"} so far` : "no decisions yet"}</small></h5>
      ${items ? `<ul class="decs">${items}</ul>` : ""}${said}`;
  }

  decision(c) {
    const f = c.flight != null ? this.flight(c.flight) : null, unit = unitName(c.detail), asked = c.ref ? ` (request ${c.ref})` : "";
    switch (c.action) {
      case "cancel":
        return `Cancelled ${f}${c.via === "accept" ? ", as the crew desk asked" : ""}${asked}`;
      case "cancel_pair":
        return `Cancelled the round trip of ${f}${asked}`;
      case "hold":
        return `Held ${f}${c.detail ? ` until ${c.detail}` : ""}${asked}`;
      case "claim":
        return `Took a slot for ${f}${c.detail ? ` at ${c.detail}` : ""}${asked}`;
      case "swap_slots":
        return `Swapped slots on ${f}${asked}`;
      case "assign":
        return `Put ${unit} on ${f}${asked}`;
      case "call_reserve":
        return `Called ${unit}${f ? ` for ${f}` : ""}${asked}`;
      case "deadhead":
        return `Flew ${unit} as passengers on ${f}${asked}`;
      case "swap":
        return `Swapped ${unit} onto ${f}'s trip${asked}`;
      case "rest":
        return `Sent ${unit} to the hotel to rest${asked}`;
      case "release":
        return c.desk === "ops" ? `Let ${f} leave when it can${asked}` : f ? `Took ${unit} off ${f}${asked}` : `Released ${unit}${asked}`;
      case "rebook":
        return `Rebooked ${c.pax != null ? `${c.pax} ` : ""}off ${f}${c.to != null ? ` onto ${this.P.sched.get(c.to)?.flight ?? `f${c.to}`}` : ""}`;
      case "hotel":
        return `Hotels for ${c.pax != null ? `${c.pax} ` : ""}off ${f}${c.at ? ` in ${c.at}` : ""}`;
      default:
        return `${c.action}${f ? ` ${f}` : ""}${c.detail ? ` ${c.detail}` : ""}`;
    }
  }
}
