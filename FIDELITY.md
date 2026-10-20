# AirOpsEnv v2: fidelity report

How faithful is the storm day the two desks play? This report says what is real data, what is modelled and what is
generated, how each layer was checked against the record before the next was built, and where the checks missed. It
covers the pack `b6-v2` (70 dates) and the env `airops-occ` released in v0.3.0.

Every check was pre-registered: its threshold was written down before its run, and a change to a rule was recorded
with its reason before it was used. A check that failed was reported as it stood and the call to ship or stop was made
once, with no fix loops. Nothing was fitted to JetBlue's or the FAA's decisions. One value per hub was fitted, the
scale that sets the unseen traffic in a program from the FAA's stated delay, on training dates only. No language model
was called by any check before the pilot; the solver is HiGHS throughout.

**Contents:** [The layers](#the-layers) · [Real data](#real-data-used-as-recorded) · [Modelled](#modelled) ·
[Generated](#generated-not-jetblues) · [Not modelled](#not-modelled-labelled) ·
[Stage 1: the world](#stage-1-the-world-without-crews) ·
[Stage 2: crews](#stage-2-the-crews) · [Stage 2b: the desks, scripted](#stage-2b-the-two-desks-scripted) ·
[Stage 3: the model desks](#stage-3-the-model-desks) · [Misses and limits](#misses-and-limits) ·
[Rerun the checks](#rerun-the-checks)

## The layers

| Layer of the real system | In v2 | Checked by |
|---|---|---|
| Flights, aircraft, the schedule | the whole JetBlue network of the date from 04:00, as recorded | stage 1: JetBlue's own decisions replayed fly what JetBlue flew |
| FAA ground stops and ground delay programs | the ATCSCC advisories at JFK and Boston: as issued for what the desks know, as they ran for the truth; slots, EDCTs and compression modelled | stage 1: other carriers' modelled program delay against their recorded delay |
| Weather | through the FAA's programs, whose advisories the desks read as issued, and each flight's recorded delay; no METARs or TAFs reach the desks (the viewer's radar and 3D weather are for people) | no separate check: the delays are the record's |
| Costs | the FAA's published rates, BLS wages, GSA hotel rates, JetBlue's 10-K fare | sourced, not checked against outcomes |
| Pilots and flight attendants | the rules as published; trips and reserves generated | stage 2: JetBlue's real day crewed by them, and the calm day |
| The two desks and their channel | the ops controller and the crew scheduler, typed requests and free text | stage 2b (scripted) and stage 3 (zero-spend runs, pilot, board) |
| Passengers' connections, other airlines as actors, gates, maintenance | not modelled | labelled below |

## Real data, used as recorded

| What | Source | Used for |
|---|---|---|
| Every JetBlue leg of the date: scheduled times, flown or cancelled, tail, departure, block time, delay causes | BTS Reporting Carrier On-Time Performance | the schedule, each flight's own delay (its departure delay less the late-aircraft and program parts), block times (capped at scheduled + 2 h), where each aircraft is at 04:00 |
| Aircraft types and seats | BTS Form 41 Schedule B-43, the FAA registry | the fleet and its families (A320, A321, A321 Mint/LR, A220, E190) |
| Passengers | BTS T-100 | load factors, each flight's passengers capped at its family's seats |
| Ground stops and ground delay programs at JFK and Boston | FAA ATCSCC advisories | the desks see them as issued, with revisions, extensions and early ends; the truth flies them as they ran |
| Other carriers' flights at JFK and Boston | BTS on-time | their arrivals take slots in the same programs |
| Airport rates and counts | FAA airport capacity profiles, FAA NASR, Port Authority and Massport movement counts | the programs' pools and the unseen-traffic check |
| Pilot rules | 14 CFR 117: Table A (flight time), Table B (flight duty period by report time and legs), the 2 h extension of 117.19, 10 h rest | legality of every pilot duty |
| Flight-attendant rules | 14 CFR 121.467(b): 14 h scheduled duty, 10 h rest (the 2022 rule, no reduction); 121.391 cabin minimums | legality of every cabin duty |
| Crew bases, by date | ALPA, Airline Pilot Central, JetBlue press releases and 10-Ks | pilots BOS, JFK, EWR, FLL, MCO, LAX, plus SJU from 2025-03-01; flight attendants the same six, plus SJU from 2024-12-12 |
| Costs | FAA *Economic Values for Investment and Regulatory Decisions*, Table 4-7 (aircraft and flight-deck crew rates); BLS OEWS and ECEC (flight-attendant pay with benefits); GSA and DoD per diem (hotels by city and season); JetBlue's 10-K average fare (a deadhead seat); CPI-U | the day's cost, in passenger-minutes at $1.07 each (2025 dollars) |

Each crew parameter is pinned in `src/agentenv_airops/programs/crew_params.json` with its URL, retrieval date, the quote
or table it comes from, and a confidence; the flight data's files are pinned by hash in each date of the pack.

## Modelled

- **The FAA's slots.** Under a ground delay program, arrival slots are shared out by schedule over every carrier's
  scheduled arrivals plus an unseen stream (foreign, cargo, charter and non-reporting traffic, which BTS doesn't
  record). The stream's rate reproduces the program's stated average delay times one scale per airport, fitted on
  training dates only, against other carriers' recorded delay: JFK 0.425, Boston 0.5. EDCTs, compression (slots freed
  by cancellations move up), ground-stop holds and hourly caps on the hub's other arrivals are modelled; the FAA's CDM
  refuses a slot outside the pool.
- **Dispatch.** Aircraft of one family at an airport are interchangeable: the first ready flies. Turns are each
  family's 5th percentile of scheduled ground times on the month's calm days (at least 25 min), or faster where
  JetBlue really turned that connection faster that day.
- **Deadlines.** A flight not gone 15 h after its schedule, or by 03:00 from JFK or Boston, is cancelled
  automatically, late.
- **Tomorrow's needs.** Per family and airport, where flying the whole published schedule leaves the aircraft; only the
  schedule is read, never how JetBlue's day really ended.
- **Diversions** fly the route's median block time.

## Generated, not JetBlue's

JetBlue's pairings, rosters and reserve lists are not public, so the crews are generated stand-ins:

- **Trips.** A cost-optimizing set-covering builder over the published schedule (never the flown day), per crew kind
  and type group (A32X, A220, E190), preferring crews to stay with their aircraft on the schedule's planned rotations,
  each trip legal as scheduled. Every scheduled leg has a trip on all but 7 legs of 6 storm dates.
- **Units.** A pilot pair or a flight-attendant team stays together for the day. Crews start the day rested where
  they overnighted (a replay of the record before 04:00 places them), except red-eye crews mid-duty. An eval date has
  a median of 364 pilot and 356 cabin crews on trips.
- **Reserves.** Short-call (report at the base about 2 h after a call) and airport standby (6 h shifts), at the middle
  of a sourced industry band of crew members on reserve: pilots 16% (band 13–20%), flight attendants 19% (16–22%).
  No JetBlue figure is public; the results were also run at both ends of the band. A median of 82 pilot and 101 cabin
  reserves a date.
- **The standard crew desk** (B0's crews, and the crew system that crews whatever the crew desk leaves alone): each
  flight's planned crew; else the cheapest of a standby or a reserve called in time, a swap with a crew at the
  station, a deadhead on a free seat, or the 2 h pilot extension, each priced by the passengers' wait, the deadhead,
  the hotel and its effect on tomorrow; it cancels (B0) or asks to (the env's crew system) only when no legal crew can
  arrive by the deadline.

The pack freezes each date's units at 04:00, so a day starts the same on any machine.

## Not modelled, labelled

| Left out | Why, and what stands in |
|---|---|
| Union contract rules (JetBlue's pilot and cabin agreements) | not public. The contract cap on flight attendants' actual duty is not modelled: under 121.467(b)(14) a leg of the team's own trip stays legal as scheduled whatever its delay |
| Multi-day limits (117.23, 117.25(b), 121.467(b)(13)) | crews start the day rested |
| The captain's agreement to the 2 h extension | assumed |
| Report and release times, crew connection time, standby shift starts | from other US contracts: report 60 min before the first departure, release 15 min after block-in, 20 min between legs; standby from 05:00, 11:00 and 17:00 |
| Deadheads on other airlines, jumpseats, outstation reserves | no seat data; deadheads ride JetBlue flights within the model's free seats |
| Passengers' connections and rebooking | no public itineraries: a cancelled passenger costs a flat 523 passenger-minutes |
| Aircraft away on unrecorded (international) flying | a tail whose last flown leg lands at JFK, BOS, FLL, MCO or SJU before 18:00 is busy for the rest of the day; daytime gaps of 4 h or more are busy blocks |
| Aircraft that flew nothing | out of service; their cancelled legs stay for other aircraft |
| Maintenance, dispatch, gates, deicing | not modelled |
| Other airlines' decisions | replayed as recorded, not actors |
| FAA programs away from JFK and Boston | other airports' delays come from the record |
| The viewer's control-center room, crew room, crews' paths, apron bus and hotel-van routes, stands and taxi routes | display-only; neither the env nor the grade sees them |

## Stage 1: the world without crews

The storm-day world, the whole network of each of 181 storm dates over 19 months, checked with JetBlue's real
decisions and against the record. Run once on all dates, every task from 04:00.

| Check | What it tests | Result | Bar | Verdict |
|---|---|---|---|---|
| Airline replay | JetBlue's own cancellations and departures, played in the world, fly what JetBlue flew | 181 of 181 dates; 1 forced cancellation in 116,986 flights flown | at most 1% forced, and at most 2, on 90% of dates | pass |
| FAA delay | other carriers' modelled program delay against their recorded delay, pooled per hub | eval JFK 0.95, Boston 1.10 (out of sample on stage 1's eval dates); train JFK 1.06, Boston 1.08 | 0.8–1.25 | pass |
| References | the hindsight optimum is sound | 183 of 183 proven within 0.0079%, replayed exactly; nothing below it; passengers within seats | proven to 0.01% | pass |
| Calm day | with no weather, the world flies its own schedule | 155 of 181 dates (85.6%) | at most 1 forced cancellation and 2 ferries, on 90% | **miss** |
| Controls | on two calm dates the record costs at least the optimum, and doing nothing is near it | summer 2024-07-03: +0.20%; winter 2025-01-15: +8.9%; the record 1.16× and 1.15× the optimum | doing nothing within 3% | **miss on winter** |

- **The calm-day miss.** A whole day has more flights than the few-hour windows the cap was set for, and the per-date
  cap was not rescaled: calm-day cancellations rose from 64 to 113 over the same dates. 11 of the 26 failing dates
  miss by one cancellation. The storm world and its optimum share the same aircraft and schedule, so a date's score is
  unaffected; every date stayed in the pool.
- **The winter-control miss** is one flight: B6 2001 BUF–JFK carries a real 733-minute carrier delay, which doing
  nothing waits out and the optimum avoids by cancelling.
- **Every date starts with aircraft out of place,** as the record has them at 04:00: a median of 9 aircraft and 9
  departures with none in place. That is part of what the desks inherit, not an error.
- Doing nothing costs a median 11.8% more than the hindsight optimum; JetBlue's own decisions, replayed, 80% more
  (with crews and information this layer leaves out).

## Stage 2: the crews

Pilots, flight attendants and reserves added to the stage-1 world, checked on all 180 storm dates that completed (one
crashed in the joint search on a code defect and is outside the pool) and the two controls.

| Check | What it tests | Result | Bar | Verdict |
|---|---|---|---|---|
| Crew replay | JetBlue's real day, crewed by the generated trips, reserves and every crew move, with the fewest flights left without a legal crew | 264 of 116,517 flown flights (0.23%) without a legal crew; at most 1% on 173 of 180 dates; at most 1% and at most 2 flights on 146 (81.1%) | at most 1% and 2, on 90% | **miss as first set; replaced by the filter** |
| Calm day with crews | what the crew model alone adds to a calm day | at most 2% on 104 of 180 (57.8%); 101 of them add nothing | at most 2% on 90% | **miss as first set; replaced by the filter** |
| Best plan and bound | the best plan replays exactly with a legal crew on every flight, and its bound is valid | 181 of 182 exact (one 0.0144% off, filtered out); every bound valid; gap median 5.3%, p90 10.6% | every kept date | pass on every kept date |
| Controls | the standard crew desk within 3% of the best plan | +3.95% (summer), +17.4% (winter) | 3% | **miss** |
| The filter, fixed before the run | keep a date only if at most 1% of its flown flights lack a legal crew and crews add at most 2% to its calm day | 100 of 180 kept: enough to draw 12 eval dates per storm hub (8 summer, 4 winter) and 44 train, at every seed tried (0–199) and at both reserve-band ends | the pool | pass |

- **Why flights lack a crew** in the replay: the planned crew elsewhere when the flight leaves (44%), a legal crew at
  the station needed on another flight (36%), no crew of the type at the station (12%); duty and flight-time limits
  bind on only 8%.
  Reserves sit at bases and rarely reach the outstations where these flights leave, and the generated trips differ
  from JetBlue's real pairings.
- **The calm-day misses are mostly unproven.** The lower bound proves the rise on 3 of the 76 failing dates, all of
  them dates where a leg had no trip; on 44 the bound shows crews need add nothing, which points to the search.
- **The controls' miss** is the standard desk waiting for crews that time out after inbound overruns (both controls),
  plus B6 2001 on the winter one. A better best plan could only widen it.
- **The bias check, flagged.** The kept dates have fewer JetBlue cancellations than the dropped (median 20.5 against
  28) and less aircraft-only leverage (6.1% against 10.4%), and stage 1's five highest-leverage dates are dropped. The
  room the crew desk leaves is similar (32.2% against 33.5% of the best plan). So v2's eval dates lean toward
  moderate storms.
- **The reserve band moves little:** flights without a legal crew 286, 264 and 258 at the low, middle and high band;
  the same 100 dates are kept at the low end.
- **14 of the 24 eval dates were stage-1 training dates,** where the unseen-traffic scale was fitted; the FAA-delay
  check on this eval set is not fully out of sample. Only that scale was fitted, against other carriers' delay.

## Stage 2b: the two desks, scripted

Before any model desk was built, scripted desks measured whether the two-desk world was worth building, on the 24
eval dates. The ops script cancels what its projection says will cancel and cuts round trips where that is cheaper;
the crew script is the standard crew desk, sending CANCEL and HOLD instead of cancelling.

| Bar | Result | Needed | Verdict |
|---|---|---|---|
| Room: the standard crew desk at least 10% over the best plan (flat-priced) | 21 of 24 dates, median 30% | 15 of 24 | pass |
| Coordination: talking worth at least 5 points of the avoidable cost over the better no-channel script | 3 of 24; median 0 | 15 of 24 | **fail** |

The scripted desks capture about 14% of the room with or without the channel, and one scripted controller does no
better than two talking desks: the room is crew waits and aircraft moves that only a joint plan makes. The two desks
shipped anyway, with this result stated; whether talking matters for model desks is what the runs after release test.

## Stage 3: the model desks

- **Zero-spend, in process,** on all 26 eval and control dates: doing nothing equals the pack's idle reference; the
  scripted ops desk and the scripted channel reproduce stage 2b's modes exactly; the stored best plan confirmed at
  04:00 reproduces the best plan, reward 1.0; the audit passes, a leak test is byte-identical, and runs are
  deterministic.
- **Zero-spend, through the real harness** on local Docker (scripted desks, no model): 12 of 12 runs as expected. Every
  scored run replays exactly; a crew desk killed mid-day is logged and not scored; each desk sees only its own tools on
  the gateway and is refused the other's.
- **Independent checks before any spend** found two blocking defects (a crew move took a crew off the rest of its
  trip; the cost cap stopped desks early) and minor ones; all were fixed, and the zero-spend checks rerun, before the
  pilot.
- **The pilot,** 6 runs of GPT-6.1 Sol on 3 dates: all scored, replayed exactly and passed the audit; $6.88.
- **The board,** 48 runs on the 24 eval dates: all scored, replayed exactly through the env's calls, and passed the
  audit; an independent recheck recomputed every reward and reward_L from the graded costs and the pack's best plan,
  bound and B0 (0 mismatches) and found one run where the crew desk's connection dropped mid-day and the harness
  recorded a voluntary stand-down. The harness was fixed to record that as an error, and the date was rerun as the
  registered retry (0.464), which replaces it on the board; the first attempt stays recorded and flagged.

## Misses and limits

1. **The calm day** passed on 85.6% of dates against a 90% bar (stage 1).
2. **One winter control** has doing nothing 8.9% over the optimum, from one flight's real 733-minute delay (stage 1).
3. **The standard crew desk** missed the controls' 3% margin: +3.95% and +17.4% (stage 2). It is B0, the point
   rewards are scaled from.
4. **The crew model fails on some dates,** and those dates were filtered out by a rule fixed before the run; the kept
   dates lean toward moderate storms.
5. **The best plan is not a proven optimum.** The joint aircraft-and-crew problem can't be proven at this size; each
   date publishes its best plan and a proven lower bound, with gaps of 2.4% to 13.9% on the eval dates (median 5.1%).
   Most of the gap is the bound's weakness rather than a measured distance to the optimum, and a run can come in under
   the best plan (2023-07-13, flagged).
6. **The coordination gate failed** for scripted desks (stage 2b).
7. **The crews are generated,** not JetBlue's pairings, and reserves are an industry band, not JetBlue's figure.
8. **Cabin duty can run past 14 h** under the (b)(14) reading for a team's own trip delayed beyond the airline's
   control; the contract cap that would limit it is not public.
9. **One storm date crashed** in the joint search on a code defect and is outside the pool; one best plan replays
   0.0144% off and its date was filtered out.

## Rerun the checks

The checks are scripts in this repository; each writes its report and every number in it. They need the raw inputs
(`scripts/build_pack.py` downloads them into `cache/inputs/`) and a large machine: the stage-2 run took 2 h 34 min on
96 workers.

| Stage | Script |
|---|---|
| 1, the world | `scripts/world_check.py --out cache/stage1/full-day` |
| 2, the crews | `scripts/crew_check.py --out cache/stage2/full2 --tasks cache/stage1/full-day`, then `--desk-replay` and `--ends-over` |
| 2b, the desks scripted | `scripts/occ_check.py --out cache/stage2b/occ --run ... --desk ... --split ... --tasks ...` |
| the pack | `scripts/build_occ_pack.py`, then `--assemble` into `data/b6-v2/` |
| 3, zero-spend | `scripts/occ_z1.py --out cache/z1` and `scripts/occ_z2.py --out cache/z2 --z1 cache/z1` |
| 3, the board | `agent-env airops-occ pilot` with the board's runs, caps and budget (`results/runs/occ-board-sol/pilot.json`), then `report` |
