const DEG = Math.PI / 180;
const HOME = { BOS: "C", JFK: "T5" };
const BAY = { BOS: { bearing: 110, range: 3600, up: 300, fov: 40 }, JFK: { bearing: 118, range: 4200, up: 320, fov: 38 } };
const dirOf = (deg) => [Math.sin(deg * DEG), Math.cos(deg * DEG)];
const add = (a, b, k = 1) => [a[0] + b[0] * k, a[1] + b[1] * k];
const mean = (pts) => [pts.reduce((s, p) => s + p[0], 0) / pts.length, pts.reduce((s, p) => s + p[1], 0) / pts.length];

/** JetBlue's terminal at the hub: its stands and their centre. */
export function homeOf(stage) {
  const ref = HOME[stage.hub] || stage.stands[0].terminal;
  const stands = stage.stands.filter((s) => s.terminal === ref);
  return { ref, stands, centre: mean(stands.map((s) => s.xy)) };
}

/** Camera presets in ENU metres ([x, y, up]) for a stage: the tower, a gate at JetBlue's terminal, the departure threshold in use and the harbour or bay. */
export function cameraPresets(stage, { stand = null, departures = null, length = 40, roll = null } = {}) {
  const tw = stage.tower;
  const home = homeOf(stage);
  const st = stage.stands.find((s) => s.ref === stand) || home.stands.reduce((a, s) => (dist(s.xy, home.centre) < dist(a.xy, home.centre) ? s : a), home.stands[0]);
  const ends = stage.runways.flatMap((r) => r.ends);
  const end = ends.find((e) => e.id === departures) || ends[0];
  const rw = mean(ends.map((e) => e.xy));
  const hd = dirOf(st.heading);
  const ed = dirOf(end.heading);
  const out = {};
  if (tw) {
    const look = add(home.centre, dirOf(Math.atan2(rw[0] - home.centre[0], rw[1] - home.centre[1]) / DEG), 260);
    const toward = unit([look[0] - tw.xy[0], look[1] - tw.xy[1]]);
    out.tower = { position: [tw.xy[0] + toward[0] * 9, tw.xy[1] + toward[1] * 9, tw.height - 17], target: [look[0], look[1], 0], fov: 52 };
  }
  const port = dirOf(st.heading - 90);
  const gateFrom = add(add(st.xy, hd, -(length + 26)), port, 30);
  out.gate = { position: [gateFrom[0], gateFrom[1], 11], target: [st.xy[0] - hd[0] * 6, st.xy[1] - hd[1] * 6, 3.5], fov: 46 };
  const left = [-ed[1], ed[0]];
  let lean = 0;
  for (const t of stage.taxiways) {
    for (const p of t.line) {
      const q = [p[0] - end.xy[0], p[1] - end.xy[1]];
      const a = q[0] * ed[0] + q[1] * ed[1];
      const c = q[0] * left[0] + q[1] * left[1];
      if (a > -100 && a < 800 && Math.abs(c) > 60 && Math.abs(c) < 320) lean += Math.sign(c);
    }
  }
  const start = roll || end.xy;
  const thFrom = add(add(start, ed, -230), left, (lean >= 0 ? 1 : -1) * 210);
  out.threshold = { position: [thFrom[0], thFrom[1], 15], target: [start[0] + ed[0] * 700, start[1] + ed[1] * 700, 0], fov: 38 };
  const bay = BAY[stage.hub] || BAY.BOS;
  const centre = [home.centre[0] * 0.55 + rw[0] * 0.45, home.centre[1] * 0.55 + rw[1] * 0.45];
  const from = add(centre, dirOf(bay.bearing), bay.range);
  out.harbour = { position: [from[0], from[1], bay.up], target: [centre[0], centre[1], -60], fov: bay.fov };
  return out;
}

const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const unit = (a) => {
  const l = Math.hypot(a[0], a[1]) || 1;
  return [a[0] / l, a[1] / l];
};
