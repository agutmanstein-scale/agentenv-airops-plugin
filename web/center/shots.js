// Film shots in the control-center room for a director that knows names, not metres: {name: room | room-desks |
// room-wall, desk: ops | crew | one, ref: a request id, t0, t1} picks a framing from where real crews film these rooms
// (a wide from the back, a two-shot over a desk's shoulder, the wall head-on), favours the desk, widens and re-aims
// until the desk and the request's cards over [t0, t1] sit inside the frame, and pushes in slowly between t0 and t1.
// Explicit {from, at, fov, to, fov1} in room metres still win.
import * as THREE from "three";

const MARGIN = 0.84;
const MAX_FOV = 62;
export const BASE = {
  room: { from: [0, 5.1, 21.0], at: [0, 2.3, 3.5], fov: 48, push: 0.12 },
  "room-desks": { from: [0.1, 2.6, 9.5], at: [0, 1.85, 5.3], fov: 40, push: 0.16 },
  "room-wall": { from: [0, 3.25, 14.6], at: [0, 3.2, 0], fov: 40, push: 0.1 },
};
const WALL_AT = [0, 3.2, 0.3];

/** The base framing of a named shot favouring the pod at x (0 for a one-agent day's single pod). */
function favoured(name, x, one) {
  const s = x < 0 ? -1 : 1;
  if (name === "room") return { from: [-x * 0.5, 5.0, 20.6], at: [x * 0.7, 2.2, 4.2], fov: 46, push: 0.12 };
  if (name === "room-wall") return { from: [x * 0.9, 3.15, 14.2], at: [x * 0.25, 3.1, 0], fov: 40, push: 0.1 };
  if (one) return { from: [-2.5, 2.6, 8.7], at: [0.2, 1.75, 5.3], fov: 42, push: 0.14 };
  return { from: [x + s * 2.3, 2.6, 8.7], at: [x - s * 0.9, 1.85, 5.3], fov: 42, push: 0.14 };
}

/** The half-height tangent a frame needs from `from` aimed at `at` to hold every point (cards with their size). */
function need(from, at, points, aspect) {
  const fwd = at.clone().sub(from).normalize(), right = fwd.clone().cross(new THREE.Vector3(0, 1, 0)).normalize(), up = right.clone().cross(fwd);
  let n = 0;
  const v = new THREE.Vector3();
  for (const { p, w = 0, h = 0 } of points) {
    v.copy(p).sub(from);
    const z = v.dot(fwd);
    if (z < 0.3) continue;
    const x = Math.abs(v.dot(right)) + w / 2, y = Math.abs(v.dot(up)) + h / 2;
    n = Math.max(n, y / z, x / z / aspect);
  }
  return n / MARGIN;
}

/** Turn `at` toward the middle of the points' angular extent, half way, so a widened frame isn't spent on one side. */
function recentre(from, at, points) {
  if (!points.length) return at;
  const fwd = at.clone().sub(from).normalize(), right = fwd.clone().cross(new THREE.Vector3(0, 1, 0)).normalize(), up = right.clone().cross(fwd);
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  const v = new THREE.Vector3();
  for (const { p } of points) {
    v.copy(p).sub(from);
    const z = v.dot(fwd);
    if (z < 0.3) continue;
    const x = v.dot(right) / z, y = v.dot(up) / z;
    x0 = Math.min(x0, x), x1 = Math.max(x1, x), y0 = Math.min(y0, y), y1 = Math.max(y1, y);
  }
  if (!Number.isFinite(x0)) return at;
  const d = at.distanceTo(from), cx = (x0 + x1) / 4, cy = (y0 + y1) / 4;
  return from.clone().add(fwd.clone().add(right.multiplyScalar(cx)).add(up.multiplyScalar(cy)).normalize().multiplyScalar(d));
}

const fovOf = (tan) => (2 * Math.atan(tan) * 180) / Math.PI;
const vec = (a) => new THREE.Vector3(...a);

/** A shot's camera: {name, from, to, at, fov, fov1, t0, t1, desk, ref} in room metres, ready to play by minute. */
export function compose(spec, { pods, cards, aspect, T }) {
  const one = pods.size === 1 && pods.has("one");
  let desk = spec.desk, ref = spec.ref, aim = Array.isArray(spec.at) ? vec(spec.at) : null;
  if (typeof spec.at === "string") {
    if (/^R\d+$/.test(spec.at)) ref ??= spec.at;
    else if (spec.at === "wall" || spec.at === "map") aim = vec(WALL_AT);
    else desk ??= spec.at;
  }
  if (desk && one) desk = "one";
  const pod = desk ? pods.get(desk) : null;
  const t0 = spec.t0 ?? T, t1 = spec.t1 ?? spec.t0 ?? T;
  const points = [];
  for (const p of pod ? [pod] : spec.name === "room-desks" || !spec.name ? [...pods.values()] : []) for (const q of p.points) points.push({ p: q });
  if (ref) {
    const span = cards.span(ref);
    let track = cards.track(ref, t0 - 0.5, Math.max(t1, t0 + 6));
    if (!track.length && span) track = cards.track(ref, span[0], Math.min(span[1], span[0] + 12));
    for (const p of track) points.push({ p, w: cards.size.w * 1.3, h: cards.size.h * 1.3 });
  }
  if (spec.from) {
    const at = aim ?? (points.length ? points.reduce((s, x) => s.add(x.p), new THREE.Vector3()).divideScalar(points.length) : vec(WALL_AT));
    return { name: spec.name || "shot", from: spec.from, to: spec.to ?? spec.from, at: at.toArray(), fov: spec.fov ?? 45, fov1: spec.fov1 ?? spec.fov ?? 45, t0, t1: spec.t1 > t0 ? spec.t1 : null, desk, ref };
  }
  const name = BASE[spec.name] ? spec.name : "room";
  const base = pod ? favoured(name, pod.anchor.x, one) : BASE[name];
  const from = vec(base.from);
  let at = aim ?? vec(base.at);
  if (!aim && need(from, at, points, aspect) > Math.tan((base.fov * Math.PI) / 360)) at = recentre(from, at, [...points, { p: at }]);
  const moving = spec.t1 != null && spec.t1 > t0;
  let push = moving ? base.push : 0, to = from.clone().lerp(at, push);
  let tan = Math.max(Math.tan((base.fov * Math.PI) / 360), need(from, at, points, aspect), need(to, at, points, aspect));
  if (fovOf(tan) > MAX_FOV && push) {
    push = 0;
    to = from.clone();
    tan = Math.max(Math.tan((base.fov * Math.PI) / 360), need(from, at, points, aspect));
  }
  const fov = Math.min(MAX_FOV + 4, fovOf(tan));
  return { name, from: from.toArray(), to: to.toArray(), at: at.toArray(), fov, fov1: fov, t0, t1: moving ? spec.t1 : null, desk: desk ?? null, ref: ref ?? null };
}
