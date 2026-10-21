import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";
import { boardAt, cities, prepare, programBands, programsAt, rewardOf, scoreAt } from "../../web/model.js";
import { asksAt, atRisk, disrupted, flowsAt, paxAt, paxIndex, strandedPaxAt, waitingAt } from "../../web/paxdata.js";
import * as plain from "../../web/plain.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const NEWS = JSON.parse(readFileSync(join(root, "tests/viewer/fixtures/news.json"), "utf8"));
const day = (id) => {
  const d = NEWS[id];
  const item = (nid) => d.news.find((n) => n.id === nid);
  return { task: d.task, item, said: (nid) => plain.news(item(nid), { task: d.task, earlier: d.news, city: (c) => c }) };
};
const hero = day("b6-bos-20250216-1500"), snow = day("b6-bos-20240107-0600"), storms = day("b6-jfk-20230714-1400");

test("METARs read as the traffic sidecar reads them", () => {
  assert.equal(hero.said("metar-bos-202502161854").text, "Light rain and mist · visibility 1 1/4 mi · cloud at 400 ft · wind NE 17 mph.");
  assert.equal(hero.said("metar-bos-202502161954").text, "Rain and mist · visibility 1 1/2 mi · cloud at 400 ft · wind NNE 23 mph gusting 30.");
  assert.equal(snow.said("metar-bos-202401071954").text, "Heavy snow and freezing fog · visibility 1/4 mi · cloud at 500 ft · wind N 17 mph gusting 29.");
  assert.equal(storms.said("metar-jfk-202307150251").text, "Thunderstorm nearby and light rain · visibility 10 mi · cloud at 4,300 ft · calm.");
  const w = plain.metar("KJFK 150251Z 00000KT 10SM VCTS -RA SCT043 BKN150 24/22 A2995 RMK AO2");
  assert.deepEqual([w.wind_kt, w.vis_sm, w.wx, w.ceiling_ft, w.temp_c], [0, 10, ["VCTS", "-RA"], 15000, 24]);
  assert.equal(plain.metar("KBOS 162254Z 27012KT 10SM SCT035 M02/M09 A2990").plain, "Partly cloudy · visibility 10 mi · wind W 14 mph");
});

test("a TAF reads as the next hours, in local time", () => {
  assert.equal(plain.taf(hero.item("taf-bos-202502161720").text, { task: hero.task, t: 740 }),
    "Until 15:00: rain showers, visibility 2 mi, low cloud at 800 ft, wind ENE 15 mph gusting 21; from 15:00: rain showers, visibility 2 mi, "
    + "low cloud at 800 ft, wind shear aloft; from 20:00: cloud at 2,000 ft, wind WSW 16 mph gusting 37");
  assert.equal(storms.said("taf-jfk-202307141455").text,
    "Until 20:00: cloud at 1,500 ft, thunderstorm with rain at times 16:00–20:00; from 20:00: fair, 30% chance of light rain showers 20:00–02:00.");
  const periods = plain.tafPeriods(storms.item("taf-jfk-202307141455").text, storms.task);
  assert.deepEqual(periods.map((p) => [p.from, p.to]), [[660, 1200], [1200, 1560], [1560, 2280]]);
});

test("the FAA's programs read in plain English, with local times", () => {
  assert.deepEqual(hero.said("adv-20250216-053"), { label: "FAA", title: "Delay program at Boston", tone: "gdp",
    text: "The FAA starts a ground delay program at Boston: flights to it get departure times, 18 to 36 arrivals an hour until 20:00; average delay 41 min, up to 2 h 43 min (snow and ice)." });
  assert.equal(hero.said("adv-20250216-061").text,
    "The FAA stops JetBlue flights to Boston: they wait at their departure airports from 08:45 to 10:00 (at JetBlue's request: its gates are full). Chance it is extended: medium.");
  assert.equal(hero.said("adv-20250216-072").title, "Ground stop at Boston extended to 11:00");
  assert.deepEqual([hero.said("adv-20250216-084").title, hero.said("adv-20250216-084").tone], ["Ground stop at Boston lifted", "clear"]);
  assert.equal(hero.said("adv-20250216-127").title, "Delay program at Boston ends");
  assert.equal(snow.said("adv-20240107-040").text,
    "The FAA proposes slowing arrivals at Boston to 24 to 28 arrivals an hour from 13:00 to 00:00; average delay 2 h 2 min, up to 3 h 27 min (snow and ice).");
  assert.equal(snow.said("adv-20240107-045").text,
    "The FAA warns of arrival delays at Boston of up to 30 minutes, circling in the air, due to snow, runway treatments, or snow removal.");
  assert.equal(snow.said("adv-20240107-051").title, "Diversion recovery at Boston");
  assert.equal(storms.said("adv-20230714-040").title, "Ground stop at JFK until 06:30");
  assert.equal(storms.said("adv-20230714-042").title, "Ground stop at JFK continues until 06:30");
  assert.match(storms.said("adv-20230714-214").text, /from 18:46 to 20:00 \(storms block the routes in\)/);
});

test("a forecast discussion reads as its first sentences for the hub, without jargon", () => {
  assert.equal(hero.said("afd-box-202502161735").text, "Periods of moderate to heavy rain this afternoon. Wind shear from the SW increase this afternoon to 40-60 knots.");
  assert.equal(snow.said("afd-box-202401071148").text,
    "Rain/snow mix through the morning with temps rising to 33-35F. Then changes over to light/moderate snow 11:00 to 13:00 with accumulating snow until about 16:00 to 18:00.");
});

test("the agent's changes and tool calls read as sentences", () => {
  const sched = new Map([[0, { id: 0, flight: "B6 287", orig: "BOS", dest: "LAX", pax: 131 }], [25, { id: 25, flight: "B6 1355", orig: "BOS", dest: "DCA", pax: 120 }],
    [23, { id: 23, flight: "B6 2554", orig: "DCA", dest: "MCO", pax: 98 }]]);
  const ctx = { sched, hub: "BOS", city: (c) => ({ LAX: "Los Angeles", DCA: "Washington", MCO: "Orlando" })[c] ?? c, flightOf: (f) => sched.get(+f.slice(1))?.flight };
  assert.equal(plain.action({ id: 0, action: "cancel" }, ctx), "Cancelled B6 287 to Los Angeles (131 passengers)");
  assert.equal(plain.action({ id: 25, action: "hold", not_before: 1200 }, ctx), "Held B6 1355 to Washington until 20:00");
  assert.equal(plain.action({ id: 25, action: "release" }, ctx), "Let B6 1355 to Washington leave as soon as it can");
  assert.equal(plain.action({ id: 23, action: "restore", not_before: null }, ctx), "Brought back B6 2554 Washington to Orlando");
  assert.equal(plain.flightName({ flight: "B6 288", orig: "LAX", dest: "BOS" }, ctx), "B6 288 from Los Angeles");
  const check = { tool: "check_plan", args: { changes: [{ flight: "f0", action: "cancel", not_before: null }, { flight: "f25", action: "hold", not_before: "18:00" }] },
    result: { cost: { total: 1989900 } } };
  assert.equal(plain.step(check, ctx), "Tried a plan to cancel B6 287 and hold B6 1355: the day as known would cost 1.99M passenger-minutes");
  assert.equal(plain.step({ ...check, tool: "confirm_changes", result: { confirmed: ["f0", "f25"] } }, ctx), "Confirmed 2 changes: cancel B6 287 and hold B6 1355");
  assert.equal(plain.step({ tool: "advance", result: { done: true, reward: 0.67 } }, ctx), "Closed the desk: the rest of the day is flown on what really happened");
  assert.equal(plain.duration(416), "6 h 56 min");
});

test("the board, the programs and the score follow the clock on an episode without a stage", () => {
  const ep = JSON.parse(readFileSync(join(root, "tests/viewer/fixtures/episode_small.json"), "utf8"));
  const P = prepare(ep, "http://x/");
  const hub = ep.task.hub;
  for (const t of [ep.task.start, ep.watches.at(-1).t, P.end]) {
    const b = boardAt(P, t);
    for (const [kind, rows] of Object.entries(b)) {
      assert.ok(rows.length <= 7);
      assert.deepEqual(rows.map((r) => r.sched), [...rows.map((r) => r.sched)].sort((x, y) => x - y));
      for (const r of rows) {
        const s = P.sched.get(r.id);
        assert.equal(kind === "dep" ? s.orig : s.dest, hub);
        assert.match(r.status, /^(On time|Delayed (\d+ min|\d*[¼½¾]? h)|Held until \d\d:\d\d|May be cancelled|Cancelled|Departed|Landed)$/);
      }
    }
  }
  const early = scoreAt(P, ep.task.start), late = scoreAt(P, Infinity);
  assert.ok(early.cost <= late.cost && late.cost === ep.cost.total && late.done);
  assert.equal(late.cancelled, ep.executed.filter((e) => e.status !== "flown").length);
  assert.ok(Math.abs(rewardOf(ep.reference, ep.cost.total) - ep.reward) < 0.002);
  for (const b of programBands(P)) assert.ok(b.from < b.to && ["gs", "gdp"].includes(b.kind));
  const live = programsAt(P, ep.task.start);
  assert.ok(live.gs === null || live.gs.data.element === hub);
  assert.deepEqual(["IAH", "RSW", "BNA"].map((c) => cities(ep.task, null).get(c)), ["Houston", "Fort Myers", "Nashville"]);
});

const cached = join(root, "cache/episodes");
const sidecars = existsSync(cached) ? readdirSync(cached).filter((f) => f.endsWith(".stage.json.gz")) : [];
test("every METAR in the cached traffic sidecars decodes word for word", { skip: !sidecars.length && "no traffic sidecars cached" }, () => {
  for (const f of sidecars) {
    const doc = JSON.parse(gunzipSync(readFileSync(join(cached, f))).toString("utf8"));
    for (const w of doc.weather) assert.equal(plain.metar(w.metar).plain, w.plain, w.metar);
  }
});

test("a day's passengers count as their events come: missed, rebooked, in hotels until they leave, and the holds for them", () => {
  const sched = new Map([[1, { id: 1, flight: "B6 1", orig: "BOS", dest: "SYR", sched_dep: 600 }], [2, { id: 2, flight: "B6 2", orig: "JFK", dest: "BOS", sched_dep: 500 }],
    [3, { id: 3, flight: "B6 3", orig: "BOS", dest: "SYR", sched_dep: 900 }]]);
  const ep = { occ: { requests: [{ sender: "customer", type: "HOLD", sent: 600, reply: "accept", answered: 610, pax: 12 },
    { sender: "customer", type: "HOLD", sent: 605, reply: "decline", answered: 615, pax: 4 }, { sender: "customer", type: "TEXT", sent: 605 }] },
    passengers: { flights: [{ id: 1, booked: 100, flown: 90 }], connections: [], events: [
      { t: 650, kind: "misconnect", at: "BOS", flight: 1, from: 2, pax: 6 }, { t: 700, kind: "rebook", at: "BOS", flight: 1, to: 3, pax: 4 },
      { t: 720, kind: "hotel", at: "BOS", flight: 1, pax: 2, until: 1800 }, { t: 730, kind: "home", at: "BOS", flight: 2, pax: 3 }] } };
  const X = paxIndex({ ep, sched, hi: 2000 });
  assert.equal(paxIndex({ ep: { occ: {} } }), null);
  assert.deepEqual(paxAt(X, 640), { missed: 0, rebooked: 0, stranded: 0, home: 0, held: 1, heldPax: 12 });
  assert.deepEqual(paxAt(X, 725), { missed: 6, rebooked: 4, stranded: 2, home: 0, held: 1, heldPax: 12 });
  assert.deepEqual([...strandedPaxAt(X, 725)], [["BOS", 2]]);
  assert.equal(strandedPaxAt(X, 1800).size, 0);
  assert.deepEqual(disrupted(X, { sched }).map((x) => [x.s.flight, x.evs.length]), [["B6 2", 1], ["B6 1", 3]]);
  assert.deepEqual(asksAt(X, 612), { sent: 2, open: 1, accepted: 1 });
  const P = { sched, exec: new Map() };
  assert.deepEqual(waitingAt(X, P, 690), []);
  assert.deepEqual(waitingAt(X, P, 725), [{ to: 3, at: "BOS", pax: 4, dep: 900 }]);
  assert.deepEqual(waitingAt(X, { sched, exec: new Map([[3, { id: 3, status: "flown", dep: 720 }]]) }, 725), []);
  assert.deepEqual(flowsAt(X, P, 704), [{ at: "BOS", dest: "SYR", pax: 4, alpha: 0.5 }]);
  assert.deepEqual(flowsAt(X, P, 930).map((f) => f.alpha.toFixed(2)), ["0.60"]);
  assert.deepEqual(flowsAt(X, P, 945), []);
});

test("the customer desk's connections at risk read off its page, each departure with its connecting passengers and misses", () => {
  const page = ["# Customer desk, JetBlue, 2025-07-08, 19:00 (watch 16 of 37, customer turn)", "", "## Messages", "R48 accepted: ok.", "",
    "## Connections at risk: departures in the next 4 h whose booked connecting passengers make it with 15 min or less (negative), both flights flying as projected",
    "id flight route dep [committed]: inbound station in(arrival) margin pax; misses, the hold that makes them, no later seat today",
    "f500 95 BOS-PDX 19:41: f352 JFK in 19:46 -50 3, f385 DCA in 20:20 -84 1, f414 JFK in 18:56 +0 5; misses 4, hold to 21:05 makes them, 4 have no later seat today",
    "f537 473 JFK-BNA 20:21 committed: f361 SJU in 19:33 +3 2", "20 more: cust_flights(status=\"attention\")", "", "## Flights ops might cancel in the next 6 h",
    "f321 1084 MCO-JFK 13:50 143(0/3) 579 min late"].join("\n");
  assert.deepEqual(atRisk(page), { more: 20, rows: [{ id: 500, orig: "BOS", dest: "PDX", dep: "19:41", pax: 9, misses: 4, noSeat: 4 },
    { id: 537, orig: "JFK", dest: "BNA", dep: "20:21", pax: 2, misses: 0, noSeat: 0 }] });
  assert.deepEqual(atRisk(null), { rows: [], more: 0 });
});
