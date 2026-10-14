"""A replayed day as the viewer's Episode JSON: valid against web/episode.schema.json and consistent with the day the
env played (each watch's plan and cost as known, the final plan on the truth), its changes one per flight and watch,
and the viewer folder with the radar frames and hub twin it points at."""

import json
from pathlib import Path

import pytest

from agentenv_airops import export
from agentenv_airops.core.known import view
from agentenv_airops.core.model import hhmm
from agentenv_airops.core.sim import FLOWN, execute
from agentenv_airops.day import Day
from agentenv_airops.episodes import replay

SONNET = "anthropic/claude-sonnet-5-5"


@pytest.fixture(scope="module")
def played(task, e2e):
    return replay(task, e2e["messages"])


@pytest.fixture(scope="module")
def episode(task, played):
    return export.episode(task, played.day.data(), played.steps, model=SONNET, run="e2e", cache=Path("nowhere"),
                          record={"sweep": "e2e", "rep": 1})


def test_the_e2e_episode_is_one_the_viewer_reads(task, played, episode):
    export.validate(episode)
    day = played.day
    assert (episode["reward"], episode["cost"]["total"], episode["model_label"]) == (0.840588, 691780,
                                                                                     "Claude Sonnet 5.5")
    assert episode["reference"]["anchor"] == 667266 and "hindsight_plan" not in episode["reference"]
    assert [w["t"] for w in episode["watches"]] == [w.t for w in day.watches]
    assert episode["watches"][0]["t"] == task.start
    assert [w["est_cost"] for w in episode["watches"]] == [e["cost"]["total"] for e in day.estimates]
    assert [w["frozen"] for w in episode["watches"]] == [len(f["frozen"]) for f in day.freezes]
    assert all(n["at"] <= w["t"] for w in episode["watches"] for n in w["news"])
    assert [n["id"] for n in episode["news"]] == [n for w in day.watches for n in w.news]
    assert episode["steps"] == played.steps and episode["record"] == {"sweep": "e2e", "rep": 1}
    assert [{k: r[k] for k in ("id", "tail", "status", "dep", "arr", "arr_delay")} for r in episode["executed"]] == (
        day.grade["executed"])
    plan = {i: e for i, e in day.plan.items()}
    for r in episode["executed"]:
        e = plan.get(r["id"])
        assert r["not_before"] == (None if not e or e["cancel"] else e["not_before"])
        assert r["held"] == (r["status"] == FLOWN and r["not_before"] is not None
                             and r["not_before"] > task.flight(r["id"]).sched_dep + task.flight(r["id"]).lb)
    assert {r["status"] for r in episode["executed"]} >= {"flown", "cancelled"}
    total = episode["cost"]
    assert total["pax_delay"] + total["cancel"] + total["ferry"] == total["total"]
    assert (episode["radar"], episode["twin"]) == (None, None)


def test_each_watch_is_the_plan_it_left_run_on_the_day_as_known(task, played, episode):
    plans = export.plans_by_watch(played.day.data())
    assert plans[-1] == played.day.plan
    for k, w in enumerate(episode["watches"]):
        run = execute(task, plans[k], *view(task, w["t"]))
        assert [(r["id"], r["status"], r["dep"], r["arr"]) for r in w["projected"]] == [
            (r.id, r.status, r.dep, r.arr) for r in run.flights.values()]
        assert w["cost_known"] == run.cost.to_dict() and w["counters"]["planning_calls"] == 1
        assert w["counters"]["cancelled"] == sum(r["status"] == "cancelled" for r in w["projected"])
        assert set(w["caps_known"]) == set(task.caps) | set(task.nominal)


def test_a_watch_lists_one_change_per_flight_from_what_it_was_to_what_it_left(task):
    day = Day(task)
    day.confirm([{"flight": "f6", "action": "cancel", "not_before": None},
                 {"flight": "f8", "action": "hold", "not_before": "21:30"}])
    day.confirm([{"flight": "f6", "action": "restore", "not_before": None},
                 {"flight": "f8", "action": "hold", "not_before": "21:45"}])
    day.open()
    day.confirm([{"flight": "f8", "action": "restore", "not_before": None},
                 {"flight": "f9", "action": "cancel", "not_before": None}])
    day.end()
    ep = export.episode(task, day.data(), [], model="follower", run="scripted", cache=Path("nowhere"))
    export.validate(ep)
    first, second = ep["watches"][0]["changes"], ep["watches"][1]["changes"]
    assert [(c["flight"], c["action"], c["not_before"], c["was"]) for c in first] == [
        (task.flight(8).flight, "hold", 1305, export.FLY)]
    assert [(c["id"], c["action"], c["by"]) for c in second] == [(8, "release", "planner"), (9, "cancel", "planner")]
    cancelled = next(r for r in ep["executed"] if r["id"] == 9)
    assert (cancelled["status"], cancelled["decided"]) == ("cancelled", day.watches[1].t)
    assert ep["model_label"].startswith("Reference follower") and ep["end_reason"] == "end_day"


def _released(task, f) -> Day:
    """``f`` held past the freeze line at the first watch and released at the second."""
    day = Day(task)
    t1 = day.watches[1].t
    day.confirm([{"flight": f"f{f.id}", "action": "hold", "not_before": hhmm(t1 + 120)}])
    day.open()
    day.confirm([{"flight": f"f{f.id}", "action": "restore", "not_before": None}])
    day.end()
    return day


def test_a_release_the_env_holds_to_the_freeze_line_is_a_hold_at_the_line(task):
    t1 = Day(task).watches[1].t
    f = next(f for f in sorted(task.flights, key=lambda f: f.sched_dep) if t1 <= f.sched_dep + f.lb < t1 + 60)
    day = _released(task, f)
    ep = export.episode(task, day.data(), [], model="follower", run="scripted", cache=Path("nowhere"))
    assert [(c["id"], c["action"], c["by"], c["not_before"]) for c in ep["watches"][1]["changes"]] == [
        (f.id, "hold", "planner", t1 + 60)]
    assert ep["watches"][1]["changes"][0]["was"] == {"cancel": False, "not_before": t1 + 120}


def test_an_unscored_day_is_not_an_episode(task, played):
    data = played.day.data() | {"reward": None}
    ep = export.episode(task, data, [], model=SONNET, run="e2e", cache=Path("nowhere"))
    with pytest.raises(ValueError, match="reward: None is not of type 'number'"):
        export.validate(ep)


def _cache(root: Path, task) -> Path:
    radar = root / "radar" / task.task_id
    radar.mkdir(parents=True)
    frames = [{"t": 1020, "utc": "2024-01-09T22:00Z", "url": "n0q_202401092200.png", "sha256": "a"},
              {"t": 1030, "utc": "2024-01-09T22:10Z", "url": "n0q_202401092210.png", "sha256": "b"}]
    (radar / "index.json").write_text(json.dumps({"task_id": task.task_id, "bounds": [-80, 38, -70, 44],
                                                  "projection": "mercator", "size": [10, 6], "step": 10,
                                                  "frames": frames, "attribution": "NEXRAD n0q: IEM"}))
    for f in frames:
        (radar / f["url"]).write_bytes(b"\x89PNG" + f["sha256"].encode())
    (root / "twin").mkdir()
    (root / "twin" / "JFK.geojson").write_text('{"type": "FeatureCollection", "features": []}')
    (root / "twin" / "index.json").write_text(json.dumps({"JFK": {"url": "JFK.geojson", "sha256": "c",
                                                                  "attribution": "© OpenStreetMap contributors"}}))
    return root


def test_the_viewer_folder_holds_the_episodes_their_index_and_the_radar_and_twin_they_point_at(task, played, tmp_path):
    cache = _cache(tmp_path / "cache", task)
    ep = export.episode(task, played.day.data(), played.steps, model=SONNET, run="e2e", cache=cache)
    assert ep["radar"]["frames"] == [{"t": 1020, "url": f"../radar/{task.task_id}/n0q_202401092200.png"},
                                     {"t": 1030, "url": f"../radar/{task.task_id}/n0q_202401092210.png"}]
    assert ep["twin"] == {"hub": "JFK", "url": "../twin/JFK.geojson", "sha256": "c",
                          "attribution": "Hub twin © OpenStreetMap contributors"}
    name = f"e2e.{export.slug(SONNET)}.{task.task_id}.json"
    files = export.folder([ep], cache, {"version": 1, "episodes": [{"url": "old.json"}]})
    assert sorted(files) == sorted([f"episodes/{name}", "episodes/index.json", f"radar/{task.task_id}/index.json",
                                    f"radar/{task.task_id}/n0q_202401092200.png",
                                    f"radar/{task.task_id}/n0q_202401092210.png", "twin/JFK.geojson"])
    index = json.loads(files["episodes/index.json"])
    assert [e["url"] for e in index["episodes"]] == ["old.json", name]
    assert index["episodes"][1] | {"url": None} == {
        "url": None, "task_id": task.task_id, "hub": "JFK", "date": "2024-01-09", "model": SONNET,
        "model_label": "Claude Sonnet 5.5", "run": "e2e", "reward": 0.840588, "cost": 691780, "anchor": 667266}
    out = tmp_path / "out"
    export.write(out, [ep], cache)
    export.write(out, [ep], cache)
    assert json.loads((out / "episodes" / name).read_text()) == ep
    assert [e["url"] for e in json.loads((out / "episodes/index.json").read_text())["episodes"]] == [name]
    assert (out / "twin/JFK.geojson").read_bytes() == (cache / "twin/JFK.geojson").read_bytes()
    export.write(cache, [ep], cache)
    assert (cache / "episodes" / name).is_file() and len(list((cache / "radar" / task.task_id).iterdir())) == 3
