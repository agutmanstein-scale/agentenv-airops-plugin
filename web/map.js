// The US map, styled like a TV weather map: Natural Earth relief, white state lines, the NEXRAD radar cross-faded
// between frames, the task's flights in the air along their great circles, coloured by status, and the airports
// ringed when the FAA stops or slows their arrivals. One MapLibre map; the inset and the full view are the same map.
// On a control-center day it also shows the crews: those resting in a hotel away from base, the reserves still on call
// at each base, the crews riding as passengers on the aircraft in the air, and the legs of a crew picked in the drawer.
import maplibregl from "maplibre-gl";
import { BASES, CREW_INK, crewIndex, deadheadsOn, reservesAt, strandedAt } from "./crewdata.js";
import { capsAt, esc } from "./model.js";

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
    this.crews = crewIndex(P);
    this.tags = new Map();
    this.focus = null;
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
    if (this.crews) this.setupCrews(empty);
    map.addLayer({ id: "codes", type: "symbol", source: "airports", minzoom: 2.6,
      layout: { "text-field": ["get", "code"], "text-font": ["Noto Sans Bold"], "text-size": ["case", ["get", "hub"], 13, 9.5],
        "text-offset": [0, -1.15], "text-allow-overlap": false, "symbol-sort-key": ["case", ["get", "hub"], 0, 1] },
      paint: { "text-color": "#14324a", "text-halo-color": "rgba(255,255,255,0.92)", "text-halo-width": 1.4 } });
    this.fit();
  }

  setupCrews(empty) {
    const map = this.map, zoom = (a, b) => ["interpolate", ["linear"], ["zoom"], 2, a, 6, b];
    const shift = zoom(["literal", [7, -7]], ["literal", [14, -14]]);
    map.addSource("deadheads", { type: "geojson", data: empty });
    map.addSource("crew-focus", { type: "geojson", data: empty });
    const round = { "line-cap": "round", "line-join": "round" };
    map.addLayer({ id: "crew-focus-casing", type: "line", source: "crew-focus", layout: round,
      paint: { "line-color": "#ffffff", "line-width": zoom(4.4, 7), "line-opacity": 0.9 } }, "rings");
    map.addLayer({ id: "crew-focus", type: "line", source: "crew-focus", layout: round,
      paint: { "line-color": ["case", ["get", "dh"], "#a77be0", "#7a3fc4"], "line-width": zoom(2.4, 4.2) } }, "rings");
    map.addLayer({ id: "dh-ring", type: "circle", source: "deadheads",
      paint: { "circle-radius": zoom(6.5, 13), "circle-color": CREW_INK, "circle-opacity": 0.18, "circle-stroke-color": CREW_INK, "circle-stroke-width": zoom(1.6, 2.6) } }, "planes");
    map.addLayer({ id: "dh-n", type: "symbol", source: "deadheads", filter: [">", ["get", "n"], 1],
      layout: { "text-field": ["to-string", ["get", "n"]], "text-font": ["Noto Sans Bold"], "text-size": zoom(8, 11), "text-allow-overlap": true,
        "text-ignore-placement": true },
      paint: { "text-color": "#5b2d91", "text-halo-color": "#ffffff", "text-halo-width": 1.4, "text-translate": shift } });
    this.layer = document.createElement("div");
    this.layer.className = "cm-tags";
    this.layer.innerHTML = `<svg class="cm-leads" aria-hidden="true"></svg>`;
    this.leads = this.layer.firstChild;
    this.el.appendChild(this.layer);
    this.sum = document.createElement("div");
    this.sum.className = "cm-sum";
    this.sum.hidden = true;
    this.el.appendChild(this.sum);
    this.key = document.createElement("div");
    this.key.className = "cm-key";
    this.key.hidden = true;
    this.el.parentElement?.appendChild(this.key);
    map.on("move", () => this.placeTags());
    const root = this.el.closest(".ep");
    if (root) new MutationObserver(() => this.drawer(root.classList.contains("expert-on"))).observe(root, { attributes: true, attributeFilter: ["class"] });
    document.addEventListener("airops:crew", (e) => this.focusCrew(e.detail?.unit ?? null));
    injectStyle();
  }

  /** With the expert drawer open over a full-screen map on a control-center day, the map fits above it and shows the
   * crew picked there; closed, the crew's legs go. */
  drawer(on) {
    if (on === !!this.expertOn) return;
    this.expertOn = on;
    this.showFocus();
    this.fit();
    this.placeTags();
  }

  fit(padding = this.padding) {
    this.padding = padding;
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (w < 60 || h < 60) return;
    const drawer = this.expertOn ? this.el.closest(".ep")?.querySelector(".expert")?.getBoundingClientRect() : null;
    const under = drawer?.height ? Math.max(0, this.el.getBoundingClientRect().bottom - drawer.top + 16) : 0;
    const pad = { top: Math.min(padding.top, h * 0.4), bottom: Math.max(Math.min(padding.bottom, h * 0.4), Math.min(under, h * 0.7)),
      left: Math.min(padding.left, w * 0.4), right: Math.min(padding.right, w * 0.4) };
    this.map.fitBounds(bounds(this.P), { padding: pad, animate: false, maxZoom: 5.5 });
  }

  resize(padding) {
    this.map.resize();
    this.fit(padding);
    this.placeTags();
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
    if (this.crews && frame.t != null) this.showCrews(frame);
  }

  /** The crews at t: a violet ring round each aircraft in the air carrying crews as passengers and, full screen, a tag
   * beside the airports with the reserves on call (at a base) and the crews resting away from base, airports close on
   * screen sharing one; the corner inset gives only the totals. */
  showCrews({ positions, t }) {
    const C = this.crews, P = this.P, dots = [];
    let riding = 0;
    for (const p of positions) {
      const n = deadheadsOn(C, p.f.s.id).length;
      if (!n) continue;
      riding += n;
      dots.push({ type: "Feature", properties: { n }, geometry: { type: "Point", coordinates: p.pos } });
    }
    this.map.getSource("deadheads").setData({ type: "FeatureCollection", features: dots });
    const stranded = strandedAt(C, t), reserves = reservesAt(C, t);
    const codes = [...new Set([...stranded.keys(), ...[...reserves.keys()].filter((b) => BASES.includes(b))])].filter((c) => P.task.airports[c]);
    const items = codes.map((code) => {
      const a = P.task.airports[code], s = stranded.get(code) ?? [], r = BASES.includes(code) ? reserves.get(code) ?? null : null;
      const rp = r?.pilot.length ?? 0, rc = r?.cabin.length ?? 0;
      return { code, lnglat: [a.lon, a.lat], s, r, rp, rc, rank: (code === P.task.hub ? 1000 : 0) + (rp + rc ? 100 : 0) + s.length };
    }).filter((i) => i.s.length + i.rp + i.rc);
    const key = items.map((i) => `${i.code}:${i.s.length}:${i.rp}:${i.rc}`).join("|");
    if (key !== this.itemsKey) {
      this.itemsKey = key;
      this.items = items;
      this.placeTags();
    }
    const sum = (f) => items.reduce((a, i) => a + f(i), 0), rest = sum((i) => i.s.length), res = sum((i) => i.rp + i.rc);
    const compact = this.el.clientWidth < 700;
    this.showKey(rest > 0, res > 0, riding > 0, compact);
    this.showSum(rest, res, riding, compact);
  }

  showKey(rest, res, riding, compact) {
    const items = [res && `<span><i class="k-res">R</i>reserve crews on call at a base (P pilots, C cabin)</span>`, rest && `<span><i class="k-rest">${MOON}</i>crews resting in a hotel away from base</span>`,
      riding && `<span><i class="k-dh"></i>crews riding as passengers</span>`].filter(Boolean);
    const html = items.join("");
    if (this.key.innerHTML !== html) this.key.innerHTML = html;
    this.key.hidden = compact || !items.length;
  }

  /** The corner inset's line: the crews resting away, the reserves on call and the crews riding, in all. */
  showSum(rest, res, riding, compact) {
    const html = rest + res + riding ? `<b>Crews</b>${[rest && `<span class="rest">${MOON}<b>${rest}</b> resting away</span>`,
      res && `<span class="res"><i>R</i><b>${res}</b> reserves</span>`, riding && `<span class="ride"><i></i><b>${riding}</b> riding</span>`].filter(Boolean).join("")}` : "";
    if (this.sum.innerHTML !== html) this.sum.innerHTML = html;
    this.sum.hidden = !compact || !html;
  }

  /** Full screen only: airports within a few pixels of each other share a tag (the hub's or a base's first, so New York's
   * airports go under JFK), which splits again as the map is zoomed; each tag sits beside its airport on the first side
   * clear of the page's cards and the tags placed before it. */
  placeTags() {
    if (!this.layer) return;
    const W = this.el.clientWidth, H = this.el.clientHeight, full = W >= 700;
    this.layer.hidden = !full;
    if (!full || !this.items) return;
    const pts = this.items.map((i) => ({ ...i, p: this.map.project(i.lnglat) })).sort((a, b) => b.rank - a.rank || a.code.localeCompare(b.code));
    const groups = [];
    for (const it of pts) {
      const g = groups.find((x) => Math.hypot(x.p.x - it.p.x, x.p.y - it.p.y) < GROUP);
      if (g) g.members.push(it);
      else groups.push({ p: it.p, members: [it] });
    }
    const live = new Set();
    for (const g of groups) {
      const id = g.members.map((m) => m.code).join("+"), key = g.members.map((m) => `${m.s.length}:${m.rp}:${m.rc}`).join("|");
      live.add(id);
      let tag = this.tags.get(id);
      if (!tag) {
        const el = document.createElement("div");
        el.className = "cm-tag";
        this.layer.appendChild(el);
        tag = { el, key: null };
        this.tags.set(id, tag);
      }
      Object.assign(tag, { p: g.p, rank: g.members[0].rank });
      if (tag.key !== key) {
        tag.key = key;
        tag.variants = variants(tag.el, g.members);
        tag.shown = null;
      }
    }
    for (const [id, tag] of this.tags) if (!live.has(id)) (tag.el.remove(), this.tags.delete(id));
    const placed = [], leads = [], d = 5, box = this.el.getBoundingClientRect();
    for (const el of document.querySelectorAll(".card, .view.corner, .timeline, .cm-key, .radar-key, .expert")) {
      const r = el.getBoundingClientRect();
      if (r.width && r.height && el.checkVisibility()) placed.push([r.left - box.left - 4, r.top - box.top - 4, r.right - box.left + 4, r.bottom - box.top + 4]);
    }
    for (const { p } of pts) placed.push([p.x - 3, p.y - 3, p.x + 3, p.y + 3], [p.x - 13, p.y - 21, p.x + 13, p.y - 6]);
    const cost = (r) => placed.reduce((sum, q) => sum + Math.max(0, Math.min(r[2], q[2]) - Math.max(r[0], q[0])) * Math.max(0, Math.min(r[3], q[3]) - Math.max(r[1], q[1])), 0)
      + (r[0] < 0 || r[1] < 0 || r[2] > W || r[3] > H ? 1e6 : 0);
    for (const g of [...this.tags.values()].sort((a, b) => b.rank - a.rank)) {
      const p = g.p;
      let best = null;
      for (const side of SIDES) {
        for (const v of g.variants) {
          const [x, y] = side(v.w, v.h, d), r = [p.x + x, p.y + y, p.x + x + v.w, p.y + y + v.h], c = cost(r);
          if (!best || c < best.c) best = { c, r, v };
          if (c === 0) break;
        }
        if (best.c === 0) break;
      }
      if (g.shown !== best.v) g.el.innerHTML = best.v.html;
      g.shown = best.v;
      placed.push(best.r);
      g.el.style.transform = `translate(${Math.round(best.r[0])}px, ${Math.round(best.r[1])}px)`;
      const [x0, y0, x1, y1] = best.r, qx = Math.max(x0, Math.min(x1, p.x)), qy = Math.max(y0, Math.min(y1, p.y));
      if (Math.hypot(qx - p.x, qy - p.y) > 12) leads.push(`<line x1="${p.x.toFixed(1)}" y1="${p.y.toFixed(1)}" x2="${qx.toFixed(1)}" y2="${qy.toFixed(1)}"/>`);
    }
    this.leads.innerHTML = leads.join("");
  }

  /** The legs of a crew picked in the expert drawer, flown solid and ridden as a passenger dashed; null clears. */
  focusCrew(unit) {
    this.focus = unit;
    const legs = unit ? this.crews.legsOf.get(unit) ?? [] : [];
    const features = legs.map((l) => this.P.sched.get(l.flight)).map((s, i) => s?._path && { type: "Feature", properties: { dh: legs[i].dh },
      geometry: { type: "LineString", coordinates: s._path } }).filter(Boolean);
    this.map.getSource("crew-focus")?.setData({ type: "FeatureCollection", features });
    this.showFocus();
  }

  showFocus() {
    if (!this.map.getLayer("crew-focus")) return;
    const on = !!(this.expertOn && this.focus);
    for (const id of ["crew-focus", "crew-focus-casing"]) this.map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
    this.map.setPaintProperty("trails", "line-opacity", on ? 0.35 : 1);
    this.map.setPaintProperty("trails-casing", "line-opacity", on ? 0.35 : 1);
    this.map.setPaintProperty("routes", "line-opacity", on ? 0.2 : 0.55);
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

const MOON = '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M8.6 9.4A4.6 4.6 0 0 1 4.9 1.2a4.6 4.6 0 1 0 5.9 6.4 4.6 4.6 0 0 1-2.2 1.8z" fill="currentColor"/></svg>';

const GROUP = 40;
/** Where a tag may sit against its airport, nearest first: below right, right, below left, left, below, above. */
const SIDES = [(w, h, d) => [d, d], (w, h, d) => [d + 2, -h / 2], (w, h, d) => [-w - d, d], (w, h, d) => [-w - d - 2, -h / 2], (w, h, d) => [-w / 2, d + 3],
  (w, h, d) => [d, -h - d], (w, h, d) => [-w - d, -h - d], (w, h, d) => [-w / 2, -h - 24], (w, h, d) => [d, d + h + 2], (w, h, d) => [-w - d, d + h + 2],
  (w, h, d) => [-w / 2, d + h + 5], (w, h, d) => [d + 14, -h / 2], (w, h, d) => [-w - d - 14, -h / 2], (w, h, d) => [-w / 2, -h - 44],
  (w, h, d) => [-w / 2, d + 34], (w, h, d) => [d + 30, d + 22], (w, h, d) => [-w - d - 30, d + 22], (w, h, d) => [d + 30, -h - 22], (w, h, d) => [-w - d - 30, -h - 22]];

/** A tag's contents, worded and short, each measured: the airports it covers when more than one, the reserves on call
 * and the crews resting away, with the breakdown by airport as its tooltip. */
function variants(el, members) {
  const n = (f) => members.reduce((a, m) => a + f(m), 0), rest = n((m) => m.s.length), rp = n((m) => m.rp), rc = n((m) => m.rc);
  const codes = members.map((m) => m.code);
  el.title = members.flatMap((m) => [m.rp + m.rc && `${m.code} reserves on call: ${list(m.r.pilot, "pilot")}; ${list(m.r.cabin, "cabin")}`,
    m.s.length && `${m.code}: ${m.s.length} crew${m.s.length > 1 ? "s" : ""} resting in a hotel away from base (${m.s.map((x) => x.unit).join(", ")})`]).filter(Boolean).join("\n");
  const name = codes.length > 1 ? `<em>${esc(codes.length <= 3 ? codes.join(" · ") : `${codes.slice(0, 2).join(" · ")} +${codes.length - 2}`)}</em>` : "";
  const resting = rest ? `<span class="rest">${MOON}<b>${rest}</b></span>` : "";
  const res = (words) => (rp + rc ? `<span class="res"><i>R</i><span>${[rp && `<b>${rp}</b>${words ? " pilot" : "P"}`, rc && `<b>${rc}</b>${words ? " cabin" : "C"}`]
    .filter(Boolean).join(" ")}</span></span>` : "");
  return [...new Set([res(true) + resting, res(false) + resting])].map((chips) => `${name}<div class="cm-row">${chips}</div>`).map((html) => {
    el.innerHTML = html;
    return { html, w: el.offsetWidth, h: el.offsetHeight };
  });
}

const list = (rs, kind) => {
  const n = (type) => rs.filter((r) => r.type === type).length;
  return `${rs.length} ${kind} crew${rs.length === 1 ? "" : "s"} (${n("standby")} on standby at the airport, ${n("short")} on short call)`;
};

const STYLE = `
.cm-tags { position: absolute; inset: 0; z-index: 2; pointer-events: none; overflow: hidden; }
.cm-tag { position: absolute; left: 0; top: 0; display: flex; flex-direction: column; align-items: flex-start; gap: 2px; white-space: nowrap; font: 600 10.5px/1 var(--sans, sans-serif);
  will-change: transform; }
.cm-row { display: flex; gap: 3px; align-items: center; }
.cm-row > span { display: inline-flex; align-items: center; gap: 3px; padding: 2px 5px; border-radius: 4px; box-shadow: 0 1px 2px rgba(10, 30, 45, 0.3);
  pointer-events: auto; cursor: default; }
.cm-tag b { font-weight: 800; font-size: 11.5px; }
.cm-tag .res { background: rgba(255, 255, 255, 0.95); color: #14324a; font-weight: 500; }
.cm-tag .res i { font-style: normal; font-weight: 800; font-size: 9px; padding: 1px 3px; border-radius: 2px; background: #2b7a6a; color: #fff; margin-right: 1px; }
.cm-tag em { font-style: normal; font-weight: 800; font-size: 9px; letter-spacing: 0.03em; color: #14324a; padding: 1px 4px; border-radius: 3px; background: rgba(255, 255, 255, 0.78); }
.cm-leads { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.cm-leads line { stroke: rgba(20, 50, 74, 0.75); stroke-width: 1.2; }
.cm-leads circle { fill: #14324a; }
.cm-tag .rest { background: ${CREW_INK}; color: #2a1446; border: 1px solid rgba(255, 255, 255, 0.9); }
.cm-tag .rest svg { margin-right: 2px; }
.cm-sum { position: absolute; left: 6px; bottom: 6px; z-index: 3; display: flex; gap: 4px; align-items: center; padding: 3px 5px; border-radius: 5px;
  background: rgba(255, 255, 255, 0.82); color: #23323d; font: 500 9.5px/1 var(--sans, sans-serif); white-space: nowrap; pointer-events: none; }
.cm-sum > b { font-weight: 700; font-size: 9px; letter-spacing: 0.06em; text-transform: uppercase; color: #46545e; margin-right: 1px; }
.cm-sum span { display: inline-flex; align-items: center; gap: 2px; }
.cm-sum span b { font-weight: 800; font-size: 10px; }
.cm-sum .rest { padding: 1px 4px; border-radius: 3px; background: ${CREW_INK}; color: #2a1446; }
.cm-sum .rest svg { width: 8px; height: 8px; margin-right: 1px; }
.cm-sum .res i { font-style: normal; font-weight: 800; font-size: 8px; padding: 1px 2px; border-radius: 2px; background: #2b7a6a; color: #fff; margin-right: 1px; }
.cm-sum .ride i { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid ${CREW_INK}; background: rgba(201, 155, 255, 0.25); margin-right: 1px; }
.cm-key { position: absolute; z-index: 3; display: flex; gap: 14px; align-items: center; padding: 3px 10px; border-radius: 6px; font-size: 11px;
  background: rgba(255, 255, 255, 0.85); color: #23323d; pointer-events: none; white-space: nowrap; }
.cm-key span { display: inline-flex; align-items: center; gap: 5px; }
.cm-key i { display: inline-flex; align-items: center; justify-content: center; font-style: normal; }
.cm-key .k-res { padding: 1px 4px; border-radius: 2px; background: #2b7a6a; color: #fff; font-weight: 800; font-size: 9px; }
.cm-key .k-rest { padding: 1px 4px; border-radius: 3px; background: ${CREW_INK}; color: #2a1446; }
.cm-key .k-dh { width: 9px; height: 9px; border-radius: 50%; background: ${CREW_INK}; box-shadow: 0 0 0 1.5px #fff, 0 0 0 2.5px rgba(20, 50, 74, 0.35); }
.view.main .cm-key { left: 50%; transform: translateX(-50%); bottom: calc(var(--tl, 58px) + var(--gap, 14px) * 2 + 64px); }
.view.corner .cm-key { display: none; }
`;

function injectStyle() {
  if (document.getElementById("cm-style")) return;
  const el = document.createElement("style");
  el.id = "cm-style";
  el.textContent = STYLE;
  document.head.appendChild(el);
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
