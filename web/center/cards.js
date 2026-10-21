// What passes between the desks in the room, display-only: each request rises at the sender's pod when the room shows it
// sent, flies on an arc to the receiver's pod and waits there; its answer flies back stamped ACCEPTED or DECLINED (a
// TEXT is a message and gets none). Each decision pops as a chip beside the desk that made it. Camera-facing sprites
// with canvas text; a card's canvas is drawn once, the first time it shows.
import * as THREE from "three";
import { clock } from "../plain.js";
import { unitName } from "../room.js";

const TYPES = { CANCEL: "Cancel", CANCEL_PAIR: "Cancel round trip", HOLD: "Hold", COVER: "Cover", TEXT: "Message", PROTECT: "Protect", SUGGEST_CANCEL: "Suggest cancelling" };
const STRIPE = { CANCEL: "#d9483b", CANCEL_PAIR: "#d9483b", HOLD: "#2a78d6", COVER: "#8a5cd6", TEXT: "#7d8a99", PROTECT: "#1f9d5c", SUGGEST_CANCEL: "#d9483b" };
const SHORT = { ops: "Ops", crew: "Crew", customer: "Customer", one: "Desk" };
const DOT = { cancel: "#ff6a5c", cancel_pair: "#ff6a5c", hold: "#5aa8ff", claim: "#4fc3c8", release: "#9fb6cc", assign: "#b08cf0",
  call_reserve: "#b08cf0", deadhead: "#b08cf0", swap: "#b08cf0", rest: "#b08cf0", rebook: "#e9c27a", hotel: "#e9c27a" };
const FONT = "Inter, 'Helvetica Neue', Arial, sans-serif";
const RISE = 1.2, FLY = 4.5, FADE = 1.5, TEXT_REST = 12, OPEN_REST = 20, REPLY_REST = 9;
const CHIP_LIFE = 9, CHIP_STAGGER = 0.7, CHIPS = 5;
const CARD = { w: 1.2, h: 0.3, px: 820 };
const CHIP = { w: 1.2, h: 0.15, px: 720 };

const clamp01 = (x) => Math.max(0, Math.min(1, x));
const ease = (u) => u * u * (3 - 2 * u);

function sprite(draw, { w, h, px }) {
  const c = document.createElement("canvas");
  c.width = px;
  c.height = Math.round((px * h) / w);
  draw(c.getContext("2d"), c.width, c.height);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, depthWrite: false, toneMapped: false }));
  s.scale.set(w, h, 1);
  s.renderOrder = 20;
  return s;
}

function fit(g, text, max) {
  let s = text;
  while (s.length > 4 && g.measureText(s).width > max) s = s.slice(0, -2);
  return s === text ? s : `${s.trimEnd()}…`;
}

/** Each card's slot (0, 1, ...) so that cards up at the same time stack instead of overlapping. */
function slots(items) {
  const ends = [];
  for (const it of [...items].sort((a, b) => a.start - b.start)) {
    let k = ends.findIndex((e) => e <= it.start);
    if (k < 0) k = ends.push(0) - 1;
    ends[k] = it.end;
    it.slot = k;
  }
}

export function buildCards(P, ep, pods) {
  const occ = ep.occ, group = new THREE.Group();
  const flight = (id) => {
    const s = P.sched.get(id);
    return s ? `${s.flight} ${s.orig}–${s.dest}` : `f${id}`;
  };
  const number = (id) => P.sched.get(id)?.flight ?? `f${id}`;
  const words = (text) => String(text ?? "").replace(/\bf(\d{1,4})\b/g, (m, id) => (P.sched.has(Number(id)) ? P.sched.get(Number(id)).flight : m));
  const at = (desk) => pods.get(desk)?.anchor ?? pods.values().next().value.anchor;

  const cards = [];
  for (const r of occ.requests ?? []) {
    if (r.shown == null || !pods.has(r.sender) || !pods.has(r.receiver)) continue;
    const typed = r.type !== "TEXT", answered = typed ? r.answered ?? null : null;
    const end = answered != null ? answered + FADE : r.shown + RISE + FLY + (typed ? OPEN_REST : TEXT_REST);
    cards.push({ r, kind: "ask", from: r.sender, to: r.receiver, start: r.shown, end: Math.max(end, r.shown + RISE + FLY + 1) });
    if (answered != null && r.reply) cards.push({ r, kind: "reply", from: r.receiver, to: r.sender, start: answered, end: answered + RISE + FLY + REPLY_REST });
  }
  slots(cards);

  const drawAsk = (r) => (g, w, h) => {
    g.fillStyle = "#f4efe2";
    g.beginPath();
    g.roundRect(4, 4, w - 8, h - 8, 18);
    g.fill();
    g.fillStyle = STRIPE[r.type] ?? "#7d8a99";
    g.fillRect(4, 22, 14, h - 44);
    g.fillStyle = "#1d232a";
    g.textBaseline = "middle";
    g.font = `700 43px ${FONT}`;
    const head = `${r.id} · ${TYPES[r.type] ?? r.type}${r.flight != null ? ` · ${flight(r.flight)}` : ""}`;
    g.fillText(fit(g, head, w - 70), 40, h * 0.36);
    g.font = `500 32px ${FONT}`;
    g.fillStyle = "#5b6672";
    const sub = r.type === "TEXT" ? `${SHORT[r.sender]} → ${SHORT[r.receiver]} · ${words(r.text)}` : `${SHORT[r.sender]} → ${SHORT[r.receiver]}${r.pax ? ` · ${r.pax} ${r.type === "HOLD" ? "connecting" : "booked"}` : ""} · sent ${clock(r.sent)}${r.time != null ? ` · by ${clock(r.time)}` : ""}`;
    g.fillText(fit(g, sub, w - 70), 40, h * 0.72);
  };
  const drawReply = (r) => (g, w, h) => {
    const ok = r.reply === "accept", col = ok ? "#1f9d5c" : "#d9483b";
    g.fillStyle = "#f4efe2";
    g.beginPath();
    g.roundRect(4, 4, w - 8, h - 8, 18);
    g.fill();
    g.fillStyle = "#1d232a";
    g.textBaseline = "middle";
    g.font = `700 46px ${FONT}`;
    g.fillText(fit(g, `${r.id}${r.flight != null ? ` · ${number(r.flight)}` : ""}`, w * 0.4), 34, h * 0.36);
    g.font = `500 32px ${FONT}`;
    g.fillStyle = "#5b6672";
    g.fillText(`${SHORT[r.receiver]} → ${SHORT[r.sender]} · ${clock(r.replied ?? r.sent)}`, 34, h * 0.72);
    g.save();
    g.translate(w * 0.72, h * 0.5);
    g.rotate(-0.08);
    g.strokeStyle = col;
    g.lineWidth = 8;
    g.font = `800 58px ${FONT}`;
    const word = ok ? "ACCEPTED" : "DECLINED", tw = g.measureText(word).width;
    g.strokeRect(-tw / 2 - 18, -42, tw + 36, 84);
    g.fillStyle = col;
    g.textAlign = "center";
    g.fillText(word, 0, 4);
    g.restore();
  };

  const decision = (c) => {
    const f = c.flight != null ? number(c.flight) : null, unit = unitName(c.detail);
    switch (c.action) {
      case "cancel": return `Cancelled ${f}`;
      case "cancel_pair": return `Cancelled ${f} and its return`;
      case "hold": return `Held ${f}${c.detail ? ` to ${c.detail}` : ""}`;
      case "claim": return `Took a slot for ${f}${c.detail ? ` at ${c.detail}` : ""}`;
      case "swap_slots": return `Swapped slots on ${f}`;
      case "assign": return `Put ${unit} on ${f}`;
      case "call_reserve": return `Called ${unit}${f ? ` for ${f}` : ""}`;
      case "deadhead": return `${unit[0].toUpperCase()}${unit.slice(1)} ride ${f}`;
      case "swap": return `Swapped ${unit} onto ${f}`;
      case "rest": return `Sent ${unit} to rest`;
      case "rebook": return `Rebooked ${c.pax ?? ""} off ${f}${c.to != null ? ` onto ${number(c.to)}` : ""}`.replace("  ", " ");
      case "hotel": return `Hotels for ${c.pax ?? ""} off ${f}${c.at ? ` in ${c.at}` : ""}`.replace("  ", " ");
      case "release": return c.desk === "ops" ? `Let ${f} go when it can` : f ? `Took ${unit} off ${f}` : `Released ${unit}`;
      default: return `${c.action}${f ? ` ${f}` : ""}`;
    }
  };
  const drawChip = (c) => (g, w, h) => {
    g.fillStyle = "rgba(10,16,24,0.86)";
    g.beginPath();
    g.roundRect(2, 2, w - 4, h - 4, h / 2 - 2);
    g.fill();
    g.strokeStyle = "rgba(160,190,220,0.35)";
    g.lineWidth = 2;
    g.stroke();
    g.fillStyle = DOT[c.action] ?? "#9fb6cc";
    g.beginPath();
    g.arc(h * 0.55, h / 2, h * 0.18, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#eef3f7";
    g.font = `600 ${Math.round(h * 0.5)}px ${FONT}`;
    g.textBaseline = "middle";
    g.fillText(fit(g, decision(c), w - h * 1.2), h * 0.95, h / 2 + 1);
  };

  const byDesk = new Map();
  for (const c of occ.decisions ?? []) {
    const d = pods.has(c.desk) ? c.desk : pods.keys().next().value;
    if (!byDesk.has(d)) byDesk.set(d, []);
    byDesk.get(d).push(c);
  }
  const chips = [];
  for (const [d, list] of byDesk) {
    const same = new Map();
    for (const c of list.sort((a, b) => (a.shown ?? a.t) - (b.shown ?? b.t))) {
      const s = c.shown ?? c.t, k = same.get(s) ?? 0;
      same.set(s, k + 1);
      chips.push({ c, desk: d, start: s + k * CHIP_STAGGER, end: s + k * CHIP_STAGGER + CHIP_LIFE });
    }
  }
  for (const d of byDesk.keys()) slots(chips.filter((x) => x.desk === d));

  const live = new Map();
  const halo = haloTexture();
  const side = (desk) => (desk === "crew" || pods.size === 3 ? 1 : -1);
  const a = new THREE.Vector3();
  const place = (it, t, out) => {
    const u = t - it.start, rise = clamp01(u / RISE), fly = ease(clamp01((u - RISE) / FLY));
    out.lerpVectors(a.copy(at(it.from)), at(it.to), fly);
    out.y += it.slot * (CARD.h + 0.12) + Math.sin(Math.PI * fly) * (0.1 + 0.08 * Math.max(0, at(it.from).distanceTo(at(it.to)) - 3)) - 0.3 * (1 - rise);
    return Math.min(rise, clamp01((it.end - t) / FADE));
  };
  let marked = null;
  return {
    group,
    size: CARD,
    /** Mark a request's cards (its ask and its answer) to stand out, or none. */
    mark(ref) {
      marked = ref ?? null;
    },
    /** Where a request's cards are between minutes t0 and t1 (every half minute), for a shot to keep them in frame. */
    track(ref, t0, t1) {
      const out = [], mine = cards.filter((it) => it.r.id === ref);
      for (const it of mine) {
        const lo = Math.max(t0, it.start + 0.3), hi = Math.min(t1, it.end - 0.3);
        for (let t = lo; t <= hi + 1e-6; t += Math.max(0.5, (hi - lo) / 24)) {
          const p = new THREE.Vector3();
          if (place(it, t, p) > 0.05) out.push(p);
        }
      }
      return out;
    },
    /** The minutes a request's cards are up, [start, end], or null. */
    span(ref) {
      const mine = cards.filter((it) => it.r.id === ref);
      return mine.length ? [Math.min(...mine.map((it) => it.start)), Math.max(...mine.map((it) => it.end))] : null;
    },
    /** Place the cards and chips up at minute t; a marked card grows to stay readable from the camera. Returns how many cards are up. */
    update(t, camera) {
      let n = 0;
      const seen = new Set();
      for (const it of cards) {
        if (t < it.start || t >= it.end) continue;
        seen.add(it);
        let s = live.get(it);
        if (!s) {
          s = sprite(it.kind === "ask" ? drawAsk(it.r) : drawReply(it.r), CARD);
          s.halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: halo, transparent: true, depthTest: false, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false }));
          s.halo.renderOrder = 19;
          s.add(s.halo);
          live.set(it, s);
          group.add(s);
        }
        const alpha = place(it, t, s.position), hot = it.r.id === marked;
        let k = 1;
        if (hot && camera) {
          const h = 2 * camera.position.distanceTo(s.position) * Math.tan((camera.fov * Math.PI) / 360);
          k = Math.max(1.25, Math.min(2.6, (0.075 * h) / CARD.h));
        }
        s.scale.set(CARD.w * k, CARD.h * k, 1);
        s.renderOrder = hot ? 22 : 20;
        s.material.opacity = alpha;
        s.halo.visible = hot;
        s.halo.scale.set(1.16, 1.5, 1);
        s.halo.material.opacity = alpha * 0.9;
        n++;
      }
      for (const it of chips) {
        if (t < it.start || t >= it.end) continue;
        const k = it.slot ?? 0;
        if (k >= CHIPS) continue;
        seen.add(it);
        let s = live.get(it);
        if (!s) {
          s = sprite(drawChip(it.c), CHIP);
          live.set(it, s);
          group.add(s);
        }
        const u = t - it.start, pod = pods.get(it.desk);
        s.position.copy(pod.anchor);
        s.position.x += side(it.desk) * (pods.size > 1 ? 1.05 : 1.6);
        s.position.y += -0.5 + k * (CHIP.h + 0.04) + 0.12 * clamp01(u / CHIP_LIFE);
        s.material.opacity = Math.min(clamp01(u / 0.5), clamp01((it.end - t) / 2));
      }
      for (const [it, s] of live) {
        s.visible = seen.has(it);
        if (!s.visible && (t < it.start - 60 || t > it.end + 60)) {
          group.remove(s);
          s.material.map.dispose();
          s.material.dispose();
          s.halo?.material.dispose();
          live.delete(it);
        }
      }
      return n;
    },
  };
}

function haloTexture() {
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 128;
  const g = c.getContext("2d");
  g.filter = "blur(10px)";
  g.fillStyle = "rgba(255,214,140,0.85)";
  g.beginPath();
  g.roundRect(22, 22, 212, 84, 16);
  g.fill();
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}
