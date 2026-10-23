set -euo pipefail
mkdir -p /opt/airops-onair
echo "$1" | base64 -d > /opt/airops-onair/fresh.py
cat > /etc/systemd/system/airops-fresh.service <<'U'
[Unit]
Description=AirOps stream: the picture against the page; restarts a stale encoder
After=airops-capture.service
[Service]
ExecStart=/usr/bin/python3 -u /opt/airops-onair/fresh.py /var/log/airops-stream/fresh.jsonl
Restart=always
RestartSec=10
CPUWeight=20
Nice=10
[Install]
WantedBy=multi-user.target
U
systemctl daemon-reload; systemctl enable -q --now airops-fresh; systemctl restart airops-fresh
sleep 75; systemctl is-active airops-fresh; tail -2 /var/log/airops-stream/fresh.jsonl
