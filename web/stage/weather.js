import * as THREE from "three";

const BOX = 70;
const KT = 0.5144;
const DEG = Math.PI / 180;
const hash = (a, b = 0) => {
  const x = Math.sin(a * 127.1 + b * 311.7) * 43758.5453;
  return x - Math.floor(x);
};

function windOf(w) {
  const kt = (w && w.wind_kt) || 0;
  const to = (((w && w.wind_dir) || 0) + 180) * DEG;
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

function flakes(count, seed) {
  let s = seed;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const seeds = new Float32Array(count * 3);
  for (let i = 0; i < seeds.length; i++) seeds[i] = rnd();
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  g.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 3));
  const m = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([
      THREE.UniformsLib.fog,
      { uCam: { value: new THREE.Vector3() }, uTime: { value: 0 }, uWind: { value: new THREE.Vector3() }, uCount: { value: 1 }, uColor: { value: new THREE.Color("#eef") }, uPx: { value: 600 }, uBox: { value: 60 }, uAlpha: { value: 0.85 } },
    ]),
    vertexShader: /* glsl */ `
      uniform vec3 uCam, uWind;
      uniform float uTime, uCount, uPx, uBox, uAlpha;
      attribute vec3 aSeed;
      varying float vA;
      #include <common>
      #include <fog_pars_vertex>
      #include <logdepthbuf_pars_vertex>
      void main() {
        float fall = 0.8 + 0.6 * aSeed.z;
        vec3 sway = vec3(sin(uTime * (0.9 + aSeed.x) + aSeed.y * 30.0), 0.0, cos(uTime * (0.7 + aSeed.y) + aSeed.x * 30.0)) * 0.6;
        vec3 p = aSeed * uBox + vec3(uWind.x, -fall, uWind.z) * uTime + sway;
        p = mod(p - uCam + uBox * 0.5, uBox) + uCam - uBox * 0.5;
        vec4 mvPosition = viewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        float d = max(-mvPosition.z, 0.1);
        gl_PointSize = clamp(0.035 * uPx / d, 1.0, 9.0);
        float keep = step(fract(aSeed.x * 91.7 + aSeed.y * 13.3), uCount);
        vA = uAlpha * keep * smoothstep(0.8, 3.0, d) * (1.0 - smoothstep(uBox * 0.3, uBox * 0.5, length(p - uCam)));
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
        float r = length(gl_PointCoord - 0.5) * 2.0;
        float a = (1.0 - smoothstep(0.25, 1.0, r)) * vA;
        if (a < 0.01) discard;
        gl_FragColor = vec4(uColor, a);
        #include <colorspace_fragment>
        #include <fog_fragment>
      }`,
    transparent: true,
    depthWrite: false,
    fog: true,
  });
  const pts = new THREE.Points(g, m);
  pts.frustumCulled = false;
  pts.renderOrder = 8;
  return pts;
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
      { uColor: { value: new THREE.Color("#777") }, uOpacity: { value: 0.95 }, uGaps: { value: 0 }, uTime: { value: 0 }, uWind: { value: new THREE.Vector2() }, uFlash: { value: 0 }, uFlashAt: { value: new THREE.Vector2() } },
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
      uniform float uOpacity, uGaps, uTime, uFlash;
      uniform vec2 uWind, uFlashAt;
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
        c += vec3(0.75, 0.8, 1.0) * uFlash * exp(-length(vW - uFlashAt) / 2500.0) * (0.6 + 0.8 * n);
        float a = uOpacity * mix(0.82, 1.0, smoothstep(0.35, 0.7, n));
        a *= mix(1.0, smoothstep(0.36, 0.5, fbm(p * 0.55 + 9.0) + 0.12 * d), uGaps);
        a *= 1.0 - smoothstep(14000.0, 33000.0, length(vW - cameraPosition.xz));
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

/** Cumulonimbus and towering cumulus on the horizon where the METAR's remarks put them: billboards of stacked cauliflower lumps under an anvil, each lump lit by the sun from its own side, lit from inside by lightning. */
function stormTowers(max) {
  const quad = new THREE.PlaneGeometry(2, 1).translate(0, 0.5, 0);
  const g = new THREE.InstancedBufferGeometry();
  g.index = quad.index;
  g.setAttribute("position", quad.getAttribute("position"));
  const aBase = new THREE.InstancedBufferAttribute(new Float32Array(max * 3), 3).setUsage(THREE.DynamicDrawUsage);
  const aShape = new THREE.InstancedBufferAttribute(new Float32Array(max * 4), 4).setUsage(THREE.DynamicDrawUsage);
  g.setAttribute("aBase", aBase);
  g.setAttribute("aShape", aShape);
  g.instanceCount = 0;
  const m = new THREE.ShaderMaterial({
    uniforms: { uSun: { value: new THREE.Vector3() }, uSunCol: { value: new THREE.Color() }, uLit: { value: new THREE.Color() }, uShade: { value: new THREE.Color() }, uHaze: { value: new THREE.Color() }, uHazeK: { value: 0.5 }, uDay: { value: 1 } },
    vertexShader: /* glsl */ `
      attribute vec3 aBase;
      attribute vec4 aShape;
      varying vec2 vKm;
      varying vec4 vShape;
      varying vec3 vRight;
      varying vec3 vToCam;
      #include <common>
      #include <logdepthbuf_pars_vertex>
      void main() {
        vec3 toCam = cameraPosition - aBase;
        vec3 right = normalize(vec3(toCam.z, 0.0, -toCam.x));
        vec3 p = aBase + right * position.x * aShape.x * 0.5 + vec3(0.0, position.y * aShape.y, 0.0);
        gl_Position = projectionMatrix * viewMatrix * vec4(p, 1.0);
        vKm = vec2(position.x * aShape.x * 0.5, position.y * aShape.y) / 1000.0;
        vShape = aShape;
        vRight = right;
        vToCam = normalize(vec3(toCam.x, 0.0, toCam.z));
        #include <logdepthbuf_vertex>
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uSun, uSunCol, uLit, uShade, uHaze;
      uniform float uHazeK, uDay;
      varying vec2 vKm;
      varying vec4 vShape;
      varying vec3 vRight;
      varying vec3 vToCam;
      #include <common>
      #include <logdepthbuf_pars_fragment>
      float h2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float n2(vec2 p) {
        vec2 i = floor(p), f = fract(p);
        vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(mix(h2(i), h2(i + vec2(1, 0)), u.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), u.x), u.y);
      }
      float fbm(vec2 p) {
        float v = 0.0, a = 0.5;
        for (int k = 0; k < 4; k++) { v += a * n2(p); p = p * 2.1 + vec2(5.0, 9.0); a *= 0.5; }
        return v;
      }
      void main() {
        #include <logdepthbuf_fragment>
        float seed = vShape.z;
        bool cb = vShape.w > 0.0;
        float flash = abs(vShape.w) - 1.0;
        float W = vShape.x / 1000.0;
        float H = vShape.y / 1000.0;
        vec2 p = vKm;
        float lean = (seed - 0.5) * 0.35;
        float best = -1.0;
        vec3 n = vec3(0.0, 0.0, 1.0);
        float top = H * (cb ? 0.8 : 0.95);
        for (int i = 0; i < 16; i++) {
          float fi = float(i) / 15.0;
          float a = h2(vec2(seed * 31.0, float(i)));
          float b = h2(vec2(float(i) * 1.7, seed * 17.0));
          float y = mix(H * 0.16, top - W * 0.15, pow(fi, 0.8));
          vec2 c = vec2((a - 0.5) * W * 0.46 * (1.0 - 0.4 * fi) + lean * y, y);
          float r = W * (0.2 + 0.09 * b) * (1.0 - 0.3 * fi * (cb ? 1.0 : 1.4)) * (cb ? 1.0 : 0.75);
          vec2 d = (p - c) / r;
          float k = 1.0 - dot(d, d) + 0.35 * (fbm(p * 2.6 / max(r, 0.3) + float(i) * 3.1 + seed * 9.0) - 0.5);
          if (k > best) {
            best = k;
            n = normalize(vec3(d, sqrt(max(0.0, 1.0 - min(1.0, dot(d, d))))));
          }
        }
        float anvil = 0.0;
        if (cb) {
          vec2 ac = vec2(lean * H + W * 0.1, H * 0.855);
          vec2 ar = vec2(W * 0.49, H * 0.1);
          vec2 d = (p - ac) / ar;
          float fib = fbm(vec2(p.x * 1.4, p.y * 6.0) + seed * 5.0);
          anvil = 1.0 - pow(abs(d.x), 2.6) - pow(abs(d.y), 1.6) * (d.y > 0.0 ? 1.4 : 1.0) + 0.3 * (fib - 0.5);
          if (anvil > best) {
            best = anvil;
            n = normalize(vec3(d.x * 0.3, d.y > 0.0 ? 0.9 : -0.7, 0.6));
          }
        }
        float a = smoothstep(0.0, 0.06, best);
        float base = smoothstep(H * 0.1, H * 0.2, p.y);
        float shaft = (1.0 - base) * smoothstep(W * 0.3, 0.0, abs(p.x - lean * p.y)) * (0.45 + 0.35 * n2(vec2(p.x * 9.0, p.y * 1.5)));
        a = max(a * base, shaft * 0.55);
        a *= 0.12 + 0.88 * uDay + flash * 1.5;
        if (a < 0.01) discard;
        vec3 sun = normalize(vec3(dot(uSun, vRight), uSun.y, dot(uSun, vToCam)));
        float lam = max(dot(n, sun), 0.0);
        float rim = pow(1.0 - n.z, 3.0) * max(dot(vec2(n.x, n.y), normalize(sun.xy + 1e-4)), 0.0);
        float hgt = smoothstep(0.0, H, p.y);
        vec3 col = uShade * (0.75 + 0.35 * hgt) + uLit * (0.55 * lam + 0.25 * hgt) + uSunCol * (lam * 0.6 + rim * 0.5);
        col = mix(col, uShade * 0.6, (1.0 - base) * 0.8);
        vec2 fp = vec2(lean * H * 0.5 + W * 0.15 * sin(seed * 40.0), H * (0.45 + 0.2 * cos(seed * 23.0)));
        col += vec3(0.82, 0.86, 1.0) * flash * 3.0 * exp(-length((p - fp) / vec2(W * 0.45, H * 0.35)) * 1.6) * (0.6 + 0.5 * n.z);
        col = mix(col, uHaze, uHazeK * (1.0 - 0.5 * hgt) * (1.0 - flash * 0.6));
        gl_FragColor = vec4(col, a);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthWrite: false,
  });
  const mesh = new THREE.Mesh(g, m);
  mesh.frustumCulled = false;
  mesh.renderOrder = -6;
  return { mesh, aBase, aShape, g, m };
}

/** Rain, drizzle and snow around the camera, the cloud deck and its scud, storm towers on the horizon and lightning, all from the blended METAR in the state; flashes follow the wall clock. */
export function buildWeather(scene) {
  const group = new THREE.Group();
  group.name = "weather";
  scene.add(group);
  const rain = streaks(30000, 7);
  const drizzle = streaks(42000, 19);
  const snow = flakes(36000, 23);
  group.add(rain, drizzle, snow);
  const deck = cloudDeck();
  group.add(deck);
  const towers = stormTowers(24);
  group.add(towers.mesh);
  for (const o of [rain, drizzle, snow, towers.mesh]) o.layers.set(1);
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
  group.add(flash, flash.target);
  const fogCol = new THREE.Color();
  const tmp = new THREE.Color();
  let last = 0;

  function bolts(wall, w) {
    const out = [];
    const slot = 0.45;
    const k0 = Math.floor(wall / slot);
    const here = Math.max(w.ts || 0, (w.vcts || 0) * 0.6);
    const sources = (w.cells || []).map((c, i) => ({ c, i, rate: c.ltg ? (c.ltg > 1 ? 0.4 : 0.2) * (w.cellsK ?? 1) : 0 })).filter((x) => x.rate > 0);
    if (here > 0.05) sources.push({ c: null, i: 99, rate: 0.16 * here });
    for (const src of sources) {
      for (let k = k0 - 1; k <= k0; k++) {
        if (hash(k, src.i + 1) > src.rate * slot) continue;
        const tau = wall - (k * slot + hash(k, src.i + 7) * slot);
        if (tau < 0 || tau > 0.7) continue;
        const e = Math.exp(-tau * 7) * (0.55 + 0.45 * Math.sin(tau * 60 + src.i)) + (tau > 0.18 && tau < 0.28 ? 0.6 : 0);
        out.push({ src, e: Math.max(0, e), k });
      }
    }
    return out;
  }

  function update(dt, wall, t, st, camera) {
    const w = st.w || {};
    const wind = windOf(w);
    const cam = camera.position;
    fogCol.copy(st.fogCol || scene.fog.color);
    const lum = fogCol.r * 0.3 + fogCol.g * 0.55 + fogCol.b * 0.15;
    const set = (obj, on, { speed, len, alpha, count, color }) => {
      obj.visible = on > 0.01;
      if (!obj.visible) return;
      const u = obj.material.uniforms;
      u.uCam.value.copy(cam);
      u.uTime.value = wall;
      u.uVel.value.set(wind.x * 0.9, -speed, -wind.y * 0.9);
      u.uLen.value = len;
      u.uAlpha.value = alpha;
      u.uCount.value = Math.min(1, count);
      u.uColor.value.copy(color);
    };
    const streakCol = fogCol.clone().multiplyScalar(1.7).addScalar(0.05 + 0.12 * (1 - Math.min(1, lum * 4)));
    const heavy = Math.min(1.6, (w.rain || 0) * (1 + 0.6 * (w.ts || 0)));
    set(rain, heavy, { speed: 7.5 + heavy * 1.5, len: 0.055 + heavy * 0.02, alpha: 0.26 + 0.1 * heavy, count: 0.25 + 0.5 * heavy, color: streakCol });
    set(drizzle, w.drizzle || 0, { speed: 2.2, len: 0.06, alpha: 0.22, count: 0.45 + 0.5 * (w.drizzle || 0), color: streakCol });
    snow.visible = (w.snow || 0) > 0.01;
    if (snow.visible) {
      const u = snow.material.uniforms;
      u.uCam.value.copy(cam);
      u.uTime.value = wall;
      u.uWind.value.set(wind.x * 0.8, 0, -wind.y * 0.8);
      u.uCount.value = Math.min(1, 0.25 + 0.5 * w.snow);
      u.uColor.value.setRGB(0.9, 0.92, 0.96).multiplyScalar(0.35 + Math.min(1.2, lum * 2.6));
      u.uPx.value = camera.projectionMatrix.elements[5] * 540;
    }

    const ceil = w.ceiling_ft;
    const deckY = ceil ? ceil * 0.3048 : null;
    const covered = (w.clouds || []).some(([c]) => c === "BKN" || c === "OVC" || c === "VV");
    deck.visible = !!deckY && covered && cam.y < deckY - 5;
    if (deck.visible) {
      deck.position.set(cam.x, deckY, cam.z);
      const u = deck.material.uniforms;
      u.uColor.value.copy(fogCol).multiplyScalar(0.95);
      const ovc = (w.clouds || []).some(([c, ft]) => (c === "OVC" || c === "VV") && ft <= (ceil || 0) + 1500);
      u.uOpacity.value = ovc ? 0.97 : 0.92;
      u.uGaps.value = ovc ? 0 : 1;
      u.uTime.value = wall;
      u.uWind.value.set(wind.x, -wind.y).multiplyScalar(3);
    }
    const scudOn = deckY && covered ? Math.min(1, 0.35 + st.mist + (deckY < 250 ? 0.4 : 0)) : 0;
    const step = wall - last;
    last = wall;
    for (const sp of scud) {
      const d = sp.userData;
      sp.visible = scudOn > 0;
      if (!sp.visible) continue;
      d.x += wind.x * 1.4 * step;
      d.z -= wind.y * 1.4 * step;
      const rx = ((((d.x - cam.x) % 6000) + 9000) % 6000) - 3000;
      const rz = ((((d.z - cam.z) % 6000) + 9000) % 6000) - 3000;
      const top = deckY || 200;
      sp.position.set(cam.x + rx, top * (0.55 + 0.4 * d.h), cam.z + rz);
      sp.scale.set(d.w, d.w * 0.32, 1);
      sp.material.color.copy(fogCol).multiplyScalar(1.04);
      sp.material.opacity = 0.42 * scudOn;
    }

    const flashes = bolts(wall, w);
    const cells = w.cells || [];
    const atm = st.atm;
    let n = 0;
    for (const [ci, c] of cells.entries()) {
      const width = Math.min(170, c.b - c.a);
      const m = Math.max(1, Math.min(4, Math.round(width / 40)));
      for (let j = 0; j < m && n < 24; j++) {
        const seed = hash(ci * 13 + j, c.a);
        const brg = (c.a + (width * (j + 0.3 + 0.4 * seed)) / m) * DEG;
        const r = (c.far ? 40000 : 24000) + (c.far ? 14000 : 7000) * hash(j, ci + 3);
        const size = c.cb ? [9000 + 6000 * seed, 11500 + 3000 * hash(seed, 2)] : [4000 + 2500 * seed, 5000 + 2500 * hash(seed, 5)];
        const fl = flashes.filter((f) => f.src.c === c && (f.k + j) % m === 0).reduce((a, f) => Math.max(a, f.e), 0);
        towers.aBase.setXYZ(n, Math.sin(brg) * r, -1200, -Math.cos(brg) * r);
        towers.aShape.setXYZW(n, size[0], size[1], seed, (c.cb ? 1 : -1) * (1 + fl));
        n++;
      }
    }
    towers.g.instanceCount = Math.round(n * Math.min(1, (w.cellsK ?? 1) * 1.2));
    if (n) {
      const u = towers.m.uniforms;
      u.uSun.value.copy(st.sunDir);
      u.uSunCol.value.copy(atm.sunCol).multiplyScalar(Math.min(1, atm.sunI / 3 + 0.25 * atm.sunGlow) * 0.55);
      u.uLit.value.copy(atm.sky.horizon).lerp(tmp.setRGB(1, 1, 1), 0.7 * atm.day).multiplyScalar(0.3 + 0.75 * atm.day);
      u.uShade.value.copy(atm.sky.mid).lerp(atm.sky.horizon, 0.4).multiplyScalar(0.88 - 0.13 * atm.day);
      u.uHaze.value.copy(atm.sky.horizon);
      u.uDay.value = atm.day;
      u.uHazeK.value = Math.min(0.8, 0.2 + 0.3 * Math.exp(-((w.vis_sm ?? 10) / 10) * 1.2) + 0.15 * st.cover);
      towers.aBase.needsUpdate = towers.aShape.needsUpdate = true;
    }

    const local = flashes.filter((f) => f.src.c === null).reduce((a, f) => Math.max(a, f.e), 0);
    const far = flashes.filter((f) => f.src.c).reduce((a, f) => Math.max(a, f.e), 0);
    const src = flashes.find((f) => f.src.c)?.src.c;
    const brg = src ? ((src.a + src.b) / 2) * DEG : 0;
    flash.intensity = (w.ts > 0.3 ? 1.2 : 0.25) * local + 0.06 * far;
    flash.position.set(cam.x + (src ? Math.sin(brg) * 3000 : 300), 2500, cam.z + (src ? -Math.cos(brg) * 3000 : -200));
    flash.target.position.set(cam.x, 0, cam.z);
    flash.target.updateMatrixWorld();
    if (deck.visible) {
      deck.material.uniforms.uFlash.value = local * 1.5;
      deck.material.uniforms.uFlashAt.value.set(cam.x + 600, cam.z - 900);
    }
    return { local, far, dir: [src ? Math.sin(brg) : 0, src ? 0.25 : 1, src ? -Math.cos(brg) : 0] };
  }

  return { group, update };
}
