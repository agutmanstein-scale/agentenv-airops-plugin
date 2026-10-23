#!/usr/bin/env bash
# Xorg on the NVIDIA GPU with a virtual screen of AIROPS_SIZE and no monitor, no screen saver and no DPMS.
set -euo pipefail
SIZE=${AIROPS_SIZE:-1920x1080}
BUS=$(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader | head -1 \
  | awk -F'[:.]' '{printf "PCI:%d:%d:%d", strtonum("0x"$2), strtonum("0x"$3), strtonum("0x"$4)}')
cat > /etc/X11/xorg-airops.conf <<XC
Section "ServerFlags"
  Option "AutoAddDevices" "false"
  Option "AutoAddGPU" "false"
  Option "BlankTime" "0"
  Option "StandbyTime" "0"
  Option "SuspendTime" "0"
  Option "OffTime" "0"
EndSection
Section "Device"
  Identifier "nvidia"
  Driver "nvidia"
  BusID "$BUS"
  Option "AllowEmptyInitialConfiguration" "true"
  Option "HardDPMS" "false"
EndSection
Section "Screen"
  Identifier "screen0"
  Device "nvidia"
  DefaultDepth 24
  Option "AllowEmptyInitialConfiguration" "true"
  SubSection "Display"
    Depth 24
    Virtual ${SIZE%x*} ${SIZE#*x}
  EndSubSection
EndSection
XC
exec Xorg "${AIROPS_DISPLAY:-:0}" -config /etc/X11/xorg-airops.conf -noreset -nolisten tcp -novtswitch -sharevts -s 0 -dpms vt7
