// MapLibre GL JS ships a UMD build only: load the vendored one as a classic script and hand its global to modules.
await new Promise((resolve, reject) => {
  const script = Object.assign(document.createElement("script"), { src: new URL("./maplibre-gl-5.24.0/maplibre-gl.js", import.meta.url).href });
  script.onload = resolve;
  script.onerror = () => reject(new Error(`could not load ${script.src}`));
  document.head.append(script);
});
export default globalThis.maplibregl;
