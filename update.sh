#!/usr/bin/env bash
# pi-crt-player — update an already-installed box to the latest main and
# re-run setup, then optionally reboot to pick up any daemon changes.
#
#   cd pi-crt-player && ./update.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

echo "==> Pulling latest main..."
git checkout main
git pull --ff-only origin main

echo "==> Running setup.sh..."
./setup.sh

read -rp "Restart now to apply changes? [y/N] " reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
  sudo reboot
fi
