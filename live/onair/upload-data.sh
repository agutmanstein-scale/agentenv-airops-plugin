#!/usr/bin/env bash
# upload-data.sh PREBUILT_DIR ASSETS_DIR [REPO_DIR]: packs what the live server reads and uploads it to
# s3://LIVE_BUCKET/LIVE_S3_PREFIX/onair/, where deploy-server.sh takes it: PREBUILT_DIR (`live_server.py prebuild`'s
# news/, traffic/ and radar/), ASSETS_DIR (a viewer folder's stage/, radar/ and twin/, e.g. the dataset's viewer/) and
# REPO_DIR's cache/inputs (the raw inputs; default: the checkout this script is in).
set -euo pipefail
O="$(cd "$(dirname "$0")" && pwd)"
source "$O/../aws/common.sh"
[ $# -ge 2 ] || die "usage: upload-data.sh PREBUILT_DIR ASSETS_DIR [REPO_DIR]"
PRE=$1; ASSETS=$2
REPO=${3:-$(git -C "$O" rev-parse --show-toplevel)}
need LIVE_BUCKET
for d in "$PRE/news" "$PRE/traffic" "$ASSETS/stage" "$ASSETS/twin" "$REPO/cache/inputs"; do [ -d "$d" ] || die "no $d"; done
tmp=$(mktemp -d)
pack() { local out=$1 dir=$2; shift 2; COPYFILE_DISABLE=1 tar --no-xattrs -cf - -C "$dir" "$@" | zstd -q -T0 -3 > "$tmp/$out"; }
pre=(news traffic); [ -d "$PRE/radar" ] && pre+=(radar)
assets=(stage twin); [ -d "$ASSETS/radar" ] && assets+=(radar)
pack prebuilt.tar.zst "$PRE" "${pre[@]}"
pack assets.tar.zst "$ASSETS" "${assets[@]}"
pack inputs.tar.zst "$REPO/cache" inputs
for f in prebuilt assets inputs; do
  log "$f.tar.zst: $(du -h "$tmp/$f.tar.zst" | cut -f1)"
  aws s3 cp --only-show-errors "$tmp/$f.tar.zst" "s3://$BUCKET/$PREFIX/onair/$f.tar.zst"
done
rm -rf "$tmp"
