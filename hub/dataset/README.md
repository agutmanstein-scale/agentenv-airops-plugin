---
license: other
license_name: us-public-domain-iem-mit-odbl-apache-2.0
license_link: https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.2.0/NOTICE
pretty_name: AirOpsEnv v1
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
- airline-operations
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

<a href="https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv"><img src="https://raw.githubusercontent.com/earakely-scale/agentenv-airops-plugin/v0.2.0/assets/airops-banner.jpg" alt="AirOpsEnv v1: Boston Logan in 3D on the storm day of 16 February 2025, replayed from GPT-6.1 Sol's run of JetBlue's operations desk" width="100%"></a>

<h1>AirOpsEnv v1</h1>

<h3>Recover an airline's storm day at JFK or Boston</h3>

<p>An AgentEnv environment built from public U.S. flight, FAA and weather records, graded with no judge against the plan a MIP proves optimal in hindsight.</p>

<a href="https://huggingface.co/spaces/earakely-scale/AirOpsEnv-AgentEnv"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Space-Replays%20in%203D-FFD21E?style=for-the-badge&labelColor=1a1a1a" alt="Replays in 3D" height="32"></a>
<a href="https://github.com/earakely-scale/agentenv-airops-plugin"><img src="https://img.shields.io/badge/GitHub-agentenv--airops--plugin-181717?style=for-the-badge&logo=github&labelColor=1a1a1a" alt="Code on GitHub" height="32"></a>
<a href="https://www.agentenvframework.com"><img src="https://img.shields.io/badge/framework-AgentEnv-6f42c1?style=for-the-badge&labelColor=1a1a1a" alt="AgentEnv Framework" height="32"></a>

</div>

---

## The task

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

## The data

Each task is one hub window on a real day of the pack `b6-v1` (JetBlue, JFK and Boston), built from 13 months of 2022
to 2025: 20 eval days, 3 control days (no cancellation in scope) and 34 training days, with 59 to 194 flights on 30 to
59 aircraft and 10 to 22 watches. Each day's window is the 3 to 6 hours where planning matters most, where weather and
air traffic control caused at least 80% of the cancellations; an eval day also needs doing nothing to cost at least
10% over the hindsight optimum and the re-planning follower to land within 15% of it.

Everything is built from public U.S. records: the flights, tails and delays from BTS on-time data, passengers from
T-100, aircraft from the FAA registry and Form 41, the advisories from the FAA Command Center and the weather from the
NWS via the Iowa Environmental Mesonet.

## Grading

The final plan is flown on the day as it happened. Its cost, in passenger-minutes, is every passenger's arrival
delay, plus 523 per passenger on a cancelled flight (NEXTOR's average re-accommodation delay plus refund exposure at
the FAA's value of time), plus the end-of-day ferries. The reward is 0.2 + 0.8 exp(−g/0.5), with g the cost over the
hindsight optimum in units of that optimum's avoidable cost (hindsight − floor + 2% of hindsight): 1.0 at the
hindsight optimum and never below 0.2, since the env never strands a flight. The references, each run on the truth:

- **hindsight** (the anchor): the time-line MIP (HiGHS, 1-minute arcs) on the day as it happened, proven to 0.01%;
- **floor**: the same without aircraft routing, a lower bound;
- **follower**: the MIP re-solved at every watch on the day as known (persistence forecast), freezing as the env does;
- **blind**: the follower with no forecast; **naive**: no plan; **airline**: JetBlue's own cancellations and times.

## Results

<!-- board -->

<!-- /board -->

## Watch a run

[![GPT-6.1 Sol's run of JetBlue's Boston operations desk through the storm of 16 February 2025, replayed in 3D at Logan](https://raw.githubusercontent.com/earakely-scale/agentenv-airops-plugin/v0.2.0/assets/airops-hero.webp)](https://earakely-scale-airopsenv-agentenv.static.hf.space/viewer/index.html?episode=https%3A%2F%2Fhuggingface.co%2Fdatasets%2Fearakely-scale%2FAirOpsEnv-AgentEnv%2Fresolve%2Fv0.2.0%2Fviewer%2Fepisodes%2Fsol-v1.openai-gpt-6.1-sol.b6-bos-20250216-1500.json)

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

## Run it yourself

You need Docker, [uv](https://docs.astral.sh/uv/) and git. Install the plugin, build the env and agent images, and
check the wiring with a task that calls no model:

```bash
uv tool install agentenv-framework \
    --with "agentenv-airops @ git+https://github.com/earakely-scale/agentenv-airops-plugin@v0.2.0"
agent-env airops setup --agent
agent-env run airops --task wiring-noplay
```

Then play a day straight from this dataset with a model on your LiteLLM endpoint (or the Hugging Face router):

```bash
agent-env hf run earakely-scale/AirOpsEnv-AgentEnv@v0.2.0 --task b6-bos-20250216-1500 \
    --model anthropic/claude-sonnet-5-5
```

`hf run` downloads this card and `bundles/` at the tag's commit, checks that the plugin the card's `agentenv` table
names is installed, shows what it will run and asks first. To play, replay and publish your own runs:

```bash
agent-env airops sweep run --name mine --models anthropic/claude-sonnet-5-5 --tasks eval \
    --episode-cap-usd 2 --cap-usd 20
agent-env airops sweep report mine          # results/airops.md
agent-env airops view mine                  # http://127.0.0.1:8238/web/
```

## Tables

All parquet. AirOps's own, one config per table, a split per split of the pack (`eval`, `control`, `train`):

| Config | What |
|---|---|
| `v1_tasks` (default) | one row per day: what the planner is told at the first watch, each watch's news, the reference costs, and the task line itself (`task`) |
| `results` | the model board: one row per model, its mean reward over the days with a 95% bootstrap CI, the references' means on the same days, tokens, cost and time |
| `v1_references` | one row per day with a reference: every reference's cost and reward, the clauses the day qualified on, the hindsight and follower plans |
| `v1_episodes` | one row per scored run: the grade, the final plan, every tool call with its result, and the chat transcript as `messages` |

And agentenv-hf's for the bundle `airops`, exactly as `agent-env hf publish` writes them: `airops_tasks` (the eval
days as AgentEnv tasks, with their steps) and `airops_episodes` (each run's status, model, reward, the verifier's full
output and the transcript); `raw/airops.jsonl` holds each run's record and native trajectory, and `bundles/airops/` the
bundle `agent-env hf run` plays. `episode_id` joins `v1_episodes` to `airops_episodes` and to `raw/airops.jsonl`.

`results/eval-v1.json` is the eval card and `runs/` the sweeps as they were recorded. `viewer/` holds the episodes as
the replay Space reads them: each episode's JSON and its traffic under `episodes/`, the airport stages under
`stage/<HUB>/`, the NEXRAD radar frames under `radar/`, the hub twins under `twin/` and the films' stills under
`thumbs/`.

```python
import json

from datasets import load_dataset

days = load_dataset("earakely-scale/AirOpsEnv-AgentEnv", "v1_tasks", split="eval")
day = days[0]
print(day["system_prompt"], day["situation"])        # what the planner gets at the first watch
watches = json.loads(day["watches"])                 # what arrives later, watch by watch
runs = load_dataset("earakely-scale/AirOpsEnv-AgentEnv", "v1_episodes", split="eval")
runs[0]["messages"]                                  # a list of chat messages, tool-call arguments as objects
```

## Simplifications

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
[NOTICE](https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.2.0/NOTICE) and each source's `SOURCE.md`
set them out.

- **U.S. Government works, public domain**: the BTS on-time, T-100 and Form 41 data, the FAA registry and the FAA
  Command Center advisories (17 U.S.C. § 105), and the USGS The National Map orthoimagery under the airport stages in
  `viewer/stage/`.
- **The Iowa Environmental Mesonet** (Iowa State University) archives the METARs, TAFs, forecast discussions and the
  NEXRAD n0q composites in `viewer/radar/`: NWS products, public domain; the IEM asks for attribution, given here.
- **airportsdata** (MIT licence, Mike Borsetti and contributors): the airports' names, coordinates and time zones.
- **The airport stages in `viewer/stage/` and the hub twins in `viewer/twin/`**: © OpenStreetMap contributors, under
  the Open Database License (ODbL 1.0).
- **Apache-2.0**: the verifier script in `bundles/` and the plugin itself, whose 3D viewer adapts scene code from
  PortSimEnv v1 (FineEnvs, Apache-2.0).
- **Not in this dataset**: the replay Space's US map loads Natural Earth relief (public domain) and OpenMapTiles vector
  tiles (© OpenStreetMap contributors, ODbL 1.0) from OpenFreeMap as it runs, and coarser USGS imagery around the
  airport stages from The National Map.

`scripts/hub_dataset.py` in the plugin builds this dataset with agentenv-hf, after its scan of every file for keys and
token shapes. This dataset is independent: neither JetBlue, the FAA, the BTS, the NWS, the USGS nor the IEM is
affiliated with it or endorses it.
