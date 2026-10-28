# Realistic mode (airops-occ3, `realistic`)

A second way to play an occ3 day, beside the current mode, which it leaves unchanged. The day runs in a world closer to
the real one. It is graded on JetBlue's own cost, with JetBlue's own decisions that day as the 0.5 mark.

## The world (`realistic.py`, `sim.World`)

- **v4's layers, in the task.** Hub departure caps and outstation arrival caps come from every carrier's BTS pushbacks
  and arrivals. Ramp closures at the hubs come from their METARs and SPECIs.
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

- **sim.known's model, unchanged** (DESIGN §4):
  - a flight's own delay and block 2 h ahead;
  - FAA programs as issued;
  - closures once begun.
- **The tails broken by t.** A breakdown is unknown until it happens.
- **Gates and the swaps rule**, which are rules.

The hourly caps are known for the 4 h ahead **as they were** (sim.known's `CAP_LOOK`). This is a perfect 4-hour
capacity forecast, kept from v4's design and stated in the desks' rules.

An audit found no page or tool that runs the simulation on the truth after t. Left as they are, all small and shared
with the current mode:

- the standard crew desk reads flights' true early-departure lb (at most 15 min) before the switch to the day as
  known;
- crew alerts include minute t's truth messages (`<=` t);
- whether the day ends looks one watch ahead on the truth;
- the customer desk's free seats use each flight's recorded tail;
- GDP exemptions use the ground stops as they ran.

## Files

| What | Where |
|---|---|
| Pack: b6-v3's 22 priced days, layered, with breakdowns, tomorrow's firsts, fares and J's references | `data/b6-v3r`, built by `scripts/build_realistic_pack.py REALREP BESTJ` |
| The day | `occ/day3r.py` (`OccDay3R`), loaded with `realistic: true` (`occ/live3.py`) |
| The bundle | `tasks3.generate(..., realistic=True)`: tasks `<task id>-<mode>-real`, rules `tasks3.RULES_R` |
| The replays and the best plans under J | `scripts/realistic_compare.py`, `scripts/realistic_best_j.py` |
| Tests | `tests/occ/test_occ3r.py` |

## Results on the 22 days (stream plans replayed, no new model play)

| Plan | Cancelled, stream / held-out | Reward, stream / held-out |
|---|---|---|
| JetBlue recorded | 888 / 528 | |
| JetBlue replay | 946 / 556 | 0.5 / 0.5 |
| Standard desks | 94 / 48 | 0.744 / 0.776 |
| Model (2026-10-23 stream plans) | 94 / - | 0.827 / - |
| Best plan under P | 289 / 140 | 0.866 / 0.911 |
| Best plan under J | 504 / 255 | 1.0 / 1.0 |

The best plan under J cancels about half of what JetBlue did, and a mostly different set: 213 flights in common out of
760. Read it as a hindsight bound, not a verdict on JetBlue. Its count depends heavily on SAVE: at SAVE = 1 it
cancelled 1,426 on the stream days.
