"""A headless Chrome page over the DevTools protocol (SwiftShader WebGL) and static servers for folders, one of them
answering with CORS headers as huggingface.co's resolve URLs do."""

import functools
import itertools
import json
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from websockets.sync.client import connect

CHROMES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable",
           "chromium", "chromium-browser"]


class Quiet(SimpleHTTPRequestHandler):
    cors = False

    def end_headers(self) -> None:
        if self.cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, *args) -> None:
        pass


class Cors(Quiet):
    cors = True


@contextmanager
def served(folder: Path, cors: bool = False):
    """The folder over HTTP on loopback; yields its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Cors if cors else Quiet, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


class Page:
    def __init__(self, ws):
        self.ws, self.ids = ws, itertools.count(1)

    def js(self, expression: str):
        i = next(self.ids)
        self.ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                                 "params": {"expression": expression, "awaitPromise": True, "returnByValue": True}}))
        while (message := json.loads(self.ws.recv(timeout=120))).get("id") != i:
            pass
        result = message["result"]
        assert "exceptionDetails" not in result, result["exceptionDetails"]
        return result["result"].get("value")

    def goto(self, url: str, ready: str = "window.airops") -> None:
        self.ws.send(json.dumps({"id": next(self.ids), "method": "Page.navigate", "params": {"url": url}}))
        time.sleep(0.2)
        self.js(f"new Promise(r => {{ const f = () => ({ready}) ? r() : setTimeout(f, 50); f(); }})")


@contextmanager
def chrome():
    binary = next((shutil.which(c) or c for c in CHROMES if shutil.which(c) or Path(c).is_file()), None)
    if not binary:
        pytest.fail("no Chrome or Chromium found")
    with tempfile.TemporaryDirectory() as profile:
        browser = subprocess.Popen([binary, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}",
                                    "--window-size=1600,900", "--enable-unsafe-swiftshader", "--use-angle=swiftshader",
                                    "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = Path(profile) / "DevToolsActivePort"
            deadline = time.monotonic() + 30
            while not port_file.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            port = port_file.read_text().split()[0]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
                ws_url = next(t["webSocketDebuggerUrl"] for t in json.load(r) if t["type"] == "page")
            with connect(ws_url, max_size=None) as ws:
                yield Page(ws)
        finally:
            browser.terminate()
            browser.wait()
