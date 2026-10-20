// The control-center room in 3D, display-only, modelled on real airline system-operations centers: tiered rows of
// desks facing a video wall of the network, the radar and the FAA's programs, with the model's two desks (the ops
// controller and the crew scheduler) at their pods in the front row and their requests flying between them.
// new ControlCenter(host, {P, ep, base, manual, controls}); await ready; then time(t), view(name), shot(spec).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RectAreaLightUniformsLib } from "three/addons/lights/RectAreaLightUniformsLib.js";
import { buildCards } from "./cards.js";
import { ROOM, buildHall } from "./hall.js";
import { buildPods } from "./pods.js";
import { WALL, buildWall } from "./wall.js";

const VIEWS = {
  room: { from: [0, 5.1, 21.0], at: [0, 2.3, 3.5], fov: 48 },
  "room-desks": { from: [0.1, 2.85, 10.6], at: [0, 2.0, 5.4], fov: 40 },
  "room-wall": { from: [0, 3.25, 14.6], at: [0, 3.2, 0], fov: 40 },
};
const CSS = `.cc-caption { position: absolute; z-index: 2; top: 12px; left: 50%; transform: translateX(-50%); padding: 3px 10px; border-radius: 4px;
  font: 500 11.5px/1.4 Inter, "Helvetica Neue", Arial, sans-serif; color: rgba(225,233,240,0.82); background: rgba(10,14,20,0.55); pointer-events: none; white-space: nowrap; }
.view.corner .cc-caption { display: none; }`;
const ease = (u) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));
const vec = (a) => new THREE.Vector3(...a);

export class ControlCenter {
  constructor(container, { P, ep, manual = false, controls = true, pixelRatio = Math.min(globalThis.devicePixelRatio || 1, 2) } = {}) {
    this.container = container;
    this.P = P;
    this.ep = ep;
    this.manual = manual;
    this.T = P.lo ?? P.task.start;
    this.wall = 0;
    this.cards = 0;
    this.seen = 0;
    this.frameMs = [];
    this.rig = { kind: "preset", name: "room", set: false };
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: manual, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(pixelRatio);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.domElement.style.display = "block";
    if (getComputedStyle(container).position === "static") container.style.position = "relative";
    container.appendChild(this.renderer.domElement);
    if (!document.getElementById("cc-css")) {
      const style = document.createElement("style");
      style.id = "cc-css";
      style.textContent = CSS;
      document.head.appendChild(style);
    }
    this.caption = document.createElement("div");
    this.caption.className = "cc-caption";
    this.caption.textContent = "The control-center room is a display-only rendering";
    container.appendChild(this.caption);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x07090c);
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.05, 200);
    this.resize();
    this.ready = this.#build(controls);
  }

  async #build(controls) {
    const { scene, renderer, P, ep } = this;
    RectAreaLightUniformsLib.init();
    this.hall = buildHall();
    scene.add(this.hall.group);
    this.screen = buildWall(P, ep);
    scene.add(this.screen.group);
    this.pods = buildPods(ep);
    scene.add(this.pods.group);
    this.flying = buildCards(P, ep, this.pods.pods);
    scene.add(this.flying.group);
    scene.add(new THREE.HemisphereLight(0x8a9bb5, 0x3a3630, 1.1));
    const glow = new THREE.RectAreaLight(0x9cb8de, 2.4, WALL.w, WALL.h);
    glow.position.set(0, WALL.y0 + WALL.h / 2, WALL.z + 0.05);
    glow.lookAt(0, WALL.y0 + WALL.h / 2, 10);
    scene.add(glow);
    for (const x of [-8, 0, 8]) {
      for (const z of [5.5, 11, 16.5]) {
        const s = new THREE.SpotLight(0xffe9cc, 40, 14, 0.8, 0.9, 1.5);
        s.position.set(x, ROOM.h - 0.05, z);
        s.target.position.set(x, 0, z + 0.5);
        scene.add(s, s.target);
      }
    }
    for (const sx of [-1, 1]) {
      for (const z of [4, 11, 18]) {
        const s = new THREE.SpotLight(0xffd9b0, 30, 9, 0.5, 1, 1.4);
        s.position.set(sx * (ROOM.w / 2 - 0.4), ROOM.h - 0.1, z);
        s.target.position.set(sx * (ROOM.w / 2), 1.2, z);
        scene.add(s, s.target);
      }
    }
    const cove = new THREE.Mesh(new THREE.BoxGeometry(WALL.w + 4, 0.03, 0.03), new THREE.MeshBasicMaterial({ color: 0x3c4856, toneMapped: false }));
    cove.position.set(0, ROOM.h - 0.3, 0.45);
    scene.add(cove);
    if (controls && !this.manual) {
      this.controls = new OrbitControls(this.camera, renderer.domElement);
      this.controls.enableDamping = true;
      this.controls.maxDistance = 26;
      this.controls.screenSpacePanning = true;
    }
    await Promise.race([this.screen.ready, new Promise((r) => setTimeout(r, 6000))]);
    this.#frame(0, true);
    if (!this.manual) {
      const clock = new THREE.Clock();
      renderer.setAnimationLoop(() => {
        const dt = Math.min(clock.getDelta(), 0.1);
        this.wall += dt;
        if (performance.now() - this.seen < 1500) this.#frame(dt);
      });
    }
    return { views: Object.keys(VIEWS), desks: this.pods.desks, requests: ep.occ.requests?.length ?? 0 };
  }

  /** Render the room at minute t of the episode; dt is the wall seconds since the last frame (recording). */
  async time(t, { dt = null } = {}) {
    await this.ready;
    this.T = +t;
    this.seen = performance.now();
    if (this.manual) {
      const step = dt ?? 1 / 30;
      this.wall += step;
      await Promise.race([this.screen.loaded(this.T), new Promise((r) => setTimeout(r, 3000))]);
      await nextFrame();
      this.#frame(step, true);
      await nextFrame();
    } else await nextFrame();
    return this.T;
  }

  /** room | room-desks | room-wall. */
  async view(name) {
    await this.ready;
    const n = VIEWS[name] ? name : "room";
    this.rig = { kind: "preset", name: n, set: false };
    if (this.controls) this.controls.enabled = true;
    if (this.manual) this.#frame(0, true);
    return n;
  }

  /** A scripted camera in room metres: {name, from: [x, y, z], at: [x, y, z], fov, to?, fov1?, t0?, t1?}. */
  async shot(spec) {
    await this.ready;
    this.rig = { kind: "shot", name: spec.name || "shot", ...spec };
    if (this.controls) this.controls.enabled = false;
    if (this.manual) this.#frame(0, true);
    return this.rig.name;
  }

  state() {
    const ms = this.frameMs.slice().sort((a, b) => a - b);
    return { t: this.T, view: this.rig.name, turn: this.pods?.active(this.T) ?? null, cards: this.cards,
      frame: ms.length ? { median: ms[ms.length >> 1], p95: ms[Math.floor(ms.length * 0.95)] } : null };
  }

  resize() {
    const w = this.container.clientWidth || innerWidth, h = this.container.clientHeight || innerHeight;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.seen = performance.now();
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    this.controls?.dispose();
    this.caption.remove();
    this.scene.traverse((o) => {
      o.geometry?.dispose?.();
      for (const m of [].concat(o.material || [])) {
        m.map?.dispose?.();
        m.dispose?.();
      }
    });
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  #frame(dt, force = false) {
    const t0 = performance.now(), T = this.T;
    this.screen.update(T, { force });
    this.pods.update(T, this.wall);
    this.cards = this.flying.update(T);
    this.#aim();
    this.renderer.render(this.scene, this.camera);
    this.frameMs.push(performance.now() - t0);
    if (this.frameMs.length > 120) this.frameMs.shift();
  }

  #aim() {
    const cam = this.camera, rig = this.rig;
    if (rig.kind === "preset") {
      if (!rig.set) {
        const v = VIEWS[rig.name];
        cam.position.copy(vec(v.from));
        cam.fov = v.fov;
        cam.updateProjectionMatrix();
        cam.lookAt(vec(v.at));
        if (this.controls) this.controls.target.copy(vec(v.at));
        rig.set = true;
      }
      this.controls?.update();
      return;
    }
    const u = rig.t1 != null ? ease((this.T - rig.t0) / (rig.t1 - rig.t0)) : 0;
    cam.position.lerpVectors(vec(rig.from), vec(rig.to ?? rig.from), u);
    cam.fov = (rig.fov ?? 45) + ((rig.fov1 ?? rig.fov ?? 45) - (rig.fov ?? 45)) * u;
    cam.updateProjectionMatrix();
    cam.lookAt(vec(rig.at));
  }
}
