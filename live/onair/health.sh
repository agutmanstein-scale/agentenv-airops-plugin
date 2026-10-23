#!/usr/bin/env bash
# health.sh ID: box/health.sh on box ID, one JSON line: the stream's picture against the page, the runner, the live
# server, capture and push.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: health.sh ID}
ssm "$ID" 120 "$H/box/health.sh"
