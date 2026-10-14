import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { aircraftLook, buildAircraft } from "./aircraft.js";
import { buildBuildings } from "./buildings.js";
import { headingToRotY, loadMoment, loadStage, toScene } from "./data.js";
import { buildEnvironment } from "./env.js";
import { buildGround, cameraPresets } from "./ground.js";
import { buildLabels } from "./labels.js";
import { buildTracks, poseAt } from "./motion.js";
import { buildWeather } from "./weather.js";

const Q = new URLSearchParams(location.search);
const RECORD = Q.get("record") === "1";
const DEG = Math.PI / 180;
const SURFACE = { parked: 0.06, pushback: 0.06, lineup: 0.14, roll: 0.14, rollout: 0.14, climb: 0.14, approach: 0.14, flare: 0.14 };
const BLOB = 0.21;
const WX = { RA: "rain", DZ: "drizzle", SN: "snow", BR: "mist", FG: "fog", HZ: "haze", PL: "ice pellets", GR: "hail", GS: "small hail", SG: "snow grains", UP: "precipitation" };
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const ease = (u) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));
const angDiff = (a, b) => ((((b - a) % 360) + 540) % 360) - 180;
const wheel = (r, w) => new THREE.CylinderGeometry(r, r, w, 12).rotateZ(Math.PI / 2);
const box = (w, h, l) => new THREE.BoxGeometry(w, h, l);
const gseMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.6 });

let wallMs = 0;
if (RECORD) {
  performance.now = () => wallMs;
  document.body.classList.add("record");
}
if (Q.get("ui") === "0") document.body.classList.add("bare");

const stageUrl = new URL(Q.get("stage") || "../../cache/stage/BOS/stage.json.gz", location.href);
const momentUrl = new URL(Q.get("moment") || "../../cache/stage/BOS/moment-bos-20250216.json", location.href);
const [stage, moment] = await Promise.all([loadStage(stageUrl.href), loadMoment(momentUrl.href)]);
const [START, END] = moment.window;
let T = +(Q.get("t") || 1035);
let rate = null;

const host = document.getElementById("stage");
const renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: true, preserveDrawingBuffer: RECORD, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
host.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 0.5, 60000);

const env = buildEnvironment(renderer, scene, stage, moment);
const ground = await buildGround(stage, new URL(".", stageUrl).href, { approach: [moment.runways_in_use?.arrivals, "4R"].filter(Boolean) });
scene.add(ground.group);
const buildings = buildBuildings(stage);
scene.add(buildings.group);
const weather = buildWeather(scene);
const tracks = buildTracks(stage, moment);

const fleet = moment.aircraft.map((ac) => {
  const a = buildAircraft({ class: ac.class, length: ac.length, span: ac.span, jetblue: !!ac.jetblue });
  const d = a.object.userData.dims;
  const root = new THREE.Group();
  const pivot = new THREE.Group();
  pivot.position.z = d.main;
  a.object.position.z = -d.main;
  root.add(pivot);
  pivot.add(a.object);
  root.name = `ac:${ac.key}`;
  scene.add(root);
  return { ac, a, d, root, pivot, track: tracks.get(ac.key), pose: null };
});
const featured = fleet.find((f) => f.track.featured) || fleet[0];
Object.assign(featured.a.addLandingSpot(60000), { angle: 0.3, penumbra: 0.9 });

const labels = buildLabels(host, { occluders: occluders(stage) });
const CAMS = cameraPresets(stage, moment);
const featStand = stage.stands.find((s) => s.ref === featured.ac.stand);
if (stage.tower && featStand) CAMS.tower = fromTower(stage.tower, [featStand.xy[0] - Math.sin(featStand.heading * DEG) * 120, featStand.xy[1] - Math.cos(featStand.heading * DEG) * 120], 50);
Object.assign(CAMS, JSON.parse(Q.get("cams") || "{}"));
const controls = RECORD ? null : new OrbitControls(camera, renderer.domElement);
if (controls) {
  controls.enableDamping = true;
  controls.maxPolarAngle = 1.53;
  controls.screenSpacePanning = false;
}
const tugs = Array.from({ length: 6 }, buildTug);
for (const g of tugs) scene.add(g);
const gse = buildGse(fleet.length);
scene.add(gse.group);
const glints = buildGlints(fleet.length * 3);
scene.add(glints.mesh);
let rig = { kind: "preset", name: "tower" };
const chase = { h: null };
const _c = new THREE.Vector3();
const _t = new THREE.Vector3();

function occluders(st) {
  const mat = new THREE.MeshBasicMaterial();
  return [...st.terminals, ...(st.hangars || [])].map((b) => {
    const shape = new THREE.Shape(b.outer.map(([x, y]) => new THREE.Vector2(x, y)));
    for (const h of b.holes || []) shape.holes.push(new THREE.Path(h.map(([x, y]) => new THREE.Vector2(x, y))));
    const m = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: b.height, bevelEnabled: false }).rotateX(-Math.PI / 2), mat);
    m.updateMatrixWorld();
    return m;
  });
}

function fromTower(tw, to, fov) {
  const d = Math.hypot(to[0] - tw.xy[0], to[1] - tw.xy[1]) || 1;
  const k = 12 / d;
  return { position: [tw.xy[0] + (to[0] - tw.xy[0]) * k, tw.xy[1] + (to[1] - tw.xy[1]) * k, tw.height - 19], target: [to[0], to[1], 0], fov };
}

function kit(parts) {
  return mergeGeometries(parts.map(([geo, col, x, y, z]) => {
    const c = new THREE.Color(col);
    geo.translate(x, y, z);
    geo.setAttribute("color", new THREE.Float32BufferAttribute(Array.from({ length: geo.getAttribute("position").count }, () => [c.r, c.g, c.b]).flat(), 3));
    return geo;
  }));
}

function buildTug() {
  const parts = [[box(2.5, 0.9, 6), "#C9A43A", 0, 0.75, -3], [box(2.3, 0.14, 5.6), "#3A3D40", 0, 1.27, -3], [box(1.5, 1.0, 1.4), "#262C31", 0.4, 1.84, -4.9], [box(2.56, 0.12, 6.06), "#2A2A2A", 0, 0.42, -3]];
  for (const x of [-1.12, 1.12]) for (const z of [-1, -5]) parts.push([wheel(0.52, 0.42), "#151617", x, 0.52, z]);
  const g = new THREE.Group();
  g.add(new THREE.Mesh(kit(parts), gseMat));
  const bar = new THREE.Mesh(box(0.16, 0.16, 1).translate(0, 0.55, 0.5), new THREE.MeshStandardMaterial({ color: "#3B3E42", roughness: 0.6 }));
  const beacon = new THREE.Mesh(box(0.26, 0.2, 0.26), new THREE.MeshBasicMaterial({ color: "#000" }));
  beacon.position.set(0.4, 2.45, -4.9);
  g.add(bar, beacon);
  g.userData = { bar, beacon };
  g.visible = false;
  return g;
}

function buildGse(n) {
  const slope = Math.atan2(1.0, 6.6);
  const loader = [[box(6.2, 0.9, 2.0), "#D5D3CC", 3.7, 0.7, 0], [box(7.0, 0.16, 0.95).rotateZ(-slope), "#2A2B2D", 3.6, 1.65, 0], [box(1.2, 1.1, 1.3), "#2B3136", 5.9, 1.7, 0.45]];
  for (const x of [1.3, 6.0]) for (const z of [-0.85, 0.85]) loader.push([wheel(0.36, 0.3), "#151617", x, 0.36, z]);
  const train = [[box(1.5, 1.0, 2.6), "#C9A43A", 0, 0.75, 1.3], [box(1.45, 0.08, 1.3), "#2A2B2D", 0, 1.9, 1.9]];
  for (let k = 0; k < 3; k++) {
    const z = 4.4 + k * 3.1;
    train.push([box(1.6, 1.3, 2.5), "#5E6B76", 0, 1.05, z], [box(1.66, 0.25, 2.56), "#2F3438", 0, 0.45, z]);
    for (const dz of [-0.8, 0.8]) for (const x of [-0.7, 0.7]) train.push([wheel(0.24, 0.18), "#151617", x, 0.24, z + dz]);
  }
  const meshes = [kit(loader), kit(train)].map((g) => {
    const m = new THREE.InstancedMesh(g, gseMat, n);
    m.frustumCulled = false;
    m.count = 0;
    return m;
  });
  const group = new THREE.Group();
  group.add(...meshes);
  const local = new THREE.Matrix4();
  const world = new THREE.Matrix4();
  return {
    group,
    update(list) {
      let i = 0;
      for (const f of list) {
        const half = Math.min(3, Math.max(1.4, f.d.length * 0.05));
        const door = Math.max(3, f.d.nose * 0.9);
        meshes[0].setMatrixAt(i, world.multiplyMatrices(f.root.matrixWorld, local.makeRotationY(-0.35).setPosition(half + 0.2, 0, door)));
        meshes[1].setMatrixAt(i, world.multiplyMatrices(f.root.matrixWorld, local.makeRotationY(0).setPosition(half + 8.5, 0, door + 3)));
        i++;
      }
      for (const m of meshes) {
        m.count = i;
        m.instanceMatrix.needsUpdate = true;
      }
    },
  };
}

function buildGlints(n) {
  const base = new THREE.PlaneGeometry(2, 1).translate(0, 0.5, 0);
  const g = new THREE.InstancedBufferGeometry();
  g.index = base.index;
  g.setAttribute("position", base.getAttribute("position"));
  const aBase = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3).setUsage(THREE.DynamicDrawUsage);
  const aCol = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3).setUsage(THREE.DynamicDrawUsage);
  g.setAttribute("aBase", aBase);
  g.setAttribute("aCol", aCol);
  g.instanceCount = 0;
  const m = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uY: { value: 0.25 } }]),
    vertexShader: /* glsl */ `
      uniform float uY, fogDensity;
      attribute vec3 aBase, aCol;
      varying vec3 vCol;
      varying vec2 vUv;
      #include <common>
      #include <logdepthbuf_pars_vertex>
      void main() {
        vec2 toCam = cameraPosition.xz - aBase.xz;
        float D = max(length(toCam), 1.0);
        vec2 dir = toCam / D;
        float h = aBase.y;
        float camH = max(cameraPosition.y, 0.5);
        float s = D * h / (h + camH);
        float L = min(0.35 * s + 4.0 + h * 2.0, (D - s) * 1.1 + 2.0);
        float w = 0.5 + h * 0.05 + D * 0.0015;
        vec2 p = aBase.xz + dir * (s + (position.y - 0.45) * L) + vec2(-dir.y, dir.x) * position.x * w;
        gl_Position = projectionMatrix * viewMatrix * vec4(p.x, uY, p.y, 1.0);
        vUv = position.xy;
        vCol = aCol * exp(-fogDensity * fogDensity * D * D * 0.55);
        #include <logdepthbuf_vertex>
      }`,
    fragmentShader: /* glsl */ `
      varying vec3 vCol;
      varying vec2 vUv;
      #include <common>
      #include <logdepthbuf_pars_fragment>
      void main() {
        #include <logdepthbuf_fragment>
        float x = abs(vUv.x);
        float a = exp(-x * x * 6.0) * exp(-pow((vUv.y - 0.4) * 2.2, 2.0)) * smoothstep(0.0, 0.1, vUv.y);
        float ripple = 0.72 + 0.28 * sin(vUv.y * 47.0 + vUv.x * 4.0) * sin(vUv.y * 13.0);
        gl_FragColor = vec4(vCol * a * ripple, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    fog: true,
  });
  const mesh = new THREE.Mesh(g, m);
  mesh.frustumCulled = false;
  mesh.renderOrder = 4;
  const w = new THREE.Vector3();
  return {
    mesh,
    update(list, cam, k) {
      let i = 0;
      for (const f of list) {
        const L = f.pose?.lights;
        if (!f.root.visible || !L || !(L.landing || L.taxi) || f.pose.alt > 3 || k <= 0) continue;
        const fw = forward(f.pose.heading);
        const d = f.d;
        const pts = L.landing ? [[0, d.ng, -0.3, 4], [-d.lx, d.ly, d.lz, 2.2], [d.lx, d.ly, d.lz, 2.2]] : [[0, d.ng, -0.3, 2]];
        const tilt = 1 - Math.min(1, Math.max(0, ((f.pose.pitch || 0) - 1.5) / 5));
        for (const [x, y, z, amp] of pts) {
          f.a.object.localToWorld(w.set(x, y, z));
          const dx = cam.x - w.x;
          const dz = cam.z - w.z;
          const look = (fw.x * dx + fw.z * dz) / (Math.hypot(dx, dz) || 1);
          const a = amp * k * tilt * Math.max(0, Math.min(1, (look - 0.2) / 0.7)) ** 2;
          if (a < 0.01 || i >= n) continue;
          aBase.setXYZ(i, w.x, Math.max(0.6, w.y), w.z);
          aCol.setXYZ(i, a, a * 0.95, a * 0.86);
          i++;
        }
      }
      g.instanceCount = i;
      aBase.needsUpdate = aCol.needsUpdate = true;
    },
  };
}

function placeTugs(wall) {
  let i = 0;
  for (const f of fleet) {
    if (f.pose?.phase !== "pushback" || i >= tugs.length) continue;
    const g = tugs[i++];
    const reach = f.d.nose + 1;
    g.visible = true;
    f.root.localToWorld(g.position.set(0, 0, -reach));
    g.position.y = 0.06;
    g.rotation.y = f.root.rotation.y;
    g.userData.bar.scale.z = reach;
    const on = (wall * 1.4 + i * 0.37) % 1 < 0.5 ? 1 : 0.08;
    g.userData.beacon.material.color.setRGB(3.2 * on, 1.5 * on, 0.15 * on);
  }
  for (; i < tugs.length; i++) tugs[i].visible = false;
}

function place(f, wall) {
  const p = poseAt(f.track, T);
  f.pose = p;
  f.root.visible = p.phase !== "absent";
  if (!f.root.visible) return;
  const base = SURFACE[p.phase] ?? 0.1;
  f.root.position.set(p.x, base, -p.y);
  f.root.rotation.y = headingToRotY(p.heading);
  f.pivot.rotation.x = (p.pitch || 0) * DEG;
  f.a.object.position.y = p.alt || 0;
  const blob = f.a.object.userData.blob;
  if (blob) blob.position.y = BLOB - base - 0.05;
  f.a.setLights({ ...p.lights, time: wall });
  f.root.updateMatrixWorld(true);
}

const centreOf = (f, out) => f.a.object.localToWorld(out.set(0, f.d.height * 0.3, f.d.length / 2 - f.d.nose));
const forward = (h) => new THREE.Vector3(Math.sin(h * DEG), 0, -Math.cos(h * DEG));

function aim(dt) {
  if (rig.kind === "preset") {
    if (!rig.set) {
      const c = CAMS[rig.name] || CAMS.tower;
      camera.position.copy(toScene(c.position, c.position[2]));
      _t.copy(toScene(c.target, c.target[2]));
      camera.fov = c.fov;
      camera.updateProjectionMatrix();
      camera.lookAt(_t);
      if (controls) controls.target.copy(_t);
      rig.set = true;
    }
    if (controls) controls.update();
    return;
  }
  const f = typeof rig.at === "string" && rig.at !== "featured" ? fleet.find((x) => x.ac.key === rig.at) || featured : featured;
  const p = f.pose;
  centreOf(f, _c);
  if (rig.kind === "follow") {
    const k = 1 - Math.exp(-dt / (rig.tau ?? 0.9));
    chase.h = chase.h == null ? p.heading : (chase.h + angDiff(chase.h, p.heading) * k + 360) % 360;
    const fw = forward(chase.h);
    const rt = new THREE.Vector3(-fw.z, 0, fw.x);
    camera.position.copy(_c).addScaledVector(fw, -(rig.dist ?? 95)).addScaledVector(rt, rig.side ?? 35);
    camera.position.y = _c.y + (rig.up ?? 30);
    _t.copy(_c).addScaledVector(fw, rig.lead ?? 30);
    camera.fov = rig.fov ?? 42;
  } else {
    const u = rig.t1 != null ? ease((T - rig.t0) / (rig.t1 - rig.t0)) : 0;
    const a = toScene(rig.from, rig.from[2]);
    camera.position.lerpVectors(a, rig.to ? toScene(rig.to, rig.to[2]) : a, u);
    if (typeof rig.at === "string") {
      _t.copy(_c).addScaledVector(forward(p.heading), rig.lead || 0);
      if (rig.offset) _t.add(toScene(rig.offset, rig.offset[2]));
    } else _t.copy(toScene(rig.at, rig.at[2]));
    camera.fov = rig.fov + ((rig.fov1 ?? rig.fov) - rig.fov) * u;
    if (rig.frame) camera.fov = Math.min(camera.fov, Math.max(3, (2 * Math.atan(rig.frame / 2 / camera.position.distanceTo(_t))) / DEG));
  }
  camera.updateProjectionMatrix();
  camera.lookAt(_t);
  if (controls) controls.target.copy(_t);
}

function legAt(ac, t) {
  const legs = ac.legs.filter((l) => l.tag);
  const key = (l) => (l.status === "cancelled" ? l.sched : l.kind === "dep" ? l.out : l.in ?? l.on);
  const live = legs.find((l) => l.status === "flown" && (l.kind === "dep" ? t >= l.out - 15 && t <= (l.off ?? l.out) + 3 : t >= (l.on ?? l.in) - 8 && t <= (l.in ?? l.on) + 20));
  if (live) return live;
  const cancelled = legs.find((l) => l.status === "cancelled" && Math.abs(t - l.sched) <= 45);
  if (cancelled) return cancelled;
  return legs.filter((l) => l.status === "flown" && key(l) >= t).sort((a, b) => key(a) - key(b))[0] || null;
}

function toneOf(leg) {
  if (leg.status === "cancelled") return "cancel";
  if (/held/i.test(leg.tag)) return "hold";
  return /delayed/.test(leg.tag) ? "delay" : "ok";
}

function tags() {
  const out = [];
  for (const f of fleet) {
    if (!f.ac.jetblue || !f.root.visible) continue;
    const leg = f === featured ? f.ac.legs.find((l) => l.flight === moment.featured?.flight) || legAt(f.ac, T) : legAt(f.ac, T);
    if (!leg) continue;
    out.push({ key: f.ac.key, object: f.a.object, text: leg.tag, tone: toneOf(leg), featured: f === featured, moving: (f.pose.speed || 0) > 0.3 });
  }
  return out;
}

function metarAt(t) {
  const list = moment.weather || [];
  return list.filter((w) => w.t <= t).pop() || list[0] || {};
}

function weatherWords(w) {
  const words = (w.wx || []).map((c) => {
    const light = c[0] === "-";
    const heavy = c[0] === "+";
    const body = c.replace(/^[-+]/, "").replace(/^VC/, "");
    const d = body.slice(0, 2);
    const rest = body.slice(2);
    let s = d === "SH" ? `${WX[rest] || "rain"} showers` : d === "TS" ? (rest ? `thunderstorm with ${WX[rest] || rest}` : "thunderstorm") : d === "FZ" ? `freezing ${WX[rest] || rest}` : WX[body] || body.toLowerCase();
    if (heavy) s = `heavy ${s}`;
    else if (light && !/DZ|BR|FG|HZ/.test(body)) s = `light ${s}`;
    return s;
  });
  const what = words.length > 1 ? `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}` : words[0] || "Dry";
  const parts = [what[0].toUpperCase() + what.slice(1)];
  if (w.vis_sm != null) parts.push(`visibility ${w.vis_sm >= 10 ? "10+" : +w.vis_sm.toFixed(w.vis_sm < 1 ? 2 : 0)} mi`);
  const layer = w.ceiling_ft ?? (w.clouds || [])[0]?.[1];
  if (layer != null) parts.push(`cloud at ${Math.round(layer).toLocaleString("en-US")} ft`);
  const mph = (kt) => Math.round(kt * 1.15078);
  if (w.wind_kt) parts.push(`wind ${w.wind_dir == null ? "variable" : COMPASS[Math.round(w.wind_dir / 22.5) % 16]} ${mph(w.wind_kt)} mph${w.gust_kt ? ` gusting ${mph(w.gust_kt)}` : ""}`);
  else if (w.wind_kt === 0) parts.push("calm");
  return parts.join(" · ");
}

const elWhen = document.getElementById("ao-when");
const elWx = document.getElementById("ao-wx");
document.getElementById("ao-attr").textContent = "Boston Logan twin: © OpenStreetMap contributors (ODbL) · imagery: USGS The National Map · flights: BTS · weather: IEM";
const dayName = new Date(`${moment.date}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }).replace(",", "");
let cardSig = "";

function card() {
  const m = Math.floor(T + 1e-6);
  const hh = `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const r = rate && rate > 1.5 ? `${Math.round(rate)}× speed` : "";
  const w = metarAt(T);
  const sig = `${hh}|${r}|${w.t}`;
  if (sig === cardSig) return;
  cardSig = sig;
  elWhen.innerHTML = `${dayName} · ${hh}${r ? `<small>${r}</small>` : ""}`;
  elWx.textContent = weatherWords(w);
}

function frame(dt) {
  const s = env.update(T);
  const w = env.weatherAt(T);
  const day = s.atm.day;
  ground.update(T, day, w);
  buildings.update(T, day);
  aircraftLook.cabin.value = 0.15 + 0.85 * (1 - day);
  aircraftLook.wet.value = 0.7 * s.wet;
  aircraftLook.beam.value = 0.55 + 0.6 * s.mist;
  const wall = performance.now() / 1000;
  for (const f of fleet) place(f, wall);
  placeTugs(wall);
  gse.update(fleet.filter((f) => f.pose.phase === "parked"));
  aim(dt);
  glints.update(fleet, camera.position, s.wet * (0.35 + 0.65 * (1 - day)));
  weather.update(dt, T, w, camera);
  renderer.render(scene, camera);
  labels.update(camera, renderer, tags());
  card();
}

function view(name, opts = {}) {
  if (name === "follow") {
    rig = { kind: "follow", name, ...opts };
    chase.h = null;
  } else rig = { kind: "preset", name: CAMS[name] ? name : "tower" };
  if (controls) controls.enabled = rig.kind === "preset";
  return rig.name;
}

function counts() {
  const c = {};
  for (const f of fleet) if (f.pose) c[f.pose.phase] = (c[f.pose.phase] || 0) + 1;
  return c;
}

const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));
view(Q.get("cam") || "tower");
const ready = ground.ready.then(async (far) => {
  for (let i = 0; i < 3; i++) {
    frame(1 / 30);
    await nextFrame();
  }
  const evs = featured.track.events.map((e) => ({ ...e }));
  return { start: START, end: END, cams: [...Object.keys(CAMS), "follow"], far, featured: { key: featured.ac.key, flight: moment.featured?.flight, stand: featured.ac.stand, events: evs } };
});

window.airops = {
  ready,
  scene,
  camera,
  renderer,
  async time(t, dt = null, speed = null) {
    await ready;
    const step = dt ?? 1 / 30;
    rate = speed;
    T = +t;
    wallMs += step * 1000;
    await nextFrame();
    frame(step);
    await nextFrame();
    return T;
  },
  view(name, opts) {
    const n = view(name, opts);
    if (RECORD) frame(0);
    return n;
  },
  shot(spec) {
    rig = { kind: "shot", name: spec.name || "shot", ...spec };
    if (controls) controls.enabled = false;
    return rig.name;
  },
  pose(t = T, key = featured.ac.key) {
    return poseAt(fleet.find((x) => x.ac.key === key).track, t);
  },
  state() {
    return { t: T, cam: rig.name, counts: counts(), featured: featured.pose && { phase: featured.pose.phase, x: featured.pose.x, y: featured.pose.y } };
  },
  perf(n = 20) {
    const gl = renderer.getContext();
    const px = new Uint8Array(4);
    frame(1 / 30);
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    const t0 = Date.now();
    for (let i = 0; i < n; i++) {
      frame(1 / 30);
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    }
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    return { ms: +((Date.now() - t0) / n).toFixed(2), calls: renderer.info.render.calls, tris: renderer.info.render.triangles, gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : "" };
  },
};

addEventListener("resize", () => {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
});

if (!RECORD) {
  const speed = +(Q.get("speed") || 10);
  let playing = Q.get("play") !== "0";
  const clock = new THREE.Clock();
  const names = [...Object.keys(CAMS), "follow"];
  addEventListener("keydown", (e) => {
    if (e.key === " ") playing = !playing;
    else if (e.key === "ArrowRight") T = Math.min(END, T + 1);
    else if (e.key === "ArrowLeft") T = Math.max(START, T - 1);
    else if (e.key === "c") view(names[(names.indexOf(rig.name) + 1) % names.length]);
  });
  renderer.setAnimationLoop(() => {
    const dt = Math.min(clock.getDelta(), 0.1);
    if (playing) T = Math.min(END, T + (dt * speed) / 60);
    rate = playing ? speed : null;
    frame(dt);
  });
}
