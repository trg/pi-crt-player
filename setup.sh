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
sudo apt install -y mpv wget python3 fontconfig

echo "==> Installing 'Press Start 2P' retro font for the idle screen..."
sudo install -d /usr/local/share/fonts
sudo wget -q \
  https://github.com/google/fonts/raw/main/ofl/pressstart2p/PressStart2P-Regular.ttf \
  -O /usr/local/share/fonts/PressStart2P-Regular.ttf
sudo fc-cache -f /usr/local/share/fonts

echo "==> Installing latest yt-dlp (apt's version rots as YouTube changes)..."
sudo wget -q https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
  -O /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp

echo "==> Adding $USER to video,render,audio groups (for DRM/GPU/sound access)..."
sudo usermod -aG video,render,audio "$USER"

echo "==> Installing mpv config to ~/.config/mpv/mpv.conf..."
mkdir -p "$HOME/.config/mpv"
cp "$REPO_DIR/config/mpv.conf" "$HOME/.config/mpv/mpv.conf"

echo "==> Installing player CLI (pcp + play/stop/queue/next/now)..."
sudo cp "$REPO_DIR/bin/pcp" /usr/local/bin/pcp
sudo chmod a+rx /usr/local/bin/pcp
for c in play stop queue next now; do
  sudo ln -sf pcp "/usr/local/bin/$c"
done

echo "==> Installing CRT Player control daemon (runs on boot)..."
sudo install -d /usr/local/lib/pi-crt-player
sudo cp "$REPO_DIR/server/pcpd.py" /usr/local/lib/pi-crt-player/pcpd.py
sudo chmod a+rx /usr/local/lib/pi-crt-player/pcpd.py
sed "s/@USER@/$USER/g" "$REPO_DIR/systemd/crt-player.service" \
  | sudo tee /etc/systemd/system/crt-player.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart crt-player.service
sudo systemctl enable crt-player.service

echo
echo "Done. The control daemon is running (it owns one persistent mpv), so:"
echo "  play space oddity david bowie   # play now"
echo "  queue another great song        # add to the queue"
echo "  now                             # what's playing + queue"
echo "  next / stop"
echo
echo "Remote is live — anyone on the network can control the TV with:"
echo "  telnet $(hostname)     (or: telnet $(hostname -I | awk '{print $1}'))"
