#!/usr/bin/env bash
# launch-gpu.sh NAME [MAX_HOURS] [ROOT_GB]: the on-air box, a GPU box on the AWS Deep Learning Base OSS NVIDIA driver
# AMI (Ubuntu 22.04). GPU capacity moves between zones: it tries each type of LIVE_GPU_TYPES in each subnet of
# LIVE_SUBNETS (default LIVE_SUBNET) until one comes up in SSM, and prints its instance id. Defaults: 48 h, 250 GB.
source "$(dirname "$0")/common.sh"
NAME=${1:?usage: launch-gpu.sh NAME [MAX_HOURS] [ROOT_GB]}
MAX_HOURS=${2:-48}; ROOT_GB=${3:-250}
TYPES=${LIVE_GPU_TYPES:-g6.4xlarge gr6.4xlarge g6.8xlarge}; SUBNETS=${LIVE_SUBNETS:-${LIVE_SUBNET:-}}
[ -n "$SUBNETS" ] || die "set LIVE_SUBNET (or LIVE_SUBNETS) in $LIVE_ENV"
AMI=$(aws ssm get-parameter --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
  --query Parameter.Value --output text)
export AMI
for t in $TYPES; do
  for s in $SUBNETS; do
    out=$(SUBNET=$s bash "$HERE/launch.sh" "$t" "$NAME" "$MAX_HOURS" "$ROOT_GB" 2>&1 || true)
    log "$t $s: $(echo "$out" | tail -1 | cut -c1-150)"
    if echo "$out" | grep -q 'online in SSM'; then echo "$out" | tail -1; exit 0; fi
  done
done
die "no $NAME came up: no capacity for $TYPES in $SUBNETS"
