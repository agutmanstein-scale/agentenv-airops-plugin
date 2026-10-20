// AirOpsEnv v1 viewer: ?episode=<url> replays one episode on its clock, the 3D airport filling the screen with the US
// map in a corner, cards over them and a timeline below; ?index=<url> lists episodes. window.airops {ready, time(t),
// view(name), show(step), shot(spec), state()} lets a recorder drive it frame by frame.
import { HUB_NAMES, cities, esc, flightsAt, positionsAt, prepare, programBands, watchAt } from "./model.js";
import { Board, Desk, Header, Score, Timeline } from "./cards.js";
import { Room } from "./room.js";
import { Views } from "./inset.js";
import { ATTRIBUTION, rings } from "./map.js";
import { CREDIT, Expert } from "./panel.js";

const params = new URLSearchParams(location.search);
const RECORD = params.get("record") === "1";
const SPEEDS = [0.25, 1, 2, 5, 10, 20, 40];
const STAGE_VIEWS = ["tower", "gate", "threshold", "harbour", "follow", "crew"];
const ROOM_VIEWS = ["room", "room-desks", "room-wall"];
const ALIASES = { hub: "tower", region: "network", map: "network", center: "room" };
const KEYS = { 1: "tower", 2: "gate", 3: "threshold", 4: "harbour", 5: "follow", 6: "room", 7: "crew", m: "network" };
const CARRIERS = { AA: "an American", AS: "an Alaska", DL: "a Delta", UA: "a United", WN: "a Southwest", NK: "a Spirit", F9: "a Frontier", G4: "an Allegiant",
  SY: "a Sun Country", HA: "a Hawaiian", MX: "a Breeze", YX: "a Republic", "9E": "an Endeavor", OH: "a PSA", MQ: "an Envoy", OO: "a SkyWest", YV: "a Mesa",
  C5: "a CommuteAir", ZW: "an Air Wisconsin", PT: "a Piedmont", G7: "a GoJet", QX: "a Horizon" };
const CAMERAS = { tower: ["Tower", "From the control tower"], gate: ["Gate", "At JetBlue's gates"], threshold: ["Runway", "At the end of the runway in use"],
  harbour: ["Harbour", "Across the water"], follow: ["Follow", "Behind an aircraft (or click one)"], network: ["Map", "The US map, full screen"],
  room: ["Room", "The control center, where the desks work"], crew: ["Crew", "With a crew at the airport"] };

let resolveReady, rejectReady;
window.airops = {
  ready: new Promise((res, rej) => ((resolveReady = res), (rejectReady = rej))),
  time: async () => {},
  view: async () => {},
  show: async () => {},
  shot: async () => {},
  state: () => ({}),
};
if (RECORD) document.documentElement.classList.add("recording");
if (params.get("ui") === "0") document.documentElement.classList.add("bare");

boot();

async function boot() {
  const app = document.getElementById("app");
  try {
    if (params.get("episode")) await episodePage(app, new URL(params.get("episode"), location.href));
    else await indexPage(app, new URL(params.get("index") ?? "../cache/episodes/index.json", location.href));
  } catch (e) {
    console.error(e);
    app.innerHTML = `<div class="error"><h2>The viewer could not load</h2><pre>${esc(e.stack ?? e)}</pre><p>Pass <code>?episode=URL</code> (an episode JSON) or <code>?index=URL</code> (a list of episodes).</p></div>`;
    rejectReady(e);
  }
}

/** The rects of the page over the airport, in the stage's pixels, which its tags keep out of. */
function covered(root) {
  if (root.dataset.main !== "stage") return [];
  const box = root.querySelector(".stage-host").getBoundingClientRect();
  return [...root.querySelectorAll(".card, .view.corner, .timeline, .credit, .follow-chip, .expert")].filter((el) => el.checkVisibility({ visibilityProperty: true }))
    .map((el) => el.getBoundingClientRect()).filter((r) => r.width && r.height).map((r) => [r.left - box.left, r.top - box.top, r.right - box.left, r.bottom - box.top]);
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  const buf = new Uint8Array(await r.arrayBuffer());
  if (buf[0] !== 0x1f || buf[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(buf));
  return JSON.parse(await new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"))).text());
}

const longDate = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).replace(",", "");

const V2 = { title: "AirOpsEnv v2: the ops control center", sub: "Run an airline's control center through a real storm day",
  intro: "Each run puts a model in JetBlue's operations control center for a whole storm day, from 04:00: an ops controller runs the flights, a crew scheduler the pilots and flight attendants, and they ask each other for holds, cancellations and cover. The day is flown on what really happened and scored against the best plan found. Pick one to replay it." };
const V1 = { title: "AirOpsEnv v1: the operations desk", sub: "Recover an airline's storm day at JFK or Boston",
  intro: "Each run puts a model at JetBlue's operations desk through a real storm day. Every hour it reads the FAA's advisories and the weather and may cancel or hold flights; the day is then flown on what really happened and scored against the best plan in hindsight. Pick one to replay it." };

async function indexPage(app, url) {
  const index = await getJSON(url);
  const v2 = index.episodes.filter((e) => e.occ), v1 = index.episodes.filter((e) => !e.occ);
  document.documentElement.classList.add("light");
  document.title = `${v2.length ? V2.title : "AirOpsEnv v1"} · episodes`;
  const card = (e) => {
    const href = `?episode=${encodeURIComponent(new URL(e.url, url).href)}`;
    const gap = e.anchor ? (100 * (e.cost - e.anchor)) / e.anchor : null;
    const thumb = e.thumb ? ` style="background-image:url('${esc(new URL(e.thumb, url).href)}')"` : "";
    return `<a class="ep-card" href="${href}"><div class="thumb${e.thumb ? "" : " none"}"${thumb}><span>${esc(e.hub)}</span>${e.featured ? "<em>Featured</em>" : ""}</div>
      <div class="body"><h2>${esc(HUB_NAMES[e.hub] ?? e.hub)} · ${esc(longDate(e.date))}</h2><p class="model">${esc(e.model_label ?? e.model)}${e.occ ? ` · ${esc(e.occ)}` : ""}</p>
      <dl><div><dt>Reward</dt><dd>${e.reward?.toFixed(3) ?? "–"}</dd></div><div><dt>${e.occ ? "Cost vs best plan" : "Cost vs hindsight"}</dt><dd>${gap != null ? `${gap >= 0 ? "+" : ""}${gap.toFixed(1)}%` : "–"}</dd></div></dl>
      <p class="id">${esc(e.task_id)} · ${esc(e.run)}</p></div></a>`;
  };
  const board = (e) => (e.run ?? "").startsWith("occ-board");
  const rank = (e) => (e.occ ? [e.featured ? 0 : 1, e.featured ?? 0, board(e) ? 0 : 1, e.date, e.occ === "two desks" ? 0 : 1] : [0, 0, 0, "", -(e.reward ?? -1)]);
  const cmp = (x, y) => (typeof x === "string" ? x.localeCompare(y) : x - y);
  const order = (list) => [...list].sort((a, b) => rank(a).reduce((d, x, i) => d || cmp(x, rank(b)[i]), 0));
  const grid = (list) => `<div class="grid">${order(list).map(card).join("")}</div>`;
  const head = (h) => `<h1>${esc(h.title)}</h1><p class="sub">${esc(h.sub)}</p><p class="intro">${esc(h.intro)}</p>`;
  const sections = [];
  if (v2.length) sections.push(`<header>${head(V2)}${note(v2)}</header>${grid(v2)}`);
  if (v1.length) sections.push(`<header class="${v2.length ? "next" : ""}">${head(v2.length ? V1 : { ...V1, title: "AirOpsEnv v1" })}</header>${grid(v1)}`);
  app.innerHTML = `<div class="list">${sections.join("")}
    <p class="credit">Reward from 0.2 to 1: 1 is the best plan ${v2.length ? "found (v2) or the best plan in hindsight (v1)" : "in hindsight"}. Cost in passenger-minutes. ${esc(CREDIT)}</p></div>`;
  resolveReady({ episodes: index.episodes.length, v2: v2.length, v1: v1.length });
}

/** The board's result in a line: each setup's mean reward over the board's days, from the list's own board runs. */
function note(list) {
  const runs = list.filter((e) => (e.run ?? "").startsWith("occ-board"));
  if (!runs.length) return "";
  const by = new Map();
  for (const e of runs) by.set(e.occ, [...(by.get(e.occ) ?? []), e.reward]);
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const parts = [...by].sort((a, b) => mean(b[1]) - mean(a[1])).map(([label, rs]) => `${mean(rs).toFixed(3)} with ${label}`);
  const days = new Set(runs.map((e) => e.task_id)).size, model = runs[0].model_label ?? runs[0].model;
  return `<p class="note">The board: ${esc(model)} on ${days} storm days scores a mean reward of ${esc(parts.join(" and "))}. 1 is the best plan found; the standard crew desk scores about 0.31.</p>`;
}

async function episodePage(app, url) {
  const ep = await getJSON(url);
  const P = prepare(ep, url), task = ep.task;
  let doc = null, traffic = null, stageUrl = null;
  if (ep.stage) {
    stageUrl = new URL(ep.stage.url, url);
    [doc, traffic] = await Promise.all([getJSON(stageUrl), getJSON(new URL(ep.stage.traffic, url))]).catch((e) => (console.warn("no 3D airport:", e), [null, null]));
  }
  P.lo = traffic?.span?.[0] ?? task.start;
  P.hi = traffic?.span?.[1] ?? P.end;
  const city = cities(task, traffic), cityOf = (c) => city.get(c) ?? c, model = ep.model_label ?? ep.model, brand = ep.occ ? "AirOpsEnv v2" : "AirOpsEnv v1";
  const stands = new Map(), terminals = new Map();
  for (const ac of traffic?.aircraft ?? []) {
    if (!ac.task) continue;
    for (const v of ac.visits) for (const [k, l] of [["arr", v.arr], ["dep", v.dep]]) if (l?.tag) stands.set(`${k}|${l.flight}`, v.stand);
  }
  for (const ref of stands.values()) if (ref.includes("-")) terminals.set(ref.split("-")[0], (terminals.get(ref.split("-")[0]) ?? 0) + 1);
  const home = [...terminals].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  for (const [k, ref] of stands) stands.set(k, home && ref.startsWith(`${home}-`) ? ref.slice(home.length + 1) : ref.replace("-", " "));
  document.title = `${HUB_NAMES[task.hub] ?? task.hub} ${task.date} · ${model} · ${brand}`;
  app.innerHTML = `<div class="ep">
    <div class="view view-stage"><div class="stage-host"></div><div class="inset-caption">The airport · click to swap</div></div>
    ${ep.occ ? `<div class="view view-room" hidden><div class="room-host"></div><div class="inset-caption">The control center · click to swap</div></div>` : ""}
    <div class="view view-map"><div class="map"></div><div class="inset-caption">US radar and JetBlue's flights in the air · click to swap</div>
      <div class="radar-key" ${P.radar ? "" : "hidden"}><span>Radar: light</span><i></i><span>heavy</span></div></div>
    <div class="cards left"><section class="card head"></section><section class="card desk"></section></div>
    <div class="cards right"><section class="card score"></section><section class="card board"></section></div>
    <div class="follow-chip" hidden></div>
    <aside class="expert" hidden></aside>
    <footer class="timeline"></footer>
    <div class="credit"></div></div>`;
  const root = app.querySelector(".ep");
  let stage = null, tagAt = null;
  if (doc) {
    try {
      const { AirportStage, tagAt: tag } = await import("./stage/stage.js").catch(() => new Promise((r) => setTimeout(r, 800)).then(() => import("./stage/stage.js")));
      tagAt = tag;
      stage = new AirportStage(root.querySelector(".stage-host"), { stage: doc, traffic, base: new URL(".", stageUrl).href, manual: RECORD, controls: !RECORD, avoid: () => covered(root) });
      await stage.ready;
    } catch (e) {
      console.error("the 3D airport failed, showing the map:", e);
      stage?.dispose?.();
      stage = null;
    }
  }
  let center = null;
  if (ep.occ) {
    try {
      const { ControlCenter } = await import("./center/center.js").catch(() => new Promise((r) => setTimeout(r, 800)).then(() => import("./center/center.js")));
      center = new ControlCenter(root.querySelector(".room-host"), { P, ep, base: url.href, manual: RECORD, controls: !RECORD });
      await center.ready;
    } catch (e) {
      console.error("the control-center room failed:", e);
      center?.dispose?.();
      center = null;
    }
  }
  const views = new Views(root, P, { stage, room: center, interactive: !RECORD,
    onSwap: (main) => setView(main === "map" ? "network" : main === "room" ? roomView : stageView) });
  const ctx = { task, earlier: P.news, city: cityOf, hub: task.hub, sched: P.sched, flightOf: (ref) => P.sched.get(Number(String(ref).replace(/^f/, "")))?.flight };
  const header = new Header(root.querySelector(".head"), P, { model: ep.occ ? `${model} · ${ep.occ.label}` : model, weather: stage ? traffic.weather : null, brand });
  const desk = ep.occ ? new Room(root.querySelector(".desk"), P, { model }) : new Desk(root.querySelector(".desk"), P, { model, city: cityOf });
  const score = new Score(root.querySelector(".score"), P, { model });
  const board = new Board(root.querySelector(".board"), P, { city: cityOf, stands, terminal: home?.replace(/^T/, ""), rows: 8, scene: !!stage,
    scope: ep.occ ? "all of its flights here" : "the task's flights" });
  const expertEl = root.querySelector(".expert");
  let expert = null;
  const crews = !!traffic?.crews?.length;
  const cameras = [...(stage ? STAGE_VIEWS.filter((v) => v !== "crew" || crews) : []), ...(center ? ["room"] : []), ...(stage || center ? ["network"] : [])].map((id) => ({ id,
    label: id === "harbour" && task.hub === "JFK" ? "Bay" : CAMERAS[id][0], title: `${CAMERAS[id][1]} (${Object.keys(KEYS).find((k) => KEYS[k] === id)})` }));
  const speed0 = SPEEDS.includes(Number(params.get("speed"))) ? Number(params.get("speed")) : 5;
  const timeline = new Timeline(root.querySelector(".timeline"), P, { bands: programBands(P), speeds: SPEEDS, speed: speed0, cameras });
  const shown = ep.occ ? "Gates, taxi routes, runways in use, the crew room, crews' walks, van routes and the control-center room are inferred, for display"
    : "Gates, taxi routes and runways in use are inferred, for display";
  root.querySelector(".credit").textContent = `${stage ? `${shown} · airport © OpenStreetMap contributors (ODbL), imagery USGS · ` : ""}${ATTRIBUTION} · flights BTS · weather NWS, IEM${ep.occ ? " · crews: generated pairings under FAR 117 and 14 CFR 121.467" : ""}`;
  const followChip = root.querySelector(".follow-chip");

  const moves = ep.executed.filter((e) => e.status === "flown").flatMap((e) => [P.sched.get(e.id).orig === task.hub ? e.dep : null, P.sched.get(e.id).dest === task.hub ? e.arr : null]).filter((m) => m != null);
  const quiet = (m) => m > P.lastWatch + 60 && !moves.some((x) => x >= m - 5 && x <= m + 30);
  let t = clamp(Number(params.get("t") ?? P.lo)), step = null, viewName = stage ? "tower" : center ? "room" : "network", stageView = "tower", roomView = "room", following = null;
  let playing = false, speed = speed0, rate = null, boost = 1, last = performance.now(), lastMap = -1, busy = false, busyRoom = false;

  function clamp(m) {
    return Math.max(P.lo, Math.min(P.hi, Number.isFinite(m) ? m : P.lo));
  }

  function draw(m, { force = false } = {}) {
    const flights = flightsAt(P, m);
    header.render(m, { rate, quiet: boost > 1 });
    desk.render(m, step);
    score.render(m);
    board.render(m, flights);
    timeline.update(m);
    if (expert && !expertEl.hidden) expert.render(m, flights, step);
    if (force || RECORD || Math.abs(m - lastMap) >= 0.25) {
      lastMap = m;
      views.map.update({ positions: positionsAt(P, flights, m), rings: rings(P, m), radar: views.radar?.at(m) ?? null, t: m });
    }
    if (following) followChip.innerHTML = followText(m);
  }

  function followText(m) {
    const ac = traffic?.aircraft.find((a) => a.key === following);
    if (!ac) return "";
    const tag = ac.task ? tagAt?.(ac, m) : null;
    const what = tag?.text ?? (ac.task ? "a JetBlue aircraft of the task" : ac.jetblue ? "a JetBlue aircraft outside the task" : `${CARRIERS[ac.carrier] ?? ac.carrier} aircraft`);
    return `Following <b>${esc(what)}</b> <span>${esc(ac.type ?? "")} ${esc(ac.tail)}</span> <button type="button">Stop · Esc</button>`;
  }

  async function setView(name, opts = {}) {
    name = ALIASES[name] ?? name;
    if (center && ROOM_VIEWS.includes(name)) {
      views.show("room");
      viewName = roomView = name;
      following = null;
      await center.view(name, opts);
    } else if (name === "network" || !stage) {
      views.show("map");
      viewName = "network";
      following = null;
    } else {
      views.show("stage");
      viewName = stageView = STAGE_VIEWS.includes(name) ? name : "tower";
      if (viewName === "follow") opts = { ...opts, key: opts.key ?? following ?? nextFeatured(t) };
      following = viewName === "follow" ? opts.key ?? null : null;
      await stage.view(viewName, opts);
    }
    followChip.hidden = !following;
    timeline.camera(viewName.startsWith("room") ? "room" : viewName);
    draw(t, { force: true });
    return viewName;
  }

  function nextFeatured(m) {
    const f = traffic?.featured ?? [], ours = (traffic?.aircraft ?? []).filter((a) => a.task).map((a) => a.key);
    const phase = (k) => stage?.pose(m, k)?.phase ?? "absent", moving = (k) => !["absent", "parked"].includes(phase(k));
    const soon = f.filter((x) => x.t >= m - 20 && x.t <= m + 90).map((x) => x.key);
    return soon.find(moving) ?? ours.find(moving) ?? [...soon, ...f.map((x) => x.key), ...ours].find((k) => phase(k) !== "absent") ?? f[0]?.key;
  }

  function setExpert(on) {
    if (on && !expert) expert = new Expert(expertEl, P, ctx);
    expertEl.hidden = !on;
    root.classList.toggle("expert-on", on);
    timeline.expert(on);
    draw(t, { force: true });
  }

  function play(on = !playing) {
    playing = on && t < P.hi;
    timeline.playing(playing);
    last = performance.now();
  }

  async function settle() {
    await Promise.all([views.map.idle(), new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))]);
  }

  const seek = (m) => {
    step = null;
    t = clamp(m);
    draw(t);
  };
  timeline.on("seek", seek).on("play", () => play()).on("speed", (v) => (speed = v)).on("camera", (id) => setView(id))
    .on("expert", () => setExpert(expertEl.hidden));
  followChip.addEventListener("click", (e) => e.target.closest("button") && setView("tower"));
  root.addEventListener("airops:seek", (e) => seek(Number(e.detail?.t)));
  stage?.onPick?.((key) => key && setView("follow", { key }));
  addEventListener("resize", () => views.resize());
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key;
    if (k === " ") {
      e.preventDefault();
      play();
    } else if (k === "ArrowRight" || k === "ArrowLeft") {
      e.preventDefault();
      seek(t + (k === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 60 : 10));
    } else if (k === "]" || k === "[") {
      const ws = P.watches.map((w) => w.t), to = k === "]" ? ws.find((x) => x > t + 0.5) : ws.findLast((x) => x < t - 0.5);
      if (to != null) seek(to);
    } else if (k === "Home" || k === "End") seek(k === "Home" ? P.lo : P.hi);
    else if (k === "+" || k === "=" || k === "-") {
      speed = SPEEDS[Math.max(0, Math.min(SPEEDS.length - 1, SPEEDS.indexOf(speed) + (k === "-" ? -1 : 1)))];
      root.querySelector(".speed").value = String(speed);
    } else if (KEYS[k] && (KEYS[k] === "network" || (KEYS[k] === "room" ? center : KEYS[k] === "crew" ? stage && crews : stage))) setView(KEYS[k]);
    else if (k === "Escape" && following) setView("tower");
    else if (k === "x") setExpert(expertEl.hidden);
  });

  function tick(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    boost = playing && quiet(t) ? 6 : 1;
    if (playing) {
      t = clamp(t + dt * speed * boost);
      if (t >= P.hi) play(false);
    }
    rate = playing ? speed * 60 * boost : null;
    draw(t);
    if (stage && !busy && views.visible("stage")) {
      busy = true;
      Promise.resolve(stage.time(t, { dt, speed: rate ?? 0 })).finally(() => (busy = false));
    }
    if (center && !busyRoom && views.visible("room")) {
      busyRoom = true;
      Promise.resolve(center.time(t, { dt, speed: rate ?? 0 })).finally(() => (busyRoom = false));
    }
    requestAnimationFrame(tick);
  }

  await views.map.loaded;
  await setView(params.get("view") ?? viewName);
  if (params.get("expert") === "1") setExpert(true);
  draw(t, { force: true });
  if (stage) await stage.time(t, { dt: 0, speed: 0 });
  if (center) await center.time(t, { dt: 0, speed: 0 });
  await settle();
  if (!RECORD) {
    if (params.get("play") === "1") play(true);
    requestAnimationFrame(tick);
  }

  window.airops = {
    ready: window.airops.ready,
    async time(m, opts = {}) {
      t = clamp(Number(m));
      rate = opts.speed ?? null;
      draw(t);
      if (stage && views.visible("stage")) await stage.time(t, { dt: opts.dt ?? 1 / 30, speed: rate ?? 0 });
      if (center && views.visible("room")) await center.time(t, { dt: opts.dt ?? 1 / 30, speed: rate ?? 0 });
      if (RECORD) await settle();
      return t;
    },
    async view(name, opts) {
      const v = await setView(name, opts);
      if (RECORD) await settle();
      return v;
    },
    async show(k) {
      step = k == null || P.steps[k] == null ? null : k;
      if (step != null) t = clamp(P.steps[step].t);
      draw(t, { force: true });
      if (stage && step != null) await stage.time(t, { dt: 0, speed: 0 });
      if (center && step != null) await center.time(t, { dt: 0, speed: 0 });
      if (RECORD) await settle();
    },
    async shot(spec) {
      const room = !!(spec?.room && center);
      if (views.main !== (room ? "room" : "stage")) await setView(room ? (ROOM_VIEWS.includes(spec.name) ? spec.name : "room") : stageView);
      return room ? center.shot(spec) : stage?.shot(spec);
    },
    expert: (on = true) => setExpert(on),
    events: () => stage?.events?.() ?? [],
    state: () => ({ t, view: viewName, main: views.main, step, following, expert: !expertEl.hidden, watch: watchAt(P, t),
      airborne: positionsAt(P, flightsAt(P, t), t).length, board: root.querySelectorAll(".board li:not(.none)").length,
      stage: stage?.state?.() ?? null, room: center?.state?.() ?? null }),
    stage,
    room: center,
    map: views.map,
  };
  resolveReady({ steps: P.steps.length, watches: P.watches.map((w) => w.t), start: task.start, t0: P.lo, end: P.hi, horizon: P.hi,
    last_watch: P.lastWatch, views: [...(stage ? STAGE_VIEWS : []), ...(center ? ROOM_VIEWS : []), "network"], radar: views.radar?.count ?? 0, twin: !!ep.twin,
    stage: !!stage, room: !!center, span: [P.lo, P.hi], featured: traffic?.featured ?? [], hub: task.hub });
}
