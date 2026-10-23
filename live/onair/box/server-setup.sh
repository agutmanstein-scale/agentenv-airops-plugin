set -euo pipefail
PAYLOAD=$1; PREBUILT=$2; ASSETS=$3; INPUTS=$4
T0=$(date +%s); log() { echo "[$(( $(date +%s) - T0 ))s] $*"; }
R=/home/ubuntu/agentenv-airops-plugin; L=/home/ubuntu/live
[ -x $R/.venv/bin/python ] || { echo "no run box checkout at $R: run the runner's setup.sh first"; exit 1; }
mkdir -p /opt/airops-onair && curl -fsSL "$PAYLOAD" | tar xz -C /opt/airops-onair
fetch() { sudo -u ubuntu mkdir -p "$2"; curl -fsSL "$1" | zstd -dc | sudo -u ubuntu tar -xf - -C "$2"; }
[ -d $L/prebuilt/news ] || fetch "$PREBUILT" $L/prebuilt
[ -d $L/assets/stage ] || fetch "$ASSETS" $L/assets
[ -d $R/cache/inputs ] || fetch "$INPUTS" $R/cache
log "prebuilt $(ls $L/prebuilt/news | wc -l) days, $(du -sh $L/prebuilt | cut -f1); assets $(du -sh $L/assets | cut -f1); inputs $(du -sh $R/cache/inputs | cut -f1)"
cp /opt/airops-onair/units/airops-live-server.service /etc/systemd/system/
for u in airops-x airops-relay airops-capture airops-push; do
  install -d /etc/systemd/system/$u.service.d; cp /opt/airops-onair/units/stream-priority.conf /etc/systemd/system/$u.service.d/priority.conf
done
systemctl daemon-reload
systemctl enable -q airops-live-server
systemctl restart airops-live-server
for i in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:8765/live/state.json 2>/dev/null && break; sleep 1; done
log "server: $(systemctl is-active airops-live-server); state $(curl -fsS http://127.0.0.1:8765/live/state.json | head -c 300)"
for p in web/index.html web/live.js web/audio.js stage/JFK/stage.json.gz; do echo "$p $(curl -s -o /dev/null -w '%{http_code} %{size_download}' http://127.0.0.1:8765/$p)"; done
tail -5 $L/server.log
