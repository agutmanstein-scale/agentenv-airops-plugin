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
const VIEWS = ["tower", "gate", "threshold", "harbour", "follow", "crew"];
const WALK_TURNS = [18, -18, 35, -35, 160, -160, 60, -60, 90, -90];
const WAIT_TURNS = [125, -125, 100, -100, 150, -150, 75, -75];
const VAN_TURNS = [145, -145, 100, -100, 55, -55, 180, 0];
const CURB_TURNS = [90, 65, 115, 45, 135, 25, 155];
/** The crew camera's bearings off a group's heading, best first: ahead of a walking group along its walkway, along
 * the building's face from a waiting one (which faces its door), behind the apron bus, square on to a van at the curb
 * from its open side (the terminal behind it); the first clear of the buildings and the aircraft about, with the group
 * in sight, is taken. */
const CREW_HOLD = 3;
const CREW_NEAREST = 0.8;
const CELL = 50;
/** The crew camera prefers a group in sight this many wall seconds at the clock's rate, and comes no nearer it than
 * this share of its place's distance as it eases between places. */
const ease = (u) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));
const angDiff = (a, b) => ((((b - a) % 360) + 540) % 360) - 180;
const forward = (h, out = new THREE.Vector3()) => out.set(Math.sin(h * DEG), 0, -Math.cos(h * DEG));
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));
const solid = (o) => {
  if (o.material?.transparent) return false;
  for (; o; o = o.parent) if (!o.visible) return false;
  return true;
};

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

  /** tower | gate | threshold | harbour | follow | crew; opts.key picks the aircraft (follow, gate), opts.stand the
   * gate; for crew, opts.key is a crew group's key (crewGroups()) or an aircraft whose crew it takes, else the
   * featured aircraft's crew or the next crew about, and opts.wide shows a van's whole road from high up. */
  async view(name, opts = {}) {
    await this.ready;
    return this.#view(name, opts);
  }

  #view(name, opts = {}) {
    const n = VIEWS.includes(name) ? name : "tower";
    if (n === "crew") {
      const key = this.crews.focus(opts.key, this.T) ? opts.key : this.crews.pick(this.T, this.#flightsOf(opts.key ?? this.featured), { ok: (k) => this.#crewClear(k), hold: (CREW_HOLD * (this.rate || 0)) / 60 });
      if (!key) return this.#view("gate", {});
      this.featured = this.#aircraftOf(key) ?? this.featured;
      this.rig = { kind: "crew", name: n, key, ...opts, at: `crew:${key}` };
      if (this.controls) this.controls.enabled = false;
      if (this.manual && this.fleet) this.#frame(0);
      return n;
    }
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

  /** A scripted camera: {from, to?, at: [x, y, up] | key | "crew:<key>", offset?, lead?, fov, fov1?, t0?, t1?, frame?}
   * in ENU metres; at a crew group with no `from`, the crew camera's own place, framing the group in the clear part of
   * the page. */
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
      crewFocus: this.rig.at?.startsWith?.("crew:") ? { key: this.rig.at.slice(5), ...this.crews.focus(this.rig.at.slice(5), this.T), people: this.crews.shown(this.rig.at.slice(5))?.n ?? 0 } : null,
    };
  }

  pose(t = this.T, key = this.featured) {
    const tr = this.plan.tracks.get(key);
    return tr ? poseAt(tr, t) : null;
  }

  /** The crew groups at the hub over the day ({key, kind, crew, flights, stand, from, to, says}), for a director. */
  crewGroups() {
    return this.crews?.list() ?? [];
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
    this.crews.update(T, this.wall, this.rate || 0, (this.inside ??= (x, y) => this.#indoors(x, y)));
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
    const crewKey = typeof rig.at === "string" && rig.at.startsWith("crew:") ? rig.at.slice(5) : null;
    if (crewKey) return this.#aimCrew(dt, crewKey);
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

  /** The crew camera, or a shot at a crew group: low and close behind the group's shoulder from the apron side,
   * following it as it walks (a van from further back and higher), the group in the left-centre of the page's clear
   * part; a shot with `from` keeps its own place. The camera keeps its distance as the group moves at any clock rate
   * and eases only between the places it looks from. */
  #aimCrew(dt, key) {
    const cam = this.camera;
    const rig = this.rig;
    if (rig.kind === "crew" && !rig.from) key = this.#crewInSight(key);
    const f = this.#crewAt(key);
    if (!f) return;
    const van = f.kind === "van";
    if (van && f.road && rig.wide) return this.#aimCurb(dt, f);
    const spots = this.#crewSpots(f);
    const P = spots.P;
    if (rig.turnKind !== f.kind) [rig.choice, rig.last, rig.turnKind] = [null, null, f.kind];
    if (!rig.choice || !dt || this.wall - (rig.chose ?? -1) > 0.1) {
      rig.chose = this.wall;
      const clear = spots.clear(rig.last);
      rig.blocked = !clear;
      rig.choice = clear ?? rig.last ?? [spots.tiers.length - 1, spots.turns[0]];
      rig.last = rig.choice;
    }
    const want = spots.at(...rig.choice).sub(P);
    const k = !dt || !rig.off ? 1 : 1 - Math.exp(-dt / (rig.tau ?? 0.6));
    rig.off = rig.off ? rig.off.lerp(want, k) : want;
    const reach = Math.hypot(rig.off.x, rig.off.z);
    const least = CREW_NEAREST * spots.tiers[rig.choice[0]][0];
    if (reach < least) rig.off.multiply(new THREE.Vector3(least / reach, 1, least / reach));
    rig.pos = P.clone().add(rig.off);
    const jump = !dt || !rig.tgt || rig.tgt.distanceTo(P) > 80;
    rig.tgt = rig.from && !jump ? rig.tgt.lerp(P, 1 - Math.exp(-dt / (rig.tau ?? 0.6))) : P.clone();
    const target = rig.tgt.clone();
    if (rig.offset) target.add(toScene(rig.offset, rig.offset[2]));
    if (rig.from) {
      const u = rig.t1 != null ? ease((this.T - rig.t0) / (rig.t1 - rig.t0)) : 0;
      const a = toScene(rig.from, rig.from[2]);
      cam.position.lerpVectors(a, rig.to ? toScene(rig.to, rig.to[2]) : a, u);
      cam.fov = (rig.fov ?? 36) + ((rig.fov1 ?? rig.fov ?? 36) - (rig.fov ?? 36)) * u;
      cam.updateProjectionMatrix();
      cam.lookAt(target);
    } else {
      cam.position.copy(rig.pos);
      cam.fov = rig.fov ?? (van ? 40 : [36, 32, 26][rig.choice[0]]);
      cam.updateProjectionMatrix();
      const [nx, ny] = this.#clearSpot();
      const dir = target.clone().sub(cam.position);
      const D = dir.length();
      dir.normalize();
      const right = new THREE.Vector3().crossVectors(dir, new THREE.Vector3(0, 1, 0)).normalize();
      const upv = new THREE.Vector3().crossVectors(right, dir);
      const tanV = Math.tan((cam.fov * DEG) / 2);
      cam.lookAt(target.clone().addScaledVector(right, -nx * D * tanV * cam.aspect).addScaledVector(upv, -ny * D * tanV));
    }
    this.focusAt = target;
  }

  /** The group the crew camera stays on: `key` while its people are drawn outdoors with a clear place to see them
   * from, else the group in sight longest (CREW_HOLD wall seconds or more at the clock's rate, if one is), else the
   * next to come out: at once when the crew followed boards or goes in, then at most every 0.1 s. */
  #crewInSight(key) {
    const rig = this.rig;
    const people = this.crews.shown(key);
    const was = rig.inSight;
    rig.inSight = !!people && !rig.blocked && !this.#indoors(people.x, people.y);
    if (rig.inSight) return key;
    if (!was && this.wall - (rig.checked ?? -1) < 0.1) return key;
    rig.checked = this.wall;
    const next = this.crews.pick(this.T, this.#flightsOf(this.featured), { ok: (k) => this.#crewClear(k), hold: (CREW_HOLD * (this.rate || 0)) / 60 });
    if (!next || next === key) return key;
    Object.assign(rig, { key: next, at: `crew:${next}`, choice: null, last: null, off: null, tgt: null, blocked: false, inSight: false });
    this.featured = this.#aircraftOf(next) ?? this.featured;
    return next;
  }

  /** Whether group `key`'s people are drawn outdoors with a place clear to see them from. */
  #crewClear(key) {
    const f = this.#crewAt(key);
    return !!f && !!this.crews.shown(key) && !this.#indoors(f.x, f.y) && !!this.#crewSpots(f).clear();
  }

  /** Where group `key` is for a camera (focus()): at the middle of its people drawn when they are, else, for the
   * crew camera waiting on a group yet to come out on foot, where its walk first leaves the buildings. */
  #crewAt(key) {
    const f = this.crews.focus(key, this.T);
    const people = f && this.crews.shown(key);
    if (people) return { ...f, x: people.x, y: people.y };
    const out = this.rig.kind === "crew" ? this.crews.comesOut(key, this.T) : null;
    if (!out) return f;
    for (let i = 1; i < out.path.length; i++) {
      const [a, b] = [out.path[i - 1], out.path[i]];
      const n = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 3));
      for (let k = 0; k <= n; k++) {
        const [x, y] = [a[0] + ((b[0] - a[0]) * k) / n, a[1] + ((b[1] - a[1]) * k) / n];
        if (!this.#indoors(x, y)) return { ...out, x, y, heading: (Math.atan2(b[0] - a[0], b[1] - a[1]) / DEG + 360) % 360 };
      }
    }
    return out;
  }

  /** The places a camera may look at group f from: {P, tiers, turns, at(tier, turn), clear(keep)}, clear giving the
   * first [tier, turn] with the group in sight and clear of the buildings, aircraft and bridges about (`keep` tried
   * first in its tier), or null. */
  #crewSpots(f) {
    const van = f.kind === "van";
    const P = new THREE.Vector3(f.x, van ? 1.7 : 1.25, -f.y);
    const open = van && !f.bus ? this.#curbSide(f) : 0;
    const curb = open ? CURB_TURNS.map((a) => a * open) : VAN_TURNS;
    const near = f.bus ? [26, 7, VAN_TURNS] : van ? [24, 5, curb] : f.kind === "wait" ? [15, 2.8, WAIT_TURNS] : [20, 3.4, WALK_TURNS];
    const [tiers, turns] = [[[near[0], near[1]], [near[0] * 1.4, 13], [40, 28]], near[2]];
    const hulls = this.#hulls(f.x, f.y, 100).concat(van ? [] : this.crews.vehicles());
    const posts = this.bridges.obstacles().filter(([x, y]) => Math.hypot(x - f.x, y - f.y) < 60);
    const tubes = this.bridges.tubes().filter((t) => segDist(f.x, f.y, t) < 80);
    const at = (tier, a) => {
      const h = (f.heading + a) * DEG;
      const [dist, up] = tiers[tier];
      return new THREE.Vector3(f.x + Math.sin(h) * dist, up, -(f.y + Math.cos(h) * dist));
    };
    const seen = (c) => {
      if (this.#indoors(c.x, -c.z) || hulls.some((h) => segDist(c.x, -c.z, h) < h[4] + 2)) return false;
      if (c.y < 9 && tubes.some((t) => segDist(c.x, -c.z, t) < t[4] + 6)) return false;
      for (let i = 1; i < 24; i++) {
        const u = i / 24;
        const [x, y] = [c.x + (P.x - c.x) * u, -(c.z + (P.z - c.z) * u)];
        if (u > 0.9 && !van) break;
        const z = c.y + (P.y - c.y) * u;
        if (this.#indoors(x, y) && z < 25) return false;
        if (z < 6 && hulls.some((h) => segDist(x, y, h) < h[4])) return false;
        if (z < 4.4 && posts.some(([px, py, r]) => Math.hypot(px - x, py - y) < r)) return false;
        if (z > 2.3 && z < 9 && tubes.some((t) => segDist(x, y, t) < t[4] + 0.5)) return false;
      }
      return van || this.#clearTo(c, P);
    };
    const clear = (keep = null) => {
      for (let tier = 0; tier < tiers.length; tier++) {
        const a = [...(keep && keep[0] === tier ? [keep[1]] : []), ...turns].find((a) => seen(at(tier, a)));
        if (a != null) return [tier, a];
      }
      return null;
    };
    return { P, tiers, turns, at, clear };
  }

  /** A van's road from high behind its curb end on the open side, looking down it: the curb, the road and the van on
   * it, clear of the buildings either side. */
  #aimCurb(dt, f) {
    const cam = this.camera;
    const rig = this.rig;
    const [a, b] = f.road;
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const d = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
    const side = this.#curbSide({ ...f, heading: (Math.atan2(d[0], d[1]) / DEG + 360) % 360, x: mid[0], y: mid[1] }) || 1;
    const out = [d[1] * side, -d[0] * side];
    const want = new THREE.Vector3(a[0] + out[0] * 30 - d[0] * 55, 75, -(a[1] + out[1] * 30 - d[1] * 55));
    const P = new THREE.Vector3(mid[0], 2, -mid[1]);
    const k = !dt || !rig.pos || rig.pos.distanceTo(want) > 80 ? 1 : 1 - Math.exp(-dt / (rig.tau ?? 0.6));
    rig.pos = rig.pos ? rig.pos.lerp(want, k) : want.clone();
    rig.tgt = rig.tgt ? rig.tgt.lerp(P, k) : P.clone();
    cam.position.copy(rig.pos);
    cam.fov = rig.fov ?? 42;
    cam.updateProjectionMatrix();
    cam.lookAt(rig.tgt);
    this.focusAt = rig.tgt.clone();
  }

  /** Which side of a van at the curb is open (1 right of its heading, -1 left), the other being the terminal; 0 when
   * neither or both are. */
  #curbSide(f) {
    const probe = (a) => {
      const h = (f.heading + a) * DEG;
      return this.#indoors(f.x + Math.sin(h) * 22, f.y + Math.cos(h) * 22);
    };
    const [r, l] = [probe(90), probe(-90)];
    return r === l ? 0 : r ? -1 : 1;
  }

  /** Where in normalised device coordinates the crew camera puts its group: 42 % across the part of the page between
   * the cards that cover the stage's sides, a little below the middle. */
  #clearSpot() {
    if (this.clear && this.wall - this.clear.at < 0.5) return this.clear.spot;
    const w = this.container.clientWidth || innerWidth;
    const h = this.container.clientHeight || innerHeight;
    let [L, R] = [0, w];
    for (const [x0, y0, x1, y1] of this.avoid ? this.avoid() : []) {
      if (y1 < h * 0.3 || y0 > h * 0.8) continue;
      if (x1 <= w * 0.5) L = Math.max(L, x1);
      else if (x0 >= w * 0.5) R = Math.min(R, x0);
    }
    this.clear = { at: this.wall, spot: [(2 * (L + 0.42 * (R - L))) / w - 1, -0.1] };
    return this.clear.spot;
  }

  /** Whether (x, y) is inside a building near the hub, through a grid of the buildings' boxes, since the crews ask
   * it for every person they draw. */
  #indoors(x, y) {
    if (!this.blocks) {
      const home = homeOf(this.data).centre;
      const near = (b) => b.outer.some(([px, py]) => Math.hypot(px - home[0], py - home[1]) < 2500);
      this.blocks = new Map();
      for (const b of [...this.data.terminals, ...(this.data.hangars || []), ...(this.data.buildings || [])].filter(near)) {
        const xs = b.outer.map((p) => p[0]);
        const ys = b.outer.map((p) => p[1]);
        const block = { ring: b.outer, box: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] };
        for (let i = Math.floor(block.box[0] / CELL); i <= Math.floor(block.box[2] / CELL); i++) {
          for (let j = Math.floor(block.box[1] / CELL); j <= Math.floor(block.box[3] / CELL); j++) {
            const k = `${i},${j}`;
            if (!this.blocks.has(k)) this.blocks.set(k, []);
            this.blocks.get(k).push(block);
          }
        }
      }
    }
    const here = this.blocks.get(`${Math.floor(x / CELL)},${Math.floor(y / CELL)}`);
    return !!here && here.some((b) => x >= b.box[0] && x <= b.box[2] && y >= b.box[1] && y <= b.box[3] && inRing(b.ring, x, y));
  }

  /** Whether the jet bridges, the buildings (their eaves and canopies over the walkway) and the aircraft with their
   * tugs and ground equipment leave a group's heads and middles at P in sight from c (a ray to each against their
   * meshes). */
  #clearTo(c, P) {
    const ray = (this.ray ??= new THREE.Raycaster());
    const to = new THREE.Vector3();
    for (const h of [0.9, 2.2]) {
      to.set(P.x, h, P.z).sub(c);
      const d = to.length();
      ray.set(c, to.normalize());
      ray.far = d - 1.5;
      if (ray.intersectObjects([this.bridges.group, this.buildings.group, this.fleet.group], true).some((x) => solid(x.object))) return false;
    }
    return true;
  }

  /** The fuselages and wings of the aircraft about within r of (x, y): [x0, y0, x1, y1, half width] in ENU metres. */
  #hulls(x, y, r) {
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    const out = [];
    for (const f of this.fleet.live) {
      if ((f.pose.alt || 0) > 5 || Math.hypot(f.pose.x - x, f.pose.y - y) > r + f.dims.length) continue;
      a.set(0, 0, -f.dims.nose).applyMatrix4(f.matrix);
      b.set(0, 0, f.dims.length - f.dims.nose).applyMatrix4(f.matrix);
      out.push([a.x, -a.z, b.x, -b.z, 2.4]);
      for (const side of [-1, 1]) {
        a.set(0, 0, f.dims.main - 1).applyMatrix4(f.matrix);
        b.set((side * f.dims.span) / 2, 0, f.dims.lz + 1).applyMatrix4(f.matrix);
        out.push([a.x, -a.z, b.x, -b.z, 2.6]);
      }
    }
    return out;
  }

  #flightsOf(key) {
    const tr = this.plan.tracks.get(key);
    return new Set((tr?.visits || []).flatMap((v) => [v.arr?.flight, v.dep?.flight]).filter(Boolean));
  }

  #aircraftOf(crewKey) {
    const g = this.crews.list().find((x) => x.key === crewKey);
    if (!g) return null;
    for (const tr of this.plan.tracks.values()) {
      if (tr.task && tr.visits.some((v) => g.flights.includes(v.dep?.flight) && v.stand === g.stand)) return tr.key;
    }
    return null;
  }

  #tags(live) {
    const flights = this.#flightsOf(this.featured);
    const focus = typeof this.rig.at === "string" && this.rig.at.startsWith("crew:") ? this.rig.at.slice(5) : null;
    const out = this.crews.tags(this.camera, flights, focus, focus && !this.crews.shown(focus) ? this.#crewAt(focus) : null);
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
  const head = leg.tag.split(" · ")[0], crew = crewAt(leg, t);
  if (crew) return { text: `${head} · ${crew}`, tone: "crew", crew: true };
  if (leg.tone === "delay" && leg.kind === "dep" && leg.sched != null && t < leg.sched - KNOWN) return { text: `${head} · on time`, tone: "ok", crew: false };
  return { text: leg.tag, tone: leg.tone || "ok", crew: false };
}

/** Minutes before its scheduled departure a flight's own delay becomes known, as the env reveals it (core/known.py
 * LOOK): until then a delayed departure's tag reads on time, as the departures board does. */
const KNOWN = 120;

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

function segDist(x, y, [x0, y0, x1, y1]) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const u = Math.max(0, Math.min(1, ((x - x0) * dx + (y - y0) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(x0 + dx * u - x, y0 + dy * u - y);
}

function inRing(ring, x, y) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
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
