# AirOpsEnv v3: harder, more realistic, playable storm days

Status: design, 2026-10-16. Base: `earakely-scale/agentenv-airops-plugin` main at `a8f1a77` (the programs model,
`model.VERSION = 3`). Home: the fork `agutmanstein-scale/agentenv-airops-plugin`. Evidence behind every choice:
the v2 probe (`~/handoff/out/report.md` on the devbox) and the layer spike (`~/handoff/spike/README.md`).

"v3" here names the environment: the programs world with realism layers, the desk's view of them, and a playable
day. To keep it apart from the data model's own version, a task that carries any layer is written as model version
4; a task without one is v2/main's task, byte for byte, and every reference on it is unchanged.

## §0 Why, and what done means

The probe found the programs world too easy and too loose for the task to test a desk:
- 64% of JetBlue's 2,260 cancellations on the 149 candidate days could have flown in hindsight (v1: 40%); JetBlue's
  GDP slot pool sits 41-69% idle in its replay; 1,078 of the cancellations are on legs no v2 rule can hold.
- Doing nothing costs only +8.9% over the hindsight optimum at the median, and the optimum cancels 26% of what
  JetBlue cancelled.

The spike showed MIP-expressible layers fix much of that: on all 149 days, do-nothing +13.9% median, the optimum
cancels 37% of JetBlue's, the forecast desk's gap +4.4% -> +8.3% on 6 days, every anchor reproduced exactly.

v3 is done when, on the v3 pack's days (§6), measured by `scripts/v3_check.py` (§6.2):
1. **Harder:** do-nothing's median excess over the hindsight optimum is at least 1.4x v2's on the same days, and the
   follower's median gap at least 1.5x v2's.
2. **More realistic:** the optimum's cancellations reach at least 35% of JetBlue's (v2: 26%), the share of JetBlue's
   cancellations that could have flown for free drops by at least 10 points, and the recorded day's excess over the
   optimum falls; the replay still flies >= 97% of the flights JetBlue flew and its departures within 15 min do not
   fall below v2's.
3. **Sound:** every anchor is proven to GAP or flagged; every MIP solution replays exactly in the sim and every sim day
   is a MIP solution, layers on, in the test suite and on the pack.
4. **Playable:** an LLM plays a v3 day end to end through the AgentEnv server (§5), graded on the truth.

Non-goals: new FAA program logic (the programs model and `flow/` are Edgar's and stay as they are), crew pairings
beyond Edgar's proxy, next-day schedules.

## §1 Principles

- **One rule, two enforcers.** Every layer is a rule `sim.execute` applies and a set of columns or rows the hindsight
  MIP (`programs/mip.py`) carries, so the anchor stays a proven optimum. The suite's agreement tests
  (`tests/programs/test_hindsight_mip.py`: `test_every_optimum_replays_exactly_in_the_sim`,
  `test_every_day_the_sim_runs_is_a_solution_of_the_model_at_its_cost`) gain a layered day per layer.
- **Layers are data.** A task carries `layers` (§2); builders in `programs/layers/` fill it from pinned public
  inputs. Absent layers mean v2 behaviour. Calibration constants are fitted on train days only and written to the
  pack's manifest.
- **Reuse upstream.** Edgar's crew model (`model.Crew`, `repairs.crews`, `Task.bases`), whole-network storm dates
  (`programs/network.py`), the world check (`scripts/world_check.py`), SPECIs (`flow/sources.py`) and the weather
  timeline (`flow/weather.py`) are used as they are; v3 adds to them and never changes their semantics silently.

## §2 Task and data

- **Unit:** a whole-network storm date (`network.build`), Edgar's current direction, with crew bases on
  (`network.build` gains `bases`, as `build_candidate` has). Per-hub windows (`candidates.build_candidate`) take the
  same layers, for comparison with v2.
- **Schema:** `Task.layers: Layers | None` (model version 4 when set), a frozen dataclass with `from_dict`/`to_dict`:
  `dep_caps: {station: {hour: cap}}`, `arr_caps: {station: {hour: cap}}` (outstations only), `closures: {station:
  ((start, end, cap), ...)}` (JetBlue's departure cap over [start, end); §3.2's `rate` stays in the builder),
  `reserves: {base: pool}` and `release: bool`. Hours and minutes from the task's midnight, as everywhere.
- **Inputs:** the 19 months' pinned BTS on-time (every carrier), IEM METARs (routine, `report_type=3`) and SPECIs
  (`report_type=4`, via `flow/sources.py`'s pins), and nothing new from the FAA. Every new input is sha256-pinned in
  the pack manifest.

## §3 World layers (the truth)

Each section: what it is, how the sim applies it, how the MIP carries it, how it is calibrated.

### §3.1 Hub departure caps
- **What:** in an hour when every carrier together pushed back (BTS gate departure) less than `SHORT` of its schedule
  at JFK or BOS (and at least `MIN_SCHED` were scheduled), JetBlue's cap is its in-scope scheduled share of the hour's
  schedule times the hour's actual pushbacks, never below what its in-scope flights actually pushed back. Other hours
  stay uncapped. (Spike: the optimum's cancellations 87 -> 112 of 296 on 16 days, the largest single lever.)
- **Sim:** `Rules.dep_hour(f, t)`; `_leave` checks it right after the stop holds, before the GDP logic, and moves t
  to the next hour when full; a pinned (`left`) flight counts in its hour.
- **MIP:** each copy leaving a capped hub hour joins row `("dep", (hub, hour))` with room = cap less pinned flights.
- **Calibration:** `SHORT` (spike 0.9) and `MIN_SCHED` (5) fitted on train days to match JetBlue's replay
  departures (the replay must stay feasible on the recorded day by construction).

### §3.2 Ramp closures as a departure-rate cut
- **What:** lightning within 5 nm closes the ramp. Truth intervals come from METARs: TS in present weather (not VCTS),
  begin and end minutes from the TSB/TSE remarks, SPECIs for exact onset. The data reject a full stop: during such
  intervals all carriers still pushed back at ~75% of the rate just before (JFK 411 vs 563, BOS 449 vs 584 over the
  same minutes). So a closure cuts the hub's departure cap to `rate` x JetBlue's normal hourly departures for its
  duration, and an aircraft landing inside it reaches its gate at the closure's end (gate hold).
- **Sim:** the closure's rate becomes a cap row over the closure interval (departures counted by pushback minute);
  `gate(world, station, landed)` gives the gate arrival, used by cost, turn and aircraft flow.
- **MIP:** a row per closure over the copies leaving inside it; each copy's gate arrival (and so its aircraft node and
  its arrival cost) is precomputed, keeping the model linear (as busy blocks are).
- **Calibration:** `rate` per hub fitted on train days from BTS pushbacks inside vs just before closures.

### §3.3 Outstation arrival caps
- **What:** the §3.1 rule on arrivals at every non-hub destination, from BTS arrivals of every carrier. It stands in
  for the destinations' own GDPs, ground stops and weather, which v2 doesn't model (44 of 125 out-of-hub residual
  flights on the shipped days had one listed).
- **Sim / MIP:** the caps join the nominal arrival caps (`World.nominal`), which `Rules.hour` and `_place` already
  read for any station: no new mechanism.

### §3.4 A finite reserve pool at crew bases
- **What:** Edgar's crew model gives base departures unlimited reserves (no crew deadline). v3 gives each base a
  pool of `reserves[base]` crews per day: a base departure past its pairing's crew deadline flies only by calling one.
- **Sim:** plan key `reserve: True`; `granted(task, plan)` grants in flight-id order while the base's pool lasts.
- **MIP:** a 0/1 reserve column per eligible flight bounding its copies past the crew deadline, and a pool row per
  base. The airline replay calls a reserve for each flight JetBlue flew past its crew deadline from a base.
- **Calibration:** the pool per base, on train days, the smallest pool that keeps the replay's auto-cancels within
  G1 (none is public).

### §3.5 Slot release (experiment)
- **What:** a cancelled flight's RBS slot is released to compression (other carriers) at the cancellation's decision
  time, unless the airline substitutes a flight into it first.
- **Sim / MIP:** slot rows gain `<= 1 - c_holder` for slots past the holder's decision; the sim drops a released slot
  from the pool.
- **Kept only if** it moves §0's metrics without breaking the replay; otherwise recorded as tried and dropped.

## §4 What the desk knows, and when (the day as known)

`sim.known(task, t)` carries the layers as a desk sees them; the follower and the playable day both read it.
- **Caps** (departure, outstation arrival, closure rates): known `CAP_LOOK` (240 min) ahead, as v1's slot caps were.
- **Closures:** known once begun (the METAR or SPECI reporting TS), expected to last `GUESS` more minutes (train-day
  median of remaining duration) until its end is reported.
- **News and its delivery lag:** advisories at signature + `lag.advisory`, METARs and SPECIs at observation +
  `lag.metar`, TAFs at issue + `lag.taf`. Lags are per product type (default 0, 5, 10 min) and part of the task.
- **Watches wake on events:** the next watch is the earliest of a news item, a freeze boundary and 60 min after the
  last one. The follower uses the same watch times (`references.watch_times`), so its gap measures the same desk.

## §5 The playable day

A v3 port of v1's day (`day.py`, `live.py`, `tasks.py`, `schedule.py`) onto the programs world, in
`src/agentenv_airops/v3day/` (v1's stay untouched).
- **Clock and watches:** event-driven watches (§4). Each tool call costs `CALL_MIN` minutes of virtual time
  (default 1), so thinking costs time without tying the score to provider speed.
- **Situation:** the time, the freeze line, the programs as issued (GDPs, stops, proposed), departure and outstation
  caps known so far, closures under way, the latest METAR/SPECI/TAF, every flight (status, known delay, the plan's
  departure and arrival on the day as known, its slot or hold, its crew deadline and rest), aircraft positions and
  the reserve pool left at each base.
- **Actions:** `cancel`, `hold` (not_before), `restore`, `call_reserve`, confirmed per watch and frozen as v2's
  follower freezes (FREEZE before departure; cancellations final).
- **Grade:** the day's cost on the truth (`sim.execute`), against the anchor and do-nothing:
  `reward = (naive - cost) / (naive - anchor)`, clipped as v1's `core.reward`, with the follower and the rule as
  reference scores.
- **Server:** an AgentEnv live env as `live.py` serves v1 (`urn:airops:live-load` pattern, virtual clock extension),
  with v3 prompts in `tasks.py`'s style.

## §6 The v3 pack and its check

### §6.1 Pack
Whole-network storm dates over the 19 months, crew on (JetBlue's bases), every layer fitted on train dates, the
draw as `network.split`. Manifest: inputs and their pins, layer constants and the dates they were fitted on.

### §6.2 Check (`scripts/v3_check.py`)
`world_check.py`'s gates with layers on, plus §0's metrics against the same dates without layers: do-nothing gap,
follower gap, optimum's cancellations against JetBlue's, the could-fly-for-free share (the probe, per restore so
pairs count once), replay flown share and departures within 15 min, proofs and exact replays. A v1/v2/v3 report.

## §7 Milestones (the loop's units; one linear stack, since they share sim.py and mip.py)

| Unit | What | Done when |
|---|---|---|
| U1 | This doc | merged in the fork |
| U2 | `Layers` schema (model v4 when set), `programs/layers/` package, hub departure caps (§3.1) | sim and MIP enforce them; agreement tests pass on a capped day; tasks without layers byte-identical; ruff, pytest green |
| U3 | Ramp closures as a rate cut with gate hold (§3.2), METAR/SPECI parsing, calibration | as U2 for a closure day; the rate fitted on train dates from BTS |
| U4 | Outstation arrival caps (§3.3) | as U2 for an outstation-capped day |
| U5 | Finite reserve pool at bases (§3.4), replay reserve calls | as U2 for a crewed day with a binding pool |
| U6 | Layers on whole-network dates: `network.build(bases=, layers=)`, the layer builders, train-date calibration | a layered network date builds, replays and solves exactly |
| U7 | The day as known (§4): layered `sim.known`, delivery lags, event-driven watch times for the follower | follower runs on a layered date; its plans replay; watch times include events |
| U8 | Slot release experiment (§3.5) | measured on the sample; kept or recorded as dropped |
| U9 | Playable day core (§5): `v3day` clock, situation, actions, grading | a scripted policy plays a day to the same cost as the sim |
| U10 | Live server, tools, prompts (§5) | an LLM (fake LiteLLM in tests) plays a day through the server |
| U11 | v3 pack and `v3_check.py` (§6), report | §0's criteria checked on the pack, with numbers |

## §8 Risks

- Upstream main moves daily; the stack rebases onto it before each push, and Edgar's semantics win.
- Share-of-actual caps are tight in calm hours by construction; only hours below `SHORT` of schedule are capped.
- Synthetic crews and pools are stand-ins (no crew data is public); they are labelled as such everywhere.
- Event-driven watches multiply follower solves; watch solves keep Edgar's 2,400 s limit and the check reports any
  unproven watch.
