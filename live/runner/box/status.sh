set -uo pipefail
N=${1:-15}
echo "unit: $(systemctl is-active airops-live 2>/dev/null) restarts $(systemctl show airops-live -p NRestarts --value 2>/dev/null)$( [ -f /home/ubuntu/live/stop.flag ] && echo ', stop.flag set')"
python3 - <<'P'
import json, time
from pathlib import Path
c = Path("/home/ubuntu/live/current.json")
if not c.is_file():
    print("current.json: none yet")
    raise SystemExit
d = json.loads(c.read_text())
print(f"current: {d['status']} day {d.get('day')} {d.get('date')} {d.get('run_id')} seq {d['seq']} at {d['updated_utc']}"
      f"{' FINAL: ' + str(d.get('reason')) if d.get('final') else ''}")
print(f"  instance {d.get('instance_id')}; env {(d.get('env') or {}).get('base')}")
print(f"  spent ${d['spent_usd']:.2f} + live ${d.get('live_usd') or 0:.2f} {d.get('desks') or ''} of ${d['budget_usd']:g}; next {d.get('next')}")
for h in d["history"][-4:]:
    print(f"  {h['date']} {h['outcome']} reward {h['reward']} ${h['cost_usd']} {h['wall_seconds']}s {h['attempts']} attempt(s)")
r = Path(f"/home/ubuntu/agentenv-airops-plugin/results/runs/{d['pilot']}/results.jsonl")
rows = [json.loads(x) for x in r.read_text().splitlines() if x.strip()] if r.is_file() else []
print(f"results: {len(rows)} attempts, {sum(x['outcome'] == 'scored' for x in rows)} scored, {len(d['history'])} days settled")
P
echo "load $(cut -d' ' -f1-3 /proc/loadavg); mem used GB $(free -g | awk '/Mem/{print $3}'); disk $(df -h / | awk 'NR==2{print $5}'); containers $(docker ps -q | wc -l)"
s=$(grep USEC /run/systemd/shutdown/scheduled 2>/dev/null | cut -d= -f2 | cut -c1-10); echo "powers off: $([ -n "$s" ] && date -u -d @$s +%FT%TZ || echo never)"
tail -$N /home/ubuntu/live/runner.log 2>/dev/null | sed -E 's/sk-[A-Za-z0-9_-]{8,}/<redacted>/g'
