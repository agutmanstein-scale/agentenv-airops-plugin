#!/usr/bin/env bash
# plan.sh ID [runner args]: the schedule `live_runner.py run --dry-run` projects on box ID with these args (the same as
# start.sh's, e.g. --set start_utc=… --set budget_usd=… --set slot_minutes=…): when each day starts and the spend
# before it, against a throwaway current.json. Plays nothing and spends nothing.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: plan.sh ID [runner args]}; shift
ssm "$ID" 300 "$H/box/plan.sh" ${LIVE_MODEL:+--set "model=$LIVE_MODEL"} "$@"
