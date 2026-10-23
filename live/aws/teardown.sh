#!/usr/bin/env bash
# teardown.sh INSTANCE_ID NAME
# Terminates INSTANCE_ID only if its Name tag is NAME and its purpose tag is LIVE_TAG_PURPOSE's, waits until it is
# terminated and prints the state. An instance already terminated is reported, not an error.
source "$(dirname "$0")/common.sh"
ID=${1:?usage: teardown.sh INSTANCE_ID NAME}
NAME=${2:?usage: teardown.sh INSTANCE_ID NAME}
read -r state name purpose < <(aws ec2 describe-instances --instance-ids "$ID" --query \
  'Reservations[0].Instances[0].[State.Name, Tags[?Key==`Name`].Value|[0], Tags[?Key==`purpose`].Value|[0]]' \
  --output text)
[ "$name" = "$NAME" ] || die "$ID is named '$name', not '$NAME'; not terminating"
[ "$purpose" = "$TAG_PURPOSE" ] || die "$ID has purpose '$purpose', not '$TAG_PURPOSE'; not terminating"
if [ "$state" != terminated ]; then
  log "terminating $ID ($NAME, $state)"
  aws ec2 terminate-instances --instance-ids "$ID" --query 'TerminatingInstances[0].CurrentState.Name' --output text >&2
  aws ec2 wait instance-terminated --instance-ids "$ID"
fi
aws ec2 describe-instances --instance-ids "$ID" \
  --query 'Reservations[0].Instances[0].[InstanceId,InstanceType,State.Name,Tags[?Key==`Name`].Value|[0]]' --output text
