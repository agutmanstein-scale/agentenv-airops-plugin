# AirOpsEnv v3: the check against v2 (2026-10-17)

Code: fork `agutmanstein-scale/agentenv-airops-plugin` at `3329980` (U1-U11). Pack: 129 whole-network storm dates over
the 19 months (24 eval, 105 train; 40 more storm dates are unbuilt: the FAA registry answers 403 from the build box)
plus 2 control dates, built by `scripts/v3_pack.py` and checked by `scripts/v3_check.py` against v2's tasks for the
same dates (no crew bases, no layers, the same scale JFK 0.425 / BOS 0.5 and cutoff). Limits: anchors 1,800 s,
follower watch solves 900 s. Constants fitted on the 105 train dates only: ramp-closure rate BOS 0.678, JFK 0.755;
reserve pool BOS 1, FLL 1, JFK 1, MCO 3; departure-cap rule SHORT 0.9, MIN_SCHED 5 (not fitted, DESIGN §3.1).

Two v3 variants were checked: **v3 with crew** (Edgar's FAR 117 crew model on JetBlue's four bases, plus every v3
layer) and **v3 layers only** (`--bases none`).

## DESIGN §0, per criterion

| §0 criterion | v2 | v3 with crew | v3 layers only |
|---|---:|---:|---:|
| 1 Harder: do-nothing's median excess over the anchor (>= 1.4x v2) | 9.16% | 20.58% (2.25x) PASS | 9.55% (1.04x) fail |
| 1 Harder: follower's median excess over the anchor (>= 1.5x v2) | 2.65% | 4.08% (1.54x) PASS | 2.91% (1.10x) fail |
| 2 Realistic: anchor's cancellations / JetBlue's (>= 35%) | 25.6% (926/3,624) | 128.8% (4,668/3,624) PASS* | 30.7% (1,112/3,624) fail |
| 2 Realistic: could fly for free, a pair once (>= 10 points below v2) | 58.3% | 54.6% (-3.7) fail | 53.8% (-4.5) fail |
| 2 Realistic: recorded day's median excess over the anchor (< v2) | +71.1% | -4.2% PASS* | +66.9% PASS |
| 2 Realistic: replay flies JetBlue's flown flights (>= 97%) | 99.53% | 99.35% PASS | 99.53% PASS |
| 2 Realistic: replay departures within 15 min (>= v2) | 95.31% | 86.09% fail | 95.27% fail (-0.04) |
| 3 Sound: anchors proven / replay exactly | 131/131 | 131/131, 131/131 PASS | 131/131, 131/131 PASS |
| 3 Sound: sim/MIP agreement tests | | 282 passed PASS | (same code) |
| 4 Playable: a fake-LLM agent plays a v3 day through the live server | | 2 passed PASS | (same code) |

\* Passes the threshold, but past realism: with crew the hindsight optimum cancels more flights than JetBlue did and
JetBlue's recorded day costs less than the "optimum" (-4.2%), so the crewed world is stricter than the real one.

world_check's gates on each pack:

| Gate | v3 with crew | v3 layers only |
|---|---|---|
| airline_replay (G1) | 0.938 PASS (85 auto-cancels of 46,667) | 1.000 PASS (0) |
| fidelity (out of sample) | PASS (eval JFK 0.96, BOS 1.11; train JFK 1.02, BOS 1.04) | PASS (same) |
| calm_day | 0.000 fail | 0.806 fail |
| controls | fail: both controls' recorded day below the anchor (1.19M vs 1.75M; 0.46M vs 1.37M) | PASS |
| references, days | PASS | PASS |

## Reading

- **The layers are sound but weak on whole-network dates.** Layers alone move every realism measure the right way
  (free share -4.5 points, optimum cancellations 26% -> 31%, recorded excess 71% -> 67%) without touching the replay,
  but the days get only slightly harder (do-nothing 9.2% -> 9.6%). The spike measured more (+8.9% -> +13.9%) on
  per-hub windows with a milder synthetic crew.
- **Edgar's crew model makes the days much harder, by making them infeasible for JetBlue's own schedule.** With it the
  calm day (no delay, no programs) cancels flights on every date (calm gate 0/129), the controls' recorded days beat
  the anchor, the optimum cancels 129% of what JetBlue did and replay departures within 15 min fall from 95.3% to
  86.1%. The proxy pairings (each tail's legs chained from a base departure to the next base arrival) appear to exceed
  FAR 117 limits on schedule (U11 crew diagnostic).
- **Not met by either variant:** the could-fly-for-free share drops 3.7-4.5 points, not 10.

## Decision (Alec, 2026-10-17)

v3 is the more realistic successor: layers only, no crews by default (`v3_pack.py --bases none`). Making the
days harder is v4's goal (docs/v4/DESIGN.md), starting with option 1 below.

## What would close it

The loop paused here (LOOP.md "Pause early": a §0 criterion still failing on the pack after its calibration pass).
Options, for Alec:
1. **A realistic crew layer:** proxy pairings planned inside FAR 117 on schedule (a planning buffer and a segment cap,
   crew changes at bases), calibrated on train dates so the calm day cancels <= 1 flight and the controls' recorded day
   is >= the anchor; then rerun the check (about 1.5 h on the box). The spike's crew of this kind gave do-nothing
   +8.9% -> +17.9% on its sample without breaking the replay.
2. Ship **v3 layers only** as the more realistic, not harder, successor of v2.
3. Ship **v3 with crew** as the harder variant, noting it is stricter than reality.

Raw results: `check.json` and `check.md` in each pack folder (`~/handoff/v3-loop/pack`, `pack-nocrew`, `pack-v2` on the
devbox).
