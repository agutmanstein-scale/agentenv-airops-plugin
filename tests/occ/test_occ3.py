"""airops-occ3 on the small storm day with passengers: three desks taking turns (crew, customer, ops, then the reply
turns ops asked for), each refused the others' tools, a customer desk that leaves skipped; the customer desk's requests
to ops, an accepted HOLD a hold and PROTECT and SUGGEST_CANCEL advice only, replacing only its own; one agent at all
three desks with no one to ask; the day under cost P graded on the pack's references, audited and replayed byte for
byte, also in a fresh interpreter with another hash seed and no display; no page, tool reply or record before the end
that the references or the future past a watch could change; the episode's passengers in the viewer's balances; the
bundle, the env's strict tools per role and the customer desk's script."""

import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from fastmcp import Client
from occ_helpers import ROLE, desk_agent, storm3, strict

from agentenv_airops.occ import customer, episode, tasks3
from agentenv_airops.occ.day import Refused, reward
from agentenv_airops.occ.day3 import OccDay3
from agentenv_airops.occ.live3 import OccEnv3
from agentenv_airops.occ.pack import World
from agentenv_airops.occ.replay import replay

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("occ3_checks", ROOT / "scripts" / "occ3_checks.py")
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)
END = {"note": None, "stand_down": False}
NONE = {"ids": None, "station": None, "status": None, "start": None, "end": None}
REPLAY = f"""
import json, sys
sys.path[:0] = [{str(ROOT / "tests" / "occ")!r}, {str(ROOT / "tests" / "programs")!r}]
from occ_helpers import storm3
from agentenv_airops.occ.day3 import OccDay3
from agentenv_airops.occ.replay import replay
again = replay(storm3(), json.loads(open(sys.argv[1]).read()), OccDay3)
print(json.dumps({{"exact": again.exact, "digest": again.day.digest(), "cost": again.day.grade["cost"]}}))
"""


@pytest.fixture(scope="module")
def world():
    return storm3()


def turn(d: OccDay3, desk: str) -> str:
    assert d.turn["desk"] == desk
    return d.call(ROLE[desk], "wait_turn", {})["page"]


def test_three_desks_take_turns_crew_customer_ops_each_with_its_own_tools(world):
    d = OccDay3(world)
    assert [d.turn["desk"], *d.queue] == ["crew", "customer", "ops"]
    assert "not your turn" in d.call("customer", "cust_flights", NONE)["error"]
    turn(d, "crew")
    d.call("crew", "end_turn", END)
    page = turn(d, "customer")
    assert page.startswith("# Customer desk, JetBlue, 2024-01-01, 00:00 (watch 1 of 25, customer turn)")
    assert "Passengers as projected:" in page and "## Connections at risk" in page and "f0 100 JFK-BOS" in page
    with pytest.raises(Refused, match="not a tool of the customer desk"):
        d.call("customer", "ops_confirm", {"changes": []})
    with pytest.raises(Refused, match="not a tool of the ops desk"):
        d.call("ops", "cust_request", {"type": "TEXT", "flight": None, "until": None, "note": "x"})
    detail = d.call("customer", "cust_flight", {"flight": "f3"})
    assert (detail["booked"], detail["local"]) == (100, 70) and detail["connecting_in"][0].startswith("f1 in ")
    assert detail["as_projected"] == {"flew": 70, "rebooked": 0, "stranded": 30, "refunded": 0}
    d.call("customer", "end_turn", END)
    assert "passenger delay 274,750, refund exposure 6,600" in turn(d, "ops")
    rows = d.call("ops", "ops_flights", NONE)["rows"]
    assert rows[2].startswith("f3 103 JFK-MCO 08:20 100(30/0) A320") and "before rebooking" in rows[2]
    d.call("ops", "ops_request", {"type": "TEXT", "flight": None, "time": None, "text": "cancelling f2",
                                  "to": "customer"})
    d.call("ops", "ops_request", {"type": "COVER", "flight": "f3", "time": "09:00", "text": None, "to": "crew"})
    assert d.call("ops", "ops_request", {"type": "COVER", "flight": "f3", "time": "09:00", "text": None,
                                         "to": "customer"}) == {"error": "ops tells the customer desk by TEXT"}
    d.call("ops", "end_turn", END)
    assert [(d.turn["kind"], d.turn["desk"]), *d.queue] == [("reply", "crew"), "creply"]
    turn(d, "crew")
    d.call("crew", "end_turn", END)
    assert "R1 text from ops: cancelling f2" in turn(d, "customer")
    d.call("customer", "end_turn", END)
    assert d.turn["desk"] == "crew" and d.k == 1


def test_a_customer_hold_ops_accepts_holds_the_departure_and_advice_changes_nothing(world):
    d = OccDay3(world)
    turn(d, "crew")
    d.call("crew", "end_turn", END)
    turn(d, "customer")
    hold = d.call("customer", "cust_request", {"type": "HOLD", "flight": "f3", "until": "11:00", "note": "30 on f1"})
    protect = d.call("customer", "cust_request", {"type": "PROTECT", "flight": "f1", "until": None, "note": None})
    cut = d.call("customer", "cust_request", {"type": "SUGGEST_CANCEL", "flight": "f0", "until": None, "note": None})
    assert (hold["pax"], protect["pax"], cut["pax"]) == (0, 90, 100)
    d.call("customer", "end_turn", END)
    page = turn(d, "ops")
    assert "R1 from customer at 00:00: HOLD f3 until 11:00" in page
    assert "R3 from customer at 00:00: SUGGEST_CANCEL f0 (100 pax)" in page
    for r in ("R1", "R2", "R3"):
        d.call("ops", "ops_reply", {"request": r, "answer": "accept", "text": None})
    assert d.plan == {3: {"holds": [[0, 660]]}}
    assert [r["status"] for r in d.requests] == ["accepted"] * 3 and d.truth.run.flights[0].status == "flown"
    assert d.truth.run.flights[3].dep >= 660


def test_the_customer_desks_requests_replace_only_its_own(world):
    d = OccDay3(world)
    turn(d, "crew")
    d.call("crew", "crew_request", {"type": "CANCEL", "flight": "f2", "time": None, "text": None})
    d.call("crew", "end_turn", END)
    turn(d, "customer")
    d.call("customer", "cust_request", {"type": "PROTECT", "flight": "f2", "until": None, "note": None})
    d.call("customer", "cust_request", {"type": "SUGGEST_CANCEL", "flight": "f2", "until": None, "note": None})
    assert [(r["sender"], r["type"], r["status"]) for r in d.requests] == [
        ("crew", "CANCEL", "open"), ("customer", "PROTECT", "superseded"), ("customer", "SUGGEST_CANCEL", "open")]


def test_a_customer_desk_that_leaves_is_skipped_and_a_text_to_it_queues_no_reply_turn(world):
    d = OccDay3(world)
    turn(d, "crew")
    d.call("crew", "end_turn", END)
    turn(d, "customer")
    assert d.call("customer", "resign", {"reason": "cost_cap"}) == {"resigned": "customer", "reason": "cost_cap",
                                                                    "done": False}
    turn(d, "ops")
    d.call("ops", "ops_request", {"type": "TEXT", "flight": None, "time": None, "text": "x", "to": "customer"})
    d.call("ops", "end_turn", END)
    assert (d.k, d.turn["desk"]) == (1, "crew")
    assert d.call("customer", "wait_turn", {}) == {"done": True, "absent": "cost_cap"}
    while not d.done:
        for n in list(d.pending):
            d.notice(n, "trigger")
        desk = d.turn["desk"]
        d.call(desk, "wait_turn", {})
        d.call(desk, "end_turn", END)
    assert d.grade["end_reason"] == "done" and d.audit()["ok"]
    assert [t["desk"] for t in d.turns][:5] == ["crew", "customer", "ops", "crew", "ops"]
    assert {t["desk"] for t in d.turns[3:]} == {"crew", "ops"} and "creply" not in {t["kind"] for t in d.turns}


def test_one_agent_runs_all_three_desks_with_no_one_to_ask(world):
    d = OccDay3(world, "one")
    page = turn(d, "one")
    assert page.startswith("# Ops control") and "## Crew system alerts" in page and "## Connections at risk" in page
    assert d.call("both", "cust_flights", NONE)["rows"]
    out = d.call("both", "cust_request", {"type": "TEXT", "flight": None, "until": None, "note": "x"})
    assert out == {"error": "one desk runs every job: there is no one to ask"}


def test_the_day_costs_p_is_graded_on_the_packs_references_audited_and_replayed(world):
    d = checks.play(world, "joint")
    g, refs = d.grade, world.refs
    assert g["reward"] == round(reward(g["cost"], refs["best"], refs["B0"]["notice"]), 6)
    assert g["reward_L"] == round(reward(g["cost"], refs["bound"], refs["B0"]["notice"]), 6)
    assert g["costs"]["lodging"] == g["passengers"]["lodging"] > 0 and g["costs"]["total"] == g["cost"]
    assert d.audit()["ok"] and replay(world, d.data(), OccDay3).exact
    assert d.data()["passengers"] == d.truth.run.pax.to_dict()
    assert {t["kind"] for t in d.turns} == {"crew", "customer", "ops"}
    nobound = World(world.task, world.units, world.airports, world.trips, {**refs, "bound": None, "gap_pct": None},
                    {}, world.local, world.passengers)
    e = checks.play(nobound, "joint")
    assert e.grade["reward_L"] is None and e.audit()["ok"] and e.grade["cost"] == g["cost"]


@pytest.mark.parametrize("mode", ["joint", "one"])
def test_no_page_reply_or_record_before_the_end_shows_or_follows_the_references_or_the_future(world, mode):
    d = checks.play(world, mode)
    assert {(r[1], r[2]) for r in d.replies} >= {(k, t) for k in d.desks for t in checks.LOOKUPS[k]}
    assert any("100(30/0)" in r[3] for r in d.replies if r[2] in ("ops_flights", "cust_flights"))
    assert [r[0] for r in d.records] == list(range(d.k + 1)) and len({r[2] for r in d.records}) == d.k + 1
    moved = checks.play(checks.refs_moved(world), mode)
    assert checks.mid_day(d) == checks.mid_day(moved) and d.digest() == moved.digest()
    assert moved.grade["best"] != d.grade["best"] and moved.grade["B0"] != d.grade["B0"]
    assert checks.scan(checks.said(d), world.refs)
    assert not checks.scan([(0, "ops", f"cost {world.refs['best']:,.0f}")], world.refs)
    assert not checks.scan([(0, "customer", f"{round(world.refs['bound'])} pax-min")], world.refs)
    assert not checks.scan([(1, "ops", "x", json.dumps({"reward": 0.5}))], world.refs)
    at = d.times[1]
    later = checks.play(checks.leaky(world, at), mode)
    assert checks.upto(d, at) == checks.upto(later, at) and checks.upto(d, at) != checks.upto(d, at + 600)
    assert later.grade["cost"] != d.grade["cost"] and checks.mid_day(later) != checks.mid_day(d)


@pytest.mark.parametrize("seed", ["0", "4242"])
def test_a_recorded_day_replays_to_its_digest_in_a_fresh_headless_interpreter(world, tmp_path, seed):
    d = checks.play(world, "joint")
    (tmp_path / "day.json").write_text(json.dumps(d.data(), default=str))
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    out = subprocess.run([sys.executable, "-c", REPLAY, str(tmp_path / "day.json")], cwd=tmp_path, check=True,
                         capture_output=True, text=True, env=env | {"PYTHONHASHSEED": seed, "MPLBACKEND": "Agg"})
    assert json.loads(out.stdout) == {"exact": True, "digest": d.digest(), "cost": d.grade["cost"]}


def test_the_episode_passengers_balance_as_the_viewer_reads_them(world):
    d = checks.play(world, "joint")
    data = json.loads(json.dumps(d.data(), default=str))
    out = episode.passengers(world, data)
    rows = {r["id"]: r for r in out["flights"]}
    for r in rows.values():
        assert r["booked"] + r["added"] == r["flown"] + sum(x["pax"] for x in r["rebooked"]) + r["stranded"] + \
            r["home"] + r["dropped"]
        assert r["misconnected"] <= r["booked"] + r["added"] - r["flown"]
    by = Counter()
    for e in out["events"]:
        by[(e["flight"], e["kind"])] += e["pax"]
    assert all(by[(i, "misconnect")] == r["misconnected"] and by[(i, "hotel")] == r["stranded"] and
               by[(i, "home")] == r["home"] for i, r in rows.items())
    assert out["connections"] == [{"from": 1, "to": 3, "at": "JFK", "pax": 30, "made": 0}]
    assert rows[3]["conn_in"] == 30 and rows[3]["misconnected"] == 30 and rows[3]["stranded"] == 30
    assert [e["t"] for e in out["events"]] == sorted(e["t"] for e in out["events"])


def test_a_bundle_day_deploys_three_desks_or_one_agent_capped(world, tmp_path):
    joint = tasks3.steps(world.task, "joint", policy="script")
    agents = [s for s in joint if s["type"] == "deploy_agent"]
    assert [(s["role"], s["agent_name"]) for s in agents] == [("crew", "crew-desk"), ("customer", "customer-desk"),
                                                               ("ops", "ops-desk")]
    assert all(s["env_vars"] == {"AIROPS_MAX_COST_USD": "5", "AIROPS_DESK_POLICY": "script"} for s in agents)
    tools = {s["role"]: s["tools"] for s in joint if s["type"] == "modify_env_tool_access" and s["action"] == "enable"}
    assert tools["customer"] == ["cust_flights", "cust_flight", "cust_request", "end_turn", "wait_turn", "resign"]
    one = tasks3.steps(world.task, "one")
    [agent] = [s for s in one if s["type"] == "deploy_agent"]
    assert agent["role"] == "both" and agent["env_vars"] == {"AIROPS_MAX_COST_USD": "12"}
    [play] = [s for s in one if s["type"] == "prompt_agent"]
    assert "cust_flight" in play["system_prompt"] and play["max_turns"] == 10 * 25 + 10
    idle = tasks3.steps(world.task, "idle")
    assert [s["type"] for s in idle] == ["deploy_env", "apply_server_config", "register_env_triggers",
                                         "sync_env_clock", "apply_server_config", "env_outcome_verifier"]
    assert all(s.get("env_id", "airops-occ3") == "airops-occ3" for s in joint + one + idle)
    assert "rebook" in tasks3.RULES["customer"] and "PROTECT" in tasks3.RULES["ops"]


@pytest.mark.anyio
async def test_every_tool_of_the_env_is_strict_and_the_customer_desk_has_three():
    async with Client(OccEnv3().create_app()) as c:
        listed = await c.list_tools()
    assert {t.name: strict(t.inputSchema, t.name) for t in listed} == {t.name: [] for t in listed}
    names = {t.name for t in listed}
    assert len(listed) == 23 and {"cust_flights", "cust_flight", "cust_request"} <= names
    assert next(t for t in listed if t.name == "ops_request").inputSchema["properties"]["to"]["enum"] == ["crew",
                                                                                                          "customer"]


def test_the_customer_desks_script_holds_a_missed_connection_and_protects_a_stranded_flight():
    script, calls = desk_agent.Script("customer"), []
    page = ("# Customer desk, JetBlue, 2024-07-28, 10:00 (watch 7 of 37, customer turn); freeze line 11:00: holds go\n"
            "## Connections at risk\n"
            "f218 947 JFK-BZN 11:05: f80 MCO in 10:38 -13 12; misses 12, hold to 11:18 makes them, 12 have no later "
            "seat today\n"
            "f220 948 JFK-BUF 11:05: f81 MCO in 10:38 -13 4; misses 4, hold to 11:18 makes them, 4 have no later seat "
            "today\n"
            "f230 523 JFK-LAX 11:00 committed: f82 MCO in 10:08 -2 30; misses 30, hold to 11:02 makes them, 0 have no "
            "later seat today\n"
            "## Flights ops might cancel\n"
            "f32 1434 SJU-MCO 12:00 150(0/16) no crew; cancelled now: 1 same day / 149 later day / 0 refund; no later "
            "seat today 120\n"
            "f33 1435 SJU-MCO 13:00 150(0/16) no crew; cancelled now: 100 same day / 50 later day / 0 refund; no later "
            "seat today 10")

    async def call(name, args):
        calls.append((name, args))
        return {}
    checks.done(script.turn(page, call))
    checks.done(script.turn(page, call))
    assert calls == [("cust_request", {"type": "HOLD", "flight": "f218", "until": "11:18", "note": None}),
                     ("cust_request", {"type": "PROTECT", "flight": "f32", "until": None, "note": None})]
    ops = desk_agent.Script("ops")
    checks.done(ops.turn("R4 from customer at 10:00: HOLD f218 until 11:18 (12 pax)\n"
                         "R5 from customer at 10:00: PROTECT f32 (150 pax)", call))
    assert calls[-1] == ("ops_reply", {"request": "R4", "answer": "accept", "text": None})
    assert desk_agent.Episode(desk_agent.DeskConfig(role="customer"), {}).desk == "customer"


def test_the_customer_page_counts_where_a_cancelled_flights_passengers_go(world):
    d = OccDay3(world)
    proj = d.project(d.plan)
    got = customer.if_cancelled(d, proj, 3)
    assert sum(got.values()) == 100 and got[customer.LATER] == 100
    assert customer.no_later(d, proj, 3) == 100 and customer.no_later(d, proj, 2) == 90
