import functools
import itertools
import json
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
CHROMES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable",
           "chromium", "chromium-browser"]

pytestmark = pytest.mark.browser


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def page():
    """A headless Chrome page (SwiftShader WebGL) and a server for the repo, which holds web/ and the fixtures."""
    chrome = next((shutil.which(c) or c for c in CHROMES if shutil.which(c) or Path(c).is_file()), None)
    if not chrome:
        pytest.fail("no Chrome or Chromium found")
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as profile:
        browser = subprocess.Popen([chrome, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}",
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
                ids = itertools.count(1)

                def js(expression: str):
                    i = next(ids)
                    ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                                        "params": {"expression": expression, "awaitPromise": True,
                                                   "returnByValue": True}}))
                    while (message := json.loads(ws.recv(timeout=90))).get("id") != i:
                        pass
                    result = message["result"]
                    assert "exceptionDetails" not in result, result["exceptionDetails"]
                    return result["result"].get("value")

                def goto(query: str):
                    ws.send(json.dumps({"id": next(ids), "method": "Page.navigate",
                                        "params": {"url": f"http://127.0.0.1:{server.server_port}/web/index.html?{query}"}}))
                    js("new Promise(r => { const f = () => window.airops ? r() : setTimeout(f, 50); f(); })")

                yield goto, js
        finally:
            browser.terminate()
            browser.wait()
            server.shutdown()


def test_the_viewer_loads_an_episode_and_takes_the_hooks(page):
    goto, js = page
    goto("record=1&episode=/tests/viewer/fixtures/episode_small.json")
    fixture = json.loads((ROOT / "tests/viewer/fixtures/episode_small.json").read_text())
    ready = js("airops.ready")
    assert ready["steps"] == len(fixture["steps"])
    assert ready["watches"] == [w["t"] for w in fixture["watches"]]
    js("airops.view('hub')")
    js(f"airops.show({len(fixture['steps']) - 1})")
    js(f"airops.time({fixture['watches'][-1]['t'] + 30})")
    state = js("airops.state()")
    assert state["view"] == "hub" and state["t"] == fixture["watches"][-1]["t"] + 30
    clock, scrubber = (js(f"document.querySelector('{q}').textContent") for q in (".ops .time", ".scrub .now"))
    assert clock == scrubber
    assert js("document.querySelectorAll('svg.tails rect.bar').length") >= len(fixture["schedule"]) // 2


def test_the_viewer_lists_episodes(page):
    goto, js = page
    goto("index=/tests/viewer/fixtures/index.json")
    assert js("airops.ready")["episodes"] == 1
    assert "episode_small.json" in js("document.querySelector('table.eps a').href")
