#!/usr/bin/env bash
# ssm.sh ID TIMEOUT_S SCRIPT [ARGS...]: runs a box script (one under a box/ folder) as root on box ID through SSM and
# prints its output, e.g. ssm.sh ID 300 live/onair/box/loudness.sh 16:00 17:00
source "$(dirname "$0")/common.sh"
[ $# -ge 3 ] || die "usage: ssm.sh ID TIMEOUT_S SCRIPT [ARGS...]"
ssm "$@"
