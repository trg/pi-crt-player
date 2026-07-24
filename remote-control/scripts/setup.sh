#!/usr/bin/env bash
# One-time environment setup: installs PlatformIO if needed, fixes the
# mklittlefs arch mismatch on Apple Silicon, and creates data/config.txt
# from the example if it doesn't exist yet.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v pio >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing PlatformIO via Homebrew..."
    brew install platformio
  else
    echo "PlatformIO not found and Homebrew not available. Install PlatformIO"
    echo "manually: https://platformio.org/install/cli" >&2
    exit 1
  fi
fi

if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  MKLFS_DIR="$HOME/.platformio/packages/tool-mklittlefs"
  MKLFS_BIN="$MKLFS_DIR/mklittlefs"
  if [ -f "$MKLFS_BIN" ] && ! file "$MKLFS_BIN" | grep -q arm64; then
    echo "Fixing mklittlefs (PlatformIO's copy is x86_64, this Mac is arm64)..."
    if ! command -v brew >/dev/null 2>&1; then
      echo "Homebrew not available; can't auto-fix. See README.md for manual steps." >&2
      exit 1
    fi
    brew list mklittlefs >/dev/null 2>&1 || brew install mklittlefs
    mv "$MKLFS_BIN" "$MKLFS_DIR/mklittlefs.x86_64.bak"
    ln -s "$(brew --prefix)/bin/mklittlefs" "$MKLFS_BIN"
  fi
fi

if [ ! -f data/config.txt ]; then
  echo "Creating data/config.txt from config.txt.example..."
  mkdir -p data
  cp config.txt.example data/config.txt
  echo "Edit data/config.txt with your WiFi SSID/password and the Pi's mDNS hostname before flashing."
fi

echo "Setup complete."
