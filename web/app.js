// AirOpsEnv viewer: ?episode=<url> replays one episode on its virtual clock; ?index=<url> lists episodes. Exposes
// window.airops {ready, view(name), show(step), time(minute)} so a recorder can drive it frame by frame.
import { STATUS, capsAt, countersAt, esc, flightsAt, hhmm, hubState, hubVisits, positionsAt, prepare, short, stepAt } from "./model.js";
import { MapView, aircraftMesh, framing, loadIcon, movement, runways, slots } from "./map.js";
import { TailChart } from "./chart.js";
import { CREDIT, OpsPanel, header } from "./panel.js";

const params = new URLSearchParams(location.search);
const VIEWS = { network: ["network", "hub"], hub: ["hub", "network"], region: ["region", "hub"] };
const SCALE = { A32X: 1, A220: 0.95, E190: 0.9 };
const SPEEDS = [5, 10, 20, 40, 80];

let resolveReady, rejectReady;
window.airops = {
  ready: new Promise((res, rej) => ((resolveReady = res), (rejectReady = rej))),
  view: async () => {},
  show: async () => {},
  time: async () => {},
};

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

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

async function indexPage(app, url) {
  const index = await getJSON(url);
  const rows = index.episodes.map((e) => {
    const href = `?episode=${encodeURIComponent(new URL(e.url, url).href)}`;
    const gap = e.anchor ? (100 * (e.cost - e.anchor)) / e.anchor : null;
    return `<tr><td><a href="${href}">${esc(e.task_id)}</a></td><td>${esc(e.hub)}</td><td>${esc(e.date)}</td><td>${esc(e.model_label ?? e.model)}</td><td>${esc(e.run)}</td>
      <td class="num mono">${e.reward?.toFixed(3) ?? "–"}</td><td class="num mono">${short(e.cost)}</td><td class="num mono">${gap != null ? `${gap >= 0 ? "+" : ""}${gap.toFixed(1)}%` : "–"}</td></tr>`;
  }).join("");
  app.innerHTML = `<div class="overview"><header class="hd"><div class="brand">AirOps<b>Env</b></div><div class="title"><h1>Episodes</h1><div class="sub">${index.episodes.length} recorded runs · ${esc(url.pathname.split("/").pop())}</div></div></header>
    <table class="eps"><thead><tr><th>task</th><th>hub</th><th>date</th><th>planner</th><th>run</th><th class="num">reward</th><th class="num">cost</th><th class="num">vs hindsight</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="muted small">Costs in passenger-minutes. ${CREDIT}</p></div>`;
  resolveReady({ episodes: index.episodes.length });
}

async function episodePage(app, url) {
  const ep = await getJSON(url);
  const P = prepare(ep, url);
  const twin = ep.twin?.url ? await getJSON(new URL(ep.twin.url, url)).then((geojson) => ({ geojson, center: geojson.center })).catch(() => null) : null;
  const radar = P.radar ? radarFrames(P.radar, url) : null;
  document.title = `${ep.task.hub} ${ep.task.date} · ${ep.model} · AirOpsEnv`;
  app.innerHTML = `<div class="viewer">${header(P)}
    <div class="stage">
      <div class="maps"><div class="map main" id="map-main"></div>
        <div class="map inset" id="map-inset"></div>
        <div class="maptag main-tag" id="tag-main"></div><div class="maptag inset-tag" id="tag-inset"></div>
        <div class="radar-key" ${radar ? "" : "hidden"}><span>NEXRAD</span><i></i><span>10</span><span>30</span><span>50 dBZ</span></div>
        <div class="views" role="group" aria-label="Camera">${Object.keys(VIEWS).map((v) => `<button data-view="${v}">${v}</button>`).join("")}</div>
      </div>
      <div class="chart" id="chart"></div>
      <div class="scrub" id="scrub"></div>
    </div>
    <aside class="ops" id="ops"></aside></div>`;
  const [icons, mesh] = [await loadIcon(), aircraftMesh()];
  const main = new MapView(document.getElementById("map-main"), P, { twin, radar, role: "main", interactive: !params.get("record") });
  const inset = new MapView(document.getElementById("map-inset"), P, { twin, radar, role: "inset", interactive: !params.get("record") });
  for (const m of [main, inset]) Object.assign(m, { icons, mesh });
  const chart = new TailChart(document.getElementById("chart"), P);
  const panel = new OpsPanel(document.getElementById("ops"), P);
  const stands = twin ? slots(twin) : [];
  if (twin) Object.assign(twin, framing(stands));
  const visits = hubVisits(P), parkAt = assignSlots(visits, stands), strips = twin ? runways(twin) : [];
  const kLat = Math.cos((P.task.airports[P.task.hub].lat * Math.PI) / 180), lags = runwayQueue(P);
  const scrub = scrubber(document.getElementById("scrub"), P);
  let t = clampT(Number(params.get("t") ?? P.task.start)), selected = null, viewName = VIEWS[params.get("view")] ? params.get("view") : "network";
  let pending = null, busy = false;

  function clampT(m) {
    return Math.max(P.t0, Math.min(P.end, m));
  }

  function frameAt(m) {
    const flights = flightsAt(P, m), byId = new Map(flights.map((f) => [f.s.id, f]));
    const caps = capsAt(P, m), nominal = ep.nominal ?? {}, h = String(Math.floor(m / 60)), rings = new Map();
    for (const code of Object.keys(ep.task.airports)) {
      const cap = caps[`${code}:arr`]?.[h], nom = nominal[`${code}:arr`]?.[h];
      if (cap === 0) rings.set(code, { rgb: STATUS.cancelled.rgb, label: "stopped" });
      else if (cap != null && nom != null && cap < nom) rings.set(code, { rgb: STATUS.delayed.rgb, label: `${cap}/h` });
    }
    const parked = parkAt(m).map(({ v, slot }) => {
      const next = v.next != null ? byId.get(v.next) : null;
      return { pos: slot.pos, heading: slot.heading, scale: SCALE[v.family] ?? 1, cls: next ? next.cls : "scheduled",
        label: next ? `${next.s.flight.replace(" ", "")} ${hhmm(next.r.dep)}` : "" };
    });
    for (const f of flights) {
      const m2 = movement(f, m, ep.task.hub, strips, kLat, lags.get(f.s.id) ?? 0);
      if (m2) parked.push({ ...m2, scale: SCALE[f.s.family] ?? 1, cls: f.cls, label: `${f.s.flight.replace(" ", "")} ${f.s.dest === ep.task.hub ? "arriving" : "departing"}` });
    }
    return { t: m, key: `${Math.round(m)}|${selected}`, cls: Math.round(m), flights, positions: positionsAt(P, flights, m), rings, parked,
      radar: radar?.at(m) ?? null };
  }

  let lastFrame = null;
  function draw(m) {
    const f = (lastFrame = frameAt(m));
    main.update(f);
    inset.update(f);
    const w = P.watches.findLast((v) => v.t <= m);
    const changed = new Set(w && m - w.t < 60 ? (w.changes ?? []).map((c) => c.id) : []);
    chart.render(m, f.flights, { changed });
    const step = selected ?? stepAt(P, m);
    panel.render(m, countersAt(P, m >= P.end - 25 ? Infinity : m, f.flights), step);
    scrub.update(m);
    tags(m);
  }

  function tags(m) {
    const hubName = twin?.geojson?.carrier_terminal ? `${ep.task.hub} · ${twin.geojson.carrier_terminal}` : ep.task.hub;
    const [a, b] = VIEWS[viewName];
    const name = (cam) => (cam === "hub" ? `${hubName} · 3D` : cam === "region" ? `${ep.task.hub} region` : `Network · ${Object.keys(ep.task.airports).length} airports`);
    const hs = hubState(P, m), stop = hs.arr.stop ? "arrivals stopped" : hs.arr.tight ? `arrivals ${hs.arr.cap}/h` : "";
    for (const [id, cam] of [["tag-main", a], ["tag-inset", b]]) {
      const el = document.getElementById(id), alert = cam !== "network" && stop;
      el.innerHTML = `${esc(name(cam))}${alert ? ` · <span class="${hs.arr.stop ? "stop" : "tight"}">${ep.task.hub} ${alert}</span>` : ""}`;
    }
  }

  async function setTime(m, { wait = true } = {}) {
    t = clampT(m);
    if (radar && wait) await radar.load(t);
    draw(t);
    if (wait) await Promise.all([main.idle(), inset.idle()]).then(frames);
  }

  function applyView(name) {
    viewName = VIEWS[name] ? name : "network";
    const [a, b] = VIEWS[viewName];
    main.setCamera(a);
    inset.setCamera(b);
    for (const btn of document.querySelectorAll(".views button")) btn.classList.toggle("on", btn.dataset.view === viewName);
    draw(t);
  }

  scrub.onInput = (m) => {
    selected = null;
    pending = m;
    if (!busy) {
      busy = true;
      requestAnimationFrame(() => {
        busy = false;
        draw(clampT(pending));
        t = clampT(pending);
        radar?.load(t);
      });
    }
  };
  let playing = false, last = 0, speed = 20;
  scrub.onPlay = () => {
    playing = !playing;
    scrub.playing(playing);
    last = performance.now();
    if (playing) requestAnimationFrame(tick);
  };
  scrub.onSpeed = (v) => (speed = v);
  function tick(now) {
    if (!playing) return;
    const dt = (now - last) / 1000;
    last = now;
    selected = null;
    t = clampT(t + dt * speed);
    draw(t);
    radar?.load(t);
    if (t >= P.end) {
      playing = false;
      scrub.playing(false);
      return;
    }
    requestAnimationFrame(tick);
  }
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea")) return;
    if (e.key === " ") (e.preventDefault(), scrub.onPlay());
    else if (e.key === "ArrowRight" || e.key === "ArrowLeft") setTime(t + (e.key === "ArrowRight" ? 10 : -10), { wait: false });
    else if (e.key === "]" || e.key === "[") {
      const ws = P.watches.map((w) => w.t), next = e.key === "]" ? ws.find((x) => x > t) : ws.findLast((x) => x < t);
      if (next != null) setTime(next, { wait: false });
    }
  });
  for (const btn of document.querySelectorAll(".views button")) btn.addEventListener("click", () => applyView(btn.dataset.view));
  new ResizeObserver(() => {
    main.resize();
    inset.resize();
    draw(t);
  }).observe(document.querySelector(".stage"));

  await Promise.all([main.loaded, inset.loaded]);
  applyView(viewName);
  await setTime(t);
  window.airops = {
    ready: window.airops.ready,
    async view(name) {
      applyView(name);
      await Promise.all([main.idle(), inset.idle()]).then(frames);
    },
    async show(k) {
      selected = k;
      await setTime(P.steps[k]?.t ?? t);
    },
    async time(m) {
      await setTime(m);
    },
    state: () => ({ t, view: viewName, step: selected, airborne: lastFrame?.positions.length, parked: lastFrame?.parked.length,
      phases: lastFrame?.flights.reduce((a, f) => ((a[f.phase] = (a[f.phase] ?? 0) + 1), a), {}) }),
    maps: { main, inset },
  };
  resolveReady({ steps: P.steps.length, watches: P.watches.map((w) => w.t), start: ep.task.start, t0: P.t0, end: P.end,
    horizon: P.end, last_watch: P.lastWatch, views: Object.keys(VIEWS), radar: radar?.count ?? 0, twin: !!twin });
}

const frames = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

/** Movements at the hub in the same minute go one after another on the runway: 1.2 min apart, in id order. */
function runwayQueue(P) {
  const lags = new Map(), seen = new Map();
  for (const e of [...P.ep.executed].filter((x) => x.status === "flown").sort((a, b) => a.dep - b.dep || a.id - b.id)) {
    const s = P.sched.get(e.id);
    for (const [kind, at] of [["d", s.orig === P.task.hub && e.dep], ["a", s.dest === P.task.hub && e.arr]]) {
      if (at === false) continue;
      const n = seen.get(`${kind}${at}`) ?? 0;
      seen.set(`${kind}${at}`, n + 1);
      if (n) lags.set(e.id, n * 1.2);
    }
  }
  return lags;
}

/** Each hub visit gets the first slot free for its whole stay, in order of arrival. */
function assignSlots(visits, all) {
  const busyUntil = all.map(() => -Infinity), placed = [];
  for (const v of visits) {
    const k = busyUntil.findIndex((u) => u + 10 <= v.from);
    if (k < 0) continue;
    busyUntil[k] = v.to;
    placed.push({ v, slot: all[k] });
  }
  return (m) => placed.filter(({ v }) => v.from <= m && m < v.to);
}

/** Radar frames as images, decoded on demand; at(t) cross-fades the two around t. */
function radarFrames(radar, base) {
  const frames = radar.frames.map((f, k) => ({ ...f, k, url: new URL(f.url, base).href, img: null, ready: null }));
  const get = (f) => {
    if (!f.ready) {
      f.img = new Image();
      f.img.crossOrigin = "anonymous";
      f.img.dataset.k = String(f.k);
      f.img.src = f.url;
      f.ready = f.img.decode().then(() => true, () => false);
      f.ready.then((ok) => (f.ok = ok));
    }
    return f.ready;
  };
  const around = (t) => {
    let i = 0;
    while (i + 1 < frames.length && frames[i + 1].t <= t) i++;
    const a = frames[i], b = frames[Math.min(i + 1, frames.length - 1)];
    const f = b === a || t <= a.t ? 0 : Math.min(1, (t - a.t) / (b.t - a.t));
    return [a, b, f];
  };
  let warm = 0;
  const preload = () => {
    for (let n = 0; n < 4 && warm < frames.length; n++) get(frames[warm++]);
    if (warm < frames.length) setTimeout(preload, 120);
  };
  setTimeout(preload, 500);
  return {
    count: frames.length,
    bounds: radar.bounds,
    load: (t) => Promise.all(around(t).slice(0, 2).map(get)),
    at(t) {
      const [a, b, f] = around(t);
      if (t > frames.at(-1).t + (radar.step ?? 10) || t < frames[0].t - (radar.step ?? 10)) return null;
      get(a);
      get(b);
      return { a: a.ok ? a.img : null, b: b.ok ? b.img : null, f: a.ok && b.ok ? f : a.ok ? 0 : 1 };
    },
  };
}

function scrubber(el, P) {
  const lo = P.t0, hi = P.end, pct = (m) => (100 * (m - lo)) / (hi - lo);
  const marks = P.watches.map((w, i) => `<i class="wm" style="left:${pct(w.t)}%" title="watch ${i + 1} · ${hhmm(w.t)}"><b>${i + 1}</b></i>`).join("");
  const plans = P.steps.filter((s) => s.tool !== "advance" && s.tool !== "get_situation").map((s) => `<i class="sm" style="left:${pct(s.t)}%"></i>`).join("");
  const win = `<i class="win" style="left:${pct(P.task.window[0])}%;width:${pct(P.task.window[1]) - pct(P.task.window[0])}%"></i>`;
  el.innerHTML = `<button class="play" aria-label="Play">▶</button>
    <select class="speed" aria-label="Speed">${SPEEDS.map((v) => `<option value="${v}" ${v === 20 ? "selected" : ""}>${v} min/s</option>`).join("")}</select>
    <div class="track"><div class="rail">${win}${marks}${plans}<i class="fill"></i></div>
      <input type="range" min="${lo}" max="${hi}" step="1" value="${lo}" aria-label="Virtual clock"></div>
    <span class="now mono"></span>`;
  const input = el.querySelector("input"), now = el.querySelector(".now"), fill = el.querySelector(".fill"), play = el.querySelector(".play");
  const api = { onInput: null, onPlay: null, onSpeed: null };
  input.addEventListener("input", () => api.onInput?.(Number(input.value)));
  play.addEventListener("click", () => api.onPlay?.());
  el.querySelector(".speed").addEventListener("change", (e) => api.onSpeed?.(Number(e.target.value)));
  api.update = (m) => {
    input.value = String(Math.round(m));
    now.textContent = hhmm(m);
    fill.style.width = `${pct(m)}%`;
  };
  api.playing = (on) => ((play.textContent = on ? "❚❚" : "▶"), play.setAttribute("aria-label", on ? "Pause" : "Play"));
  return api;
}
