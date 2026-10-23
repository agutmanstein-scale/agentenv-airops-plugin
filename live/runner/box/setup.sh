set -euo pipefail
REPO_URL=$1; BUNDLE_URL=$2; REF=$3; PINS=$4; PYVER=$5; LITELLM_URL=$6; KEY_PARAM=$7; REGION=$8
T0=$(date +%s); log() { echo "[$(( $(date +%s) - T0 ))s] $*"; }
systemctl stop unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
cloud-init status --wait >/dev/null 2>&1 || true
for i in $(seq 1 200); do fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1 || break; sleep 3; done
if ! command -v docker >/dev/null; then
  apt-get -o DPkg::Lock::Timeout=600 update -qq && DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y -qq docker.io docker-buildx docker-compose-v2 git zstd jq >/dev/null
  mkdir -p /etc/docker
  printf '{"default-address-pools":[{"base":"172.20.0.0/14","size":24}]}\n' > /etc/docker/daemon.json
  systemctl enable docker >/dev/null 2>&1; systemctl restart docker
  usermod -aG docker ubuntu
fi
printf "fs.inotify.max_user_instances=8192\nfs.inotify.max_user_watches=1048576\n" > /etc/sysctl.d/90-inotify.conf; sysctl -q --system
docker compose version >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y -qq docker-compose-v2 >/dev/null
command -v aws >/dev/null || snap install aws-cli --classic >/dev/null
log "docker $(docker --version | cut -d' ' -f3) compose $(docker compose version --short) aws $(aws --version 2>&1 | cut -d' ' -f1)"
docker ps --format '{{.Names}}' | grep -q '^agentenv-registry$' || \
  docker run -d --restart unless-stopped -p 5000:5000 --name agentenv-registry public.ecr.aws/docker/library/registry:2 >/dev/null
log "registry up"
install -d -o ubuntu -g ubuntu /home/ubuntu/live
cat > /home/ubuntu/with-model.sh <<W
#!/bin/bash
export LITELLM_BASE_URL=$LITELLM_URL
export LITELLM_API_KEY="\$(aws ssm get-parameter --region $REGION --name $KEY_PARAM --with-decryption --query Parameter.Value --output text)"
[ -n "\$LITELLM_API_KEY" ] || { echo "no key at $KEY_PARAM" >&2; exit 1; }
cd /home/ubuntu/agentenv-airops-plugin
exec "\$@"
W
cat > /home/ubuntu/no-model.sh <<'W'
#!/bin/bash
export LITELLM_BASE_URL=http://127.0.0.1:9 LITELLM_API_KEY=sk-no-model
cd /home/ubuntu/agentenv-airops-plugin
exec "$@"
W
chmod 755 /home/ubuntu/with-model.sh /home/ubuntu/no-model.sh; chown ubuntu:ubuntu /home/ubuntu/with-model.sh /home/ubuntu/no-model.sh
curl -sf -o /tmp/live.bundle "$BUNDLE_URL"; chmod 644 /tmp/live.bundle
cat > /tmp/user-setup.sh <<U
set -euo pipefail
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
export PATH=\$HOME/.local/bin:\$PATH
[ -d agentenv-airops-plugin ] || git clone -q $REPO_URL agentenv-airops-plugin
cd agentenv-airops-plugin && git fetch -q --tags origin && git fetch -q /tmp/live.bundle "refs/heads/$REF" && git checkout -q --detach FETCH_HEAD
[ -x .venv/bin/python ] || uv venv -q -p $PYVER .venv
uv sync -q -p .venv/bin/python --all-extras
uv pip install -q -p .venv/bin/python $PINS
echo "repo at \$(git log --oneline -1)"
.venv/bin/python -c "import importlib.metadata as m, platform; print('python', platform.python_version(), 'framework', m.version('agentenv-framework'), 'protocol', m.version('agentenv-framework-protocol'))"
.venv/bin/agent-env airops-occ3 setup > /home/ubuntu/live/occ3-setup.log 2>&1 || { tail -30 /home/ubuntu/live/occ3-setup.log; exit 1; }
grep -E "^Registered" /home/ubuntu/live/occ3-setup.log
.venv/bin/python scripts/live_runner.py run --current /home/ubuntu/live/current.json --dry-run | tail -3
U
chmod 644 /tmp/user-setup.sh
sudo -iu ubuntu bash /tmp/user-setup.sh
n=$(aws ssm get-parameter --region "$REGION" --name "$KEY_PARAM" --with-decryption --query 'length(Parameter.Value)' --output text 2>/dev/null || echo "none yet")
log "key at $KEY_PARAM: $n chars"
if [ -n "$LITELLM_URL" ]; then
  log "litellm health: $(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$LITELLM_URL/health/liveliness")"
else
  log "no LIVE_LITELLM_BASE_URL: scripted desks only"
fi
s=$(grep USEC /run/systemd/shutdown/scheduled 2>/dev/null | cut -d= -f2 | cut -c1-10); log "box powers off: $([ -n "$s" ] && date -u -d @$s +%FT%TZ || echo never)"
log "setup done"
