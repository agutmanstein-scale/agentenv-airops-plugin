#!/usr/bin/env bash
# Waits for the display, then shows the slate over the whole root window, not just the output the driver sets up
# (1024x768), which is what the stream shows while Chrome restarts.
export DISPLAY=${AIROPS_DISPLAY:-:0}
for _ in $(seq 1 60); do xset q >/dev/null 2>&1 && break; sleep 0.5; done
xset s off s noblank -dpms || true
hsetroot -root -fill /opt/airops-stream/slate.png >/dev/null || true
