import * as THREE from "three";

const TONES = { ok: "#5ad08f", delay: "#f3b13f", hold: "#5aa8ff", cancel: "#ff6a5c", crew: "#c99bff" };
const FADE = [
  [5000, 12000, 0.65],
  [2200, 6500, 0],
  [700, 1900, 0],
];
const MAX = 7;
const CSS = `
.ao-tags { position: absolute; inset: 0; pointer-events: none; overflow: hidden; z-index: 5; }
.ao-tag {
  position: absolute; left: 0; top: 0; white-space: nowrap; will-change: transform, opacity;
  padding: 3px 9px 3px 11px; border-radius: 5px; color: #f2f6f8;
  font: 500 11.5px/1.35 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; letter-spacing: 0.01em;
  background: rgba(10, 24, 34, 0.8); border: 1px solid rgba(176, 203, 213, 0.18);
  box-shadow: 0 3px 14px rgba(4, 25, 39, 0.28); backdrop-filter: blur(6px); transition: opacity 0.25s linear;
}
.ao-tag::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; border-radius: 5px 0 0 5px; background: var(--tone); }
.ao-tag::after { content: ""; position: absolute; left: var(--tick, 50%); top: 100%; width: 1px; height: var(--drop, 8px); background: rgba(199, 228, 237, 0.55); }
.ao-tag b { font-weight: 650; }
.ao-tag i { font-style: normal; color: #c3d6de; }
.ao-tag.featured { font-size: 13px; padding: 4px 11px 4px 13px; background: rgba(8, 20, 30, 0.9); border-color: rgba(214, 236, 242, 0.32); }
`;

/** HTML tags over the task's aircraft: each sits just above its fin wherever the camera is, the featured one first and never covered, the rest fanned out or dropped where they would overlap; avoid() gives the container's rects [x0, y0, x1, y1] the page covers, which the tags keep out of. */
export function buildLabels(container, { occluders = null, avoid = null } = {}) {
  if (!document.getElementById("ao-tags-css")) {
    const style = document.createElement("style");
    style.id = "ao-tags-css";
    style.textContent = CSS;
    document.head.appendChild(style);
  }
  const layer = document.createElement("div");
  layer.className = "ao-tags";
  container.appendChild(layer);
  const els = new Map();
  const hidden = new Map();
  const ray = new THREE.Raycaster();
  const cam = new THREE.Vector3();
  const v = new THREE.Vector3();
  const dir = new THREE.Vector3();
  let turn = 0;
  let frame = 0;
  let blocked = [];

  const element = (it) => {
    let el = els.get(it.key);
    if (!el) {
      el = document.createElement("div");
      el.className = "ao-tag";
      layer.appendChild(el);
      els.set(it.key, el);
    }
    const sig = `${it.text}|${it.tone}|${!!it.featured}`;
    if (el._sig !== sig) {
      const cut = it.text.indexOf(" · ");
      const b = document.createElement("b");
      b.textContent = cut < 0 ? it.text : it.text.slice(0, cut);
      el.replaceChildren(b);
      if (cut >= 0) {
        const i = document.createElement("i");
        i.textContent = it.text.slice(cut);
        el.appendChild(i);
      }
      el.classList.toggle("featured", !!it.featured);
      el.style.setProperty("--tone", TONES[it.tone] || TONES.ok);
      el.hidden = false;
      el._sig = sig;
      el._w = el.offsetWidth;
      el._h = el.offsetHeight;
    }
    return el;
  };

  const occluded = (key, d) => {
    if (!occluders || !occluders.length || d < 80) return false;
    if (!hidden.has(key) || turn++ % 6 === 0) {
      dir.copy(v).sub(cam).normalize();
      ray.set(cam, dir);
      ray.far = d - 6;
      hidden.set(key, ray.intersectObjects(occluders, true).length > 0);
    }
    return hidden.get(key);
  };

  return {
    setOccluders(list) {
      occluders = list;
      hidden.clear();
    },
    dispose() {
      layer.remove();
      els.clear();
    },
    update(camera, renderer, items) {
      const canvas = renderer.domElement;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      camera.getWorldPosition(cam);
      if (avoid && frame++ % 10 === 0) blocked = avoid();
      const clear = (r) => !blocked.some((b) => r[0] < b[2] && r[2] > b[0] && r[1] < b[3] && r[3] > b[1]);
      const want = [];
      const live = new Set();
      for (const it of items) {
        live.add(it.key);
        if (!it.text) continue;
        v.set(0, it.dims.height + 0.6, it.dims.fin).applyMatrix4(it.matrix);
        const d = v.distanceTo(cam);
        const prio = it.priority ?? (it.featured ? 0 : it.moving ? 1 : 2);
        const [near, far, floor] = FADE[Math.min(prio, 2)];
        const alpha = Math.max(floor, Math.min(1, (far - d) / (far - near)));
        if (alpha < 0.04) continue;
        if (prio > 0 && occluded(it.key, d)) continue;
        v.project(camera);
        if (v.z > 1 || v.z < -1) continue;
        const sx = (v.x * 0.5 + 0.5) * w;
        const sy = (-v.y * 0.5 + 0.5) * h;
        if (sx < -60 || sx > w + 60 || sy < -10 || sy > h + 80) continue;
        want.push({ it, prio, d, sx, sy, alpha });
      }
      want.sort((a, b) => a.prio - b.prio || a.d - b.d);
      if (want.some((c) => !c.prio)) want.splice(0, want.length, ...want.filter((c) => c.prio < 2));
      const placed = [];
      const shown = new Set();
      const free = (r) => !placed.some((p) => {
        const pad = p.featured ? 8 : 0;
        return r[0] < p[2] + pad && r[2] > p[0] - pad && r[1] < p[3] + pad && r[3] > p[1] - pad;
      });
      for (const c of want) {
        if (placed.length >= MAX) break;
        const el = element(c.it);
        const tries = [[0, 0], [-0.5, -1], [0.5, -1], [0, -1.15], [-0.62, 0], [0.62, 0]];
        let spot = null;
        for (const [fx, fy] of tries) {
          const lx = Math.max(4, Math.min(w - el._w - 4, c.sx - el._w / 2 + fx * el._w));
          const ly = Math.max(4, c.sy - el._h - 9 + fy * (el._h + 6));
          const r = [lx - 3, ly - 3, lx + el._w + 3, ly + el._h + 3];
          if (!c.prio && !spot) spot = { lx, ly, r };
          if ((!c.prio || free(r)) && clear(r)) {
            spot = { lx, ly, r };
            break;
          }
        }
        if (!spot) continue;
        spot.r.featured = !c.prio;
        placed.push(spot.r);
        shown.add(c.it.key);
        el.hidden = false;
        el.style.transform = `translate3d(${Math.round(spot.lx)}px, ${Math.round(spot.ly)}px, 0)`;
        el.style.setProperty("--tick", `${Math.round(Math.max(6, Math.min(el._w - 6, c.sx - spot.lx)))}px`);
        el.style.setProperty("--drop", `${Math.max(8, Math.round(c.sy - spot.ly - el._h))}px`);
        el.style.opacity = c.alpha.toFixed(2);
        el.style.zIndex = String(30 - c.prio * 10 - Math.min(9, Math.floor(c.d / 1000)));
      }
      for (const [key, el] of els) {
        if (!live.has(key)) {
          el.remove();
          els.delete(key);
          hidden.delete(key);
        } else if (!shown.has(key)) el.hidden = true;
      }
    },
  };
}
