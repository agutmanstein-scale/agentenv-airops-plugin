#!/usr/bin/env bash
# status.sh [MINUTES]: the units, the relay's paths, the last metrics of capture and push, restarts and warnings in
# the last MINUTES (default 30), GPU, memory and disk.
M=${1:-30}
echo "units: $(systemctl is-active airops-x airops-relay airops-capture airops-push | paste -sd' ') (x relay capture push)"
grep -E "^AIROPS_(URL|TARGET|SIZE|VBITRATE)=" /etc/airops-stream.env
curl -fsS http://127.0.0.1:9997/v3/paths/list 2>/dev/null | jq -r '.items[] | "relay: \(.name) ready=\(.ready) in=\(.bytesReceived) out=\(.bytesSent) readers=\(.readers|length)"' \
  || echo "relay: not answering"
for m in capture push; do
  f=/var/log/airops-stream/metrics-$m.jsonl
  [ -f $f ] && echo "$m: $(tail -1 $f)"
done
echo "--- restarts and warnings, last $M min"
journalctl -u airops-capture -u airops-push -u airops-relay -u airops-x --since "-${M}min" --no-pager -o short-iso 2>/dev/null \
  | grep -Ei "restart|stall|error|fail|exited|warn|memory|disk" | grep -v metrics | tail -20
echo "---"
nvidia-smi --query-gpu=utilization.gpu,utilization.encoder,memory.used,temperature.gpu --format=csv,noheader
free -m | awk 'NR==2{print "mem: used " $3 " MB, available " $7 " MB"}'
df -h /var/lib/airops-stream | awk 'NR==2{print "disk: " $4 " free"}'
ls -la /var/lib/airops-stream/rec/airops 2>/dev/null | tail -3
cat /run/systemd/shutdown/scheduled 2>/dev/null | grep -E "USEC|MODE" | tr '\n' ' ' && echo "(a scheduled power-off is set: shutdown -c cancels it)"
