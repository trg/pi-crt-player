#!/usr/bin/env bash
# Streams the board's serial log (WiFi status, HTTP response codes).
# Ctrl+C to exit. Usage: scripts/monitor.sh [serial-port]
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-}"

if [ -n "$PORT" ]; then
  pio device monitor --port "$PORT"
else
  pio device monitor
fi
