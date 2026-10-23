set -euo pipefail
curl -fsS -o /tmp/sync.bundle "$1"; chmod 644 /tmp/sync.bundle
sudo -iu ubuntu bash -c "cd agentenv-airops-plugin && git fetch -q /tmp/sync.bundle refs/heads/$2 && git checkout -q --detach FETCH_HEAD && git log --oneline -1 && git status --short | head"
