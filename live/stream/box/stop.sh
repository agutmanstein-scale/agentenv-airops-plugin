#!/usr/bin/env bash
# stop.sh [--push-only]: stops pushing to the target (--push-only), or the whole stream: push, capture, relay and the
# display. The units stay enabled, so a reboot starts the stream again; stop.sh --disable keeps it stopped.
set -euo pipefail
case ${1:-} in
  --push-only) systemctl stop airops-push ;;
  --disable) systemctl disable -q --now airops-push airops-capture airops-relay airops-x ;;
  *) systemctl stop airops-push airops-capture airops-relay airops-x ;;
esac
systemctl is-active airops-x airops-relay airops-capture airops-push | paste -sd' ' || true
