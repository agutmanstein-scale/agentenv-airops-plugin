from types import SimpleNamespace

from flow_helpers import CACHE, DERIVED, needs_cache

from agentenv_airops.core.sim import FLOWN
from agentenv_airops.flow import demand, desk, faa, hindsight, joint, queue
from agentenv_airops.flow import world as flow_world
from agentenv_airops.flow.capacity import Reductions


def test_jetblue_holds_until_it_lands_while_the_others_divert():
    offered = queue.slots([1] * 20)
    arrivals = [(0, (k,), k) for k in range(6)]
    assert desk.land(arrivals, offered, frozenset())[5] is None
    assert desk.land(arrivals, offered, frozenset({5}))[5] == 75
    assert desk.land(arrivals, offered, frozenset()) == queue.land(arrivals, offered)


def test_the_day_so_far_pins_what_left_and_holds_the_rest_from_the_watch():
    task = SimpleNamespace(flights=[SimpleNamespace(id=k) for k in range(4)])
    plan = {1: {"cancel": True, "decided": 600}, 2: {"not_before": 700}, 3: {"not_before": 500}}
    got = desk.so_far(task, plan, 660, {0: 610})
    assert got == {0: {"left": 610}, 1: {"cancel": True, "decided": 600}, 2: {"not_before": 700},
                   3: {"not_before": 660}}


def test_unseen_rates_count_the_period_and_the_median_daytime_hour():
    unseen = [60 * h + m for h in range(24) for m in ((10, 40) if h < 12 else (10, 30, 50))]
    assert desk.rate(unseen, 600, 719, 9.0) == 2.0 and desk.rate(unseen, 1500, 1600, 9.0) == 9.0
    assert desk.median_rate(unseen) == 3.0


@needs_cache
def test_a_train_day_hub_links_its_task_and_the_joint_hindsight_replays_exactly():
    med = demand.medians(CACHE, DERIVED, ["2022-12-07", "2024-08-18"])
    worlds = {h: flow_world.build(CACHE, DERIVED, "2022-12-07", h, med, Reductions()) for h in ("JFK", "BOS")}
    w = worlds["JFK"]
    h = hindsight.solve(w)
    assert hindsight.bound(w) <= h.system and h.system <= flow_world.evaluate(w).totals["system"]
    assert flow_world.evaluate(w, faa.replay(w)).totals["flights"] == sum(not a.cancelled for a in w.arrivals)
    assert desk.unseen_arrivals(CACHE, DERIVED, "2022-12-07", "BOS") == [a.sched_arr for a in worlds["BOS"].arrivals
                                                                         if a.unseen]
    task = desk.tasks(CACHE, DERIVED, "2022-12-07", "JFK")
    lk = desk.link(w, task, med)
    assert len(lk.arrival) == sum(f.dest == "JFK" for f in task.flights)
    assert all(lk.world.arrivals[j].carrier == "B6" and not lk.world.arrivals[j].cancelled
               for j in lk.arrival.values())
    naive = desk.naive(lk)
    assert naive["converged"] and naive["joint"] == naive["others"] + naive["jetblue"]
    floor = joint.solve(lk, 600)
    assert floor["proven"] and floor["exact"] and floor["joint"] <= naive["joint"]
    assert floor["detail"]["jetblue_held_minutes"] == 0 and floor["detail"][FLOWN] > 0
