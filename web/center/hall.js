// The control-center hall, display-only: a dim room with a carpeted floor, an acoustic ceiling with recessed lights and
// slatted side walls, and tiered rows of curved workstations facing the video wall (instanced desks, monitors, chairs
// and a few seated people). Metres, y up; the video wall hangs on the front wall at z = 0 and faces +z.
import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

export const ROOM = { w: 30, d: 22, h: 6.6 };
const ARC_Z = -18;
export const ROWS = [
  { r: 24, y: 0, slots: [3.4, 5.2, 7.6, 9.4] },
  { r: 27.4, y: 0.32, slots: [0.9, 2.7, 4.5, 7.0, 8.8, 10.6] },
  { r: 30.8, y: 0.64, slots: [0.9, 2.7, 4.5, 7.0, 8.8, 10.6] },
  { r: 34.2, y: 0.96, slots: [0.9, 2.7, 4.5, 7.0, 8.8, 10.6] },
];
const STATION_W = 1.8;
const MONITOR = { w: 0.62, h: 0.37, sw: 0.585, sh: 0.335 };

/** A random stream from a seed, so the room is the same on every load. */
function rng(seed = 7) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

/** The matrix of a workstation at arc length s along a row, turned to face the wall's centre of curvature. */
export function stationMatrix(row, s) {
  const a = s / row.r;
  const m = new THREE.Matrix4().makeRotationY(a);
  m.setPosition(row.r * Math.sin(a), row.y, ARC_Z + row.r * Math.cos(a));
  return m;
}

function paint(geo, hex) {
  const c = new THREE.Color(hex), n = geo.attributes.position.count, a = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) c.toArray(a, i * 3);
  geo.setAttribute("color", new THREE.BufferAttribute(a, 3));
  return geo;
}

const box = (w, h, d, [x, y, z], hex, { rx = 0, ry = 0 } = {}) => paint(new THREE.BoxGeometry(w, h, d).rotateX(rx).rotateY(ry).translate(x, y, z), hex);

function limb(a, b, r, hex) {
  const A = new THREE.Vector3(...a), B = new THREE.Vector3(...b);
  const g = new THREE.CapsuleGeometry(r, A.distanceTo(B), 3, 8);
  g.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), B.clone().sub(A).normalize()));
  return paint(g.translate((A.x + B.x) / 2, (A.y + B.y) / 2, (A.z + B.z) / 2), hex);
}

const ball = (r, [x, y, z], hex, [sx, sy, sz] = [1, 1, 1]) => paint(new THREE.SphereGeometry(r, 12, 9).scale(sx, sy, sz).translate(x, y, z), hex);

/** A seated office worker facing -z, low-poly, at a chair placed at the origin; head separate so it can move. */
export function personGeometry({ shirt = 0x3b4a5c, trousers = 0x23272d, skin = 0xc69a7c, hair = 0x2a211c } = {}) {
  const body = [];
  for (const x of [-0.1, 0.1]) {
    body.push(limb([x, 0.53, 0.1], [x * 1.1, 0.54, -0.3], 0.075, trousers));
    body.push(limb([x * 1.1, 0.5, -0.33], [x * 1.1, 0.1, -0.37], 0.056, trousers));
    body.push(box(0.1, 0.07, 0.25, [x * 1.1, 0.035, -0.43], 0x16181b));
    body.push(limb([x * 1.9, 1.04, 0.11], [x * 2.2, 0.83, 0.02], 0.05, shirt));
    body.push(limb([x * 2.2, 0.83, 0.02], [x * 1.4, 0.8, -0.26], 0.043, shirt));
    body.push(ball(0.045, [x * 1.35, 0.795, -0.3], skin));
  }
  body.push(paint(new THREE.CapsuleGeometry(0.15, 0.3, 3, 10).scale(1.22, 1, 0.78).rotateX(-0.12).translate(0, 0.84, 0.1), shirt));
  body.push(paint(new THREE.CylinderGeometry(0.045, 0.05, 0.12, 8).translate(0, 1.1, 0.07), skin));
  const head = mergeGeometries([ball(0.088, [0, 0, 0], skin, [0.92, 1.12, 1]), ball(0.092, [0, 0.028, 0.02], hair, [0.96, 0.92, 1])]);
  return { body: mergeGeometries(body), head, headAt: [0, 1.22, 0.055] };
}

/** An office chair facing -z with its seat centre over the origin. */
export function chairGeometry() {
  const c = 0x1d2024, parts = [
    box(0.5, 0.07, 0.48, [0, 0.465, 0.08], c),
    box(0.46, 0.58, 0.06, [0, 0.84, 0.35], c, { rx: 0.1 }),
    box(0.06, 0.32, 0.04, [0, 0.55, 0.36], 0x2b2f34),
    paint(new THREE.CylinderGeometry(0.028, 0.028, 0.36, 8).translate(0, 0.25, 0.1), 0x3a3e44),
  ];
  for (const x of [-0.26, 0.26]) parts.push(box(0.05, 0.03, 0.26, [x, 0.67, 0.1], c), box(0.03, 0.18, 0.03, [x, 0.58, 0.14], c));
  for (let k = 0; k < 5; k++) {
    const a = (k / 5) * Math.PI * 2;
    parts.push(box(0.3, 0.03, 0.045, [0, 0, 0], c).translate(0.15, 0, 0).rotateY(a).translate(0, 0.06, 0.1));
  }
  return mergeGeometries(parts);
}

/** A desk of width w with its user's edge on the origin, reaching 0.75 m toward the wall (-z); monitor arms and a keyboard. */
export function deskGeometry(w = STATION_W, { monitors = 3, scale = 1 } = {}) {
  const top = 0x41464c, panel = 0x24282d, dark = 0x15171a;
  const parts = [
    box(w, 0.035, 0.75, [0, 0.74, -0.375], top),
    box(w, 0.012, 0.03, [0, 0.758, -0.015], 0x3c4248),
    box(w, 0.62, 0.025, [0, 0.4, -0.72], panel),
    box(0.03, 0.72, 0.72, [-w / 2 + 0.015, 0.36, -0.37], panel),
    box(0.03, 0.72, 0.72, [w / 2 - 0.015, 0.36, -0.37], panel),
    box(0.44, 0.02, 0.14, [0, 0.768, -0.2], dark),
    box(0.06, 0.02, 0.1, [0.32, 0.768, -0.2], dark),
    box(0.16, 0.05, 0.12, [-w / 2 + 0.25, 0.78, -0.32], 0x202327),
    paint(new THREE.CylinderGeometry(0.018, 0.018, 0.48, 8).translate(0, 0.98, -0.64), 0x2a2d31),
    box(0.22, 0.012, 0.16, [0, 0.763, -0.64], 0x2a2d31),
  ];
  for (const m of monitorMatrices(monitors, scale)) {
    parts.push(paint(new THREE.BoxGeometry(MONITOR.w * scale, MONITOR.h * scale, 0.035).applyMatrix4(m), dark));
    parts.push(paint(new THREE.BoxGeometry(0.12, 0.1, 0.03).translate(0, 0, -0.035).applyMatrix4(m), 0x2a2d31));
  }
  return mergeGeometries(parts);
}

/** Where a desk's monitors sit (centres, facing the user), left to right. */
export function monitorMatrices(n = 3, scale = 1) {
  const out = [], gap = MONITOR.w * scale + 0.015;
  for (let k = 0; k < n; k++) {
    const i = k - (n - 1) / 2, yaw = -i * 0.38;
    const m = new THREE.Matrix4().makeRotationY(yaw);
    m.setPosition(i * gap * Math.cos(yaw * 0.5), 0.96 + (MONITOR.h * scale) / 2, -0.6 + Math.abs(i) * 0.12);
    out.push(m);
  }
  return out;
}

export function screenMatrix(m, scale = 1) {
  return m.clone().multiply(new THREE.Matrix4().makeTranslation(0, 0, 0.0185)).multiply(new THREE.Matrix4().makeScale(MONITOR.sw * scale, MONITOR.sh * scale, 1));
}

function canvas(w, h, draw) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  draw(c.getContext("2d"), w, h);
  return c;
}

function texture(c, { repeat = null, srgb = true } = {}) {
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) {
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(...repeat);
  }
  t.anisotropy = 8;
  return t;
}

function carpet() {
  const r = rng(11);
  return canvas(256, 256, (g, w, h) => {
    g.fillStyle = "#262b33";
    g.fillRect(0, 0, w, h);
    for (let i = 0; i < 9000; i++) {
      const v = 30 + r() * 22;
      g.fillStyle = `rgba(${v},${v + 4},${v + 12},0.5)`;
      g.fillRect(r() * w, r() * h, 1.5, 1.5);
    }
    g.fillStyle = "rgba(0,0,0,0.07)";
    for (const [x, y, across] of [[0, 0, 1], [128, 128, 1], [128, 0, 0], [0, 128, 0]]) {
      for (let k = 0; k < 128; k += 8) g.fillRect(x + (across ? 0 : k), y + (across ? k : 0), across ? 128 : 3, across ? 3 : 128);
    }
    g.strokeStyle = "rgba(0,0,0,0.35)";
    g.lineWidth = 1.5;
    for (const [x, y] of [[0, 0], [128, 0], [0, 128], [128, 128]]) g.strokeRect(x, y, 128, 128);
  });
}

function ceiling() {
  const r = rng(5);
  return canvas(256, 256, (g, w, h) => {
    g.fillStyle = "#7d8288";
    g.fillRect(0, 0, w, h);
    for (let i = 0; i < 5000; i++) {
      const v = 110 + r() * 40;
      g.fillStyle = `rgba(${v},${v},${v + 4},0.35)`;
      g.fillRect(r() * w, r() * h, 1, 1);
    }
    g.fillStyle = "#4d5257";
    g.fillRect(0, 0, w, 3);
    g.fillRect(0, 0, 3, h);
  });
}

function slats() {
  return canvas(256, 256, (g, w, h) => {
    g.fillStyle = "#14171b";
    g.fillRect(0, 0, w, h);
    for (let x = 0; x < w; x += 32) {
      const grad = g.createLinearGradient(x, 0, x + 22, 0);
      grad.addColorStop(0, "#3b2f26");
      grad.addColorStop(0.5, "#4a3a2e");
      grad.addColorStop(1, "#2e251e");
      g.fillStyle = grad;
      g.fillRect(x + 4, 0, 22, h);
    }
  });
}

/** Six kinds of dim screen a background desk shows: lists, a Gantt of rotations, a map, a dashboard, a terminal, radar. */
function screens() {
  const r = rng(23), out = [];
  const W = 384, H = 220;
  const frame = (g, title) => {
    g.fillStyle = "#0c131c";
    g.fillRect(0, 0, W, H);
    g.fillStyle = "#1c3550";
    g.fillRect(0, 0, W, 16);
    g.fillStyle = "#7f9bb8";
    g.font = "bold 10px sans-serif";
    g.fillText(title, 6, 12);
  };
  out.push(canvas(W, H, (g) => {
    frame(g, "FLIGHT WATCH");
    for (let y = 22; y < H - 6; y += 11) {
      g.fillStyle = y % 22 ? "#121c27" : "#0f1720";
      g.fillRect(4, y, W - 8, 10);
      for (let x = 8; x < W - 30; x += 40 + r() * 30) {
        g.fillStyle = r() < 0.08 ? "#c8574a" : r() < 0.12 ? "#d9a441" : "#7f8fa0";
        g.fillRect(x, y + 3, 14 + r() * 22, 4);
      }
    }
  }));
  out.push(canvas(W, H, (g) => {
    frame(g, "ROTATIONS");
    for (let y = 22; y < H - 6; y += 9) {
      g.fillStyle = "#16202b";
      g.fillRect(40, y + 7, W - 44, 1);
      g.fillStyle = "#62717f";
      g.fillRect(6, y + 2, 26, 4);
      let x = 40 + r() * 30;
      while (x < W - 20) {
        const w = 18 + r() * 50;
        g.fillStyle = r() < 0.1 ? "#b8524a" : r() < 0.2 ? "#c9963c" : "#3d6e9e";
        g.fillRect(x, y, Math.min(w, W - 6 - x), 6);
        x += w + 6 + r() * 20;
      }
    }
  }));
  out.push(canvas(W, H, (g) => {
    frame(g, "NETWORK");
    g.fillStyle = "#13243a";
    g.beginPath();
    g.ellipse(W / 2, H / 2 + 14, W * 0.42, H * 0.34, 0, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = "rgba(120,170,220,0.5)";
    for (let k = 0; k < 26; k++) {
      g.beginPath();
      const x0 = W * 0.78, y0 = H * 0.42, x1 = 30 + r() * (W - 60), y1 = 40 + r() * (H - 60);
      g.moveTo(x0, y0);
      g.quadraticCurveTo((x0 + x1) / 2, Math.min(y0, y1) - 30, x1, y1);
      g.stroke();
    }
  }));
  out.push(canvas(W, H, (g) => {
    frame(g, "OPS STATUS");
    for (let k = 0; k < 6; k++) {
      const x = 8 + (k % 3) * 124, y = 24 + Math.floor(k / 3) * 96;
      g.fillStyle = "#121d29";
      g.fillRect(x, y, 116, 88);
      g.fillStyle = ["#5aa0d8", "#5fc48a", "#d9a441"][k % 3];
      g.fillRect(x + 8, y + 26, 30 + r() * 60, 18);
      for (let j = 0; j < 4; j++) {
        g.fillStyle = "#3c4c5c";
        g.fillRect(x + 8, y + 54 + j * 8, 40 + r() * 60, 3);
      }
    }
  }));
  out.push(canvas(W, H, (g) => {
    g.fillStyle = "#06090c";
    g.fillRect(0, 0, W, H);
    for (let y = 8; y < H - 4; y += 9) {
      g.fillStyle = r() < 0.15 ? "#7fbf8e" : "#7d8a95";
      g.fillRect(6, y, 20 + r() * (W - 40), 3);
    }
  }));
  out.push(canvas(W, H, (g) => {
    frame(g, "WEATHER");
    g.fillStyle = "#132233";
    g.fillRect(4, 20, W - 8, H - 24);
    for (let k = 0; k < 40; k++) {
      const x = 40 + r() * (W - 80), y = 40 + r() * (H - 70), s = 4 + r() * 18;
      g.fillStyle = r() < 0.2 ? "rgba(230,190,60,0.7)" : "rgba(60,170,90,0.6)";
      g.beginPath();
      g.ellipse(x, y, s, s * 0.6, r() * 3, 0, Math.PI * 2);
      g.fill();
    }
  }));
  return out;
}

/** The room shell, the tiers, the rows of background workstations and the ceiling lights; the two desks' pods are pods.js's. */
export function buildHall() {
  const group = new THREE.Group();
  const { w: W, d: D, h: H } = ROOM;
  const carpetTex = texture(carpet(), { repeat: [W / 1, D / 1] });
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, D).rotateX(-Math.PI / 2).translate(0, 0, D / 2),
    new THREE.MeshStandardMaterial({ map: carpetTex, roughness: 0.95, metalness: 0 }));
  group.add(floor);
  const ceilTex = texture(ceiling(), { repeat: [W / 0.6, D / 0.6] });
  const ceil = new THREE.Mesh(new THREE.PlaneGeometry(W, D).rotateX(Math.PI / 2).translate(0, H, D / 2),
    new THREE.MeshStandardMaterial({ map: ceilTex, roughness: 1, color: 0x9a9fa6, emissive: 0x3a3f46, emissiveMap: ceilTex }));
  group.add(ceil);
  const wallMat = new THREE.MeshStandardMaterial({ color: 0x1a1d22, roughness: 0.9 });
  const front = new THREE.Mesh(new THREE.PlaneGeometry(W, H).translate(0, H / 2, 0), wallMat);
  group.add(front);
  const back = new THREE.Mesh(new THREE.PlaneGeometry(W, H).rotateY(Math.PI).translate(0, H / 2, D), wallMat);
  group.add(back);
  const slatTex = texture(slats(), { repeat: [D / 1.2, 1] });
  const slatMat = new THREE.MeshStandardMaterial({ map: slatTex, roughness: 0.75, color: 0xb0a090, emissive: 0x6a5a4a, emissiveMap: slatTex });
  for (const sx of [-1, 1]) {
    const side = new THREE.Mesh(new THREE.PlaneGeometry(D, H).rotateY(-sx * Math.PI / 2).translate(sx * W / 2, H / 2, D / 2), slatMat);
    group.add(side);
    const skirt = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.12, D).translate(sx * (W / 2 - 0.03), 0.06, D / 2), new THREE.MeshStandardMaterial({ color: 0x0f1113 }));
    group.add(skirt);
  }

  const riser = new THREE.MeshStandardMaterial({ color: 0x1c2026, roughness: 0.8 });
  const tierCarpet = carpetTex.clone();
  tierCarpet.repeat.set(1, 1);
  const tierTop = new THREE.MeshStandardMaterial({ map: tierCarpet, roughness: 0.95 });
  const strips = new THREE.MeshBasicMaterial({ color: 0x6d7f91 });
  for (const row of ROWS.slice(1)) {
    const r0 = row.r - 1.25, A = 0.75, shape = new THREE.Shape();
    for (let k = 0; k <= 48; k++) {
      const a = -A + (2 * A * k) / 48, x = r0 * Math.sin(a), z = ARC_Z + r0 * Math.cos(a);
      if (k === 0) shape.moveTo(x, -z);
      else shape.lineTo(x, -z);
    }
    shape.lineTo(W, -(D + 1));
    shape.lineTo(-W, -(D + 1));
    shape.closePath();
    const geo = new THREE.ExtrudeGeometry(shape, { depth: row.y, bevelEnabled: false, curveSegments: 4 }).rotateX(-Math.PI / 2);
    group.add(new THREE.Mesh(geo, [tierTop, riser]));
    group.add(new THREE.Mesh(new THREE.RingGeometry(r0 - 0.012, r0 + 0.035, 120, 1, -Math.PI / 2 - A, 2 * A).rotateX(-Math.PI / 2).translate(0, row.y + 0.003, ARC_Z), strips));
  }

  const stations = [];
  ROWS.forEach((row, i) => {
    for (const s of row.slots) for (const sx of [-1, 1]) stations.push({ row: i, m: stationMatrix(row, sx * s) });
  });
  const deskMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.7, metalness: 0.05 });
  const desks = new THREE.InstancedMesh(deskGeometry(), deskMat, stations.length);
  const chairs = new THREE.InstancedMesh(chairGeometry(), deskMat, stations.length);
  const r = rng(41);
  const seated = stations.filter(() => r() < 0.38);
  const person = personGeometry({ shirt: 0x4a5560 });
  const people = new THREE.InstancedMesh(person.body, deskMat, seated.length);
  const heads = new THREE.InstancedMesh(person.head, deskMat, seated.length);
  const chairAt = new THREE.Matrix4().makeTranslation(0, 0, 0.5);
  stations.forEach((st, k) => {
    desks.setMatrixAt(k, st.m);
    chairs.setMatrixAt(k, st.m.clone().multiply(chairAt).multiply(new THREE.Matrix4().makeRotationY((r() - 0.5) * 0.6)));
  });
  const tint = new THREE.Color();
  seated.forEach((st, k) => {
    const at = st.m.clone().multiply(chairAt);
    people.setMatrixAt(k, at);
    heads.setMatrixAt(k, at.clone().multiply(new THREE.Matrix4().makeRotationY((r() - 0.5) * 0.5).setPosition(...person.headAt)));
    const shirt = [0x55606c, 0x3e4b5a, 0x6b6460, 0x2f3a46, 0x5e6b62, 0x707782][Math.floor(r() * 6)];
    people.setColorAt(k, tint.set(shirt).lerp(new THREE.Color(1, 1, 1), 0.35));
  });
  for (const m of [desks, chairs, people, heads]) m.instanceMatrix.needsUpdate = true;
  group.add(desks, chairs, people, heads);

  const kinds = screens().map((c) => texture(c));
  const plane = new THREE.PlaneGeometry(1, 1);
  const screenSets = kinds.map((tex) => ({ tex, items: [] }));
  const mons = monitorMatrices(3);
  const on = new Set(seated);
  stations.forEach((st) => {
    for (const mm of mons) {
      const lit = on.has(st) || r() < 0.55;
      screenSets[Math.floor(r() * kinds.length)].items.push({ m: st.m.clone().multiply(screenMatrix(mm)), v: lit ? 0.42 + r() * 0.28 : 0.06 });
    }
  });
  for (const set of screenSets) {
    const mesh = new THREE.InstancedMesh(plane, new THREE.MeshBasicMaterial({ map: set.tex, toneMapped: false }), set.items.length);
    set.items.forEach((it, k) => {
      mesh.setMatrixAt(k, it.m);
      mesh.setColorAt(k, tint.setScalar(it.v));
    });
    mesh.instanceMatrix.needsUpdate = true;
    group.add(mesh);
  }

  const lights = new THREE.Group();
  const panel = new THREE.MeshBasicMaterial({ color: 0xfff1dc, toneMapped: false });
  const spots = [];
  for (let x = -12; x <= 12; x += 3) for (let z = 3; z <= 21; z += 3) spots.push([x, z]);
  const cans = new THREE.InstancedMesh(new THREE.CircleGeometry(0.09, 20).rotateX(Math.PI / 2), panel, spots.length);
  spots.forEach(([x, z], k) => cans.setMatrixAt(k, new THREE.Matrix4().makeTranslation(x, H - 0.005, z)));
  spots.forEach((_, k) => cans.setColorAt(k, tint.setScalar(0.7)));
  lights.add(cans);
  group.add(lights);
  return { group, stations, chairs, chairCount: stations.length };
}

