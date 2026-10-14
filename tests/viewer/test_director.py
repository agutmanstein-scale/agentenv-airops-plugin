import gzip
import importlib.util
import json
from pathlib import Path

import pytest

from agentenv_airops import export

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("record_script", ROOT / "scripts" / "record.py")
record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record)
EPISODES = sorted((ROOT / "cache" / "episodes").glob("sol-v1.*.json"))


def leg(flight, kind, other, city, sched, at=None, taxi=12, tag=None, tone="ok", status="flown"):
    g = {"flight": flight, "kind": kind, "other": other, "city": city, "status": status, "sched": sched, "out": None,
         "off": None, "on": None, "in": None, "tag": tag or f"{flight} {'→' if kind == 'dep' else 'from'} {city}",
         "tone": tone}
    if at is not None:
        g.update({"out": at, "off": at + taxi} if kind == "dep" else {"on": at - taxi, "in": at})
    return g


def day():
    """A small storm day at Boston: a cancellation at the 10:00 watch on a parked aircraft, a ground stop the 11:00
    watch hears of, and B6 10 to Miami held until 11:30 whose aircraft lands from Miami at 10:48 first."""
    task = {"task_id": "b6-bos-20250216-0900", "carrier": "B6", "hub": "BOS", "date": "2025-02-16",
            "tz": "America/New_York", "window": [600, 720], "start": 540, "airports": {
                "BOS": {"name": "General Edward Lawrence Logan International Airport"},
                "MIA": {"name": "Miami International Airport"},
                "JFK": {"name": "John F Kennedy International Airport"}}}
    stop = {"kind": "advisory", "at": 630, "text": "GROUND STOP BOS", "data": {
        "element": "BOS", "kind": "gs", "gs_period": [625, 700], "flt_incl": "CARRIER JBU ONLY"}}
    watches = [{"t": t, "news": [stop] if t == 660 else [], "changes": [], "projected": []}
               for t in (540, 600, 660, 720)]
    watches[1]["changes"] = [{"id": 2, "flight": "B6 20", "orig": "BOS", "dest": "JFK", "action": "cancel",
                              "cancel": True, "not_before": None}]
    watches[1]["changes"].append({"id": 1, "flight": "B6 10", "orig": "BOS", "dest": "MIA", "action": "hold",
                                  "cancel": False, "not_before": 690})
    schedule = [{"id": i, "flight": f, "orig": o, "dest": d, "sched_dep": a, "sched_arr": b, "pax": pax}
                for i, f, o, d, a, b, pax in ((1, "B6 10", "BOS", "MIA", 610, 800, 140),
                                              (2, "B6 20", "BOS", "JFK", 640, 715, 90),
                                              (3, "B6 9", "MIA", "BOS", 420, 600, 150))]
    executed = [{"id": 1, "status": "flown", "dep": 700, "arr": 900, "held": True, "not_before": 690, "decided": 600},
                {"id": 2, "status": "cancelled", "dep": None, "arr": None, "held": False, "decided": 600},
                {"id": 3, "status": "flown", "dep": 460, "arr": 648, "held": False, "decided": None}]
    ep = {"task": task, "model": "openai/gpt-6.1-sol", "model_label": "GPT-6.1 Sol", "reward": 0.7,
          "watches": watches, "news": [stop], "schedule": schedule, "executed": executed}
    held = leg("B6 10", "dep", "MIA", "Miami", 610, 700, 15, tag="B6 10 → Miami · held until 11:30", tone="hold")
    traffic = {"hub": "BOS", "span": [540, 960], "weather": [{"t": 534, "vis_sm": 1.5}], "runways": [], "aircraft": [
        {"key": "N1", "tail": "N1", "carrier": "B6", "jetblue": True, "task": True, "visits": [
            {"stand": "C1", "from": 648, "to": 700, "arr": leg("B6 9", "arr", "MIA", "Miami", 600, 648, 8),
             "dep": held}]},
        {"key": "N2", "tail": "N2", "carrier": "B6", "jetblue": True, "task": True, "visits": [
            {"stand": "C2", "from": None, "to": None, "arr": None,
             "dep": leg("B6 20", "dep", "JFK", "New York", 640, tag="B6 20 → New York · cancelled", tone="cancel",
                        status="cancelled")}]},
        {"key": "N3", "tail": "N3", "carrier": "AA", "jetblue": False, "task": False, "visits": [
            {"stand": "B1", "from": None, "to": 620, "arr": None,
             "dep": leg("AA 1", "dep", "JFK", "New York", 615, 620) | {"tag": None, "tone": None}}]}],
        "featured": [{"key": "N1", "flight": "B6 10", "reason": "held", "t": 700}]}
    return ep, traffic


def frames(shot):
    return [t for t, _ in shot.clock(30)]


def test_the_director_tells_the_day_in_order_with_hard_cuts():
    ep, traffic = day()
    shots = record.direct(ep, traffic, seconds=48)
    names = [s.name for s in shots]
    assert names == ["establish", "decide", "inbound", "faa", "push", "taxi", "takeoff", "end"]
    assert 42 <= sum(s.seconds for s in shots) <= 50
    assert all(a.t0 <= b.t0 for a, b in zip(shots[1:-1], shots[2:-1], strict=False))
    assert all((a.camera, a.key) != (b.camera, b.key) for a, b in zip(shots, shots[1:], strict=False))
    assert all(max(s.speed) <= record.FAST[s.camera] for s in shots)
    assert shots[0].camera == "harbour" and shots[0].t0 == 540 and shots[0].reach == pytest.approx(1950)
    assert shots[-1].cards == ("end",) and shots[-1].t0 > shots[-2].t1


def test_a_decision_lands_on_its_gate_and_the_board_as_the_watch_opens():
    ep, traffic = day()
    decide = next(s for s in record.direct(ep, traffic, seconds=48) if s.name == "decide")
    assert (decide.key, decide.camera, decide.at) == ("N2", "gate", 600)
    assert frames(decide)[0] < 600 < frames(decide)[-1]
    assert decide.cards == ("head", "desk", "board", "tags")
    assert decide.caption == "GPT-6.1 Sol cancels B6 20 to New York and holds B6 10 to Miami until 11:30"
    assert decide.kicker == "10:00 · watch 2 of 4"
    assert {"B6 10", "B6 20"} <= record.Day(ep, traffic, None, None).board(600.3)


def test_the_held_flight_pushes_back_taxis_and_takes_off_on_camera():
    ep, traffic = day()
    shots = {s.name: s for s in record.direct(ep, traffic, seconds=48)}
    push, taxi, up, inbound = shots["push"], shots["taxi"], shots["takeoff"], shots["inbound"]
    assert frames(push)[0] < 700 < frames(push)[-1] and push.caption == "B6 10 to Miami, held until 11:30, pushes back"
    assert push.t1 < taxi.t0 and taxi.t1 < up.t0
    assert frames(up)[0] < 715 < frames(up)[-1]
    liftoff = min(range(len(frames(up))), key=lambda i: abs(frames(up)[i] - 715))
    assert liftoff / 30 == pytest.approx(5.5, abs=0.1)
    assert up.speed[0] > up.speed[1]
    assert frames(inbound)[0] < 640 < frames(inbound)[-1] and inbound.key == "N1"
    assert inbound.caption == "B6 9 from Miami lands 48 min late with the aircraft for B6 10"


def test_the_faa_shot_opens_on_the_watch_that_hears_of_the_stop():
    ep, traffic = day()
    faa = next(s for s in record.direct(ep, traffic, seconds=48) if s.name == "faa")
    assert (faa.camera, faa.at) == ("tower", 660) and frames(faa)[0] < 660 < frames(faa)[-1]
    assert faa.caption == "The FAA stops JetBlue flights to Boston Logan at their departure airports until 11:40"
    assert faa.kicker == "11:00 · Ground stop" and "map" in faa.cards


def test_a_short_film_drops_the_extras_and_a_poser_taxi_keeps_moving():
    ep, traffic = day()
    short = record.direct(ep, traffic, seconds=30)
    assert "inbound" not in {s.name for s in short} and {"push", "taxi", "takeoff"} <= {s.name for s in short}

    def poses(key, a, b):
        return [[a + i / 12, "taxi" if a + i / 12 > 708 else "hold", 8] for i in range(round((b - a) * 12) + 1)]

    taxi = next(s for s in record.direct(ep, traffic, poses=poses, seconds=48) if s.name == "taxi")
    assert taxi.t0 >= 708 - 1 / 12


def test_tags_follow_the_stage():
    ep, traffic = day()
    n1, n2 = traffic["aircraft"][0], traffic["aircraft"][1]
    assert record.tag_at(n2, 600)["flight"] == "B6 20" and record.tag_at(n2, 590) is None
    assert record.tag_at(n1, 650)["flight"] == "B6 9" and record.tag_at(n1, 675)["flight"] == "B6 10"
    late = n2 | {"visits": [n2["visits"][0] | {"dep": n2["visits"][0]["dep"] | {"decided": 760}}]}
    assert record.tag_at(late, 640) is None and record.tag_at(late, 760)["flight"] == "B6 20"
    assert [e["kind"] for e in record.sidecar_events(traffic) if e["key"] == "N1"] == [
        "landing", "gate_in", "pushback", "takeoff"]


def test_a_shot_reaches_its_moment_on_time():
    shot = record.Shot("takeoff", "takeoff", record.lead_in(100, 5.5, (4, 1.5), 8), 8, (4, 1.5))
    t = frames(shot)
    assert t[round(5.5 * 30)] == pytest.approx(100, abs=0.01) and shot.t1 == pytest.approx(t[-1], abs=0.01)
    assert record.until(1199) == "20:00" and record.until(1215) == "20:15" and record.late(212) == "3 h 32 min"


def test_the_rollout_shows_every_watch_from_the_tower_then_the_score():
    ep, traffic = day()
    ep["steps"] = [{"t": 600, "watch": 1, "tool": t} for t in ("get_situation", "check_plan", "check_plan",
                                                                 "confirm_changes", "advance")]
    shots = record.Day(ep, traffic, None, None).rollout()
    assert [(s.name, s.camera) for s in shots] == [("establish", "harbour"), *[("watch", "tower")] * 4,
                                                    ("end", "harbour")]
    watches = shots[1:-1]
    assert [s.t0 for s in watches] == [540, 600, 660, 720]
    assert [s.kicker for s in watches] == [
        "09:00 · watch 1 of 4", "10:00 · watch 2 of 4 · check_plan ×2 · confirm_changes", "11:00 · watch 3 of 4",
        "12:00 · watch 4 of 4"]
    assert [s.caption for s in watches] == [
        "GPT-6.1 Sol keeps its plan", "GPT-6.1 Sol cancels B6 20 to New York and holds B6 10 to Miami until 11:30",
        "The FAA stops JetBlue flights to Boston Logan at their departure airports until 11:40",
        "GPT-6.1 Sol keeps its plan"]
    assert [s.seconds for s in watches] == [2.2, 4.0, 4.0, 2.2]
    assert all(s.speed == (0, record.FAST["tower"]) and frames(s)[0] == frames(s)[1] for s in watches)
    assert all(s.cards == ("head", "desk", "board", "tags") for s in watches)
    assert shots[-1].cards == ("end",) and shots[-1].t0 > watches[-1].t1


@pytest.mark.skipif(not EPISODES, reason="no Sol episodes are exported under cache/episodes")
@pytest.mark.parametrize("path", EPISODES, ids=lambda p: p.stem.split(".")[-1])
def test_every_sol_day_makes_a_film(path):
    ep = json.loads(path.read_text())
    traffic = json.loads(gzip.decompress((path.parent / ep["stage"]["traffic"]).read_bytes()))
    hero = ep["task"]["task_id"] == "b6-bos-20250216-1500"
    shots = record.direct(ep, traffic, seconds=66 if hero else 40)
    length = sum(s.seconds for s in shots)
    assert (60 <= length <= 75) if hero else (30 <= length <= 45)
    assert [s.name for s in shots][:1] == ["establish"] and shots[-1].name == "end"
    assert {"push", "taxi", "takeoff", "decide"} <= {s.name for s in shots}
    assert all(max(s.speed) <= record.FAST[s.camera] for s in shots)
    assert all(traffic["span"][0] <= s.t0 and s.t1 <= traffic["span"][1] for s in shots)
    assert all(a.t0 <= b.t0 for a, b in zip(shots[1:-1], shots[2:-1], strict=False))
    assert record.highlight(shots) and sum(d for _, d in record.highlight(shots)) == pytest.approx(12, abs=1.5)
    roll = record.Day(ep, traffic, None, None).rollout()
    assert len(roll) == len(ep["watches"]) + 2 and 40 <= sum(s.seconds for s in roll) <= 56
    assert all(traffic["span"][0] <= s.t0 and s.t1 <= traffic["span"][1] for s in roll[:-1])


def test_a_thumbnail_the_films_made_reaches_the_episode_list(tmp_path):
    ep = json.loads((ROOT / "tests/viewer/fixtures/episode_small.json").read_text())
    stem = export.file_name(ep)[:-5]
    assert "thumb" not in export.entry(ep, "x.json", tmp_path)
    (tmp_path / "thumbs").mkdir()
    (tmp_path / "thumbs" / f"{stem}.jpg").write_bytes(b"\xff\xd8thumb")
    files = export.folder([ep], tmp_path)
    assert files[f"thumbs/{stem}.jpg"] == b"\xff\xd8thumb"
    assert json.loads(files["episodes/index.json"])["episodes"][0]["thumb"] == f"../thumbs/{stem}.jpg"
