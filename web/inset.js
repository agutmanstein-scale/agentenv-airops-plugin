// The views of an episode, the 3D airport, the US map and, on a control-center day, the 3D control-center room: one
// fills the screen and another sits in a corner inset under its caption (the map, or with the map in front the 3D view
// last shown), and a click on the inset swaps them. Without a 3D view the map is the only view. A 3D view off screen
// keeps its size, out of sight.
import { NetworkMap, radarFrames } from "./map.js";

const KINDS = ["stage", "room", "map"];

export class Views {
  constructor(root, P, { stage = null, room = null, interactive = true, onSwap = () => {} } = {}) {
    this.root = root;
    this.onSwap = onSwap;
    this.stage = stage;
    this.room = room;
    this.el = { stage: root.querySelector(".view-stage"), room: root.querySelector(".view-room"), map: root.querySelector(".view-map") };
    this.radar = P.radar ? radarFrames(P.radar, P.base) : null;
    this.map = new NetworkMap(this.el.map.querySelector(".map"), P, { radar: this.radar, interactive });
    this.scene = stage ? "stage" : room ? "room" : null;
    this.main = this.scene ?? "map";
    for (const k of ["stage", "room"]) if (this.el[k]) this.el[k].hidden = !this[k];
    for (const k of KINDS) {
      if (!this.el[k]) continue;
      for (const type of ["pointerdown", "pointerup", "mousedown", "wheel", "click"]) {
        this.el[k].addEventListener(type, (e) => {
          if (this.corner !== k) return;
          e.stopPropagation();
          if (type === "click") this.show(k);
        }, { capture: true, passive: type !== "click" });
      }
    }
    this.layout();
  }

  get corner() {
    if (!this.scene) return null;
    return this.main === "map" ? this.scene : "map";
  }

  /** Whether a view is on screen, in front or in the corner. */
  visible(kind) {
    return this.main === kind || this.corner === kind;
  }

  show(main) {
    if (main === this.main || !this.el[main] || (main !== "map" && !this[main])) return;
    if (main !== "map") this.scene = main;
    this.main = main;
    this.layout();
    this.onSwap(main);
  }

  swap() {
    this.show(this.corner ?? this.main);
  }

  padding() {
    const big = this.main === "map", w = innerWidth;
    return big ? { top: 70, bottom: 120, left: w > 1200 ? 500 : 30, right: w > 1200 ? 470 : 30 } : { top: 26, bottom: 10, left: 10, right: 10 };
  }

  layout() {
    for (const k of KINDS) {
      if (!this.el[k]) continue;
      this.el[k].classList.toggle("main", this.main === k);
      this.el[k].classList.toggle("corner", this.corner === k);
      this.el[k].classList.toggle("behind", !this.visible(k));
    }
    this.root.dataset.main = this.main;
    this.resize();
  }

  resize() {
    this.map.resize(this.padding());
    this.stage?.resize();
    this.room?.resize();
  }
}
