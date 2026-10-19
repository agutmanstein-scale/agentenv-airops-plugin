"""A recorded control-center day as the viewer's episode: v1's shape with the room in `occ`, valid under the schema and
listed as such; the crew tags at the gate by the desks' flag rule; each watch's projection from an exact replay."""

import json

import pytest
from occ_helpers import play, scripted, storm

from agentenv_airops import export
from agentenv_airops.occ import episode


@pytest.fixture(scope="module")
def played():
    w = storm()
    return w, play(w, policies=scripted("crew", "ops")).data()


def test_a_played_day_is_an_episode_the_viewer_reads_with_the_room_in_it(played, tmp_path):
    w, data = played
    ep = episode.episode(w, data, model="openai/gpt-6.1-sol", run="pilot-joint", inputs=tmp_path, cache=tmp_path)
    export.validate(ep)
    assert (ep["task"]["hub"], ep["reward"], ep["reference"]["anchor"]) == (w.task.hub, data["grade"]["reward"],
                                                                            w.refs["best"])
    assert [x["id"] for x in ep["executed"]] == sorted(f.id for f in w.task.flights)
    cancel = next(r for r in ep["occ"]["requests"] if r["type"] == "CANCEL")
    assert (cancel["sender"], cancel["receiver"], cancel["flight"], cancel["status"], cancel["reply"]) == (
        "crew", "ops", 2, "accepted", "accept")
    assert {"t": 0, "desk": "ops", "action": "cancel", "flight": 2, "detail": None, "ref": cancel["id"],
            "via": "accept"} in ep["occ"]["decisions"]
    assert [c["id"] for c in ep["watches"][0]["changes"] if c["action"] == "cancel"] == [2]
    assert ep["stage"] is None and [r["reward"] for r in ep["occ"]["refs"]][0] == 1.0
    index = json.loads(export.folder([ep], tmp_path)["episodes/index.json"])
    assert index["episodes"][0]["occ"] == "two desks" and index["episodes"][0]["task_id"] == w.task.task_id


def test_a_gate_tag_shows_no_crew_and_a_crew_clock_only_near_the_flights_departure():
    def log(*rows):
        return [{"at": at, "status": status, "until": until} for at, status, until in rows]

    status = {"1": log(("04:00", "legal", None), ("05:00", "no crew", None), ("06:00", "times out", "09:00"),
                       ("06:00", "times out", "09:30"), ("08:40", "flown", None)),
              "2": log(("04:00", "times out", "12:00"), ("09:00", "flown", None)),
              "3": log(("04:00", "times out", "12:00"), ("07:00", "cancelled", None))}
    executed = {1: {"status": "flown", "dep": 520}, 2: {"status": "flown", "dep": 540},
                3: {"status": "cancelled", "dep": None}}
    assert episode.crew_tags(status, executed) == {"1": [[300, "no crew", None], [360, "times out", 570],
                                                         [520, None, None]],
                                                   "3": [[240, "times out", 720], [420, None, None]]}


def test_each_watch_projects_the_plan_it_left_from_an_exact_replay(played):
    w, data = played
    out = episode.projections(w, data)
    assert len(out) == len(data["estimates"]) and all(len(p) == len(w.task.flights) for p in out)
    assert next(r for r in out[0] if r["id"] == 2)["status"] == "cancelled"
