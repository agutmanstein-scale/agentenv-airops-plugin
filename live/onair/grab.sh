#!/usr/bin/env bash
# grab.sh ID DEST_DIR HH:MM:SS[=name]...: frames from the box's recording at those UTC times (today, or a full
# YYYY-MM-DDTHH:MM:SS), into DEST_DIR.
set -euo pipefail
O="$(cd "$(dirname "$0")" && pwd)"
source "$O/../aws/common.sh"
[ $# -ge 3 ] || die "usage: grab.sh ID DEST_DIR HH:MM:SS[=name]..."
ID=$1; DEST=$2; shift 2
need LIVE_BUCKET
key="$PREFIX/onair/grab-$(date -u +%H%M%S).tar"
ssm "$ID" 600 "$O/box/grab.sh" "$(presign put "$key" 3600)" "$@"
mkdir -p "$DEST"; aws s3 cp --only-show-errors "s3://$BUCKET/$key" - | tar -xf - -C "$DEST"
