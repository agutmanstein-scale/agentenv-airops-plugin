import json
from dataclasses import replace

from day_helpers import capped, crewed, day, flight, tail, three_into_a_program
from program_helpers import adv
from test_reference_plays import committed_then_stopped, stopped_then_released

from agentenv_airops.programs import references, sim
from agentenv_airops.v3day import clock
from agentenv_airops.v3day.day import Day
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


def test_reward_is_one_at_the_anchor_and_zero_at_doing_nothing():
    assert reward(100, 100, 50) == 0.0 and reward(50, 100, 50) == 1.0 and reward(75, 100, 50) == 0.5
    assert reward(140, 100, 50) == 0.0 and reward(40, 100, 50) == 1.0
    assert reward(100, 100, 100) == 1.0 and reward(101, 100, 100) == 0.0


def test_grade_prices_the_plan_on_the_truth_against_naive_and_anchor():
    task = stopped_then_released()
    plan = {0: {"cancel": True, "decided": 0}}
    g = grade(task, plan)
    assert g["cost"]["total"] == sim.execute(task, plan).cost.total
    assert g["naive"] == references.naive(task).cost.total and g["anchor"] == references.anchor(task)[1].cost.total
    assert g["reward"] == round(reward(g["cost"]["total"], g["naive"], g["anchor"]), 6) < 1
    assert grade(task, {}, anchor=g["anchor"])["reward"] == reward(g["naive"], g["naive"], g["anchor"])


def test_the_hubs_advisories_are_the_news():
    task = day([flight(0, "BUF", "JFK", 100, 60)], [tail("N1", "BUF", end="JFK")],
               advisories=(adv("a2", "gdp_proposed", 50, 200, 400, (30,) * 4), adv("a1", "gs", 10, 10, 90)))
    assert [(n.id, n.at) for n in clock.news(task)] == [("a1", 10), ("a2", 50)]
    assert "proposed ground delay program 03:20-06:40" in clock.news(task)[1].text
