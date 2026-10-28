# Realistic mode (airops-occ3, `realistic`)

A second way to play an occ3 day, beside the current mode, which it leaves unchanged. The day runs in a world closer to
the real one. It is graded on JetBlue's own cost, with JetBlue's own decisions that day as the 0.5 mark.

## The world (`realistic.py`, `sim.World`)

- **Every station's hourly room** (`realistic.capacity`), from every carrier's BTS pushbacks and landings:
  - **Short hours.** In an hour when all carriers together managed under 90% of their schedule (v4's rule), JetBlue
    gets its share of what they managed. This applies to departures everywhere and to arrivals at outstations.
  - **Congested airports.** At JFK, LGA, DCA (slot-controlled), EWR (schedule-facilitated) and BOS (JetBlue's hub),
    every other hour has a capacity too: the most the airport handled in that hour on its fair days. JetBlue may use
    whatever the other carriers left of it. So a flight pushed out of a short hour finds the next hour as full as it
    was, not empty.
  - **Elsewhere,** fair days measure demand rather than capacity, so only short hours are capped.
  - **Floors.** A cap is never below what JetBlue's flights did in the hour, nor, outside short hours, below its own
    schedule.
  - **Hub arrivals** answer to the FAA programs and the hub's nominal rate, as in v3.
- **Program slots JetBlue actually used** (`realistic.recorded_slots`). Each ground delay program's JetBlue slots also
  hold the arrival time of each controlled flight JetBlue pushed back during the program (its pushback plus scheduled
  block), so a replay of JetBlue's day can take the slots JetBlue got. Truth only; the desks see the programs as issued.
- **Ramp closures** at the hubs come from their METARs and SPECIs (v4).
- **Gates.** A station holds at most as many JetBlue aircraft at once as the pack's 70 recorded days ever had there. A
  flight into a full station waits at its origin.
- **Breakdowns.** Each leg's aircraft breaks with JetBlue's fair-weather code-A cancellation rate (BTS), seeded by the
  date, an hour before its departure, and is out for the rest of the day.
- **Swaps only at JFK and BOS.** At an outstation a flight takes the aircraft that flew in on JetBlue's own day, while
  it is there.
- **Tomorrow.** The end-of-day aircraft must cover the next day's first departures (BTS, d + 1).

## The cost J (`realistic.airline`, `realistic.jetblue`)

J is in passenger-minutes; the desks see it in dollars, at $1.07 per passenger-minute.

- **Aircraft:** FAA's rates (crew and fixed costs per minute of departure delay, the whole cost per minute over block),
  less SAVE of the fuel and maintenance a cancelled flight saves.
- **Crews:** deadheads, hotels away from base and the end of day.
- **Tomorrow:** the ferries plus the aircraft's cost for each minute a first departure leaves late.
- **Revenue lost:** each refunded passenger's fare, and TAKE of each stranded passenger's fare. A passenger rebooked
  today costs nothing.
- **Fares:** each itinerary's fare by distance, from a line through the DOT Consumer Airfare Report (Table 1, B6),
  CPI-adjusted.
- **TAKE and SAVE** are fitted on the 13 stream days to JetBlue's Q1 2014 8-K (4,100 cancellations, $50M of revenue,
  $35M of operating income):
  - on JetBlue's replay, it loses 0.676 of the fares booked on its cancelled flights (TAKE = 0.728);
  - each cancellation saves 30% of the revenue it loses (SAVE = 0.445).

## The reward (`realistic.reward`)

`0.2 + 0.8 / (1 + 5/3 x^2)`, with `x = (J - top) / (anchor - top)`:

- **anchor:** J of JetBlue's replay (`jbfull@360`: its cancellations decided 6 h ahead, its tails, its GDP slot
  claims). It scores 0.5.
- **top:** the cheapest J of any plan priced, in practice the best plan found under J (`scripts/realistic_best_j.py`,
  15 min a day). It scores 1.0.

Cost P's figures stay in the grade under `P`.

## What the desks know at watch t (`realistic.known`)

- **From sim.known** (DESIGN §4):
  - a flight's own delay and block 2 h ahead;
  - FAA programs as issued;
  - closures once begun.
- **Each station's hourly room as it stands in t's hour,** carried over the next 4 hours: a persistence forecast,
  `realworld.persisted`. The desks never see the room those hours turn out to have.
- **The tails broken by t.** A breakdown is unknown until it happens.
- **Gates and the swaps rule**, which are rules.

An audit found no page or tool that runs the simulation on the truth after t. Left as they are, all small and shared
with the current mode:

- the standard crew desk reads flights' true early-departure lb (at most 15 min) before the switch to the day as
  known;
- crew alerts include minute t's truth messages (`<=` t);
- whether the day ends looks one watch ahead on the truth;
- the customer desk's free seats use each flight's recorded tail;
- GDP exemptions use the ground stops as they ran.

## Validation

Run with `scripts/realistic_report.py` (fidelity, J) and `scripts/realistic_peers.py` (other carriers).

1. **Same actions, same outcomes.** JetBlue's own actions are replayed: its cancellations decided 6 h ahead, its
   tails and its GDP slot claims. The replay is compared with JetBlue's recorded day, flight by flight, on the flights
   JetBlue flew, and by hour and by day.
2. **Is J JetBlue's money?** On the days of JetBlue's replay, the operating income it loses per cancellation (revenue
   lost less the saving) is set against JetBlue's disclosed storms:
   - Hurricane Irene, Q3 2011: about 1,400 cancellations, about $8M of operating income;
   - the early January 2014 storms: about 1,800 cancellations, about $30M;
   - Q1 2014: 4,100 cancellations, about $35M.

   The Q1 2014 figure is the one TAKE and SAVE are calibrated to. 21 of the 22 days fall within the three storms'
   range ($6.0k to $16.7k per cancellation, 2014 dollars); 2025-02-06 is below it.
3. **Where JetBlue cancelled, did the airport have trouble?** For each JetBlue cancellation the standard desks fly
   within an hour, we take the other carriers' cancellation share at that airport in that hour, from BTS. Where they
   kept running, flying it is plausible: JetBlue's own hedge, crew or aircraft. Where they cancelled 20% or more, the
   world is likely missing something, though they may have hedged too.

## Files

| What | Where |
|---|---|
| Pack: b6-v3's 22 priced days, layered, with breakdowns, tomorrow's firsts, fares and J's references | `data/b6-v3r`, built by `scripts/build_realistic_pack.py REALREP BESTJ` |
| The day | `occ/day3r.py` (`OccDay3R`), loaded with `realistic: true` (`occ/live3.py`) |
| The bundle | `tasks3.generate(..., realistic=True)`: tasks `<task id>-<mode>-real`, rules `tasks3.RULES_R` |
| The replays and the best plans under J | `scripts/realistic_compare.py`, `scripts/realistic_best_j.py` |
| Validation | `scripts/realistic_report.py`, `scripts/realistic_peers.py` |
| Tests | `tests/occ/test_occ3r.py` |

## Results on the 22 days (stream plans replayed, no new model play)

| Plan | Not flown, stream / held-out | Reward, stream / held-out |
|---|---|---|
| JetBlue recorded | 888 / 528 | |
| JetBlue replay | 952 / 565 | 0.5 / 0.5 |
| Standard desks | 214 / 128 | 0.714 / 0.771 |
| Model (2026-10-23 stream plans) | 210 / - | 0.772 / - |
| Best plan under P | 382 / 198 | 0.794 / 0.850 |
| Best plan under J | 533 / 283 | 1.0 / 1.0 |

JetBlue's replay flies what JetBlue flew in a world built from the record: on the flights both flew, per-flight delay
correlates 0.75 with the record on the stream days (0.67 held out), each hour's total 0.85 (0.80) and each day's total
0.97 (0.98), with the days' total delay within 1% (4%). Its count is JetBlue's cancellations plus the flights the world
could not fly by day's end.

The best plan under J cancels a bit over half of what JetBlue did, and a mostly different set. Read it as a hindsight
bound, not a verdict on JetBlue. Its count depends heavily on SAVE: at SAVE = 1 it cancelled 1,426 on the stream days
of an earlier world.

## First model days (GPT-6 Astra, 2026-10-28)

Three desks, each capped at $23.50 as on the stream, through the real `agent-env run` on a gr6.4xlarge
(`live_runner.py --config scripts/live_runner_real.toml`).

| Day | Astra reward | J | Cancelled | Model cost | JetBlue replay (0.5) | Desks | Stream plan | Best under P | Best under J |
|---|---|---|---|---|---|---|---|---|---|
| 2024-08-03 | 0.904 | $2.25M | 26 | $22.32 | $3.22M, 104 | 0.690 | 0.711 | 0.681 | $1.86M, 50 |
| 2023-06-27 | 0.689 | $4.60M | 18 | $31.13 | $5.63M, 187 | 0.600 | 0.644 | 0.743 | $2.93M, 93 |
| 2025-01-19 | 0.994 | $1.35M | 10 | $14.09 | $3.29M, 157 | 0.972 | 0.988 | 0.985 | $1.21M, 22 |

2025-01-19 played to the end but its audit failed (A5: the named replay cost otherwise). The named replay pinned each
flight to its departure, which reordered flights ready at one minute. Two JFK departures at 22:00 then took each
other's aircraft, and an Orlando flight lost the one aircraft it may fly at an outstation. `crewday.named` now keeps the
day's order (a plan entry's `order`). The day is graded above from its transcripts replayed with that fix, and its
audit passes.

On 2025-01-19 JetBlue's replay costs $2.1M more than the best plan found, so every plan that flies scores near 1.
