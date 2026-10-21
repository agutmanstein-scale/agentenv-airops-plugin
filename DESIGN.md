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
stage      {hub, url, traffic} | null                      # the 3D airport and the episode's traffic (Viewer)
passengers {flights, connections, events} | absent         # v3: the day's passengers (below)
```

On a v3 day, `passengers` holds `flights` `[{id, booked, added, conn_in, conn_out, flown, misconnected, rebooked:
[{to, pax}], stranded, home, dropped, hold}]` (booked + added = flown + Σ rebooked + stranded + home + dropped;
`misconnected` the sum of its misconnect events, at least Σ (pax − made) over its booked connections, more when
passengers rebooked onto it miss it on their new itinerary; `hold` the minutes the departure waited for connecting
passengers), `connections` `[{from, to, at, pax, made}]` (booked at the start of the day) and `events` `[{t, kind:
misconnect | rebook | hotel | home, at, flight, pax, from, to, until}]`. The customer desk is the role `customer` in
`occ`: its turns, pages and notes, its requests (a `HOLD` for connecting passengers carries `pax`) and its decisions
(`rebook` with `to` and `pax`, `hotel` with `at` and `pax`). `web/episode.schema.json` has them all.

## Viewer

The viewer draws the hub in 3D (`web/stage/`) with a US map of the radar and the aircraft in the air beside it.
Everything it adds to the Episode JSON is for display only: the stands, the taxi routes and the runway in use are
inferred (BTS records none of them), and nothing of it reaches the env, the planner or the grade.

- A viewer folder (`cache/` locally, `viewer/` in the dataset) holds `episodes/<stem>.json` with their `index.json`
  and each one's traffic `episodes/<stem>.stage.json.gz`, `stage/<HUB>/stage.json.gz` and `ortho.jpg`,
  `radar/<task_id>/`, `twin/<HUB>.geojson` and `thumbs/<stem>.jpg`.
- Coordinates are local ENU metres about the hub's reference point (the task's `airports[hub]` lat/lon), points
  `[x_east, y_north]`, headings degrees true clockwise from north. The Three.js scene puts a point at `(x, h, -y)`, a
  model's nose along −z and `rotation.y = -heading` (`web/stage/data.js`).
- An episode's `stage` is null when the hub has no stage in the cache, else
  `{"hub": "BOS", "url": "../stage/BOS/stage.json.gz", "traffic": "<episode file stem>.stage.json.gz"}`, both URLs
  relative to the episode JSON's.
- The stage (`scripts/build_stage.py`), one per hub as gzip JSON with `ortho.jpg` beside it: `runways` with their
  ends, `taxiways`, `aprons`, `stands`, `jet_bridges`, `terminals`, `hangars`, `land`, `buildings`, `tower`,
  `taxi_graph`, `ortho` (the USGS mosaic and its bounds) and `attribution`. OpenStreetMap-derived, ODbL 1.0.
- The traffic, one per episode as gzip JSON: every airline's aircraft at the hub over the episode's span, from the BTS
  on-time data and the FAA registry, with the run's JetBlue decisions. `stage.traffic` bakes what no run changes into
  `cache/traffic/<task_id>.json.gz` (`scripts/prebake_traffic.py`); `stage.sidecar` lays a run's executed plan over it.

```
hub, date, tz
span       [start, end]                    # the task's first watch to the end of the day's activity at the hub
weather    [{t, metar, wind_dir, wind_kt, gust_kt, vis_sm, wx: [...], clouds: [[cover, ft], ...], ceiling_ft,
             temp_c, dew_c, plain: "Light rain · visibility 8 mi · cloud at 700 ft · wind NNW 9 mph"}]   # by t
runways    [{t, departures: "27", arrivals: "33L"}]     # the runways in use, changing with the wind, at most hourly
aircraft   [{key, tail, carrier, jetblue: bool, task: bool,
             class: "a320" | "a321" | "a220" | "e190" | "regional" | "regional_t" | "narrowbody" | "widebody",
             type, length, span, visits: [visit]}]
featured   [{key, flight, reason: "held" | "delayed" | "cancelled" | "first_after_stop" | ..., t}]   # best first
passengers {terminal, floor, depth, lounges, counter, crowds, queue, groups, tags} | absent  # v3 (occ/paxstage.py)
visit      {stand: "C11", from: minute | null, to: minute | null, arr: leg | null, dep: leg | null}
leg        {flight, kind: "arr" | "dep", other: "MIA", city: "Miami", status: "flown" | "cancelled",
            sched, out, off, on, in, tag: str | null, tone: "ok" | "delay" | "hold" | "cancel" | null,
            decided}                                    # decided: a cancelled task leg's, the minute it was cancelled
```

- A visit is the aircraft on the ground at `stand` from `from` (its arrival's `in`; null if it is there when the span
  starts) to `to` (its departure's `out`; null if it is still there when the span ends). `arr` carries `on` and `in`,
  `dep` carries `out` and `off`.
- `task` aircraft are the task's tails: they fly the run's final plan as executed on the truth, and only they carry
  tags. A cancelled task leg keeps its aircraft parked, with the tone `cancel`, and shows from when it is cancelled:
  its scheduled time, or `decided` when the plan or the env cancelled it later (the env 6 hours after schedule).
  Every other aircraft, JetBlue's included, flies its real BTS times, untagged.
- `featured` lists candidate moments for a film, best first.
- On a day with passengers, `passengers` is the hub's, display-only like the stands: a lounge behind the home
  terminal's glass on the departure level at each jet-bridge gate (`lounges`, each its facade polyline `p`, inward
  normals `n` and `door`; `terminal` indexes the stage's terminals, `floor` and `depth` in metres), the service
  counter's lounge (`counter`), each departure's crowd there (`crowds`: `n`, `t` its arrive, full, board and gone
  minutes, `cancel`), the line at the counter as `[t, n]` steps (`queue`), the walking groups in the crews' `segs`
  with `z` their floor (`groups`: connect, misconnect, rebook, hotel and the hotel `shuttle`), and each departure's
  gate tag by flight (`tags`: `{conn, held, missed, sched}`).
