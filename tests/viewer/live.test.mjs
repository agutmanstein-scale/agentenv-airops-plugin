import assert from "node:assert/strict";
import { test } from "node:test";
import { Pacer } from "../../web/live.js";

/** The pacer driven at 60 frames a second for `seconds`, `land(wall)` moving the frontier; every frame's clock. */
function run(p, seconds, land = () => {}, from = 0) {
  const out = [];
  for (let k = 0; k < seconds * 60; k++) {
    const now = from + k / 60;
    land(p, now);
    out.push({ now, ...p.tick(now, 1 / 60), F: p.F });
  }
  return out;
}

test("the clock never passes what is decided and eases to a hold there", () => {
  const p = new Pacer(240, 300, 0);
  const frames = run(p, 400);
  assert.ok(frames.every((f) => f.t <= f.F + 1e-9));
  const end = frames.at(-1);
  assert.ok(end.t > 299.5 && end.rate < 1, `held at ${end.t} at ${end.rate}`);
  const rates = frames.map((f) => f.rate);
  assert.ok(rates.slice(1).every((r, i) => Math.abs(r - rates[i]) < 3), "no jump in speed");
});

test("each decided hour plays over about the time a watch takes", () => {
  const p = new Pacer(240, 300, 0);
  const frames = run(p, 1200, (q, now) => {
    const k = Math.floor(now / 100);
    if (300 + 60 * k > q.F) q.advance(300 + 60 * k, now);
  });
  const late = frames.filter((f) => f.now > 600);
  assert.ok(late.every((f) => f.t <= f.F + 1e-9));
  const speed = (late.at(-1).t - late[0].t) / (late.at(-1).now - late[0].now);
  assert.ok(speed > 0.5 && speed < 0.7, `plays ${speed} min/s for watches of 100 s`);
  assert.ok(late.every((f) => f.F - f.t < 90), "keeps within an hour and a half of what is decided");
  assert.ok(Math.abs(p.watch() - 100) < 5, `times a watch at ${p.watch()} s`);
});

test("a shot's slower clock applies at the cut, and the clock jumps on when far behind", () => {
  const p = new Pacer(240, 300, 0);
  run(p, 30);
  p.advance(1500, 30);
  p.cut(0.1, false, 30);
  assert.ok(p.v <= 0.1);
  const t = p.t;
  run(p, 60, () => {}, 30);
  p.cut(Infinity, true, 90);
  assert.ok(p.t > t + 20, `jumped from ${t} to ${p.t}`);
  assert.ok(p.t <= p.F);
});

test("once every watch is decided the day's end plays out at the end speed", () => {
  const p = new Pacer(1500, 1560, 0);
  run(p, 100);
  p.advance(1965, 100, true);
  const frames = run(p, 120, () => {}, 100);
  assert.ok(frames.at(-1).t > 1960, `at ${frames.at(-1).t}`);
  assert.ok(frames.every((f) => f.rate <= 5 * 60 + 1e-9));
});
