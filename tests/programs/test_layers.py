import json
from collections import Counter
from dataclasses import replace

import pandas as pd
import pytest
from day_helpers import day, flight, outcapped, tail

from agentenv_airops.programs import candidates, references
from agentenv_airops.programs.layers import arrcaps, depcaps
from agentenv_airops.programs.model import LAYERED, VERSION, Layers, Outcome, Task


@pytest.mark.parametrize("name", ["task", "crewed"])
def test_a_task_without_layers_serialises_as_a_v2_task_byte_for_byte(name, request):
    task = request.getfixturevalue(name)
    line = task.to_json()
    d = json.loads(line)
    assert task.layers is None and "layers" not in d and d["version"] == VERSION
    assert list(d)[-1] == "needs"
    assert Task.from_dict(d).to_json() == line


def test_a_task_with_layers_round_trips_at_version_4():
    layers = Layers(dep_caps={"JFK": {17: 9, 18: 7}}, arr_caps={"PWM": {19: 2}},
                    closures={"BOS": ((1000, 1045, 3, 2),)}, reserves={"JFK": 6}, release=True)
    task = replace(day([flight(0, "JFK", "BUF", 1000, 60)], [tail("N1", "JFK", end="BUF")]), layers=layers)
    d = json.loads(task.to_json())
    assert d["version"] == LAYERED == 4 and d["layers"]["dep_caps"] == {"JFK": {"17": 9, "18": 7}}
    back = Task.from_dict(d)
    assert back == task and back.to_json() == task.to_json()


def test_a_task_whose_layers_are_stripped_or_empty_serialises_as_the_v2_task():
    task = day([flight(0, "JFK", "BUF", 1000, 60)], [tail("N1", "JFK", end="BUF")])
    loaded = Task.from_dict(json.loads(replace(task, layers=Layers(dep_caps={"JFK": {16: 1}})).to_json()))
    assert replace(loaded, layers=None).to_json() == task.to_json()
    assert replace(task, layers=Layers()).to_json() == task.to_json()


def leaving_jfk(i: int, dep: int, pushed: int | None):
    return flight(i, "JFK", "BUF", dep, 60, actual=Outcome(pushed is None, False, "", pushed, None, None, "N1"))


def test_depcaps_caps_only_short_hours_at_the_share_never_below_what_jetblue_pushed():
    """Every carrier at JFK, per hour (scheduled, pushed back): 17 (10, 6) and 18 (10, 5) short of 90%, 19 (10, 9)
    not, 20 (4, 1) under MIN_SCHED, 21 (10, 2) short but with no JetBlue flight scheduled in it. JetBlue scheduled 4
    in hour 17 and pushed back 3 in it: max(3, 4/10 x 6) = 3; 2 in 18, one cancelled and one pushed back in 19:
    max(0, 2/10 x 5) = 1. BOS had no JetBlue flight in the task."""
    base = 1_000_000
    flights = [leaving_jfk(0, 1020, 1030), leaving_jfk(1, 1030, 1040), leaving_jfk(2, 1040, 1050),
               leaving_jfk(3, 1050, 1150), leaving_jfk(4, 1090, 1150), leaving_jfk(5, 1100, None),
               leaving_jfk(6, 1140, 1145), leaving_jfk(7, 1200, 1210)]
    task = replace(day(flights, [tail("N1", "JFK")], start=960), base=base)
    rows = [("JFK", base + 60 * h + k, base + 60 * h + k + 5 if k < pushed else None)
            for h, sched, pushed in ((17, 10, 6), (18, 10, 5), (19, 10, 9), (20, 4, 1), (21, 10, 2))
            for k in range(sched)]
    rows += [("BOS", base + 60 * 17 + k, None) for k in range(10)]
    frame = pd.DataFrame(rows, columns=["Origin", "sched_dep", "act_dep"]).astype({"act_dep": float})
    assert depcaps.build(task, frame) == {"JFK": {17: 3, 18: 1}}
    assert depcaps.build(task, frame, short=0.5) == {}
    assert depcaps.build(task, frame, min_sched=4) == {"JFK": {17: 3, 18: 1, 20: 1}}


def test_depcaps_on_the_slice_reads_the_bts_frame_and_keeps_jetblues_pushbacks_feasible(task, slice_inputs, tmp_path):
    caps = depcaps.build(task, candidates.frame(slice_inputs, tmp_path, task.date))
    flew = Counter((f.orig, f.actual.dep // 60) for f in task.flights if f.actual.dep is not None)
    assert caps == {"JFK": {17: 1, 18: 1, 19: 1}}
    assert all(cap >= flew[(hub, h)] for hub, by in caps.items() for h, cap in by.items())


@pytest.mark.parametrize("hub", ["JFK", "BOS"])
def test_an_arrival_cap_at_a_hub_is_rejected_as_its_arrivals_answer_to_its_programs(hub):
    with pytest.raises(ValueError, match=f"arrival caps at hub.*{hub}"):
        Layers(arr_caps={"PWM": {19: 2}, hub: {19: 3}})
    d = Layers(arr_caps={"PWM": {19: 2}}).to_dict()
    with pytest.raises(ValueError, match=hub):
        Layers.from_dict(d | {"arr_caps": d["arr_caps"] | {hub: {"19": 3}}})


def into(i: int, orig: str, dest: str, dep: int, block: int, gated: int | None, end: str | None = None):
    """A JetBlue flight scheduled to land at dep + block, gated in at `gated` (None: it didn't fly) at `end`."""
    return flight(i, orig, dest, dep, block, actual=Outcome(gated is None, False, "", None if gated is None else dep,
                                                           gated, end or (None if gated is None else dest), "N1"))


def test_arrcaps_caps_only_short_hours_at_outstations_at_the_share_never_below_what_the_replay_lands():
    """Every carrier at PWM, per hour (scheduled, arrived): 14 (10, 2), before the first watch's hour 15; 16 (10, 5),
    17 (10, 6) and 18 (10, 2) short of 90%; 19 (10, 9) exactly at it, not short; 20 (4, 1) under MIN_SCHED; 21 (10, 2)
    short but with no JetBlue flight scheduled to land in it. JetBlue's flights into PWM, by the hour the replay lands
    them (pushback plus block): in 14 one, outside the span; in 16 three, two cancelled: max(1, 3/10 x 5 = 1.5) = 2,
    rounded to the nearest flight; in 17 four, one of them gating in only in hour 19: max(4, 4/10 x 6 = 2.4) = 4; in 18
    two, one cancelled and one diverted to BOS, which the replay still lands at PWM: max(1, 2/10 x 2 = 0.4) = 1; in 19
    one; in 20 one: max(1, 1/4 x 1) = 1. A flight bound for BUF that diverted into PWM in hour 17 doesn't count there.
    JFK, short at 17, is a hub: its arrivals answer to its programs."""
    base = 1_000_000
    flights = [into(0, "JFK", "PWM", 960, 60, 1030), into(1, "JFK", "PWM", 970, 60, 1040),
               into(2, "BOS", "PWM", 990, 40, 1050), into(3, "JFK", "PWM", 1000, 60, 1150),
               into(4, "JFK", "PWM", 1030, 60, None), into(5, "JFK", "PWM", 1040, 60, 1110, end="BOS"),
               into(6, "JFK", "PWM", 1100, 60, 1165), into(7, "PWM", "JFK", 1000, 60, 1070),
               into(8, "JFK", "BUF", 980, 60, 1060, end="PWM"), into(9, "JFK", "PWM", 1150, 60, 1215),
               into(10, "JFK", "PWM", 790, 60, 850), into(11, "JFK", "PWM", 920, 60, 985),
               into(12, "JFK", "PWM", 930, 60, None), into(13, "JFK", "PWM", 940, 60, None)]
    task = replace(day(flights, [tail("N1", "JFK")], start=900), base=base)
    rows = [("PWM", base + 60 * h + k, base + 60 * h + k + 5 if k < arrived else None)
            for h, sched, arrived in ((14, 10, 2), (16, 10, 5), (17, 10, 6), (18, 10, 2), (19, 10, 9), (20, 4, 1),
                                      (21, 10, 2))
            for k in range(sched)]
    rows += [("JFK", base + 60 * 17 + k, None) for k in range(10)]
    frame = pd.DataFrame(rows, columns=["Dest", "sched_arr", "act_arr"]).astype({"act_arr": float})
    assert arrcaps.build(task, frame) == {"PWM": {16: 2, 17: 4, 18: 1}}
    assert arrcaps.build(task, frame, short=0.5) == {"PWM": {18: 1}}
    assert arrcaps.build(task, frame, min_sched=4) == {"PWM": {16: 2, 17: 4, 18: 1, 20: 1}}


def test_the_airline_replay_of_a_diversion_and_a_late_gate_in_inside_built_caps_flies_as_on_the_uncapped_day():
    """N1's JFK-PWM pushed back at 100 and diverted to BOS; N2's JFK-BUF pushed back at 110 and gated in only at 300.
    The replay lands both in hour 2, which every carrier's arrivals (one of five) cap at none by the share alone: the
    floor at what the replay lands keeps both in it, so the replay is the uncapped day's."""
    base = 1_000_000
    task = replace(day([into(0, "JFK", "PWM", 100, 60, 200, end="BOS"), into(1, "JFK", "BUF", 110, 60, 300)],
                       [tail("N1", "JFK", end="BOS"), tail("N2", "JFK", end="BUF")]), base=base)
    task = replace(task, flights=(task.flights[0], replace(task.flights[1], actual=replace(task.flights[1].actual,
                                                                                         tail="N2"))))
    rows = [(s, base + 120 + k, base + 125 if k == 0 else None) for s in ("PWM", "BUF") for k in range(5)]
    frame = pd.DataFrame(rows, columns=["Dest", "sched_arr", "act_arr"]).astype({"act_arr": float})
    caps = arrcaps.build(task, frame)
    assert caps == {"BUF": {2: 1}, "PWM": {2: 1}}
    capped, free = references.airline(outcapped(task, caps)), references.airline(task)
    assert capped.auto == free.auto == 0 and capped.run.cost == free.run.cost and capped.run.rows() == free.run.rows()


def test_the_airline_replay_under_built_caps_on_the_fixture_day_flies_as_on_the_uncapped_day(task, slice_inputs,
                                                                                            tmp_path):
    caps = arrcaps.build(task, candidates.frame(slice_inputs, tmp_path, task.date), min_sched=1)
    capped, free = references.airline(outcapped(task, caps)), references.airline(task)
    assert capped.auto == free.auto and capped.run.cost == free.run.cost and capped.run.rows() == free.run.rows()


def test_arrcaps_on_the_slice_reads_the_bts_frame_and_keeps_the_replays_arrivals_within_its_caps(task, slice_inputs,
                                                                                                 tmp_path):
    """The slice holds few flights per outstation hour, under MIN_SCHED; counting every hour with one scheduled caps
    PBI's hour 20 (JetBlue's two scheduled, one of the two arrived there) at one, and the hours whose one flight,
    JetBlue's cancelled one, never arrived at none."""
    frame = candidates.frame(slice_inputs, tmp_path, task.date)
    assert arrcaps.build(task, frame) == {}
    caps = arrcaps.build(task, frame, min_sched=1)
    assert caps == {"ATL": {23: 0}, "BUF": {21: 0}, "DTW": {23: 0}, "PBI": {20: 1}, "PWM": {19: 0}, "RNO": {26: 0},
                    "SMF": {24: 0}}
    landed = Counter((f.dest, (f.actual.dep + f.block) // 60) for f in task.flights if f.actual.dep is not None)
    assert all(cap >= landed[(s, h)] for s, by in caps.items() for h, cap in by.items())
    assert not set(caps) & {"JFK", "BOS"}
