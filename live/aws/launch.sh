#!/usr/bin/env bash
# launch.sh TYPE NAME [MAX_HOURS] [ROOT_GB]
# Launches (or finds, when one named NAME is already pending or running) an Ubuntu 24.04 box (AMI= another image),
# waits until SSM sees it and prints its instance id on stdout. The box powers itself off, and so terminates, MAX_HOURS
# (default 12) after boot.
source "$(dirname "$0")/common.sh"
need LIVE_SECURITY_GROUP LIVE_INSTANCE_PROFILE
[ -n "$SUBNET" ] || die "set LIVE_SUBNET in $LIVE_ENV"
TYPE=${1:?usage: launch.sh TYPE NAME [MAX_HOURS] [ROOT_GB]}
NAME=${2:?usage: launch.sh TYPE NAME [MAX_HOURS] [ROOT_GB]}
MAX_HOURS=${3:-12}
ROOT_GB=${4:-100}

id=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$NAME" "Name=tag:purpose,Values=$TAG_PURPOSE" \
            "Name=instance-state-name,Values=pending,running" \
  --query 'Reservations[].Instances[0].InstanceId' --output text)
if [ -n "$id" ] && [ "$id" != None ]; then
  log "$NAME is already up as $id"
else
  arch=amd64
  [[ $TYPE =~ ^[a-z]+[0-9]+[a-z]*g[a-z]*\. ]] && arch=arm64
  ami=${AMI:-$(aws ssm get-parameter --name "/aws/service/canonical/ubuntu/server/24.04/stable/current/$arch/hvm/ebs-gp3/ami-id" \
        --query Parameter.Value --output text)}
  userdata=$(printf '#!/bin/bash\nshutdown -h +%d "airops: MAX_HOURS reached"\n' $((MAX_HOURS * 60)) | base64 | tr -d '\n')
  tags="{Key=Name,Value=$NAME},{Key=owner,Value=$TAG_OWNER},{Key=purpose,Value=$TAG_PURPOSE}"
  id=$(aws ec2 run-instances --image-id "$ami" --instance-type "$TYPE" --count 1 \
    --subnet-id "$SUBNET" --security-group-ids "$SG" --iam-instance-profile "Name=$PROFILE_NAME" \
    --instance-initiated-shutdown-behavior terminate \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$ROOT_GB,VolumeType=gp3,DeleteOnTermination=true}" \
    --user-data "$userdata" \
    --tag-specifications "ResourceType=instance,Tags=[$tags]" "ResourceType=volume,Tags=[$tags]" \
    --query 'Instances[0].InstanceId' --output text)
  log "launched $id ($TYPE, $arch, $ami, ${ROOT_GB} GB root, powers off after ${MAX_HOURS} h)"
fi
aws ec2 wait instance-running --instance-ids "$id"
log "$id running; waiting for SSM"
wait_ssm "$id"
log "$id online in SSM"
echo "$id"
