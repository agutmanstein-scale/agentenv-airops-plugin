#!/usr/bin/env bash
# fresh.sh ID: installs box/fresh.py on box ID as airops-fresh (enabled, restarted on exit): each minute the stream's
# picture against the page, logged to /var/log/airops-stream/fresh.jsonl; three stale minutes in a row restart the
# encoder. Start the stream first.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: fresh.sh ID}
ssm "$ID" 300 "$H/box/fresh-install.sh" "$(base64 < "$H/box/fresh.py" | tr -d '\n')"
