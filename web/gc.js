// Freeing a 3D view's parts as a live day replaces them, keeping what the parts that stay share with them.

/** The geometries, materials and textures under `objs`, and the instanced meshes, whose buffers are their own. */
function owned(objs) {
  const out = new Set();
  for (const root of objs) {
    root.traverse((o) => {
      if (o.isInstancedMesh) out.add(o);
      if (o.geometry) out.add(o.geometry);
      for (const m of [].concat(o.material || [])) {
        out.add(m);
        for (const v of Object.values(m)) if (v?.isTexture) out.add(v);
        for (const u of Object.values(m.uniforms || {})) if (u?.value?.isTexture) out.add(u.value);
      }
    });
  }
  return out;
}

/** Free what `old` holds that nothing under `keep` uses (module-level kits and materials are shared across builds). */
export function freed(old, keep) {
  const kept = owned(keep);
  for (const r of owned(old)) if (!kept.has(r)) r.dispose?.();
}
