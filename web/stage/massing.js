const RES = 2.5;
const PIER = 46;

/** A terminal's massing from its footprint alone: the local width of every point (the widest disc inside the outline that covers it) splits low piers from the tall halls, and the field it comes from shapes the roofs. */
export function massing(terminals) {
  const all = terminals.flatMap((t) => [t.outer, ...(t.holes || [])]).flat();
  const x0 = Math.min(...all.map((p) => p[0])) - 10;
  const y0 = Math.min(...all.map((p) => p[1])) - 10;
  const nx = Math.ceil((Math.max(...all.map((p) => p[0])) + 10 - x0) / RES);
  const ny = Math.ceil((Math.max(...all.map((p) => p[1])) + 10 - y0) / RES);
  const field = { x0, y0, nx, ny, res: RES, edge: new Float32Array(nx * ny), width: new Float32Array(nx * ny), ridge: new Uint8Array(nx * ny) };
  const blocks = [];
  for (const t of terminals) {
    const inside = raster(t, field);
    const d = distance(inside, field);
    const w = thickness(d, field);
    const tall = t.height || 20;
    const low = Math.min(tall, Math.max(11, Math.min(15, tall * 0.62)));
    for (let k = 0; k < d.length; k++) {
      if (!inside[k]) continue;
      field.edge[k] = Math.max(field.edge[k], d[k]);
      field.width[k] = Math.max(field.width[k], w[k]);
      const i = k % nx;
      const j = (k / nx) | 0;
      const ge = (di, dj) => d[k] >= d[(j + dj) * nx + i + di] - 0.05 && d[k] >= d[(j - dj) * nx + i - di] - 0.05;
      if (d[k] > 4 && (ge(1, 0) || ge(0, 1) || ge(1, 1) || ge(1, -1))) field.ridge[k] = 1;
    }
    blocks.push({ outer: t.outer, holes: t.holes || [], top: tall > low + 2 ? low : tall, kind: "pier" });
    if (tall > low + 2) {
      const hall = new Uint8Array(d.length);
      let n = 0;
      for (let k = 0; k < d.length; k++) if (inside[k] && w[k] >= PIER) {
        hall[k] = 1;
        n++;
      }
      if (n * RES * RES > 1500) for (const ring of contours(hall, field)) blocks.push({ ...ring, top: tall, kind: "hall" });
    }
  }
  return { blocks, field };
}

function raster(t, f) {
  const out = new Uint8Array(f.nx * f.ny);
  const rings = [t.outer, ...(t.holes || [])];
  for (let j = 0; j < f.ny; j++) {
    const y = f.y0 + (j + 0.5) * f.res;
    const xs = [];
    for (const r of rings) {
      for (let a = 0, b = r.length - 1; a < r.length; b = a++) {
        const p = r[a];
        const q = r[b];
        if (p[1] > y !== q[1] > y) xs.push(p[0] + ((y - p[1]) * (q[0] - p[0])) / (q[1] - p[1]));
      }
    }
    xs.sort((a, b) => a - b);
    for (let s = 0; s + 1 < xs.length; s += 2) {
      const i0 = Math.max(0, Math.ceil((xs[s] - f.x0) / f.res - 0.5));
      const i1 = Math.min(f.nx - 1, Math.floor((xs[s + 1] - f.x0) / f.res - 0.5));
      for (let i = i0; i <= i1; i++) out[j * f.nx + i] = 1;
    }
  }
  return out;
}

/** Metres from each inside cell to the outline, by a two-pass chamfer. */
function distance(inside, f) {
  const { nx, ny, res } = f;
  const d = new Float32Array(nx * ny);
  for (let k = 0; k < d.length; k++) d[k] = inside[k] ? 1e9 : 0;
  const a = res;
  const b = res * Math.SQRT2;
  const relax = (k, k2, c) => {
    if (d[k2] + c < d[k]) d[k] = d[k2] + c;
  };
  for (let j = 1; j < ny - 1; j++) {
    for (let i = 1; i < nx - 1; i++) {
      const k = j * nx + i;
      if (!d[k]) continue;
      relax(k, k - 1, a);
      relax(k, k - nx, a);
      relax(k, k - nx - 1, b);
      relax(k, k - nx + 1, b);
    }
  }
  for (let j = ny - 2; j > 0; j--) {
    for (let i = nx - 2; i > 0; i--) {
      const k = j * nx + i;
      if (!d[k]) continue;
      relax(k, k + 1, a);
      relax(k, k + nx, a);
      relax(k, k + nx + 1, b);
      relax(k, k + nx - 1, b);
    }
  }
  for (let k = 0; k < d.length; k++) if (d[k] > 1e8) d[k] = 0;
  return d;
}

/** Twice the radius of the widest inscribed disc covering each cell, painted from the ridge of the distance field. */
function thickness(d, f) {
  const { nx, ny, res } = f;
  const w = new Float32Array(nx * ny);
  for (let j = 1; j < ny - 1; j++) {
    for (let i = 1; i < nx - 1; i++) {
      const k = j * nx + i;
      const r = d[k];
      if (r <= 0) continue;
      if (r < d[k - 1] || r < d[k + 1] || r < d[k - nx] || r < d[k + nx]) continue;
      const c = Math.ceil(r / res);
      const v = 2 * r;
      for (let dj = -c; dj <= c; dj++) {
        const jj = j + dj;
        if (jj < 0 || jj >= ny) continue;
        const span = Math.floor(Math.sqrt(Math.max(0, c * c - dj * dj)));
        for (let ii = Math.max(0, i - span); ii <= Math.min(nx - 1, i + span); ii++) {
          const kk = jj * nx + ii;
          if (d[kk] > 0 && w[kk] < v) w[kk] = v;
        }
      }
    }
  }
  return w;
}

/** Outlines of a cell mask by marching squares, simplified, as {outer, holes} rings in ENU metres. */
function contours(mask, f) {
  const { nx, ny, res, x0, y0 } = f;
  const at = (i, j) => (i >= 0 && j >= 0 && i < nx && j < ny ? mask[j * nx + i] : 0);
  const P = (i, j) => [x0 + (i + 0.5) * res, y0 + (j + 0.5) * res];
  const segs = new Map();
  const key = (p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`;
  const push = (a, b) => segs.set(key(a), [a, b]);
  for (let j = -1; j < ny; j++) {
    for (let i = -1; i < nx; i++) {
      const c = (at(i, j) ? 1 : 0) | (at(i + 1, j) ? 2 : 0) | (at(i + 1, j + 1) ? 4 : 0) | (at(i, j + 1) ? 8 : 0);
      if (c === 0 || c === 15) continue;
      const [a, b, cc, d] = [P(i, j), P(i + 1, j), P(i + 1, j + 1), P(i, j + 1)];
      const mid = (p, q) => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
      const S = mid(a, b);
      const E = mid(b, cc);
      const N = mid(cc, d);
      const W = mid(d, a);
      const table = { 1: [[W, S]], 2: [[S, E]], 3: [[W, E]], 4: [[E, N]], 5: [[W, N], [E, S]], 6: [[S, N]], 7: [[W, N]], 8: [[N, W]], 9: [[N, S]], 10: [[N, E], [S, W]], 11: [[N, E]], 12: [[E, W]], 13: [[E, S]], 14: [[S, W]] };
      for (const [p, q] of table[c]) push(p, q);
    }
  }
  const rings = [];
  while (segs.size) {
    const [k0, first] = segs.entries().next().value;
    segs.delete(k0);
    const ring = [first[0]];
    let cur = first[1];
    for (let guard = 0; guard < 100000; guard++) {
      ring.push(cur);
      const nk = key(cur);
      const nxt = segs.get(nk);
      if (!nxt) break;
      segs.delete(nk);
      cur = nxt[1];
    }
    if (ring.length > 6) {
      let far = 0;
      for (let i = 1; i < ring.length; i++) if (Math.hypot(ring[i][0] - ring[0][0], ring[i][1] - ring[0][1]) > Math.hypot(ring[far][0] - ring[0][0], ring[far][1] - ring[0][1])) far = i;
      const half = (a, b) => simplify(ring.slice(a, b + 1), 1.6);
      rings.push(smooth([...half(0, far).slice(0, -1), ...half(far, ring.length - 1)]));
    }
  }
  const area = (r) => r.reduce((s, p, i) => s + (r[(i + 1) % r.length][0] - p[0]) * (r[(i + 1) % r.length][1] + p[1]), 0) / 2;
  const big = rings.filter((r) => Math.abs(area(r)) > 600).sort((a, b) => Math.abs(area(b)) - Math.abs(area(a)));
  const out = [];
  for (const r of big) {
    const host = out.find((o) => inside(r[0], o.outer));
    if (host) host.holes.push(r);
    else out.push({ outer: r, holes: [] });
  }
  return out;
}

function simplify(p, tol) {
  if (p.length < 4) return p;
  const keep = new Uint8Array(p.length);
  keep[0] = keep[p.length - 1] = 1;
  const stack = [[0, p.length - 1]];
  while (stack.length) {
    const [a, b] = stack.pop();
    const dx = p[b][0] - p[a][0];
    const dy = p[b][1] - p[a][1];
    const L = Math.hypot(dx, dy) || 1;
    let best = -1;
    let bd = tol;
    for (let i = a + 1; i < b; i++) {
      const d = Math.abs((p[i][0] - p[a][0]) * dy - (p[i][1] - p[a][1]) * dx) / L;
      if (d > bd) {
        bd = d;
        best = i;
      }
    }
    if (best > 0) {
      keep[best] = 1;
      stack.push([a, best], [best, b]);
    }
  }
  const out = p.filter((_, i) => keep[i]);
  if (out.length > 1 && Math.hypot(out[0][0] - out[out.length - 1][0], out[0][1] - out[out.length - 1][1]) < 0.01) out.pop();
  return out;
}

function smooth(r) {
  const out = [];
  for (let i = 0; i < r.length; i++) {
    const a = r[i];
    const b = r[(i + 1) % r.length];
    out.push([a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25], [a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75]);
  }
  return out;
}

function inside(p, ring) {
  let c = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i];
    const b = ring[j];
    if (a[1] > p[1] !== b[1] > p[1] && p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]) c = !c;
  }
  return c;
}
