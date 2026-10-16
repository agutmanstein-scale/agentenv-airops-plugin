import json
from dataclasses import replace

import pytest
from day_helpers import day, flight, tail

from agentenv_airops.programs.model import LAYERED, VERSION, Layers, Task


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
                    closures={"BOS": ((1000, 1045, 0.75),)}, reserves={"JFK": 6}, release=True)
    task = replace(day([flight(0, "JFK", "BUF", 1000, 60)], [tail("N1", "JFK", end="BUF")]), layers=layers)
    d = json.loads(task.to_json())
    assert d["version"] == LAYERED == 4 and d["layers"]["dep_caps"] == {"JFK": {"17": 9, "18": 7}}
    back = Task.from_dict(d)
    assert back.layers == layers and back.to_json() == task.to_json()
    assert replace(back, layers=None, version=VERSION).to_json() == replace(task, layers=None).to_json()
