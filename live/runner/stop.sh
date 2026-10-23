#!/usr/bin/env bash
# stop.sh ID [now|cancel]: the runner on box ID finishes the day in progress and exits (stop.flag); `now` stops the day
# at once (agent-env tears it down; the attempt is recorded and counts as one of the day's two); `cancel` takes
# stop.flag back.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: stop.sh ID [now|cancel]}
ssm "$ID" 60 "$H/box/stop.sh" "${2:-}"
