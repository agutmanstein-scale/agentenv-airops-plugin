#!/usr/bin/env bash
# setup.sh ID [REPO_DIR] [BRANCH]: make box ID a run box. Ships BRANCH (default: REPO_DIR's checked-out branch; REPO_DIR
# defaults to the checkout this script is in) as a git bundle over LIVE_BASE_REF through S3, then on the box: Docker,
# compose v2, the inotify sysctl, aws-cli, a registry:2 on :5000, the plugin at BRANCH in a py3.12 venv with LIVE_PINS
# (the framework pinned), `agent-env airops-occ3 setup`, with-model.sh (LIVE_LITELLM_BASE_URL, the key read from the
# SSM parameter LIVE_LITELLM_KEY_PARAM at every start) and no-model.sh (the scripted desks), and a dry run of the
# schedule. Safe to run again.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: setup.sh ID [REPO_DIR] [BRANCH]}; DIR=${2:-$(git -C "$H" rev-parse --show-toplevel)}
BRANCH=${3:-$(git -C "$DIR" rev-parse --abbrev-ref HEAD)}
need LIVE_BUCKET
PINS=${LIVE_PINS:-agentenv-framework==0.9.1339 agentenv-framework-protocol==0.1.354 highspy==1.15.1 numpy==2.5.4 pandas==3.0.6 pyarrow==26.0.0 shapely==2.2.0 airportsdata==20260905}
tmp=$(mktemp -d); key="$PREFIX/runner-$(date -u +%Y%m%dT%H%M%SZ).bundle"
git -C "$DIR" bundle create "$tmp/live.bundle" "$BASE_REF..$BRANCH" 2>/dev/null
log "bundle $BRANCH at $(git -C "$DIR" rev-parse --short "$BRANCH"): $(du -h "$tmp/live.bundle" | cut -f1)"
code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT --upload-file "$tmp/live.bundle" "$(presign put "$key" 3600)")
[ "$code" = 200 ] || die "bundle upload: http $code"
wait_ssm "$ID"
ssm "$ID" 3000 "$H/box/setup.sh" "$REPO_URL" "$(presign get "$key" 3600)" "$BRANCH" "$PINS" 3.12.12 \
  "${LIVE_LITELLM_BASE_URL:-}" "${LIVE_LITELLM_KEY_PARAM:-/airops-live/litellm-key}" "$AWS_REGION"
