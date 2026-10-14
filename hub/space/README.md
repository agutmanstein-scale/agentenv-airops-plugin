---
title: AirOpsEnv v1
emoji: ✈️
colorFrom: blue
colorTo: gray
sdk: static
app_file: index.html
pinned: true
license: other
license_name: us-public-domain-iem-mit-odbl-apache-2.0
license_link: https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.2.0/NOTICE
short_description: Recover an airline's storm day at JFK or Boston
tags:
- agentenv
- rl-environment
- agents
- airline-operations
- disruption-recovery
- scheduling
- weather
datasets:
- earakely-scale/AirOpsEnv-AgentEnv
---

# AirOpsEnv v1

An agent runs the JetBlue operations desk at JFK or Boston on a real storm day, watch by watch on the
[AgentEnv Framework](https://www.agentenvframework.com)'s virtual clock: it cancels and holds flights as the FAA's
ground stops and ground delay programs, the TAFs and the METARs arrive. This Space replays the recorded runs of
[earakely-scale/AirOpsEnv-AgentEnv](https://huggingface.co/datasets/earakely-scale/AirOpsEnv-AgentEnv) at the
airport in 3D: every airline's flights on their real times under the weather the METARs report, the task's JetBlue
aircraft on the agent's plan with tags, a US map of the radar and the aircraft in the air in a corner (click it to
swap), the departures board, the desk telling each watch in plain English, and an expert mode with the advisories,
caps and tool calls as the agent saw them. The day is graded on what really happened against the hindsight optimum.
The stands, taxi routes and runway in use are the viewer's inferences, for display only.

It is a static page: the viewer, under `viewer/`, reads the episodes straight from the dataset's files
(`viewer/episodes/index.json` and the episodes it lists, with their traffic, the airport stages, radar frames, hub
twins and stills). Open one episode with `?episode=<url of an episode JSON>`, or another list with `?index=<url>`.

Airport © OpenStreetMap contributors (ODbL) · imagery: USGS The National Map · map: Natural Earth, © OpenMapTiles ©
OpenStreetMap contributors via OpenFreeMap · radar: NEXRAD n0q composite, Iowa Environmental Mesonet · flights: BTS
on-time data · aircraft: FAA registry · advisories: FAA ATCSCC · TAFs, METARs: NWS via the IEM. The environment and the
viewer:
[earakely-scale/agentenv-airops-plugin](https://github.com/earakely-scale/agentenv-airops-plugin) (Apache-2.0; its 3D
scene code adapts PortSimEnv v1 from FineEnvs, Apache-2.0).
