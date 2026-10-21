import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { snowOn } from "./env.js";

const FLOOR = 4.6;
const REST_FLOOR = 3.5;
const CAB = { w: 3.8, h: 3.1, d: 3.3 };
const DOCK = [45, 80];
const UNDOCK = [540, 100];
const ease = (u) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));
const angDiff = (a, b) => Math.atan2(Math.sin(b - a), Math.cos(b - a));
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const PARTS = { outer: "#BFC3C6", inner: "#C9CCCE", band: "#30383E", cab: "#B9BDC0", cabGlass: "#2A3238", bellows: "#1A1B1D", leg: "#55595C", bogie: "#26282A" };

/** Jet bridges that swing out and dock at the forward door of the aircraft parked at their stand, and tuck back in before it pushes; fit(points, tip) gives a fixed walkway (its points from the terminal to the rotunda, its cab toward tip) kept out of the gate lounges' halls. */
export function buildBridges(stage, plan, { fit = null } = {}) {
  const group = new THREE.Group();
  group.name = "bridges";
  const stands = new Map(stage.stands.map((s) => [s.ref, s]));
  const list = [];
  for (const jb of stage.jet_bridges) {
    let line = jb.line.filter((p, i, a) => i === 0 || dist(p, a[i - 1]) > 0.5);
    if (line.length < 2) continue;
    let st = jb.stand ? stands.get(jb.stand) : null;
    if (!st) {
      const tip = [line[0], line[line.length - 1]];
      const near = stage.stands.map((s) => [s, Math.min(dist(s.xy, tip[0]), dist(s.xy, tip[1]))]).sort((a, b) => a[1] - b[1])[0];
      if (near && near[1] < 45) st = near[0];
    }
    if (st && dist(line[0], st.xy) < dist(line[line.length - 1], st.xy)) line = line.slice().reverse();
    const n = line.length;
    let k = 0;
    let bend = 20 * (Math.PI / 180);
    for (let i = 1; i < n - 1; i++) {
      const a = Math.atan2(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1]);
      const b = Math.atan2(line[i + 1][0] - line[i][0], line[i + 1][1] - line[i][1]);
      if (Math.abs(angDiff(a, b)) > bend && dist(line[i], line[n - 1]) > 8) {
        bend = Math.abs(angDiff(a, b));
        k = i;
      }
    }
    const fixed = fit ? fit(line.slice(0, k + 1), line[n - 1]) : line.slice(0, k + 1);
    const P = fixed[fixed.length - 1];
    const tip = line[n - 1];
    const L = Math.max(dist(tip, P), 6);
    list.push({ stand: st?.ref ?? null, P, fixed, restAng: Math.atan2(tip[0] - P[0], tip[1] - P[1]), restLen: Math.min(30, Math.max(8, 0.7 * L)), reach: Math.max(L * 1.9 + 12, 30) });
  }
  const byStand = new Map();
  for (const b of list) if (b.stand) (byStand.get(b.stand) || byStand.set(b.stand, []).get(b.stand)).push(b);

  const statics = [];
  const color = (g, c) => {
    const col = new THREE.Color(c);
    g = g.index ? g.toNonIndexed() : g;
    g.setAttribute("color", new THREE.Float32BufferAttribute(Array.from({ length: g.getAttribute("position").count }, () => [col.r, col.g, col.b]).flat(), 3));
    g.deleteAttribute("uv");
    return g;
  };
  for (const b of list) {
    statics.push(color(new THREE.CylinderGeometry(0.75, 0.9, FLOOR - 0.2, 12).translate(b.P[0], (FLOOR - 0.2) / 2, -b.P[1]), "#8E9295"));
    statics.push(color(new THREE.CylinderGeometry(2.5, 2.5, 3.5, 18).translate(b.P[0], FLOOR + 1.45, -b.P[1]), "#B4B8BB"));
    statics.push(color(new THREE.CylinderGeometry(2.7, 2.7, 0.35, 18).translate(b.P[0], FLOOR + 3.35, -b.P[1]), "#8C9094"));
    for (let i = 0; i + 1 < b.fixed.length; i++) {
      const m = beam([b.fixed[i][0], b.fixed[i][1], FLOOR + 1.5], [b.fixed[i + 1][0], b.fixed[i + 1][1], FLOOR + 1.5], 3.0, 3.0);
      statics.push(color(new THREE.BoxGeometry(1, 1, 1).applyMatrix4(m), "#C4C7C9"));
      const g = beam([b.fixed[i][0], b.fixed[i][1], FLOOR + 1.85], [b.fixed[i + 1][0], b.fixed[i + 1][1], FLOOR + 1.85], 3.06, 1.0);
      statics.push(color(new THREE.BoxGeometry(1, 1, 1).applyMatrix4(g), "#3A4248"));
    }
  }
  const uSnow = { value: 0 };
  const staticMat = snowOn(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, metalness: 0.25 }), uSnow);
  if (statics.length) {
    const m = new THREE.Mesh(mergeGeometries(statics), staticMat);
    m.castShadow = m.receiveShadow = true;
    group.add(m);
  }
  const unit = new THREE.BoxGeometry(1, 1, 1);
  const parts = {};
  for (const [name, c] of Object.entries(PARTS)) {
    const glassy = name === "band" || name === "cabGlass";
    const mat = new THREE.MeshStandardMaterial({ color: c, roughness: glassy ? 0.2 : 0.55, metalness: glassy ? 0.4 : 0.25 });
    const m = new THREE.InstancedMesh(unit, glassy ? mat : snowOn(mat, uSnow), list.length * (name === "leg" ? 2 : 1));
    m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    m.castShadow = !glassy;
    m.receiveShadow = true;
    m.frustumCulled = false;
    group.add(m);
    parts[name] = m;
  }
  const door = new THREE.Vector3();
  const side = new THREE.Vector3();
  const dir = (a) => [Math.sin(a), Math.cos(a)];

  function poseOf(b, target, u) {
    let ang = b.restAng;
    let len = b.restLen;
    let yaw = b.restAng;
    let h1 = REST_FLOOR;
    if (target && u > 0) {
      const back = [target.c[0] - Math.sin(target.yaw) * CAB.d * 0.5, target.c[1] - Math.cos(target.yaw) * CAB.d * 0.5];
      const dl = dist(back, b.P);
      const da = Math.atan2(back[0] - b.P[0], back[1] - b.P[1]);
      if (dl > 5 && dl < b.reach && Math.abs(angDiff(b.restAng, da)) < 2.2) {
        const k = ease(u);
        ang = b.restAng + angDiff(b.restAng, da) * k;
        len = b.restLen + (dl - b.restLen) * k;
        yaw = b.restAng + angDiff(b.restAng, target.yaw) * k;
        h1 = REST_FLOOR + (target.h - REST_FLOOR) * k;
      }
    }
    const B = [b.P[0] + dir(ang)[0] * len, b.P[1] + dir(ang)[1] * len];
    return { B, C: [B[0] + dir(yaw)[0] * CAB.d * 0.5, B[1] + dir(yaw)[1] * CAB.d * 0.5], len, ang, yaw, h1 };
  }

  function write(i, b, q) {
    const lo = Math.min(q.len * 0.55, b.restLen * 0.75);
    const at = (s, up = 0) => [b.P[0] + dir(q.ang)[0] * s, b.P[1] + dir(q.ang)[1] * s, FLOOR + ((q.h1 - FLOOR) * s) / q.len + up];
    parts.outer.setMatrixAt(i, beam(at(1.5, 1.5), at(lo, 1.5), 3.2, 3.05));
    parts.inner.setMatrixAt(i, beam(at(lo - 0.8, 1.45), at(q.len, 1.45), 2.85, 2.8));
    parts.band.setMatrixAt(i, beam(at(1.5, 1.95), at(q.len, 1.95), 3.24, 0.9));
    const c = [q.C[0], q.C[1], q.h1 + CAB.h / 2 + 0.05];
    const f = dir(q.yaw);
    parts.cab.setMatrixAt(i, beam([c[0] - f[0] * CAB.d * 0.5, c[1] - f[1] * CAB.d * 0.5, c[2]], [c[0] + f[0] * CAB.d * 0.5, c[1] + f[1] * CAB.d * 0.5, c[2]], CAB.w, CAB.h));
    parts.cabGlass.setMatrixAt(i, beam([c[0] - f[0] * CAB.d * 0.45, c[1] - f[1] * CAB.d * 0.45, c[2] + 0.4], [c[0] + f[0] * CAB.d * 0.35, c[1] + f[1] * CAB.d * 0.35, c[2] + 0.4], CAB.w + 0.06, 1.0));
    parts.bellows.setMatrixAt(i, beam([c[0] + f[0] * CAB.d * 0.5, c[1] + f[1] * CAB.d * 0.5, c[2]], [c[0] + f[0] * (CAB.d * 0.5 + 0.45), c[1] + f[1] * (CAB.d * 0.5 + 0.45), c[2]], CAB.w - 0.3, CAB.h - 0.2));
    const s = Math.max(lo + 1, q.len - 4.5);
    const top = at(s);
    const n = [-dir(q.ang)[1], dir(q.ang)[0]];
    b.legs = [top[0], top[1]];
    b.tube = [b.P[0], b.P[1], q.C[0] + f[0] * CAB.d * 0.5, q.C[1] + f[1] * CAB.d * 0.5, CAB.w / 2];
    for (const [k, sg] of [[0, -1], [1, 1]]) parts.leg.setMatrixAt(i * 2 + k, beam([top[0] + n[0] * sg * 1.0, top[1] + n[1] * sg * 1.0, 0.7], [top[0] + n[0] * sg * 1.0, top[1] + n[1] * sg * 1.0, top[2]], 0.42, 0.42));
    parts.bogie.setMatrixAt(i, beam([top[0] - dir(q.ang)[0] * 0.7, top[1] - dir(q.ang)[1] * 0.7, 0.55], [top[0] + dir(q.ang)[0] * 0.7, top[1] + dir(q.ang)[1] * 0.7, 0.55], 3.0, 1.1));
  }

  const docked = new Map();
  return {
    group,
    list,
    docked,
    /** What stands on the apron under the bridges now, for a camera to keep clear of: [x, y, radius] ENU metres for
     * each rotunda's column and each pair of legs. */
    obstacles() {
      return list.flatMap((b) => [[b.P[0], b.P[1], 1.3], ...(b.legs ? [[b.legs[0], b.legs[1], 2.0]] : [])]);
    },
    /** Each bridge's tunnel and cab overhead as [x0, y0, x1, y1, half width] ENU metres, rotunda to cab front. */
    tubes() {
      return list.filter((b) => b.tube).map((b) => b.tube);
    },
    update(t, live, s) {
      uSnow.value = s.snow;
      const ts = t * 60;
      const byKey = new Map(live.map((f) => [f.key, f]));
      const targets = new Map();
      docked.clear();
      for (const [ref, bs] of byStand) {
        const stay = (plan.stays.get(ref) || []).find((s) => s.a <= ts && ts < s.b);
        const f = stay && byKey.get(stay.key);
        if (!f || f.pose.phase !== "parked") continue;
        const u = Math.min(ease((ts - stay.a - DOCK[0]) / DOCK[1]), stay.leave ? 1 - ease((ts - (stay.b - UNDOCK[0])) / UNDOCK[1]) : 1);
        if (u <= 0) continue;
        const doors = f.dims.doors.slice(0, f.dims.length > 50 ? 2 : 1).map((p) => {
          door.set(...p).applyMatrix4(f.matrix);
          side.set(-1, 0, 0).transformDirection(f.matrix);
          const c = [door.x + side.x * (CAB.d * 0.5 + 0.55), -door.z - side.z * (CAB.d * 0.5 + 0.55)];
          return { c, yaw: Math.atan2(-side.x, side.z), h: door.y - 0.05, at: [door.x, -door.z] };
        });
        const order = bs.slice().sort((a, b) => dist(a.P, doors[0].at) - dist(b.P, doors[0].at));
        doors.forEach((d, k) => order[k] && targets.set(order[k], { ...d, u }));
        docked.set(ref, u);
      }
      list.forEach((b, i) => {
        const tg = targets.get(b);
        write(i, b, poseOf(b, tg, tg ? tg.u : 0));
      });
      for (const m of Object.values(parts)) m.instanceMatrix.needsUpdate = true;
    },
  };
}

const _x = new THREE.Vector3();
const _y = new THREE.Vector3();
const _z = new THREE.Vector3();
const _up = new THREE.Vector3(0, 1, 0);

/** A unit box stretched from a to b ([x, y, up] ENU metres) with a w × h cross-section, as a matrix. */
function beam(a, b, w, h) {
  const A = new THREE.Vector3(a[0], a[2], -a[1]);
  const B = new THREE.Vector3(b[0], b[2], -b[1]);
  _z.subVectors(B, A);
  const L = Math.max(_z.length(), 0.01);
  _z.divideScalar(L);
  _x.crossVectors(_up, _z);
  if (_x.lengthSq() < 1e-8) _x.set(1, 0, 0);
  _x.normalize();
  _y.crossVectors(_z, _x);
  const m = new THREE.Matrix4().makeBasis(_x.multiplyScalar(w), _y.multiplyScalar(h), _z.multiplyScalar(L));
  return m.setPosition(A.add(B).multiplyScalar(0.5));
}
