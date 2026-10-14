import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { aircraftKit, aircraftMaterials, LIGHT_COLORS } from "./aircraft.js";
import { poseAt } from "./motion.js";

const DEG = Math.PI / 180;
const RUNWAY = new Set(["lineup", "roll", "rollout", "climb", "approach", "flare"]);
const PARTS = ["paint", "metal", "dark", "fan", "gearMetal", "gearDark", "blob"];
const SNOWY = new Set(["paint", "metal", "dark"]);
const BEAMS = 18;
const TUGS = 16;
const ZERO = new THREE.Matrix4().makeScale(0, 0, 0);
const LANDING = [[-1, "ly", "lz", 90, 9, 2.5], [1, "ly", "lz", 90, 9, 2.5], [0, "ng", -0.2, 80, 8, 2]];
const TAXI = [[0, "ng", -0.25, 45, 9, 4.5]];
const wheel = (r, w) => new THREE.CylinderGeometry(r, r, w, 12).rotateZ(Math.PI / 2);
const box = (w, h, l) => new THREE.BoxGeometry(w, h, l);
const hash = (s) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10000) / 10000;
};

/** Every aircraft of the day drawn instanced by kit: only the ones about at t are in the scene, posed from their tracks, with their lights, beams, tugs and ground crews. */
export function buildFleet(plan) {
  const S = aircraftMaterials();
  const group = new THREE.Group();
  group.name = "fleet";
  const kits = new Map();
  const craft = new Map();
  for (const tr of plan.tracks.values()) {
    const K = aircraftKit(tr.spec);
    if (!kits.has(K.key)) kits.set(K.key, { K, cap: 0, n: 0, keys: [] });
    const k = kits.get(K.key);
    k.cap++;
    craft.set(tr.key, { key: tr.key, track: tr, kit: k, phase: hash(tr.key) * 10 });
  }
  const picks = [];
  for (const k of kits.values()) {
    k.meshes = {};
    k.snow = [];
    for (const part of PARTS) {
      const src = part === "blob" ? k.K.shadow.geo : k.K.geos[part];
      const geo = new THREE.BufferGeometry();
      for (const [name, a] of Object.entries(src.attributes)) geo.setAttribute(name, a);
      geo.setIndex(src.index);
      if (SNOWY.has(part)) {
        const a = new THREE.InstancedBufferAttribute(new Float32Array(k.cap), 1).setUsage(THREE.DynamicDrawUsage);
        geo.setAttribute("aSnow", a);
        k.snow.push(a);
      }
      const mat = part === "paint" ? k.K.skin : part === "blob" ? k.K.shadow.mat : S[part];
      const m = new THREE.InstancedMesh(geo, mat, k.cap);
      m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      m.frustumCulled = false;
      m.count = 0;
      m.castShadow = part !== "blob" && part !== "fan";
      m.receiveShadow = part !== "blob";
      if (part === "blob") m.renderOrder = -1;
      m.userData.kit = k;
      group.add(m);
      k.meshes[part] = m;
      if (part === "paint" || part === "metal") picks.push(m);
    }
  }
  const nLights = [...craft.values()].reduce((n, c) => n + c.kit.K.lights.length, 0);
  const glowGeo = new THREE.BufferGeometry();
  const gPos = new THREE.BufferAttribute(new Float32Array(nLights * 3), 3).setUsage(THREE.DynamicDrawUsage);
  const gCol = new THREE.BufferAttribute(new Float32Array(nLights * 3), 3).setUsage(THREE.DynamicDrawUsage);
  const gSize = new THREE.BufferAttribute(new Float32Array(nLights), 1).setUsage(THREE.DynamicDrawUsage);
  const gDir = new THREE.BufferAttribute(new Float32Array(nLights * 3), 3).setUsage(THREE.DynamicDrawUsage);
  glowGeo.setAttribute("position", gPos);
  glowGeo.setAttribute("aColor", gCol);
  glowGeo.setAttribute("aSize", gSize);
  glowGeo.setAttribute("aDir", gDir);
  const glow = new THREE.Points(glowGeo, S.glow);
  glow.frustumCulled = false;
  glow.renderOrder = 5;
  const _px = new THREE.Vector2();
  glow.onBeforeRender = (r) => (S.glow.uniforms.uPx.value = r.getDrawingBufferSize(_px).y / 2);
  group.add(glow);
  const beams = Array.from({ length: BEAMS * 3 }, () => {
    const m = new THREE.Mesh(S.cone, S.beam);
    m.matrixAutoUpdate = false;
    m.visible = false;
    m.renderOrder = 4;
    m.frustumCulled = false;
    group.add(m);
    return m;
  });
  const spot = new THREE.SpotLight(0xfff2e2, 0, 260, 0.3, 0.9, 2);
  spot.castShadow = false;
  group.add(spot, spot.target);
  const tugs = Array.from({ length: TUGS }, buildTug);
  group.add(...tugs);
  const gse = buildGse(craft.size);
  group.add(gse.group);
  const glints = buildGlints(BEAMS * 3);
  group.add(glints.mesh);
  group.traverse((o) => o.layers.set(1));

  const M = new THREE.Matrix4();
  const A = new THREE.Matrix4();
  const B = new THREE.Matrix4();
  const v = new THREE.Vector3();
  const w = new THREE.Vector3();
  const live = [];
  let last = [];

  function matrixOf(p, d, out) {
    const base = p.phase === "parked" || p.phase === "pushback" ? 0.06 : RUNWAY.has(p.phase) ? 0.14 : 0.1;
    out.makeRotationY(-p.heading * DEG).setPosition(p.x, base, -p.y);
    if (p.pitch || p.alt) {
      out.multiply(A.makeTranslation(0, 0, d.main)).multiply(B.makeRotationX((p.pitch || 0) * DEG)).multiply(A.makeTranslation(0, p.alt || 0, -d.main));
    }
    return out;
  }

  function lightsOf(c, p, wall, M, i0) {
    const L = p.lights;
    const t = wall + c.phase;
    const bp = t % 1.05;
    const sp = t % 1.3;
    const level = {
      beacon: L.beacon ? Math.exp(-Math.max(0, bp - 0.04) * 18) * (bp < 0.45 ? 1 : 0) : 0,
      navL: L.nav ? 1 : 0,
      navR: L.nav ? 1 : 0,
      navT: L.nav ? 1 : 0,
      strobe: L.strobe && (sp < 0.05 || (sp > 0.13 && sp < 0.18)) ? 1 : 0,
      landing: L.landing ? 1 : 0,
      taxi: L.taxi ? 1 : 0,
    };
    let i = i0;
    for (const [kind, pos, size] of c.kit.K.lights) {
      v.set(...pos).applyMatrix4(M);
      gPos.setXYZ(i, v.x, v.y, v.z);
      const col = LIGHT_COLORS[kind];
      const lv = level[kind];
      gCol.setXYZ(i, col[0] * lv, col[1] * lv, col[2] * lv);
      gSize.setX(i, size);
      if (kind === "landing" || kind === "taxi") {
        w.set(0, -0.05, -1).transformDirection(M);
        gDir.setXYZ(i, w.x, w.y, w.z);
      } else gDir.setXYZ(i, 0, 0, 0);
      i++;
    }
    return i;
  }

  return {
    group,
    kits,
    picks,
    craft,
    /** Pose everyone about at t; snow(c, p) is the snow lying on a parked aircraft; returns the live list. */
    update(t, wall, camera, { featured = null, snow = () => 0, wet = 0, night = 0 } = {}) {
      for (const k of kits.values()) k.n = 0;
      live.length = 0;
      let li = 0;
      for (const key of plan.near(t, 0.05)) {
        const c = craft.get(key);
        const p = poseAt(c.track, t);
        if (p.phase === "absent") continue;
        const k = c.kit;
        const d = k.K.dims;
        const i = k.n++;
        const m = matrixOf(p, d, new THREE.Matrix4());
        for (const part of ["paint", "metal", "dark", "fan"]) k.meshes[part].setMatrixAt(i, m);
        if (p.gear > 0.002) {
          const g = A.makeTranslation(0, d.belly, 0).multiply(B.makeScale(1 - 0.25 * (1 - p.gear), p.gear, 0.6 + 0.4 * p.gear)).multiply(M.makeTranslation(0, -d.belly, 0));
          const gm = new THREE.Matrix4().multiplyMatrices(m, g);
          k.meshes.gearMetal.setMatrixAt(i, gm);
          k.meshes.gearDark.setMatrixAt(i, gm);
        } else {
          k.meshes.gearMetal.setMatrixAt(i, ZERO);
          k.meshes.gearDark.setMatrixAt(i, ZERO);
        }
        if ((p.alt || 0) < 0.5) {
          const base = p.phase === "parked" || p.phase === "pushback" ? 0.12 : RUNWAY.has(p.phase) ? 0.13 : 0.12;
          k.meshes.blob.setMatrixAt(i, M.makeRotationY(-p.heading * DEG).setPosition(p.x, base - 0.04, -p.y));
        } else k.meshes.blob.setMatrixAt(i, ZERO);
        const sn = p.phase === "parked" ? snow(c, p, t) : 0;
        for (const a of k.snow) a.setX(i, sn);
        k.keys[i] = key;
        li = lightsOf(c, p, wall, m, li);
        live.push({ key, c, pose: p, matrix: m, dims: d, kit: k.K, featured: key === featured });
      }
      for (const k of kits.values()) {
        for (const mesh of Object.values(k.meshes)) {
          mesh.count = k.n;
          mesh.instanceMatrix.needsUpdate = true;
        }
        for (const a of k.snow) a.needsUpdate = true;
      }
      glowGeo.setDrawRange(0, li);
      for (const a of [gPos, gCol, gSize, gDir]) {
        a.needsUpdate = true;
        a.clearUpdateRanges();
        a.addUpdateRange(0, li * a.itemSize);
      }
      const cam = camera.position;
      const lit = live.filter((f) => f.pose.lights.landing || f.pose.lights.taxi);
      lit.sort((a, b) => b.featured - a.featured || dist2(a.matrix, cam) - dist2(b.matrix, cam));
      let bi = 0;
      for (const f of lit.slice(0, BEAMS)) {
        const d = f.dims;
        for (const [sx, y, z, len, rad, tilt] of f.pose.lights.landing ? LANDING : TAXI) {
          const b = beams[bi++];
          b.visible = true;
          b.matrix.copy(f.matrix).multiply(A.makeTranslation(sx * d.lx, typeof y === "string" ? d[y] + (y === "ng" ? 0.15 : 0) : y, typeof z === "string" ? d[z] : z)).multiply(B.makeRotationX(-tilt * DEG)).multiply(M.makeScale(rad, rad, len));
        }
      }
      for (; bi < beams.length; bi++) beams[bi].visible = false;
      const fl = live.find((f) => f.featured);
      if (fl && (fl.pose.lights.landing || fl.pose.lights.taxi)) {
        spot.intensity = fl.pose.lights.landing ? 60000 : 24000;
        spot.position.set(0, fl.dims.ng + 0.2, -0.4).applyMatrix4(fl.matrix);
        spot.target.position.set(0, 0, -60).applyMatrix4(fl.matrix);
        spot.target.updateMatrixWorld();
      } else spot.intensity = 0;
      placeTugs(tugs, live, wall);
      gse.update(live.filter((f) => f.pose.phase === "parked" && t * 60 - (f.pose.since ?? -Infinity) > 240));
      glints.update(live, cam, wet * (0.35 + 0.65 * night));
      last = live.slice();
      return last;
    },
    get live() {
      return last;
    },
    pick(raycaster) {
      const hit = raycaster.intersectObjects(picks, false)[0];
      return hit ? hit.object.userData.kit.keys[hit.instanceId] : null;
    },
    matrixOf,
  };
}

const dist2 = (m, c) => (m.elements[12] - c.x) ** 2 + (m.elements[13] - c.y) ** 2 + (m.elements[14] - c.z) ** 2;

const gseMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.6 });

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
  const body = new THREE.Mesh(kit(parts), gseMat);
  body.castShadow = true;
  g.add(body);
  const bar = new THREE.Mesh(box(0.16, 0.16, 1).translate(0, 0.55, 0.5), new THREE.MeshStandardMaterial({ color: "#3B3E42", roughness: 0.6 }));
  const beacon = new THREE.Mesh(box(0.26, 0.2, 0.26), new THREE.MeshBasicMaterial({ color: "#000" }));
  beacon.position.set(0.4, 2.45, -4.9);
  g.add(bar, beacon);
  g.userData = { bar, beacon };
  g.visible = false;
  return g;
}

function placeTugs(tugs, live, wall) {
  let i = 0;
  const q = new THREE.Quaternion();
  const s = new THREE.Vector3();
  for (const f of live) {
    if (f.pose.phase !== "pushback" || i >= tugs.length) continue;
    const g = tugs[i++];
    const reach = f.dims.nose + 1;
    g.visible = true;
    g.position.set(0, 0, -reach).applyMatrix4(f.matrix);
    g.position.y = 0.06;
    f.matrix.decompose(s, q, s.clone());
    g.quaternion.copy(q);
    g.userData.bar.scale.z = reach;
    const on = (wall * 1.4 + i * 0.37) % 1 < 0.5 ? 1 : 0.08;
    g.userData.beacon.material.color.setRGB(3.2 * on, 1.5 * on, 0.15 * on);
  }
  for (; i < tugs.length; i++) tugs[i].visible = false;
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
    m.castShadow = true;
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
        const half = Math.min(3, Math.max(1.4, f.dims.length * 0.05));
        const door = Math.max(3, f.dims.nose * 0.9);
        meshes[0].setMatrixAt(i, world.multiplyMatrices(f.matrix, local.makeRotationY(-0.35).setPosition(half + 0.2, 0, door)));
        meshes[1].setMatrixAt(i, world.multiplyMatrices(f.matrix, local.makeRotationY(0).setPosition(half + 8.5, 0, door + 3)));
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
  const p = new THREE.Vector3();
  return {
    mesh,
    update(list, cam, k) {
      let i = 0;
      for (const f of list) {
        const L = f.pose.lights;
        if (!(L.landing || L.taxi) || (f.pose.alt || 0) > 3 || k <= 0) continue;
        const h = f.pose.heading * DEG;
        const fx = Math.sin(h);
        const fz = -Math.cos(h);
        const d = f.dims;
        const pts = L.landing ? [[0, d.ng, -0.3, 4], [-d.lx, d.ly, d.lz, 2.2], [d.lx, d.ly, d.lz, 2.2]] : [[0, d.ng, -0.3, 2]];
        const tilt = 1 - Math.min(1, Math.max(0, ((f.pose.pitch || 0) - 1.5) / 5));
        for (const [x, y, z, amp] of pts) {
          p.set(x, y, z).applyMatrix4(f.matrix);
          const dx = cam.x - p.x;
          const dz = cam.z - p.z;
          const look = (fx * dx + fz * dz) / (Math.hypot(dx, dz) || 1);
          const a = amp * k * tilt * Math.max(0, Math.min(1, (look - 0.2) / 0.7)) ** 2;
          if (a < 0.01 || i >= n) continue;
          aBase.setXYZ(i, p.x, Math.max(0.6, p.y), p.z);
          aCol.setXYZ(i, a, a * 0.95, a * 0.86);
          i++;
        }
      }
      g.instanceCount = i;
      aBase.needsUpdate = aCol.needsUpdate = true;
    },
  };
}
