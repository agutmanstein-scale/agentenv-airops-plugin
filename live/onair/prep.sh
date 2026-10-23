#!/usr/bin/env bash
# prep.sh ID: box/docker-pools.sh on box ID, before the runner's setup.sh: the Deep Learning AMI ships Docker, so
# setup.sh leaves its daemon.json alone; this adds the address pools the env's networks need, keeping the NVIDIA runtime.
source "$(dirname "$0")/../aws/common.sh"
H="$(cd "$(dirname "$0")" && pwd)"
ID=${1:?usage: prep.sh ID}
wait_ssm "$ID"
ssm "$ID" 300 "$H/box/docker-pools.sh"
