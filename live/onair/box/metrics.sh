# metrics.sh HH:MM:SS HH:MM:SS: the capture's metrics between two UTC times of the day in its log: the encoder's runs
# with their dropped frames, the fps, speed, CPU, GPU, NVENC, Chrome's memory and frame rate, Chrome restarts.
python3 - "$@" <<'P'
import json, sys
a, b = sys.argv[1], sys.argv[2]
rows = []
for x in open("/var/log/airops-stream/metrics-capture.jsonl"):
    try: r = json.loads(x)
    except ValueError: continue
    if a <= r["t"][11:19] <= b: rows.append(r)
f = lambda k: [float(str(r[k]).strip().rstrip("x")) for r in rows if r.get(k) not in (None, "", "N/A")]
runs, prev = [], None
for r in rows:
    d = int(r.get("ff_drop") or 0)
    if prev is None or d < prev: runs.append([r["t"][11:19], d, d])
    else: runs[-1][2] = d
    prev = d
print(f"{len(rows)} minutes {a}-{b}")
print("encoder runs [start, drop at first sample, drop at last]:", runs)
for k in ("ff_fps", "ff_speed", "cpu_pct", "gpu_pct", "nvenc_pct", "chrome_pss_mb", "page_fps", "load1", "gpu_c"):
    v = f(k)
    if v: print(f"{k}: min {min(v):.2f} mean {sum(v)/len(v):.2f} max {max(v):.2f}")
print("chrome_restarts", rows[-1].get("chrome_restarts") if rows else None)
print("pss by 5 min:", [(r["t"][11:16], r["chrome_pss_mb"]) for r in rows[::5]])
P
