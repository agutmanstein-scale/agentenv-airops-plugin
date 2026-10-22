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


HERO = ROOT / "cache" / "episodes" / "sol-v1.openai-gpt-6.1-sol.b6-bos-20250216-1500.json"


def test_an_episode_without_a_stage_plays_on_the_map(page):
    goto, js = page
    goto("record=1&episode=/tests/viewer/fixtures/episode_small.json")
    fixture = json.loads((ROOT / "tests/viewer/fixtures/episode_small.json").read_text())
    ready = js("airops.ready")
    assert ready["steps"] == len(fixture["steps"]) and ready["watches"] == [w["t"] for w in fixture["watches"]]
    assert ready["stage"] is False and ready["views"] == ["network"]
    assert js("airops.view('hub')") == "network"
    js(f"airops.show({len(fixture['steps']) - 1})")
    js(f"airops.time({fixture['watches'][-1]['t'] + 30})")
    state = js("airops.state()")
    assert (state["view"], state["main"], state["t"]) == ("network", "map", fixture["watches"][-1]["t"] + 30)
    assert js("document.querySelector('.ep .view-map').classList.contains('main')")
    clock, scrubber = (js(f"document.querySelector('{q}').textContent") for q in (".head .clock b", ".timeline .now"))
    assert clock == scrubber
    assert js("document.querySelector('.desk-h b').textContent").startswith(f"Watch {len(fixture['watches'])} of ")
    assert js("document.querySelectorAll('.board li:not(.none)').length") > 0
    assert "GPT" not in js("document.querySelector('.head h1').textContent")
    js("airops.expert(true)")
    assert js("document.querySelectorAll('.expert svg.tails rect.bar').length") >= len(fixture["schedule"]) // 2
    assert js("document.querySelectorAll('.expert .calls li').length") > 0


def test_the_sound_is_off_until_asked_for_and_absent_with_sound_0(page):
    goto, js = page
    goto("episode=/tests/viewer/fixtures/episode_small.json")
    js("airops.ready.then(() => new Promise(r => { const f = () => airops.sound ? r() : setTimeout(f, 50); f(); }))")
    assert js("({on: airops.sound.on, ctx: airops.sound.ctx})") == {"on": False, "ctx": None}
    assert js("document.querySelector('.timeline .sound-toggle').getAttribute('aria-pressed')") == "false"
    goto("episode=/tests/viewer/fixtures/episode_small.json&sound=0")
    js("airops.ready.then(() => new Promise(r => setTimeout(r, 300)))")
    assert js("!airops.sound && !document.querySelector('.sound-toggle')")


def test_the_viewer_lists_episodes(page):
    goto, js = page
    goto("index=/tests/viewer/fixtures/index.json")
    assert js("airops.ready")["episodes"] == 1
    assert "episode_small.json" in js("document.querySelector('a.ep-card').href")
    assert js("document.querySelector('.list h1').textContent") == "AirOpsEnv v1"
    assert js("document.querySelector('.ep-card dd').textContent") == "0.588"


@pytest.mark.skipif(not HERO.is_file(), reason="Sol's Boston run is not exported under cache/episodes")
def test_an_episode_with_a_stage_fills_the_screen_with_the_airport(page):
    goto, js = page
    goto(f"record=1&episode=/{HERO.relative_to(ROOT)}")
    ready = js("airops.ready")
    assert ready["stage"] is True and ready["span"][0] == 840 and ready["end"] > ready["last_watch"]
    assert set(ready["views"]) == {"tower", "gate", "threshold", "harbour", "follow", "network"}
    assert js("document.querySelector('.ep .view-stage').classList.contains('main')")
    assert js("document.querySelector('.ep .view-map').classList.contains('corner')")
    js("airops.time(1035)")
    state = js("airops.state()")
    assert state["main"] == "stage" and state["stage"]["t"] == 1035 and state["stage"]["aircraft"] > 10
    assert js("document.querySelector('.desk-h b').textContent") == "Watch 4 of 12 · 17:00"
    assert "Light rain" in js("document.querySelector('.head .wx').textContent")
    statuses = js("[...document.querySelectorAll('.board li .s')].map(e => e.textContent)")
    words = {"On", "Delayed", "Held", "May", "Cancelled", "Departed", "Landed"}
    assert statuses and all(s.split()[0] in words for s in statuses)
    assert js("airops.view('network')") == "network" and js("airops.state()")["main"] == "map"
    js("document.querySelector('.ep .view-stage').click()")
    assert js("airops.state()")["main"] == "stage"
    for name in ("gate", "threshold", "harbour", "tower"):
        assert js(f"airops.view('{name}')") == name
    key = ready["featured"][0]["key"]
    assert js(f"airops.view('follow', {{key: '{key}'}})") == "follow" and js("airops.state()")["following"] == key
    assert js("document.querySelector('.follow-chip').hidden") is False
