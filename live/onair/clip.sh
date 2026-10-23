#!/usr/bin/env bash
# clip.sh ID OUT.mp4 HH:MM:SS=SECONDS...: a clip of those pieces of the box's recording (UTC today, or a full
# YYYY-MM-DDTHH:MM:SS), to OUT.mp4.
set -euo pipefail
O="$(cd "$(dirname "$0")" && pwd)"
source "$O/../aws/common.sh"
[ $# -ge 3 ] || die "usage: clip.sh ID OUT.mp4 HH:MM:SS=SECONDS..."
ID=$1; OUT=$2; shift 2
need LIVE_BUCKET
key="$PREFIX/onair/clip-$(date -u +%H%M%S).mp4"
ssm "$ID" 1800 "$O/box/clip.sh" "$(presign put "$key" 3600)" "$@"
aws s3 cp --only-show-errors "s3://$BUCKET/$key" "$OUT"; ls -la "$OUT"
