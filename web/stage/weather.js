import * as THREE from "three";
import { weatherAt } from "./env.js";

const BOX = 70;
const KT = 0.5144;

function precip(w) {
  const wx = (w && w.wx) || [];
  const out = { rain: 0, drizzle: 0, snow: 0, storm: false, mist: 0 };
  for (const c of wx) {
    const k = c.startsWith("+") ? 1.4 : c.startsWith("-") ? 0.55 : 1;
    if (/RA/.test(c)) out.rain = Math.max(out.rain, k);
    if (/DZ/.test(c)) out.drizzle = Math.max(out.drizzle, k);
    if (/(SN|SG|PL)/.test(c)) out.snow = Math.max(out.snow, k);
    if (/TS/.test(c)) out.storm = true;
    if (/(BR|FG|HZ)/.test(c)) out.mist = /FG/.test(c) ? 1 : 0.6;
  }
  return out;
}

function windOf(w) {
  const kt = (w && w.wind_kt) || 0;
  const to = (((w && w.wind_dir) || 0) + 180) * (Math.PI / 180);
  return new THREE.Vector2(Math.sin(to) * kt * KT, Math.cos(to) * kt * KT);
}

function streaks(count, seed) {
  let s = seed;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const pos = new Float32Array(count * 2 * 3);
  const seedA = new Float32Array(count * 2 * 3);
  const end = new Float32Array(count * 2);
  for (let i = 0; i < count; i++) {
    const a = [rnd(), rnd(), rnd()];
    for (let k = 0; k < 2; k++) {
      seedA.set(a, (i * 2 + k) * 3);
      end[i * 2 + k] = k;
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("aSeed", new THREE.BufferAttribute(seedA, 3));
  g.setAttribute("aEnd", new THREE.BufferAttribute(end, 1));
  const m = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([
      THREE.UniformsLib.fog,
      {
        uCam: { value: new THREE.Vector3() },
        uTime: { value: 0 },
        uVel: { value: new THREE.Vector3(0, -7, 0) },
        uLen: { value: 0.05 },
        uAlpha: { value: 0.3 },
        uColor: { value: new THREE.Color("#aaa") },
        uCount: { value: 1 },
        uBox: { value: BOX },
      },
    ]),
    vertexShader: /* glsl */ `
      uniform vec3 uCam, uVel, uColor;
      uniform float uTime, uLen, uAlpha, uCount, uBox;
      attribute vec3 aSeed;
      attribute float aEnd;
      varying float vA;
      #include <common>
      #include <fog_pars_vertex>
      #include <logdepthbuf_pars_vertex>
      void main() {
        vec3 jitter = vec3(sin(aSeed.y * 40.0 + uTime * 0.7), 0.0, cos(aSeed.x * 40.0 + uTime * 0.6)) * 0.15;
        vec3 vel = uVel * (0.8 + 0.4 * aSeed.z);
        vec3 p = aSeed * uBox + vel * uTime + jitter;
        p = mod(p - uCam + uBox * 0.5, uBox) + uCam - uBox * 0.5;
        p -= vel * uLen * aEnd;
        vec4 mvPosition = viewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        float d = length(p - uCam);
        float keep = step(fract(aSeed.x * 91.7 + aSeed.y * 13.3), uCount);
        vA = uAlpha * keep * smoothstep(1.2, 4.0, d) * (1.0 - smoothstep(uBox * 0.3, uBox * 0.5, d)) * mix(1.0, 0.35, aEnd);
        #include <logdepthbuf_vertex>
        #include <fog_vertex>
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      varying float vA;
      #include <common>
      #include <fog_pars_fragment>
      #include <logdepthbuf_pars_fragment>
      void main() {
        #include <logdepthbuf_fragment>
        gl_FragColor = vec4(uColor, vA);
        #include <colorspace_fragment>
        #include <fog_fragment>
      }`,
    transparent: true,
    depthWrite: false,
    fog: true,
  });
  const lines = new THREE.LineSegments(g, m);
  lines.frustumCulled = false;
  lines.renderOrder = 8;
  return lines;
}

function puffTexture() {
  const cv = document.createElement("canvas");
  cv.width = cv.height = 256;
  const g = cv.getContext("2d");
  let s = 11;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  g.filter = "blur(10px)";
  for (let i = 0; i < 60; i++) {
    const r = 18 + rnd() * 50;
    const a = rnd() * Math.PI * 2;
    const d = rnd() * 70;
    const x = 128 + Math.cos(a) * d;
    const y = 128 + Math.sin(a) * d * 0.55;
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    gr.addColorStop(0, `rgba(255,255,255,${0.10 + rnd() * 0.12})`);
    gr.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = gr;
    g.fillRect(0, 0, 256, 256);
  }
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function cloudDeck() {
  const m = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([
      THREE.UniformsLib.fog,
      { uColor: { value: new THREE.Color("#777") }, uOpacity: { value: 0.95 }, uTime: { value: 0 }, uWind: { value: new THREE.Vector2() } },
    ]),
    vertexShader: /* glsl */ `
      varying vec2 vW;
      #include <common>
      #include <fog_pars_vertex>
      #include <logdepthbuf_pars_vertex>
      void main() {
        vec4 wp = modelMatrix * vec4(position, 1.0);
        vW = wp.xz;
        vec4 mvPosition = viewMatrix * wp;
        gl_Position = projectionMatrix * mvPosition;
        #include <logdepthbuf_vertex>
        #include <fog_vertex>
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      uniform float uOpacity, uTime;
      uniform vec2 uWind;
      varying vec2 vW;
      #include <common>
      #include <fog_pars_fragment>
      #include <logdepthbuf_pars_fragment>
      float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float noise(vec2 p) {
        vec2 i = floor(p), f = fract(p);
        vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
      }
      float fbm(vec2 p) {
        float v = 0.0, a = 0.5;
        for (int k = 0; k < 5; k++) { v += a * noise(p); p = p * 2.07 + vec2(11.0, 7.0); a *= 0.5; }
        return v;
      }
      void main() {
        #include <logdepthbuf_fragment>
        vec2 p = (vW - uWind * uTime) / 900.0;
        float n = fbm(p);
        float d = fbm(p * 3.3 + 4.0);
        vec3 c = uColor * (0.66 + 0.5 * n - 0.16 * d);
        float a = uOpacity * mix(0.82, 1.0, smoothstep(0.35, 0.7, n));
        gl_FragColor = vec4(c, a);
        #include <colorspace_fragment>
        #include <fog_fragment>
      }`,
    transparent: true,
    depthWrite: false,
    fog: true,
  });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(70000, 70000, 1, 1), m);
  mesh.rotation.x = Math.PI / 2;
  mesh.frustumCulled = false;
  mesh.renderOrder = -5;
  return mesh;
}

/** update(dt, t, weather, camera): weather is a METAR record or the moment's list; the deck is only seen from below. */
export function buildWeather(scene) {
  const group = new THREE.Group();
  group.name = "weather";
  scene.add(group);
  const rain = streaks(26000, 7);
  const drizzle = streaks(42000, 19);
  const snow = streaks(16000, 23);
  group.add(rain, drizzle, snow);
  const deck = cloudDeck();
  group.add(deck);
  const puffTex = puffTexture();
  const scud = [];
  let s = 5;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 26; i++) {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: puffTex, color: "#999", transparent: true, depthWrite: false, opacity: 0, fog: true }));
    sp.userData = { x: (rnd() - 0.5) * 6000, z: (rnd() - 0.5) * 6000, h: rnd(), w: 300 + rnd() * 500 };
    sp.renderOrder = -4;
    scud.push(sp);
    group.add(sp);
  }
  const flash = new THREE.DirectionalLight("#cfd8ff", 0);
  flash.position.set(400, 1200, -300);
  group.add(flash);
  let time = 0;
  let nextFlash = 4;
  let flashT = -1;
  const fogCol = new THREE.Color();

  function update(dt, t, weather, camera) {
    time += Math.min(dt, 0.1);
    const w = weatherAt(weather, t) || {};
    const p = precip(w);
    const wind = windOf(w);
    const cam = camera.position;
    fogCol.copy(scene.fog ? scene.fog.color : new THREE.Color("#888"));
    const lum = fogCol.r * 0.3 + fogCol.g * 0.55 + fogCol.b * 0.15;

    const set = (obj, on, { speed, len, alpha, count, color }) => {
      obj.visible = on > 0;
      if (!on) return;
      const u = obj.material.uniforms;
      u.uCam.value.copy(cam);
      u.uTime.value = time;
      u.uVel.value.set(wind.x * 0.9, -speed, -wind.y * 0.9);
      u.uLen.value = len;
      u.uAlpha.value = alpha;
      u.uCount.value = Math.min(1, count);
      u.uColor.value.copy(color);
    };
    const streakCol = fogCol.clone().multiplyScalar(1.7).addScalar(0.05 + 0.12 * (1 - Math.min(1, lum * 4)));
    set(rain, p.rain, { speed: 7.5, len: 0.055, alpha: 0.32, count: 0.35 + 0.5 * p.rain, color: streakCol });
    set(drizzle, p.drizzle, { speed: 2.2, len: 0.06, alpha: 0.22, count: 0.45 + 0.5 * p.drizzle, color: streakCol });
    set(snow, p.snow, { speed: 1.1, len: 0.25, alpha: 0.6, count: 0.4 + 0.5 * p.snow, color: new THREE.Color(0.85, 0.87, 0.9).multiplyScalar(0.25 + lum * 3) });

    const ceil = w.ceiling_ft;
    const deckY = ceil ? ceil * 0.3048 : null;
    const covered = (w.clouds || []).some(([c]) => c === "BKN" || c === "OVC" || c === "VV");
    deck.visible = !!deckY && covered && cam.y < deckY - 5;
    if (deck.visible) {
      deck.position.set(cam.x, deckY, cam.z);
      const u = deck.material.uniforms;
      u.uColor.value.copy(fogCol).multiplyScalar(0.95);
      u.uOpacity.value = (w.clouds || []).some(([c]) => c === "OVC" || c === "VV") ? 0.97 : 0.85;
      u.uTime.value = time;
      u.uWind.value.set(wind.x, -wind.y).multiplyScalar(3);
    }
    const scudOn = deckY && covered ? Math.min(1, 0.35 + p.mist + (deckY < 250 ? 0.4 : 0)) : 0;
    for (const sp of scud) {
      const d = sp.userData;
      sp.visible = scudOn > 0;
      if (!sp.visible) continue;
      d.x += wind.x * 1.4 * dt;
      d.z -= wind.y * 1.4 * dt;
      const rx = ((((d.x - cam.x) % 6000) + 9000) % 6000) - 3000;
      const rz = ((((d.z - cam.z) % 6000) + 9000) % 6000) - 3000;
      const top = deckY || 200;
      sp.position.set(cam.x + rx, top * (0.55 + 0.4 * d.h), cam.z + rz);
      sp.scale.set(d.w, d.w * 0.32, 1);
      sp.material.color.copy(fogCol).multiplyScalar(1.04);
      sp.material.opacity = 0.42 * scudOn;
    }

    flash.intensity = 0;
    if (p.storm) {
      if (flashT < 0 && time > nextFlash) {
        flashT = 0;
        nextFlash = time + 3 + Math.random() * 9;
      }
      if (flashT >= 0) {
        flashT += dt;
        const e = Math.exp(-flashT * 9) * (0.6 + 0.4 * Math.sin(flashT * 70));
        flash.intensity = 6 * e;
        if (flashT > 0.6) flashT = -1;
      }
    }
  }

  return { group, update };
}
