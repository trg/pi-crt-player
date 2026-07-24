# ESP32 touchscreen remote — hello world design

Status: approved, not yet implemented.

## What this is

A physical remote control for pi-crt-player, built on a 2.8" 240x320
touchscreen ESP32 board (ESP32-2432S028R, "Cheap Yellow Display" / CYD). This
first version is a hello-world: two touch zones, "CH 7" and "CH 13", each of
which tunes the CRT player to that channel. It exists to prove the full path
— touch → WiFi → pcpd → CRT — end to end, as a foundation for a fuller remote
later (more channels, play/pause, volume, etc. — out of scope here).

Hardware is already confirmed working in a prior sandbox project at
`/Users/tom/claude-sandbox/2.8-in-lcd-display/current-conditions` (a weather
display on the same board). See `/Users/tom/claude-sandbox/2.8-in-lcd-display/CLAUDE.md`
for the full hardware writeup (pinout, gotchas, macOS toolchain fix) — this
design reuses that project's confirmed pinout and PlatformIO setup, not
rediscovering it.

## Architecture

New `remote-control/` directory in this repo, sibling to `server/`, `bin/`,
`config/`. Structured like `current-conditions`: a PlatformIO project
targeting `esp32dev` with TFT_eSPI, built specifically for this board's
pinout (SPI TFT + resistive XPT2046 touch sharing the bus, per the confirmed
pin map in the hardware CLAUDE.md).

Components:

- **Firmware** (`remote-control/src/main.cpp`) — on boot: init display, load
  `config.txt` from LittleFS, connect WiFi. Then loop: poll touch, act on
  taps, redraw as needed.
- **Server-side change** (`server/pcpd.py`) — change the `HTTP_HOST` default
  from `127.0.0.1` to `0.0.0.0` so the existing HTTP/JSON API is reachable
  from the ESP32 over the LAN. No new endpoint: the firmware calls the same
  `POST /channel` the `pcp` CLI already uses. This is a widening of an
  existing local-only API, not a new attack surface — it adopts the same
  "unauthenticated, LAN-only, by design" trust model telnet already has (see
  root `CLAUDE.md`'s Security note). No auth is added.
- **Config** (`remote-control/data/config.txt`, gitignored, seeded from a
  checked-in `config.txt.example`) — `WIFI_SSID`, `WIFI_PASS`, `PCP_HOST`,
  `PCP_PORT`. Loaded from LittleFS at boot, identical mechanism to
  `current-conditions`. No SD card involved — LittleFS lives in the ESP32
  module's own onboard flash, uploaded over USB; the board's physical microSD
  slot is unrelated and unused.
- **Deploy scripts** (`remote-control/scripts/{setup.sh,flash.sh,monitor.sh}`)
  — adapted from the proven `current-conditions` versions (including the
  Apple-Silicon `mklittlefs` fix).

## UI / data flow

Screen is split into two touch zones (left half / right half in landscape
rotation): tapping the left half requests channel 7, the right half channel
13. No confirmation UI on success — the CRT switching channel *is* the
confirmation. Only failures interrupt: a short on-screen message (e.g. "no
response", "HTTP 404") appears for ~1.5s, then the buttons redraw. Every state
is recoverable by tapping again; nothing requires a reboot except fixing
`config.txt` and reflashing.

Request flow: `tft.getTouch(&x, &y)` each loop iteration, debounced so a held
finger fires once → `HTTPClient` `POST http://<PCP_HOST>:<PCP_PORT>/channel`
with body `{"num": 7}` or `{"num": 13}` → only the HTTP status code is
checked (200 = success); no JSON response parsing, no ArduinoJson dependency
needed for this hello-world.

Boot-time failures follow the `current-conditions` pattern: missing/bad
`config.txt` halts with an on-screen message (nothing to retry without
reflashing); WiFi connect failure shows "WiFi connect failed" and `loop()`
keeps retrying periodically without user intervention.

## Testing & verification

No unit tests, matching this repo's existing philosophy (no build step / test
suite anywhere in pi-crt-player). Verification is physical and manual:

- `python3 -m py_compile server/pcpd.py` after the `HTTP_HOST` change.
- After that change ships, confirm the API answers from another LAN machine:
  `curl http://<pi-host>:8677/status`.
- Flash the firmware, tap each zone, confirm the CRT actually switches
  channel (via `pcp now` or the on-screen channel banner).
- `remote-control/scripts/monitor.sh` streams serial logs (WiFi status, HTTP
  response codes) for debugging without needing the CRT in view.

## Deploy workflow

- **First time**: `remote-control/scripts/setup.sh` (installs PlatformIO,
  applies the Apple-Silicon `mklittlefs` fix, seeds `data/config.txt` from the
  example) → edit `data/config.txt` with real WiFi + Pi address →
  `remote-control/scripts/flash.sh` (builds and uploads both firmware and the
  config filesystem in one shot; auto-detects the serial port, e.g.
  `/dev/cu.usbserial-10`, or accepts one explicitly).
- **Firmware-only change**: `pio run -t upload`.
- **Config-only change** (new WiFi, new Pi address): `pio run -t uploadfs`.
- **Debugging**: `remote-control/scripts/monitor.sh` for the serial log.

## Documentation artifacts

To leave a complete trail for both the user and future agents:

- `remote-control/README.md` — setup/flash/monitor instructions, same shape
  as `current-conditions/README.md`.
- A new short section in the root `CLAUDE.md` describing the remote-control
  architecture (what it is, where it lives, the `HTTP_HOST` LAN-exposure
  rationale) and pointing to `remote-control/README.md` for details.
- Inline code comments only where genuinely non-obvious: the touch-zone
  split, and the reasoning behind the `HTTP_HOST` default change.

## Explicitly out of scope (YAGNI for this hello-world)

- More than two channels, or any command besides channel-tuning.
- On-device WiFi configuration (captive portal) — config.txt via USB reflash
  is enough for now.
- OTA firmware updates — USB reflash is the deploy path until this remote
  grows beyond hello-world.
- Any response-body parsing / error detail beyond HTTP status code.
- Authentication on the HTTP API — matches the project's existing security
  posture.
