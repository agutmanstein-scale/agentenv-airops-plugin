"""A day of airops-occ3 followed live from the env's record as it grows (agentenv_airops.occ.onair): played again to the
env's state at every point, also resumed from a checkpoint at each, the day so far as an episode with the room and the
projection, no grade and nothing of the truth after the open watch, the done day as the recorded episode; and the live
server following a fake runner's days through failing data/get calls and an instance that dies and starts again, and
going on from its checkpoint when it is itself restarted."""

import copy
import importlib.util
import json
import socket
import threading
import time
from argparse import Namespace
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from occ_helpers import ROLE, desk_agent, storm3

from agentenv_airops import export
from agentenv_airops.occ import episode, onair
from agentenv_airops.occ.day import costs
from agentenv_airops.occ.day3 import OccDay3

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("live_server", ROOT / "scripts" / "live_server.py")
live_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_server)
END = {"note": "watch done", "stand_down": False}
MODEL = "openai/gpt-6-astra"


@pytest.fixture(scope="module")
def world():
    return storm3()


@pytest.fixture(scope="module")
def day(world):
    """The scripts' day at three desks, and the env's record (data/get) after each call."""
    d = OccDay3(world, "joint")
    scripts = {k: desk_agent.Script(k) for k in d.desks}
    records = []

    def call(desk: str, tool: str, args: dict) -> dict:
        out = d.call(ROLE[desk], tool, args)
        records.append(onair.jsonable(d.data()))
        return out
    while not d.done:
        desk = d.turn["desk"]
        for n in list(d.pending):
            d.notice(n, "trigger")
        page = call(desk, "wait_turn", {})

        async def act(name: str, args: dict, desk: str = desk) -> dict:
            return json.loads(json.dumps(call(desk, name, args)))
        run(scripts[desk].turn(page["page"], act))
        call(desk, "end_turn", END)
    for desk in d.desks:
        call(desk, "resign", {"reason": "done"})
    return d, records


def run(coro) -> None:
    try:
        while True:
            coro.send(None)
    except StopIteration:
        pass


def live_validator() -> Draft202012Validator:
    schema = copy.deepcopy(export.SCHEMA)
    schema["properties"]["reward"] = {"type": ["number", "null"], "minimum": 0, "maximum": 1}
    schema["properties"]["occ"]["properties"]["grade"] = {"type": ["object", "null"]}
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_a_day_followed_as_its_record_grows_is_the_envs_day_at_every_point(world, day):
    d, records = day
    assert any(r["requests"] for r in records) and any(n["via"] == "trigger" for n in records[-1]["notices"])
    f, g, worlds = onair.Follower(world, "joint"), onair.Follower(world, "joint"), [world, storm3()]
    for i in [*range(0, len(records) - 1, 3), len(records) - 1]:
        new = f.follow(records[i])
        assert onair.jsonable(f.day.data()) == records[i]
        assert all(x["seq"] > 0 for x in new)
        g = onair.unpickled(onair.pickled(g, g.world), worlds[i % 2])
        assert g.world is worlds[i % 2] and g.day.task is g.world.task
        assert [x["seq"] for x in g.follow(records[i])] == [x["seq"] for x in new]
        assert onair.jsonable(g.day.data()) == records[i]
    assert f.day.done and not f.mismatches and f.day.digest() == d.digest()
    assert g.day.done and not g.mismatches and g.day.digest() == d.digest() and g.day.left == f.day.left
    assert f.follow(records[-1]) == []
    assert len(f.day.left) == len(d.estimates)
    with pytest.raises(onair.Diverged, match="calls up to seq"):
        f.follow(records[5])
    with pytest.raises(onair.Diverged, match="not"):
        onair.Follower(world, "one").follow(records[0])


def test_the_day_so_far_is_an_episode_of_the_room_and_the_projection_with_nothing_after_its_watch(world, day,
                                                                                                tmp_path):
    d, records = day
    final = records[-1]
    statics = onair.Statics(world, tmp_path, tmp_path)
    f = onair.Follower(world, "joint")
    mid = next(r for r in records if r["t"] >= 660 and r["turns"][-1]["desk"] == "ops")
    f.follow(mid)
    ep = onair.now(f, statics, model=MODEL, run_label="live-joint")
    assert not list(live_validator().iter_errors(ep))
    assert (ep["reward"], ep["occ"]["grade"], ep["end_reason"], ep["audit"], ep["stage"]) == (None,) * 5
    assert len(ep["watches"]) == f.day.k + 1 == mid["watch"] + 1
    assert [w["projected"] for w in ep["watches"][:-1]] == episode.projections(world, final)[: f.day.k]
    assert "est_cost" not in ep["watches"][-1] and ep["watches"][-1]["projected"] == onair.rows(
        f.day, f.day.project(f.day.plan))
    assert [s["tool"] for s in ep["steps"]] == [c["tool"] for c in mid["calls"]]
    assert [r["id"] for r in ep["occ"]["requests"]] == [r["id"] for r in mid["requests"]]
    assert ep["cost"]["total"] == costs(f.day.project(f.day.plan).day)["total"]
    t, truth = f.day.t, {x["id"]: x for x in final["grade"]["executed"]}
    gone = [x for x in ep["executed"] if x["dep"] is not None and x["dep"] < t]
    assert gone and all((x["dep"], x["tail"], x["status"]) == (truth[x["id"]]["dep"], truth[x["id"]]["tail"],
                                                               truth[x["id"]]["status"]) for x in gone)
    rows = ep["passengers"]["flights"]
    assert all(r["booked"] + r["added"] == r["flown"] + sum(x["pax"] for x in r["rebooked"]) + r["stranded"] +
               r["home"] + r["dropped"] for r in rows)
    assert onair.decided_until(f.day, ep["executed"]) == t

    f.follow(final)
    done = onair.final(f, statics, model=MODEL, run_label="live-joint")
    want = episode.episode(world, final, model=MODEL, run="live-joint", inputs=tmp_path, cache=tmp_path,
                           projected=episode.projections(world, final))
    assert done == want
    export.validate(done)


def test_the_feed_and_the_room_say_what_each_desk_did(world, day):
    d, records = day
    f = onair.Follower(world, "joint")
    new = f.follow(records[-1])
    rows = onair.feed(f, new, {})
    kinds = {r["kind"] for r in rows}
    assert {"request", "reply", "note", "off"} <= kinds
    request = next(r for r in rows if r["kind"] == "request")
    assert request["request"] == "R1" and request["text"].startswith(f"{d.requests[0]['sender']} to ")
    assert all("f" + str(r["flight"]) not in r["text"] for r in rows if r["flight"] is not None)
    last = {x["desk"]: {"last": {"tool": x["tool"], "text": onair.summary(x, world.task)}, "at": "now"}
            for x in new if x["tool"] != "notice"}
    assert [(x["role"], x["state"], x["off"], x["last"]["tool"]) for x in onair.desks(f, last)] == [
        (k, "off", "done", "resign") for k in ("crew", "customer", "ops")]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_server_follows_a_runners_days_through_failed_calls_and_a_restart(world, day, tmp_path, monkeypatch):
    d, records = day
    file = tmp_path / "day.json"
    file.write_text(json.dumps(records[-1]))
    monkeypatch.setattr(live_server, "Pack3", lambda path=None: Namespace(world=lambda task_id: world))
    current, feed_port, port = tmp_path / "current.json", free_port(), free_port()
    feed = live_server.Feed(Namespace(days=[file], current=current, port=feed_port, watch_seconds=0.15,
                                      start_delay=0.3, linger=2.5, gap=3.0, hiccups=0.2, hang=0.2, restart_at=2,
                                      pilot="occ3-live-test", model=MODEL, cost_usd=1.5))
    server = live_server.Server(Namespace(current=current, env=None, task=None, live=tmp_path / "live",
                                          assets=tmp_path, inputs=None, web=ROOT / "web", pack=None,
                                          host="127.0.0.1", port=port, poll=0.2))
    for target in (feed.serve, feed.play, server.bind().serve_forever, server.loop):
        threading.Thread(target=target, daemon=True).start()
    base, seen, deadline = f"http://127.0.0.1:{port}", [], time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            live = httpx.get(f"{base}/live/state.json", timeout=5).json()["live"]
        except httpx.HTTPError:
            time.sleep(0.1)
            continue
        if live.get("run_id"):
            seen.append((live["run_id"], live["status"], live["watch"], live["decided_until"], live["seq"]))
        if live.get("run_id", "").endswith("a2") and live["status"] == "intermission":
            break
        time.sleep(0.1)
    runs = list(dict.fromkeys(r for r, *_ in seen))
    assert [r.rsplit("-", 1)[1] for r in runs] == ["a1", "a2"]
    assert {s for r, s, *_ in seen if r == runs[1]} >= {"playing", "done", "intermission"}
    assert all(a[4] <= b[4] for a, b in zip(seen, seen[1:], strict=False))
    a2 = [x for x in seen if x[0] == runs[1]]
    assert all(a[3] <= b[3] for a, b in zip(a2, a2[1:], strict=False))
    r = httpx.get(f"{base}/live/episode.json", headers={"Accept-Encoding": "gzip"}, timeout=10)
    assert r.headers["access-control-allow-origin"] == "*" and r.headers["content-encoding"] == "gzip"
    ep = r.json()
    live = ep.pop("live")
    assert (live["status"], live["exact"], live["mismatches"], live["result"]["reward"]) == (
        "intermission", True, 0, records[-1]["grade"]["reward"])
    want = episode.episode(world, records[-1], model=MODEL, run="occ3-live-test-joint", inputs=tmp_path,
                           cache=tmp_path, projected=episode.projections(world, records[-1]))
    assert {k: v for k, v in ep.items() if k != "record"} == {k: v for k, v in want.items() if k != "record"}
    assert ep["record"]["attempt"] == 2 and ep["record"]["cost_usd"] == 1.5
    assert httpx.get(f"{base}/live/episode.json", headers={"If-None-Match": r.headers["etag"]}).status_code in (
        200, 304)
    [entry] = httpx.get(f"{base}/live/index.json").json()["episodes"]
    assert entry["run"] == "occ3-live-test-joint" and entry["reward"] == records[-1]["grade"]["reward"]
    kept = httpx.get(f"{base}/live/{entry['url']}").json()
    assert kept["live"]["status"] == "done" and kept["executed"] == ep["executed"]
    assert httpx.get(f"{base}/web/index.html").status_code == 200
    assert httpx.get(f"{base}/stage/../../current.json").status_code == 404


def test_a_server_started_after_the_day_reads_it_from_the_day_file_watch_by_watch(world, day, tmp_path, monkeypatch):
    d, records = day
    file = tmp_path / "day.json"
    file.write_text(json.dumps(records[-1]))
    monkeypatch.setattr(live_server, "Pack3", lambda path=None: Namespace(world=lambda task_id: world))
    current = tmp_path / "current.json"
    current.write_text(json.dumps({"status": "intermission", "task_id": records[-1]["task_id"], "run_id": "x-joint-a1",
                                   "mode": "joint", "model": MODEL, "pilot": "late", "day_file": str(file),
                                   "instance_id": "i1", "next": {"task_id": "y"}, "result": {"cost_usd": 2.0}}))
    server = live_server.Server(Namespace(current=current, env=None, task=None, live=tmp_path / "live",
                                          assets=tmp_path, inputs=None, web=ROOT / "web", pack=None,
                                          host="127.0.0.1", port=0, poll=0.0))
    seen = []
    while not (server.day and server.day.final):
        server.step()
        seen.append((server.status, server.day.follower.day.k))
    assert len(seen) > 2 and {s for s, _ in seen[:-1]} == {"ending"} and seen[-1][0] == "intermission"
    assert [k for _, k in seen] == sorted(k for _, k in seen)
    live = json.loads(server.state)["live"]
    assert (live["status"], live["exact"], live["next"], live["result"]["reward"]) == (
        "intermission", True, {"task_id": "y"}, records[-1]["grade"]["reward"])
    ep = json.loads(server.episode[0])
    assert [{k: x[k] for k in ("id", "status", "dep", "arr", "tail")} for x in ep["executed"]] == [
        {k: x[k] for k in ("id", "status", "dep", "arr", "tail")} for x in d.grade["executed"]]
    assert ep["record"]["cost_usd"] == 2.0


def test_a_restarted_server_goes_on_from_its_checkpoint_to_the_same_episode(world, day, tmp_path, monkeypatch):
    d, records = day
    file = tmp_path / "day.json"
    file.write_text(json.dumps(records[-1]))
    monkeypatch.setattr(live_server, "Pack3", lambda path=None: Namespace(world=lambda task_id: storm3()))
    current, ckpt = tmp_path / "current.json", tmp_path / "live" / "ckpt"
    cur = {"status": "intermission", "task_id": records[-1]["task_id"], "run_id": "x-joint-a1", "attempt": 1,
           "mode": "joint", "model": MODEL, "pilot": "late", "day_file": str(file), "instance_id": "i1"}
    current.write_text(json.dumps(cur))
    ckpt.mkdir(parents=True)
    (ckpt / "x-joint-a0.pkl").write_bytes(b"another day's")
    args = Namespace(current=current, env=None, task=None, live=tmp_path / "live", assets=tmp_path, inputs=None,
                     web=ROOT / "web", pack=None, host="127.0.0.1", port=0, poll=0.0)

    def played(until: int = 99) -> tuple[live_server.Server, dict]:
        server = live_server.Server(args)
        server.step()
        while not server.day.final and server.day.follower.day.k < until:
            server.step()
        ep = json.loads((tmp_path / "live" / "live" / "x-joint-a1.json").read_text()) if server.day.final else None
        return server, ep

    first, _ = played(until=3)
    k, n = first.day.follower.day.k, first.day.follower.played
    assert [p.name for p in ckpt.iterdir()] == ["x-joint-a1.pkl"] and not first.day.resumed
    assert k >= 3 and 0 < n < len(records[-1]["calls"])
    resumed, ep = played()
    assert resumed.day.resumed and resumed.day.started == first.day.started
    assert {w: at for w, at in resumed.day.opened.items() if w <= k} == first.day.opened
    after, again = played(until=0)
    assert after.day.resumed and after.day.final
    (ckpt / "x-joint-a1.pkl").write_bytes(b"torn")
    whole, want = played()
    assert not whole.day.resumed and whole.day.follower.played == resumed.day.follower.played > n
    assert all((x["live"]["exact"], x["live"]["mismatches"]) == (True, 0) for x in (ep, again, want))
    assert {k: v for k, v in ep.items() if k != "live"} == {k: v for k, v in again.items() if k != "live"} == {
        k: v for k, v in want.items() if k != "live"}
    assert not live_server.Day(cur | {"attempt": 2}, args.live, tmp_path, live_server.Pack3(), None).resumed
    monkeypatch.setattr(live_server, "code", lambda: "another build")
    assert not live_server.Day(cur, args.live, tmp_path, live_server.Pack3(), None).resumed
