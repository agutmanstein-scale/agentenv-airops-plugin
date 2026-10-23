#!/usr/bin/env bash
# sync.sh ID [REPO_DIR] [BRANCH]: checks the box's plugin out at BRANCH's head (default: the checked-out branch of the
# checkout this script is in) without re-running setup: web/ changes are served at once; restart airops-live-server for
# server changes and the runner (between days) for runner changes. The env and desk images stay as registered (re-run
# the runner's setup.sh for those).
set -euo pipefail
O="$(cd "$(dirname "$0")" && pwd)"
source "$O/../aws/common.sh"
ID=${1:?usage: sync.sh ID [REPO_DIR] [BRANCH]}; DIR=${2:-$(git -C "$O" rev-parse --show-toplevel)}
BRANCH=${3:-$(git -C "$DIR" rev-parse --abbrev-ref HEAD)}
need LIVE_BUCKET
tmp=$(mktemp -d); key="$PREFIX/onair/sync-$(date -u +%Y%m%dT%H%M%SZ).bundle"
git -C "$DIR" bundle create "$tmp/live.bundle" "$BASE_REF..$BRANCH" 2>/dev/null
aws s3 cp --only-show-errors "$tmp/live.bundle" "s3://$BUCKET/$key"
ssm "$ID" 300 "$O/box/sync.sh" "$(presign get "$key" 3600)" "$BRANCH"
