# AirOpsEnv v3: fidelity report

v3 puts JetBlue's passengers on v2's storm days and adds a third desk for them, the customer desk. This report says
what about the passengers is real data and what is generated, which rules move them when their flight goes, how the
generator and the references were checked before the next stage was built, and where the checks missed. It covers the
pack `b6-v3` (v2's 70 dates, each with its passengers and its references under the passenger cost P) and the env
`airops-occ3` released in v0.4.0. v2's world, crews and their checks are unchanged and were not rerun: crews keep their
seats ahead of passengers, so v2's crew sim plays every day as before. [v2's report](#airopsenv-v2-fidelity-report)
follows below, unchanged.

The rules are v2's. Every check was pre-registered, and every change to a rule was recorded with its reason before it
was used: step 0 was re-registered twice and stage 2 twice, each time before the runs it governed. A check that failed
is reported as it stood, and the call to ship or stop was made once. Nothing was fitted to JetBlue's decisions or to
its passengers' outcomes, and no language model was called by any check. Before each result was posted an independent
agent recomputed its bars from the run's per-date files and re-simulated a sample of dates with the pinned code. One
disclosure: before step 0 was registered, one eval date (2024-07-13) was run once against an earlier model with no seat
limits in its MIP; that model was discarded, and the bars were written without that result being seen.

**Contents:** [The layers](#v3-the-layers) · [Real data](#v3-real-data-used-as-recorded) ·
[Generated passengers](#v3-passengers-generated-per-flight) · [Rebooking rules](#v3-the-rebooking-rules) ·
[Cost P](#v3-cost-p) · [Generator checks](#v3-the-generator-checks) ·
[Reported only](#v3-reported-only-no-comparable-public-record) · [Controls](#v3-the-controls-under-p) ·
[Reference and bound](#v3-the-reference-and-its-bound) ·
[Step 0 and stage 2b](#v3-is-there-something-for-a-customer-desk-to-do) · [Stage 3](#v3-stage-3-the-model-desks) ·
[Not modelled](#v3-not-modelled-labelled) · [Misses and limits](#v3-misses-and-limits) ·
[Rerun](#v3-rerun-the-checks)

## v3: the layers

| Layer of the real system | In v3 | Checked by |
|---|---|---|
| Passengers per flight | each flight's seats times its route-month load factor from T-100, as in v2 | P1 and P4: bookings against T-100 |
| Connections | generated per date from the itinerary survey of its own period, at JFK, BOS, FLL, MCO and SJU | P2 and P3: against the survey's connecting shares and DB1C's connection times |
| Rebooking when a flight goes | JetBlue only, from the decision, earliest arrival first, for up to five days, then a refund; a move 3 h or more earlier only when no other seat is left | the published rules; outcomes reported, with no public record to check them against |
| What a disruption costs | cost P: delay at the final destination, refund exposure, a lodging night per night away, refunded fares | sourced, not checked against outcomes |
| The customer desk | requests to ops (hold, don't cancel, this flight's passengers can be moved) and pages of each flight's passengers; rebooking stays automatic | stage 2b (scripted desks), then the model board |
| International connections, other airlines' seats, compensation, rebooking by hand | not modelled | labelled below |

## v3: real data, used as recorded

| What | Source | Used for |
|---|---|---|
| JetBlue's passengers and seats per route and month | BTS T-100 Segment, the census JetBlue files; summed over a year it gives JetBlue's reported load factor (FY2025: 82.37% against 82.4%) | each flight's booked passengers, as in v2; the following days' seats left |
| Itineraries with connections | BTS Airline Origin and Destination Survey: DB1B Market (a 10% ticket sample, quarterly, to 2025 Q2, when its collection ended) and DB1C Market (40%, monthly, from July 2025, with each connection's scheduled time) | each date's connecting flows, from its own quarter or month; connection times (`data/pax/b6-transfers.csv`, `b6-dwell.csv`) |
| Fares | the same surveys: the passenger-weighted median one-way market fare on JetBlue per period, taxes included ($163 to $203 in each period's own dollars) | a refunded passenger's fare (`data/pax/b6-fares.csv`) |
| The following five days' flights | BTS on-time schedules of the next five days, each flight's seats from its tail's type less its own T-100 load | where a passenger with no seat today can go |
| Lodging | GSA per diem lodging rates by city and season, as v2's crew hotels | a night away |
| The rules | JetBlue's Contract of Carriage and Customer Service Plan; DOT's refund rule, 14 CFR 260 | rebooking, refunds and what is owed (below) |

## v3: passengers, generated per flight

No airline's itineraries are public, so each date's passengers are generated stand-ins, labelled as such like v2's
crews (`programs/pax.py`, `book`):

- **Who connects.** A route's departing passengers at a connecting station (JFK, BOS, FLL, MCO, SJU) include those who
  arrived on an earlier JetBlue flight: per origin, the share the survey of the date's own period shows, times the
  route's booked passengers that day. Each origin's connecting passengers are spread over the day's scheduled
  JetBlue-to-JetBlue connections onto the route, from the station's minimum connection time to 6 h, in proportion to
  DB1C's connection-time distribution at the station, never more passengers than either flight has left to assign. A
  passenger connects at most once (three-leg itineraries are 0.04% to 0.2% of JetBlue's). Everyone else is local. The
  same date always gets the same passengers.
- **Minimum connection times.** JetBlue publishes none. Its sold connections in DB1C hardly ever go below 45 minutes
  at JFK and BOS or 40 elsewhere, so those are the minimums (labelled).
- **On an eval date** the generator books a median of 86,148 passenger trips (59,412 to 96,759), 2.7% of them
  connecting (2.0% to 3.5%), from 11 survey periods: DB1B quarters for dates to June 2025, DB1C months after.
  JetBlue's own connecting share fell across the window, from about 10% of the passengers on its domestic flights in
  2023 Q1 to 5% to 6% in 2025 and 2026, so each date takes its own period's flows, never a pooled one.

## v3: the rebooking rules

How passengers move when their flight is cancelled or their connection is missed decides most of P. The rules are
JetBlue's own, as published, plus DOT's:

| Rule | Source |
|---|---|
| **JetBlue only, the next available flight, from the decision.** A passenger off a cancelled flight is rebooked from the minute of the cancellation (or of arriving at the hub, after a missed connection) onto JetBlue flights only | Contract of Carriage §37(c): "reaccommodation on the next available JetBlue flight in the same class of service at no additional charge", word for word in Rev 88 (in force in December 2022), Rev 98 and Rev 103. The contract gives no right to another airline (§25(a)). Chosen over rebooking on other airlines when JetBlue can't, and over v2's flat price |
| **Up to five days, then a refund.** A passenger with no JetBlue seat today takes the first seat of the next five days' schedules, a night away for each night waited; with none in five days, a refund | JetBlue's Customer Service Plan: "Rebook to an alternate JetBlue flight within five days" or a full refund. The five-day wording is in the plan's archived captures from May 2025 on; the same window is applied on every date (labelled) |
| **Under 3 h earlier is free; 3 h or more earlier only if no other seat is left.** A move onto a flight leaving 3 h or more before the one booked is taken only when no later seat exists today or in the five days; otherwise it would be a refund | DOT, 14 CFR 260.2 (89 FR 32760, compliance from 2024-10-28): a departure 3 h or more earlier is a significant change the passenger may refuse for a refund. Applied on every date, before 2024-10-28 too (labelled). Airlines do rebook onto earlier flights (American's Rule 240, as quoted in 2003). Drawn after step 0 showed its pass leaning on free moves to any earlier flight |
| **Earliest scheduled arrival first,** ties by departure; first come, first served in the order passengers are disrupted | JetBlue publishes no rule; this is how automatic re-accommodation works (labelled) |
| **Ready one minimum connection time after the decision** at the passenger's origin | an assumption: a passenger at its origin is taken to be at the airport (labelled) |
| **Crews keep seat priority** | so the crew sim is v2's, unchanged |
| **Nothing owed on a storm day.** No compensation, meals or hotels are charged | the contract treats weather and air traffic control as force majeure, and its chain rule extends that to the crew time-outs and misplaced aircraft that follow. Refunds and rebooking still apply. A night away is charged as one GSA lodging night, the passenger's own cost (labelled: many of those stranded are at their own origin) |

**Why the rules changed before any eval run.** As first registered, passengers were rebooked first come, first served
by scheduled departure, never before their booked departure, with a next-day fallback. On 8 train dates JetBlue's own
replayed day then stranded 88% of the passengers off its cancelled flights overnight: 54% had no later JetBlue flight
to their destination that day, and the rest found the later flights full, at a 0.90 load factor. The public benchmarks
(about 80% recovered the same day, Bratu and Barnhart, a hub carrier in August 2000; a 457-minute average delay of a
disrupted passenger, TDI 2010 for US 2007) describe calm periods and carriers that rebook onto other airlines; JetBlue's
contract doesn't. Re-registration 1 adopted the contract's rules, re-registration 2 the earliest-arrival order, and
stage 2's registration the 3 h line, each before the runs it governed.

## v3: cost P

P keeps every term of v2's cost but two (`programs/cost.py`, in passenger-minutes at the FAA's $1.07):

- **Delay is charged at each passenger's final destination,** rebooked or a later day's arrival included, in place of
  v2's delay at every leg. A refunded passenger is charged the time waited, from the booked departure to the refund.
- **A cancelled flight's passengers cost 66 each,** the refund exposure in v1's 523 (an assumed 0.3 take-up of the $235
  DB1B median fare), their real delay replacing the flat 457 minutes.
- **Two terms join the flat cost:** a GSA lodging night per night away, and the fare of every refunded passenger.

The notice term (a cancellation decided under 2 hours ahead costs more) and the airline's aircraft and crew costs are
v2's. With passengers off, every cost and output is v2's to the byte; a test checks the digests.

## v3: the generator checks

These check that the generated passengers reproduce their public inputs. They are not an independent fidelity test:
there is no public per-day record of JetBlue's passengers. Registered before stage 2 ran, on all 70 dates.

| Check | What it tests | Result | Bar | Verdict |
|---|---|---|---|---|
| P1 | booked passengers per date against T-100's passengers per departure on the date's routes that month, times the day's departures | 70 of 70 within 5% | within 5% on 90% of dates | pass |
| P2 | connecting share per connecting station against the period's DB1B or DB1C JetBlue-to-JetBlue share | 65 of 75 (period, station) cells within 1.5 points; 66 of the 75 below the survey | within 1.5 points | **fail** |
| P3 | median scheduled connection time against DB1C's, at stations with 1,000 or more survey transfers | 5 of 5 within 20 min (the largest difference 10.6 min, JFK) | within 20 min | pass |
| P4 | the following days' loads, tested as P1 | 70 of 70 within 5% | as P1 | pass |

- **Why P2 fails.** The generator drops connecting flows it can't place: about 20% of the connecting passengers it
  targets (JFK 18.6%, BOS 27%, FLL 20.5%, MCO 22.5%, SJU 3.4%) have no inbound flight from their origin landing
  between 45 and 360 minutes before the outbound that day, and become locals. DB1C's connection-time distribution stops
  at 360 minutes and DB1B has no times, so longer connections can't be placed. Seats drop almost none (22 passengers
  over the 70 dates). Pooled over the dates, the generated shares fall short of the target by 1.9 points at JFK, 1.5
  at BOS, 1.2 at FLL, 0.5 at MCO and 0.1 at SJU. A second effect hides part of it: the day's route mix can lift the
  target above the period's share (JFK in 2024 Q1: 11.70% against 10.26%), so some cells pass only because of that.
- **What it does:** fewer connecting passengers than the survey has, so fewer misconnects in P and fewer batches of
  passengers large enough for a customer desk to hold a flight for. It works against the desk. Shipped as it is,
  labelled.
- **P1 and P4 are close to true by construction:** booked passengers are seats times the route-month load factor, so
  they test only the day's gauge against the month's.

## v3: reported only, no comparable public record

No public record gives JetBlue's passengers' outcomes on a storm day, nor any airline's on JetBlue-only rebooking. These
are reported as findings, with no bar (stage 2, medians over the 70 dates):

| Figure | v3 | The nearest public figure, and why it doesn't compare |
|---|---|---|
| Passengers disrupted (off a cancelled flight or a missed connection), under the reference | 0.7% of trips | 3.4% of US passengers in 2007, all days and airlines (TDI 2010) |
| Mean delay of a disrupted passenger, under the reference | 930 min | 457 min (TDI 2010), with rebooking on any airline and caps of 8 h by day and 16 h at night |
| Misconnects and refunds, under the reference | 250 misconnected a date; refunds none on most dates (at most 96) | none per date |
| JetBlue's own decisions replayed: of the passengers off its cancelled flights, the same day / overnight / refunded | 15% / 80% / 1.4%, mean delay 1,861 min (over the 64 dates whose cancelled flights carried passengers) | about 80% the same day (Bratu and Barnhart: a hub carrier in calm August 2000, rebooking onto other airlines) |

**Holds.** The reference's holds were counted (a median of 85 a date, up to 303; a median hold of 6 minutes), but the
count takes in every flight leaving later than its aircraft-only dispatch, crew waits of up to 709 minutes included,
and 55% of those holds sit on the 25 dates whose reference is v2's own plan. It doesn't measure holds for connecting
passengers, so the comparison with United's (about 4 minutes) and American's (about 10) published averages was
withdrawn.

## v3: the controls under P

Two calm control dates. v3's "doing nothing" is the trip rule with no crew desk at all; B0, as in v2, is the standard
crew desk with the aircraft doing nothing.

| | 2024-07-03 (summer) | 2025-01-15 (winter) |
|---|---:|---:|
| Doing nothing, no desk, over the reference | 1.10× | 1.39× |
| B0 over the reference, under P | 1.05× | 1.14× |
| B0 over the best plan, under v2's cost (v2's controls) | 1.04× | 1.17× |

- **P barely moves the calm-day gap** against B0; the large ratios are the no-desk arm's. On 2024-07-03 crew cost is
  100K of the 117K gap.
- **The winter control's reference cancels 7 flights at 04:00** on a calm day, 497 passengers among them a JFK–Los
  Angeles round trip (B6 323 and B6 724); v2's best plan cancels 1. Under P a calm-day cancellation whose passengers
  are rebooked the same day is cheap (66 a passenger, its aircraft costs saved): there it nets out cheaper. P carries
  no lost revenue or goodwill, which a real airline would weigh (labelled).

## v3: the reference and its bound

**The reference** (registered as v2's): the cheapest plan under P that replays exactly with a legal crew on every flight
it flies, from a fixed set of candidates: v2's joint best plan; B0; the passenger-aware aircraft plans, re-solved under
the 3 h line for all 70 dates and played through the standard crew desk (the MIP's incumbent, its rounded LP plan and
v2's aircraft plan); and one repair of the best of those with v2's own crew-plan search, capped at 1,800 s. No other
search.

- **Where it came from:** the repair on 42 of the 70 dates, v2's best plan on 25 and B0 on 3; on the 24 eval dates,
  16, 7 and 1. Re-simulated with the pinned code on the 24 eval and 2 control dates, the P of the reference, v2's best
  plan, B0 and doing nothing all match the stored values, with no flight uncrewed.
- **The legality check is weak as a filter:** in the named replay a flight whose named crew can't fly waits for it or
  cancels at its deadline, and that is priced in P, so no uncrewed flight is near-automatic (as in v2's definition).
- **The repair's cap was soft:** it ran past 1,800 s on 49 of the 70 dates, up to 2,268 s with setup. That can only
  make the reference cheaper; even if the five closest passing dates of 2b-1 flipped, 2b-1 would still pass.
- **B0 can be close to the reference under P.** B0 scores 0.308 where it costs at least 10% over the reference, which
  holds on 20 of the 24 eval dates. On 2023-07-29 (0.907), 2024-06-23 (where B0 is the reference, 1.0), 2024-07-15
  (0.697) and 2024-07-22 (0.498) it scores more. Under v2's cost B0 scored 0.308 on every eval date.
- **The reference is not an optimum.** It is the best of a fixed set and one capped repair, so a run can come in under
  it; such a run scores 1.0 and is flagged, as in v2.

**The bound** (reported only, no bar):

- **As first registered** (v2's bound machinery with every passenger term at its floor) it came out negative on all 70
  dates (−1.14M to −2.90M): with a cancelled passenger at its floor of 66, the floor model cancels everything.
- **Replaced (stage 2's re-registration 2) by a capacity-aware floor** (`pax.Proxy.lower`): a local passenger's delay
  charged exactly, a held connection at 0, and a disrupted passenger at the cheapest of three outcomes: the earliest
  possible arrival today on a flight into their destination, with seats relaxed to the family's largest aircraft; the
  first arrival on a later day plus one night's cheapest lodging; or the refund at the fare alone. The bound is the
  largest of the A*'s, the restricted A*'s, and the restricted A*'s plus each crew's least cost. It never charges more
  than P; a test checks that on 10 fixture days and 12 plans each.
- **On two train dates** its gaps were 58.6% (2024-07-28) and 81.4% (2025-02-18), most of it HiGHS not finishing (49%
  and 81% left at its 900 s limit).
- **The registered run** (all 70 dates, 1,800 s each): [TBD: bound run, valid on N of 70; eval-date gaps min to max,
  median].
- **So the gap mostly measures the bound,** not the reference, and `reward_L`, the reward against the bound, reads low.
  Read the reward against the reference first.

## v3: is there something for a customer desk to do?

Two pre-registered checks came before the desk was built.

**Step 0, the premise without crews.** Aircraft only, on v2's step-0 world. G1: v2's aircraft plan costs at least 5%
more under P than the passenger-aware plan, on 12 or more of the 24 eval dates. **Pass, 16 of 24**; the median ratio
1.098, the range 1.0 to 1.72, the smallest passing ratio 1.069 and the largest failing 1.0025.

- **It is not the connections.** The same 16 dates pass at half and at twice the connecting shares. The pass comes from
  pricing each cancelled passenger by their real rebooking instead of v2's flat 523: the passenger-aware plan cancels
  more flights than v2's on 11 of the 16 passing dates, flights whose passengers fit on other JetBlue flights, often
  earlier ones. That is what led to the 3 h line.
- **The MIP proved no date** (gaps 4.5% to 34%, and two dates got only negative bounds), ran past its 30-minute limit on
  16 dates, and the run stored no plans, so the 6 dates it improved can't be re-scored; without them G1 is still 14 of
  24.
- **Assigning the scarce seats optimally** instead of by the default order was worth 0% to 9% of P (median about
  1.6%) in step 0, before the 3 h line: an upper bound, since that assignment sees the whole day.

**Stage 2b, with crews, under the 3 h line.**

| Bar | Result | Needed | Verdict |
|---|---|---|---|
| 2b-1, the premise with crews: P(v2's joint best plan) at least 1.05 × P(the reference) | 17 of 24 eval dates; median 1.096, range 1.000 to 1.721 | 12 of 24 | pass |
| 2b-2, can a desk capture it: P(three scripted desks) at most 0.98 × P(two scripted desks) | 2 of 24 (2023-07-09 at 0.910, 2023-12-18 at 0.965); median 1.017, worst 1.126 (2023-07-13) | 12 of 24 | **fail** |

- **2b-1 is robust:** five passing dates sit between 1.05 and 1.09, and with all five flipped it would still be 12 of
  24.
- **The scripted customer desk** follows published practice, as registered: it holds an outbound up to 15 minutes when
  10 or more connecting passengers would otherwise misconnect, and asks ops not to cancel a flight when at least half of
  its passengers have no later JetBlue itinerary that day (stage 2's re-registration 1). An independent re-simulation
  confirmed both rules on 4 dates.
- **Why it hurts: the requests not to cancel.** With the customer desk, ops cancels 13 flights by its rule instead of
  48, and cancellations at the deadline rise from 1 to 32: the protected flights mostly can't fly, so they cancel hours
  later at late-notice cost (+266K on 2023-07-13, +305K on 2024-07-13). On 2023-07-09, the best date, ops' projection
  was too pessimistic and the protected flights flew late instead, cutting delay by 1.15M.
- **Holds alone** move P by −0.4% to +0.35%. The hold rule fired 11 times in 24 dates, partly because P2's thin
  connecting shares make batches of 10 rare.
- **Edgar's call: ship the customer desk anyway, labelled,** as v2 shipped its two desks when its coordination
  gate failed. The premise holds with crews; a scripted desk built from published practice doesn't capture it. Whether
  a model desk does is what the board measures.

## v3: stage 3, the model desks

[TBD: the env lane's zero-spend checks of `airops-occ3` (doing nothing reproduces the pack's reference, the scripted
desks reproduce stage 2b's arms, the stored reference plan scores 1.0, the audit, a leak test, determinism), the
pilot, and the board: runs scored, replayed exactly and audited, and the independent recheck of every reward.]

## v3: not modelled, labelled

| Left out | Why, and what stands in |
|---|---|
| International connections | DB1B and DB1C hold domestic itineraries only (US points, Puerto Rico and the USVI included), and BTS on-time data has no international flights, so v2's world has none. JetBlue flew 26% of its departures and 28% of its passengers internationally in 2025 (T-100); from the gap between T-100 and the survey, about 8% to 10% of its domestic passengers at JFK in 2025 Q2 were on itineraries with a foreign point (an estimate). None are generated |
| Other airlines' rebooking | JetBlue's contract promises JetBlue only; interline connections (0.15% to 0.71% of JetBlue's segment passengers) are not generated |
| Compensation, meals and hotels on storm days | force majeure under the contract, so none is charged; a night away costs the passenger one GSA lodging night |
| Rebooking by hand at the customer desk | rebooking stays automatic, earliest arrival first; reassigning the scarce seats by hand is worth 0% to 1% under the 3 h line, and is left out |
| Passengers who choose a refund | only those with no JetBlue seat within five days are refunded |
| Itineraries of three or more legs | 0.04% to 0.2% of JetBlue's; a passenger connects at most once |
| Extra sections and upgauges | no desk can add a flight or a larger aircraft |
| Lost revenue and goodwill | P carries the refund exposure and the refunded fares, not future revenue |
| The rules' dates | the five-day window and DOT's 3 h line apply on every date, though the plan's wording dates from 2025 and DOT's rule from 2024-10-28 |
| The viewer's passengers | the gate crowds, the walks, the counter queue and the hotel shuttle are drawn for display only |

## v3: misses and limits

1. **P2 failed:** the generated connecting shares run 0.1 to 1.9 points below the survey, because connections the
   generator can't place within 45 to 360 minutes are dropped.
2. **2b-2 failed:** a scripted customer desk built from published practice did not beat two scripted desks (2 of 24
   dates against 12 needed). The desk ships anyway, labelled.
3. **The bound is weak:** [TBD: bound run]. Most of the published gap is the bound, so `reward_L` reads low.
4. **The reference is a best-found plan,** from a fixed set of candidates and one capped repair whose cap ran over on
   49 of 70 dates.
5. **B0 is near the reference on some dates under P,** so it scores above 0.308 on 4 of the 24 eval dates.
6. **No public record checks the passengers' outcomes.** JetBlue's replayed day strands 80% of the passengers off its
   cancelled flights overnight under JetBlue-only rebooking; that is reported, not validated.
7. **The winter control's reference cancels 7 flights at 04:00** on a calm day, because P prices a calm-day
   cancellation cheaply.
8. **Step 0's MIP proved no date** and stored no plans; G1 holds at 14 of 24 without the dates it improved.
9. **The passengers are generated,** from route-month loads and a ticket survey, not JetBlue's bookings.

## v3: rerun the checks

They need the raw inputs (`scripts/build_pack.py` downloads them into `cache/inputs/`), the survey extracts under
`data/pax/` (`scripts/pax_shares.py` rebuilds them from DB1B and DB1C) and a large machine: stage 2 took about 2.5 h on
one r7i.48xlarge.

| Stage | Script |
|---|---|
| 0, the premise | `scripts/pax_step0.py --out cache/v3/step0/eval --split eval --seeds 4 --time-limit 1800 --sensitivity 0.5,2`, then `--crew` for v2's crew-world plans |
| 2, the reference and the checks | `scripts/pax_stage2_box.py --out cache/v3/stage2` (the aircraft plans under the 3 h line, then `scripts/pax_stage2.py`) |
| 2, the bound | `scripts/pax_bound.py --reference cache/v3/stage2/reference --out cache/v3/bound --limit 1800` |
| 2b | `scripts/pax_stage2b.py --out cache/v3/stage2b --reference cache/v3/stage2/reference` |
| the pack | `scripts/build_occ_pack.py --v3 cache/v3/stage2/reference --pack data/b6-v3 --bound cache/v3/bound` |
| 3, the board | [TBD: env lane's pilot command and the board's folder] |

---

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
