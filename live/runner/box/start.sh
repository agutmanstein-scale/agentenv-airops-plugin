set -euo pipefail
WRAP=/home/ubuntu/with-model.sh; prev=
for a in "$@"; do [ "$prev" = --policy ] && [ "$a" = script ] && WRAP=/home/ubuntu/no-model.sh; prev=$a; done
if systemctl is-active -q airops-live; then echo "airops-live is already running"; exit 1; fi
systemctl reset-failed airops-live 2>/dev/null || true
rm -f /home/ubuntu/live/stop.flag
systemd-run --unit airops-live --uid ubuntu --gid ubuntu --property=WorkingDirectory=/home/ubuntu/agentenv-airops-plugin \
  --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin \
  --property=SupplementaryGroups=docker --property=KillMode=process --property=TimeoutStopSec=7200 \
  --property=Restart=on-failure --property=RestartSec=30 \
  --property=StandardOutput=append:/home/ubuntu/live/runner.log --property=StandardError=append:/home/ubuntu/live/runner.log \
  "$WRAP" .venv/bin/python scripts/live_runner.py run --current /home/ubuntu/live/current.json "$@"
sleep 5; echo "airops-live: $(systemctl is-active airops-live) ($(basename $WRAP))"; tail -5 /home/ubuntu/live/runner.log
