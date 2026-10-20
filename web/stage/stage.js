import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { aircraftLook } from "./aircraft.js";
import { buildBridges } from "./bridges.js";
import { buildBuildings } from "./buildings.js";
import { cameraPresets, homeOf } from "./cameras.js";
import { buildCrews } from "./crews.js";
import { toScene } from "./data.js";
import { buildEnvironment } from "./env.js";
import { buildFleet } from "./fleet.js";
import { buildGround } from "./ground.js";
import { buildLabels } from "./labels.js";
import { buildTracks, poseAt } from "./motion.js";
import { buildSpray } from "./spray.js";
import { buildWeather } from "./weather.js";

const DEG = Math.PI / 180;
const VIEWS = ["tower", "gate", "threshold", "harbour", "follow"];
const ease = (u) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));
const angDiff = (a, b) => ((((b - a) % 360) + 540) % 360) - 180;
const forward = (h, out = new THREE.Vector3()) => out.set(Math.sin(h * DEG), 0, -Math.cos(h * DEG));
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));

/** The hub in 3D over a whole episode: new AirportStage(container, {stage, traffic, base}); await stage.ready; then time(t), view(name), shot(spec). */
export class AirportStage {
  constructor(container, { stage, traffic, base = location.href, manual = false, controls = true, avoid = null, pixelRatio = Math.min(globalThis.devicePixelRatio || 1, 2) } = {}) {
    this.container = container;
    this.avoid = avoid;
    this.data = stage;
    this.traffic = traffic;
    this.manual = manual;
    this.T = traffic.span[0];
    this.rate = null;
    this.wall = 0;
    this.picks = [];
    this.rig = { kind: "preset", name: "tower" };
    this.featured = null;
    this.frameMs = [];
    this.renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true, preserveDrawingBuffer: manual, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(pixelRatio);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.domElement.style.display = "block";
    if (getComputedStyle(container).position === "static") container.style.position = "relative";
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.5, 90000);
    this.camera.layers.enable(1);
    this.resize();
    this.ready = this.#build(base, controls);
  }

  async #build(base, controls) {
    const { data: stage, traffic, scene, renderer } = this;
    this.env = buildEnvironment(renderer, scene, stage, traffic);
    this.plan = buildTracks(stage, traffic);
    const approach = [...new Set((traffic.runways || []).map((r) => r.arrivals))];
    const month = +traffic.date.slice(5, 7);
    this.ground = await buildGround(stage, new URL(base, location.href).href, { approach, winter: month <= 3 || month >= 11 });
    scene.add(this.ground.group);
    aircraftLook.lightMap.value = this.ground.U.uLightMap.value;
    aircraftLook.lightM.value.copy(this.ground.U.uLightM.value);
    this.buildings = buildBuildings(stage);
    scene.add(this.buildings.group);
    this.bridges = buildBridges(stage, this.plan);
    scene.add(this.bridges.group);
    this.weather = buildWeather(scene, traffic);
    this.fleet = buildFleet(this.plan);
    scene.add(this.fleet.group);
    this.crews = buildCrews(traffic.crews || []);
    scene.add(this.crews.group);
    this.spray = buildSpray(this.plan);
    scene.add(this.spray.mesh);
    this.labels = buildLabels(this.container, { occluders: occluders(stage), avoid: this.avoid });
    if (controls && !this.manual) {
      this.controls = new OrbitControls(this.camera, renderer.domElement);
      this.controls.enableDamping = true;
      this.controls.maxPolarAngle = 1.53;
      this.controls.screenSpacePanning = false;
    }
    this.#pointer();
    this.#view("tower");
    this.#frame(1 / 30);
    const far = await this.ground.ready;
    this.#frame(1 / 30);
    if (!this.manual) {
      const clock = new THREE.Clock();
      renderer.setAnimationLoop(() => {
        const dt = Math.min(clock.getDelta(), 0.1);
        this.wall += dt;
        this.#frame(dt);
      });
    }
    return { hub: stage.hub, start: traffic.span[0], end: traffic.span[1], views: VIEWS, far, aircraft: this.plan.tracks.size, movements: this.plan.report.movements };
  }

  /** Render the stage at minute t of the episode; dt is the wall seconds since the last frame (recording), speed the clock rate shown. */
  async time(t, { dt = null, speed = null } = {}) {
    await this.ready;
    this.T = +t;
    this.rate = speed;
    if (this.manual) {
      const step = dt ?? 1 / 30;
      this.wall += step;
      await nextFrame();
      this.#frame(step);
      await nextFrame();
    } else await nextFrame();
    return this.T;
  }

  /** tower | gate | threshold | harbour | follow; opts.key picks the aircraft (follow, gate), opts.stand the gate. */
  async view(name, opts = {}) {
    await this.ready;
    return this.#view(name, opts);
  }

  #view(name, opts = {}) {
    const n = VIEWS.includes(name) ? name : "tower";
    if (n === "gate" && !opts.key && !opts.stand) opts = { ...opts, key: this.#parkedAtHome(this.T) ?? undefined };
    this.featured = opts.key ?? (n === "follow" ? this.traffic.featured?.[0]?.key : this.featured);
    if (n === "follow") {
      this.rig = { kind: "follow", name: n, ...opts, key: this.featured, h: null };
    } else {
      const stand = opts.stand ?? (opts.key ? this.#standOf(opts.key, this.T) : null);
      const length = this.plan.tracks.get(opts.key)?.spec.length || 40;
      const departures = this.plan.config(this.T).departures;
      const cams = cameraPresets(this.data, { stand, departures, length, roll: this.plan.rolls.get(departures) });
      this.rig = { kind: "preset", name: n, cam: cams[n] || cams.tower, set: false };
    }
    if (this.controls) this.controls.enabled = this.rig.kind === "preset";
    if (this.manual && this.fleet) this.#frame(0);
    return this.rig.name;
  }

  /** A scripted camera: {from, to?, at: [x, y, up] | key, offset?, lead?, fov, fov1?, t0?, t1?, frame?} in ENU metres. */
  async shot(spec) {
    this.rig = { kind: "shot", name: spec.name || "shot", ...spec };
    if (typeof spec.at === "string") this.featured = spec.at;
    if (this.controls) this.controls.enabled = false;
    return this.rig.name;
  }

  state() {
    const counts = {};
    for (const f of this.fleet?.live || []) counts[f.pose.phase] = (counts[f.pose.phase] || 0) + 1;
    const f = this.fleet?.live.find((x) => x.key === this.featured);
    const ms = this.frameMs.slice().sort((a, b) => a - b);
    return {
      t: this.T,
      view: this.rig.name,
      counts,
      aircraft: this.fleet?.live.length || 0,
      featured: f ? { key: f.key, phase: f.pose.phase, x: f.pose.x, y: f.pose.y } : null,
      runways: this.plan?.config(this.T),
      weather: this.env?.weatherAt(this.T)?.plain || "",
      sun: this.env?.sunAt(this.T).el,
      lightning: this.lightning ? Math.max(this.lightning.local, this.lightning.far) : 0,
      snow: this.env?.snowAt(this.T),
      frame: ms.length ? { median: ms[ms.length >> 1], p95: ms[Math.floor(ms.length * 0.95)] } : null,
      crew: this.crews?.count || 0,
    };
  }

  pose(t = this.T, key = this.featured) {
    const tr = this.plan.tracks.get(key);
    return tr ? poseAt(tr, t) : null;
  }

  events() {
    return this.plan.events.map((e) => ({ ...e }));
  }

  onPick(cb) {
    this.picks.push(cb);
    return () => (this.picks = this.picks.filter((x) => x !== cb));
  }

  resize() {
    const w = this.container.clientWidth || innerWidth;
    const h = this.container.clientHeight || innerHeight;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    this.controls?.dispose();
    this.labels?.dispose();
    this.scene.traverse((o) => {
      o.geometry?.dispose?.();
      for (const m of [].concat(o.material || [])) m.dispose?.();
    });
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  #parkedAtHome(t) {
    const home = homeOf(this.data);
    let best = null;
    for (const s of home.stands) {
      const stay = (this.plan.stays.get(s.ref) || []).find((x) => x.a <= t * 60 && t * 60 < x.b);
      const score = stay && Math.hypot(s.xy[0] - home.centre[0], s.xy[1] - home.centre[1]) - (this.plan.tracks.get(stay.key)?.task ? 300 : 0);
      if (stay && (!best || score < best.score)) best = { key: stay.key, score };
    }
    return best?.key;
  }

  #standOf(key, t) {
    const stays = this.plan.tracks.get(key)?.stays || [];
    return (stays.find((x) => x.a <= t * 60 && t * 60 < x.b) || stays[0])?.stand ?? null;
  }

  #pointer() {
    const el = this.renderer.domElement;
    let down = null;
    el.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
    el.addEventListener("pointerup", (e) => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5 || !this.picks.length) return;
      const r = el.getBoundingClientRect();
      const ray = new THREE.Raycaster();
      ray.layers.enable(1);
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camera);
      const key = this.fleet.pick(ray);
      if (key) for (const cb of this.picks) cb(key);
    });
  }

  #frame(dt) {
    const t0 = performance.now();
    const T = this.T;
    const s = this.env.update(T, this.wall);
    this.ground.update(T, s, this.wall);
    this.buildings.update(T, s, this.wall);
    aircraftLook.cabin.value = 0.15 + 0.85 * (1 - s.atm.day);
    aircraftLook.wet.value = 0.7 * s.wet;
    aircraftLook.beam.value = 0.55 + 0.6 * s.mist;
    aircraftLook.pool.value = this.ground.U.uPool.value;
    const live = this.fleet.update(T, this.wall, this.camera, {
      featured: this.featured,
      wet: s.wet,
      night: 1 - s.atm.day,
      snow: (c, p, t) => s.snow * Math.min(1, Math.max(0.15, (t * 60 - p.since) / 2400)),
    });
    this.bridges.update(T, live, s);
    this.crews.update(T, this.wall, this.rate || 0);
    this.spray.update(T, live, s, this.camera);
    this.#aim(dt, live);
    this.env.focus(this.camera, this.focusAt);
    this.lightning = this.weather.update(dt, this.wall, T, s, this.camera);
    this.env.flash(this.lightning);
    this.renderer.render(this.scene, this.camera);
    this.labels.update(this.camera, this.renderer, this.#tags(live));
    this.frameMs.push(performance.now() - t0);
    if (this.frameMs.length > 120) this.frameMs.shift();
  }

  #aim(dt, live) {
    const cam = this.camera;
    const rig = this.rig;
    const target = new THREE.Vector3();
    if (rig.kind === "preset") {
      if (!rig.set) {
        const c = rig.cam;
        cam.position.copy(toScene(c.position, c.position[2]));
        target.copy(toScene(c.target, c.target[2]));
        cam.fov = c.fov;
        cam.updateProjectionMatrix();
        cam.lookAt(target);
        if (this.controls) this.controls.target.copy(target);
        rig.set = true;
        rig.target = target.clone();
      }
      if (this.controls) this.controls.update();
      this.focusAt = this.controls ? this.controls.target : rig.target;
      return;
    }
    const f = live.find((x) => x.key === (typeof rig.at === "string" ? rig.at : rig.key));
    const centre = new THREE.Vector3();
    if (f) centre.set(0, f.dims.height * 0.3, f.dims.length / 2 - f.dims.nose).applyMatrix4(f.matrix);
    if (rig.kind === "follow") {
      if (!f) return;
      const k = 1 - Math.exp(-(dt || 0) / (rig.tau ?? 0.9));
      rig.h = rig.h == null || !dt ? f.pose.heading : (rig.h + angDiff(rig.h, f.pose.heading) * k + 360) % 360;
      const fw = forward(rig.h);
      const rt = new THREE.Vector3(-fw.z, 0, fw.x);
      const back = rig.dist ?? Math.max(70, f.dims.length * 2.3);
      cam.position.copy(centre).addScaledVector(fw, -back).addScaledVector(rt, rig.side ?? back * 0.36);
      cam.position.y = Math.max(centre.y + (rig.up ?? back * 0.3), 3);
      target.copy(centre).addScaledVector(fw, rig.lead ?? 20);
      cam.fov = rig.fov ?? 42;
    } else {
      const u = rig.t1 != null ? ease((this.T - rig.t0) / (rig.t1 - rig.t0)) : 0;
      const a = toScene(rig.from, rig.from[2]);
      cam.position.lerpVectors(a, rig.to ? toScene(rig.to, rig.to[2]) : a, u);
      if (typeof rig.at === "string") {
        if (!f) return;
        target.copy(centre).addScaledVector(forward(f.pose.heading), rig.lead || 0);
        if (rig.offset) target.add(toScene(rig.offset, rig.offset[2]));
      } else target.copy(toScene(rig.at, rig.at[2]));
      cam.fov = rig.fov + ((rig.fov1 ?? rig.fov) - rig.fov) * u;
      if (rig.frame) cam.fov = Math.min(cam.fov, Math.max(3, (2 * Math.atan(rig.frame / 2 / cam.position.distanceTo(target))) / DEG));
    }
    cam.updateProjectionMatrix();
    cam.lookAt(target);
    this.focusAt = target;
    if (this.controls) this.controls.target.copy(target);
  }

  #tags(live) {
    const tr = this.plan.tracks.get(this.featured);
    const flights = new Set((tr?.visits || []).flatMap((v) => [v.arr?.flight, v.dep?.flight]).filter(Boolean));
    const out = this.crews.tags(this.camera, flights);
    for (const f of live) {
      const tr = f.c.track;
      if (!tr.task) continue;
      const tag = tagAt(tr, this.T);
      if (!tag) continue;
      out.push({ key: f.key, matrix: f.matrix, dims: f.dims, text: tag.text, tone: tag.tone, featured: f.key === this.featured,
        moving: (f.pose.speed || 0) > 0.3, priority: tag.crew && f.key !== this.featured ? 1 : undefined });
    }
    return out;
  }
}

const pad2 = (n) => String(n).padStart(2, "0");
const hhmm = (m) => `${pad2(Math.floor((((m % 1440) + 1440) % 1440) / 60))}:${pad2(((m % 60) + 60) % 60)}`;

/** A departure's crew status at the gate at t, from its crew log (control-center days): "waiting for crew" once
 * boarding would start with no legal crew ("no crew yet" before), "crew times out 22:40" when the crew's clock is
 * close to the flight's departure; null once it pushes back or when its crew is fine. */
export function crewAt(leg, t) {
  if (!leg.crew || leg.kind !== "dep" || leg.status !== "flown" || t >= leg.out) return null;
  const row = leg.crew.findLast((r) => r[0] <= t);
  if (!row || !row[1]) return null;
  if (row[1] === "no crew") return t >= (leg.sched ?? leg.out) - 40 ? "waiting for crew" : "no crew yet";
  return t < row[2] ? `crew times out ${hhmm(row[2])}` : null;
}

/** A task aircraft's tag at t: its leg's (legAt), with the crew status at the gate in place of the leg's own when it
 * has one (crewAt). */
export function tagAt(track, t) {
  const leg = legAt(track, t);
  if (!leg?.tag) return null;
  const crew = crewAt(leg, t);
  return { text: crew ? `${leg.tag.split(" · ")[0]} · ${crew}` : leg.tag, tone: crew ? "crew" : leg.tone || "ok", crew: !!crew };
}

/** The leg a task aircraft's tag shows at t: the one it is flying, a cancellation within 45 minutes of when it is cancelled and not before it was decided, else its next. */
export function legAt(track, t) {
  const legs = track.visits.flatMap((v) => [v.arr, v.dep]).filter((l) => l?.tag);
  const at = (l) => Math.max(l.sched, l.decided ?? l.sched);
  const key = (l) => (l.status === "cancelled" ? at(l) : l.kind === "dep" ? l.out : (l.in ?? l.on));
  const live = legs.find((l) => l.status === "flown" && (l.kind === "dep" ? t >= l.out - 20 && t <= (l.off ?? l.out) + 3 : t >= (l.on ?? l.in) - 6 && t <= (l.in ?? l.on) + 20));
  if (live) return live;
  const cancelled = legs.find((l) => l.status === "cancelled" && t >= (l.decided ?? -Infinity) && Math.abs(t - at(l)) <= 45);
  if (cancelled) return cancelled;
  return legs.filter((l) => l.status === "flown" && key(l) >= t).sort((a, b) => key(a) - key(b))[0] || null;
}

function occluders(stage) {
  const mat = new THREE.MeshBasicMaterial();
  return [...stage.terminals, ...(stage.hangars || [])].map((b) => {
    const shape = new THREE.Shape(b.outer.map(([x, y]) => new THREE.Vector2(x, y)));
    for (const h of b.holes || []) shape.holes.push(new THREE.Path(h.map(([x, y]) => new THREE.Vector2(x, y))));
    const m = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: b.height || 16, bevelEnabled: false }).rotateX(-Math.PI / 2), mat);
    m.updateMatrixWorld();
    return m;
  });
}
