#!/usr/bin/env bash
# deploy-server.sh ID: installs the live server on the on-air box as airops-live-server (enabled, Restart=always,
# 127.0.0.1:8765), with the prebuilt days, the viewer assets and cache/inputs (upload-data.sh puts them in
# s3://LIVE_BUCKET/LIVE_S3_PREFIX/onair/), and the CPU priority drop-ins for the stream units. Needs the runner's
# setup.sh done first. Safe to run again.
set -euo pipefail
O="$(cd "$(dirname "$0")" && pwd)"
source "$O/../aws/common.sh"
ID=${1:?usage: deploy-server.sh ID}
need LIVE_BUCKET
tmp=$(mktemp -d)
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$tmp/onair.tgz" -C "$O/box" units
aws s3 cp --only-show-errors "$tmp/onair.tgz" "s3://$BUCKET/$PREFIX/onair/onair.tgz"; rm -rf "$tmp"
ssm "$ID" 1800 "$O/box/server-setup.sh" "$(presign get "$PREFIX/onair/onair.tgz" 3600)" \
  "$(presign get "$PREFIX/onair/prebuilt.tar.zst" 3600)" "$(presign get "$PREFIX/onair/assets.tar.zst" 3600)" \
  "$(presign get "$PREFIX/onair/inputs.tar.zst" 3600)"
