# Sourced by the other scripts: the settings (live/live.env, or the file LIVE_ENV names), the AWS profile and region,
# and the S3 and SSM helpers.
# shellcheck disable=SC2034
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIVE_ENV="${LIVE_ENV:-$(cd "$HERE/.." && pwd)/live.env}"
[ -f "$LIVE_ENV" ] || { echo "ERROR: no $LIVE_ENV: copy live/live.env.example there and fill it in" >&2; exit 1; }
# shellcheck source=/dev/null
source "$LIVE_ENV"

# Forced, not defaulted: a shell's own AWS_PROFILE (e.g. a production one) must never be used here.
if [ -n "${LIVE_AWS_PROFILE:-}" ]; then
  export AWS_PROFILE="$LIVE_AWS_PROFILE"
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
fi
export AWS_REGION="${LIVE_AWS_REGION:-us-west-2}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
if [ -n "${LIVE_AWS_ACCOUNT:-}" ]; then
  [ "$(aws sts get-caller-identity --query Account --output text)" = "$LIVE_AWS_ACCOUNT" ] \
    || { echo "ERROR: these credentials are not account $LIVE_AWS_ACCOUNT" >&2; exit 1; }
fi

REPO_URL="${LIVE_REPO_URL:-https://github.com/earakely-scale/agentenv-airops-plugin.git}"
BASE_REF="${LIVE_BASE_REF:-v0.5.0}"
BUCKET="${LIVE_BUCKET:-}"
PREFIX="${LIVE_S3_PREFIX:-airops-live}"
SUBNET="${SUBNET:-${LIVE_SUBNET:-}}"
SG="${LIVE_SECURITY_GROUP:-}"
PROFILE_NAME="${LIVE_INSTANCE_PROFILE:-}"
TAG_OWNER="${LIVE_TAG_OWNER:-${USER:-airops}}"
TAG_PURPOSE="${LIVE_TAG_PURPOSE:-airops-live}"

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }
die() { echo "ERROR: $*" >&2; exit 1; }
need() { local v; for v in "$@"; do [ -n "${!v:-}" ] || die "set $v in $LIVE_ENV"; done; }

# presign get|put KEY SECONDS: a presigned URL for s3://$BUCKET/KEY (aws s3 presign only does GET).
presign() {
  local py=(python3)
  python3 -c 'import boto3' 2>/dev/null || py=(uv run --quiet --no-project --with boto3 python)
  "${py[@]}" - "$1" "$BUCKET" "$2" "$3" "$AWS_REGION" <<'PY'
import sys, boto3
from botocore.config import Config
op, bucket, key, ttl, region = sys.argv[1:6]
s3 = boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
                  config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
print(s3.generate_presigned_url({"get": "get_object", "put": "put_object"}[op],
                                Params={"Bucket": bucket, "Key": key}, ExpiresIn=int(ttl)))
PY
}

wait_ssm() {
  local id=$1 i
  for i in $(seq 1 90); do
    [ "$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$id" \
         --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)" = Online ] && return 0
    sleep 10
  done
  die "$id never came online in SSM"
}

# ssm INSTANCE TIMEOUT_S SCRIPT [ARGS...]: run SCRIPT with ARGS under bash as root on the box; prints its stdout and
# stderr (SSM keeps ~24 KB of stdout and ~8 KB of stderr) and returns its exit code. The script and its arguments
# travel base64-encoded in the command, so presigned URLs passed as arguments sit in SSM's command history.
ssm() {
  local id=$1 timeout=$2 file=$3; shift 3
  local wrapped b64 params cid status=Pending out
  wrapped="$(printf 'set --'; printf ' %q' "$@"; printf '\n'; cat "$file")"
  b64=$(printf '%s\n' "$wrapped" | base64 | tr -d '\n')
  params=$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]], "executionTimeout": [sys.argv[2]]}))' \
    "f=\$(mktemp); echo $b64 | base64 -d > \$f; bash \$f" "$timeout")
  cid=$(aws ssm send-command --instance-ids "$id" --document-name AWS-RunShellScript --timeout-seconds 600 \
        --comment "airops $(basename "$file")" --parameters "$params" --query Command.CommandId --output text)
  while :; do
    sleep 5
    status=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$id" --query Status --output text \
             2>/dev/null || echo Pending)
    case $status in Pending|InProgress|Delayed|Cancelling) ;; *) break ;; esac
  done
  out=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$id" --output json)
  python3 -c '
import json, sys
r = json.loads(sys.stdin.read())
sys.stdout.write(r.get("StandardOutputContent", ""))
err = r.get("StandardErrorContent", "")
if err.strip():
    sys.stderr.write("--- stderr ---\n" + err)
' <<<"$out"
  [ "$status" = Success ] || { log "SSM command $cid ended $status"; return 1; }
}
