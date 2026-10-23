set -uo pipefail
cd /home/ubuntu/agentenv-airops-plugin || exit 1
echo "runner: $(systemctl is-active airops-live)"
curl -s --max-time 5 http://127.0.0.1:8765/live/state.json | python3 -c 'import json,sys; l=json.load(sys.stdin).get("live",{}); print("server:", l.get("status"), l.get("watch"), l.get("exact"), l.get("final"))'
sudo -u ubuntu /home/ubuntu/no-model.sh .venv/bin/python scripts/live_runner.py run --current /tmp/dry-current.json --dry-run "$@" 2>&1 | tail -40
