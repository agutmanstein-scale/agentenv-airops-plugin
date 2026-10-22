// The viewer live (?live=<base>): the day a live server (scripts/live_server.py; scripts/live_fake.py for tests) serves,
// played as it is decided (CONTRACT.md). It polls <base>/live/state.json and fetches <base>/live/episode.json when more
// of the day is decided, grows the page in place (app.js episodePage's update()), plays each newly decided hour over about the time
// the desks take to decide the next and eases to a hold where decisions end, never past them; the director
// (director.js) cuts between the room and the airport on the day's events; a live panel shows the run, each desk's
// latest move and the projected cost; at the day's end its score card, its best moments replayed and the next storm
// until the next day appears, which reloads the page. &stream lays it out for a 1920x1080 broadcast with nothing to
// click; the sound (web/audio.js, where the page mounts it as window.airops.sound) is updated from its frames.
import { HUB_NAMES, esc, hhmm, short } from "./model.js";
import { Director } from "./director.js";

const POLL = 3000;
const WATCH = 150;
/** Wall seconds the desks take over a watch until the day's own watches time them. */
const SPEED_MAX = 2.5;
const SPEED_END = 5;
/** The fastest the clock runs to catch up, in minutes of the day a second; once every watch is decided, through the
 * rest of the day's flying. */
const TAU = 4;
const STRETCH = 1.2;
/** Each decided hour is paced to take this share more than a watch is expected to, so the clock rarely stops. */
const SKIP = 60;
/** Minutes behind where it should be past which the clock jumps on at a cut to the room or the map. */
const SCORE_SECONDS = 22;
const HIGHLIGHT_SECONDS = 7;
const NEXT_SECONDS = 10;
/** The least the next-storm card shows, when the next day is already on. */
const CARD_KEY = "airops-live-card";
const DESKS = { ops: "Ops", crew: "Crew", customer: "Customer", one: "Desk" };
const BRAND = "AirOpsEnv";

const longDate = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).replace(",", "");
const utc = (d) => `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}:${String(d.getUTCSeconds()).padStart(2, "0")}`;
const runOf = (l) => (l ? l.run_id ?? l.instance_id ?? `${l.task_id ?? l.date}|${l.started_utc ?? ""}` : null);
const finished = (l) => ["done", "intermission"].includes(l?.status);
const keyOf = (l) => [runOf(l), l?.status, l?.decided_until].join("|");
const SETTLE = 20000;
/** Milliseconds after more of the day is decided that the episode is fetched once more, for the traffic sidecar the
 * server rebuilds after the watch's last decision. */

export async function livePage(app, base, { episodePage, params, ready }) {
  const stream = params.has("stream");
  document.documentElement.classList.add("live", ...(stream ? ["stream"] : []));
  const css = document.head.appendChild(Object.assign(document.createElement("link"), { rel: "stylesheet", href: new URL("./live.css", import.meta.url).href }));
  await Promise.race([new Promise((r) => css.addEventListener("load", r, { once: true })), sleep(3000)]);
  const feed = new Feed(base);
  const saved = sessionStorage.getItem(CARD_KEY);
  sessionStorage.removeItem(CARD_KEY);
  if (saved) app.innerHTML = saved;
  else app.innerHTML = `<div class="live-wait"><div class="live-card next"><small>${BRAND} · live</small><h1>Waiting for the live feed</h1><p>The next storm day appears here as soon as the run starts.</p></div></div>`;
  for (let n = 0; ; n++) {
    try {
      await feed.poll();
      if (feed.ep?.task && runOf(feed.ep.live) === runOf(feed.state) && !["intermission", "stale"].includes(feed.state.status)) break;
      if (feed.state?.status === "intermission" || (feed.state && feed.ep && runOf(feed.ep.live) !== runOf(feed.state))) {
        app.innerHTML = nextCard(feed.state, feed.ep);
      }
    } catch (e) {
      if (n % 10 === 0) console.warn("the live feed is not answering yet:", e.message);
    }
    if (n === 0) ready({ live: true, waiting: true });
    await sleep(POLL);
  }
  const live = new Live(feed, { stream, params });
  const page = await episodePage(app, new URL("live/episode.json", feed.root), live);
  live.attach(page, app);
  ready?.({ live: true });
  return live;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** A live episode as the recorded viewer reads one: what the day has yet to decide empty, and its cost and grade, which
 * only a finished day has, null before. */
function normal(ep) {
  if (!ep?.task) return ep;
  const done = finished(ep.live);
  for (const k of ["schedule", "executed", "watches", "steps", "news", "frozen", "ferries"]) ep[k] ??= [];
  if (ep.occ) for (const k of ["turns", "requests", "decisions", "notes", "pages"]) ep.occ[k] ??= [];
  ep.cost = { ...(ep.cost ?? {}), total: done ? ep.cost?.total ?? null : null };
  if (!done) ep.reward = ep.reward_L = null;
  return ep;
}

async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw Object.assign(new Error(`${url}: HTTP ${r.status}`), { status: r.status });
  const buf = new Uint8Array(await r.arrayBuffer());
  if (buf[0] !== 0x1f || buf[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(buf));
  return JSON.parse(await new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"))).text());
}

/** The live server's state, polled, and its episode, fetched again only when more of the day is decided. */
class Feed {
  constructor(base) {
    this.root = new URL(base.href.replace(/\/?$/, "/"));
    this.state = null;
    this.ep = null;
    this.key = null;
    this.light = true;
    this.at = 0;
  }

  /** The state; the episode too when more of the day is decided (and once more SETTLE after), or without a state. */
  async poll() {
    let st = null;
    if (this.light) {
      try {
        const s = await fetchJSON(new URL("live/state.json", this.root));
        st = s.live ?? s;
      } catch (e) {
        if (e.status !== 404) throw e;
        this.light = false;
      }
    }
    const now = performance.now();
    if (!st || !this.ep || keyOf(st) !== this.key || (this.settle && now >= this.settle)) {
      try {
        const ep = normal(await fetchJSON(new URL("live/episode.json", this.root)));
        const key = keyOf(ep.live);
        this.settle = key !== this.key ? now + SETTLE : null;
        this.key = key;
        this.ep = ep;
        st ??= ep.live;
      } catch (e) {
        if (e.status !== 404 || !st) throw e;
      }
    }
    this.state = st ?? this.state;
    this.at = performance.now();
  }
}

/** The clock: each decided hour played over about the wall time a watch takes, eased to a hold where decisions end. */
export class Pacer {
  constructor(t, frontier, now) {
    this.t = t;
    this.F = frontier;
    this.v = 0;
    this.cap = Infinity;
    this.max = SPEED_MAX;
    this.marks = [];
    this.seg = { s0: t, s1: frontier, w0: now, D: Math.max(1, ((frontier - t) / 60) * WATCH * STRETCH) };
  }

  /** Decisions final through minute F from wall second `now`; `end` once the day is. */
  advance(F, now, end = false) {
    this.max = end ? SPEED_END : SPEED_MAX;
    if (F === this.F) return;
    if (F > this.F) this.marks = [...this.marks, [now, F, F - this.F]].slice(-6);
    const v = Math.min(this.virtual(now), F);
    this.F = F;
    this.seg = { s0: v, s1: F, w0: now, D: Math.max(end ? 0 : this.watch() * STRETCH, (F - v) / this.max, 1) };
  }

  /** Wall seconds a watch (an hour) takes: the median of the latest ones seen decided one at a time, else the server's
   * mean over the day's watches, else a guess. */
  watch() {
    const xs = [];
    for (let i = 1; i < this.marks.length; i++) {
      const [w, , dF] = this.marks[i];
      if (dF > 0 && dF <= 90) xs.push(((w - this.marks[i - 1][0]) / dF) * 60);
    }
    xs.sort((a, b) => a - b);
    const W = xs.length >= 3 ? xs[xs.length >> 1] : this.wall ?? WATCH;
    return Math.max(4, Math.min(900, W));
  }

  virtual(now) {
    const { s0, s1, w0, D } = this.seg;
    return s0 + (s1 - s0) * Math.max(0, Math.min(1, (now - w0) / D));
  }

  lag(now = performance.now() / 1000) {
    return this.virtual(now) - this.t;
  }

  tick(now, dt) {
    const { s0, s1, w0, D } = this.seg, V = this.virtual(now), slope = now - w0 < D ? (s1 - s0) / D : 0;
    const want = Math.max(0, Math.min(this.cap, this.max, slope + (V - this.t) / TAU));
    this.v += (want - this.v) * (1 - Math.exp(-dt / 1.2));
    this.t = Math.min(this.F, this.t + this.v * dt);
    return { t: this.t, rate: this.v * 60 };
  }

  /** A cut: the clock at once no faster than the new shot wants, and far behind where it should be, on to near it. */
  cut(cap, jump, now = performance.now() / 1000) {
    this.cap = cap;
    this.v = Math.min(this.v, cap);
    if (jump && this.lag(now) > SKIP) this.t = Math.min(this.F, this.virtual(now) - SKIP / 3);
  }

  holding() {
    return this.F - this.t < 0.5 && this.v < 0.03;
  }
}

class Live {
  constructor(feed, { stream, params }) {
    this.feed = feed;
    this.stream = stream;
    this.params = params;
    this.ep = feed.ep;
    this.instance = runOf(feed.ep.live);
    this.applied = { key: keyOf(feed.ep.live), traffic: feed.ep.stage?.traffic ?? null };
    this.phase = "day";
    const l = feed.ep.live ?? {}, ws = feed.ep.watches ?? [];
    const lo = feed.ep.task.start, F = this.frontier(l);
    const t0 = finished(l) ? F : Math.max(lo, Math.min(F, ws.findLast((w) => w.t < F)?.t ?? lo));
    this.pacer = new Pacer(t0, F, performance.now() / 1000);
    this.view = "room";
    this.override = null;
  }

  /** Where the day may be played to: what is decided (the end of the day's flying once it is all decided), within
   * the page's span once it has one. */
  frontier(l) {
    const hi = this.page?.P.hi ?? Infinity;
    return Math.min(hi, l.decided_until ?? (finished(l) ? hi : this.ep.watches?.at(-1)?.t ?? this.ep.task.start));
  }

  /** app.js asks for the clock each frame. */
  clock(now, dt) {
    if (this.override) return this.override(now, dt);
    if (!this.page) return { t: this.pacer.t, rate: 0 };
    return this.pacer.tick(now / 1000, dt);
  }

  attach(page, app) {
    this.page = page;
    this.app = app;
    const lo = page.P.lo, hi = page.P.hi, done = finished(this.ep.live), F = this.frontier(this.ep.live ?? {});
    this.pacer.F = F;
    this.pacer.t = done ? F : Math.max(lo, Math.min(F, this.pacer.t));
    this.pacer.seg = { ...this.pacer.seg, s0: Math.min(this.pacer.seg.s0, this.pacer.F), s1: this.pacer.F };
    this.root = page.root;
    if (page.room) page.room.stagger = true;
    this.panel = new Panel(this.root, { stream: this.stream });
    this.cap = this.root.appendChild(Object.assign(document.createElement("div"), { className: "live-cap", hidden: true }));
    this.cap.innerHTML = "<small></small><b></b><span></span>";
    if (this.stream) {
      const model = this.ep.model_label ?? this.ep.model;
      this.root.appendChild(Object.assign(document.createElement("div"), { className: "live-title",
        innerHTML: `<b>${BRAND} <i>live</i></b><span>${esc(model)} runs JetBlue's control center through real storm days, one after another</span>` }));
      this.root.appendChild(Object.assign(document.createElement("div"), { className: "live-credit", innerHTML: credit() }));
    }
    this.director = new Director(page, { pacer: this.pacer, caption: (beat, t) => this.caption(beat, t), log: this.params.get("log") === "1" });
    this.panel.render(this.feed.state ?? this.ep.live, this);
    this.timer = setInterval(() => this.poll(), POLL);
    window.airops.live = this;
    if (done) this.score();
  }

  async poll() {
    if (this.polling) return;
    this.polling = true;
    try {
      await this.feed.poll();
      const st = this.feed.state, ep = this.feed.ep;
      if (runOf(st) !== this.instance) return this.nextDay(st);
      if (st.watch_wall_s) this.pacer.wall = st.watch_wall_s;
      if (runOf(ep.live) === this.instance && (keyOf(ep.live) !== this.applied.key || (ep.stage?.traffic ?? null) !== this.applied.traffic)) await this.grow(ep);
      this.panel.render(st, this);
    } catch (e) {
      this.panel.offline(e);
    } finally {
      this.polling = false;
    }
  }

  /** More of the day decided: the page grown, its traffic sidecar fetched if it changed, the clock let further. */
  async grow(ep) {
    this.ep = ep;
    let traffic = null;
    if (ep.stage?.traffic && ep.stage.traffic !== this.applied.traffic && this.page.stage) {
      traffic = await fetchJSON(new URL(ep.stage.traffic, new URL("live/episode.json", this.feed.root))).catch((e) => (console.warn("the traffic sidecar did not load:", e), null));
    }
    this.applied = { key: keyOf(ep.live), traffic: traffic || !this.page.stage ? ep.stage?.traffic ?? null : this.applied.traffic };
    await this.page.update(ep, traffic);
    this.director.index();
    this.pacer.advance(this.frontier(ep.live ?? {}), performance.now() / 1000, finished(ep.live));
  }

  /** Each frame: the director, the transitions at the day's end, the sound. */
  frame(t, now, dt) {
    const st = this.feed.state ?? this.ep.live ?? {};
    if (this.page.stage) this.page.stage.hidden = !this.page.views.visible("stage");
    if (this.phase === "day") {
      this.director.tick(t, now / 1000, this.pacer.v);
      if (finished(this.ep.live) && t >= this.pacer.F - 0.5) this.score();
    } else if (this.phase === "highlights") this.highlight(t, now / 1000);
    if (now - (this.drawn ?? 0) > 500) {
      this.drawn = now;
      this.panel.tick(st, this);
    }
    const sound = window.airops?.sound;
    if (sound && !this.mute) {
      const main = this.page.views.main;
      try {
        sound.update({ t, rate: this.page.rate() ?? 0, view: this.page.view(), main, phase: this.phase, holding: this.pacer.holding() },
          main === "room" ? this.page.room?.camera : main === "stage" ? this.page.stage?.camera : null);
      } catch (e) {
        console.warn("the live sound failed, off:", e);
        this.mute = true;
      }
    }
  }

  /** An error in a frame, logged once a minute at most so a day's stream goes on. */
  error(e) {
    const now = performance.now();
    if (now - (this.errAt ?? -1e9) > 60000) console.error("live frame:", e);
    this.errAt = now;
    this.errors = (this.errors ?? 0) + 1;
  }

  caption(beat, t) {
    const text = [beat?.kicker ?? "", beat?.caption ?? "", typeof beat?.subline === "function" ? beat.subline(t) : beat?.subline ?? ""];
    const key = text.join("|");
    if (this.capKey === key) return;
    this.capKey = key;
    const [k, c, s] = this.cap.children;
    k.textContent = text[0];
    c.textContent = text[1];
    s.textContent = text[2];
    this.cap.hidden = !text[1];
  }

  /** The day's score card, then its highlights. */
  score() {
    if (this.phase !== "day") return;
    this.phase = "score";
    this.caption(null);
    this.card = this.root.appendChild(Object.assign(document.createElement("div"), { className: "live-over", innerHTML: scoreCard(this.ep, this.feed.state ?? this.ep.live) }));
    this.root.classList.add("over");
    setTimeout(() => this.highlights(), SCORE_SECONDS * 1000);
  }

  async highlights() {
    this.card?.remove();
    this.root.classList.remove("over");
    this.reel = this.director.highlights(6);
    this.phase = "highlights";
    this.panel.badge("Highlights");
    if (!this.reel.length) return this.next();
    this.shot = -1;
    await this.cue(0);
  }

  async cue(k) {
    if (k >= this.reel.length) return this.next();
    const b = this.reel[k], speed = b.camera.startsWith("room") || b.camera === "network" ? 0.8 : 0.12;
    const t0 = Math.max(this.page.P.lo, b.at - speed * 1.5), start = performance.now() / 1000;
    this.shot = k;
    this.override = (now) => ({ t: Math.min(this.page.P.hi, t0 + (now / 1000 - start) * speed), rate: speed * 60 });
    this.until = start + HIGHLIGHT_SECONDS;
    this.director.busy = true;
    try {
      await this.director.apply(b, t0);
    } catch (e) {
      console.warn("a highlight failed:", e);
    }
    this.director.busy = false;
    this.caption({ ...b, kicker: `Highlights · ${b.kicker}` }, b.at + 9);
  }

  highlight(t, now) {
    if (this.shot >= 0 && now >= this.until && !this.cueing) {
      this.cueing = true;
      this.cue(this.shot + 1).finally(() => (this.cueing = false));
    }
  }

  /** The next storm's card over the finished day, until the next day appears. */
  next() {
    this.phase = "next";
    this.caption(null);
    this.override = () => ({ t: this.pacer.F, rate: 0 });
    this.page.setView(this.page.stage ? "harbour" : "network").catch(() => {});
    this.panel.badge("Next storm");
    this.card?.remove();
    this.card = this.root.appendChild(Object.assign(document.createElement("div"), { className: "live-over", innerHTML: nextCard(this.feed.state, this.ep) }));
    this.root.classList.add("over");
    this.nextTimer = setInterval(() => (this.card.innerHTML = nextCard(this.feed.state, this.ep)), 5000);
    this.nextAt = performance.now();
    if (this.waiting) setTimeout(() => this.reload(), NEXT_SECONDS * 1000);
  }

  /** Another day on the server: once this one's card, highlights and next card have shown, the page reloads onto it. */
  nextDay(st) {
    if (!st || finished(st)) return;
    this.waiting = true;
    if (this.phase === "day" && !finished(this.ep.live)) return this.reload();
    if (this.phase === "next" && performance.now() - this.nextAt >= NEXT_SECONDS * 1000) this.reload();
  }

  reload() {
    if (this.reloading) return;
    this.reloading = true;
    clearInterval(this.timer);
    clearInterval(this.nextTimer);
    try {
      sessionStorage.setItem(CARD_KEY, `<div class="live-wait">${nextCard(this.feed.state, this.ep, true)}</div>`);
    } catch {}
    location.reload();
  }

  state() {
    const st = this.feed.state ?? {};
    return { phase: this.phase, t: this.page?.time() ?? this.pacer.t, errors: this.errors ?? 0, frontier: this.pacer.F, speed: this.pacer.v, cap: this.pacer.cap, lag: this.pacer.lag(), watch: this.pacer.watch(),
      holding: this.pacer.holding(), status: st.status, seq: st.seq, instance: this.instance, view: this.page?.view(), main: this.page?.views.main,
      director: this.director?.state(), reel: this.reel?.map((b) => b.id) ?? null, sound: !!window.airops?.sound && !this.mute };
  }
}

/** The live panel: LIVE and the time, the model and the storm, the watch, each desk's latest move, the cost so far. */
class Panel {
  constructor(root, { stream }) {
    this.el = document.createElement("section");
    this.el.className = "card live-panel";
    const left = root.querySelector(".cards.left");
    left.insertBefore(this.el, left.querySelector(".desk"));
    this.stream = stream;
    this.memo = "";
    this.label = "";
  }

  render(st, live) {
    this.st = st ?? {};
    this.live = live;
    this.tick(this.st, live);
  }

  badge(label) {
    this.label = label;
    this.tick(this.st, this.live);
  }

  offline(e) {
    this.down = e.message;
    this.tick(this.st, this.live);
  }

  tick(st, live) {
    if (!live?.page) return;
    if (live.feed.at && performance.now() - live.feed.at < POLL * 3) this.down = null;
    const ep = live.ep, P = live.page.P, task = P.task, now = new Date(), done = finished(st) || live.phase !== "day";
    const model = st.model_label ?? ep.model_label ?? ep.model, mode = ep.occ?.label ?? "";
    const off = this.down || st.status === "stale";
    const head = `<div class="lp-top"><span class="lp-live${off ? " off" : ""}">${off ? "Reconnecting" : "Live"}</span><span class="lp-now">${utc(now)} UTC</span>${this.label ? `<span class="lp-badge">${esc(this.label)}</span>` : ""}</div>`;
    const title = `<h2>${esc(model)}<small>${esc(mode)}</small></h2><p class="lp-storm">${esc(HUB_NAMES[task.hub] ?? task.hub)} · ${esc(longDate(task.date))} · a real storm day, replayed</p>`;
    const holding = !done && live.pacer.holding(), next = st.next_watch_at ? Math.round((new Date(st.next_watch_at) - now) / 1000) : null;
    const watch = st.status === "starting" ? `The desks open at ${hhmm(task.start)}`
      : done ? "The day is done: every watch decided"
        : st.status === "ending" ? "The desks are done; the day plays out"
          : `Deciding ${st.t != null ? hhmm(st.t) : "the next hour"}${st.watches ? ` · watch ${Math.min((st.watch ?? 0) + 1, st.watches)} of ${st.watches}` : ""}`;
    const sub = done ? "" : holding ? "The airport waits for the desks' decisions" : `The airport plays ${hhmm(live.pacer.t)} of what's decided${next != null && next > 0 && st.status === "playing" ? ` · the next watch in about ${next < 90 ? `${next} s` : `${Math.round(next / 60)} min`}` : ""}`;
    const words = (text) => String(text ?? "").replace(/\bf(\d{1,4})\b/g, (m, id) => P.sched.get(Number(id))?.flight ?? m);
    const desks = (ep.occ?.mode === "one" ? ["one"] : ep.occ?.desks?.includes("customer") ? ["ops", "crew", "customer"] : ["ops", "crew"]).map((d) => {
      const x = (st.desks ?? []).find((y) => y.role === d || d === "one"), ev = (st.feed ?? []).findLast((f) => (f.desk === d || d === "one") && ["request", "reply", "decision"].includes(f.kind));
      const what = ev ? words(ev.text).replace(/^(ops|crew|customer|one)\b:?\s*/i, "") : x?.state === "off" || done ? "Stood down" : x?.state === "turn" ? "At work" : "Waiting for its turn";
      return `<li class="${x?.state === "turn" && !done ? "on" : ""}"><b>${DESKS[d]}</b><span>${esc(what)}</span><time>${esc(ev ? ago(ev.at, now) : "")}</time></li>`;
    }).join("");
    const cost = done && ep.cost?.total != null ? ep.cost.total : st.projected?.total ?? st.projected_cost ?? ep.watches?.findLast((w) => w.est_cost != null)?.est_cost ?? null;
    const day = st.result?.cost_usd ?? st.spent_usd ?? null;
    const total = st.total_usd ?? (st.history ?? []).filter((h) => h.run_id !== st.run_id).reduce((a, h) => a + (h.cost_usd ?? 0), day ?? 0);
    const reward = st.result?.reward ?? ep.reward;
    const grade = done && reward != null ? `<div class="lp-grade"><span>Final reward</span><b>${reward.toFixed(3)}</b><em>1 = the best plan found</em></div>` : "";
    const html = `${head}${title}<div class="lp-watch"><b>${esc(watch)}</b><span>${esc(sub)}</span></div><ol class="lp-desks">${desks}</ol>
      <div class="lp-kpis"><div><b>${cost != null ? short(cost) : "–"}</b><span>${done ? "the day's cost" : "projected cost of the day"}, passenger-minutes</span></div>
      <div><b>${day != null ? `$${day.toFixed(2)}` : "–"}</b><span>model spend this day${total > (day ?? 0) + 0.005 ? ` · $${total.toFixed(2)} on the stream` : ""}</span></div></div>${grade}`;
    if (html !== this.memo) this.el.innerHTML = this.memo = html;
  }
}

function ago(at, now) {
  if (at == null) return "";
  if (typeof at === "number") return hhmm(at);
  const s = Math.round((now - new Date(at)) / 1000);
  return !Number.isFinite(s) ? "" : s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : `${Math.floor(s / 3600)} h ago`;
}

function scoreCard(ep, st) {
  const P = ep, ref = P.reference ?? {}, std = (P.occ?.refs ?? []).find((r) => /standard/i.test(r.label));
  const h = (st?.history ?? []).find((x) => x.run_id === (st?.run_id ?? ep.live?.run_id));
  const model = ep.model_label ?? ep.model, task = ep.task, reward = st?.result?.reward ?? ep.reward ?? h?.reward;
  const rows = [[model, reward, true], ["Best plan found", 1, false], ...(std ? [["Standard crew desk (B0)", std.reward, false]] : [])].filter((r) => r[1] != null);
  const bar = ([label, r, me]) => `<div class="ref${me ? " me" : ""}"><span>${esc(label)}</span><i><b style="width:${(100 * (r - 0.2)) / 0.8}%"></b></i><em>${r.toFixed(3)}</em></div>`;
  const over = ref.anchor && ep.cost?.total != null ? (100 * (ep.cost.total - ref.anchor)) / ref.anchor : null;
  const wall = st?.result?.wall_seconds ?? ep.record?.wall_seconds ?? h?.wall_seconds ?? st?.wall_seconds;
  const spend = st?.result?.cost_usd ?? ep.record?.cost_usd ?? h?.cost_usd ?? st?.spent_usd;
  const dl = [["The day's cost", ep.cost?.total != null ? `${short(ep.cost.total)} passenger-minutes${over != null ? `, ${over >= 0 ? "+" : ""}${over.toFixed(1)}% vs the best plan` : ""}` : "–"],
    ["Doing what the standard desks do (B0)", ref.B0 != null ? `${short(ref.B0)} passenger-minutes` : "–"],
    ["Wall time", wall != null ? `${Math.floor(wall / 3600) ? `${Math.floor(wall / 3600)} h ` : ""}${Math.round((wall % 3600) / 60)} min` : "–"],
    ["Model spend", spend != null ? `$${spend.toFixed(2)}` : "–"]];
  return `<div class="live-card score"><small>${BRAND} · storm day done</small><h1>${esc(HUB_NAMES[task.hub] ?? task.hub)} · ${esc(longDate(task.date))}</h1>
    <p>${esc(model)} ran JetBlue's control center${esc(ep.occ?.label ? ` at ${ep.occ.label}` : "")} through the storm</p>
    <div class="refs"><div class="refs-h">Reward (1 = the best plan found in hindsight)</div>${rows.map(bar).join("")}</div>
    <dl>${dl.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl></div>`;
}

function nextCard(st, ep, loading = false) {
  const n = st?.next ?? null, hist = (st?.history ?? []).filter((h) => h.outcome !== "skipped");
  const when = n?.starts_utc ? new Date(n.starts_utc) : null, mins = when ? Math.round((when - Date.now()) / 60000) : null;
  const what = n ? `${n.hub ? `${HUB_NAMES[n.hub] ?? n.hub} · ` : ""}${longDate(n.date)}` : loading ? "The next storm day" : "Between storm days";
  const rows = hist.slice(-6).map((h) => `<tr><td>${esc(longDate(h.date))}</td><td>${h.reward != null ? h.reward.toFixed(3) : esc(h.outcome ?? "–")}</td><td>${h.cost_usd != null ? `$${h.cost_usd.toFixed(2)}` : "–"}</td><td>${h.wall_seconds != null ? `${Math.round(h.wall_seconds / 60)} min` : "–"}</td></tr>`).join("");
  const scored = hist.filter((h) => h.reward != null), spend = hist.reduce((a, h) => a + (h.cost_usd ?? 0), 0);
  const total = scored.length ? `${scored.length} storm day${scored.length > 1 ? "s" : ""} so far · mean reward ${(scored.reduce((a, h) => a + h.reward, 0) / scored.length).toFixed(3)} · $${spend.toFixed(2)} of model spend` : "";
  const model = st?.model_label ?? ep?.model_label ?? "The model";
  return `<div class="live-card next"><small>${BRAND} · live · next storm</small><h1>${esc(what)}</h1>
    <p>${esc(model)} takes JetBlue's control center ${hist.length ? "again " : ""}from 04:00 local${when ? `, ${mins > 1 ? `in about ${mins} min` : "any moment now"} (${utc(when).slice(0, 5)} UTC)` : ""}.</p>
    ${rows ? `<table><thead><tr><th>Storm day</th><th>Reward</th><th>Spend</th><th>Wall</th></tr></thead><tbody>${rows}</tbody></table>` : ""}${total ? `<p class="total">${esc(total)}</p>` : ""}</div>`;
}

function credit() {
  return `<b>AirOpsEnv</b> · built on <b>AgentEnv</b> · data and viewer on <b>Hugging Face</b> · flights BTS, advisories FAA ATCSCC, weather NWS and IEM, airport © OpenStreetMap contributors · the room, gates and passengers are drawn for display`;
}
