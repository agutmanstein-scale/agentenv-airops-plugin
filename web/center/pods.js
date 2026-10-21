// The desks the model works, display-only: the ops controller's and the crew scheduler's pods at the centre of the
// front row, with the customer desk's beside the ops controller on a day with passengers (one pod on a one-agent day),
// each with a seated figure, a name board on the monitor arm and three monitors
// showing, in type big enough to read over the desk's shoulder, the page that desk read at the start of its latest turn:
// its header and summary, then the first items of its first two sections. The desk whose turn it is lights up.
import * as THREE from "three";
import { clock } from "../plain.js";
import { unitName } from "../room.js";
import { ROWS, chairGeometry, deskGeometry, monitorMatrices, personGeometry, screenMatrix, stationMatrix } from "./hall.js";

export const DESKS = { ops: "Ops controller", crew: "Crew scheduler", customer: "Customer desk", one: "Ops controller · crew scheduler" };
const ALL = "Ops · crew · customer desks";
const LOOKS = {
  ops: { shirt: 0x24476b, skin: 0xd1a585, hair: 0x4a3426, style: "short" },
  crew: { shirt: 0x5a3f5e, skin: 0xb5835f, hair: 0x1b1410, style: "long" },
  customer: { shirt: 0x2e5f5a, skin: 0x8d5f43, hair: 0x15100c, style: "bun" },
  one: { shirt: 0x24476b, skin: 0xd1a585, hair: 0x4a3426, style: "short" },
};
const POD_W = 2.4;
export const POD_GAP = 3.0;
const SCALE = 1.25;
const FONT = "Inter, 'Helvetica Neue', Arial, sans-serif";
const PAGE = { w: 1024, h: 586, pad: 22, top: 50 };
const TEXT = { size: 44, line: 53 };
const EMPTY = /^(none|none new)\.?$/i;

const clamp01 = (x) => Math.max(0, Math.min(1, x));

/** Lines of at most `width` pixels in the context's current font, the rest dropped after `most` lines with an ellipsis. */
function wrap(g, text, width, most = Infinity) {
  const out = [];
  let line = "";
  for (const word of String(text).split(/\s+/).filter(Boolean)) {
    const next = line ? `${line} ${word}` : word;
    if (g.measureText(next).width <= width || !line) line = next;
    else {
      out.push(line);
      line = word;
    }
  }
  if (line) out.push(line);
  if (out.length > most) {
    out.length = most;
    out[most - 1] = `${out[most - 1].replace(/\s*\S*$/, "")}…`;
  }
  return out;
}

/** A desk's page as parts: its title ("Crew scheduler · 14:00 · watch 11 of 37"), the rest of the header, the summary
 * paragraph and the sections that have items, flights and crews said in words. */
function parse(text, words) {
  const lines = String(text ?? "").split("\n").map((l) => l.trim());
  const head = (lines.find((l) => l.startsWith("# ")) ?? "").slice(2);
  const m = /^([^,]+), [^,]+, [\d-]+, ([\d:+-]+) \(watch (\d+) of (\d+)[^)]*\);?\s*(.*)$/.exec(head);
  const title = m ? `${m[1]} · ${m[2]} · watch ${m[3]} of ${m[4]}` : head;
  const sections = [];
  const summary = [];
  let cur = null;
  for (const l of lines) {
    if (!l || l.startsWith("# ")) continue;
    if (l.startsWith("## ")) sections.push((cur = { title: l.slice(3).replace(/\s*\(.*\)\s*$/, ""), items: [] }));
    else if (cur) cur.items.push(words(l));
    else summary.push(words(l));
  }
  return { title, note: m ? m[5] : "", summary, sections: sections.filter((x) => x.items.some((i) => !EMPTY.test(i))) };
}

function signCanvas(title, sub) {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = 192;
  const g = c.getContext("2d");
  g.fillStyle = "#0e1620";
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = "#2e6da4";
  g.fillRect(0, c.height - 10, c.width, 10);
  g.fillStyle = "#f2f5f8";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.font = `700 ${title.length > 22 ? 52 : 70}px ${FONT}`;
  g.fillText(title.toUpperCase(), c.width / 2, 76);
  g.font = `500 36px ${FONT}`;
  g.fillStyle = "#93a9be";
  g.fillText(sub, c.width / 2, 146);
  return c;
}

class Pod {
  constructor(group, desk, s, { label, pages, turns, words, title = DESKS[desk] }) {
    this.desk = desk;
    this.title = title;
    this.words = words;
    this.pages = pages;
    this.turns = turns;
    const row = ROWS[0], m = stationMatrix(row, s);
    this.m = m;
    const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.7, metalness: 0.05 });
    const deskMesh = new THREE.Mesh(deskGeometry(POD_W, { monitors: 3, scale: SCALE }), mat);
    deskMesh.applyMatrix4(m);
    group.add(deskMesh);
    const chairAt = m.clone().multiply(new THREE.Matrix4().makeTranslation(0, 0, 0.5));
    const chair = new THREE.Mesh(chairGeometry(), mat);
    chair.applyMatrix4(chairAt);
    group.add(chair);
    const person = personGeometry(LOOKS[desk]);
    const body = new THREE.Mesh(person.body, mat);
    body.applyMatrix4(chairAt);
    group.add(body);
    this.head = new THREE.Mesh(person.head, mat);
    this.headBase = chairAt.clone().multiply(new THREE.Matrix4().makeTranslation(...person.headAt));
    this.head.matrixAutoUpdate = false;
    group.add(this.head);

    this.screens = monitorMatrices(3, SCALE).map((mm) => {
      const c = document.createElement("canvas");
      c.width = PAGE.w;
      c.height = PAGE.h;
      const tex = new THREE.CanvasTexture(c);
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = 8;
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
      mesh.applyMatrix4(m.clone().multiply(screenMatrix(mm, SCALE)));
      group.add(mesh);
      return { c, g: c.getContext("2d"), tex, mesh };
    });

    const glow = new THREE.MeshBasicMaterial({ color: 0x000000, toneMapped: false });
    const strip = new THREE.Mesh(new THREE.BoxGeometry(POD_W - 0.1, 0.012, 0.012).translate(0, 0.715, -0.01), glow);
    strip.applyMatrix4(m);
    group.add(strip);
    this.glow = glow;

    const sign = new THREE.Group();
    const signTex = new THREE.CanvasTexture(signCanvas(title, label));
    signTex.colorSpace = THREE.SRGBColorSpace;
    signTex.anisotropy = 8;
    this.signMat = new THREE.MeshBasicMaterial({ map: signTex, toneMapped: false, color: 0x9a9a9a });
    const sw = desk === "one" ? 1.6 : 1.15, sh = sw * 0.1875;
    sign.add(new THREE.Mesh(new THREE.BoxGeometry(sw + 0.04, sh + 0.04, 0.04), new THREE.MeshStandardMaterial({ color: 0x0b0d10 })));
    for (const side of [1, -1]) {
      const face = new THREE.Mesh(new THREE.PlaneGeometry(sw, sh), this.signMat);
      face.position.z = side * 0.022;
      if (side < 0) face.rotation.y = Math.PI;
      sign.add(face);
    }
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.3), new THREE.MeshStandardMaterial({ color: 0x2a2d31 }));
    post.position.y = -sh / 2 - 0.12;
    sign.add(post);
    sign.position.set(0, 1.78, -0.66);
    sign.applyMatrix4(m);
    group.add(sign);

    this.light = new THREE.PointLight(0xffe2b8, 0, 4.5, 1.6);
    this.light.position.set(0, 1.9, 0.1).applyMatrix4(m);
    group.add(this.light);
    const lamp = new THREE.PointLight(0xa8c4ff, 0.6, 2.2, 2);
    lamp.position.set(0, 1.2, -0.25).applyMatrix4(m);
    group.add(lamp);
    this.lamp = lamp;

    this.anchor = new THREE.Vector3(0, 2.3, -0.3).applyMatrix4(m);
    this.points = [[-1.05, 0.95, -0.5], [1.05, 0.95, -0.5], [-1.05, 1.45, -0.5], [1.05, 1.45, -0.5], [0, 1.35, 0.55], [0, 0.75, 0.75], [0, 2.02, -0.66]]
      .map((p) => new THREE.Vector3(...p).applyMatrix4(m));
    this.key = null;
    this.paint(null);
  }

  /** The page this desk had up at t: the one of its latest turn begun by then. */
  pageAt(t) {
    let best = null;
    for (const p of this.pages) if ((p.from ?? p.t) <= t && (!best || p.t >= best.t)) best = p;
    return best;
  }

  paint(page) {
    const doc = page ? parse(page.text, this.words) : null;
    const [first, second] = doc?.sections ?? [];
    this.screens.forEach((sc, i) => {
      const g = sc.g, W = PAGE.w - 2 * PAGE.pad;
      g.fillStyle = "#0a1017";
      g.fillRect(0, 0, PAGE.w, PAGE.h);
      g.fillStyle = "#14263a";
      g.fillRect(0, 0, PAGE.w, 40);
      g.font = `600 26px ${FONT}`;
      g.fillStyle = "#a9c3dc";
      g.textBaseline = "middle";
      g.fillText(`${this.title}${page ? ` · page at ${clock(page.t)}` : ""}`, PAGE.pad, 21);
      g.textBaseline = "alphabetic";
      let y = PAGE.top + TEXT.line;
      const room = () => y < PAGE.h - 10;
      const put = (text, { font = `400 ${TEXT.size}px ${FONT}`, color = "#d3dde6", most = 3, indent = 0 } = {}) => {
        g.font = font;
        g.fillStyle = color;
        for (const line of wrap(g, text, W - indent, most)) {
          if (!room()) return;
          g.fillText(line, PAGE.pad + indent, y);
          y += TEXT.line;
        }
      };
      if (!doc) {
        if (i === 1) put("Waiting for the first watch", { color: "#5d7187" });
      } else if (i === 0) {
        put(doc.title, { font: `700 ${TEXT.size}px ${FONT}`, color: "#ffd27a", most: 2 });
        if (doc.note) put(doc.note, { color: "#9fb2c4", most: 2 });
        y += 6;
        for (const para of doc.summary) put(para, { most: 4 });
      } else {
        const sec = i === 1 ? first : second;
        if (!sec) put(i === 1 ? "Nothing flagged this turn" : "", { color: "#5d7187" });
        else {
          put(sec.title, { font: `700 ${TEXT.size}px ${FONT}`, color: "#7fc4ff", most: 1 });
          y += 4;
          for (const item of sec.items.filter((x) => !EMPTY.test(x))) {
            if (!room()) break;
            put(`· ${item}`, { most: 2 });
          }
        }
      }
      sc.tex.needsUpdate = true;
    });
  }

  /** The turn's glow (0 to 1) at t: up while this desk works, eased at its ends. */
  activity(t) {
    let best = 0;
    for (const u of this.turns) if (u.from - 0.6 <= t && t < u.to + 0.6) best = Math.max(best, clamp01((t - u.from + 0.6) / 0.6) * clamp01((u.to + 0.6 - t) / 0.6));
    return best;
  }

  update(t, wall) {
    const page = this.pageAt(t), key = page ? `${page.t}|${page.desk}` : "none";
    if (key !== this.key) {
      this.key = key;
      this.paint(page);
    }
    const a = this.activity(t);
    for (const sc of this.screens) sc.mesh.material.color.setScalar(0.55 + 0.45 * a);
    this.light.intensity = 3.2 * a;
    this.glow.color.setRGB(0.25 * a, 0.55 * a, 0.95 * a);
    this.signMat.color.setScalar(0.6 + 0.4 * a);
    const yaw = Math.sin(wall * 0.37 + this.anchor.x) * 0.25 * (1 - a) + Math.sin(wall * 0.9) * 0.08 * a;
    const nod = -0.12 * a + Math.sin(wall * 2.1) * 0.025 * a;
    this.head.matrix.copy(this.headBase).multiply(new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(nod, yaw, 0, "YXZ")));
    return a;
  }
}

export function buildPods(P, ep) {
  const occ = ep.occ, group = new THREE.Group();
  const flight = (id) => P.sched.get(Number(id))?.flight;
  const words = (text) => text
    .replace(/\bf(\d{1,4}) (\d{1,4})\b/g, (m, id, n) => (flight(id)?.endsWith(` ${n}`) ? flight(id) : m))
    .replace(/\bf(\d{1,4})\b/g, (m, id) => flight(id) ?? m)
    .replace(/\bR?[PC]-[A-Z0-9]+-(?:[A-Z]{3}-[AS]\d+|\d+)\b/g, (id) => unitName(id));
  const label = ep.model_label ?? ep.model ?? "";
  const one = occ.mode === "one" || (occ.desks ?? []).length === 1;
  const three = !one && (occ.desks ?? []).includes("customer");
  const desks = one ? ["one"] : three ? ["customer", "ops", "crew"] : ["ops", "crew"];
  const at = one ? [0] : three ? [-POD_GAP, 0, POD_GAP] : [-1.3, 1.3];
  const from = new Map(occ.turns.map((u) => [`${u.t}|${u.desk}`, u.from]));
  const pods = new Map(desks.map((d, i) => {
    const pages = occ.pages.filter((p) => one || p.desk === d).map((p) => ({ ...p, from: from.get(`${p.t}|${p.desk}`) ?? p.t }));
    const turns = occ.turns.filter((u) => one || u.desk === d);
    const title = one && ep.passengers ? ALL : DESKS[d];
    return [d, new Pod(group, d, at[i], { label, pages, turns, words, title })];
  }));
  return {
    group,
    pods,
    desks,
    /** The desk whose turn it is at t, or null. */
    active(t) {
      const u = occ.turns.find((x) => x.from <= t && t < x.to);
      return u ? (one ? "one" : u.desk) : null;
    },
    update(t, wall) {
      for (const p of pods.values()) p.update(t, wall);
    },
  };
}
