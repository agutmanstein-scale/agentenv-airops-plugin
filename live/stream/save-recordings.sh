#!/usr/bin/env bash
# save-recordings.sh INSTANCE_ID [PATTERN]: copies the box's recordings (all, or the names matching PATTERN, e.g.
# 2026-10-23) to s3://LIVE_BUCKET/LIVE_S3_PREFIX/rec/ with a presigned PUT each (the box's role needs no S3 rights).
# Skips the segment being written and those already in S3 at the same size.
set -euo pipefail
STREAM="$(cd "$(dirname "$0")" && pwd)"
source "$STREAM/../aws/common.sh"
ID=${1:?usage: save-recordings.sh INSTANCE_ID [PATTERN]}
PAT=${2:-}
need LIVE_BUCKET
tmp=$(mktemp -d); LIST="$tmp/list.sh"; PUT="$tmp/put.sh"
printf 'cd /var/lib/airops-stream/rec/airops && ls -t | tail -n +2 | grep -F -- "%s" | while read f; do echo "$f $(stat -c %%s "$f")"; done\n' "$PAT" > "$LIST"
args=()
while read -r f size; do
  [ -n "$f" ] || continue
  have=$(aws s3api head-object --bucket "$BUCKET" --key "$PREFIX/rec/$f" --query ContentLength --output text 2>/dev/null || echo 0)
  [ "$have" = "$size" ] && { log "$f already saved"; continue; }
  args+=("$f" "$(presign put "$PREFIX/rec/$f" 21600)")
done < <(ssm "$ID" 120 "$LIST")
[ ${#args[@]} -gt 0 ] || { log "nothing to save"; exit 0; }
cat > "$PUT" <<'BOX'
cd /var/lib/airops-stream/rec/airops
while [ $# -gt 1 ]; do curl -fsS -X PUT -T "$1" "$2" && echo "saved $1"; shift 2; done
BOX
ssm "$ID" 7200 "$PUT" "${args[@]}"
aws s3 ls "s3://$BUCKET/$PREFIX/rec/"; rm -rf "$tmp"
