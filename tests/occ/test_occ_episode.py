"""A recorded control-center day as the viewer's episode: v1's shape with the room in `occ`, valid under the schema and
listed as such, the turns spread over the hour after their watch; the crews with their legal limits; the crew tags at
the gate by the desks' flag rule; each watch's projection from a replay of the calls that change the day, as the exact
replay has it."""

import json

import pytest
from occ_helpers import play, scripted, storm

from agentenv_airops import export
from agentenv_airops.occ import episode
from agentenv_airops.occ.day import OccDay
from agentenv_airops.occ.replay import replay
from agentenv_airops.programs import crews


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
    [decision] = ep["occ"]["decisions"]
    assert decision == {"t": 0, "shown": cancel["answered"], "desk": "ops", "action": "cancel", "flight": 2,
                        "detail": None, "ref": cancel["id"], "via": "accept", "tool": "ops_reply"}
    turns = ep["occ"]["turns"]
    assert all(u["t"] + episode.ROOM[0] <= u["from"] < u["to"] <= u["t"] + episode.ROOM[1] for u in turns)
    assert [u["desk"] for u in turns[:2]] == ["crew", "ops"] and turns[0]["to"] == turns[1]["from"]
    assert turns[0]["from"] < cancel["shown"] < turns[0]["to"] <= cancel["answered"] < turns[1]["to"]
    c1, p1 = ep["occ"]["units"]["C1"], ep["occ"]["units"]["P1"]
    assert c1["duties"][0]["limit"] == 840 + crews.CABIN_DUTY - crews.DEBRIEF and c1["size"] == 4
    assert p1["duties"][0]["ext"] - p1["duties"][0]["limit"] == crews.EXTENSION
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


def test_each_watch_projects_the_plan_it_left_as_an_exact_replay_does(played):
    w, data = played
    out = episode.projections(w, data)
    exact = []

    class Watched(OccDay):
        def _close(self):
            super()._close()
            flights = self.project(self.plan).day.run.flights
            exact.append([(i, r.status, r.dep, r.arr) for i, r in sorted(flights.items())])

    assert replay(w, data, Watched).exact and exact == [[(r["id"], r["status"], r["dep"], r["arr"]) for r in p]
                                                        for p in out]
    assert len(out) == len(data["estimates"]) and all(len(p) == len(w.task.flights) for p in out)
    assert next(r for r in out[0] if r["id"] == 2)["status"] == "cancelled"


def test_a_desks_words_read_with_flight_numbers_and_their_glued_fragments_apart(played):
    w, _ = played
    f0, f2 = (w.task.flight(i).flight for i in (0, 2))
    assert episode.prose("f0 waits; f212:40 cancel, DHf2 now; delay1289m,f2 ok; f99", w.task) == \
        f"{f0} waits; {f2} 12:40 cancel, DH {f2} now; delay 1289m, {f2} ok; f99"
    assert episode.prose(None, w.task) is None
