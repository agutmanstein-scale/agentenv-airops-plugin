---
title: AirOpsEnv v3
emoji: ✈️
colorFrom: blue
colorTo: gray
sdk: static
app_file: index.html
pinned: true
license: other
license_name: us-public-domain-iem-mit-odbl-apache-2.0
license_link: https://github.com/earakely-scale/agentenv-airops-plugin/blob/v0.5.0/NOTICE
short_description: Run an airline's control center through a storm day
tags:
- agentenv
- rl-environment
- agents
- multi-agent
- airline-operations
- crew-scheduling
- passengers
- disruption-recovery
- weather
datasets:
- earakely-scale/AirOpsEnv-AgentEnv
---

# AirOpsEnv v3: passengers and the customer desk

A third desk joins the ops control center: the customer desk asks ops to hold departures for connecting passengers,
not to cancel flights whose passengers have nowhere else to go, or to cancel early. The airline rebooks whoever misses
a connection or loses a flight onto its next seats, today or on a later day, or refunds them. On a storm day it owes
no hotel (weather and air traffic control are force majeure under its contract): those it can't fly today find their
own room, and the env charges one GSA lodging night as the passenger's cost. A v3 run adds a Terminal view in the
gate hall: a gate's screen with its flight on time, delayed, boarding, held for its connecting passengers or
cancelled, the crowd at the glass and the aircraft at the bridge, and ‹ › steps from gate to gate and to the line at the
service counter. The room holds all three desks, the customer desk's requests piling at the desk they wait on, its own
screens and a PASSENGERS panel on the wall; the stranded leave on the hotel shuttle, and the US map draws where
rebooked passengers fly and how many are stranded overnight at each airport. The gate halls, the lounges, the counter,
the passengers' walks and the shuttle are inferred, for display. v3's runs head the page, with the one to watch first
under *Start here*; v2's runs follow them.

## AirOpsEnv v2: the ops control center

Two desks run JetBlue's operations control center through a real storm day, from 04:00, on the
[AgentEnv Framework](https://www.agentenvframework.com)'s virtual clock: an ops controller runs the flights (cancel,
hold, take FAA slots) and a crew scheduler runs the pilots and flight attendants (reserves, swaps, deadheads, hotels,
under FAR 117 and 14 CFR 121.467), and they ask each other for holds, cancellations and cover. This Space replays the
recorded runs of [earakely-scale/AirOpsEnv-AgentEnv](https://huggingface.co/datasets/earakely-scale/AirOpsEnv-AgentEnv)
in 3D: the hub airport with every airline's flights on their real times under the weather the METARs report, JetBlue's
aircraft with their crews' status at the gate and the crews walking to their gates, the control-center room with the
two desks at their pods and their requests travelling between them, the US map with the radar, the aircraft in the air
and where crews are resting, and an expert mode with each crew's duty clock, the full request log and the pages the
desks read. The day is graded on what really happened against the best plan found. AirOpsEnv v1's runs, one desk on a
storm window at JFK or Boston, follow below them.

The crew room, the crews' walks, the van and bus routes, the stands, taxi routes and runway in use, and the
control-center room itself are the viewer's inferences, for display only; the crews are pairings generated from the
published schedule, not JetBlue's.

It is a static page: the viewer, under `viewer/`, reads the episodes straight from the dataset's files
(`viewer/episodes/index.json` and the episodes it lists, with their traffic, the airport stages, radar frames, hub
twins and stills). Open one episode with `?episode=<url of an episode JSON>`, or another list with `?index=<url>`.

Airport © OpenStreetMap contributors (ODbL) · imagery: USGS The National Map · map: Natural Earth, US Census (us-atlas),
© OpenMapTiles © OpenStreetMap contributors via OpenFreeMap · radar: NEXRAD n0q composite, Iowa Environmental Mesonet ·
flights: BTS on-time data · aircraft: FAA registry · advisories: FAA ATCSCC · TAFs, METARs: NWS via the IEM · crew
rules: FAR 117, 14 CFR 121.467. The environment and the viewer:
[earakely-scale/agentenv-airops-plugin](https://github.com/earakely-scale/agentenv-airops-plugin) (Apache-2.0; its 3D
scene code adapts PortSimEnv v1 from FineEnvs, Apache-2.0).
