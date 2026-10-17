import json
import math
import random
from dataclasses import replace

from day_helpers import (
    capped,
    crewed,
    day,
    flight,
    network_day,
    random_day,
    reserve_day,
    tail,
    three_into_a_program,
)
from program_helpers import adv
from test_reference_plays import committed_then_stopped, delay_revealed_late, stopped_then_released

from agentenv_airops.core.model import hhmm
from agentenv_airops.core.sim import FLOWN
from agentenv_airops.programs import mip, references, sim
from agentenv_airops.v3day import clock
from agentenv_airops.v3day.day import Day, changes_to, play
from agentenv_airops.v3day.grade import grade, reward
from agentenv_airops.v3day.situation import KEYS


def held_then_released():
    """f0 JFK-BUF at 01:40 and f1 JFK-MCO at 08:20, each on its own aircraft at JFK."""
    return day([flight(0, "JFK", "BUF", 100, 60), flight(1, "JFK", "MCO", 500, 150)],
               [tail("N1", "JFK", end="BUF"), tail("N2", "JFK", end="MCO")])


def cancel(i: int) -> list[dict]:
    return [{"flight": f"f{i}", "action": "cancel"}]


def test_watches_are_the_followers_watch_times():
    task = stopped_then_released()
    assert [w.t for w in Day(task).watches] == references.watch_times(task) == clock.watch_times(task)


def test_news_arrives_at_the_first_watch_after_its_signature():
    d = Day(stopped_then_released())
    assert [n["id"] for n in d.drain()] == ["s1"] and d.drain() == []
    for _ in range(3):
        assert d.advance()["news"] == []
    assert d.t == 180 and d.drain() == []
    assert d.advance() == {"watch": 4, "time": "04:00", "freeze_line": "05:00", "frozen": 1, "news": ["c1"]}
    assert [(n["id"], n["at"], n["kind"]) for n in d.drain()] == [("c1", "03:20", "gs_cnx")]
    assert [(e["news_id"], e["watch"], e["via"]) for e in d.log] == [("s1", 0, "load"), ("c1", 4, "watch")]


def test_each_call_costs_a_minute_of_the_watch_and_acts_at_the_clock():
    d = Day(stopped_then_released())
    assert d.clock == 0
    assert d.situation()["clock"] == "00:00" and d.clock == clock.CALL_MIN
    assert d.check(cancel(1))["now"] == "00:02"
    d.confirm(cancel(1))
    assert d.plan == {1: {"cancel": True, "decided": 2}} and d.clock == 3 and d.calls == 3
    assert clock.virtual_time(d.task, d.clock) == "2024-01-01T05:03:00Z"


def test_the_watch_ends_when_its_minutes_run_out():
    d = Day(stopped_then_released())
    for _ in range(59):
        assert "watch_over" not in d.situation()
    out = d.situation()
    assert out["watch_over"]["watch"] == 1 and d.t == 60 and d.clock == 60
    while not d.last:
        d.advance()
    for _ in range(59):
        d.situation()
    assert d.situation()["watch_over"]["done"] and d.done and d.end_reason == "time"
    assert d.situation() == {"error": "the day is over"}


def test_a_flight_leaving_before_the_freeze_line_commits():
    task = committed_then_stopped()
    d = Day(task)
    d.advance()
    assert d.t == 60 and d.frozen == {0}
    out = d.confirm(cancel(0))
    assert out["confirmed"] == [] and out["refused"][0]["flight"] == "f0" and "frozen" in out["refused"][0]["reason"]
    assert d.plan == {} and d.situation()["flights"][0]["status"] == "frozen"


def test_a_cancellation_is_final_from_the_next_watch():
    d = Day(stopped_then_released())
    d.confirm(cancel(0))
    d.confirm([{"flight": "f0", "action": "restore"}])
    assert d.plan == {}
    d.confirm(cancel(0))
    d.advance()
    assert 0 in d.frozen
    out = d.confirm([{"flight": "f0", "action": "restore"}])
    assert out["refused"] and d.plan == {0: {"cancel": True, "decided": 2}}
    assert d.situation()["flights"][0]["status"] == "cancelled"


def test_an_open_flight_is_held_to_the_freeze_line():
    d = Day(held_then_released())
    d.confirm([{"flight": "f0", "action": "hold", "not_before": "03:20"}])
    d.advance()
    assert d.t == 60 and 0 not in d.frozen
    preview = d.check([{"flight": "f0", "action": "restore"}])
    assert preview["held_to_freeze_line"] == ["f0"] and d.plan == {0: {"not_before": 200}}
    out = d.confirm([{"flight": "f0", "action": "restore"}])
    assert out["confirmed"] == ["f0"] and out["held_to_freeze_line"] == ["f0"]
    assert d.plan == {0: {"not_before": 120}} and d.project(d.plan).flights[0].dep == 120
    assert d.changes[-1]["now"] == {"not_before": 120}


def test_check_previews_without_changing_the_plan():
    d = Day(stopped_then_released())
    out = d.check(cancel(1))
    assert d.plan == {} and out["cost_now"] == d.project({}).cost.total
    assert out["cost"]["total"] == d.project({1: {"cancel": True, "decided": 0}}).cost.total
    assert [r.split()[0] for r in out["flights"]] == ["f1"] and "cancelled" in out["flights"][0]


def test_bad_changes_are_reported():
    d = Day(stopped_then_released())
    assert "not valid JSON" in d.confirm("cancel f0")["error"]
    out = d.confirm([{"flight": "f9", "action": "cancel"}, {"flight": "f0", "action": "divert"},
                     {"flight": "f1", "action": "hold", "not_before": "9pm"}])
    assert len(out["problems"]) == 3 and d.plan == {} and d.calls == 2


def test_situation_keys_are_all_there_and_json_able():
    d = Day(stopped_then_released())
    s = d.situation()
    assert set(KEYS) <= set(s) and json.loads(json.dumps(s))["watch"] == 0
    assert s["programs"]["JFK"]["stops"] == [{"chain": "s1", "from": "00:00", "to": "10:00"}]
    row = s["flights"][0]
    assert {"id", "status", "known_delay", "plan", "dep", "arr", "late", "tail", "slot", "hold"} <= set(row)
    assert row["status"] == "auto-cancel" and row["dep"] is None and s["aircraft"]["stations"]["BUF"][0]["tail"] == "N1"
    assert "First watch" in d.text() and "## Flights" in d.text()
    d.advance()
    assert "Freeze line 02:00" in d.text()


def test_programs_as_issued_show_a_gdp_and_its_slots():
    s = Day(three_into_a_program()).situation()
    gdps = s["programs"]["JFK"]["gdps"]
    assert [g["id"] for g in gdps] == [s.id for s in sim.known(three_into_a_program(), 0).segments["JFK"]]
    assert gdps[0]["kind"] == "gdp" and gdps[0]["period"] == ["02:30", "06:40"]


def test_departure_caps_are_known_cap_look_ahead():
    task = capped(held_then_released(), {"JFK": {2: 1, 5: 1}})
    d = Day(task)
    assert d.situation()["dep_caps"] == {"JFK": {"02": 1}}
    d.advance()
    d.advance()
    assert d.t == 120 and d.situation()["dep_caps"] == {"JFK": {"02": 1, "05": 1}}
    assert Day(held_then_released()).situation()["dep_caps"] == {}


def test_crew_deadline_and_rest_are_shown():
    task = held_then_released()
    task = replace(task, flights=(crewed(task.flight(0), 200), task.flight(1)))
    rows = Day(task).situation()["flights"]
    assert rows[0]["crew"] == {"pairing": "N1/0", "deadline": "03:20", "rest": "13:20"} and "crew" not in rows[1]


def call(i: int) -> list[dict]:
    return [{"flight": f"f{i}", "action": "call_reserve"}]


def test_a_reserve_call_flies_a_base_departure_past_its_crew_deadline_while_the_pool_lasts():
    d = Day(reserve_day())
    before = d.situation()
    assert before["reserves"] == {"JFK": {"pool": 1, "called": [], "left": 1}}
    assert before["flights"][0]["crew"] == {"pairing": "N1/0", "deadline": "02:30", "rest": "12:30", "reserve": False}
    assert before["flights"][0]["status"] == "auto-cancel"
    out = d.confirm(call(1))
    assert out["confirmed"] == ["f1"] and d.plan == {1: {"reserve": True}} and out["auto_cancelled"] == ["f0"]
    s = d.situation()
    assert s["reserves"]["JFK"] == {"pool": 1, "called": ["f1"], "left": 0}
    row = next(r for r in s["flights"] if r["id"] == "f1")
    assert row["crew"]["reserve"] and row["dep"] == "03:25" and row["plan"] == "reserve"
    assert "JFK: 0 of 1 left (called: f1)" in d.text() and "02:30/12:30+R" in d.text()
    refused = d.confirm(call(0))["refused"]
    assert len(refused) == 1 and "no reserve crew is left at JFK" in refused[0]["reason"] and 0 not in d.plan


def test_a_reserve_is_refused_off_a_pooled_base_and_on_a_cancelled_flight_and_kept_by_a_hold():
    d = Day(reserve_day())
    assert "doesn't leave a crew base" in d.confirm(call(2))["refused"][0]["reason"]
    d.confirm(cancel(1))
    assert "is cancelled" in d.confirm(call(1))["refused"][0]["reason"]
    d.confirm([{"flight": "f1", "action": "restore"}, *call(1), {"flight": "f1", "action": "hold",
                                                                 "not_before": "03:40"}])
    assert d.plan[1] == {"not_before": 220, "reserve": True}
    assert d.check(call(1))["refused"] == [] and d.confirm(call(1))["unchanged"] == ["f1"]
    d.confirm(cancel(1))
    assert d.plan[1] == {"cancel": True, "decided": d.clock - 1} and d.situation()["reserves"]["JFK"]["left"] == 1


def test_a_scripted_policy_playing_the_anchors_reserve_calls_scores_the_anchors_cost():
    task = reserve_day()
    sol = mip.solve(task)
    target = sol.plan(task.start)
    rows = changes_to(Day(task), target)
    assert {"flight": "f1", "action": "call_reserve"} in rows
    played = play(task, lambda d: target, anchor=sol.objective)
    assert played.plan[1]["reserve"] and played.result["cost"]["total"] == sol.objective


def test_without_a_pool_the_situation_shows_no_reserves():
    task = replace(reserve_day(), layers=None)
    d = Day(task)
    assert d.situation()["reserves"] == {} and "Reserve crews" not in d.text()
    assert "reserve" not in d.situation()["flights"][0]["crew"]


def test_reward_is_step0s_curve_one_at_the_anchor_and_never_below_the_floor_even_on_a_day_costing_nothing():
    assert reward(50, 100, 50) == 1.0 and reward(40, 100, 50) == 1.0
    assert reward(100, 100, 50) == 0.2 + 0.8 * math.exp(-2)
    assert reward(75, 100, 50) == 0.2 + 0.8 * math.exp(-1)
    assert 0.2 <= reward(10_000, 100, 50) < 0.2 + 1e-9
    assert reward(101, 100, 100) == 0.2 + 0.8 * math.exp(-0.1 / 0.5)
    assert (reward(0, 0, 0), reward(5, 0, 0)) == (1.0, 0.2)


def test_grade_prices_the_plan_on_the_truth_against_naive_and_anchor():
    task = stopped_then_released()
    plan = {0: {"cancel": True, "decided": 0}}
    g = grade(task, plan)
    assert g["cost"]["total"] == sim.execute(task, plan).cost.total
    assert g["naive"] == references.naive(task).cost.total and g["anchor"] == references.anchor(task)[1].cost.total
    assert g["reward"] == round(reward(g["cost"]["total"], g["naive"], g["anchor"]), 6) < 1
    rule = references.rule(task).cost
    assert g["references"] == {"rule": {"cost": rule, "reward": round(reward(rule, g["naive"], g["anchor"]), 6)},
                               "follower": None}
    assert grade(task, {}, anchor=g["anchor"])["reward"] == reward(g["naive"], g["naive"], g["anchor"])
    assert grade(task, plan, anchor=g["anchor"], follower=g["naive"])["references"]["follower"] == \
        {"cost": g["naive"], "reward": round(reward(g["naive"], g["naive"], g["anchor"]), 6)}


def test_the_hubs_advisories_are_the_news():
    task = day([flight(0, "BUF", "JFK", 100, 60)], [tail("N1", "BUF", end="JFK")],
               advisories=(adv("a2", "gdp_proposed", 50, 200, 400, (30,) * 4), adv("a1", "gs", 10, 10, 90)))
    assert [(n.id, n.at) for n in clock.news(task)] == [("a1", 10), ("a2", 50)]
    assert "proposed ground delay program 03:20-06:40" in clock.news(task)[1].text


def early_leaver():
    """f0 JFK-BOS at 00:10, ten minutes into the first watch, and f1 back at 03:20, on one aircraft."""
    return day([flight(0, "JFK", "BOS", 10, 60), flight(1, "BOS", "JFK", 200, 60)], [tail("N1", "JFK")])


def test_a_flight_gone_by_the_clock_at_the_first_watch_stands():
    d = Day(early_leaver())
    for _ in range(40):
        d.situation()
    assert d.clock == 40 and d.situation()["flights"][0]["status"] == "airborne"
    for change in (cancel(0), [{"flight": "f0", "action": "hold", "not_before": "00:50"}]):
        out = d.confirm(change)
        assert out["confirmed"] == [] and "is gone: it left at 00:10" in out["refused"][0]["reason"]
    assert d.plan == {} and d.project(d.plan).flights[0].dep == 10


def test_a_flight_restored_at_the_first_watch_leaves_no_earlier_than_the_clock():
    d = Day(early_leaver())
    d.confirm(cancel(0))
    for _ in range(20):
        d.situation()
    out = d.confirm([{"flight": "f0", "action": "restore"}])
    assert out["confirmed"] == ["f0"] and out["held_to_freeze_line"] == ["f0"]
    assert d.plan == {0: {"not_before": 21}} and d.project(d.plan).flights[0].dep == 21


def changes_keep_the_day_so_far(seed: int) -> list[tuple]:
    """Random cancels, restores and holds at every watch after the first; what moved among the flights frozen or
    gone by the clock (outcome, departure, aircraft), and the open flights left before the freeze line."""
    rng = random.Random(seed)
    task = network_day(rng) if seed % 2 else random_day(rng)
    d, bad = Day(task, anchor=0), []
    while not d.done:
        if d.watch > 0:
            before, at = d.project(d.plan).flights, d.clock
            pinned = d.frozen | {i for i, r in before.items() if r.status == FLOWN and r.dep < at}
            rows = [{"flight": f"f{i}", "action": (a := rng.choice(["cancel", "restore", "hold"])),
                     "not_before": hhmm(task.flight(i).sched_dep + rng.randint(0, 200)) if a == "hold" else None}
                    for i in rng.sample([f.id for f in task.flights], min(3, len(task.flights)))]
            out = d.confirm(rows)
            if d.done or "watch_over" in out:
                continue
            after, truth = d.project(d.plan).flights, sim.execute(task, d.plan).flights
            bad += [(seed, d.watch, i) for i in pinned if _kept(before[i]) != _kept(after[i])]
            bad += [(seed, d.watch, i, "open") for i, r in after.items()
                    if r.status == FLOWN and i not in d.frozen and at <= r.dep < d.line
                    and not (truth[i].status == FLOWN and truth[i].dep < d.clock)]
        d.advance()
    return bad


def _kept(r) -> tuple:
    return r.status, r.dep, r.tail


def test_no_change_reaches_back_past_the_freeze_or_the_clock():
    assert [b for seed in range(130) for b in changes_keep_the_day_so_far(seed)] == []


def changes_keep_the_truth_so_far(seed: int) -> list[tuple]:
    """Random changes after a random stretch of thinking at every watch; what moved, on the truth the grade prices,
    among the flights that had left or auto-cancelled on the truth by the clock."""
    rng = random.Random(seed)
    task = network_day(rng) if seed % 2 else random_day(rng)
    d, bad = Day(task, anchor=0), []
    while not d.done:
        for _ in range(rng.randint(0, 50)):
            if "watch_over" in d.situation():
                break
        if d.done:
            break
        before, at = sim.execute(task, d.plan).flights, d.clock
        gone = {i for i, r in before.items() if (r.status == FLOWN and r.dep < at)
                or (r.status == "auto_cancelled" and r.decided < at)}
        rows = [{"flight": f"f{i}", "action": (a := rng.choice(["cancel", "restore", "hold"])),
                 "not_before": hhmm(task.flight(i).sched_dep + rng.randint(0, 200)) if a == "hold" else None}
                for i in rng.sample([f.id for f in task.flights], min(3, len(task.flights)))]
        out = d.confirm(rows)
        after = sim.execute(task, d.plan).flights
        bad += [(seed, d.watch, i) for i in gone if _kept(before[i]) != _kept(after[i])]
        if not d.done and "watch_over" not in out:
            d.advance()
    return bad


def test_no_change_reaches_back_past_what_has_left_on_the_truth():
    assert [b for seed in range(60) for b in changes_keep_the_truth_so_far(seed)] == []


def test_a_flight_gone_on_the_truth_by_the_clock_is_shown_gone_and_stands():
    task = random_day(random.Random(48))
    d = Day(task, anchor=0)
    d.advance()
    while d.clock < 109:
        d.situation()
    assert (sim.execute(task, d.plan).flights[1].status, sim.execute(task, d.plan).flights[1].dep) == (FLOWN, 62)
    assert d.situation()["flights"][1]["status"] in ("airborne", "flown")
    out = d.confirm(cancel(1))
    assert out["confirmed"] == [] and "is gone: it left at 01:02" in out["refused"][0]["reason"]
    assert d.plan == {} and sim.execute(task, d.plan).flights[1].status == FLOWN


def test_a_delay_shows_only_once_it_is_known():
    d = Day(delay_revealed_late())
    assert d.situation()["flights"][0]["known_delay"] is None
    assert "B6 100 DCA-JFK".split()[-1] in d.text() and " ? " in d.text().split("## Flights")[1]
    while d.t + 120 < 400:
        d.advance()
    assert d.situation()["flights"][0]["known_delay"] == 300 and " 300 " in d.text().split("## Flights")[1]


def test_news_signed_on_a_watch_minute_arrives_at_that_watch():
    task = replace(held_then_released(), programs=day(
        [flight(0, "JFK", "BUF", 100, 60)], [tail("N1", "JFK")],
        advisories=(adv("a1", "gs", 60, 60, 120),)).programs)
    d = Day(task)
    assert d.drain() == [] and d.advance()["news"] == ["a1"] and d.t == 60


def test_each_flight_row_is_its_projection():
    d = Day(three_into_a_program())
    run, rows = d.project(d.plan).flights, d.situation()["flights"]
    for row in rows:
        r, f = run[int(row["id"][1:])], d.task.flight(int(row["id"][1:]))
        assert (row["dep"], row["arr"], row["tail"]) == (r.dep and hhmm(r.dep), r.arr and hhmm(r.arr), r.tail)
        assert row["late"] == (r.arr - f.sched_arr if r.status == FLOWN else None)
        assert row["slot"] == (r.slot and {"program": r.program, "at": hhmm(r.slot)})
    assert [(r["status"], r["dep"], r["arr"], r["late"], r["tail"]) for r in rows] == [
        ("auto-cancel", None, None, None, None), ("auto-cancel", None, None, None, None),
        ("open", "05:41", "06:41", 241, "N3")]


def test_every_aircraft_is_somewhere_at_every_watch():
    rng = random.Random(4)
    for _ in range(6):
        d = Day(network_day(rng), anchor=0)
        while not d.done:
            a = d.situation()["aircraft"]
            if "watch_over" in a:
                break
            where = [x["tail"] for xs in a["stations"].values() for x in xs] + [x["tail"] for x in a["airborne"]]
            assert sorted(where) == sorted(t.tail for t in d.task.tails)
            assert all(x["lands"] > hhmm(d.clock - 1) for x in a["airborne"])
            d.advance()
