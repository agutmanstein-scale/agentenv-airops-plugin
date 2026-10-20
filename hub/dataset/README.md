---
license: other
license_name: us-public-domain-iem-mit-odbl-apache-2.0
license_link: https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.3.0/NOTICE
pretty_name: 'AirOpsEnv v2: the ops control center'
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

<a href="https://earakely-scale-airopsenv-agentenv.static.hf.space/viewer/index.html?episode=https%3A%2F%2Fhuggingface.co%2Fdatasets%2Fearakely-scale%2FAirOpsEnv-AgentEnv%2Fresolve%2Fv0.3.0%2Fviewer%2Fepisodes%2Focc-board-sol-joint.openai-gpt-6.1-sol.b6-20240713-0400.json"><img src="https://raw.githubusercontent.com/earakely-scale/agentenv-airops-plugin/v0.3.0/assets/airops-occ-hero.webp" alt="Two GPT-6.1 Sol desks run JetBlue's operations control center through the Boston storm of 13 July 2024 on AgentEnv: the control-center room, where requests pass between the ops controller and the crew scheduler, and Logan in 3D, where crews walk to their gates and held flights leave" width="100%"></a>

<sub>Two GPT-6.1 Sol desks run JetBlue's network through the Boston storm of Saturday 13 July 2024
(<code>b6-20240713-0400</code>): 612 flights on 193 aircraft from 04:00, an FAA ground stop and a ground delay program
at Logan in the afternoon. The crew scheduler makes 34 crew moves, the ops controller cancels 7 flights and holds 3,
and between them pass 15 messages, a HOLD and three COVER requests. Flown on what really happened, the day saves 86% of
what standard crew-desk practice leaves on the table: reward 0.806, against 0.657 for one Sol agent running both desks
and 0.308 for the standard crew desk. Click to replay it in 3D: the control-center room and the airport.</sub>

<h1>AirOpsEnv v2: the ops control center</h1>

<h3>Run an airline's whole network through a real storm day, from the two desks of its control center</h3>

<p>An AgentEnv environment built from public U.S. flight, FAA and crew-rule records, graded with no judge against the best plan of aircraft and crews found in hindsight, next to its proven bound.</p>

<a href="https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Space-Replays%20in%203D-FFD21E?style=for-the-badge&labelColor=1a1a1a" alt="Replays in 3D" height="32"></a>
<a href="https://github.com/earakely-scale/agentenv-airops-plugin"><img src="https://img.shields.io/badge/GitHub-agentenv--airops--plugin-181717?style=for-the-badge&logo=github&labelColor=1a1a1a" alt="Code on GitHub" height="32"></a>
<a href="https://www.agentenvframework.com"><img src="https://img.shields.io/badge/framework-AgentEnv-6f42c1?style=for-the-badge&labelColor=1a1a1a" alt="AgentEnv Framework" height="32"></a>

</div>

---

## The task

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

## The days

The pack `b6-v2`: 24 eval storm dates (12 at JFK and 12 at Boston, 8 summer and 4 winter each, every one from its own
storm event), 44 training dates from other storm events, and 2 calm control days, from 19 summer and winter months
between December 2022 and July 2026. An eval date has a median of 696 flights on 218 aircraft, 364 pilot and 356 cabin
crews on trips and about 180 reserves. Everything is built from public U.S. records: the flights, tails and delays
from BTS on-time data, aircraft from Form 41 and the FAA registry, the FAA Command Center's advisories, the crew rules
from the eCFR, the crew bases from public filings and union pages, and the costs from the FAA, the BLS and GSA.
JetBlue's pairings are not public, so the crews' trips are generated from the published schedule by a cost-optimizing
builder, and reserves are set at a sourced industry band.

How faithful the day is, layer by layer, with every check against the record and every miss, is in
[FIDELITY.md](https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.3.0/FIDELITY.md).

## Grading

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

## Results

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

## Watch a run

Every run replays in the [replay Space](https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv), watch by
watch: the control-center room in 3D, tiered desk rows facing a video wall of the network, the radar and the FAA's
programs, with the two desks at their pods and their requests travelling between them; the hub airport in 3D, with every
airline's flights on their real times, the crews walking to their gates, riding the apron bus and leaving in the hotel
van when their duty ends away from base, and aircraft tagged "waiting for crew"; the US map with the radar and the crews
resting away from base, the reserves on call and the crews riding as passengers; and expert panes with each crew's duty
clock against its legal limit, the full request log and the pages the desks read. The room, the crew room and the crews'
paths are drawn for display only.

## Tables

All parquet, one config per table, a split per split of the pack (`eval`, `control`, `train`).

| Config | What |
|---|---|
| `v2_tasks` (default) | one row per date: its size, crews and reserves, the FAA advisories as the desks get them, every reference's cost and reward (best, bound, gap, B0, idle, the scripted desks, JetBlue's own decisions replayed, the calm day), the desks' rules, and the date's world as the env loads it (`world`) |
| `v2_board` | one row per mode with its mean reward and reward_L and their 95% bootstrap CIs, B0, and the paired difference of the two modes |
| `v2_runs` | one row per recorded attempt (the board's 48, its retry and the pilot's 6): reward, reward_L and the costs against the best plan, the bound and B0, the day's cost by kind, cancellations, holds, crew moves and requests by type and outcome, spend, tokens, turns, each desk's end, whether it replays exactly and passes the audit, its decisions and the request log; `on_board` and `superseded_by` mark the board's runs |
| `v2_desks` | one row per desk of each attempt, with its usage and its transcript as chat `messages` |
| `v1_tasks`, `results`, `v1_references`, `v1_episodes` | AirOpsEnv v1, as published in v0.2.0 (below) |

And agentenv-hf's for v1's bundle `airops`, exactly as `agent-env hf publish` writes them: `airops_tasks` and
`airops_episodes`; `raw/airops.jsonl` holds each v1 run's record and native trajectory. `bundles/airops/` and
`bundles/airops-occ/` are the two bundles `agent-env hf run` plays, v1's the default.

`results/eval-v2.json` and `results/eval-v1.json` are the eval cards, and `runs/` holds the run folders as recorded:
each control-center attempt's day record (`days/`) and each desk's transcript (`transcripts/`), and v1's sweep.
`viewer/` holds the episodes as the replay Space reads them: each episode's JSON and its traffic under `episodes/`, the
airport stages under `stage/<HUB>/`, the NEXRAD radar frames under `radar/`, the hub twins under `twin/` and the list's
stills under `thumbs/`.

```python
import json

from datasets import load_dataset

days = load_dataset("earakely-scale/AirOpsEnv-AgentEnv", "v2_tasks", split="eval")
day = days[0]
print(day["system_prompt_crew"])                     # what the crew desk is told
advisories = json.loads(day["advisories"])           # the FAA's advisories, each at its signature
runs = load_dataset("earakely-scale/AirOpsEnv-AgentEnv", "v2_runs", split="eval").filter(lambda r: r["on_board"])
desks = load_dataset("earakely-scale/AirOpsEnv-AgentEnv", "v2_desks", split="eval")
desks[0]["messages"]                                 # a desk's chat transcript, tool-call arguments as objects
```

## Run it yourself

You need Docker, [uv](https://docs.astral.sh/uv/) and git. Install the plugin, build the env and the desk agent, check
the wiring with a day no model plays, then play a day straight from this dataset with a model on your LiteLLM endpoint
(or the Hugging Face router):

```bash
uv tool install agentenv-framework \
    --with "agentenv-airops @ git+https://github.com/earakely-scale/agentenv-airops-plugin@v0.3.0"
agent-env airops-occ setup
agent-env hf run earakely-scale/AirOpsEnv-AgentEnv@v0.3.0 --bundle airops-occ --task wiring-noplay
agent-env hf run earakely-scale/AirOpsEnv-AgentEnv@v0.3.0 --bundle airops-occ --task b6-20240713-0400-joint \
    --model openai/gpt-6.1-sol
```

The bundle `airops-occ` (`bundles/airops-occ/`) holds every date of the pack in both modes, `<task id>-joint` and
`<task id>-one`, the very task files the board ran (each desk capped at $5 in a two-desk run, $8 for one agent), and
`wiring-noplay`, which runs the first eval date with no desk. This dataset's default bundle is v1's `airops`: with no
`--bundle`, `agent-env hf run earakely-scale/AirOpsEnv-AgentEnv@v0.3.0 --task b6-bos-20250216-1500 --model <model>`
plays a v1 day, once `agent-env airops setup --agent` has built its images. `hf run` downloads this card and
`bundles/` at the tag's commit, checks that the plugin the card's `agentenv` table names is installed, shows what it
will run and asks first.

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

[![GPT-6.1 Sol's run of JetBlue's Boston operations desk through the storm of 16 February 2025, replayed in 3D at Logan](https://raw.githubusercontent.com/earakely-scale/agentenv-airops-plugin/v0.3.0/assets/airops-hero.webp)](https://earakely-scale-airopsenv-agentenv.static.hf.space/viewer/index.html?episode=https%3A%2F%2Fhuggingface.co%2Fdatasets%2Fearakely-scale%2FAirOpsEnv-AgentEnv%2Fresolve%2Fv0.3.0%2Fviewer%2Fepisodes%2Fsol-v1.openai-gpt-6.1-sol.b6-bos-20250216-1500.json)

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
[NOTICE](https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.3.0/NOTICE) and each source's `SOURCE.md`
set them out.

- **U.S. Government works, public domain**: the BTS on-time, T-100 and Form 41 data, the FAA registry and the FAA
  Command Center advisories (17 U.S.C. § 105), and the USGS The National Map orthoimagery under the airport stages in
  `viewer/stage/`.
- **The Iowa Environmental Mesonet** (Iowa State University) archives the METARs, TAFs, forecast discussions and the
  NEXRAD n0q composites in `viewer/radar/`: NWS products, public domain; the IEM asks for attribution, given here.
- **airportsdata** (MIT licence, Mike Borsetti and contributors): the airports' names, coordinates and time zones.
- **v2's crew rules and costs**: 14 CFR parts 117 and 121 (eCFR) and the Federal Register, the FAA's economic values
  (Table 4-7), BLS wages, benefits and CPI-U, and GSA and DoD per diem rates, all U.S. Government works; crew bases,
  fleet configurations and the deadhead fare are facts cited from JetBlue's 10-K filings and press releases, ALPA and
  Airline Pilot Central, each with its URL in the plugin's `src/agentenv_airops/programs/crew_params.json`.
- **The airport stages in `viewer/stage/` and the hub twins in `viewer/twin/`**: © OpenStreetMap contributors, under
  the Open Database License (ODbL 1.0).
- **Apache-2.0**: the verifier script in `bundles/` and the plugin itself, whose 3D viewer adapts scene code from
  PortSimEnv v1 (FineEnvs, Apache-2.0).
- **Not in this dataset**: the replay Space's US map loads Natural Earth relief (public domain) and OpenMapTiles vector
  tiles (© OpenStreetMap contributors, ODbL 1.0) from OpenFreeMap as it runs, and coarser USGS imagery around the
  airport stages from The National Map; the control-center room's video wall loads us-atlas (US Census Bureau
  boundaries) and world-atlas (Natural Earth) through topojson-client, all ISC.

`scripts/hub_dataset.py` in the plugin builds this dataset with agentenv-hf, after its scan of every file for keys and
token shapes. This dataset is independent: neither JetBlue, the FAA, the BTS, the NWS, the USGS, the IEM nor any
union is affiliated with it or endorses it.
