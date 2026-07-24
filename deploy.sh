#!/usr/bin/env bash
# deploy.sh — push local changes to an already-set-up Pi without git
# (scp + restart). Complements setup.sh, which is for a fresh install.
#
#   ./deploy.sh pi@crt-station.local
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <user@pi-host>" >&2
  exit 1
fi
HOST="$1"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Copying server/pcpd.py..."
scp -q "$REPO_DIR/server/pcpd.py" "$HOST:/tmp/pcpd.py"
ssh "$HOST" 'sudo install -m 755 /tmp/pcpd.py /usr/local/lib/pi-crt-player/pcpd.py'

echo "==> Copying bin/pcp..."
scp -q "$REPO_DIR/bin/pcp" "$HOST:/tmp/pcp"
ssh "$HOST" 'sudo install -m 755 /tmp/pcp /usr/local/bin/pcp'

echo "==> Copying config/mpv.conf..."
scp -q "$REPO_DIR/config/mpv.conf" "$HOST:.config/mpv/mpv.conf"

echo "==> Copying config/channels.txt..."
CHANNELS_FILE="$(ssh "$HOST" 'systemctl show crt-player -p Environment' \
  | tr ' ' '\n' | sed -n 's/^CHANNELS_FILE=//p')"
if [ -z "$CHANNELS_FILE" ]; then
  echo "!! couldn't read CHANNELS_FILE from crt-player.service on $HOST — skipping channels.txt" >&2
else
  scp -q "$REPO_DIR/config/channels.txt" "$HOST:$CHANNELS_FILE"
fi

echo "==> Restarting crt-player (picks up pcpd.py + mpv.conf)..."
ssh "$HOST" 'sudo systemctl restart crt-player'

echo "Done."
