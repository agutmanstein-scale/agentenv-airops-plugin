// The US map, styled like a TV weather map: Natural Earth relief, white state lines, the NEXRAD radar cross-faded
// between frames, the task's flights in the air along their great circles, coloured by status, and the airports
// ringed when the FAA stops or slows their arrivals. One MapLibre map; the inset and the full view are the same map.
import maplibregl from "maplibre-gl";
import { capsAt } from "./model.js";

const TILES = "https://tiles.openfreemap.org";
const PLANE = '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 64 64"><path d="M32 3c2.4 0 3.8 2.8 3.8 6.6V25l21.8 12.6v5.6L35.8 36v14.2l6.6 5.2V60L32 57l-10.4 3v-4.6l6.6-5.2V36L6.4 43.2v-5.6L28.2 25V9.6C28.2 5.8 29.6 3 32 3z" fill="#fff"/></svg>';
const INK = { ontime: "#1f9d5c", delayed: "#d98a0b", held: "#2a78d6", cancelled: "#d9483b", auto: "#c4458f", scheduled: "#5a6774", morning: "#5a6774" };
const BY_CLASS = ["match", ["get", "cls"], ...Object.entries(INK).flat(), "#5a6774"];

export const ATTRIBUTION = "map Natural Earth, © OpenMapTiles © OpenStreetMap contributors via OpenFreeMap · radar NEXRAD";

function style() {
  const omt = (id, layer, extra) => ({ id, source: "omt", "source-layer": layer, ...extra });
  return {
    version: 8,
    glyphs: `${TILES}/fonts/{fontstack}/{range}.pbf`,
    sources: {
      relief: { type: "raster", tiles: [`${TILES}/natural_earth/ne2sr/{z}/{x}/{y}.png`], tileSize: 256, maxzoom: 6 },
      omt: { type: "vector", url: `${TILES}/planet` },
    },
    layers: [
      { id: "ocean", type: "background", paint: { "background-color": "#9fc5de" } },
      { id: "relief", type: "raster", source: "relief", paint: { "raster-saturation": -0.15, "raster-contrast": -0.08, "raster-brightness-min": 0.1, "raster-fade-duration": 0 } },
      omt("water", "water", { type: "fill", paint: { "fill-color": "#9fc5de" } }),
      omt("states", "boundary", { type: "line", filter: ["all", ["==", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
        paint: { "line-color": "rgba(255,255,255,0.85)", "line-width": ["interpolate", ["linear"], ["zoom"], 2, 0.5, 6, 1.2] } }),
      omt("countries", "boundary", { type: "line", filter: ["all", ["==", ["get", "admin_level"], 2], ["!=", ["get", "maritime"], 1]],
        paint: { "line-color": "#ffffff", "line-width": ["interpolate", ["linear"], ["zoom"], 2, 1, 6, 2] } }),
      omt("state-names", "place", { type: "symbol", minzoom: 3.6, filter: ["==", ["get", "class"], "state"],
        layout: { "text-field": ["upcase", ["coalesce", ["get", "name:en"], ["get", "name"]]], "text-font": ["Noto Sans Regular"], "text-size": 10,
          "text-letter-spacing": 0.12, "text-max-width": 8 },
        paint: { "text-color": "rgba(46,62,72,0.55)", "text-halo-color": "rgba(255,255,255,0.4)", "text-halo-width": 1 } }),
    ],
  };
}

function bounds(P) {
  const pts = Object.values(P.task.airports).filter((a) => a.lon > -130 && a.lon < -60 && a.lat > 23 && a.lat < 50);
  const lons = pts.map((a) => a.lon), lats = pts.map((a) => a.lat);
  return [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]];
}

function planeImage() {
  const img = new Image(48, 48);
  img.src = `data:image/svg+xml;utf8,${encodeURIComponent(PLANE)}`;
  return img.decode().then(() => {
    const c = document.createElement("canvas");
    c.width = c.height = 48;
    c.getContext("2d").drawImage(img, 0, 0, 48, 48);
    return c.getContext("2d").getImageData(0, 0, 48, 48);
  });
}

export class NetworkMap {
  constructor(el, P, { radar = null, interactive = true } = {}) {
    this.el = el;
    this.P = P;
    this.radar = radar;
    this.padding = { top: 20, bottom: 20, left: 20, right: 20 };
    this.shown = { a: null, b: null };
    this.map = new maplibregl.Map({ container: el, style: style(), bounds: bounds(P), fitBoundsOptions: { padding: 20 }, attributionControl: false,
      fadeDuration: 0, interactive, renderWorldCopies: false, canvasContextAttributes: { preserveDrawingBuffer: true, antialias: true } });
    this.loaded = new Promise((resolve) => this.map.once("load", resolve)).then(() => this.setup());
  }

  async setup() {
    const map = this.map, P = this.P, hub = P.task.hub;
    map.addImage("plane", await planeImage(), { sdf: true });
    const empty = { type: "FeatureCollection", features: [] };
    if (this.radar) {
      const [w, s, e, n] = this.radar.bounds, coordinates = [[w, n], [e, n], [e, s], [w, s]];
      for (const k of ["a", "b"]) {
        map.addSource(`radar-${k}`, { type: "image", url: this.radar.frames[0].url, coordinates });
        map.addLayer({ id: `radar-${k}`, type: "raster", source: `radar-${k}`, paint: { "raster-opacity": 0, "raster-fade-duration": 0, "raster-resampling": "nearest" } }, "states");
      }
    }
    const airports = Object.entries(P.task.airports).map(([code, a]) => ({ type: "Feature", properties: { code, hub: code === hub }, geometry: { type: "Point", coordinates: [a.lon, a.lat] } }));
    map.addSource("airports", { type: "geojson", data: { type: "FeatureCollection", features: airports } });
    for (const id of ["routes", "trails", "planes", "rings"]) map.addSource(id, { type: "geojson", data: empty });
    map.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-cap": "round" },
      paint: { "line-color": BY_CLASS, "line-opacity": 0.55, "line-width": ["interpolate", ["linear"], ["zoom"], 2, 0.8, 6, 1.6], "line-dasharray": [2, 2] } });
    map.addLayer({ id: "trails-casing", type: "line", source: "trails", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "rgba(255,255,255,0.85)", "line-width": ["interpolate", ["linear"], ["zoom"], 2, 2.6, 6, 4.4] } });
    map.addLayer({ id: "trails", type: "line", source: "trails", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": BY_CLASS, "line-width": ["interpolate", ["linear"], ["zoom"], 2, 1.3, 6, 2.4] } });
    map.addLayer({ id: "rings", type: "circle", source: "rings",
      paint: { "circle-radius": ["case", ["get", "hub"], 11, 8], "circle-color": ["get", "color"], "circle-opacity": 0.22,
        "circle-stroke-color": ["get", "color"], "circle-stroke-width": 2 } });
    map.addLayer({ id: "airports", type: "circle", source: "airports",
      paint: { "circle-radius": ["case", ["get", "hub"], 5.5, 2.6], "circle-color": ["case", ["get", "hub"], "#14324a", "#ffffff"],
        "circle-stroke-color": ["case", ["get", "hub"], "#ffffff", "#14324a"], "circle-stroke-width": ["case", ["get", "hub"], 2, 1] } });
    map.addLayer({ id: "planes", type: "symbol", source: "planes",
      layout: { "icon-image": "plane", "icon-size": ["interpolate", ["linear"], ["zoom"], 2, 0.34, 6, 0.62], "icon-rotate": ["get", "heading"],
        "icon-rotation-alignment": "map", "icon-allow-overlap": true, "icon-ignore-placement": true },
      paint: { "icon-color": BY_CLASS, "icon-halo-color": "#ffffff", "icon-halo-width": 1.6 } });
    map.addLayer({ id: "codes", type: "symbol", source: "airports", minzoom: 2.6,
      layout: { "text-field": ["get", "code"], "text-font": ["Noto Sans Bold"], "text-size": ["case", ["get", "hub"], 13, 9.5],
        "text-offset": [0, -1.15], "text-allow-overlap": false, "symbol-sort-key": ["case", ["get", "hub"], 0, 1] },
      paint: { "text-color": "#14324a", "text-halo-color": "rgba(255,255,255,0.92)", "text-halo-width": 1.4 } });
    this.fit();
  }

  fit(padding = this.padding) {
    this.padding = padding;
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (w < 60 || h < 60) return;
    const pad = { top: Math.min(padding.top, h * 0.4), bottom: Math.min(padding.bottom, h * 0.4), left: Math.min(padding.left, w * 0.4), right: Math.min(padding.right, w * 0.4) };
    this.map.fitBounds(bounds(this.P), { padding: pad, animate: false, maxZoom: 5.5 });
  }

  resize(padding) {
    this.map.resize();
    this.fit(padding);
  }

  update(frame) {
    if (!this.map.getSource("planes")) return;
    const feature = (coordinates, props, type = "LineString") => ({ type: "Feature", properties: props, geometry: { type, coordinates } });
    const air = frame.positions, collection = (features) => ({ type: "FeatureCollection", features });
    this.map.getSource("routes").setData(collection(air.map((p) => feature(p.f.s._path, { cls: p.f.cls }))));
    this.map.getSource("trails").setData(collection(air.map((p) => feature(p.trail, { cls: p.f.cls }))));
    this.map.getSource("planes").setData(collection(air.map((p) => feature(p.pos, { cls: p.f.cls, heading: p.heading }, "Point"))));
    this.map.getSource("rings").setData(collection([...frame.rings].map(([code, r]) => {
      const a = this.P.task.airports[code];
      return feature([a.lon, a.lat], { hub: code === this.P.task.hub, color: INK[r.key] }, "Point");
    })));
    this.showRadar(frame.radar);
  }

  showRadar(r) {
    if (!this.radar) return;
    for (const [k, f, a] of [["a", r?.a, r ? 1 - r.f : 0], ["b", r?.b, r ? r.f : 0]]) {
      if (f && this.shown[k] !== f.url) {
        this.map.getSource(`radar-${k}`).updateImage({ url: f.url });
        this.shown[k] = f.url;
      }
      this.map.setPaintProperty(`radar-${k}`, "raster-opacity", f ? 0.8 * a : 0);
    }
  }

  idle() {
    return new Promise((resolve) => {
      let done = false;
      const finish = () => !done && ((done = true), resolve());
      this.map.once("idle", finish);
      this.map.triggerRepaint();
      setTimeout(finish, 15000);
    });
  }
}

/** The airports ringed at t: stopped (no arrivals this hour) or slowed (a cap under the schedule), as the desk knew. */
export function rings(P, t) {
  const caps = capsAt(P, t), nominal = P.ep.nominal ?? {}, h = String(Math.floor(t / 60)), out = new Map();
  for (const code of Object.keys(P.task.airports)) {
    const cap = caps[`${code}:arr`]?.[h], nom = nominal[`${code}:arr`]?.[h];
    if (cap === 0) out.set(code, { key: "cancelled", label: "stopped" });
    else if (cap != null && nom != null && cap < nom) out.set(code, { key: "delayed", label: `${cap}/h` });
  }
  return out;
}

/** The radar frames by time: at(t) gives the two frames around t and the cross-fade between them. */
export function radarFrames(radar, base) {
  const frames = radar.frames.map((f) => ({ ...f, url: new URL(f.url, base).href }));
  const step = radar.step ?? 10;
  return {
    count: frames.length,
    bounds: radar.bounds,
    frames,
    at(t) {
      if (!frames.length || t > frames.at(-1).t + step || t < frames[0].t - step) return null;
      let i = 0;
      while (i + 1 < frames.length && frames[i + 1].t <= t) i++;
      const a = frames[i], b = frames[Math.min(i + 1, frames.length - 1)];
      return { a, b, f: b === a || t <= a.t ? 0 : Math.min(1, (t - a.t) / (b.t - a.t)) };
    },
  };
}
