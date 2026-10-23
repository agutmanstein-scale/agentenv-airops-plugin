set -euo pipefail
URL=$1; NAME=$(python3 -c "import json; print(json.load(open('/home/ubuntu/live/current.json'))['pilot'])")
cd /home/ubuntu && tar -cf - live "agentenv-airops-plugin/results/runs/$NAME" | zstd -q -T0 -3 > /tmp/live-out.tar.zst
curl -s -o /dev/null -w "put $NAME http=%{http_code}\n" -X PUT --upload-file /tmp/live-out.tar.zst "$URL"
