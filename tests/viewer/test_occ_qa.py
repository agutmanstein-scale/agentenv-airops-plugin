import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("occ_qa", ROOT / "scripts" / "occ_qa.py")
qa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qa)


class Page:
    def __init__(self, events):
        self.events = events


def test_console_errors_and_exceptions_fail_but_the_waived_ones():
    page = Page([
        {"method": "Runtime.consoleAPICalled", "params": {"type": "error", "args": [
            {"type": "string", "value": "the control-center room failed:"}, {"type": "object", "description": "E"}]}},
        {"method": "Runtime.consoleAPICalled", "params": {"type": "warning", "args": [{"value": "no radar"}]}},
        {"method": "Runtime.exceptionThrown", "params": {"exceptionDetails": {
            "text": "Uncaught", "exception": {"description": "TypeError: x is undefined\n    at app.js:1"}}}},
        {"method": "Log.entryAdded", "params": {"entry": {"level": "error", "text": "404", "url": "/favicon.ico"}}},
        {"method": "Log.entryAdded", "params": {"entry": {"level": "error", "text": "404", "url": "/viewer/x.js"}}},
        *({"method": "Log.entryAdded", "params": {"entry": {"level": "error", "source": "network", "text": "ERR",
                                                            "url": f"https://tiles.example.org/{i}.png"}}}
          for i in range(3)),
        *({"method": "Log.entryAdded", "params": {"entry": {"level": "error", "source": "javascript", "text":
            f"Access to fetch at '{url}' from origin 'http://127.0.0.1:5000' has been blocked by CORS policy"}}}
          for url in ("https://tiles.example.org/9.png", "http://127.0.0.1:5001/data/stage/ortho.jpg"))])
    notes = []
    assert qa.errors(page, [qa.QUIET, re.compile("room failed")], notes) == [
        "console: TypeError: x is undefined", "console: 404 /viewer/x.js",
        "console: Access to fetch at 'http://127.0.0.1:5001/data/stage/ortho.jpg' from origin 'http://127.0.0.1:5000' "
        "has been blocked by CORS policy"]
    assert notes == ["4 resources from tiles.example.org failed to load"] and page.events == []


def test_the_minutes_checked_are_the_story_and_the_first_answer_between_the_desks():
    ep = {"task": {"window": [660, 1080]}, "occ": {"requests": [{"answered": None}, {"answered": 864.77},
                                                                {"answered": 434.0}, {"answered": 1287.4}]}}
    assert qa.default_minutes(ep) == [866, 870]
    assert qa.default_minutes({"task": {"window": [600, 720]}}) == [660]
