import gzip
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "web" / "episode.schema.json").read_text())
SMALL = ROOT / "tests" / "viewer" / "fixtures" / "episode_small.json"
CACHED = sorted((ROOT / "cache" / "episodes").glob("*.json"))


def validate(episode: dict) -> None:
    Draft202012Validator.check_schema(SCHEMA)
    errors = sorted(Draft202012Validator(SCHEMA, format_checker=FormatChecker()).iter_errors(episode), key=str)
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:10])


def consistent(e: dict) -> None:
    ids = {s["id"] for s in e["schedule"]}
    tails = {t["tail"] for t in e["task"]["tails"]}
    airports = set(e["task"]["airports"])
    assert {x["id"] for x in e["executed"]} == ids
    assert {x["tail"] for x in e["executed"] if x["status"] == "flown"} <= tails
    assert all(x["dep"] is not None and x["arr"] > x["dep"] for x in e["executed"] if x["status"] == "flown")
    assert all(x["dep"] is None for x in e["executed"] if x["status"] != "flown")
    assert {a for s in e["schedule"] for a in (s["orig"], s["dest"])} <= airports
    assert e["task"]["hub"] in airports
    ts = [w["t"] for w in e["watches"]]
    assert ts == sorted(set(ts)) and ts[0] == e["task"]["start"]
    assert [s["t"] for s in e["steps"]] == sorted(s["t"] for s in e["steps"])
    for w in e["watches"]:
        assert {c["id"] for c in w["changes"]} <= ids
        assert {r["id"] for r in w.get("projected", [])} <= ids
        assert all(n["at"] <= w["t"] for n in w["news"])
    total = e["cost"]["pax_delay"] + e["cost"]["cancel"] + e["cost"]["ferry"]
    assert total == e["cost"]["total"]
    assert 0.2 <= e["reward"] <= 1


def test_the_small_fixture_is_a_valid_episode():
    episode = json.loads(SMALL.read_text())
    validate(episode)
    consistent(episode)


def test_the_schema_rejects_a_broken_episode():
    episode = json.loads(SMALL.read_text())
    episode["executed"][0]["status"] = "diverted"
    del episode["task"]["hub"]
    errors = list(Draft202012Validator(SCHEMA).iter_errors(episode))
    assert {tuple(e.absolute_path) for e in errors} >= {("executed", 0, "status"), ("task",)}


@pytest.mark.parametrize("path", [p for p in CACHED if p.name != "index.json"], ids=lambda p: p.name)
def test_built_episodes_are_valid(path):
    episode = json.loads(path.read_text())
    validate(episode)
    consistent(episode)
    for frame in (episode["radar"] or {}).get("frames", []):
        assert (path.parent / frame["url"]).resolve().is_file()
    if episode["twin"]:
        assert (path.parent / episode["twin"]["url"]).resolve().is_file()
    if episode.get("stage"):
        stage = (path.parent / episode["stage"]["url"]).resolve()
        assert stage.is_file() and (stage.parent / "ortho.jpg").is_file()
        traffic = json.loads(gzip.decompress((path.parent / episode["stage"]["traffic"]).read_bytes()))
        assert (traffic["hub"], traffic["date"]) == (episode["task"]["hub"], episode["task"]["date"])
        assert traffic["span"][0] == episode["task"]["start"] and traffic["span"][1] > episode["watches"][-1]["t"]
        tagged = {leg["flight"] for a in traffic["aircraft"] for v in a["visits"] for leg in (v["arr"], v["dep"])
                  if leg and leg["tag"]}
        hub = episode["task"]["hub"]
        assert tagged <= {f["flight"] for f in episode["schedule"] + episode["frozen"] if hub in (f["orig"], f["dest"])}


def test_the_schema_takes_a_stage_or_none():
    episode = json.loads(SMALL.read_text())
    for stage in (None, {"hub": "BOS", "url": "../stage/BOS/stage.json.gz", "traffic": "x.stage.json.gz"}):
        validate(episode | {"stage": stage})
    errors = list(Draft202012Validator(SCHEMA).iter_errors(episode | {"stage": {"hub": "BOS"}}))
    assert [tuple(e.absolute_path) for e in errors] == [("stage",)]
