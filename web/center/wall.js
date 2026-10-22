// The control center's video wall, display-only: tiled LCD panels with thin bezels across the front wall, a row of
// LED clocks above, and five windows drawn on canvases from the episode at minute t: JetBlue's network map with the
// flights in the air and the FAA's programs ringed, the radar (or the hub's weather from its METARs), the FAA programs
// at the hub and Boston, the hub's departures and the day's operations so far; on a day with passengers the wall is two
// panels wider, for a sixth window beside the operations: the passengers so far and the customer desk's requests to ops.
// A window redraws only when its content changes (the map a few times a sim minute).
import * as THREE from "three";
import { STATUS, boardAt, cities, flightsAt, positionsAt, programsAt, scoreAt, utcAt } from "../model.js";
import { PAX_INK, asksAt, flowsAt, paxAt, paxIndex, strandedPaxAt, waitingAt } from "../paxdata.js";
import { clock, metar } from "../plain.js";
import { radarFrames, rings } from "../map.js";

export const WALL = { cols: 14, rows: 7, pw: 1.2, ph: 0.675, y0: 0.9, z: 0.32 };
WALL.w = WALL.cols * WALL.pw;
WALL.h = WALL.rows * WALL.ph;
const PAX_COLS = 2;
const BOW = 0.26;
const PX = 240;
const FONT = "Inter, 'Helvetica Neue', Arial, sans-serif";
const MONO = "'SF Mono', Menlo, Consolas, monospace";
const TOPO = new URL("../vendor/topojson-client-3.1.0/index.js", import.meta.url).href;
const STATES = new URL("../vendor/us-atlas-3.0.1/states-10m.json", import.meta.url).href;
const LAND = new URL("../vendor/world-atlas-2.0.2/land-110m.json", import.meta.url).href;
const PLANE = new Path2D("M32 3c2.4 0 3.8 2.8 3.8 6.6V25l21.8 12.6v5.6L35.8 36v14.2l6.6 5.2V60L32 57l-10.4 3v-4.6l6.6-5.2V36L6.4 43.2v-5.6L28.2 25V9.6C28.2 5.8 29.6 3 32 3z");
const INK = Object.fromEntries(Object.entries(STATUS).map(([k, v]) => [k, `rgb(${v.rgb.join(",")})`]));
const CATEGORY = { VFR: "#3fbf6f", MVFR: "#4b8fe0", IFR: "#e0483b", LIFR: "#c64fd0" };
const ZONES = [["New York", "America/New_York"], ["Chicago", "America/Chicago"], ["Denver", "America/Denver"], ["Los Angeles", "America/Los_Angeles"],
  ["San Juan", "America/Puerto_Rico"], ["UTC", "UTC"]];
const HUBS = { JFK: "New York JFK", BOS: "Boston Logan" };

const rad = Math.PI / 180;

function albers(lon0 = -96, lat0 = 37.5, p1 = 29.5, p2 = 45.5) {
  const n = (Math.sin(p1 * rad) + Math.sin(p2 * rad)) / 2, C = Math.cos(p1 * rad) ** 2 + 2 * n * Math.sin(p1 * rad);
  const rho0 = Math.sqrt(C - 2 * n * Math.sin(lat0 * rad)) / n;
  return (lon, lat) => {
    const th = n * (lon - lon0) * rad, rho = Math.sqrt(C - 2 * n * Math.sin(lat * rad)) / n;
    return [rho * Math.sin(th), -(rho0 - rho * Math.cos(th))];
  };
}

const mercator = (lon, lat) => [lon * rad, -Math.log(Math.tan(Math.PI / 4 + (lat * rad) / 2))];

/** A projection fitted into a box [x, y, w, h] around lon/lat bounds [w, s, e, n]. */
function fitted(raw, [W, S, E, N], [bx, by, bw, bh]) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i <= 12; i++) {
    for (let j = 0; j <= 12; j++) {
      const [x, y] = raw(W + ((E - W) * i) / 12, S + ((N - S) * j) / 12);
      x0 = Math.min(x0, x), x1 = Math.max(x1, x), y0 = Math.min(y0, y), y1 = Math.max(y1, y);
    }
  }
  const k = Math.min(bw / (x1 - x0), bh / (y1 - y0)), ox = bx + (bw - (x1 - x0) * k) / 2, oy = by + (bh - (y1 - y0) * k) / 2;
  return (lon, lat) => {
    const [x, y] = raw(lon, lat);
    return [ox + (x - x0) * k, oy + (y - y0) * k];
  };
}

function rings2d(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") return geometry.coordinates;
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat();
  if (geometry.type === "MultiLineString") return geometry.coordinates;
  if (geometry.type === "LineString") return [geometry.coordinates];
  return [];
}

function trace(g, proj, lines, close) {
  g.beginPath();
  for (const line of lines) {
    line.forEach(([lon, lat], i) => {
      const [x, y] = proj(lon, lat);
      if (i) g.lineTo(x, y);
      else g.moveTo(x, y);
    });
    if (close) g.closePath();
  }
}

let GEOGRAPHY = null;
const geography = () => (GEOGRAPHY ??= coastlines());

async function coastlines() {
  const [topo, states, land] = await Promise.all([import(TOPO), fetch(STATES).then((r) => r.json()), fetch(LAND).then((r) => r.json())]);
  const near = (ring) => ring.every(([lon, lat]) => lon > -175 && lon < -30 && lat > 0 && lat < 80);
  return {
    land: rings2d(topo.feature(land, land.objects.land).geometry).filter(near),
    states: topo.feature(states, states.objects.states).features.flatMap((f) => rings2d(f.geometry)),
    borders: rings2d(topo.mesh(states, states.objects.states, (a, b) => a !== b)),
    nation: rings2d(topo.mesh(states, states.objects.nation)),
  };
}

function header(g, w, title, right, h = 44) {
  g.fillStyle = "#0f2133";
  g.fillRect(0, 0, w, h);
  g.fillStyle = "#2e6da4";
  g.fillRect(0, h - 3, w, 3);
  g.fillStyle = "#e8f0f7";
  g.font = `600 ${h * 0.55}px ${FONT}`;
  g.textBaseline = "middle";
  g.fillText(title, 16, h / 2);
  if (right) {
    g.textAlign = "right";
    g.fillStyle = "#9fb6cc";
    g.fillText(right, w - 16, h / 2);
    g.textAlign = "left";
  }
}

function pill(g, x, y, text, color, size = 26) {
  g.font = `700 ${size}px ${FONT}`;
  const w = g.measureText(text).width + size;
  g.fillStyle = color;
  g.beginPath();
  g.roundRect(x, y - size * 0.75, w, size * 1.5, size * 0.3);
  g.fill();
  g.fillStyle = "#0b1118";
  g.textBaseline = "middle";
  g.fillText(text, x + size / 2, y + 1);
  return w;
}

class Window {
  constructor(group, { col, row, cols, rows, draw }) {
    const w = cols * WALL.pw, h = rows * WALL.ph;
    this.canvas = document.createElement("canvas");
    this.canvas.width = Math.round(w * PX);
    this.canvas.height = Math.round(h * PX);
    this.g = this.canvas.getContext("2d");
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    this.tex.anisotropy = 8;
    this.draw = draw;
    this.key = null;
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: this.tex, toneMapped: false, color: 0xe6e6e6 }));
    mesh.position.set(-WALL.w / 2 + (col + cols / 2) * WALL.pw, WALL.y0 + WALL.h - (row + rows / 2) * WALL.ph, WALL.z);
    group.add(mesh);
    this.mesh = mesh;
  }

  update(key, ...args) {
    if (key === this.key) return false;
    this.key = key;
    this.draw(this.g, this.canvas.width, this.canvas.height, ...args);
    this.tex.needsUpdate = true;
    return true;
  }
}

export function buildWall(P, ep) {
  const group = new THREE.Group();
  const task = P.task, hub = task.hub, city = cities(task, null), occ = ep.occ, pax = paxIndex(P);
  const cols = WALL.cols + (pax ? PAX_COLS : 0), w = cols * WALL.pw, x = -WALL.w / 2 + w / 2;
  const dark = new THREE.MeshStandardMaterial({ color: 0x0b0c0e, roughness: 0.6 });
  group.add(new THREE.Mesh(new THREE.BoxGeometry(w + 0.5, WALL.h + 0.4, 0.3).translate(x, WALL.y0 + WALL.h / 2, WALL.z - 0.16), dark));
  group.add(new THREE.Mesh(new THREE.BoxGeometry(w + 0.5, WALL.y0 - 0.15, 0.7).translate(x, (WALL.y0 - 0.15) / 2, 0.35),
    new THREE.MeshStandardMaterial({ color: 0x15181c, roughness: 0.5 })));
  const lines = [];
  for (let c = 0; c <= cols; c++) lines.push([-WALL.w / 2 + c * WALL.pw, WALL.y0 + WALL.h / 2, 0.012, WALL.h]);
  for (let r = 0; r <= WALL.rows; r++) lines.push([x, WALL.y0 + r * WALL.ph, w, 0.012]);
  const bezels = new THREE.InstancedMesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ color: 0x050607 }), lines.length);
  lines.forEach(([x, y, w, h], k) => bezels.setMatrixAt(k, new THREE.Matrix4().makeScale(w, h, 1).setPosition(x, y, WALL.z + 0.002)));
  group.add(bezels);

  const radar = P.radar ? radarFrames(P.radar, P.base) : null;
  const images = new Map();
  const image = (url) => {
    if (!images.has(url)) {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.src = url;
      img.decode().then(() => (img.ok = true), () => {});
      images.set(url, img);
    }
    return images.get(url);
  };

  const metars = P.news.filter((n) => n.kind === "metar" && (n.speaker ?? "").startsWith(hub)).map((n) => ({ ...n, w: metar(n.text) }));
  const metarAt = (t) => metars.findLast((n) => n.at <= t) ?? null;
  const elements = [hub, ...["JFK", "BOS"].filter((c) => c !== hub && P.news.some((n) => n.kind === "advisory" && n.data?.element === c))];
  const geo = { data: null };
  let base = null;
  const ready = geography().then((d) => ((geo.data = d), (base = null)), (e) => console.warn("the video wall's map has no coastlines:", e));

  const mapW = 6 * WALL.pw * PX, mapH = 7 * WALL.ph * PX;
  const box = [16, 66, mapW - 32, mapH - 112];
  const proj = fitted(albers(), [-124.5, 17.4, -64.6, 49.2], box);
  const drawBase = () => {
    const c = document.createElement("canvas");
    c.width = mapW;
    c.height = mapH;
    const g = c.getContext("2d");
    g.fillStyle = "#06111c";
    g.fillRect(0, 0, mapW, mapH);
    if (geo.data) {
      g.fillStyle = "#132233";
      trace(g, proj, geo.data.land, true);
      g.fill();
      g.fillStyle = "#1a2f45";
      trace(g, proj, geo.data.states, true);
      g.fill("evenodd");
      g.strokeStyle = "rgba(140,175,210,0.38)";
      g.lineWidth = 1.4;
      trace(g, proj, geo.data.borders, false);
      g.stroke();
      g.strokeStyle = "rgba(160,195,230,0.65)";
      g.lineWidth = 2;
      trace(g, proj, geo.data.nation, false);
      g.stroke();
    }
    const busy = new Map();
    for (const s of P.ep.schedule) for (const c of [s.orig, s.dest]) busy.set(c, (busy.get(c) ?? 0) + 1);
    const labelled = [...busy].sort((a, b) => b[1] - a[1]).slice(0, 22).map(([c]) => c);
    for (const [code, a] of Object.entries(task.airports)) {
      const [x, y] = proj(a.lon, a.lat);
      if (x < 0 || x > mapW || y < 0 || y > mapH) continue;
      g.fillStyle = code === hub ? "#ffffff" : "#8fb3d1";
      g.beginPath();
      g.arc(x, y, code === hub ? 7 : 3.4, 0, Math.PI * 2);
      g.fill();
      if (labelled.includes(code) || code === hub) {
        g.font = `${code === hub ? 700 : 600} ${code === hub ? 26 : 19}px ${FONT}`;
        g.fillStyle = code === hub ? "#ffffff" : "rgba(170,198,222,0.85)";
        g.textBaseline = "middle";
        g.fillText(code, x + 9, y - 10);
      }
    }
    return c;
  };

  const windows = {
    map: new Window(group, { col: 4, row: 0, cols: 6, rows: 7, draw: (g, w, h, t, air, ringed) => {
      base ??= drawBase();
      g.drawImage(base, 0, 0);
      g.lineCap = "round";
      for (const p of air) {
        g.strokeStyle = "rgba(120,160,200,0.16)";
        g.lineWidth = 1.5;
        trace(g, proj, [p.f.s._path], false);
        g.stroke();
        g.strokeStyle = INK[p.f.cls] ?? INK.scheduled;
        g.globalAlpha = 0.55;
        g.lineWidth = 2.2;
        trace(g, proj, [p.trail], false);
        g.stroke();
        g.globalAlpha = 1;
      }
      for (const [code, r] of ringed) {
        const a = task.airports[code];
        if (!a) continue;
        const [x, y] = proj(a.lon, a.lat), col = INK[r.key];
        g.strokeStyle = col;
        g.lineWidth = 4;
        g.fillStyle = r.key === "cancelled" ? "rgba(255,106,92,0.18)" : "rgba(243,177,63,0.16)";
        g.beginPath();
        g.arc(x, y, code === hub ? 30 : 20, 0, Math.PI * 2);
        g.fill();
        g.stroke();
        g.font = `700 20px ${FONT}`;
        g.fillStyle = col;
        g.textBaseline = "middle";
        g.fillText(r.label, x + (code === hub ? 36 : 25), y + 14);
      }
      if (pax) drawPassengers(g, t);
      for (const p of air) {
        const [x, y] = proj(...p.pos), [x2, y2] = proj(p.pos[0] + 0.3 * Math.sin(p.heading * rad) / Math.cos(p.pos[1] * rad), p.pos[1] + 0.3 * Math.cos(p.heading * rad));
        g.save();
        g.translate(x, y);
        g.rotate(Math.atan2(y2 - y, x2 - x) + Math.PI / 2);
        g.scale(0.42, 0.42);
        g.translate(-32, -32);
        g.fillStyle = INK[p.f.cls] ?? INK.scheduled;
        g.fill(PLANE);
        g.restore();
      }
      header(g, w, `JETBLUE NETWORK · ${air.length} IN THE AIR`, `${clock(t)} ${zone(t)}`, 56);
      let x = 24;
      g.font = `600 20px ${FONT}`;
      g.textBaseline = "middle";
      for (const [k, label] of [["ontime", "on time"], ["delayed", "delayed"], ["held", "held"], ["cancelled", "ground stop"], ["delayed", "slowed by the FAA"],
        ...(pax ? [["pax", "passengers rebooked"], ["pax", "stranded overnight"]] : [])]) {
        g.fillStyle = INK[k] ?? PAX_INK;
        if (label === "stranded overnight") {
          g.beginPath();
          g.arc(x + 9, h - 26, 8, 0, Math.PI * 2);
          g.fill();
        } else if (label === "ground stop" || label === "slowed by the FAA") {
          g.strokeStyle = INK[k];
          g.lineWidth = 3;
          g.beginPath();
          g.arc(x + 9, h - 26, 9, 0, Math.PI * 2);
          g.stroke();
        } else g.fillRect(x, h - 30, 18, 8);
        g.fillStyle = "#b8c8d8";
        g.fillText(label, x + 26, h - 26);
        x += 44 + g.measureText(label).width;
      }
    } }),
    sky: new Window(group, { col: 0, row: 0, cols: 4, rows: 4, draw: radar ? drawRadar : drawWeather }),
    board: new Window(group, { col: 0, row: 4, cols: 4, rows: 3, draw: drawBoard }),
    faa: new Window(group, { col: pax ? 10 + PAX_COLS : 10, row: 0, cols: 4, rows: 3, draw: drawPrograms }),
    ops: new Window(group, { col: pax ? 10 + PAX_COLS : 10, row: 3, cols: 4, rows: 4, draw: drawOps }),
    pax: pax ? new Window(group, { col: 10, row: 0, cols: PAX_COLS, rows: 7, draw: drawPax }) : null,
  };

  const zoneFmt = new Intl.DateTimeFormat("en-US", { timeZone: task.tz, timeZoneName: "short" });
  const zone = (t) => zoneFmt.formatToParts(new Date(utcAt(task, t))).find((p) => p.type === "timeZoneName")?.value ?? "";

  const ledW = 12, ledH = 0.34;
  const led = document.createElement("canvas");
  led.width = 2400;
  led.height = 68;
  const ledTex = new THREE.CanvasTexture(led);
  ledTex.colorSpace = THREE.SRGBColorSpace;
  ledTex.anisotropy = 8;
  group.add(new THREE.Mesh(new THREE.BoxGeometry(ledW + 0.2, ledH + 0.1, 0.08).translate(0, WALL.y0 + WALL.h + 0.36, WALL.z - 0.05), dark));
  const ledMesh = new THREE.Mesh(new THREE.PlaneGeometry(ledW, ledH), new THREE.MeshBasicMaterial({ map: ledTex, toneMapped: false }));
  ledMesh.position.set(0, WALL.y0 + WALL.h + 0.36, WALL.z);
  group.add(ledMesh);
  const fmts = ZONES.map(([, tz]) => new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }));
  let ledKey = null;
  const drawLed = (t) => {
    const m = Math.floor(t);
    if (m === ledKey) return;
    ledKey = m;
    const g = led.getContext("2d"), cw = led.width / ZONES.length, when = new Date(utcAt(task, m));
    g.fillStyle = "#050505";
    g.fillRect(0, 0, led.width, led.height);
    ZONES.forEach(([name], i) => {
      g.textBaseline = "middle";
      g.font = `600 22px ${FONT}`;
      g.fillStyle = "#8c7a63";
      g.textAlign = "right";
      g.fillText(name.toUpperCase(), i * cw + cw * 0.48, 35);
      g.textAlign = "left";
      g.font = `700 46px ${MONO}`;
      g.fillStyle = "#ffb347";
      g.fillText(fmts[i].format(when), i * cw + cw * 0.53, 36);
    });
    ledTex.needsUpdate = true;
  };

  function drawRadar(g, w, h, t) {
    g.fillStyle = "#06111c";
    g.fillRect(0, 0, w, h);
    const rp = fitted(mercator, radar.bounds, [0, 44, w, h - 44]);
    if (geo.data) {
      g.fillStyle = "#14263a";
      trace(g, rp, geo.data.land, true);
      g.fill();
      g.strokeStyle = "rgba(150,185,215,0.4)";
      g.lineWidth = 1;
      trace(g, rp, geo.data.borders, false);
      g.stroke();
      g.strokeStyle = "rgba(170,200,230,0.6)";
      trace(g, rp, geo.data.nation, false);
      g.stroke();
    }
    const fr = radar.at(t);
    if (fr) {
      const [W, S, E, N] = radar.bounds, [x0, y0] = rp(W, N), [x1, y1] = rp(E, S);
      for (const [f, a] of [[fr.a, 1 - fr.f], [fr.b, fr.f]]) {
        const img = image(f.url);
        if (!img.ok || a <= 0.01) continue;
        g.globalAlpha = 0.85 * a;
        g.drawImage(img, x0, y0, x1 - x0, y1 - y0);
      }
      g.globalAlpha = 1;
    }
    const a = task.airports[hub], [x, y] = rp(a.lon, a.lat);
    g.strokeStyle = "#fff";
    g.lineWidth = 3;
    g.beginPath();
    g.arc(x, y, 9, 0, Math.PI * 2);
    g.stroke();
    g.font = `700 24px ${FONT}`;
    g.fillStyle = "#fff";
    g.fillText(hub, x - 60, y - 4);
    const n = metarAt(t);
    if (n) {
      g.fillStyle = "rgba(6,17,28,0.82)";
      g.fillRect(0, h - 50, w, 50);
      g.font = `600 23px ${FONT}`;
      g.fillStyle = "#dfe8f0";
      g.textBaseline = "middle";
      g.fillText(`${hub} ${clock(n.at)}  ${n.w.plain}`.slice(0, 92), 16, h - 25);
    }
    header(g, w, "NEXRAD RADAR", fr ? `${clock(fr.f < 0.5 ? fr.a.t : fr.b.t)} ${zone(t)}` : "no frame", 44);
  }

  function drawWeather(g, w, h, t) {
    g.fillStyle = "#081420";
    g.fillRect(0, 0, w, h);
    const n = metarAt(t);
    header(g, w, `${HUBS[hub] ?? hub} WEATHER`.toUpperCase(), n ? `METAR ${clock(n.at)}` : "", 44);
    if (!n) return;
    const [lead, ...rest] = n.w.plain.split(" · ");
    g.textBaseline = "alphabetic";
    g.font = `700 52px ${FONT}`;
    g.fillStyle = "#f2f6fa";
    g.fillText(lead, 24, 118);
    const cat = n.data?.category;
    if (cat) pill(g, w - 170, 100, cat, CATEGORY[cat] ?? "#999", 30);
    g.font = `500 32px ${FONT}`;
    g.fillStyle = "#b9c9d8";
    rest.forEach((line, i) => g.fillText(line[0].toUpperCase() + line.slice(1), 24, 172 + i * 44));
    if (n.w.temp_c != null) {
      g.font = `600 64px ${FONT}`;
      g.fillStyle = "#f2f6fa";
      g.textAlign = "right";
      g.fillText(`${Math.round(n.w.temp_c * 1.8 + 32)}°F`, w - 28, 200);
      g.textAlign = "left";
    }
    const past = metars.filter((m) => m.at <= t).slice(-12), bw = (w - 48) / 12;
    past.forEach((m, i) => {
      g.fillStyle = CATEGORY[m.data?.category] ?? "#555";
      g.fillRect(24 + i * bw, h - 92, bw - 4, 16);
      if (i % 3 === 0) {
        g.font = `500 18px ${FONT}`;
        g.fillStyle = "#7f93a6";
        g.fillText(clock(m.at), 24 + i * bw, h - 56);
      }
    });
    g.font = `400 17px ${MONO}`;
    g.fillStyle = "#6f8396";
    g.fillText(n.text.split(" RMK")[0].slice(0, 96), 24, h - 22);
  }

  function drawBoard(g, w, h, t, flights) {
    g.fillStyle = "#081420";
    g.fillRect(0, 0, w, h);
    header(g, w, `DEPARTURES · ${(HUBS[hub] ?? hub).toUpperCase()}`, clock(t), 44);
    const rows = boardAt(P, t, flights, { rows: 8 }).dep, cols = [24, 150, 330, 690];
    g.font = `600 19px ${FONT}`;
    g.fillStyle = "#6f8aa3";
    g.textBaseline = "middle";
    ["SCHED", "FLIGHT", "TO", "STATUS"].forEach((s, i) => g.fillText(s, cols[i], 68));
    const tone = { ok: "#5fd08f", delay: "#f3b13f", cancel: "#ff6a5c", hold: "#5aa8ff" };
    rows.forEach((r, i) => {
      const y = 104 + i * 46;
      g.fillStyle = i % 2 ? "#0b1a29" : "#0e2032";
      g.fillRect(12, y - 21, w - 24, 42);
      g.font = `600 26px ${MONO}`;
      g.fillStyle = "#e9eef3";
      g.fillText(clock(r.sched), cols[0], y);
      g.fillText(r.flight, cols[1], y);
      g.font = `500 26px ${FONT}`;
      g.fillText((city.get(r.other) ?? r.other).slice(0, 20), cols[2], y);
      g.fillStyle = tone[r.tone] ?? "#e9eef3";
      g.font = `600 25px ${FONT}`;
      g.fillText(r.status, cols[3], y);
    });
    if (!rows.length) {
      g.font = `500 28px ${FONT}`;
      g.fillStyle = "#7f93a6";
      g.fillText("No departures in the next hours", 24, 120);
    }
  }

  function drawPrograms(g, w, h, t) {
    g.fillStyle = "#081420";
    g.fillRect(0, 0, w, h);
    header(g, w, "FAA PROGRAMS", `ATCSCC · ${clock(t)}`, 44);
    const rowH = (h - 54) / elements.length;
    elements.forEach((code, i) => {
      const y = 54 + i * rowH, now = programsAt({ news: P.news, task: { hub: code } }, t);
      g.fillStyle = i % 2 ? "#0b1a29" : "#0d1d2e";
      g.fillRect(10, y + 4, w - 20, rowH - 8);
      g.textBaseline = "alphabetic";
      g.font = `700 34px ${FONT}`;
      g.fillStyle = "#f0f4f8";
      g.fillText(code, 28, y + 50);
      g.font = `500 22px ${FONT}`;
      g.fillStyle = "#8ea4b8";
      g.fillText(HUBS[code] ?? code, 28, y + 80);
      const x = 260;
      if (now.gs) {
        const [, b] = now.gs.data.gs_period, d = String(now.gs.data.gs_delays ?? "").split("/").map((s) => s.trim());
        pill(g, x, y + 40, "GROUND STOP", INK.cancelled, 26);
        g.font = `500 26px ${FONT}`;
        g.fillStyle = "#e4ebf1";
        g.textBaseline = "middle";
        g.fillText(`No arrivals until ${clock(b)}${d[2] ? ` · average delay ${d[2]} min` : ""}`, x, y + 92);
      } else if (now.gdp) {
        const d = now.gdp.data, [a, b] = d.program_period, rate = d.program_rate?.[Math.floor((t - a) / 60)] ?? d.program_rate?.[0];
        pill(g, x, y + 40, "GROUND DELAY PROGRAM", INK.delayed, 26);
        g.font = `500 26px ${FONT}`;
        g.fillStyle = "#e4ebf1";
        g.textBaseline = "middle";
        g.fillText(`${clock(a)}–${clock(b)}${rate ? ` · ${rate} arrivals/h` : ""}${d.avg_delay ? ` · average delay ${d.avg_delay} min` : ""}`, x, y + 92);
      } else {
        pill(g, x, y + 40, "NO PROGRAM", "#3f6b55", 26);
        g.font = `500 26px ${FONT}`;
        g.fillStyle = "#a8b8c6";
        g.textBaseline = "middle";
        g.fillText("Arrivals at the normal rate", x, y + 92);
      }
      const last = P.news.findLast((n) => n.kind === "advisory" && n.data?.element === code && n.at <= t);
      if (last && rowH > 150) {
        g.font = `400 20px ${FONT}`;
        g.fillStyle = "#6f8396";
        g.fillText(`Latest advisory ${clock(last.at)} · ${String(last.data.title ?? "").toLowerCase()}${last.data.condition ? ` · ${last.data.condition.toLowerCase()}` : ""}`.slice(0, 84), x, y + 134);
      }
    });
  }

  function drawOps(g, w, h, t, air) {
    g.fillStyle = "#081420";
    g.fillRect(0, 0, w, h);
    header(g, w, "OPERATIONS TODAY", clock(t), 44);
    const s = scoreAt(P, t), made = occ.decisions.filter((c) => (c.shown ?? c.t) <= t);
    const sent = occ.requests.filter((r) => (r.shown ?? r.sent) <= t);
    const waiting = sent.filter((r) => r.type !== "TEXT" && (r.answered ?? r.replied ?? Infinity) > t).length;
    const tiles = [
      [String(air), "JetBlue flights in the air", "#5fd08f"],
      [String(s.cancelled), "flights cancelled", "#ff6a5c"],
      [s.delayedPax.toLocaleString("en-US"), "passengers 15+ min late", "#f3b13f"],
      [String(made.filter((c) => c.action === "hold" || c.action === "claim").length), "holds and slots taken", "#5aa8ff"],
      [String(made.filter((c) => c.desk !== "ops" && !["cancel", "cancel_pair", "rebook", "hotel"].includes(c.action)).length), "crew moves", "#b08cf0"],
      occ.mode === "one" ? [String(made.length), "decisions by the desk", "#9fb6cc"] : [`${sent.length}${waiting ? ` · ${waiting}` : ""}`, waiting ? "requests · waiting" : "requests between desks", "#9fb6cc"],
    ];
    const tw = (w - 40) / 3, th = (h - 64) / 2;
    tiles.forEach(([big, label, col], i) => {
      const x = 14 + (i % 3) * (tw + 6), y = 54 + Math.floor(i / 3) * (th + 4);
      g.fillStyle = "#0d1d2e";
      g.fillRect(x, y, tw, th);
      g.fillStyle = col;
      g.fillRect(x, y, 6, th);
      g.textBaseline = "alphabetic";
      g.font = `700 64px ${FONT}`;
      g.fillStyle = "#f3f6f9";
      g.fillText(big, x + 22, y + th * 0.56);
      g.font = `500 22px ${FONT}`;
      g.fillStyle = "#94a9bd";
      g.fillText(label, x + 22, y + th * 0.84);
    });
  }

  /** Where the passengers off a cancelled flight or a missed connection were rebooked, a sand arc as wide as how many
   * from their airport to the new flight's destination, and a sand disc with the count where they are stranded overnight. */
  function drawPassengers(g, t) {
    const ap = task.airports;
    g.lineCap = "round";
    for (const f of flowsAt(pax, P, t).sort((a, b) => a.pax - b.pax)) {
      if (!ap[f.at] || !ap[f.dest]) continue;
      const [x0, y0] = proj(ap[f.at].lon, ap[f.at].lat), [x1, y1] = proj(ap[f.dest].lon, ap[f.dest].lat), dx = x1 - x0, dy = y1 - y0;
      const cx = (x0 + x1) / 2 + dy * BOW, cy = (y0 + y1) / 2 - dx * BOW, wide = 1.5 + 1.25 * Math.sqrt(f.pax);
      g.globalAlpha = 0.85 * f.alpha;
      for (const [col, lw] of [["rgba(4,10,16,0.7)", wide + 4], [PAX_INK, wide]]) {
        g.strokeStyle = col;
        g.lineWidth = lw;
        g.beginPath();
        g.moveTo(x0, y0);
        g.quadraticCurveTo(cx, cy, x1, y1);
        g.stroke();
      }
    }
    g.globalAlpha = 1;
    g.font = `700 22px ${FONT}`;
    g.textBaseline = "middle";
    for (const [code, n] of strandedPaxAt(pax, t)) {
      if (!ap[code]) continue;
      const [x, y] = proj(ap[code].lon, ap[code].lat), r = 6 + 1.4 * Math.sqrt(n);
      g.fillStyle = PAX_INK;
      g.strokeStyle = "#5a3c06";
      g.lineWidth = 2;
      g.beginPath();
      g.arc(x, y, r, 0, Math.PI * 2);
      g.fill();
      g.stroke();
      g.fillStyle = PAX_INK;
      g.fillText(n.toLocaleString("en-US"), x - r - 8 - g.measureText(n.toLocaleString("en-US")).width, y + 16);
    }
  }

  function drawPax(g, w, h, t) {
    g.fillStyle = "#081420";
    g.fillRect(0, 0, w, h);
    header(g, w, "PASSENGERS", clock(t), 44);
    const a = paxAt(pax, t), asks = asksAt(pax, t), waiting = waitingAt(pax, P, t).reduce((n, x) => n + x.pax, 0);
    const where = [...strandedPaxAt(pax, t)].sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]));
    const n = (v) => v.toLocaleString("en-US");
    const tiles = [
      [n(a.missed), "missed connections", a.held ? `${a.held} departure${a.held > 1 ? "s" : ""} held for ${n(a.heldPax)} connecting` : "no departure held for them yet", PAX_INK],
      [n(a.rebooked), "rebooked onto a later flight", waiting ? `${n(waiting)} waiting for it now` : "none waiting now", PAX_INK],
      [n(a.stranded), "stranded overnight", where.length ? where.slice(0, 3).map(([c, k]) => `${c} ${n(k)}`).join(" · ") : "nobody yet", PAX_INK],
      [n(asks.open), asks.open === 1 ? "request to ops open" : "requests to ops open", `${asks.sent} sent · ${asks.accepted} accepted`, "#9fb6cc"],
    ];
    const th = (h - 60) / tiles.length - 6;
    tiles.forEach(([big, label, sub, col], i) => {
      const y = 54 + i * (th + 6);
      g.fillStyle = "#0d1d2e";
      g.fillRect(12, y, w - 24, th);
      g.fillStyle = col;
      g.fillRect(12, y, 6, th);
      g.textBaseline = "alphabetic";
      g.font = `700 76px ${FONT}`;
      g.fillStyle = "#f3f6f9";
      g.fillText(big, 36, y + th * 0.47);
      g.font = `600 27px ${FONT}`;
      g.fillStyle = "#c9d6e2";
      g.fillText(label, 36, y + th * 0.68);
      g.font = `500 22px ${FONT}`;
      g.fillStyle = "#8ea4b8";
      g.fillText(sub, 36, y + th * 0.86, w - 60);
    });
  }

  let lastMap = -Infinity;
  return {
    group,
    ready,
    span: [-WALL.w / 2, -WALL.w / 2 + w],
    /** Redraw what changed at minute t; `now` (wall ms) throttles the map when the clock runs fast, `force` skips that. */
    update(t, { now = performance.now(), force = false } = {}) {
      const flights = flightsAt(P, t), air = positionsAt(P, flights, t), m = Math.floor(t);
      if (force || now - lastMap > 110 || windows.map.key == null) {
        const k = `${Math.floor(t * 3)}|${!!geo.data}`;
        if (windows.map.key !== k) {
          windows.map.update(k, t, air, rings(P, t));
          lastMap = now;
        }
      }
      const fr = radar?.at(t);
      const okA = fr && image(fr.a.url).ok, okB = fr && image(fr.b.url).ok;
      windows.sky.update(radar ? `${fr?.a.url}|${fr?.b.url}|${Math.round((fr?.f ?? 0) * 8)}|${okA}|${okB}|${!!geo.data}` : metarAt(t)?.id ?? "none", t);
      windows.board.update(`${m}`, t, flights);
      windows.faa.update(`${m}`, t);
      windows.ops.update(`${m}|${air.length}`, t, air.length);
      windows.pax?.update(`${m}`, t);
      drawLed(t);
    },
    /** Resolves once the radar frames shown at t are loaded, so a recorded frame has them. */
    loaded(t) {
      const fr = radar?.at(t);
      return fr ? Promise.all([fr.a, fr.b].map((f) => image(f.url).decode().catch(() => {}))) : Promise.resolve();
    },
  };
}
