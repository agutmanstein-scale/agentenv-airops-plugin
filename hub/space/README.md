---
title: AirOpsEnv on AgentEnv
emoji: ✈️
colorFrom: blue
colorTo: gray
sdk: static
app_file: index.html
pinned: true
license: other
license_name: us-public-domain-iem-mit-odbl-apache-2.0
license_link: https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.1.0/NOTICE
short_description: JetBlue storm days on AgentEnv, replayed by watch
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

# AirOpsEnv on AgentEnv

An agent runs the JetBlue operations desk at JFK or Boston on a real storm day, watch by watch on the
[AgentEnv Framework](https://www.agentenvframework.com)'s virtual clock: it cancels and holds flights as the FAA's
ground stops and ground delay programs, the TAFs and the METARs arrive. This Space replays the recorded runs of
[earakely-scale/AirOpsEnv-AgentEnv](https://huggingface.co/datasets/earakely-scale/AirOpsEnv-AgentEnv): the network
and the hub on the clock, the NEXRAD radar, each watch's news, caps and changes, the planner's tool calls, and the
day as it was flown on the truth, graded against the hindsight optimum.

It is a static page: the viewer, under `viewer/`, reads the episodes straight from the dataset's files
(`viewer/episodes/index.json` and the episodes it lists, with their radar frames and hub twins). Open one episode
with `?episode=<url of an episode JSON>`, or another list with `?index=<url>`.

Basemap © OpenMapTiles © OpenStreetMap contributors, via OpenFreeMap · hub twin © OpenStreetMap contributors (ODbL) ·
radar: NEXRAD n0q composite, Iowa Environmental Mesonet · flights: BTS on-time data · advisories: FAA ATCSCC · TAFs,
METARs: NWS via the IEM. The environment and the viewer:
[earakely-scale/agentenv-airops-plugin](https://github.com/earakely-scale/agentenv-airops-plugin) (Apache-2.0).
