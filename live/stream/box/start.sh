#!/usr/bin/env bash
# start.sh [--url URL] [--target none|ssm|rtmp(s)://...] [--size WxH] [--bitrate 6000k] [--safe-url URL]: sets what is
# given in /etc/airops-stream.env, starts what is stopped and restarts what a change concerns. --target ssm streams to
# X with the server and key in SSM (AIROPS_SSM_SERVER, AIROPS_SSM_KEY); none only records. Changing only the
# target restarts only the push.
set -euo pipefail
ENVF=/etc/airops-stream.env
changed=$(python3 - "$ENVF" "$@" <<'PY'
import sys
path, args = sys.argv[1], sys.argv[2:]
keys = {"--url": "AIROPS_URL", "--target": "AIROPS_TARGET", "--size": "AIROPS_SIZE", "--bitrate": "AIROPS_VBITRATE",
        "--safe-url": "AIROPS_SAFE_URL"}
if len(args) % 2 or any(a not in keys for a in args[::2]):
    sys.exit(f"usage: start.sh [{' X] ['.join(keys)} X]")
new = {keys[a]: v for a, v in zip(args[::2], args[1::2])}
lines = open(path).read().splitlines()
out = [f"{l.split('=', 1)[0]}={new.pop(l.split('=', 1)[0])}" if l.split("=", 1)[0] in new else l for l in lines]
out += [f"{k}={v}" for k, v in new.items()]
open(path, "w").write("\n".join(out) + "\n")
print(" ".join(keys[a] for a in args[::2]))
PY
)
if [[ " $changed " == *" AIROPS_SIZE "* ]]; then
  python3 /opt/airops-stream/slate.py /opt/airops-stream/slate.png "$(grep ^AIROPS_SIZE= $ENVF | cut -d= -f2)"
  systemctl restart airops-x
fi
systemctl start airops-x airops-relay
for unit in airops-capture airops-push; do
  if ! systemctl is-active -q $unit; then
    systemctl start $unit
  elif [ -n "$changed" ] && { [ $unit = airops-push ] || [ "$changed" != AIROPS_TARGET ]; }; then
    systemctl restart $unit
  fi
done
sleep 3
grep -E "^AIROPS_(URL|TARGET|SIZE|VBITRATE|SAFE_URL)=" $ENVF
echo "units: $(systemctl is-active airops-x airops-relay airops-capture airops-push | paste -sd' ') (x relay capture push)"
