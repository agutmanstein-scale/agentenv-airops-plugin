---
license: other
license_name: us-public-domain-iem-mit-odbl-apache-2.0
license_link: https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.4.0/NOTICE
pretty_name: 'AirOpsEnv v3: passengers and the customer desk'
language:
- en
task_categories:
- reinforcement-learning
- text-generation
size_categories:
- n<1K
tags:
- agentenv
- rl-environment
- agents
- multi-agent
- airline-operations
- crew-scheduling
- passengers
- disruption-recovery
- scheduling
- operations-research
- mcp
- real-world-data
- weather
agentenv:
  default: airops
---

<div align="center">

<a href="https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv?episode=https://huggingface.co/datasets/earakely-scale/AirOpsEnv-AgentEnv/resolve/v0.4.0/viewer/episodes/occ3-board-sol-joint.openai-gpt-6.1-sol.b6-20250708-0400.json&t=498&view=room&play=1&speed=2"><img src="https://raw.githubusercontent.com/earakely-scale/agentenv-airops-plugin/v0.4.0/assets/airops-occ3-hero.webp" alt="GPT-6.1 Sol's three desks run JetBlue's control center through the storm of 8 July 2025: at Kennedy passengers who missed their connection reach the service counter and the stranded leave on the hotel shuttle, and in the room ops declines the customer desk's request not to cancel B6 675" width="100%"></a>

<sub>Three GPT-6.1 Sol desks run JetBlue's network through the storm of Tuesday 8 July 2025
(<code>b6-20250708-0400</code>), shown at Kennedy: 637 flights from 04:00 with 81,279 passenger trips, an FAA ground
stop in the afternoon and ground delay programs at JFK and Boston into the night. The crew scheduler makes 24 crew
moves, the ops controller holds 31 departures and cancels one flight, and the customer desk sends 60 requests; ops
accepts 30 of its 46 HOLDs and 7 of its 8 PROTECTs. Flown on what really happened, the day saves 85% of what standard
crew-desk practice leaves on the table: reward 0.795, against 0.655 for one Sol agent at all three desks and 0.308 for
the standard crew desk. In the clip passengers who missed their connection reach the service counter, the stranded
leave on the hotel shuttle, and ops declines the customer desk's request not to cancel B6 675. Click to replay the day
in 3D: the control-center room and the airport.</sub>

<h1>AirOpsEnv v3: passengers and the customer desk</h1>

<h3>Run an airline's whole network through a real storm day, its passengers on board, from the three desks of its control center</h3>

<p>An AgentEnv environment built from public U.S. flight, passenger, FAA and crew-rule records, graded with no judge against the best plan found in hindsight under a passenger cost.</p>

<a href="https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Space-Replays%20in%203D-FFD21E?style=for-the-badge&labelColor=1a1a1a" alt="Replays in 3D" height="32"></a>
<a href="https://github.com/earakely-scale/agentenv-airops-plugin"><img src="https://img.shields.io/badge/GitHub-agentenv--airops--plugin-181717?style=for-the-badge&logo=github&labelColor=1a1a1a" alt="Code on GitHub" height="32"></a>
<a href="https://www.agentenvframework.com"><img src="https://img.shields.io/badge/framework-AgentEnv-6f42c1?style=for-the-badge&labelColor=1a1a1a" alt="AgentEnv Framework" height="32"></a>

</div>

---

## The task

AirOpsEnv v2's control center runs JetBlue's whole network through a real storm day from two desks, the ops controller
and the crew scheduler. v3 puts the passengers on board and adds the third desk real control centers have beside
them: the **customer desk**. Its page lists the connections at risk (departures in the next 4 hours whose connecting
passengers make it with 15 minutes or less, or miss it) and the flights ops might cancel (auto-cancelling, without a
crew, asked to cancel, or 3 hours late or more), with where their passengers would go if cancelled now: the same day,
a later day or a refund. It asks ops to HOLD a departure for its connecting passengers, to PROTECT a flight (don't
cancel it), to SUGGEST_CANCEL one whose passengers are better off rebooked now, or writes TEXT. Ops decides, as in real
control centers, where the customer desk proposes and ops disposes: an accepted HOLD is ops' hold, and PROTECT and
SUGGEST_CANCEL are advice ops carries out, if at all, with its own tools. No desk rebooks passengers: the passenger
system does, on JetBlue only per its contract, earliest arrival first. The day is played on the
[AgentEnv Framework](https://www.agentenvframework.com)'s virtual clock in hourly watches from 04:00: each watch the
crew desk takes its turn, then the customer desk, then ops, then the crew desk again if ops asked it something and the
customer desk if ops texted it. It is graded once, on what really happened, under a cost that follows every passenger
to their final destination. The environment is the plugin
[earakely-scale/agentenv-airops-plugin](https://github.com/earakely-scale/agentenv-airops-plugin).

| Desk | Tools |
|---|---|
| ops | `ops_flights`, `ops_program`, `ops_aircraft`, `ops_check`, `ops_confirm`, `ops_request` (COVER to the crew desk, TEXT to either), `ops_reply` |
| crew | `crew_flights`, `crew_crews`, `crew_reserves`, `crew_program`, `crew_options`, `crew_check`, `crew_confirm`, `crew_request` (HOLD, CANCEL, CANCEL_PAIR, TEXT), `crew_reply` |
| customer | `cust_flights`, `cust_flight`, `cust_request` (HOLD, PROTECT, SUGGEST_CANCEL, TEXT) |
| all three | `end_turn` (its result is the next page), `wait_turn`, `resign` |

In mode `one`, one agent runs all three desks from one page, with no channel, for comparison.

## The days

The pack `b6-v3` is v2's 70 dates (24 eval storm dates, 44 training dates from other storm events and 2 calm
controls), each with its passengers. Itineraries aren't public, so the passengers are generated stand-ins, per date
from public records: each flight's load from BTS T-100, and who connects where from the DOT's ticket survey of the
date's own period (DB1B to June 2025, DB1C after). An eval date has a median of 86,148 passenger trips, 2.7% of them
connecting at JFK, Boston, Fort Lauderdale, Orlando or San Juan. When a flight is cancelled or a connection missed,
its passengers are rebooked the way JetBlue's contract and customer service plan say: on JetBlue only, from the
decision, the earliest arrival first, for up to five days, then a refund; a flight 3 h or more earlier only when no
other seat is left, per DOT's refund rule.

How faithful the passengers are, with every check and every miss (a connecting-share check that ran low among them),
is in [FIDELITY.md](https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.4.0/FIDELITY.md).

## Grading

The day's cost P, in passenger-minutes: each passenger's arrival delay at their final destination, rebooking
included; 66 per cancelled passenger (the refund exposure, more when the cancellation comes under 2 hours ahead); a
lodging night for each night a passenger waits; the fare of each passenger refunded; and v2's aircraft and crew costs.
JetBlue owes no compensation on a storm day, so none is charged. The reward is 0.2 + 0.8 exp(−g/0.5), with
g = max(0, P − best) / max(B0 − best, 0.1 best):

- **best** (1.0): the cheapest plan under P a fixed search found knowing the whole day (v2's best plan, B0, the
  passenger-aware aircraft plans with the standard crew desk, and a repair of the best of them), replayed exactly with
  a legal crew on every flight;
- **bound**: a capacity-aware lower bound under P, published per date, 54% to 79% under the best plan on the eval dates
  (median 66%), most of it the solver not finishing; `reward_L` is the reward against it, so it reads low;
- **B0**: standard crew-desk practice with the aircraft doing nothing; 0.308 on 20 of the 24 eval dates, more on the
  4 where it comes within 10% of the best plan.

v2's best plan, blind to passengers, costs at least 5% more than the best plan on 17 of the 24 eval dates and scores a
mean 0.602. A run is scored only if its audit passes, as in v2. No judge.

## Results

GPT-6.1 Sol (reasoning effort high) on the 24 eval dates, once in each mode, registered before the first run, at caps
of $3 a desk and $6 for one agent. All 48 runs scored, every one replays exactly through the env and passes the audit;
the whole board cost $107.04.

<!-- board-v3 -->

<!-- /board-v3 -->

**What this shows, and what it doesn't.** Three Sol desks scored above one Sol agent running all three on these 24
dates (+0.044, bootstrap CI 0.007 to 0.083, higher on 14 dates and lower on 5), and both recover most of what standard
crew-desk practice loses. It doesn't show that the third desk or talking helps. The margin is small (a sign test gives
p = 0.064, and without the 7 dates with a below_best run the CI is −0.002 to 0.090), Sol's score for the same day and
mode moves by up to 0.23 from one run to the next, and the three desks used about twice the compute (2.0× the spend,
2.0× the input and 2.4× the output tokens, more spend on every date). Nor does it measure the customer desk by itself,
as there is no two-desk arm under P; the model customer desk sent 1,303 requests over the 24 days, 664 of them
accepted by ops. 12 of the 48 runs came in under the best plan found, both modes on 5 dates and three desks alone on 2:
they score 1.0 and are flagged `below_best`, with `reward_L` beside them (0.32 to 0.59), and on 4 of those 5 dates B0
itself comes within 10% of the best plan. Before any model ran, a scripted customer desk built
from published practice (hold up to 15 minutes for 10 or more connecting passengers; ask ops not to cancel a flight
when half its passengers have no later seat that day) did not beat two scripted desks: better by 2% on 2 of 24 eval
dates, against 12 needed. Its requests not to cancel mostly protected flights that couldn't fly, which then cancelled
at their deadline at late notice. The customer desk ships anyway, labelled.

## Watch a run

Every three-desk run replays in the [replay Space](https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv),
watch by watch, as v2's do, with the passengers added: the customer desk at its pod in the room, its requests
travelling to ops and back; the gate lounges behind the terminal's glass filling and boarding, connecting passengers
walking gate to gate, the line at the service counter and the hotel shuttle at the curb; a passengers pane with the
missed connections, rebookings and nights away; and where passengers are stranded on the US map. The lounges, the
counter, the passengers' walks and the shuttle are inferred, for display only.

## Tables

All parquet, one config per table, a split per split of the pack (`eval`, `control`, `train`).

| Config | What |
|---|---|
| `v3_tasks` (default) | one row per date: v2's day (hubs, size, crews and reserves, the FAA's advisories as the desks get them), its passengers (trips, connecting, the survey period, a refunded passenger's fare in passenger-minutes), every reference under P and its reward (best, bound, gap, B0, doing nothing, v2's best plan, JetBlue's own decisions replayed), the desks' rules, and the date's world with its passengers as the env loads it (`world`) |
| `v3_board` | one row per mode with its mean reward and reward_L and their 95% bootstrap CIs, B0, and the paired difference of the two modes |
| `v3_references` | one row per date: every candidate the best plan was chosen from with its P and reward, where the best plan came from, the bound, the gap, v2's best plan over the best, and the best plan itself |
| `v3_episodes` | one row per recorded attempt: reward, reward_L and the costs against the best plan, the bound and B0, the day's cost by kind (lodging and refunded fares among them), cancellations, holds, crew moves and requests by type and outcome, spend, tokens, turns, each desk's end, the audit, its decisions and the request log; `on_board` and `superseded_by` mark the board's runs |
| `v3_desks` | one row per desk of each attempt, the customer desk's included, with its usage and its transcript as chat `messages` |
| `v2_tasks`, `v2_board`, `v2_runs`, `v2_desks` | AirOpsEnv v2, as published in v0.3.0 (below) |
| `v1_tasks`, `results`, `v1_references`, `v1_episodes` | AirOpsEnv v1, as published in v0.2.0 (below) |

And agentenv-hf's for v1's bundle `airops`, exactly as `agent-env hf publish` writes them: `airops_tasks` and
`airops_episodes`; `raw/airops.jsonl` holds each v1 run's record and native trajectory. `bundles/airops/`,
`bundles/airops-occ/` and `bundles/airops-occ3/` are the three bundles `agent-env hf run` plays, v1's the default.

`results/eval-v3.json`, `results/eval-v2.json` and `results/eval-v1.json` are the eval cards, and `runs/` holds the run
folders as recorded: each control-center attempt's day record (`days/`) and each desk's transcript (`transcripts/`),
and v1's sweep. `viewer/` holds the episodes as the replay Space reads them: each episode's JSON and its traffic under
`episodes/`, the airport stages under `stage/<HUB>/`, the NEXRAD radar frames under `radar/`, the hub twins under
`twin/` and the list's stills under `thumbs/`.

```python
import json

from datasets import load_dataset

days = load_dataset("earakely-scale/AirOpsEnv-AgentEnv", "v3_tasks", split="eval")
day = days[0]
print(day["passengers"], day["connecting"])          # the date's passenger trips, and those connecting
world = json.loads(day["world"])                     # the day as the env loads it, passengers included
refs = load_dataset("earakely-scale/AirOpsEnv-AgentEnv", "v3_references", split="eval")
runs = load_dataset("earakely-scale/AirOpsEnv-AgentEnv", "v3_episodes", split="eval").filter(lambda r: r["on_board"])
desks = load_dataset("earakely-scale/AirOpsEnv-AgentEnv", "v3_desks", split="eval")
desks[0]["messages"]                                 # a desk's chat transcript, tool-call arguments as objects
```

## Run it yourself

You need Docker, [uv](https://docs.astral.sh/uv/) and git. Install the plugin, build the env and the desk agent, check
the wiring with a day no model plays, then play a day straight from this dataset with a model on your LiteLLM endpoint
(or the Hugging Face router):

```bash
uv tool install agentenv-framework \
    --with "agentenv-airops @ git+https://github.com/earakely-scale/agentenv-airops-plugin@v0.4.0"
agent-env airops-occ3 setup
agent-env hf run earakely-scale/AirOpsEnv-AgentEnv@v0.4.0 --bundle airops-occ3 --task wiring-noplay
agent-env hf run earakely-scale/AirOpsEnv-AgentEnv@v0.4.0 --bundle airops-occ3 --task b6-20240713-0400-joint \
    --model openai/gpt-6.1-sol
```

The bundle `airops-occ3` (`bundles/airops-occ3/`) holds every date of the pack in both modes, `<task id>-joint` and
`<task id>-one`, the very task files the board ran (each desk capped at $3 in a three-desk run, $6 for one agent), and
`wiring-noplay`, which runs the first eval date with no desk. v2's two-desk days stay in the bundle `airops-occ`, and
the dataset's default bundle is v1's `airops`. `hf run` downloads this card and `bundles/` at the tag's commit, checks
that the plugin the card's `agentenv` table names is installed, shows what it will run and asks first.

---

# AirOpsEnv v2: the ops control center

v2, published in v0.3.0 and unchanged here, runs the same network with two desks and no passengers: a cancelled
passenger costs a flat 523 passenger-minutes.

<div align="center">

<a href="https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv?episode=https://huggingface.co/datasets/earakely-scale/AirOpsEnv-AgentEnv/resolve/v0.4.0/viewer/episodes/occ-board-sol-joint.openai-gpt-6.1-sol.b6-20240713-0400.json&t=362&view=room&play=1&speed=2"><img src="https://raw.githubusercontent.com/earakely-scale/agentenv-airops-plugin/v0.4.0/assets/airops-occ-hero.webp" alt="Two GPT-6.1 Sol desks run JetBlue's operations control center through the Boston storm of 13 July 2024 on AgentEnv: the control-center room, where requests pass between the ops controller and the crew scheduler, and Logan in 3D, where crews walk to their gates and held flights leave" width="100%"></a>

<sub>Two GPT-6.1 Sol desks run JetBlue's network through the Boston storm of Saturday 13 July 2024
(<code>b6-20240713-0400</code>): 612 flights on 193 aircraft from 04:00, an FAA ground stop and a ground delay program
at Logan in the afternoon. The crew scheduler makes 34 crew moves, the ops controller cancels 7 flights and holds 3,
and between them pass 15 messages, a HOLD and three COVER requests. Flown on what really happened, the day saves 86% of
what standard crew-desk practice leaves on the table: reward 0.806, against 0.657 for one Sol agent running both desks
and 0.308 for the standard crew desk. In the clip the crew desk's HOLD of B6 515 is accepted in the room and B6 515
pushes back held until 07:45. Click to replay the day in 3D: the control-center room and the airport.</sub>

</div>

## v2: the task

AI agents run JetBlue's operations control center through a real storm day, split between two desks the way airline
control centers split the work. The **ops controller** runs the flights and aircraft with the FAA: it cancels flights
and round trips, holds them, and claims and swaps arrival slots in the FAA's ground delay programs. The **crew
scheduler** runs the pilots and flight attendants: it calls reserves, swaps crews between trips, deadheads them as
passengers and rests them in hotels, with pilots under FAR 117 and flight attendants under 14 CFR 121.467. Neither can
do the other's job, so they talk, through typed requests the other desk accepts or declines (HOLD a flight until its
crew is ready, CANCEL or CANCEL_PAIR, COVER a flight) and free text. The day is a real one, JetBlue's whole network from
04:00 Eastern, played on the [AgentEnv Framework](https://www.agentenvframework.com)'s virtual clock in hourly watches:
each watch the crew desk takes its turn, then ops, then the crew desk again if ops asked it something. The FAA Command
Center's ground stops and ground delay programs at JFK and Boston arrive as they were issued, and each flight's delay
becomes known 2 hours before it leaves. The day is then graded once, on what really happened. The environment is the
plugin [earakely-scale/agentenv-airops-plugin](https://github.com/earakely-scale/agentenv-airops-plugin).

| Desk | Tools |
|---|---|
| ops | `ops_flights`, `ops_program`, `ops_aircraft`, `ops_check`, `ops_confirm`, `ops_request` (COVER, TEXT), `ops_reply` |
| crew | `crew_flights`, `crew_crews`, `crew_reserves`, `crew_program`, `crew_options`, `crew_check`, `crew_confirm`, `crew_request` (HOLD, CANCEL, CANCEL_PAIR, TEXT), `crew_reply` |
| both | `end_turn` (its result is the next page), `wait_turn`, `resign` |

The ops desk sees the network as projected now, with each flight's crew status from the crew system (legal, times out
at HH:MM, waits for its crew until HH:MM, no crew); the crew desk also sees every crew's trip, duty clock and rest, and
the reserves at each base. A crew system crews every flight the crew desk leaves alone by standard practice and can't
cancel. In mode `one`, one agent runs both desks from one page, with no channel, for comparison.

## v2: the days

The pack `b6-v2`: 24 eval storm dates, drawn 12 per storm hub (8 summer and 4 winter each, every one from its own storm
event; `drawn_for` is the hub a date was drawn for, `hub` the airport the viewer shows), 44 training dates from other
storm events, and 2 calm control days, from 19 summer and winter months between December 2022 and July 2026. An eval
date has a median of 696 flights on 218 aircraft, 364 pilot and 356 cabin crews on trips and about 180 reserves.
Everything is built from public U.S. records: the flights, tails and delays from BTS on-time data, aircraft from Form 41
and the FAA registry, the FAA Command Center's advisories, the crew rules from the eCFR, the crew bases from public
filings and union pages, and the costs from the FAA, the BLS and GSA. JetBlue's pairings are not public, so the crews'
trips are generated from the published schedule by a cost-optimizing builder, and reserves are set at a sourced industry
band.

How faithful the day is, layer by layer, with every check against the record and every miss, is in
[FIDELITY.md](https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.4.0/FIDELITY.md#airopsenv-v2-fidelity-report).

## v2: grading

The day's cost, in passenger-minutes: each passenger's arrival delay; each cancelled passenger (523, more when the
cancellation comes under 2 hours ahead); the FAA's rates for aircraft time and tonight's ferries; crew deadheads,
hotels and crews left out of place for tomorrow. The reward is 0.2 + 0.8 exp(−g/0.5), with
g = max(0, cost − best) / max(B0 − best, 0.1 best):

- **best** (1.0): the cheapest day of aircraft and crews together a solver found knowing the whole day, replayed
  exactly with a legal crew on every flight;
- **bound**: its proven lower bound; the gap is published per date (eval dates 2.4% to 13.9%, median 5.1%), and
  `reward_L` is the reward against it;
- **B0** (0.308): standard crew-desk practice with the aircraft doing nothing.

A run is scored only if its audit passes: every watch's news on time, every call in its desk's turn, no decision
reaching back, each desk in its role, a legal crew on every flight, the cost not under the bound, and both desks
present. No judge.

## v2: results

GPT-6.1 Sol (reasoning effort high) on the 24 eval dates, once in each mode, pre-registered before the first run.
Every run scored, replays exactly through the env and passes the audit.

<!-- board-v2 -->

<!-- /board-v2 -->

**What this shows, and what it doesn't.** Two Sol desks beat one Sol agent running both on these 24 dates, and both
recover most of what standard crew-desk practice loses. It doesn't show that talking is what helps: the two desks used
about twice the compute (1.9× the spend, 2.0× the input and 2.5× the output tokens, more on every date), so the board
can't separate two desks from more compute; the channel-off run comes next. Before any model ran, scripted desks
failed the gate for talking (worth at least 5 points of the avoidable cost on 3 of 24 dates, against 15 needed). On
2023-07-13 the two desks came in under the best plan (reward 1.0, flagged; reward_L 0.578 is the fairer reading), and
on 2026-07-09 the registered retry replaces an attempt whose crew desk lost its connection; `v2_runs` keeps both.

## v2: watch a run

Every run replays in the [replay Space](https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv), watch by
watch: the control-center room in 3D, tiered desk rows facing a video wall of the network, the radar and the FAA's
programs, with the two desks at their pods and their requests travelling between them; the hub airport in 3D, with every
airline's flights on their real times, the crews walking to their gates, riding the apron bus and leaving in the hotel
van when their duty ends away from base, a first officer walking round each departing aircraft before pushback, and
aircraft tagged "waiting for crew"; the US map with the radar and the crews resting away from base, the reserves on call
and the crews riding as passengers; and expert panes with each crew's duty clock against its legal limit, the full
request log and the pages the desks read. The room, the crew room, the crews' paths and the walk-rounds are drawn for
display only.

## v2: run it

Once `agent-env airops-occ setup` has built v2's env and desk agent, play a v2 day from this dataset with
`agent-env hf run earakely-scale/AirOpsEnv-AgentEnv@v0.4.0 --bundle airops-occ --task b6-20240713-0400-joint --model <model>`.
The bundle `airops-occ` holds every date of `b6-v2` in both modes, `<task id>-joint` and `<task id>-one`, the very
task files v2's board ran (each desk capped at $5 in a two-desk run, $8 for one agent), and `wiring-noplay`.

---

# AirOpsEnv v1: one desk at one hub

v1, published in v0.2.0 and unchanged here, is one operations desk at one hub in the 3 to 6 hours of a storm day where
planning matters most, with aircraft only.

## v1: the task

An agent runs the JetBlue operations desk at one of its hubs, JFK or Boston, on a real day that went wrong:
thunderstorms, snow or wind, and the FAA ground stops and ground delay programs that followed, cut how many JetBlue
flights could land and take off each hour. Watch by watch, on the [AgentEnv Framework](https://www.agentenvframework.com)'s
virtual clock, it decides which flights to cancel and which to hold so that passengers lose as little time as
possible. The FAA Command Center's advisories, the hub's METARs and TAFs and the forecasters' discussion reach it as
they were issued; flight delays and slot caps become known as they would have. The day is then flown on what really
happened and graded, with no judge, against the plan a MIP solver proves optimal in hindsight. The environment is the
plugin [earakely-scale/agentenv-airops-plugin](https://github.com/earakely-scale/agentenv-airops-plugin).

| tool | what it does |
|---|---|
| `get_situation()` | the time, the freeze line, the slot caps as known for the next 8 hours, the FAA programs in effect, the latest METAR and TAF, every flight and where the aircraft are |
| `check_plan(changes)` | runs changes over the plan on the day as known without committing them; one of the watch's 3 planning calls |
| `confirm_changes(changes)` | commits them; also one of the watch's 3 planning calls |
| `advance()` | ends the watch: the clock moves an hour and flights leave as planned |

- **Watches.** The day is played in hourly watches from an hour before the disruption window, until every delay and
  cap is known (or no flight is left to decide). Time passes only when the planner calls `advance`.
- **Decisions.** Per flight: cancel it, restore it, or hold it until a time (`not_before`). A change is
  `{"flight": "f12", "action": "cancel" | "restore" | "hold", "not_before": "HH:MM" | null}`. Aircraft of one family
  at one airport are interchangeable: the env dispatches them, keeps the hourly slot caps, cancels a flight that can't
  leave within 6 hours of its schedule, and ferries aircraft to where tomorrow needs them.
- **What the desk knows.** A flight's own delay becomes known 2 hours before its scheduled departure; an hour's slot
  cap 4 hours ahead (until then JetBlue's own schedule stands in); the news as issued, delivered by the env's triggers
  at the first watch at or after it.
- **The freeze.** From the second watch on, flights the plan has leaving within the hour are frozen and can't change;
  the env holds any other flight the plan would have leaving before that line to it.

## v1: the days

Each task is one hub window on a real day of the pack `b6-v1` (JetBlue, JFK and Boston), built from 13 months of 2022
to 2025: 20 eval days, 3 control days (no cancellation in scope) and 34 training days, with 59 to 194 flights on 30 to
59 aircraft and 10 to 22 watches. Each day's window is the 3 to 6 hours where planning matters most, where weather and
air traffic control caused at least 80% of the cancellations; an eval day also needs doing nothing to cost at least
10% over the hindsight optimum and the re-planning follower to land within 15% of it.

Everything is built from public U.S. records: the flights, tails and delays from BTS on-time data, passengers from
T-100, aircraft from the FAA registry and Form 41, the advisories from the FAA Command Center and the weather from the
NWS via the Iowa Environmental Mesonet.

## v1: grading

The final plan is flown on the day as it happened. Its cost, in passenger-minutes, is every passenger's arrival
delay, plus 523 per passenger on a cancelled flight (NEXTOR's average re-accommodation delay plus refund exposure at
the FAA's value of time), plus the end-of-day ferries. The reward is 0.2 + 0.8 exp(−g/0.5), with g the cost over the
hindsight optimum in units of that optimum's avoidable cost (hindsight − floor + 2% of hindsight): 1.0 at the
hindsight optimum and never below 0.2, since the env never strands a flight. The references, each run on the truth:

- **hindsight** (the anchor): the time-line MIP (HiGHS, 1-minute arcs) on the day as it happened, proven to 0.01%;
- **floor**: the same without aircraft routing, a lower bound;
- **follower**: the MIP re-solved at every watch on the day as known (persistence forecast), freezing as the env does;
- **blind**: the follower with no forecast; **naive**: no plan; **airline**: JetBlue's own cancellations and times.

## v1: results

<!-- board -->

<!-- /board -->

## v1: watch a run

[![GPT-6.1 Sol's run of JetBlue's Boston operations desk through the storm of 16 February 2025, replayed in 3D at Logan](https://raw.githubusercontent.com/earakely-scale/agentenv-airops-plugin/v0.4.0/assets/airops-hero.webp)](https://earakely-scale-airopsenv-agentenv.static.hf.space/viewer/index.html?episode=https%3A%2F%2Fhuggingface.co%2Fdatasets%2Fearakely-scale%2FAirOpsEnv-AgentEnv%2Fresolve%2Fv0.4.0%2Fviewer%2Fepisodes%2Fsol-v1.openai-gpt-6.1-sol.b6-bos-20250216-1500.json)

<sub>GPT-6.1 Sol runs JetBlue's Boston desk through the storm of Sunday 16 February 2025 (`b6-bos-20250216-1500`): 81
flights to 38 airports, 12 hourly watches. The FAA Command Center's ground delay program, three ground stops and their
cancellations reach it as they were issued, with 8 TAFs and 21 METARs. In 21 planning calls it cancels and holds
flights; the day, flown on what really happened, costs 11.3% more than the plan a MIP proves optimal in hindsight:
reward 0.672. The re-planning reference scores 0.937, doing nothing 0.366 and JetBlue's own decisions that day 0.224.
Click to replay it in the Space; the [full film](https://github.com/earakely-scale/agentenv-airops-plugin/releases/tag/v0.2.0)
is on the release. Airport © OpenStreetMap contributors (ODbL), imagery USGS The National Map; flights BTS; weather
NWS and NEXRAD via the Iowa Environmental Mesonet.</sub>

Every scored run replays in the [replay Space](https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv). The
hub fills the screen in 3D, modelled from OpenStreetMap over USGS aerial photos, under the weather the METARs report:
every airline's flights land, taxi and take off on their real BTS times, other airlines' aircraft in grey, and the
task's JetBlue aircraft follow the agent's plan with tags that say whether each flight is on time, delayed, held or
cancelled. A US map of the radar and the aircraft in the air sits in a corner (click it to swap), beside a departures
board, the desk telling each watch in plain English, and an expert mode with the advisories, METARs, TAFs, caps and
tool calls as the agent saw them. The stands, taxi routes and runway in use are the viewer's inferences, for display
only.

## v1: simplifications

v2 lifts several of these (the whole network, FAA slots rather than caps, crews); what it leaves out is in FIDELITY.md.

- **One airline, two hubs.** Only JetBlue's flights at JFK or Boston in a window around the disruption are in play;
  other carriers appear only through the hourly caps.
- **Caps, not slots.** An hour's cap at an airport is JetBlue's ration-by-schedule share of the movements all carriers
  actually made in it, never below what JetBlue's in-scope flights moved there. It is not the FAA's program rate.
- **Aircraft only.** No crews, no maintenance, no gates; aircraft of one family at one airport are interchangeable,
  so tail assignment is the env's. Turn times are each family's 5th percentile of the day's observed turns.
- **Delays are exogenous.** A flight's own delay is its departure delay less the late-aircraft part; a cancelled
  flight's is the 75th percentile of flown flights near it. Holding or cancelling never changes another airline's
  delays, and diversions count as cancellations.
- **Passengers are numbers.** Passengers per flight come from T-100 load factors; there are no connections,
  rebookings or crew legality, and a cancelled passenger costs a flat 523 minutes.
- **The weather is text.** METARs, TAFs, forecast discussions and FAA advisories arrive as issued; the radar and the
  3D weather in the viewer are for people, not the planner.
- **The viewer's airport is for display.** Its stands, taxi routes and runway in use are inferred (BTS records none of
  them); other airlines' traffic and JetBlue's aircraft outside the task fly their real BTS times and play no part in
  the env.

## Licence and attribution

The dataset carries several sets of terms, hence `license: other`; the plugin's
[NOTICE](https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.4.0/NOTICE) and each source's `SOURCE.md`
set them out.

- **U.S. Government works, public domain**: the BTS on-time, T-100 and Form 41 data, the DOT's Airline Origin and
  Destination Survey (DB1B and DB1C) behind v3's passengers, the FAA registry and the FAA Command Center advisories
  (17 U.S.C. § 105), and the USGS The National Map orthoimagery under the airport stages in `viewer/stage/`.
- **The Iowa Environmental Mesonet** (Iowa State University) archives the METARs, TAFs, forecast discussions and the
  NEXRAD n0q composites in `viewer/radar/`: NWS products, public domain; the IEM asks for attribution, given here.
- **airportsdata** (MIT licence, Mike Borsetti and contributors): the airports' names, coordinates and time zones.
- **v2's crew rules and costs**: 14 CFR parts 117 and 121 (eCFR) and the Federal Register, the FAA's economic values
  (Table 4-7), BLS wages, benefits and CPI-U, and GSA and DoD per diem rates, all U.S. Government works; crew bases,
  fleet configurations and the deadhead fare are facts cited from JetBlue's 10-K filings and press releases, ALPA and
  Airline Pilot Central, each with its URL in the plugin's `src/agentenv_airops/programs/crew_params.json`.
- **v3's rebooking rules** are facts of JetBlue's Contract of Carriage and Customer Service Plan, quoted with their
  source in FIDELITY.md, and DOT's refund rule, 14 CFR part 260 (a U.S. Government work).
- **The airport stages in `viewer/stage/` and the hub twins in `viewer/twin/`**: © OpenStreetMap contributors, under
  the Open Database License (ODbL 1.0).
- **Apache-2.0**: the verifier script in `bundles/` and the plugin itself, whose 3D viewer adapts scene code from
  PortSimEnv v1 (FineEnvs, Apache-2.0).
- **Not in this dataset**: the replay Space's US map loads Natural Earth relief (public domain) and OpenMapTiles vector
  tiles (© OpenStreetMap contributors, ODbL 1.0) from OpenFreeMap as it runs, and coarser USGS imagery around the
  airport stages from The National Map. The Space ships its viewer's code under `viewer/vendor/`: Three.js (MIT),
  MapLibre GL JS (BSD-3-Clause), and for the control-center room's video wall topojson-client with us-atlas (US Census
  Bureau boundaries) and world-atlas (Natural Earth), all ISC.

`scripts/hub_dataset.py` in the plugin builds this dataset with agentenv-hf, after its scan of every file for keys and
token shapes. This dataset is independent: neither JetBlue, the FAA, the BTS, the NWS, the USGS, the IEM nor any
union is affiliated with it or endorses it.
