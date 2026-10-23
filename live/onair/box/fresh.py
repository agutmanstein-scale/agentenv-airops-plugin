"""The stream's picture against the page: each minute Chrome's screenshot of the page and the relay's next seconds of
frames, at 96x54 grey; the closest frame's mean difference stays a few levels while the encoder sees the page. Three
minutes in a row past FAR restart the encoder (the capture supervisor starts it again): x11grab can keep encoding a
stale screen at full rate, which no frame count catches."""
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websocket

OUT, EVERY, FAR, TIMES = sys.argv[1], 60.0, 12.0, 3
W, H = 96, 54
RELAY = "rtmp://127.0.0.1:1935/airops"


def page() -> bytes:
    pages = [x for x in json.load(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=10))
             if x.get("type") == "page"]
    ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                break
    finally:
        ws.close()
    return grey(["-f", "png_pipe", "-i", "-"], base64.b64decode(m["result"]["data"]))


def grey(args: list[str], stdin: bytes | None = None) -> bytes:
    return subprocess.run(["ffmpeg", "-nostdin" if stdin is None else "-hide_banner", "-loglevel", "error", *args,
                           "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                          input=stdin, capture_output=True, timeout=60).stdout


def closest(shot: bytes, frames: bytes) -> float | None:
    n = W * H
    diffs = [sum(abs(a - b) for a, b in zip(shot, frames[i:i + n], strict=False)) / n
             for i in range(0, len(frames) - n + 1, n)]
    return round(min(diffs), 1) if diffs and len(shot) == n else None


def main() -> None:
    far = 0
    while True:
        t0 = time.time()
        row = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        try:
            shot = page()
            frames = grey(["-i", RELAY, "-t", "6", "-r", "10"])
            row |= {"diff": closest(shot, frames), "frames": len(frames) // (W * H)}
            far = far + 1 if row["diff"] is None or row["diff"] > FAR else 0
            if far >= TIMES:
                subprocess.run(["pkill", "-f", "x11grab -framerate"], timeout=10)
                row["action"] = "restarted the encoder"
                far = 0
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"[:200]
        row["far"] = far
        with open(OUT, "a") as f:
            f.write(json.dumps(row) + "\n")
        time.sleep(max(1.0, EVERY - (time.time() - t0)))


if __name__ == "__main__":
    main()
