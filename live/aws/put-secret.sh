#!/usr/bin/env bash
# put-secret.sh litellm-key|x-server|x-key: reads the value without echoing it and stores it as a SecureString at the
# SSM parameter live.env names for it (LIVE_LITELLM_KEY_PARAM, LIVE_X_SERVER_PARAM, LIVE_X_KEY_PARAM). The value never
# reaches the terminal, the shell history or a file.
source "$(dirname "$0")/common.sh"
case ${1:-} in
  litellm-key) NAME=${LIVE_LITELLM_KEY_PARAM:-/airops-live/litellm-key} ;;
  x-server) NAME=${LIVE_X_SERVER_PARAM:-/airops-live/x-server} ;;
  x-key) NAME=${LIVE_X_KEY_PARAM:-/airops-live/x-key} ;;
  *) die "usage: put-secret.sh litellm-key|x-server|x-key" ;;
esac
read -rsp "value for $NAME: " v; echo >&2
[ -n "$v" ] || die "empty value; nothing stored"
aws ssm put-parameter --name "$NAME" --type SecureString --overwrite --value "$v" --query Version --output text >/dev/null
unset v
log "stored $NAME ($(aws ssm get-parameter --name "$NAME" --with-decryption --query 'length(Parameter.Value)' --output text) chars)"
