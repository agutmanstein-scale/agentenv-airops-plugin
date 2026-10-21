// The customer desk's three monitors on a day with passengers, display-only, in type big enough to read over the desk's
// shoulder: the connections at risk as its latest page listed them (those not yet gone), the passengers rebooked onto a
// later flight and waiting for it, and the passengers stranded overnight by airport.
import { PAX_INK, atRisk, strandedPaxAt, waitingAt } from "../paxdata.js";
import { clock } from "../plain.js";

const FONT = "Inter, 'Helvetica Neue', Arial, sans-serif";
const ROWS = 7;
const TONE = { miss: "#ff8a7a", tight: "#f3b13f", wait: "#d3dde6", night: PAX_INK };

export function paxDesk(P, X) {
  const gone = (id, t) => (P.exec.get(id)?.dep ?? Infinity) <= t;
  const route = (s) => `${s.flight} ${s.orig}–${s.dest}`;
  const content = (t, page) => {
    const risk = atRisk(page?.text);
    const open = risk.rows.filter((r) => !gone(r.id, t) && P.sched.has(r.id));
    const waiting = waitingAt(X, P, t);
    const night = [...strandedPaxAt(X, t)].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    const sum = (xs, f) => xs.reduce((a, x) => a + f(x), 0);
    return [
      { head: "Connections at risk", note: page ? `page at ${clock(page.t)}` : "", empty: page ? "None at risk now" : "Waiting for the first watch",
        title: `${sum(open, (r) => r.pax)} connecting on ${open.length} departure${open.length === 1 ? "" : "s"}`,
        rows: open.map((r) => ({ left: `${route(P.sched.get(r.id))} ${r.dep}`, right: r.misses ? `${r.misses} miss` : `${r.pax} tight`, tone: r.misses ? "miss" : "tight" })),
        extra: risk.more },
      { head: "Rebooked, waiting to fly", note: clock(t), empty: "Nobody waiting on a new flight",
        title: `${sum(waiting, (w) => w.pax)} on ${waiting.length} later flight${waiting.length === 1 ? "" : "s"}`,
        rows: waiting.map((w) => ({ left: `${route(P.sched.get(w.to))}${w.dep != null ? ` ${clock(w.dep)}` : ""}`, right: `${w.pax}`, tone: "wait" })), extra: 0 },
      { head: "Stranded overnight", note: clock(t), empty: "Nobody stranded yet",
        title: `${sum(night, (x) => x[1]).toLocaleString("en-US")} with no seat today`,
        rows: night.map(([code, n]) => ({ left: code, right: n.toLocaleString("en-US"), tone: "night" })), extra: 0 },
    ];
  };
  let key = null, shown = null;
  return {
    /** Repaint the monitors when what they show at t has changed; whether it did. */
    update(screens, t, page, dims) {
      const k = `${page?.t}|${Math.floor(t)}`;
      if (k === key) return false;
      key = k;
      const c = content(t, page), s = JSON.stringify(c);
      if (s === shown) return false;
      shown = s;
      paint(screens, c, dims);
      return true;
    },
  };
}

function paint(screens, content, { w, h, pad, top, size, line }) {
  content.forEach((c, i) => {
    const g = screens[i].g, W = w - 2 * pad;
    g.fillStyle = "#0b1016";
    g.fillRect(0, 0, w, h);
    g.fillStyle = "#33270f";
    g.fillRect(0, 0, w, 40);
    g.font = `600 26px ${FONT}`;
    g.textBaseline = "middle";
    g.fillStyle = "#f0d9a8";
    g.fillText(`Customer desk · ${c.head.toLowerCase()}`, pad, 21);
    g.textAlign = "right";
    g.fillStyle = "#b39a6c";
    g.fillText(c.note, w - pad, 21);
    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    let y = top + line;
    g.font = `700 ${size}px ${FONT}`;
    g.fillStyle = PAX_INK;
    g.fillText(c.rows.length ? c.title : c.empty, pad, y, W);
    y += line + 6;
    g.font = `500 ${size}px ${FONT}`;
    const fit = c.rows.length + c.extra > ROWS ? ROWS - 1 : ROWS, more = Math.max(0, c.rows.length - fit) + c.extra;
    for (const r of c.rows.slice(0, fit)) {
      g.fillStyle = "#d3dde6";
      g.fillText(r.left, pad, y, W - 190);
      g.textAlign = "right";
      g.fillStyle = TONE[r.tone];
      g.font = `700 ${size}px ${FONT}`;
      g.fillText(r.right, w - pad, y);
      g.font = `500 ${size}px ${FONT}`;
      g.textAlign = "left";
      y += line;
    }
    if (more) {
      g.fillStyle = "#7d8fa1";
      g.fillText(`+ ${more} more`, pad, y);
    }
    screens[i].tex.needsUpdate = true;
  });
}
