#!/usr/bin/env bash
# setup.sh PAYLOAD_URL [TEST_BUNDLE_URL]: makes a fresh GPU box (AWS Deep Learning Base OSS NVIDIA driver AMI, Ubuntu
# 22.04, e.g. g6.xlarge) a stream box. Installs Xorg, PulseAudio, ffmpeg, Google Chrome and mediamtx, the payload
# (deploy.sh uploads it) into /opt/airops-stream, the airops-* units (enabled, not started) and, with TEST_BUNDLE_URL,
# a static viewer bundle in /opt/airops-view behind airops-static on :8080. Idempotent; keeps /etc/airops-stream.env but
# for the region and the X parameter names, which follow the payload's (live.env's).
set -euo pipefail
PAYLOAD_URL=${1:?usage: setup.sh PAYLOAD_URL [TEST_BUNDLE_URL]}
BUNDLE_URL=${2:-}
MEDIAMTX=v1.21.2
export DEBIAN_FRONTEND=noninteractive
nvidia-smi -L >/dev/null || { echo "no NVIDIA GPU or driver: use the Deep Learning Base OSS NVIDIA driver AMI" >&2; exit 1; }
for _ in $(seq 1 60); do fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1 || break; sleep 5; done
systemctl stop unattended-upgrades 2>/dev/null || true
apt-get -o Acquire::Retries=5 update -qq 2>/dev/null
apt-get -o Acquire::Retries=5 install -y -qq --no-install-recommends xserver-xorg-core x11-xserver-utils x11-utils hsetroot \
  pulseaudio pulseaudio-utils ffmpeg fonts-noto-core fonts-dejavu-core fonts-noto-color-emoji python3-websocket \
  python3-boto3 python3-pil jq curl mesa-utils >/dev/null
if ! command -v google-chrome >/dev/null; then
  curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -y -qq /tmp/chrome.deb >/dev/null
fi
id streamer >/dev/null 2>&1 || useradd -m -s /bin/bash streamer
usermod -aG video,audio,render streamer 2>/dev/null || true
mkdir -p /opt/airops-stream
curl -fsSL "$PAYLOAD_URL" | tar xz -C /opt/airops-stream
if [ ! -x /opt/airops-stream/mediamtx ] || ! /opt/airops-stream/mediamtx --version 2>&1 | grep -q "$MEDIAMTX"; then
  curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/$MEDIAMTX/mediamtx_${MEDIAMTX}_linux_amd64.tar.gz" \
    | tar xz -C /opt/airops-stream mediamtx
fi
if [ ! -f /etc/airops-stream.env ]; then
  cp /opt/airops-stream/airops-stream.env /etc/airops-stream.env
  [ "$(nproc)" -le 4 ] && sed -i 's/^AIROPS_PAGE_FPS=.*/AIROPS_PAGE_FPS=30/' /etc/airops-stream.env
fi
for k in AIROPS_SSM_SERVER AIROPS_SSM_KEY AWS_DEFAULT_REGION; do
  v=$(grep "^$k=" /opt/airops-stream/airops-stream.env | cut -d= -f2-)
  if grep -q "^$k=" /etc/airops-stream.env; then sed -i "s|^$k=.*|$k=$v|" /etc/airops-stream.env; else echo "$k=$v" >> /etc/airops-stream.env; fi
done
python3 /opt/airops-stream/slate.py /opt/airops-stream/slate.png "$(grep ^AIROPS_SIZE= /etc/airops-stream.env | cut -d= -f2)"
install -d -o streamer -g streamer /var/lib/airops-stream /var/lib/airops-stream/rec /var/log/airops-stream
cp /opt/airops-stream/units/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q airops-x airops-relay airops-capture airops-push
if [ -n "$BUNDLE_URL" ]; then
  rm -rf /opt/airops-view && mkdir -p /opt/airops-view && curl -fsSL "$BUNDLE_URL" | tar xz -C /opt/airops-view
  systemctl enable -q --now airops-static
fi
echo "chrome: $(google-chrome --version)"
echo "mediamtx: $(/opt/airops-stream/mediamtx --version 2>&1 | head -1)"
echo "ffmpeg: $(ffmpeg -version | head -1)"
echo "gpu: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader)"
echo "setup done; start.sh starts the stream"
