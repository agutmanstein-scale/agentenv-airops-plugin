#!/usr/bin/env bash
# deploy.sh INSTANCE_ID [--test-bundle TGZ]: uploads box/ (with the viewer's web/audio/apron.ogg as bed.ogg, and
# live.env's region and X parameter names in airops-stream.env) to S3 and runs box/setup.sh on the box through SSM;
# --test-bundle also installs TGZ, a static viewer folder (viewer/ and data/), behind airops-static on :8080, for tests
# without the live server.
set -euo pipefail
STREAM="$(cd "$(dirname "$0")" && pwd)"
source "$STREAM/../aws/common.sh"
ID=${1:?usage: deploy.sh INSTANCE_ID [--test-bundle TGZ]}
need LIVE_BUCKET
tmp=$(mktemp -d); mkdir "$tmp/box"
cp -R "$STREAM/box/." "$tmp/box/"; rm -rf "$tmp/box/__pycache__"
cp "$STREAM/../../web/audio/apron.ogg" "$tmp/box/bed.ogg"
sed -e "s|^AIROPS_SSM_SERVER=.*|AIROPS_SSM_SERVER=${LIVE_X_SERVER_PARAM:-/airops-live/x-server}|" \
    -e "s|^AIROPS_SSM_KEY=.*|AIROPS_SSM_KEY=${LIVE_X_KEY_PARAM:-/airops-live/x-key}|" \
    -e "s|^AWS_DEFAULT_REGION=.*|AWS_DEFAULT_REGION=$AWS_REGION|" "$STREAM/box/airops-stream.env" > "$tmp/box/airops-stream.env"
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$tmp/box.tgz" -C "$tmp/box" .
aws s3 cp "$tmp/box.tgz" "s3://$BUCKET/$PREFIX/stream/box.tgz" --only-show-errors
BUNDLE=
if [ "${2:-}" = --test-bundle ]; then
  aws s3 cp "${3:?usage: deploy.sh INSTANCE_ID [--test-bundle TGZ]}" "s3://$BUCKET/$PREFIX/stream/test-bundle.tgz" --only-show-errors
  BUNDLE=$(presign get "$PREFIX/stream/test-bundle.tgz" 3600)
fi
ssm "$ID" 1200 "$STREAM/box/setup.sh" "$(presign get "$PREFIX/stream/box.tgz" 3600)" $BUNDLE
rm -rf "$tmp"
