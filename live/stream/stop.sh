#!/usr/bin/env bash
# stop.sh ID [--push-only|--disable]: box/stop.sh on box ID: stops pushing to the target (--push-only), or the whole
# stream (a reboot starts it again; --disable keeps it stopped).
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: stop.sh ID [--push-only|--disable]}; shift
ssm "$ID" 60 "$H/box/stop.sh" "$@"
