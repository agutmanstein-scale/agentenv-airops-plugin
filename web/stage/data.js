import * as THREE from "three";

/** A JSON document, gzip or not, by URL. */
export async function loadJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const buf = new Uint8Array(await res.arrayBuffer());
  if (buf[0] !== 0x1f || buf[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(buf));
  const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

export const toScene = ([x, y], h = 0) => new THREE.Vector3(x, h, -y);
export const headingToRotY = (deg) => -THREE.MathUtils.degToRad(deg);
