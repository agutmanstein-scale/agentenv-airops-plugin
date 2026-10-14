import * as THREE from "three";
import { poseAt } from "./motion.js";

const DEG = Math.PI / 180;
const N = 20;
const AGE = 2.6;
const MAX = 20;
const SPRAY = new Set(["roll", "rollout"]);

/** Spray thrown up behind the main gear on a wet or snowy runway: puffs laid along the last seconds of the track, so a frame depends on t alone. */
export function buildSpray(plan) {
  const n = MAX * N * 2;
  const quad = new THREE.PlaneGeometry(1, 1);
  const g = new THREE.InstancedBufferGeometry();
  g.index = quad.index;
  g.setAttribute("position", quad.getAttribute("position"));
  const aC = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3).setUsage(THREE.DynamicDrawUsage);
  const aS = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3).setUsage(THREE.DynamicDrawUsage);
  g.setAttribute("aC", aC);
  g.setAttribute("aS", aS);
  g.instanceCount = 0;
  const mat = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, { uColor: { value: new THREE.Color("#ccc") } }]),
    vertexShader: /* glsl */ `
      attribute vec3 aC, aS;
      varying vec2 vUv;
      varying float vA;
      varying float vSeed;
      uniform float fogDensity;
      #include <common>
      #include <logdepthbuf_pars_vertex>
      void main() {
        vec4 mv = viewMatrix * vec4(aC, 1.0);
        mv.xy += position.xy * aS.x;
        gl_Position = projectionMatrix * mv;
        vUv = position.xy;
        float d = -mv.z;
        vA = aS.y * exp(-fogDensity * fogDensity * d * d * 0.7) * smoothstep(2.0, 10.0, d);
        vSeed = aS.z;
        #include <logdepthbuf_vertex>
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      varying vec2 vUv;
      varying float vA;
      varying float vSeed;
      #include <common>
      #include <logdepthbuf_pars_fragment>
      float h2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float n2(vec2 p) {
        vec2 i = floor(p), f = fract(p);
        vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(mix(h2(i), h2(i + vec2(1, 0)), u.x), mix(h2(i + vec2(0, 1)), h2(i + vec2(1, 1)), u.x), u.y);
      }
      void main() {
        #include <logdepthbuf_fragment>
        float r = length(vUv) * 2.0;
        float puff = 0.6 * n2(vUv * 4.0 + vSeed * 17.0) + 0.4 * n2(vUv * 9.0 - vSeed * 9.0);
        float a = (1.0 - smoothstep(0.35, 1.0, r)) * (0.55 + 0.6 * puff) * vA * smoothstep(-0.5, -0.15, vUv.y);
        if (a < 0.004) discard;
        gl_FragColor = vec4(uColor * (0.92 + 0.16 * puff), a);
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthWrite: false,
    fog: true,
  });
  const mesh = new THREE.Mesh(g, mat);
  mesh.frustumCulled = false;
  mesh.renderOrder = 6;
  mesh.layers.set(1);
  return {
    mesh,
    update(t, live, s, camera) {
      const wet = Math.min(1, Math.max(0, (s.wet - 0.35) / 0.5));
      const snow = Math.min(1, s.snow * 1.4);
      const k = Math.max(wet, snow);
      let i = 0;
      if (k > 0.02) {
        mat.uniforms.uColor.value.copy(s.fogCol).multiplyScalar(1.22).lerp(new THREE.Color(0.93, 0.95, 0.98), snow * 0.9);
        const cam = camera.position;
        const list = live.filter((f) => SPRAY.has(f.pose.phase) && f.pose.speed > 15).sort((a, b) => dist2(a, cam) - dist2(b, cam)).slice(0, MAX);
        for (const f of list) {
          const tr = f.c.track;
          const d = f.dims;
          const half = d.span * 0.105;
          for (let j = 0; j < N; j++) {
            const tau = ((j + 0.5) / N) * AGE;
            const p = poseAt(tr, t - tau / 60);
            if (!SPRAY.has(p.phase) || p.speed < 12) continue;
            const h = p.heading * DEG;
            const fx = Math.sin(h);
            const fy = Math.cos(h);
            const v = Math.min(1, (p.speed - 12) / 55);
            for (const sg of [-1, 1]) {
              const seed = (j * 7 + (sg > 0 ? 3 : 0)) % 17;
              const spread = half + tau * (1.4 + 0.5 * Math.sin(seed));
              const x = p.x - fx * d.main + fy * sg * spread + fx * p.speed * tau * 0.18;
              const y = p.y - fy * d.main - fx * sg * spread + fy * p.speed * tau * 0.18;
              const size = 2 + tau * (3 + 4 * v);
              aC.setXYZ(i, x, Math.max(0.6 + tau * (1.2 + 1.6 * v), size * 0.45), -y);
              aS.setXYZ(i, size, k * v * (0.42 + 0.3 * snow) * (1 - tau / AGE) ** 1.5, seed / 17);
              i++;
            }
          }
        }
      }
      g.instanceCount = i;
      aC.needsUpdate = aS.needsUpdate = true;
    },
  };
}

const dist2 = (f, c) => (f.matrix.elements[12] - c.x) ** 2 + (f.matrix.elements[14] - c.z) ** 2;
