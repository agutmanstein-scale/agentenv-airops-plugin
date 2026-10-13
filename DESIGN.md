# AirOpsEnv design (v1)

An airline operations desk recovers a real storm day at a JetBlue hub, live on AgentEnv's virtual clock. This file is
the contract between the modules; the README tells users how to run it.

## Units and time

- Every time is an **integer number of minutes from 00:00 hub-local on the task's `date`**. Times can pass 1440 for
  flights after midnight. Displayed as `HH:MM` local (with `+1` past midnight); the hub's time zone is in the task.
- Costs are in **passenger-minutes**.

## Task (one line of `data/<pack>/tasks.jsonl`)

```
task_id    "b6-jfk-20230626-1400"            # carrier-hub-date-windowstart (local HHMM)
split      "eval" | "control" | "train"
carrier    "B6"
hub        "JFK"
date       "2023-06-26"
tz         "America/New_York"
window     [840, 1200]                       # the disruption window that defines the scope
start      780                               # first watch = window[0] - 60; flights flown before it are fixed
turn       {"A32X": 49, "E190": 37, "A220": 47}   # minimum turn (minutes) by aircraft family
turn_all   45
dmax       360                               # a flight leaves at most dmax after its schedule, else auto-cancelled
airports   {"JFK": {"name": "...", "lat": 40.64, "lon": -73.78, "tz": "America/New_York"}, ...}
tails      [{"tail": "N123JB", "family": "A32X", "type": "A320-232", "seats": 162,
             "pos": "JFK", "ready": 812, "end_req": "BOS", "actual_end": "JFK"}]
flights    [{"id": 0, "flight": "B6 1234", "orig": "JFK", "dest": "BOS", "sched_dep": 845, "sched_arr": 920,
             "block": 75, "pax": 138, "family": "A32X", "lb": 35, "dmax": 360,
             "actual": {"cancelled": 0, "cancel_code": "", "dep": 902, "arr": 981, "end": "BOS", "tail": "N123JB"}}]
caps       {"JFK:arr": {"13": 7, "14": 4, ...}, "JFK:dep": {...}, "BOS:arr": {...}}   # truth, by local hour
nominal    {"JFK:arr": {"13": 9, ...}, ...}  # JetBlue's scheduled movements per hour: the view with no program
news       [{"id": "adv-049", "at": 790, "kind": "advisory" | "taf" | "metar" | "outlook",
             "speaker": "FAA Command Center", "text": "...", "data": {...}}]
frozen     [{"flight": "B6 99", "orig": "...", "dest": "...", "dep": 700, "arr": 770, "tail": "N..."}]
reference  {"floor": ..., "hindsight": ..., "anchor": ..., "follower": ..., "blind": ..., "naive": ...,
            "airline": ..., "gap_pct": ..., "qualifies": true, "clauses": {...},
            "hindsight_plan": {"<flight id>": {"cancel": 0, "dep": 905}}, "follower_plan": {...}}
sources    {...}                             # sha256 of each pinned input
```

- `lb` is the flight's exogenous delay: `max(0, DepDelay - LateAircraftDelay)` for a flight that flew, the 75th
  percentile of flown flights from the same origin within ±90 min for one that was cancelled. Its earliest departure
  is `sched_dep + lb`.
- `caps[a:kind][h]` is JetBlue's ration-by-schedule share of the movements all carriers actually made at airport `a`
  in local hour `h`, never below what JetBlue's in-scope flights moved there. Hours not listed are unconstrained.
- Tails are in scope when they fly a leg touching the hub inside `window`. Flights in scope are their legs from
  `start` to the end of the day.

## Decisions

The planner controls, per flight: **cancel** (or restore) and **not_before** (hold the departure until a minute).
Aircraft of one family at one station are interchangeable (no maintenance, crew or tail pinning in v1), so the env
dispatches them. A plan is `{flight_id: {"cancel": bool, "not_before": int | None}}`; flights left out fly as early as
they can.

## Execution (`core.sim.execute`)

Given a plan, the exogenous delays `lb` and the hourly `caps` (truth at the end of the day, or the day as known):

1. Events are processed in order of each flight's earliest candidate departure: `max(sched_dep + lb, not_before)`.
2. A flight takes the first-ready aircraft of its family at its origin (ties by tail); it departs at the later of its
   candidate time and that aircraft's ready time (previous arrival + the family's turn).
3. Hourly caps: if the departure hour at the origin or the arrival hour at the destination is full, the flight moves
   to the start of the next hour with room at both, and is re-queued.
4. A flight that cannot depart by `sched_dep + dmax` is **auto-cancelled**.
5. At the end of the day each aircraft not at its `end_req` is ferried there at the ferry cost.

The result is the executed day: per flight status (`flown`, `cancelled`, `auto_cancelled`), tail, dep, arr,
arrival delay; per tail its legs; the ferries; and the cost.

## Cost and reward

- Cost = Σ pax × max(0, arr − sched_arr) + 523 × Σ pax of cancelled flights + Σ ferries.
  - 523 = NEXTOR's 457-min average re-accommodation delay for disrupted passengers + refund exposure
    (0.3 take-up × the $235 DB1B median fare ÷ the FAA's $1.07 per passenger-minute value of time).
  - Ferry = 92 × (30 + miles / 8): A4A's $98.41 airline cost per block minute ÷ $1.07, over an assumed block time.
- Floor: the cost with no aircraft routing (each flight alone at its earliest time under the caps).
- Anchor: the hindsight optimum.
- Reward = 0.2 + 0.8 · exp(−g / 0.5), g = max(0, cost − anchor) / (anchor − floor + 0.02 · anchor).
  No rule breaks exist in v1: the env never strands a flight.

## Information (`core.known`)

At watch minute `t`:
- a flight's `lb` is known once `sched_dep ≤ t + 120`; before that it is 0;
- an hour's cap is known once `60·h ≤ t + 240`; before that the nominal schedule applies;
- news with `at ≤ t` has arrived (advisories, TAFs, METARs as issued).

Watches every 60 minutes from `start` until every flight is frozen or flown. Flights the plan has leaving before
`t + 60` are frozen. Each watch allows 3 planning calls.

## References

- `hindsight`: the time-line MIP on the truth (HiGHS, 1-minute arcs, proven to 0.01%).
- `follower`: the same MIP re-solved at each watch on the day as known (persistence forecast for unknown delays and
  caps), freezing as the env does; its plan executed on the truth.
- `blind`: the follower with no forecast.
- `naive`: the empty plan.
- `airline`: JetBlue's real cancellations and departure times as the plan (display only).

## Episode JSON (the viewer's input)

```
task       {task_id, carrier, hub, date, tz, window, start, airports, tails}
model, run, reward, cost {pax_delay, cancel, ferry, total}, reference {...}
schedule   [{id, flight, orig, dest, sched_dep, sched_arr, family, pax}]
executed   [{id, tail, status, dep, arr, arr_delay}]       # the planner's plan run on the truth
frozen     [...]                                           # flown before start
watches    [{t, news: [{at, kind, speaker, text}], caps_known: {...}, changes: [...], est_cost, counters}]
steps      [{t, tool, args, result}]                       # the transcript
caps       {...}                                           # truth
radar      {bounds: [w, s, e, n], frames: [{t, url}]}
twin       {hub, url}
```
