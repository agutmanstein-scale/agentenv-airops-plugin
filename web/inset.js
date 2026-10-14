// The two views of an episode, the 3D airport and the US map: one fills the screen, the other sits in a corner inset
// under its caption, and a click on the inset swaps them. Without a stage the map is the only view.
import { NetworkMap, radarFrames } from "./map.js";

export class Views {
  constructor(root, P, { stage = null, interactive = true, onSwap = () => {} } = {}) {
    this.root = root;
    this.onSwap = onSwap;
    this.stage = stage;
    this.el = { stage: root.querySelector(".view-stage"), map: root.querySelector(".view-map") };
    this.radar = P.radar ? radarFrames(P.radar, P.base) : null;
    this.map = new NetworkMap(this.el.map.querySelector(".map"), P, { radar: this.radar, interactive });
    this.main = stage ? "stage" : "map";
    this.el.stage.hidden = !stage;
    for (const k of ["stage", "map"]) {
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
    return this.stage ? (this.main === "stage" ? "map" : "stage") : null;
  }

  show(main) {
    if (!this.stage || main === this.main) return;
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
    for (const k of ["stage", "map"]) {
      this.el[k].classList.toggle("main", this.main === k);
      this.el[k].classList.toggle("corner", this.corner === k);
    }
    this.root.dataset.main = this.main;
    this.resize();
  }

  resize() {
    this.map.resize(this.padding());
    this.stage?.resize();
  }
}
