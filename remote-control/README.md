# Remote control (ESP32 touchscreen)

Hello-world hardware remote for pi-crt-player: a 2.8" 240x320 touchscreen
ESP32 board (ESP32-2432S028R, "Cheap Yellow Display" / CYD), portrait
orientation, with two touch zones — top half tunes the CRT to **channel 7**,
bottom half to **channel 13**. It talks straight to pcpd's existing HTTP API
(`POST /channel {"num": <n>}`, the same endpoint the `pcp ch <n>` CLI uses) —
no server-side logic lives here, only a thin frontend, per the control-core
split described in the root `CLAUDE.md`.

See `docs/superpowers/specs/2026-07-24-esp32-remote-control-design.md` in the
repo root for the full design rationale.

## Prerequisites

- [PlatformIO Core](https://platformio.org/install/cli) (`brew install platformio`)
- A USB **data** cable to the board (many cables are charge-only — if the
  board doesn't show up as a serial device, try a different cable first)
- pcpd on the Pi must be reachable over the LAN, not just localhost — see
  "Server-side requirement" below.

## Setup

```
scripts/setup.sh
```

Installs PlatformIO if missing, fixes the `mklittlefs` tool if you're on
Apple Silicon (it ships x86_64-only from PlatformIO's registry), and creates
`data/config.txt` from the example if it doesn't exist yet.

Then edit `data/config.txt` (gitignored — never committed):

```
WIFI_SSID=your-wifi-name
WIFI_PASS=your-wifi-password
PCP_HOST=crt-station
PCP_PORT=8677
```

`PCP_HOST` is the Pi's **mDNS hostname** — the same one you'd `telnet` from a
Mac (no `.local` suffix needed). The firmware re-resolves it via mDNS
(`ESPmDNS`) on every request rather than storing a fixed IP, since DHCP
leases aren't stable — the Pi can change address over time with no config
change needed here.

## Flashing

Connect the board via USB, then:

```
scripts/flash.sh
```

Builds and uploads both the firmware and the config filesystem image. Pass a
serial port explicitly if auto-detection picks the wrong device (e.g. if you
have multiple boards connected), or if it fails to auto-detect:

```
scripts/flash.sh /dev/cu.usbserial-10
```

To find the port: run `ls /dev/cu.*` before and after plugging the board in
(with a data cable) — the new entry is your port.

## First boot

1. **Touch calibration** — the first boot ever (or after erasing NVS) shows
   "Tap the TOP button" then "Tap the BOTTOM button"; tap each one when
   prompted. The result is cached in flash (`Preferences`/NVS), so every
   later boot skips straight to the two buttons. (This is a simple 2-point
   calibration, not TFT_eSPI's built-in 4-corner one — see "Hardware notes"
   below for why the touch controller needs its own separate handling on
   this board.)
2. **WiFi connect** — shows "Connecting WiFi...", then either the two
   buttons or "WiFi connect failed" (it keeps retrying in the background;
   fix `data/config.txt` and re-run `pio run -t uploadfs` if it never
   connects).
3. Tap a button. Nothing shows on success — the CRT switching channel *is*
   the confirmation (check with `pcp now` on the Pi, or just look at the
   TV). A failed request shows a short error (`HTTP 404`, `no response`,
   etc.) for ~1.5s, then returns to the buttons — always safe to just tap
   again.

## Monitoring

```
scripts/monitor.sh
```

Streams the board's serial log (WiFi status, HTTP response codes). Ctrl+C to
exit.

## Re-flashing after a change

- Firmware only (`src/main.cpp`): `pio run -t upload`
- Config only (new WiFi, new Pi address): `pio run -t uploadfs`
- Both: `scripts/flash.sh`

## Server-side requirement

pcpd's HTTP API must be reachable from the ESP32 over the LAN — by default
it's bound to `127.0.0.1` only. `server/pcpd.py`'s `HTTP_HOST` default was
changed to `0.0.0.0` for this reason (see that file's comment and the root
`CLAUDE.md`'s Security note). If you're running an older checkout on the Pi,
`./deploy.sh`/`./setup.sh` from the repo root to pick up the change, then
restart `crt-player`.

## Hardware notes

Board pinout (backlight pin, display SPI pins) is documented in
`platformio.ini`'s build flags. Display setup was previously confirmed
working on this exact board in a sibling sandbox project,
`2.8-in-lcd-display/current-conditions` (a weather display, not part of this
repo — kept only as a pointer).

**Touch is on its own separate SPI bus**, not shared with the display as
most CYD writeups (and this repo's own earlier assumption) claim — confirmed
the hard way, on real hardware, when TFT_eSPI's built-in touch support read
a flat zero on every sample no matter how the screen was pressed. Fixed by
using a dedicated `XPT2046_Touchscreen` instance on the touch bus (`MOSI 32,
MISO 39, SCK 25, CS 33, IRQ 36`) while forcing the display onto the other
SPI hardware peripheral (`-DUSE_HSPI_PORT=1` in `platformio.ini`) so the two
don't contend. Full writeup, including how to diagnose this same symptom on
a different board revision: `2.8-in-lcd-display/CLAUDE.md`'s "Touch gotcha"
section (sibling project, same machine).
