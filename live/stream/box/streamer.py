#!/usr/bin/env python3
"""The AirOpsEnv live streamer on one box. `capture` keeps PulseAudio, Chrome on the viewer URL and an ffmpeg that
encodes the X display and the page's sound (over an airport bed, AIROPS_BED, ducked under the page and heard only
while the page is silent: a reload, a Chrome restart) into the local relay (mediamtx) running, and restarts whatever
dies, hangs or grows too big; `push` forwards the relay to the RTMP target (X's server and key from SSM, or any URL)
and reconnects with backoff. Both log a metrics line a minute to the journal and to AIROPS_LOG_DIR; neither prints
the stream key."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import websocket

E = os.environ
DISPLAY = E.get("AIROPS_DISPLAY", ":0")
SIZE = E.get("AIROPS_SIZE", "1920x1080")
FPS = int(E.get("AIROPS_FPS", "30"))
VBITRATE = E.get("AIROPS_VBITRATE", "6000k")
ABITRATE = E.get("AIROPS_ABITRATE", "128k")
RELAY = E.get("AIROPS_RELAY", "rtmp://127.0.0.1:1935/airops")
URL = E.get("AIROPS_URL", "")
TARGET = E.get("AIROPS_TARGET", "none")
SSM_SERVER = E.get("AIROPS_SSM_SERVER", "/airops-live/x-server")
SSM_KEY = E.get("AIROPS_SSM_KEY", "/airops-live/x-key")
SAFE_URL = E.get("AIROPS_SAFE_URL", "")
MEM_SOFT_MB = int(E.get("AIROPS_MEM_SOFT_MB", "6000"))
MEM_HARD_MB = int(E.get("AIROPS_MEM_HARD_MB", "9000"))
RESTART_HOURS = float(E.get("AIROPS_RESTART_HOURS", "0"))
CHROME_FLAGS = E.get("AIROPS_CHROME_FLAGS", "").split()
STATE = Path(E.get("AIROPS_STATE_DIR", "/var/lib/airops-stream"))
LOGS = Path(E.get("AIROPS_LOG_DIR", "/var/log/airops-stream"))
REC = Path(E.get("AIROPS_REC_DIR", str(STATE / "rec")))
MIN_FREE_GB = float(E.get("AIROPS_MIN_FREE_GB", "8"))
CDP_PORT = int(E.get("AIROPS_CDP_PORT", "9222"))
PAGE_FPS = int(E.get("AIROPS_PAGE_FPS", "0"))
SINK = "broadcast"
BED = Path(E.get("AIROPS_BED", "/opt/airops-stream/bed.ogg"))
BED_DB = float(E.get("AIROPS_BED_DB", "-6"))
FMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
FRAMES = """(() => { if (window.__airopsFrames !== undefined) return;
  window.__airopsFrames = 0; window.__airopsLost = 0;
  const f = () => { window.__airopsFrames++; requestAnimationFrame(f); }; requestAnimationFrame(f);
  const get = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, ...rest) {
    const ctx = get.call(this, type, ...rest);
    if (ctx && /webgl/.test(type) && !this.__airopsWatched) {
      this.__airopsWatched = true;
      this.addEventListener("webglcontextlost", () => window.__airopsLost++);
    }
    return ctx;
  }; })()"""
THROTTLE = """(() => { if (window.__airopsThrottle) return; window.__airopsThrottle = %d;
  const raf = window.requestAnimationFrame.bind(window), gap = 1000 / %d - 4;
  let queue = new Map(), id = 0, last = 0, pending = false;
  const pump = (t) => { if (t - last < gap) { raf(pump); return; } last = t; pending = false;
    const run = queue; queue = new Map(); for (const cb of run.values()) cb(t); };
  window.requestAnimationFrame = (cb) => { queue.set(++id, cb);
    if (!pending) { pending = true; raf(pump); } return id; };
  window.cancelAnimationFrame = (i) => { queue.delete(i); }; })()"""
PROBE = """JSON.stringify({f: window.__airopsFrames || 0, err: !!document.querySelector('.error'),
  lost: window.__airopsLost || 0,
  ready: !!(window.airops && window.airops.state && window.airops.state().view),
  heap: performance.memory ? performance.memory.usedJSHeapSize : 0})"""
SECRETS: dict[str, str] = {}


def log(*parts) -> None:
    line = " ".join(str(p) for p in parts)
    for secret, name in SECRETS.items():
        line = line.replace(secret, f"<{name}>")
    print(line, flush=True)


def metrics(mode: str, row: dict) -> None:
    row = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}
    log("metrics", json.dumps(row))
    with open(LOGS / f"metrics-{mode}.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")


class Backoff:
    """Seconds to wait before the next restart: doubling from `lo` to `hi`, back to `lo` once a run lasted `healthy`."""

    def __init__(self, lo: float = 1, hi: float = 60, healthy: float = 120):
        self.lo, self.hi, self.healthy, self.cur, self.since = lo, hi, healthy, lo, None

    def started(self) -> None:
        self.since = time.monotonic()

    def failed(self) -> float:
        if self.since is not None and time.monotonic() - self.since >= self.healthy:
            self.cur = self.lo
        delay, self.cur, self.since = self.cur, min(self.hi, self.cur * 2), None
        return delay


class Ffmpeg:
    """An ffmpeg whose `-progress` blocks are parsed as they come and whose warnings go to the log, redacted. It counts
    as progressing only while its frame count moves: x11grab on a display that went away keeps ffmpeg alive and
    reporting, on the sound alone."""

    def __init__(self, cmd: list[str], env: dict, name: str):
        self.name, self.progress, self.last, self.mark = name, {}, time.monotonic(), None
        self.proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True)
        threading.Thread(target=self._read_progress, daemon=True).start()
        threading.Thread(target=self._relay_stderr, daemon=True).start()

    def _read_progress(self) -> None:
        block = {}
        for raw in iter(self.proc.stdout.readline, b""):
            k, _, v = raw.decode(errors="replace").strip().partition("=")
            block[k] = v
            if k == "progress":
                mark = block.get("frame") or block.get("out_time_us")
                if mark != self.mark:
                    self.mark, self.last = mark, time.monotonic()
                self.progress, block = block, {}

    def _relay_stderr(self) -> None:
        for raw in iter(self.proc.stderr.readline, b""):
            log(f"[{self.name}]", raw.decode(errors="replace").rstrip())

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stalled(self, seconds: float) -> bool:
        return time.monotonic() - self.last > seconds

    def stop(self) -> None:
        stop_group(self.proc)


def stop_group(proc: subprocess.Popen | None, grace: float = 10) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(grace)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


class Cdp:
    def __init__(self, port: int, timeout: float = 10):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout) as r:
            page = next(t for t in json.load(r) if t["type"] == "page" and not t["url"].startswith("devtools"))
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True, timeout=timeout)
        self.n = 0

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m["result"]

    def eval(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("text"))
        return r["result"].get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except OSError:
            pass


class Chrome:
    """Chrome in kiosk mode on the viewer URL, a fresh profile each start, with a frame counter injected into every
    document so a hung or crashed page shows as frames that stop advancing."""

    def __init__(self, env: dict):
        self.env, self.proc, self.cdp, self.backoff = env, None, None, Backoff(2, 60, 300)
        self.restarts, self.frames, self.heap = 0, 0, 0
        self.started = self.progressed = self.reloaded = self.answered = 0.0
        self.lost_since = None
        self.profile = STATE / "chrome"

    def start(self) -> None:
        w, h = SIZE.split("x")
        shutil.rmtree(self.profile, ignore_errors=True)
        cmd = ["google-chrome", f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
               "--noerrdialogs", "--disable-infobars", "--hide-scrollbars", "--disable-session-crashed-bubble",
               "--disable-component-update", "--password-store=basic", "--disable-features=Translate,MediaRouter",
               "--kiosk", "--window-position=0,0", f"--window-size={w},{h}",
               "--autoplay-policy=no-user-gesture-required", "--disable-background-timer-throttling",
               "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
               "--ignore-gpu-blocklist", "--enable-gpu-rasterization", "--enable-zero-copy",
               f"--remote-debugging-port={CDP_PORT}", *CHROME_FLAGS, "about:blank"]
        self.proc = subprocess.Popen(cmd, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        self.started = self.progressed = self.answered = time.monotonic()
        self.frames = 0
        for _ in range(60):
            time.sleep(0.5)
            try:
                self.attach()
                break
            except (OSError, RuntimeError, StopIteration, ValueError, websocket.WebSocketException):
                self.cdp = None
        if self.cdp is None:
            log("chrome: DevTools never answered")
            return
        self.cdp.call("Page.navigate", url=URL)
        self.backoff.started()
        log(f"chrome: started (pid {self.proc.pid}) on {URL}")

    def attach(self) -> None:
        """A DevTools session that injects the frame counter into this document and every later one (an injected
        script lasts as long as the session that added it, and Chrome injects it only with the Page domain enabled)."""
        self.cdp = Cdp(CDP_PORT, timeout=5)
        self.cdp.call("Page.enable")
        for script in ([THROTTLE % (PAGE_FPS, PAGE_FPS)] if PAGE_FPS else []) + [FRAMES]:
            self.cdp.call("Page.addScriptToEvaluateOnNewDocument", source=script)
            self.cdp.eval(script)

    def stop(self, why: str) -> None:
        log(f"chrome: stopping ({why})")
        if self.cdp:
            self.cdp.close()
        self.cdp = None
        stop_group(self.proc)
        subprocess.run(["pkill", "-KILL", "-f", f"--user-data-dir={self.profile}"], capture_output=True)
        self.proc = None

    def restart(self, why: str) -> None:
        self.stop(why)
        self.restarts += 1
        delay = self.backoff.failed()
        log(f"chrome: restart #{self.restarts} in {delay:.0f} s")
        time.sleep(delay)
        self.start()

    def pss_mb(self) -> int:
        """The proportional set size of Chrome's processes, in MB (RSS would count shared pages once a process)."""
        total = 0
        for pid in (p for p in os.listdir("/proc") if p.isdigit()):
            try:
                if str(self.profile).encode() not in Path(f"/proc/{pid}/cmdline").read_bytes():
                    continue
                for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
                    if line.startswith("Pss:"):
                        total += int(line.split()[1])
            except OSError:
                pass
        return total // 1024

    def check(self) -> None:
        now = time.monotonic()
        if self.proc is None or self.proc.poll() is not None:
            self.restart(f"exited with {None if self.proc is None else self.proc.returncode}")
            return
        try:
            if self.cdp is None:
                self.attach()
            p = json.loads(self.cdp.eval(PROBE))
            self.answered = now
        except (OSError, RuntimeError, StopIteration, ValueError, TypeError, websocket.WebSocketException) as e:
            if self.cdp:
                self.cdp.close()
            self.cdp = None
            log(f"chrome: probe failed: {type(e).__name__} {e}")
            if now - self.answered > 45 and now - max(self.started, self.reloaded) > 90:
                self.restart("not answering for 45 s")
            return
        if p["f"] > self.frames or p["f"] < self.frames // 2:
            self.progressed = now
        self.frames, self.heap = p["f"], p["heap"]
        self.lost_since = (self.lost_since or now) if p["lost"] > 0 else None
        if self.lost_since and now - self.lost_since > 5:
            log("chrome: a WebGL context was lost (the GPU process restarted?); reloading")
            self.lost_since = None
            self.reload()
        elif p["err"] and now - self.reloaded > 30:
            log("chrome: the viewer shows an error; reloading")
            self.reload()
        elif now - self.progressed > 20 and now - self.started > 60:
            if now - self.reloaded > 90:
                log("chrome: no frames for 20 s; reloading")
                self.reload()
            else:
                self.restart("no frames after a reload")

    def reload(self) -> None:
        self.reloaded = self.progressed = time.monotonic()
        try:
            self.cdp.call("Page.navigate", url=URL)
        except (OSError, RuntimeError, AttributeError, websocket.WebSocketException) as e:
            log(f"chrome: reload failed: {e}")


def safe_now() -> bool:
    """Whether a restart would not cut a day short: SAFE_URL's status (or live.status) is intermission or done."""
    if not SAFE_URL:
        return True
    try:
        with urllib.request.urlopen(SAFE_URL, timeout=5) as r:
            d = json.load(r)
    except (OSError, ValueError):
        return False
    return (d.get("status") or (d.get("live") or {}).get("status")) in ("intermission", "done")


def start_pulse(env: dict) -> subprocess.Popen:
    """PulseAudio with one null sink as the default output: Chrome plays into it, ffmpeg reads its monitor."""
    Path(env["XDG_RUNTIME_DIR"]).mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(["pkill", "-u", str(os.getuid()), "-x", "pulseaudio"], capture_output=True)
    proc = subprocess.Popen(
        ["pulseaudio", "--daemonize=no", "--exit-idle-time=-1", "--disallow-exit", "-n",
         "--load=module-native-protocol-unix",
         f"--load=module-null-sink sink_name={SINK} rate=48000 sink_properties=device.description=Broadcast"],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    for _ in range(50):
        if not subprocess.run(["pactl", "set-default-sink", SINK], env=env, capture_output=True).returncode:
            log(f"pulse: started (pid {proc.pid})")
            return proc
        time.sleep(0.2)
    stop_group(proc)
    raise RuntimeError("PulseAudio did not start")


def x_server() -> str:
    """The X server's pid from its lock file: x11grab keeps reading the old server's shared memory after a restart."""
    try:
        return Path(f"/tmp/.X{DISPLAY.lstrip(':').split('.')[0]}-lock").read_text().strip()
    except OSError:
        return ""


def encoder_cmd() -> list[str]:
    rate = int(VBITRATE.rstrip("k"))
    audio = ["-map", "1:a", "-af", "aresample=async=1000"]
    if BED.is_file():
        audio = ["-filter_complex",
                 f"[1:a]aresample=48000:async=1000,{FMT},asplit=2[page][key];"
                 f"amovie=filename={BED}:loop=0,aresample=48000,{FMT},volume={BED_DB}dB[bed];"
                 "[bed][key]sidechaincompress=threshold=0.003:ratio=20:attack=20:release=1500[duck];"
                 f"[duck]{FMT}[bf];[page][bf]amerge=inputs=2,pan=stereo|c0=c0+c2|c1=c1+c3[a]", "-map", "[a]"]
    return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostats", "-progress", "pipe:1", "-stats_period", "5",
            "-thread_queue_size", "1024", "-f", "x11grab", "-framerate", str(FPS), "-video_size", SIZE,
            "-draw_mouse", "0", "-i", f"{DISPLAY}.0",
            "-thread_queue_size", "1024", "-f", "pulse", "-i", f"{SINK}.monitor",
            "-map", "0:v", *audio, "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-profile:v", "high",
            "-rc", "cbr", "-bf", "0", "-spatial-aq", "1", "-pix_fmt", "yuv420p",
            "-b:v", VBITRATE, "-maxrate", VBITRATE, "-bufsize", f"{2 * rate}k", "-g", str(2 * FPS),
            "-keyint_min", str(2 * FPS),
            "-c:a", "aac", "-b:a", ABITRATE, "-ar", "44100", "-ac", "2", "-f", "flv", RELAY]


class Cpu:
    def __init__(self):
        self.prev = self._read()

    @staticmethod
    def _read() -> tuple[int, int]:
        f = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
        return sum(f), f[3] + f[4]

    def percent(self) -> float:
        cur = self._read()
        total, idle = cur[0] - self.prev[0], cur[1] - self.prev[1]
        self.prev = cur
        return round(100 * (1 - idle / total), 1) if total else 0.0


def gpu() -> dict:
    try:
        out = subprocess.run(["nvidia-smi",
                              "--query-gpu=utilization.gpu,utilization.encoder,memory.used,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        u, enc, mem, temp = (x.strip() for x in out.split(","))
        return {"gpu_pct": int(u), "nvenc_pct": int(enc), "gpu_mem_mb": int(mem), "gpu_c": int(temp)}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def guard_disk() -> None:
    """Deletes the oldest recordings while the recording disk has less than AIROPS_MIN_FREE_GB free."""
    while REC.exists() and shutil.disk_usage(REC).free < MIN_FREE_GB * 2**30:
        files = sorted((p for p in REC.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
        if len(files) < 2:
            return
        log(f"disk: under {MIN_FREE_GB} GB free; deleting {files[0]}")
        files[0].unlink()


def capture() -> int:
    if not URL:
        log("AIROPS_URL is not set")
        return 2
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {**E, "DISPLAY": DISPLAY, "XDG_RUNTIME_DIR": str(STATE / "pulse-runtime")}
    pulse, chrome, ff = start_pulse(env), Chrome(env), None
    ff_backoff, restarts, cpu = Backoff(1, 30, 120), {"pulse": 0, "ffmpeg": 0}, Cpu()
    chrome.start()
    up, next_metrics, next_mem, next_disk, last_frames = time.monotonic(), 0.0, 0.0, 0.0, (0, time.monotonic())
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    try:
        while not stopping:
            time.sleep(2)
            now = time.monotonic()
            if pulse.poll() is not None:
                restarts["pulse"] += 1
                log("pulse: exited; restarting it and ffmpeg")
                pulse = start_pulse(env)
                if ff:
                    ff.stop()
            if ff is None or not ff.alive() or ff.stalled(20) or ff.x != x_server():
                if ff is not None:
                    why = f"exited with {ff.proc.returncode}" if not ff.alive() else \
                        "stalled" if ff.stalled(20) else "the X server changed"
                    ff.stop()
                    restarts["ffmpeg"] += 1
                    delay = ff_backoff.failed()
                    log(f"ffmpeg: {why}; restart #{restarts['ffmpeg']} in {delay:.0f} s")
                    time.sleep(delay)
                ff = Ffmpeg(encoder_cmd(), env, "encode")
                ff.x = x_server()
                ff_backoff.started()
                log(f"ffmpeg: encoding {SIZE} at {FPS} fps, {VBITRATE} NVENC to {RELAY}")
            chrome.check()
            if now >= next_mem:
                next_mem = now + 30
                pss = chrome.pss_mb()
                aged = RESTART_HOURS and now - chrome.started > RESTART_HOURS * 3600
                if pss > MEM_HARD_MB:
                    chrome.restart(f"memory {pss} MB over the hard limit {MEM_HARD_MB} MB")
                elif (pss > MEM_SOFT_MB or aged) and safe_now():
                    chrome.restart(f"memory {pss} MB" if pss > MEM_SOFT_MB else f"up {RESTART_HOURS} h")
            if now >= next_disk:
                next_disk = now + 300
                guard_disk()
            if now >= next_metrics:
                next_metrics = now + 60
                f0, t0 = last_frames
                page_fps = round((chrome.frames - f0) / (now - t0), 1) if chrome.frames >= f0 and now > t0 else None
                last_frames = (chrome.frames, now)
                p = ff.progress
                metrics("capture", {"up_s": round(now - up), "page_fps": page_fps, "heap_mb": chrome.heap // 2**20,
                                    "chrome_pss_mb": chrome.pss_mb(), "chrome_restarts": chrome.restarts,
                                    "ff_frame": p.get("frame"), "ff_fps": p.get("fps"), "ff_drop": p.get("drop_frames"),
                                    "ff_dup": p.get("dup_frames"), "ff_speed": p.get("speed"),
                                    "ff_bitrate": p.get("bitrate"), **restarts, "cpu_pct": cpu.percent(),
                                    "load1": round(os.getloadavg()[0], 2), "mem_avail_mb": mem_available_mb(), **gpu(),
                                    "rec_free_gb": round(shutil.disk_usage(REC if REC.exists() else STATE).free
                                                         / 2**30, 1)})
    finally:
        log("capture: stopping")
        if ff:
            ff.stop()
        chrome.stop("service stopping")
        stop_group(pulse)
    return 0


def target_url() -> str | None:
    if TARGET == "none":
        return None
    if TARGET != "ssm":
        return TARGET
    import boto3
    ssm = boto3.client("ssm", region_name=E.get("AWS_DEFAULT_REGION", "us-west-2"))
    server = ssm.get_parameter(Name=SSM_SERVER, WithDecryption=True)["Parameter"]["Value"].strip()
    key = ssm.get_parameter(Name=SSM_KEY, WithDecryption=True)["Parameter"]["Value"].strip()
    SECRETS[key] = "stream key"
    return f"{server.rstrip('/')}/{key}"


def relay_ready() -> bool:
    name = RELAY.rsplit("/", 1)[-1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:9997/v3/paths/get/{name}", timeout=5) as r:
            return bool(json.load(r).get("ready"))
    except (OSError, ValueError):
        return False


def push() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
    backoff, restarts, ff, up, next_metrics, url = Backoff(1, 30, 60), 0, None, time.monotonic(), 0.0, None
    while not stopping:
        if url is None:
            try:
                url = target_url()
            except Exception as e:
                log(f"push: cannot read the target from SSM ({type(e).__name__}); retrying in 30 s")
                time.sleep(30)
                continue
            if url is None:
                log("push: AIROPS_TARGET is none; not pushing")
                while not stopping:
                    time.sleep(5)
                break
            shown = TARGET if TARGET != "ssm" else f"X ({SSM_SERVER} + {SSM_KEY})"
            SECRETS[url] = shown
        if not relay_ready():
            log(f"push: waiting for {RELAY}")
            while not stopping and not relay_ready():
                time.sleep(1)
            continue
        ff = Ffmpeg(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostats", "-progress", "pipe:1",
                     "-stats_period", "5", "-rw_timeout", "15000000", "-i", RELAY, "-map", "0", "-c", "copy",
                     "-f", "flv", "-flvflags", "no_duration_filesize", url], dict(E), "push")
        backoff.started()
        log(f"push: forwarding {RELAY} to {shown}")
        while not stopping and ff.alive() and not ff.stalled(30):
            time.sleep(2)
            now = time.monotonic()
            if now >= next_metrics:
                next_metrics = now + 60
                p = ff.progress
                metrics("push", {"up_s": round(now - up), "restarts": restarts, "bitrate": p.get("bitrate"),
                                 "speed": p.get("speed"), "out_time": p.get("out_time"), "frame": p.get("frame")})
        ended = not ff.alive() and ff.proc.returncode == 0
        why = "stopping" if stopping else (f"exited with {ff.proc.returncode}" if not ff.alive() else "stalled")
        ff.stop()
        if stopping:
            break
        restarts += 1
        delay = 1 if ended else backoff.failed()
        log(f"push: {why}; reconnect #{restarts} in {delay:.0f} s")
        time.sleep(delay)
    log("push: stopped")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["capture", "push"])
    return capture() if p.parse_args().mode == "capture" else push()


if __name__ == "__main__":
    raise SystemExit(main())
