// The two desks the model works, display-only: the ops controller's and the crew scheduler's pods at the centre of the
// front row (one pod on a one-agent day), each with a seated figure, a hanging name sign and three monitors showing the
// page that desk read at the start of its latest turn. The desk whose turn it is gets its lamp and screens up.
import * as THREE from "three";
import { clock } from "../plain.js";
import { ROWS, chairGeometry, deskGeometry, monitorMatrices, personGeometry, screenMatrix, stationMatrix } from "./hall.js";

export const DESKS = { ops: "Ops controller", crew: "Crew scheduler", one: "Ops controller · crew scheduler" };
const SHIRTS = { ops: 0x24476b, crew: 0x5a3f5e, one: 0x24476b };
const POD_W = 2.4;
const SCALE = 1.15;
const FONT = "Inter, 'Helvetica Neue', Arial, sans-serif";
const MONO = "'SF Mono', Menlo, Consolas, monospace";
const PAGE = { w: 1024, h: 586, line: 17, cols: 104 };

const clamp01 = (x) => Math.max(0, Math.min(1, x));

function wrap(text, cols) {
  const out = [];
  for (const raw of String(text ?? "").split("\n")) {
    let line = raw;
    while (line.length > cols) {
      const cut = line.lastIndexOf(" ", cols) > cols * 0.6 ? line.lastIndexOf(" ", cols) : cols;
      out.push(line.slice(0, cut));
      line = `  ${line.slice(cut).trimStart()}`;
    }
    out.push(line);
  }
  return out;
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
  constructor(group, desk, s, { label, pages, turns }) {
    this.desk = desk;
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
    const person = personGeometry({ shirt: SHIRTS[desk], skin: desk === "crew" ? 0xb5835f : 0xd1a585, hair: desk === "crew" ? 0x1b1410 : 0x5a4030 });
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
    const signTex = new THREE.CanvasTexture(signCanvas(DESKS[desk], label));
    signTex.colorSpace = THREE.SRGBColorSpace;
    signTex.anisotropy = 8;
    this.signMat = new THREE.MeshBasicMaterial({ map: signTex, toneMapped: false, color: 0x9a9a9a });
    const sw = desk === "one" ? 1.9 : 1.45, sh = sw * 0.1875;
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
    sign.position.set(0, 1.83, -0.66);
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
    const lines = page ? wrap(page.text, PAGE.cols) : [];
    const per = Math.floor((PAGE.h - 40) / PAGE.line);
    this.screens.forEach((sc, i) => {
      const g = sc.g;
      g.fillStyle = "#0a1017";
      g.fillRect(0, 0, PAGE.w, PAGE.h);
      g.fillStyle = "#14263a";
      g.fillRect(0, 0, PAGE.w, 30);
      g.font = `600 17px ${FONT}`;
      g.fillStyle = "#a9c3dc";
      g.textBaseline = "middle";
      g.fillText(`${DESKS[this.desk]}${page ? ` · page at ${clock(page.t)}` : ""} · ${i + 1}/3`, 12, 15);
      if (!page) {
        g.font = `500 28px ${FONT}`;
        g.fillStyle = "#5d7187";
        g.fillText(i === 1 ? "Waiting for the first watch" : "", 40, PAGE.h / 2);
      }
      g.textBaseline = "alphabetic";
      lines.slice(i * per, (i + 1) * per).forEach((line, k) => {
        const y = 40 + (k + 1) * PAGE.line - 4;
        if (line.startsWith("## ")) {
          g.font = `700 15px ${MONO}`;
          g.fillStyle = "#7fc4ff";
          line = line.slice(3);
        } else if (line.startsWith("# ")) {
          g.font = `700 15px ${MONO}`;
          g.fillStyle = "#ffd27a";
          line = line.slice(2);
        } else {
          g.font = `400 15px ${MONO}`;
          g.fillStyle = "#c3cfda";
        }
        g.fillText(line, 12, y);
      });
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

export function buildPods(ep) {
  const occ = ep.occ, group = new THREE.Group();
  const label = ep.model_label ?? ep.model ?? "";
  const one = occ.mode === "one" || (occ.desks ?? []).length === 1;
  const desks = one ? ["one"] : ["ops", "crew"];
  const from = new Map(occ.turns.map((u) => [`${u.t}|${u.desk}`, u.from]));
  const pods = new Map(desks.map((d, i) => {
    const pages = occ.pages.filter((p) => one || p.desk === d).map((p) => ({ ...p, from: from.get(`${p.t}|${p.desk}`) ?? p.t }));
    const turns = occ.turns.filter((u) => one || u.desk === d);
    return [d, new Pod(group, d, one ? 0 : i === 0 ? -1.3 : 1.3, { label, pages, turns })];
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
