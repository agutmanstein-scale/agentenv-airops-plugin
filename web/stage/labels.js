import * as THREE from "three";

const TONES = { ok: "#5ad08f", delay: "#f3b13f", hold: "#5aa8ff", cancel: "#ff6a5c" };
const FADE = [
  [5000, 12000, 0.65],
  [2200, 6500, 0],
  [1100, 3200, 0],
];
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
.ao-tag::after { content: ""; position: absolute; left: var(--tick, 50%); bottom: -8px; width: 1px; height: 8px; background: rgba(199, 228, 237, 0.55); }
.ao-tag b { font-weight: 650; }
.ao-tag i { font-style: normal; color: #c3d6de; }
.ao-tag.featured { font-size: 13px; padding: 4px 11px 4px 13px; background: rgba(8, 20, 30, 0.9); border-color: rgba(214, 236, 242, 0.32); }
`;

export function buildLabels(container, { occluders = null } = {}) {
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
  const seen = new Map();
  const anchors = new WeakMap();
  const hidden = new Map();
  const ray = new THREE.Raycaster();
  const cam = new THREE.Vector3();
  const v = new THREE.Vector3();
  const wp = new THREE.Vector3();
  const dir = new THREE.Vector3();
  let turn = 0;

  const anchorOf = (object) => {
    let a = anchors.get(object);
    if (!a) {
      const d = object.userData.dims;
      if (d) a = new THREE.Vector3(0, d.height + 8, d.length * 0.45);
      else {
        object.updateWorldMatrix(true, true);
        const box = new THREE.Box3().setFromObject(object).applyMatrix4(object.matrixWorld.clone().invert());
        a = new THREE.Vector3((box.min.x + box.max.x) / 2, box.max.y + 8, (box.min.z + box.max.z) / 2);
      }
      anchors.set(object, a);
    }
    return a;
  };

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
    if (!hidden.has(key) || (turn++ % 6 === 0)) {
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
      const want = [];
      const live = new Set();
      for (const it of items) {
        live.add(it.key);
        const o = it.object;
        if (!o || !o.visible || !it.text) continue;
        o.updateWorldMatrix(true, false);
        const pos = o.getWorldPosition(wp);
        const prev = seen.get(it.key);
        const moving = it.moving ?? (prev ? prev.distanceToSquared(pos) > 0.0025 : false);
        if (prev) prev.copy(pos);
        else seen.set(it.key, pos.clone());
        v.copy(anchorOf(o));
        o.localToWorld(v);
        const d = v.distanceTo(cam);
        const prio = it.priority ?? (it.featured ? 0 : moving ? 1 : 2);
        const [near, far, floor] = FADE[Math.min(prio, 2)];
        const alpha = Math.max(floor, Math.min(1, (far - d) / (far - near)));
        if (alpha < 0.04) continue;
        if (prio > 0 && occluded(it.key, d)) continue;
        v.project(camera);
        if (v.z > 1 || v.z < -1) continue;
        const sx = (v.x * 0.5 + 0.5) * w;
        const sy = (-v.y * 0.5 + 0.5) * h;
        if (sx < -60 || sx > w + 60 || sy < -10 || sy > h + 60) continue;
        want.push({ it, prio, d, sx, sy, alpha });
      }
      want.sort((a, b) => a.prio - b.prio || a.d - b.d);
      const placed = [];
      const shown = new Set();
      for (const c of want) {
        const el = element(c.it);
        const lx = Math.max(4, Math.min(w - el._w - 4, c.sx - el._w / 2));
        const ly = c.sy - el._h - 8;
        const r = [lx - 3, ly - 3, lx + el._w + 3, ly + el._h + 3];
        if (c.prio > 0 && placed.some((p) => r[0] < p[2] && r[2] > p[0] && r[1] < p[3] && r[3] > p[1])) continue;
        placed.push(r);
        shown.add(c.it.key);
        el.hidden = false;
        el.style.transform = `translate3d(${Math.round(lx)}px, ${Math.round(ly)}px, 0)`;
        el.style.setProperty("--tick", `${Math.round(Math.max(6, Math.min(el._w - 6, c.sx - lx)))}px`);
        el.style.opacity = c.alpha.toFixed(2);
        el.style.zIndex = String(30 - c.prio * 10 - Math.min(9, Math.floor(c.d / 1000)));
      }
      for (const [key, el] of els) {
        if (!live.has(key)) {
          el.remove();
          els.delete(key);
          seen.delete(key);
          hidden.delete(key);
        } else if (!shown.has(key)) el.hidden = true;
      }
    },
  };
}
