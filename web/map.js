// The two maps: the network (great-circle legs coloured by status, aircraft moving along them, NEXRAD radar under
// them, airports ringed by their slot caps) and the hub in 3D (OpenStreetMap twin, aircraft at the gates). Each is a
// MapLibre map with a deck.gl overlay drawn into the map's own WebGL context.
import maplibregl from "maplibre-gl";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { BitmapLayer, IconLayer, PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { SimpleMeshLayer } from "@deck.gl/mesh-layers";
import { PathStyleExtension } from "@deck.gl/extensions";
import { STATUS, bearing } from "./model.js";

const STYLE = "https://tiles.openfreemap.org/styles/dark";
const PLANE = `data:image/svg+xml;utf8,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><path d="M32 3c2.4 0 3.8 2.8 3.8 6.6V25l21.8 12.6v5.6L35.8 36v14.2l6.6 5.2V60L32 57l-10.4 3v-4.6l6.6-5.2V36L6.4 43.2v-5.6L28.2 25V9.6C28.2 5.8 29.6 3 32 3z" fill="#fff"/></svg>')}`;
const HUB_BEARING = { JFK: -28, BOS: 40 };
const PHASE_ALPHA = { landed: 95, air: 150, planned: 70, scheduled: 45, cancelled: 150 };
const lighten = (rgb, k = 0.4) => rgb.map((v) => Math.round(v + (255 - v) * k));
const dash = new PathStyleExtension({ dash: true });

export const CAMERAS = {
  network: (P, el) => fit(P, el),
  region: (P) => ({ center: [hubLngLat(P)[0] - 3.4, hubLngLat(P)[1] + 0.7], zoom: 5.3, pitch: 40, bearing: -8 }),
  hub: (P, el, twin) => ({ center: twin?.focus ?? twin?.center ?? hubLngLat(P), zoom: el.clientWidth < 700 ? 15.2 : 16.1, pitch: 62,
    bearing: twin?.bearing ?? HUB_BEARING[P.task.hub] ?? 0 }),
};

const hubLngLat = (P) => [P.task.airports[P.task.hub].lon, P.task.airports[P.task.hub].lat];

function fit(P, el) {
  const pts = Object.values(P.task.airports).filter((a) => a.lon > -130 && a.lon < -60 && a.lat > 23 && a.lat < 50);
  const lons = pts.map((a) => a.lon), lats = pts.map((a) => a.lat);
  const w = Math.min(...lons), e = Math.max(...lons), s = Math.min(...lats), n = Math.max(...lats);
  const span = Math.max((e - w) / Math.max(el.clientWidth, 300) * 512, (n - s) * 1.35 / Math.max(el.clientHeight, 200) * 512);
  const zoom = Math.max(1.5, Math.min(6, Math.log2(360 / span) + 0.08));
  return { center: [(w + e) / 2 + (e - w) * 0.02, (s + n) / 2 - (n - s) * 0.12], zoom, pitch: 32, bearing: 0 };
}

export class MapView {
  constructor(el, P, { twin, radar, interactive = true, role }) {
    this.el = el;
    this.P = P;
    this.twin = twin;
    this.radar = radar;
    this.role = role;
    this.camera = null;
    this.map = new maplibregl.Map({
      container: el, style: STYLE, center: hubLngLat(P), zoom: 4, attributionControl: false, fadeDuration: 0,
      interactive, canvasContextAttributes: { preserveDrawingBuffer: true, antialias: true }, maxPitch: 75,
    });
    this.overlay = new MapboxOverlay({ interleaved: true, layers: [] });
    this.map.addControl(this.overlay);
    this.loaded = new Promise((resolve) => this.map.once("load", resolve)).then(() => this.setup());
  }

  setup() {
    const style = this.map.getStyle();
    this.labelsAt = style.layers.find((l) => l.type === "symbol")?.id;
    for (const l of style.layers) {
      if (l.type === "symbol" && /poi|housenum|aerialway/.test(l.id)) this.map.setLayoutProperty(l.id, "visibility", "none");
    }
    if (this.twin) addTwin(this.map, this.twin, this.labelsAt);
  }

  setCamera(name) {
    this.camera = name;
    this.map.jumpTo(CAMERAS[name](this.P, this.el, this.twin));
    this.el.dataset.camera = name;
  }

  resize() {
    this.map.resize();
    if (this.camera) this.setCamera(this.camera);
  }

  update(frame) {
    const hub = this.camera === "hub";
    this.overlay.setProps({ layers: [...radarLayers(this, frame, hub), ...networkLayers(this, frame, hub), ...hubLayers(this, frame, hub)] });
  }

  idle() {
    return new Promise((resolve) => {
      let done = false;
      const finish = () => !done && (done = true, resolve());
      this.map.once("idle", finish);
      this.map.triggerRepaint();
      setTimeout(finish, 15000);
    });
  }
}

function radarLayers(view, frame, hub) {
  const r = frame.radar;
  if (!r || !view.radar) return [];
  const base = (hub ? 0.32 : 0.72);
  const out = [];
  for (const [key, img, a] of [["a", r.a, 1 - r.f], ["b", r.b, r.f]]) {
    if (!img || a <= 0.001) continue;
    out.push(new BitmapLayer({ id: `radar-${key}-${img.dataset.k}`, image: img, bounds: view.radar.bounds, opacity: base * a,
      beforeId: view.labelsAt, textureParameters: { minFilter: "linear", magFilter: "nearest" } }));
  }
  return out;
}

function networkLayers(view, frame, hub) {
  const { flights, positions, rings, cls } = frame;
  const legs = flights.filter((f) => f.s._path);
  const color = (f) => [...STATUS[f.cls].rgb, PHASE_ALPHA[f.phase] ?? 90];
  const key = frame.key;
  const codes = Object.entries(view.P.task.airports).map(([code, a]) => ({ code, ...a, ring: rings.get(code) }));
  return [
    new PathLayer({ id: "legs", data: legs, getPath: (f) => f.s._path, getColor: color, widthUnits: "pixels",
      getWidth: (f) => (f.phase === "air" ? 1.6 : f.phase === "cancelled" ? 1.3 : f.phase === "landed" ? 1.2 : 1), capRounded: true,
      jointRounded: true, getDashArray: (f) => (f.phase === "cancelled" ? [4, 3] : [0, 0]),
      dashJustified: true, extensions: [dash], updateTriggers: { getColor: key, getWidth: key, getDashArray: key },
      visible: !hub }),
    new PathLayer({ id: "trails", data: positions, getPath: (p) => p.trail, getColor: (p) => [...lighten(STATUS[p.f.cls].rgb, 0.25), 235],
      widthUnits: "pixels", getWidth: 2.6, capRounded: true, jointRounded: true, visible: !hub }),
    new ScatterplotLayer({ id: "rings", data: codes.filter((a) => a.ring), getPosition: (a) => [a.lon, a.lat],
      radiusUnits: "pixels", getRadius: (a) => (a.code === view.P.task.hub ? 15 : 11), stroked: true, filled: true,
      getFillColor: (a) => [...a.ring.rgb, 45], getLineColor: (a) => [...a.ring.rgb, 230], lineWidthUnits: "pixels",
      getLineWidth: 2, updateTriggers: { getFillColor: key, getLineColor: key }, visible: !hub }),
    new ScatterplotLayer({ id: "airports", data: codes, getPosition: (a) => [a.lon, a.lat], radiusUnits: "pixels",
      getRadius: (a) => (a.code === view.P.task.hub ? 6 : 3.2), getFillColor: (a) => (a.code === view.P.task.hub ? [240, 239, 236] : [178, 182, 188]),
      stroked: true, getLineColor: [12, 14, 17], lineWidthUnits: "pixels", getLineWidth: 1, visible: !hub }),
    new TextLayer({ id: "codes", data: codes, getPosition: (a) => [a.lon, a.lat],
      getText: (a) => (a.ring && a.code === view.P.task.hub ? `${a.code} ${a.ring.label}` : a.code), getSize: (a) => (a.code === view.P.task.hub ? 13 : 10.5),
      getColor: (a) => (a.ring ? [...a.ring.rgb, 255] : [214, 216, 220, 230]), getPixelOffset: [0, -15], fontFamily: "ui-monospace, Menlo, monospace",
      fontWeight: 600, outlineWidth: 3, outlineColor: [12, 14, 17, 255], fontSettings: { sdf: true }, characterSet: "auto",
      updateTriggers: { getText: key, getColor: key }, visible: !hub }),
    ...["halo", "planes"].map((id) => new IconLayer({ id, data: positions, getPosition: (p) => p.pos, getIcon: () => "plane",
      iconAtlas: view.icons, iconMapping: { plane: { x: 0, y: 0, width: 64, height: 64, mask: true, anchorY: 32 } },
      getAngle: (p) => -p.heading, getSize: id === "halo" ? 30 : 24, sizeUnits: "pixels",
      getColor: (p) => (id === "halo" ? [10, 11, 12, 230] : [...lighten(STATUS[p.f.cls].rgb, 0.15), 255]),
      updateTriggers: { getColor: cls }, visible: !hub, alphaCutoff: 0.05 })),
  ];
}

function hubLayers(view, frame, hub) {
  if (!view.twin || !frame.parked) return [];
  return [
    new SimpleMeshLayer({ id: "parked", data: frame.parked, mesh: view.mesh, getPosition: (a) => a.pos,
      getOrientation: (a) => [a.pitch ?? 0, 90 - a.heading, 0], getScale: (a) => [a.scale, a.scale, a.scale],
      getColor: (a) => [...STATUS[a.cls].rgb, 255], sizeScale: 1, material: { ambient: 0.55, diffuse: 0.6, shininess: 24, specularColor: [60, 60, 60] },
      updateTriggers: { getColor: frame.key }, visible: hub || view.role === "inset" }),
    new TextLayer({ id: "parked-tags", data: frame.parked.filter((a) => a.label && a.cls !== "ontime" && a.cls !== "scheduled"), getPosition: (a) => [...a.pos, 14],
      getText: (a) => a.label, getSize: 11, getColor: (a) => [...STATUS[a.cls].rgb, 255], fontFamily: "ui-monospace, Menlo, monospace",
      fontWeight: 600, outlineWidth: 3, outlineColor: [12, 14, 17, 255], fontSettings: { sdf: true }, characterSet: "auto",
      billboard: true, updateTriggers: { getColor: frame.key, getText: frame.key }, visible: hub && view.role === "main" }),
  ];
}

function addTwin(map, twin, before) {
  map.addSource("twin", { type: "geojson", data: twin.geojson, attribution: "Hub twin © OpenStreetMap contributors (ODbL)" });
  const is = (...kinds) => ["in", ["get", "kind"], ["literal", kinds]];
  const meters = (k) => ["interpolate", ["exponential", 2], ["zoom"], 12, ["*", ["coalesce", ["get", "width"], 20], 0.035 * k], 20, ["*", ["coalesce", ["get", "width"], 20], 8.9 * k]];
  const carrier = twin.geojson.carrier_terminal ?? "";
  const layers = [
    { id: "twin-apron", type: "fill", filter: is("apron"), paint: { "fill-color": "#23272d", "fill-opacity": 0.95 } },
    { id: "twin-runway", type: "fill", filter: is("runway"), paint: { "fill-color": "#3a3f47", "fill-outline-color": "#6b7079" } },
    { id: "twin-stopway", type: "fill", filter: is("stopway"), paint: { "fill-color": "#2e3238" } },
    { id: "twin-taxiway", type: "line", filter: ["all", is("taxiway"), ["==", ["geometry-type"], "LineString"]],
      layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#30343a", "line-width": meters(1) } },
    { id: "twin-taxiway-c", type: "line", filter: ["all", is("taxiway", "taxilane"), ["==", ["geometry-type"], "LineString"]],
      paint: { "line-color": "#c9a94a", "line-opacity": 0.55, "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.4, 16, 1.2] } },
    { id: "twin-stand", type: "line", filter: is("parking_position"), paint: { "line-color": "#5a606a", "line-width": 1 } },
    { id: "twin-bridge", type: "line", filter: is("jet_bridge"), paint: { "line-color": "#9aa3ad", "line-width": meters(0.16) } },
    { id: "twin-hangar", type: "fill-extrusion", filter: ["all", is("hangar"), ["!=", ["geometry-type"], "Point"]],
      paint: { "fill-extrusion-color": "#4e5662", "fill-extrusion-height": ["coalesce", ["get", "height"], 16], "fill-extrusion-opacity": 0.92 } },
    { id: "twin-terminal", type: "fill-extrusion", filter: ["all", is("terminal"), ["!=", ["geometry-type"], "Point"]],
      paint: { "fill-extrusion-color": ["case", ["==", ["get", "name"], carrier], "#8aa4c8", "#66717f"],
        "fill-extrusion-height": ["coalesce", ["get", "height"], 20], "fill-extrusion-opacity": 0.94 } },
    { id: "twin-gate", type: "circle", filter: ["all", is("gate"), ["==", ["geometry-type"], "Point"]],
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 1.5, 17, 4], "circle-color": ["case", ["==", ["get", "terminal"], carrier], "#e6e4e0", "#7c838d"],
        "circle-stroke-color": "#101215", "circle-stroke-width": 1 } },
    { id: "twin-gate-ref", type: "symbol", minzoom: 15.2, filter: ["all", is("gate"), ["==", ["get", "terminal"], carrier]],
      layout: { "text-field": ["get", "ref"], "text-size": 10, "text-offset": [0, -1.1], "text-font": ["Noto Sans Regular"], "text-allow-overlap": false },
      paint: { "text-color": "#d8d6d0", "text-halo-color": "#101215", "text-halo-width": 1.2 } },
  ];
  for (const l of layers) map.addLayer({ source: "twin", minzoom: 11, ...l }, l.type === "symbol" ? undefined : before);
}

/** A twin's gates as aircraft slots: the carrier terminal's gates first (nose to the building), then its stands, then
 * the nearest other stands. */
export function slots(twin) {
  const feats = twin.geojson.features, carrier = twin.geojson.carrier_terminal;
  const terminals = feats.filter((f) => f.properties.kind === "terminal" && f.geometry.type !== "Point");
  const stands = feats.filter((f) => f.properties.kind === "parking_position" && f.geometry.type === "LineString");
  const gates = feats.filter((f) => f.properties.kind === "gate" && f.geometry.type === "Point");
  const k = Math.cos((twin.center[1] * Math.PI) / 180), m = 111320;
  const dist = (a, b) => Math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * m;
  const rings = terminals.flatMap((f) => (f.geometry.type === "Polygon" ? [f.geometry.coordinates[0]] : f.geometry.coordinates.map((p) => p[0])).map((r) => ({ r, name: f.properties.name })));
  const nearestEdge = (p, name) => {
    let best = null, bd = Infinity;
    for (const { r, name: n } of rings) {
      if (name && n !== name) continue;
      for (let i = 0; i < r.length - 1; i++) {
        const q = closest(p, r[i], r[i + 1], k), d = dist(p, q);
        if (d < bd) (bd = d), (best = q);
      }
    }
    return best;
  };
  const out = [], used = new Set();
  const byRef = (a, b) => String(a.properties.ref).localeCompare(String(b.properties.ref), "en", { numeric: true });
  for (const g of gates.filter((f) => f.properties.terminal === carrier).sort(byRef)) {
    const p = g.geometry.coordinates;
    const stand = stands.find((s) => !used.has(s) && [s.geometry.coordinates[0], s.geometry.coordinates.at(-1)].some((e) => dist(e, p) < 45));
    let pos, heading;
    if (stand) {
      used.add(stand);
      const c = dist(stand.geometry.coordinates[0], p) < dist(stand.geometry.coordinates.at(-1), p) ? stand.geometry.coordinates : [...stand.geometry.coordinates].reverse();
      heading = bearing(c[1], c[0]);
      pos = toward(c[0], c[1], 19, k);
    } else {
      const edge = nearestEdge(p, carrier) ?? p;
      heading = bearing(p, edge);
      pos = toward(p, edge, -26, k);
    }
    out.push({ pos, heading, ref: g.properties.ref, own: true });
  }
  const center = twin.center;
  const rest = stands.filter((s) => !used.has(s)).map((s) => ({ s, d: dist(s.geometry.coordinates[0], center), own: s.properties.terminal === carrier }))
    .sort((a, b) => (b.own - a.own) || a.d - b.d);
  for (const { s } of rest) {
    const c = s.geometry.coordinates;
    out.push({ pos: toward(c[0], c.at(-1), 0.5 * dist(c[0], c.at(-1)), k), heading: bearing(c.at(-1), c[0]), ref: s.properties.ref || "" });
  }
  return out;
}

/** Runway centrelines (each direction), from the strips build_twin.py made of them, longest first. */
export function runways(twin) {
  const k = Math.cos((twin.center[1] * Math.PI) / 180), out = [];
  for (const f of twin.geojson.features) {
    if (f.properties.kind !== "runway" || f.geometry.type !== "MultiPolygon") continue;
    const rings = f.geometry.coordinates.map((p) => p[0]), first = rings[0], last = rings.at(-1);
    const mid = (u, v) => [(u[0] + v[0]) / 2, (u[1] + v[1]) / 2];
    const a = mid(first[0], first[3]), b = mid(last[1], last[2]);
    const len = Math.hypot((b[0] - a[0]) * k, b[1] - a[1]) * 111320;
    if (len > 1200) out.push({ a, b, len, heading: bearing(a, b) }, { a: b, b: a, len, heading: bearing(b, a) });
  }
  return out.sort((x, y) => y.len - x.len).slice(0, 4);
}

/** An arrival on final and its roll-out, or a departure's roll and climb, along the runway best lined up with its route,
 * ``lag`` minutes behind the movements of the same minute; null outside those few minutes. */
export function movement(f, t, hub, rws, k, lag = 0) {
  if (!rws.length || f.phase !== "air") return null;
  const arriving = f.s.dest === hub, p = f.s._path;
  if (arriving ? f.r.arr - lag - t > 7 || f.r.arr - lag - t < 0.6 : t - f.r.dep - lag > 5.5 || f.s.orig !== hub) return null;
  const route = arriving ? bearing(p.at(-2), p.at(-1)) : bearing(p[0], p[1]);
  const off = (r) => Math.abs((((r.heading - route) % 360) + 540) % 360 - 180);
  const r = rws.reduce((best, x) => (off(x) < off(best) ? x : best));
  if (arriving) {
    const left = f.r.arr - lag - t;
    if (left > 2.4) {
      const u = (left - 2.4) / 4.6;
      return { pos: [...toward(r.a, r.b, -16000 * u, k), 900 * u + 3], heading: r.heading, pitch: -3 };
    }
    return { pos: [...toward(r.a, r.b, r.len * 0.65 * (1 - (left - 0.6) / 1.8), k), 0], heading: r.heading, pitch: 0 };
  }
  const gone = t - f.r.dep - lag;
  if (gone < 0) return { pos: [...toward(r.a, r.b, -70 * Math.ceil(-gone / 1.2), k), 0], heading: r.heading, pitch: 0 };
  if (gone < 1.4) return { pos: [...toward(r.a, r.b, r.len * 0.6 * (gone / 1.4) ** 2, k), 0], heading: r.heading, pitch: 0 };
  const u = (gone - 1.4) / 4.1;
  return { pos: [...toward(r.a, r.b, r.len * 0.6 + 14000 * u, k), 1100 * u], heading: r.heading, pitch: 8 };
}

/** Where the hub camera looks: the middle of the carrier's gates, from the apron toward the building. */
export function framing(all) {
  const own = all.filter((s) => s.own);
  if (!own.length) return {};
  const x = own.reduce((a, s) => a + Math.sin((s.heading * Math.PI) / 180), 0), y = own.reduce((a, s) => a + Math.cos((s.heading * Math.PI) / 180), 0);
  const focus = [own.reduce((a, s) => a + s.pos[0], 0) / own.length, own.reduce((a, s) => a + s.pos[1], 0) / own.length];
  return { focus, bearing: ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360 };
}

function closest(p, a, b, k) {
  const ax = a[0] * k, bx = b[0] * k, px = p[0] * k, dx = bx - ax, dy = b[1] - a[1];
  const u = Math.max(0, Math.min(1, ((px - ax) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return [(ax + u * dx) / k, a[1] + u * dy];
}

function toward(a, b, meters, k) {
  const d = Math.hypot((b[0] - a[0]) * k, b[1] - a[1]) * 111320 || 1, u = meters / d;
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
}

/** A narrow-body built from boxes, nose along +x, in metres: fuselage, nose, wings, engines, tailplane and fin. */
export function aircraftMesh() {
  const pos = [], nor = [];
  const quad = (a, b, c, d, n) => {
    pos.push(...a, ...b, ...c, ...a, ...c, ...d);
    for (let i = 0; i < 6; i++) nor.push(...n);
  };
  const box = (x0, x1, y0, y1, z0, z1) => {
    quad([x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1], [0, 0, 1]);
    quad([x0, y1, z0], [x1, y1, z0], [x1, y0, z0], [x0, y0, z0], [0, 0, -1]);
    quad([x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1], [0, -1, 0]);
    quad([x1, y1, z0], [x0, y1, z0], [x0, y1, z1], [x1, y1, z1], [0, 1, 0]);
    quad([x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1], [1, 0, 0]);
    quad([x0, y1, z0], [x0, y0, z0], [x0, y0, z1], [x0, y1, z1], [-1, 0, 0]);
  };
  const tri = (a, b, c) => {
    const u = a.map((v, i) => b[i] - v), w = a.map((v, i) => c[i] - v);
    const n = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]], l = Math.hypot(...n) || 1;
    pos.push(...a, ...b, ...c);
    for (let i = 0; i < 3; i++) nor.push(n[0] / l, n[1] / l, n[2] / l);
  };
  box(-15, 15, -2, 2, 1.8, 5.8);
  const tip = [19.5, 0, 3.6], c = [[15, -2, 1.8], [15, 2, 1.8], [15, 2, 5.8], [15, -2, 5.8]];
  for (let i = 0; i < 4; i++) tri(c[i], c[(i + 1) % 4], tip);
  const tail = [-18.5, 0, 5.4];
  for (let i = 0; i < 4; i++) tri(c[(i + 1) % 4].map((v, j) => (j === 0 ? -15 : v)), c[i].map((v, j) => (j === 0 ? -15 : v)), tail);
  box(-3.5, 3.5, -17, 17, 2.6, 3.1);
  box(-1.5, 3.2, -6.4, -4.4, 1.0, 2.6);
  box(-1.5, 3.2, 4.4, 6.4, 1.0, 2.6);
  box(-18.2, -14.6, -6.2, 6.2, 5.2, 5.6);
  box(-18.4, -13.2, -0.3, 0.3, 5.6, 11.5);
  return { positions: { value: new Float32Array(pos), size: 3 }, normals: { value: new Float32Array(nor), size: 3 } };
}

export function loadIcon() {
  const img = new Image(64, 64);
  img.src = PLANE;
  return img.decode().then(() => img);
}
