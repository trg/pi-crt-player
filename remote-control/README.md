# Remote control (ESP32 touchscreen)

Hardware remote for pi-crt-player: a 2.8" 240x320 touchscreen ESP32 board
(ESP32-2432S028R, "Cheap Yellow Display" / CYD), portrait orientation,
showing a paged, tap-to-tune list of every channel from pcpd's guide (the
same list `pcp guide`/telnet's `guide` command shows). Tapping a row POSTs
straight to pcpd's existing HTTP API (`POST /channel {"num": <n>}`, the same
endpoint the `pcp ch <n>` CLI uses) — no server-side logic lives here, only a
thin frontend, per the control-core split described in the root `CLAUDE.md`.

See `docs/superpowers/specs/2026-07-24-esp32-remote-control-design.md` (the
original two-button hello-world),
`docs/superpowers/specs/2026-07-24-esp32-remote-channel-list-design.md` (this
paged list, and the calibration rework), and
`docs/superpowers/specs/2026-07-24-esp32-remote-lcars-theme-design.md` (the
LCARS color palette — note the built firmware simplified that doc's
sidebar/header layout away after hardware review; see that spec's status
note) in the repo root for the full design rationale.

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

1. **Touch calibration** — the first boot ever (or after clearing NVS) shows
   three prompts in turn: "Tap TOP-LEFT", "Tap TOP-RIGHT", "Tap BOTTOM-LEFT".
   Tap each target, lifting your finger fully before the next prompt appears
   (calibration waits for a clean release between taps — see "Hardware
   notes" for why). The result is cached in flash (`Preferences`/NVS), so
   every later boot skips straight to the channel list. This is a 3-point
   calibration (not TFT_eSPI's built-in 4-corner one) — see "Hardware notes"
   below for why the touch controller needs its own separate handling on
   this board, and why 3 points rather than a simpler 2-corner diagonal.
2. **WiFi connect** — shows "Connecting WiFi...", then "Fetching
   channels..." once connected, or "WiFi connect failed" (it keeps retrying
   in the background; fix `data/config.txt` and re-run `pio run -t uploadfs`
   if it never connects).
3. **Channel list** — fetched once from pcpd's `/channels` endpoint (the
   same list `pcp guide` shows), 6 rows per page (zero-padded numbers, e.g.
   "01"), with PREV (rose) / NEXT (orange) buttons along the bottom in
   distinct colors (wrapping around at either end). The channel airing at
   boot time is highlighted in ice blue; tapping any row updates that
   highlight locally so it tracks whatever you last tuned to, without
   needing to re-fetch the whole list. The list itself is **not** re-fetched
   after boot — editing `channels.txt` on the Pi means power-cycling the
   remote to see the change here (`pcp guide`/telnet's `guide` always show
   the live version regardless).
4. Tap a row. Nothing shows on success — the CRT switching channel *is* the
   confirmation (check with `pcp now` on the Pi, or just look at the TV). A
   failed request shows a short error (`HTTP 404`, `no response`, etc.) for
   ~1.5s, then returns to the list — always safe to just tap again.

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

**A single tap can read as several, in two different ways** — worth knowing
if you're extending this firmware:
- *Contact bounce*: a resistive panel can report touched/released/touched
  within milliseconds from one physical tap. The main loop guards against
  this with a time-based cooldown (`TAP_COOLDOWN_MS`) rather than a simple
  "was it pressed last frame" latch.
- *No release-wait between prompts*: calibration originally showed its next
  target immediately after accepting a tap, so a finger still resting on the
  glass (or bouncing right after lift-off) would immediately satisfy the
  *next* target too, with nearly identical coordinates. Fixed by having
  `waitForStableRawTouch()` block until the finger actually lifts (plus a
  short settle delay) before returning.
- *Runtime hit-testing needs a settled sample, too*: the very first two-zone
  version of this firmware read a single, immediate `ts.getPoint()` per tap
  in the main loop — unlike calibration, which already double-sampled. That
  appears to have been the cause of a real bug where every tap resolved to
  the same zone regardless of where the screen was pressed: an unsettled
  first sample is noisy enough to consistently bias toward one end.
  `getStableTouch()` now double-samples runtime taps the same way
  calibration does.
