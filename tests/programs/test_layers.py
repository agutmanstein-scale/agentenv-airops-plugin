import json
from collections import Counter
from dataclasses import replace

import pandas as pd
import pytest
from day_helpers import day, flight, tail

from agentenv_airops.programs import candidates
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


def test_arrcaps_caps_only_short_hours_at_outstations_at_the_share_never_below_what_jetblue_gated_in():
    """Every carrier at PWM, per hour (scheduled, arrived): 17 (10, 6) and 18 (10, 2) short of 90%, 19 (10, 9) not,
    20 (4, 1) under MIN_SCHED, 21 (10, 2) short but with no JetBlue flight scheduled to land in it. JetBlue
    scheduled 4 into PWM in hour 17, 3 of them gated in in it: max(3, 4/10 x 6 = 2.4) = 3; 2 in 18, one cancelled and
    one diverted to BOS, gating in there in the hour: max(0, 2/10 x 2 = 0.4) = 0; 1 in 20, gated in in it: max(1,
    1/4 x 1) = 1. JFK, short at 17, is a hub: its arrivals answer to its programs."""
    base = 1_000_000
    flights = [into(0, "JFK", "PWM", 960, 60, 1030), into(1, "JFK", "PWM", 970, 60, 1040),
               into(2, "BOS", "PWM", 990, 40, 1050), into(3, "JFK", "PWM", 1000, 60, 1150),
               into(4, "JFK", "PWM", 1030, 60, None), into(5, "JFK", "PWM", 1040, 60, 1110, end="BOS"),
               into(6, "JFK", "PWM", 1150, 60, 1215), into(7, "PWM", "JFK", 1000, 60, 1070)]
    task = replace(day(flights, [tail("N1", "JFK")], start=900), base=base)
    rows = [("PWM", base + 60 * h + k, base + 60 * h + k + 5 if k < arrived else None)
            for h, sched, arrived in ((17, 10, 6), (18, 10, 2), (19, 10, 9), (20, 4, 1), (21, 10, 2))
            for k in range(sched)]
    rows += [("JFK", base + 60 * 17 + k, None) for k in range(10)]
    frame = pd.DataFrame(rows, columns=["Dest", "sched_arr", "act_arr"]).astype({"act_arr": float})
    assert arrcaps.build(task, frame) == {"PWM": {17: 3, 18: 0}}
    assert arrcaps.build(task, frame, short=0.5) == {"PWM": {18: 0}}
    assert arrcaps.build(task, frame, min_sched=4) == {"PWM": {17: 3, 18: 0, 20: 1}}


def test_arrcaps_on_the_slice_reads_the_bts_frame_and_keeps_jetblues_gate_ins_within_its_caps(task, slice_inputs,
                                                                                              tmp_path):
    """The slice holds few flights per outstation hour, under MIN_SCHED; counting every hour with one scheduled caps
    PBI's hour 20 (JetBlue's two scheduled, one of the two arrived there) at one, and the hours whose one flight,
    JetBlue's cancelled one, never arrived at none."""
    frame = candidates.frame(slice_inputs, tmp_path, task.date)
    assert arrcaps.build(task, frame) == {}
    caps = arrcaps.build(task, frame, min_sched=1)
    assert caps == {"ATL": {23: 0}, "BUF": {21: 0}, "DTW": {23: 0}, "PBI": {20: 1}, "PWM": {19: 0}, "RNO": {26: 0},
                    "SMF": {24: 0}}
    gated = Counter((f.dest, f.actual.arr // 60) for f in task.flights
                    if f.actual.arr is not None and f.actual.end == f.dest)
    assert all(cap >= gated[(s, h)] for s, by in caps.items() for h, cap in by.items())
    assert not set(caps) & {"JFK", "BOS"}
