#!/usr/bin/env bash
# start.sh ID [--url URL] [--target none|ssm|rtmp(s)://...] [--size WxH] [--bitrate 6000k] [--safe-url URL]:
# box/start.sh on box ID: sets what is given in /etc/airops-stream.env, starts what is stopped and restarts what a
# change concerns.
# --target ssm streams to X with the server and key in SSM (LIVE_X_SERVER_PARAM, LIVE_X_KEY_PARAM); none only records.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: start.sh ID [--url URL] [--target T] [--size WxH] [--bitrate B] [--safe-url URL]}; shift
ssm "$ID" 120 "$H/box/start.sh" "$@"
