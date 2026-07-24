#!/usr/bin/env bash
# Builds and uploads both the firmware and the LittleFS filesystem image
# (data/config.txt). Usage: scripts/flash.sh [serial-port]
# If no port is given, PlatformIO auto-detects it.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-}"

if [ -n "$PORT" ]; then
  pio run -t upload --upload-port "$PORT"
  pio run -t uploadfs --upload-port "$PORT"
else
  pio run -t upload
  pio run -t uploadfs
fi
