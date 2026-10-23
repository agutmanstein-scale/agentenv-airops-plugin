#!/usr/bin/env bash
# status.sh ID [N]: the unit, current.json in a few lines (day, instance, env, spend, next, the last days), the results,
# the box's load and power-off time, and the last N lines of runner.log.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: status.sh ID [N]}
ssm "$ID" 60 "$H/box/status.sh" "${2:-15}"
