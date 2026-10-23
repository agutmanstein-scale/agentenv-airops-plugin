# docker-pools.sh: on the Deep Learning AMI (Docker preinstalled, with the NVIDIA runtime), adds Docker address pools
# of /24s to its daemon.json, keeping the rest, so the env's networks don't run out; turns off apt's timers.
set -euo pipefail
f=/etc/docker/daemon.json; [ -f $f ] || echo '{}' > $f
if ! jq -e '."default-address-pools"' $f >/dev/null; then
  jq '. + {"default-address-pools":[{"base":"172.20.0.0/14","size":24}]}' $f > $f.new && mv $f.new $f
  [ -z "$(docker ps -q)" ] || { echo "containers running: not restarting docker"; exit 1; }
  systemctl restart docker
fi
cat $f; docker buildx version; docker compose version --short
systemctl stop unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl disable unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
