#!/usr/bin/env bash
# start.sh ID [runner args]: start the runner on box ID as the systemd unit airops-live (restarted on a crash, which
# picks the running day up; `systemctl stop` finishes the day first). Args go to `live_runner.py run`, e.g.
#   start.sh ID                                                  the stream: the config's days and budget
#   start.sh ID --set start_utc=2026-10-23T16:00:00Z            hold the first day until then
#   start.sh ID --policy script --name live-script-box --set slot_minutes=6 --days 3    a $0 rehearsal
#   start.sh ID --name occ3-live-rehearsal --days 1            one real Astra day (~$23) in its own pilot
# LIVE_MODEL, when set, comes first as --set model=…, so a later --set model= wins. current.json, stop.flag and
# runner.log are in /home/ubuntu/live/.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: start.sh ID [runner args]}; shift
ssm "$ID" 120 "$H/box/start.sh" ${LIVE_MODEL:+--set "model=$LIVE_MODEL"} "$@"
