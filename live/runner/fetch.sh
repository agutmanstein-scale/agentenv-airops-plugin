#!/usr/bin/env bash
# fetch.sh ID [DEST]: copy the box's /home/ubuntu/live and the pilot folder (results.jsonl, days/, transcripts/, logs/,
# bundle/, pilot.json) to DEST (default live/runner/fetched/<UTC time>), through S3.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: fetch.sh ID [DEST]}; DEST=${2:-$H/fetched/$(date -u +%Y%m%dT%H%M%SZ)}
need LIVE_BUCKET
key="$PREFIX/out-$(date -u +%Y%m%dT%H%M%SZ).tar.zst"
ssm "$ID" 1800 "$H/box/pack.sh" "$(presign put "$key" 3600)"
tmp=$(mktemp -d); aws s3 cp --only-show-errors "s3://$BUCKET/$key" "$tmp/o.tar.zst"
mkdir -p "$DEST"; zstd -dc "$tmp/o.tar.zst" | tar -xf - -C "$DEST"; rm -rf "$tmp"; log "fetched into $DEST"
