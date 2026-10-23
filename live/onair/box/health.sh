# health.sh: one JSON line: the stream's picture against Chrome's screenshot of the page (mean difference at 96x54 grey:
# about 1 while the encoder sees the page, tens on a stale screen), the runner, the live server, and the last capture
# and push metrics.
python3 - <<'PY'
import json, urllib.request, base64, subprocess, threading, websocket, time
def raw(args):
    return subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", *args, "-vf", "scale=96:54,format=gray", "-f", "rawvideo", "-"], capture_output=True, timeout=40).stdout
shot = {}
def page():
    t = [x for x in json.load(urllib.request.urlopen("http://127.0.0.1:9222/json")) if x.get("type") == "page"]
    ws = websocket.create_connection(t[0]["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == 1: break
    open("/tmp/h-page.png", "wb").write(base64.b64decode(m["result"]["data"]))
th = threading.Thread(target=page); th.start()
relay = raw(["-i", "rtmp://127.0.0.1:1935/airops", "-frames:v", "1"])
th.join()
pg = raw(["-i", "/tmp/h-page.png"])
diff = round(sum(abs(a - b) for a, b in zip(relay, pg)) / max(1, len(pg)), 1) if relay and pg and len(relay) == len(pg) else None
cur = json.load(open("/home/ubuntu/live/current.json"))
st = json.loads(urllib.request.urlopen("http://127.0.0.1:8765/live/state.json", timeout=10).read())["live"]
def last(f):
    try: return json.loads(open(f).read().strip().splitlines()[-1])
    except Exception: return {}
cap, push = last("/var/log/airops-stream/metrics-capture.jsonl"), last("/var/log/airops-stream/metrics-push.jsonl")
print(json.dumps({"utc": time.strftime("%H:%M:%S", time.gmtime()), "picture_vs_page": diff,
  "runner": {k: cur.get(k) for k in ("status", "day", "date", "hub", "spent_usd", "live_usd", "final")},
  "next": (cur.get("next") or {}).get("starts_utc"),
  "server": {k: st.get(k) for k in ("status", "watch", "watches", "lag", "exact", "mismatches", "error")},
  "capture": {k: cap.get(k) for k in ("t", "page_fps", "ff_fps", "ff_drop", "ff_speed", "chrome_pss_mb", "chrome_restarts", "ffmpeg", "cpu_pct", "gpu_c")},
  "push": {k: push.get(k) for k in ("t", "target", "bitrate", "fps", "speed", "reconnects", "restarts") if k in push} or push}))
PY
