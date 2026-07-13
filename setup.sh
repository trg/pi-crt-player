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
sudo apt install -y mpv wget

echo "==> Installing latest yt-dlp (apt's version rots as YouTube changes)..."
sudo wget -q https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -O /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp

echo "==> Adding $USER to video,render groups (for DRM/GPU access)..."
sudo usermod -aG video,render "$USER"

echo "==> Installing mpv config to ~/.config/mpv/mpv.conf..."
mkdir -p "$HOME/.config/mpv"
cp "$REPO_DIR/config/mpv.conf" "$HOME/.config/mpv/mpv.conf"

echo "==> Installing crt-play / crt-stop commands to /usr/local/bin..."
sudo cp "$REPO_DIR/bin/crt-play" "$REPO_DIR/bin/crt-stop" /usr/local/bin/
sudo chmod a+rx /usr/local/bin/crt-play /usr/local/bin/crt-stop

echo
echo "Done. Log out and back in ONCE (for the group change to apply), then:"
echo "  crt-play 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'"
echo "  crt-play space oddity david bowie"
echo "  crt-stop"
