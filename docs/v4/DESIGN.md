# AirOpsEnv v4: harder days, still no stricter than reality

Status: design, 2026-10-17. Base: v3 (fork tag `v3.0`, `29f2b83`): the programs world with v3's layers, no crews by
default. Evidence: v3's check on 129 storm dates (docs/v3/REPORT.md).

## §0 What done means

v3 made the days more realistic but barely harder (do-nothing's median excess over the anchor 9.2% -> 9.6%).
Edgar's crew model made them much harder (20.6%) by being stricter than reality: on schedule alone, with no delay and
no programs, the calm day cancelled flights on every date, the controls' recorded days cost less than the anchor, and
the anchor cancelled 129% of what JetBlue did.

v4 is done when, on the pack's dates, measured by `scripts/v3_check.py` against v2 and v3 on the same dates:
1. **Harder:** do-nothing's median excess over the anchor >= 1.4x v2's, and the follower's >= 1.5x v2's.
2. **No stricter than reality (guards):** world_check's controls gate passes (each control's recorded day >= its
   anchor); the calm-day gate passes (<= 1 forced cancellation and <= 2 ferries on >= 90% of dates); the recorded
   day's excess over the anchor is >= 0 on >= 95% of dates; the anchor cancels no more than JetBlue did (<= 100%);
   the replay flies >= 97% of JetBlue's flown flights and its departures within 15 min stay within 1 point of v2's.
3. **Sound and playable:** as v3 (every anchor proven or flagged and replaying exactly; agreement tests; the live
   server test).

## §1 Lever 1: a crew layer legal on schedule

Edgar's proxy pairings (`repairs.pairings`) chain each tail's legs from a base departure to the next base arrival
with no duty limit, so a long chain through outstations exceeds FAR 117 on schedule and its later legs can never fly.
v4 keeps Edgar's crew API (`model.Crew`, `Flight.crew`, `Flight.legal`, `Task.bases`, `Task.auto_at`, the sim and MIP
enforcement, the reserve pools of v3's U5) and replaces only how pairings are cut, in `programs/layers/crew.py`:
- a duty starts at a tail's first leg, at a base departure, or after a rest (a ground gap >= REST, 10 h);
- it takes the tail's next leg while the duty would still end, on schedule, `buffer` minutes inside its Table B limit
  (report REPORT minutes before its first departure, 117 Table B by the report's local hour and the duty's segments)
  and holds at most `max_segments` legs; otherwise a new duty starts there (a crew change at a base, or an
  overnight crew at an outstation);
- each leg's crew deadline is report + limit + EXTENSION (2 h, 117.19) - its scheduled block, as Edgar's; a base
  departure past it flies only on a reserve (v3's pools).

So every duty is legal on schedule and the crew binds only when delays push a duty past its limit, as real crews time
out. `buffer` and `max_segments` are fitted on train dates only: the values that maximise do-nothing's excess while
every §0 guard holds on the train dates (defaults 60 min and 4, the spike's). Reserve pools are then fitted as in v3.

## §2 Further levers (only if §1 alone misses §0)

Candidates, each with the same guards: fitting the departure-cap rule (SHORT) on train dates to the realism guard;
closure rates per hub and month. The loop pauses for Alec before any of these (LOOP.md pause rules).

## §3 Units

| Unit | What | Done when |
|---|---|---|
| V4-0 | This doc | merged |
| V4-1 | `layers/crew.py` pairings legal on schedule, `network.build(crew=...)`, `v3_pack --crew v4`, train-date fit of buffer and max_segments under the guards, tests (sim/MIP agreement on a v4-crewed day; the calm day of a v4-crewed date cancels nothing for crews) | gates green; a sample of real dates passes the calm and controls guards |
| V4-2 | The v4 pack and check against v2 and v3 on the same dates; docs/v4/REPORT.md | §0 met, or the loop pauses with the numbers |
