// The viewer's sound, made each frame from what is on screen. Outdoors: the airport's bed and the aircraft nearest the
// camera by phase (idle and taxi, pushback with its tug, APU and engine start, the takeoff roar with Doppler, approach,
// reverse thrust and spool-down), each with gain, air absorption and pan by its distance and bearing. In the terminal:
// its hall and crowd by the gate area's people, a soft chime now and then and the jets muffled by the glass. In the
// control-center room: its tone, the keys of the desk whose turn it is and clicks as requests fly. On the map: the
// airport faint and muffled. Rain, wind and thunder follow the stage's weather. A leveller and a sample-peak limiter on
// the audio thread hold it near -20 LUFS for a stream. Recordings are CC0 (audio/SOURCES.md); what has none is synthesized.
// const sound = new AirportSound().attach(window.airops); await sound.enable(); then sound.update(state, camera) per frame.

const FILES = {
  apron: "apron.ogg", idle: "idle.ogg", roar: "roar.ogg", far: "roar-far.ogg", approach: "approach.ogg", reverse: "reverse.ogg",
  tug: "tug.ogg", terminal: "terminal.ogg", crowd: "crowd.ogg", keys: "keys.ogg", click: "click.ogg", rain: "rain.ogg",
  wind: "wind.ogg", thunder: "thunder.ogg", rumble: "rumble.ogg",
};
const VOICES = 8;
const R0 = 60;
const HEARD = 0.004;
/** Each scene's bus gains, the low-pass on the outdoors heard from it, and whether the leveller holds its gain there (the map's bed is quiet on purpose). */
const MIX = {
  outdoor: { air: 1, hall: 0, room: 0, lp: 20000, hold: 0 },
  terminal: { air: 0.42, hall: 1, room: 0, lp: 650, hold: 0 },
  room: { air: 0, hall: 0, room: 1, lp: 400, hold: 0 },
  map: { air: 0.55, hall: 0, room: 0, lp: 420, hold: 1 },
};
/** Per aircraft class: pitch and loudness. */
const CLASS = { widebody: [0.86, 1.3], a321: [0.97, 1.08], narrowbody: [1, 1], a320: [1, 1], a220: [1.06, 0.92], e190: [1.08, 0.88],
  regional: [1.12, 0.8], regional_t: [1.3, 0.62] };
const LEVEL = { jets: 2.2, apron: 0.8, wind: 0.75, rain: 0.8, terminal: 1.1, crowd: 0.85, keys: 0.85, chime: 0.22, tone: 3.2, hvac: 3 };

const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const smooth = (a, b, x) => {
  const u = clamp((x - a) / (b - a), 0, 1);
  return u * u * (3 - 2 * u);
};
/** setTargetAtTime unless the value is already headed there, so a param's timeline stays short over a day of frames. */
const ramp = (p, v, now, tau) => {
  if (p._v !== undefined && Math.abs(p._v - v) <= 0.0015 + 0.01 * Math.abs(v)) return;
  p._v = v;
  p.setTargetAtTime(v, now, tau);
};

/** A looped buffer whose source runs only while it is heard: set(level, rate) each frame. */
class Loop {
  constructor(ctx, out, buf) {
    this.ctx = ctx;
    this.buf = buf;
    this.g = ctx.createGain();
    this.g.gain.value = 0;
    this.g.connect(out);
    this.src = null;
    this.quiet = 0;
  }

  set(level, rate = null, tau = 0.08) {
    const now = this.ctx.currentTime;
    if (level > 1e-4 && !this.src && this.buf) {
      const s = this.ctx.createBufferSource();
      s.buffer = this.buf;
      s.loop = true;
      s.playbackRate.value = rate ?? 1;
      s.connect(this.g);
      s.start(now, Math.random() * this.buf.duration);
      this.src = s;
    }
    if (!this.src) return;
    ramp(this.g.gain, level, now, tau);
    if (rate != null) ramp(this.src.playbackRate, rate, now, 0.12);
    if (level > 1e-4) this.quiet = 0;
    else if (!this.quiet) this.quiet = now;
    else if (now - this.quiet > 2.5) this.stop();
  }

  stop() {
    if (!this.src) return;
    this.src.stop();
    this.src.disconnect();
    this.src = null;
    this.quiet = 0;
  }
}

/** One aircraft's sound: its layers through a low-pass for air absorption, a gain for distance and a pan for bearing. */
class Voice {
  constructor(ctx, out, B) {
    this.ctx = ctx;
    this.lp = ctx.createBiquadFilter();
    this.lp.type = "lowpass";
    this.lp.Q.value = 0.55;
    this.g = ctx.createGain();
    this.g.gain.value = 0;
    this.pan = ctx.createStereoPanner();
    this.lp.connect(this.g).connect(this.pan).connect(out);
    this.L = Object.fromEntries(["idle", "roar", "far", "approach", "reverse", "tug", "apu"].map((k) => [k, new Loop(ctx, this.lp, B[k])]));
    this.key = null;
    this.free = 0;
    this.d = null;
    this.vr = 0;
  }

  play(c, now, tau, cut, dts) {
    if (this.key !== c.key || cut) {
      this.d = null;
      this.vr = 0;
    }
    this.key = c.key;
    if (this.d != null && dts > 0) this.vr += (clamp((this.d - c.d) / dts, -110, 110) - this.vr) * 0.35;
    else if (dts <= 0) this.vr *= 0.8;
    this.d = c.d;
    const dop = clamp(343 / (343 - this.vr), 0.72, 1.4);
    const s = c.spec, r = c.rate * dop;
    const far = smooth(260, 1600, c.d);
    ramp(this.g.gain, c.g, now, tau);
    ramp(this.lp.frequency, clamp(19000 * (150 / Math.max(150, c.de)) ** 0.65, 400, 19000), now, tau);
    ramp(this.pan.pan, c.pan, now, tau);
    this.L.idle.set(s.idle || 0, r * (s.idleRate || 1), tau);
    this.L.roar.set((s.roar || 0) * (1 - far), r * (s.roarRate || 1), tau);
    this.L.far.set((s.roar || 0) * far * 1.25 + (s.reverse || 0) * far * 0.6, r * (s.roarRate || 1), tau);
    this.L.approach.set(s.approach || 0, r, tau);
    this.L.reverse.set((s.reverse || 0) * (1 - 0.5 * far), r, tau);
    this.L.tug.set(s.tug || 0, 1, tau);
    this.L.apu.set(s.apu || 0, c.rate, tau);
  }

  release(now) {
    if (this.key == null) return;
    this.key = null;
    this.free = now + 0.8;
    ramp(this.g.gain, 0, now, 0.18);
    for (const l of Object.values(this.L)) l.set(0, null, 0.18);
  }

  tick() {
    if (this.key != null) return;
    for (const l of Object.values(this.L)) if (l.src) l.set(0, null, 0.18);
  }
}

/** The master: a slow leveller that holds the loudness of the last quarter-minute or so near `target` (and its gain
 * while `hold`), a compressor and a lookahead sample-peak limiter at `ceiling`, with BS.1770 meters (M, S, I) posted back
 * four times a second. */
const MASTER = `
const kw = (fs) => {
  let K = Math.tan(Math.PI * 1681.974450955533 / fs), Q = 0.7071752369554196, Vh = Math.pow(10, 3.999843853973347 / 20), Vb = Math.pow(Vh, 0.4996667741545416);
  let a0 = 1 + K / Q + K * K;
  const pre = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0];
  K = Math.tan(Math.PI * 38.13547087602444 / fs); Q = 0.5003270373238773; a0 = 1 + K / Q + K * K;
  return [pre, [1, -2, 1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]];
};
const db = (x) => (x > 1e-12 ? 10 * Math.log10(x) : -120);
const lufs = (e) => -0.691 + db(e);
class Master extends AudioWorkletProcessor {
  static get parameterDescriptors() {
    return [{ name: "target", defaultValue: -20, automationRate: "k-rate" }, { name: "ceiling", defaultValue: -3.5, automationRate: "k-rate" },
      { name: "hold", defaultValue: 0, automationRate: "k-rate" }];
  }
  constructor() {
    super();
    this.k = kw(sampleRate);
    this.z = [new Float64Array(4), new Float64Array(4), new Float64Array(4), new Float64Array(4)];
    this.blk = Math.round(sampleRate / 10);
    this.e = 0; this.n = 0;
    this.ring = new Float64Array(30); this.ri = 0; this.filled = 0;
    this.hist = new Float64Array(760); this.histN = new Uint32Array(760);
    this.agc = 0; this.env = 1e-6; this.cg = 1; this.lg = 1;
    this.d = [new Float32Array(128), new Float32Array(128)]; this.w = [new Float32Array(128), new Float32Array(128)]; this.dPeak = 0;
    this.peak = 0; this.minLg = 1; this.post = 0;
    this.att = Math.exp(-1 / (0.012 * sampleRate)); this.rel = Math.exp(-1 / (0.35 * sampleRate));
    this.gatt = Math.exp(-1 / (0.004 * sampleRate)); this.grel = Math.exp(-1 / (0.25 * sampleRate));
    this.port.onmessage = (m) => { if (m.data === "reset") { this.hist.fill(0); this.histN.fill(0); } };
  }
  biquad(c, z, x) {
    const y = c[0] * x + z[0] + 1e-20;
    z[0] = c[1] * x - c[3] * y + z[1];
    z[1] = c[2] * x - c[4] * y;
    return y;
  }
  block(target, hold) {
    this.ring[this.ri] = this.e / this.n; this.ri = (this.ri + 1) % 30; this.filled = Math.min(30, this.filled + 1);
    this.e = 0; this.n = 0;
    let m = 0, s = 0;
    for (let i = 0; i < this.filled; i++) { const v = this.ring[(this.ri - 1 - i + 30) % 30]; if (i < 4) m += v; s += v; }
    const M = lufs(m / Math.min(4, this.filled)), S = lufs(s / this.filled);
    if (M > -70) { const b = Math.min(759, Math.max(0, Math.round((M + 70) * 10))); this.hist[b] += Math.pow(10, (M + 0.691) / 10); this.histN[b]++; }
    this.M = M; this.S = S;
    this.lt = Math.max(1e-12, this.lt == null ? s / this.filled : this.lt + (s / this.filled - this.lt) * 0.007);
    const LT = lufs(this.lt);
    if (!hold && LT > target - 16 && this.filled >= 10) {
      const err = target - LT;
      this.agc += Math.max(-0.03, Math.min(0.015, err * 0.01));
      this.agc = Math.max(-9, Math.min(6, this.agc));
    }
  }
  integrated() {
    let e = 0, n = 0;
    for (let i = 0; i < 760; i++) { e += this.hist[i]; n += this.histN[i]; }
    if (!n) return -70;
    const gate = lufs(e / n) - 10;
    e = 0; n = 0;
    for (let i = Math.max(0, Math.ceil((gate + 70) * 10)); i < 760; i++) { e += this.hist[i]; n += this.histN[i]; }
    return n ? lufs(e / n) : -70;
  }
  process(inputs, outputs, P) {
    const out = outputs[0], inp = inputs[0], n = out[0].length;
    const L = inp && inp[0], R = inp && (inp[1] || inp[0]);
    const target = P.target[0], hold = P.hold[0] >= 0.5, ceil = Math.pow(10, P.ceiling[0] / 20);
    const g0 = Math.pow(10, this.agc / 20), thr = Math.pow(10, (target + 9) / 10);
    if (this.w[0].length !== n) this.w = [new Float32Array(n), new Float32Array(n)];
    const yl = this.w[0], yr = this.w[1];
    let pk = 0;
    for (let i = 0; i < n; i++) {
      const l = L ? L[i] * g0 : 0, r = R ? R[i] * g0 : 0;
      const ms = (l * l + r * r) / 2;
      this.env = Math.max(1e-12, ms > this.env ? this.att * this.env + (1 - this.att) * ms : this.rel * this.env + (1 - this.rel) * ms);
      const want = this.env > thr ? Math.pow(thr / this.env, 0.5 * (1 - 1 / 3)) : 1;
      this.cg = want < this.cg ? this.gatt * this.cg + (1 - this.gatt) * want : this.grel * this.cg + (1 - this.grel) * want;
      const a = l * this.cg, b = r * this.cg;
      yl[i] = a; yr[i] = b;
      pk = Math.max(pk, Math.abs(a), Math.abs(b));
      const kl = this.biquad(this.k[1], this.z[1], this.biquad(this.k[0], this.z[0], a));
      const kr = this.biquad(this.k[1], this.z[3], this.biquad(this.k[0], this.z[2], b));
      this.e += kl * kl + kr * kr; this.n++;
      if (this.n >= this.blk) this.block(target, hold);
    }
    const T = Math.min(1, ceil / Math.max(pk, this.dPeak, 1e-9));
    const end = T < this.lg ? T : this.lg + (T - this.lg) * (1 - Math.exp(-n / (0.15 * sampleRate)));
    const o0 = out[0], o1 = out[1] || out[0], d0 = this.d[0], d1 = this.d[1];
    for (let i = 0; i < n; i++) {
      const g = this.lg + (end - this.lg) * (i + 1) / n;
      o0[i] = Math.max(-ceil, Math.min(ceil, d0[i] * g));
      if (o1 !== o0) o1[i] = Math.max(-ceil, Math.min(ceil, d1[i] * g));
      this.peak = Math.max(this.peak, Math.abs(o0[i]), Math.abs(o1[i]));
    }
    this.minLg = Math.min(this.minLg, end);
    this.lg = end;
    this.w = this.d; this.d = [yl, yr]; this.dPeak = pk;
    if ((this.post += n) >= sampleRate / 4) {
      this.post = 0;
      this.port.postMessage({ M: this.M ?? -70, S: this.S ?? -70, I: this.integrated(), peak: 20 * Math.log10(Math.max(this.peak, 1e-6)),
        limit: 20 * Math.log10(this.minLg), agc: this.agc, comp: 20 * Math.log10(this.cg) });
      this.peak = 0; this.minLg = 1;
    }
    return true;
  }
}
registerProcessor("airops-master", Master);
`;

/** A seeded noise source for the synthesized buffers. */
function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296) * 2 - 1;
}

/** A stereo buffer of `sec` seconds filled by f(i, t, ch) with its last `fade` seconds crossfaded into its start, so it loops. */
function synth(ctx, sec, f, fade = 0.5) {
  const sr = ctx.sampleRate, n = Math.round(sec * sr), x = Math.round(fade * sr);
  const buf = ctx.createBuffer(2, n, sr);
  for (let ch = 0; ch < 2; ch++) {
    const tmp = new Float32Array(n + x);
    for (let i = 0; i < n + x; i++) tmp[i] = f(i, i / sr, ch);
    const d = buf.getChannelData(ch);
    for (let i = 0; i < n; i++) d[i] = tmp[i];
    for (let i = 0; i < x; i++) {
      const u = i / x;
      d[i] = tmp[i] * Math.sin((u * Math.PI) / 2) + tmp[n + i] * Math.cos((u * Math.PI) / 2);
    }
  }
  return buf;
}

/** Coloured noise: white through two one-pole low-passes at `lo` Hz and a one-pole high-pass at `hi` Hz, near `level` RMS. */
function noise(sr, seed, lo, hi, level) {
  const r = rng(seed), a = Math.exp((-2 * Math.PI * lo) / sr), b = Math.exp((-2 * Math.PI * hi) / sr);
  let y = 0, y2 = 0, h = 0, px = 0;
  const k = level * Math.sqrt((1 + a) / (1 - a)) * 2.7;
  return () => {
    y = a * y + (1 - a) * r();
    y2 = a * y2 + (1 - a) * y;
    h = b * (h + y2 - px);
    px = y2;
    return h * k;
  };
}

/** The sounds no recording covers, and a stand-in for any recording that fails to load. */
function synthesized(ctx) {
  const sr = ctx.sampleRate;
  const S = {};
  const apuN = [noise(sr, 7, 4500, 900, 0.025), noise(sr, 8, 4500, 900, 0.025)];
  const wob = (t, f) => 2 * Math.PI * f * t + 0.3 * Math.sin(2 * Math.PI * 0.5 * t);
  S.apu = synth(ctx, 6, (i, t, ch) => apuN[ch]() + 0.018 * Math.sin(wob(t, 1166)) + 0.007 * Math.sin(wob(t, 2332) + ch) + 0.004 * Math.sin(wob(t, 4310) + 2 * ch)
    + 0.012 * Math.sin(2 * Math.PI * 400 * t), 1);
  const toneN = [noise(sr, 11, 140, 25, 0.03), noise(sr, 12, 140, 25, 0.03)], hvacN = [noise(sr, 13, 2200, 260, 0.006), noise(sr, 14, 2200, 260, 0.006)];
  S.tone = synth(ctx, 9, (i, t, ch) => toneN[ch]() + 0.0025 * Math.sin(2 * Math.PI * 120 * t) + 0.0012 * Math.sin(2 * Math.PI * 180 * t), 1.5);
  S.hvac = synth(ctx, 11, (i, t, ch) => hvacN[ch]() * (1 + 0.15 * Math.sin(2 * Math.PI * 0.09 * t + ch)), 1.5);
  const hallN = [noise(sr, 15, 700, 60, 0.012), noise(sr, 16, 700, 60, 0.012)];
  S.hall = synth(ctx, 10, (i, t, ch) => hallN[ch](), 1.5);
  const jet = [noise(sr, 21, 1800, 40, 0.25), noise(sr, 22, 1800, 40, 0.25)];
  S.jet = synth(ctx, 6, (i, t, ch) => jet[ch]() * (1 + 0.2 * Math.sin(2 * Math.PI * 3.1 * t + ch)) + 0.02 * Math.sin(2 * Math.PI * 2650 * t), 1);
  const whine = [noise(sr, 23, 5000, 300, 0.06), noise(sr, 24, 5000, 300, 0.06)];
  S.whine = synth(ctx, 6, (i, t, ch) => whine[ch]() + 0.025 * Math.sin(2 * Math.PI * 3300 * t) + 0.015 * Math.sin(2 * Math.PI * 1650 * t + ch), 1);
  const air = [noise(sr, 25, 500, 30, 0.08), noise(sr, 26, 500, 30, 0.08)];
  S.air = synth(ctx, 8, (i, t, ch) => air[ch]() * (0.8 + 0.25 * Math.sin(2 * Math.PI * 0.13 * t + 2 * ch)), 1.5);
  const hiss = [noise(sr, 27, 12000, 900, 0.07), noise(sr, 28, 12000, 900, 0.07)];
  S.hiss = synth(ctx, 7, (i, t, ch) => hiss[ch](), 1);
  return S;
}

/** The terminal's chime: two soft bell tones, rendered once through a hall's reverb. */
async function chime(ctx) {
  const sr = ctx.sampleRate, sec = 5;
  const off = new OfflineAudioContext(2, sr * sec, sr);
  const ir = off.createBuffer(2, Math.round(sr * 2.6), sr);
  for (let ch = 0; ch < 2; ch++) {
    const r = rng(31 + ch), d = ir.getChannelData(ch);
    for (let i = 0; i < d.length; i++) d[i] = r() * Math.exp((-i / sr) * 2.6) * 0.5;
  }
  const verb = off.createConvolver();
  verb.buffer = ir;
  const wet = off.createGain();
  wet.gain.value = 0.35;
  verb.connect(wet).connect(off.destination);
  for (const [at, f] of [[0.1, 784], [0.75, 622.3]]) {
    for (const [h, a] of [[1, 0.5], [2.76, 0.12], [5.4, 0.04]]) {
      const o = off.createOscillator(), g = off.createGain();
      o.frequency.value = f * h;
      g.gain.setValueAtTime(0, at);
      g.gain.linearRampToValueAtTime(a, at + 0.012);
      g.gain.setTargetAtTime(0, at + 0.012, 0.55 / h);
      o.connect(g);
      g.connect(off.destination);
      g.connect(verb);
      o.start(at);
      o.stop(at + 3.5);
    }
  }
  return off.startRendering();
}

export class AirportSound {
  /** base: where the recordings are; volume: the output's level (0..1). */
  constructor({ base = new URL("./audio/", import.meta.url).href, volume = 1 } = {}) {
    this.base = base;
    this.volume = volume;
    this.viewer = null;
    this.ctx = null;
    this.on = false;
    this.ready = null;
    this.meter = null;
    this.scene = null;
    this.voices = [];
    this.shots = 0;
    this.last = 0;
    this.external = 0;
    this.lite = null;
    this.prev = { T: null, rig: null, view: null };
    this.weather = { flash: 0, far: 0, thunderAt: 0, gust: 0 };
    this.terminal = { chimeAt: 0 };
    this.room = { cards: null, turn: null, clickAt: 0, keyAt: 0 };
    this.listeners = new Set();
  }

  /** The viewer to listen to: window.airops (its stage, room and state()). */
  attach(viewer) {
    this.viewer = viewer;
    return this;
  }

  /** Start the sound (from a click where the browser needs one); resolves to whether it plays. */
  async enable() {
    this.on = true;
    if (!this.ctx) {
      const AC = globalThis.AudioContext || globalThis.webkitAudioContext;
      this.ctx = new AC({ latencyHint: "playback", sampleRate: 48000 });
      this.ready = this.#build().catch((e) => {
        console.warn("sound: the audio graph failed", e);
        throw e;
      });
      this.#notify();
    }
    await this.ctx.resume().catch(() => {});
    await this.ready;
    const now = this.ctx.currentTime;
    this.out.gain.cancelScheduledValues(now);
    this.out.gain.setTargetAtTime(this.volume, now, 0.5);
    this.out.gain._v = this.volume;
    this.#notify();
    return this.ctx.state === "running";
  }

  /** Fade out and let the context sleep. */
  async disable() {
    this.on = false;
    this.#notify();
    if (!this.ctx) return;
    await this.ready;
    const now = this.ctx.currentTime;
    this.out.gain.cancelScheduledValues(now);
    this.out.gain.setTargetAtTime(0, now, 0.12);
    this.out.gain._v = 0;
    clearTimeout(this.sleep);
    this.sleep = setTimeout(() => !this.on && this.ctx.suspend(), 900);
  }

  toggle() {
    return this.on ? this.disable() : this.enable();
  }

  /** The output's level (0..1), eased. */
  setVolume(v) {
    this.volume = clamp(v, 0, 1);
    if (this.out && this.on) ramp(this.out.gain, this.volume, this.ctx.currentTime, 0.4);
  }

  /** Called by the viewer each frame: state as window.airops.state() gives it (main, view; derived when absent) and the
   * camera on screen (derived when absent). */
  update(state = null, camera = null) {
    this.external = performance.now();
    this.#update(state, camera);
  }

  /** Drive update() from its own animation frames whenever the viewer has not called it lately. */
  auto() {
    if (this.raf) return this;
    const loop = () => {
      this.raf = requestAnimationFrame(loop);
      if (performance.now() - this.external > 300) this.#update(null, null);
    };
    this.raf = requestAnimationFrame(loop);
    return this;
  }

  /** What plays: the scene, the voices and their aircraft, the meters (LUFS, dBFS) and the live one-shots. */
  state() {
    return {
      on: this.on,
      context: this.ctx?.state ?? null,
      worklet: !!this.master?.port,
      scene: this.scene,
      voices: this.voices.filter((v) => v.key != null).map((v) => ({ key: v.key, gain: +v.g.gain.value.toFixed(4), phase: v.phase })),
      sources: this.voices.reduce((n, v) => n + Object.values(v.L).filter((l) => l.src).length, 0) + Object.values(this.beds ?? {}).filter((l) => l.src).length,
      shots: this.shots,
      meter: this.meter,
      time: this.ctx?.currentTime ?? 0,
    };
  }

  /** A speaker button for the timeline that turns the sound on and off. */
  button() {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "sound-toggle";
    const paint = () => {
      b.classList.toggle("on", this.on);
      b.setAttribute("aria-pressed", String(this.on));
      b.title = this.on ? "Sound on: the airport, the terminal and the room (click to mute)" : "Sound off (click for the airport's sound)";
      b.innerHTML = `<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M3 7.5h3l4-3.5v12l-4-3.5H3z" fill="currentColor"/>${this.on
        ? '<path d="M13 7a4 4 0 0 1 0 6M15.2 4.8a7 7 0 0 1 0 10.4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>'
        : '<path d="M13.2 7.6l4.8 4.8M18 7.6l-4.8 4.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>'}</svg>`;
      b.setAttribute("aria-label", this.on ? "Mute the sound" : "Turn the sound on");
    };
    b.addEventListener("click", () => this.toggle());
    this.listeners.add(paint);
    paint();
    return b;
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.raf = null;
    this.ctx?.close();
    this.ctx = null;
    this.on = false;
  }

  #notify() {
    for (const f of this.listeners) f();
  }

  async #build() {
    const ctx = this.ctx;
    const S = synthesized(ctx);
    const loaded = await Promise.all(Object.entries(FILES).map(async ([k, f]) => {
      try {
        const r = await fetch(new URL(f, this.base));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return [k, await ctx.decodeAudioData(await r.arrayBuffer())];
      } catch (e) {
        console.warn(`sound: ${f} did not load, synthesizing it`, e);
        return [k, null];
      }
    }));
    const B = Object.fromEntries(loaded);
    const stand = { apron: S.air, idle: S.whine, roar: S.jet, far: S.air, approach: S.whine, reverse: S.jet, tug: S.air, terminal: S.hall, crowd: S.hall, keys: null,
      click: null, rain: S.hiss, wind: S.air, thunder: null, rumble: null };
    for (const k of Object.keys(FILES)) B[k] ??= stand[k];
    B.apu = S.apu;
    B.chime = await chime(ctx);

    this.out = ctx.createGain();
    this.out.gain.value = 0;
    this.out.connect(ctx.destination);
    this.mix = ctx.createGain();
    const hp = ctx.createBiquadFilter(), shelf = ctx.createBiquadFilter(), air = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 55;
    hp.Q.value = 0.7;
    shelf.type = "lowshelf";
    shelf.frequency.value = 200;
    shelf.gain.value = -8;
    air.type = "highshelf";
    air.frequency.value = 3500;
    air.gain.value = 3;
    const eq = this.mix.connect(hp).connect(shelf).connect(air);
    try {
      const url = URL.createObjectURL(new Blob([MASTER], { type: "application/javascript" }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      this.master = new AudioWorkletNode(ctx, "airops-master", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2], channelCount: 2, channelCountMode: "explicit" });
      this.master.port.onmessage = (e) => (this.meter = e.data);
      eq.connect(this.master).connect(this.out);
    } catch (e) {
      console.warn("sound: no AudioWorklet (needs https, localhost or a secure-origin flag), using the built-in compressor and a clipper", e);
      this.master = null;
      const comp = ctx.createDynamicsCompressor(), lim = ctx.createDynamicsCompressor(), trim = ctx.createGain(), clip = ctx.createWaveShaper();
      const c = 10 ** (-2 / 20), knee = 0.6 * c;
      clip.curve = Float32Array.from({ length: 4097 }, (_, i) => {
        const x = (i / 2048) - 1, a = Math.abs(x);
        return Math.sign(x) * (a <= knee ? a : knee + (c - knee) * Math.tanh((a - knee) / (c - knee)));
      });
      comp.threshold.value = -20;
      comp.knee.value = 8;
      comp.ratio.value = 3;
      comp.attack.value = 0.01;
      comp.release.value = 0.3;
      lim.threshold.value = -3;
      lim.knee.value = 0;
      lim.ratio.value = 20;
      lim.attack.value = 0.002;
      lim.release.value = 0.12;
      trim.gain.value = 0.6;
      eq.connect(comp).connect(lim).connect(trim).connect(clip).connect(this.out);
    }
    const bus = (to = this.mix) => {
      const g = ctx.createGain();
      g.gain.value = 0;
      g.connect(to);
      return g;
    };
    this.airLp = ctx.createBiquadFilter();
    this.airLp.type = "lowpass";
    this.airLp.frequency.value = 20000;
    this.airLp.Q.value = 0.5;
    this.bus = { air: bus(), hall: bus(), room: bus() };
    this.airIn = ctx.createGain();
    this.airIn.connect(this.airLp).connect(this.bus.air);
    this.jets = ctx.createGain();
    this.jets.gain.value = LEVEL.jets;
    this.jets.connect(this.airIn);
    this.voices = Array.from({ length: VOICES + 2 }, () => new Voice(ctx, this.jets, B));
    const thunderBus = ctx.createGain();
    thunderBus.connect(this.airIn);
    this.thunderBus = thunderBus;
    this.hallBus = ctx.createGain();
    this.hallBus.connect(this.bus.hall);
    this.roomBus = ctx.createGain();
    this.roomBus.connect(this.bus.room);
    this.panes = {};
    for (const [k, p] of [["customer", -0.35], ["ops", 0], ["crew", 0.35], ["one", 0]]) {
      const s = ctx.createStereoPanner();
      s.pan.value = p;
      s.connect(this.roomBus);
      this.panes[k] = s;
    }
    this.beds = {
      apron: new Loop(ctx, this.airIn, B.apron),
      apron2: new Loop(ctx, this.airIn, B.idle),
      wind: new Loop(ctx, this.airIn, B.wind),
      rain: new Loop(ctx, this.airIn, B.rain),
      terminal: new Loop(ctx, this.hallBus, B.terminal),
      crowd: new Loop(ctx, this.hallBus, B.crowd),
      hall: new Loop(ctx, this.hallBus, S.hall),
      tone: new Loop(ctx, this.roomBus, S.tone),
      hvac: new Loop(ctx, this.roomBus, S.hvac),
    };
    this.keys = Object.fromEntries(["customer", "ops", "crew"].map((k) => [k, new Loop(ctx, this.panes[k], B.keys)]));
    this.B = B;
  }

  #update(state, camera) {
    if (!this.on || !this.ctx || !this.B || !this.viewer) return;
    const wall = performance.now();
    if (wall - this.last < 30) return;
    const dtw = Math.min(0.5, (wall - this.last) / 1000);
    this.last = wall;
    if (this.ctx.state !== "running") {
      if (wall - (this.resumed ?? 0) > 2000) {
        this.resumed = wall;
        this.ctx.resume().catch(() => {});
      }
      return;
    }
    const v = this.viewer;
    if (!state?.main) {
      if (!this.lite || wall - this.lite.at > 250) this.lite = { at: wall, s: v.state?.() ?? {} };
      state = { ...this.lite.s, ...(state ?? {}) };
    }
    const scene = state.main === "room" ? "room" : state.main === "map" || state.view === "network" || (!v.stage && state.main !== "room") ? "map"
      : state.view === "terminal" ? "terminal" : "outdoor";
    const stage = v.stage ?? null;
    camera ??= scene === "room" ? v.room?.camera : stage?.camera;
    const now = this.ctx.currentTime;
    const cutScene = scene !== this.scene;
    if (cutScene) this.#scene(scene, now);
    const rig = stage?.rig ?? null;
    const T = stage?.T ?? state.t ?? 0;
    const dts = this.prev.T == null ? 0 : (T - this.prev.T) * 60;
    const rate = stage?.rate ?? 0;
    const cut = cutScene || rig !== this.prev.rig || state.view !== this.prev.view || dts < 0 || dts > Math.max(8, Math.abs(rate) * dtw * 3 + 2);
    this.prev = { T, rig, view: state.view };
    const fast = smooth(20, 300, Math.abs(rate));
    const tau = cut ? 0.3 : 0.07 + 0.35 * fast;
    const outside = scene === "outdoor" || scene === "terminal";
    if (stage && camera && outside) this.#fleet(stage, camera, now, tau, cut, dts, scene, 1 - 0.4 * fast);
    else for (const vc of this.voices) vc.release(now);
    for (const vc of this.voices) vc.tick();
    if (stage && camera && scene !== "room") this.#air(stage, camera, now, scene, dtw);
    else for (const k of ["apron", "apron2", "wind", "rain"]) this.beds[k].src && this.beds[k].set(0, null, 0.4);
    if (stage && scene === "terminal") this.#terminal(stage, now, wall);
    else for (const k of ["terminal", "crowd", "hall"]) this.beds[k].src && this.beds[k].set(0, null, 0.4);
    if (scene === "room" && v.room) this.#room(v.room, now, wall);
    else {
      for (const k of ["tone", "hvac"]) this.beds[k].src && this.beds[k].set(0, null, 0.4);
      for (const l of Object.values(this.keys)) l.src && l.set(0, null, 0.4);
    }
  }

  #scene(scene, now) {
    const m = MIX[scene];
    const tau = this.scene ? 0.3 : 0.05;
    ramp(this.bus.air.gain, m.air, now, tau);
    ramp(this.bus.hall.gain, m.hall, now, tau);
    ramp(this.bus.room.gain, m.room, now, tau);
    ramp(this.airLp.frequency, m.lp, now, tau);
    if (this.master) this.master.parameters.get("hold").setValueAtTime(m.hold, now);
    this.scene = scene;
  }

  /** The aircraft heard from the camera: the loudest VOICES of them by phase and distance, each kept on its voice while
   * it stays near the top; softer and smoother when the clock runs many times real time. */
  #fleet(stage, camera, now, tau, cut, dts, scene, trim) {
    const live = stage.fleet?.live ?? [];
    const ts = stage.T * 60;
    const cp = camera.position, e = camera.matrixWorldInverse.elements;
    const zoom = clamp(((camera.fov || 50) / 50) ** 0.7, 0.35, 1.2);
    const wet = (scene === "terminal" ? 0.32 : 1) * trim;
    const cands = [];
    for (const f of live) {
      const spec = phaseOf(f, ts);
      if (!spec) continue;
      const m = f.matrix.elements, x = m[12], y = m[13], z = m[14];
      const d = Math.hypot(x - cp.x, y - cp.y, z - cp.z);
      const de = Math.max(R0, d * zoom);
      const g = spec.loud * (R0 / de) ** 0.78;
      if (g < HEARD) continue;
      const xc = e[0] * x + e[4] * y + e[8] * z + e[12], zc = e[2] * x + e[6] * y + e[10] * z + e[14];
      const k = CLASS[f.c?.track?.spec?.class] ?? [1, 1];
      cands.push({ key: f.key, spec, d, de, g: g * k[1] * wet, rate: k[0], pan: Math.sin(Math.atan2(xc, -zc)) * (0.85 - 0.35 * smooth(800, 4000, d)), phase: f.pose.phase });
    }
    cands.sort((a, b) => b.g - a.g);
    const near = new Map(cands.slice(0, VOICES + 2).map((c) => [c.key, c]));
    const placed = new Set();
    for (const vc of this.voices) {
      if (vc.key == null) continue;
      const c = near.get(vc.key);
      if (!c) vc.release(now);
      else {
        placed.add(c.key);
        vc.phase = c.phase;
        vc.play(c, now, tau, cut, dts);
      }
    }
    let busy = this.voices.filter((vc) => vc.key != null).length;
    for (const c of cands.slice(0, VOICES)) {
      if (placed.has(c.key) || busy >= VOICES) continue;
      const vc = this.voices.find((x) => x.key == null && x.free <= now) ?? this.voices.find((x) => x.key == null);
      if (!vc) break;
      vc.phase = c.phase;
      vc.play(c, now, cut ? tau : 0.15, true, dts);
      busy++;
    }
  }

  /** The bed, wind, rain and thunder outdoors, heard through the scene's filter indoors. */
  #air(stage, camera, now, scene, dtw) {
    const w = stage.env?.weatherAt?.(stage.T) ?? {};
    const cp = camera.position;
    const away = smooth(1500, 4500, Math.hypot(cp.x, cp.z));
    const B = this.beds;
    const tau = 0.6;
    B.apron.set(LEVEL.apron * (1 - 0.15 * away), 1, tau);
    B.apron2.set(LEVEL.apron * 0.18 * (1 - away), 0.97, tau);
    if (scene === "outdoor") ramp(this.airLp.frequency, 20000 - 16500 * away, now, 0.3);
    const kt = w.wind_kt ?? 0;
    this.weather.gust += (Math.random() - 0.5) * dtw * 0.8 - this.weather.gust * dtw * 0.25;
    const gust = clamp(1 + this.weather.gust, 0.6, 1.4);
    const wind = Math.max(smooth(6, 34, kt), 0.35 * away) * gust;
    B.wind.set(LEVEL.wind * wind * (scene === "outdoor" ? 1 : 0.6), 0.92 + 0.12 * wind, 0.5);
    const rain = clamp((w.rain ?? 0) * 0.75 + (w.drizzle ?? 0) * 0.3 + (w.ts ?? 0) * 0.25 + (w.snow ?? 0) * 0.08, 0, 1.2);
    B.rain.set(LEVEL.rain * rain, 1, 0.8);
    const lt = scene === "map" ? { local: 0, far: 0 } : stage.lightning ?? { local: 0, far: 0 };
    const wall = performance.now();
    const odds = Math.random() < 0.5;
    if (lt.local > 0.45 && this.weather.flash <= 0.45 && wall > this.weather.thunderAt && odds) this.#thunder(true, now, wall);
    else if (lt.far > 0.4 && this.weather.far <= 0.4 && wall > this.weather.thunderAt && odds) this.#thunder(false, now, wall);
    this.weather.flash = lt.local;
    this.weather.far = lt.far;
  }

  #thunder(near, now, wall) {
    const buf = near ? this.B.thunder : this.B.rumble;
    if (!buf || this.shots > 10) return;
    this.weather.thunderAt = wall + (near ? 14000 : 18000);
    const delay = near ? 0.25 + Math.random() * 1.2 : 2 + Math.random() * 5;
    const offs = near ? [0] : [0, 9.5, 19, 28.5];
    const off = offs[Math.floor(Math.random() * offs.length)];
    this.#shot(buf, this.thunderBus, { at: now + delay, offset: off, dur: near ? undefined : 9.5, gain: near ? 0.6 : 0.5 + 0.3 * Math.random(),
      rate: 0.9 + Math.random() * 0.15, lp: near ? 9000 : 1600, pan: (Math.random() - 0.5) * 0.8 });
  }

  /** The terminal: its hall's air and murmur, a crowd by the gate area's people and the line, and a chime now and then. */
  #terminal(stage, now, wall) {
    const g = stage.gate?.() ?? null;
    const people = (g?.people ?? 0) + (g?.line ?? 0);
    const busy = smooth(5, 140, people);
    const B = this.beds;
    B.hall.set(LEVEL.hvac * 0.8, 1, 0.5);
    B.terminal.set(LEVEL.terminal * (0.55 + 0.45 * busy), 1, 1);
    B.crowd.set(LEVEL.crowd * busy, 1, 1.2);
    if (!this.terminal.chimeAt) this.terminal.chimeAt = wall + 15000 + Math.random() * 30000;
    if (wall > this.terminal.chimeAt) {
      this.terminal.chimeAt = wall + 55000 + Math.random() * 80000;
      this.#shot(this.B.chime, this.hallBus, { at: now + 0.05, gain: LEVEL.chime, pan: (Math.random() - 0.5) * 0.3 });
    }
  }

  /** The control-center room: its tone and air, the keys of the desk whose turn it is (a little of the room's other desks always), and clicks as cards fly. */
  #room(room, now, wall) {
    const s = room.state?.() ?? {};
    const B = this.beds;
    B.tone.set(LEVEL.tone, 1, 0.5);
    B.hvac.set(LEVEL.hvac, 1, 0.5);
    const turn = s.turn ?? null, desk = turn === "one" ? "ops" : turn;
    const pane = this.panes[desk] ?? this.panes.ops;
    if (turn && turn !== this.room.turn) this.room.keyAt = wall;
    const typing = 0.4 + 0.6 * (0.5 + 0.5 * Math.sin((wall - this.room.keyAt) / 1900));
    for (const [k, l] of Object.entries(this.keys)) l.set(LEVEL.keys * (k === desk ? typing : 0.07), k === "crew" ? 1.04 : k === "customer" ? 0.96 : 1, 0.25);
    if (s.cards != null && this.room.cards != null && s.cards > this.room.cards) this.#clicks(1 + Math.floor(Math.random() * 3), pane, now);
    else if (turn && wall > this.room.clickAt) {
      this.room.clickAt = wall + 2500 + Math.random() * 5000;
      this.#clicks(1 + (Math.random() < 0.3), pane, now);
    }
    this.room.cards = s.cards ?? null;
    this.room.turn = turn;
  }

  #clicks(n, pane, now) {
    if (!this.B.click) return;
    let at = now + 0.03;
    for (let i = 0; i < n; i++) {
      this.#shot(this.B.click, pane, { at, gain: 0.25 + Math.random() * 0.2, rate: 0.9 + Math.random() * 0.25 });
      at += 0.09 + Math.random() * 0.25;
    }
  }

  /** A one-shot that disconnects itself when done; at most a dozen at once. */
  #shot(buf, out, { at, offset = 0, dur, gain = 1, rate = 1, lp = null, pan = 0 }) {
    if (!buf || this.shots >= 12) return;
    const ctx = this.ctx;
    const s = ctx.createBufferSource(), g = ctx.createGain(), p = ctx.createStereoPanner();
    s.buffer = buf;
    s.playbackRate.value = rate;
    g.gain.value = gain;
    p.pan.value = pan;
    let head = s;
    let f = null;
    if (lp) {
      f = ctx.createBiquadFilter();
      f.type = "lowpass";
      f.frequency.value = lp;
      s.connect(f);
      head = f;
    }
    head.connect(g).connect(p).connect(out);
    this.shots++;
    s.onended = () => {
      this.shots--;
      s.disconnect();
      f?.disconnect();
      g.disconnect();
      p.disconnect();
    };
    if (dur) {
      g.gain.setValueAtTime(gain, at + dur - 1.5);
      g.gain.linearRampToValueAtTime(0, at + dur);
      s.start(at, offset, dur);
    } else s.start(at, offset);
  }
}

/** The track segment in force at ts (seconds), as motion.js's poseAt finds it. */
function segAt(track, ts) {
  const segs = track.segs;
  let lo = 0, hi = segs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ts >= segs[mid].b) lo = mid + 1;
    else hi = mid;
  }
  return segs[lo];
}

/** What an aircraft sounds like at ts: its layers' levels (0..1), their pitches and its loudness at the source. */
function phaseOf(f, ts) {
  const p = f.pose, ph = p.phase, track = f.c?.track;
  const seg = track ? segAt(track, ts) : null;
  const tau = seg ? ts - seg.a : 0;
  const v = p.speed || 0;
  switch (ph) {
    case "parked":
      return { apu: 0.3, idle: 0.3, idleRate: 1.08, loud: 0.12 };
    case "pushback": {
      const u = seg && seg.b > seg.a ? clamp((ts - seg.a) / (seg.b - seg.a), 0, 1) : 0.5;
      const start = smooth(0.3, 1, u);
      return { apu: 0.45, tug: 0.75, idle: 0.85 * start, idleRate: 0.55 + 0.45 * start, loud: 0.5 };
    }
    case "taxi":
    case "taxi_in":
    case "lineup":
    case "hold":
      return { idle: 1, idleRate: 0.97 + 0.06 * Math.min(1, v / 12), loud: 0.5 + 0.12 * Math.min(1, v / 12) };
    case "roll":
    case "climb": {
      const k = smooth(0, 7, tau);
      return { idle: 1 - k, roar: 0.35 + 0.65 * k, roarRate: 0.84 + 0.16 * k, idleRate: 1 + 0.2 * k, loud: 3 * (ph === "climb" ? 1 - 0.25 * smooth(60, 120, tau) : 1) };
    }
    case "approach":
    case "flare":
      return { approach: 1, loud: 1.5 };
    case "rollout": {
      const rev = smooth(0.8, 2.5, tau) * (1 - smooth(11, 19, tau));
      return { reverse: rev, approach: 1 - smooth(0, 3, tau), idle: smooth(9, 17, tau), idleRate: 1.06 - 0.1 * smooth(14, 24, tau), loud: 0.7 + 1.5 * rev };
    }
    default:
      return null;
  }
}

/** The viewer's sound under its timeline: a speaker button (none on a stream), on from the start with `on`, driven by
 * its own frames until the viewer calls update(). Returns the AirportSound. */
export function mountSound(viewer, bar, { on = false, stream = false } = {}) {
  const sound = new AirportSound().attach(viewer).auto();
  viewer.sound = sound;
  if (bar && !stream) bar.append(sound.button());
  if (on) {
    sound.enable().catch(() => false).then((playing) => {
      if (playing) return;
      const go = () => {
        removeEventListener("pointerdown", go, true);
        removeEventListener("keydown", go, true);
        sound.enable();
      };
      addEventListener("pointerdown", go, true);
      addEventListener("keydown", go, true);
    });
  }
  return sound;
}
