#!/usr/bin/env bash
# status.sh ID [MINUTES]: box/status.sh on box ID: the units, the relay, the last metrics of capture and push, restarts
# and warnings in the last MINUTES (default 30), GPU, memory, disk and a scheduled power-off.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: status.sh ID [MINUTES]}
ssm "$ID" 60 "$H/box/status.sh" "${2:-30}"
