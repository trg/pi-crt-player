#!/usr/bin/env bash
# pi-crt-player — one-shot setup for a fresh Raspberry Pi 4 running
# Raspberry Pi OS Lite (trixie). Sets the Pi up to play YouTube videos on an
# HDMI-connected CRT, driven over SSH.
#
#   git clone <this repo> && cd pi-crt-player && ./setup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing mpv..."
sudo apt update
sudo apt install -y mpv wget python3

echo "==> Installing latest yt-dlp (apt's version rots as YouTube changes)..."
sudo wget -q https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -O /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp

echo "==> Adding $USER to video,render,audio groups (for DRM/GPU/sound access)..."
sudo usermod -aG video,render,audio "$USER"

echo "==> Installing mpv config to ~/.config/mpv/mpv.conf..."
mkdir -p "$HOME/.config/mpv"
cp "$REPO_DIR/config/mpv.conf" "$HOME/.config/mpv/mpv.conf"

echo "==> Installing play / stop commands to /usr/local/bin..."
sudo cp "$REPO_DIR/bin/play" "$REPO_DIR/bin/stop" /usr/local/bin/
sudo chmod a+rx /usr/local/bin/play /usr/local/bin/stop

echo "==> Installing CRT Player telnet control server (runs on boot)..."
sudo install -d /usr/local/lib/pi-crt-player
sudo cp "$REPO_DIR/server/player.py" /usr/local/lib/pi-crt-player/player.py
sudo chmod a+rx /usr/local/lib/pi-crt-player/player.py
sed "s/@USER@/$USER/" "$REPO_DIR/systemd/crt-player.service" \
  | sudo tee /etc/systemd/system/crt-player.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now crt-player.service

echo
echo "Done. Log out and back in ONCE (for the group change to apply), then:"
echo "  play 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'"
echo "  play space oddity david bowie"
echo "  stop"
echo
echo "Remote is live — anyone on the network can control the TV with:"
echo "  telnet $(hostname).local     (or: telnet $(hostname -I | awk '{print $1}'))"
