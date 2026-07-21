# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Turns a Raspberry Pi 4 + small CRT (over HDMI) into a telnet-controlled video
player: `telnet <pi-host>` gets you search/play/queue for YouTube, plus a
"channel surfing" mode that turns YouTube channels into simulated live TV.
No auth — anyone on the LAN can control it, by design.

## Commands

No build step, no test suite, no linter — this is dependency-free stdlib
Python 3 deployed straight onto Pi OS Lite. Sanity-check syntax before
committing:

```bash
python3 -m py_compile server/pcpd.py
```

Deploy/install (fresh Pi only — reinstalls the daemon, CLI, fonts, systemd unit):

```bash
./setup.sh
```

On the box:

```bash
systemctl status crt-player      # daemon status
systemctl restart crt-player     # after changing server/pcpd.py (needs ./setup.sh to sync the copy)
journalctl -u crt-player -f      # logs
```

Editing `config/channels.txt` needs **no** restart — the daemon polls its
mtime and reloads on the next `surf`/`ch`/`guide`. Editing `server/pcpd.py`
does — rerun `./setup.sh` (it copies the file to
`/usr/local/lib/pi-crt-player/pcpd.py`) or `systemctl restart crt-player`
after manually syncing.

## Architecture

**Control core / frontend split** (see ROADMAP.md §0): all playback logic
lives in one place; frontends are thin translators with zero business logic.

- **`server/pcpd.py`** — the whole daemon (`crt-player` systemd service). `main()`
  spawns the single `mpv` subprocess directly (persistent, `--idle=yes`, driven
  over its JSON IPC socket) and watches it — if mpv dies unexpectedly the whole
  process exits so systemd restarts the stack. Owns:
  - `Mpv` — async JSON-IPC client to that `mpv` process (kept alive between
    videos so `now` always reflects true state and the screen never drops to
    a console login).
  - `Controller` — all playback state and logic: queue, play/pause/stop,
    channel surfing. Everything else is a thin adapter over it.
  - A **telnet server** (port 23) — the retro remote, line-based.
  - An **HTTP/JSON API** (`127.0.0.1:8677`) — used by the `pcp` CLI today,
    designed to back a future box-hosted web UI without changes to the core.
  - When adding a feature, add it to `Controller` first, then expose it from
    both frontends — never put logic only in telnet or only in HTTP.

- **`bin/pcp`** — CLI client, thin wrapper over the HTTP API. Installed as
  `pcp` plus symlinks `play`/`stop`/`queue`/`next`/`now` (dispatches on
  `argv[0]`).

- **`server/player.py`** — an earlier, simpler telnet-only prototype
  (shells out to standalone `play`/`stop` scripts, no queue, no persistent
  mpv). Superseded by `pcpd.py` and not installed by `setup.sh` or referenced
  by the systemd unit; kept around as a historical reference, not live code.

- **`config/channels.txt`** — the channel-surfing lineup, one channel per
  line (`<@handle-or-URL> [| Display Name]`), read live from the repo
  checkout path (not copied during install) so a `git pull` updates the
  lineup with no reinstall. Order = channel number. See the format comment
  at the top of the file.

- **`config/mpv.conf`** — CRT/Pi-4-specific tuning: DRM/KMS output pinned to
  `720x480`, hardware H.264 decode (`v4l2m2m-copy`), forces `<=480p` avc1 from
  yt-dlp (the Pi 4 has no VP9/AV1 hardware decode), panscan to fill 4:3.

- **`systemd/crt-player.service`** — template unit; `setup.sh` substitutes
  `@USER@` and `@CHANNELS@` (absolute path to the repo's `channels.txt`)
  before installing it.

### Channel surfing (`Controller` §"channel surfing")

Each channel is a YouTube channel/playlist; its uploads become an endless
programming loop. "What's on" is **deterministic from the wall clock**:
total loop runtime = sum of video durations, current position = `time.time()
% total`, so everyone tuning in at the same moment sees the same thing
airing — an illusion of live TV, not per-viewer randomness. Channel listings
are cached with a TTL (`CHANNEL_TTL`) and warmed in the background at boot
(`Controller.warm_channels`, one channel at a time to avoid pegging the Pi's
CPU). A green ASS-drawn banner (`_info_ass`/`_announce`) shows channel/title
on tune-in and fades out like a real TV, timed off the `file-loaded` mpv
event rather than a fixed delay.

### Idle / attract screen

When nothing is playing, mpv draws a green "attract" overlay via its OSD
(libass, `Controller._idle_ass`) — no image tooling needed. Small virtual
canvas (`IDLE_RES_X`/`IDLE_RES_Y`) keeps glyphs large and CRT-readable
(Press Start 2P font, installed by `setup.sh`). It shows how to connect:
`$ telnet <hostname>`, a fallback `<LAN IPv4>` (via `_local_ipv4()` — never
`socket.gethostbyname(hostname)`, which resolves to the Debian `/etc/hosts`
loopback alias `127.0.1.1`, not the real address), and the wifi SSID
(`_wifi_ssid()`, tries `nmcli` then `iwgetid`, `None` on wired/no-wifi boxes).
This info is cached on `Controller._net` and refreshed every `NET_INFO_TTL`
seconds by `Controller._watch_net()`, redrawing the overlay only while it's
actually on screen.

## Security note (intentional, not a bug)

The telnet and HTTP surfaces are deliberately unauthenticated (home-network
remote). Search/play input reaches `yt-dlp`/`mpv` only via `asyncio.create_subprocess_exec`
argument lists, never a shell string — preserve that pattern for any new
subprocess calls so network input can't inject commands.

## Roadmap

See `ROADMAP.md` for the design rationale behind the frontend/core split, the
DRM wall that blocks Netflix/HBO/Paramount+ natively, and planned work (web
frontend, casting receiver, subscriptions-as-channels).
