# pi-crt-player

Play YouTube videos on a small 4:3 CRT connected to a Raspberry Pi 4 (over HDMI),
controlled entirely over SSH. Built for Raspberry Pi OS Lite (trixie) — no
desktop environment required.

## Setup

On a fresh Pi:

```bash
git clone https://github.com/trg/pi-crt-player.git
cd pi-crt-player
./setup.sh
```

That's it — `setup.sh` starts the control daemon, so you're ready immediately.

## Usage

```bash
play space oddity david bowie   # play now (URL or search terms)
queue another great song        # add to the queue
now                             # what's playing + queue
next                            # skip to the next video
stop                            # stop and clear the queue
```

These are thin clients that talk to the always-running control daemon, so
playback is independent of your SSH session.

## Remote control (telnet)

The same daemon runs a no-login telnet server (port 23) so anyone on the
network can drive the TV:

```
telnet <pi-host>
```

```
======================================
        C R T   P L A Y E R
======================================
> search apollo 11 restored footage
 1) Apollo 11 Launch - Restored
 2) First Moon Landing 1969
> play 1
playing: Apollo 11 Launch - Restored
> queue 2
queued (#1): First Moon Landing 1969
```

Commands: `search`, `play <n|url|words>`, `queue <n|url|words>`, `list`,
`next`, `pause`, `stop`, `now`, `help`, `quit`.

It's an **open, unauthenticated** service — anyone who can reach the Pi can
control playback (a home-network remote, by design). Search terms are passed to
yt-dlp as argument lists, never through a shell, so they can't inject commands.
Manage it with `systemctl status/restart crt-player`.

## How it works

- A **control daemon** (`crt-player` systemd service, `server/pcpd.py`) owns a
  single **persistent mpv** instance and drives it over mpv's JSON IPC socket.
  Because mpv stays alive between videos, the screen never drops back to the
  console login, and `now` always reflects true state.
- The **queue** is managed by the daemon; when a video ends it auto-advances to
  the next one, or shows the idle "attract" screen — big green text (`play
  videos` / `telnet <host>.local`) so a guest knows how to drive it. (Drop a PNG
  at `/usr/local/lib/pi-crt-player/idle.png` for a background behind the text.)
- Two thin **frontends** talk to the daemon: the telnet server, and an
  **HTTP/JSON API** on `127.0.0.1:8677` (used by the `pcp` CLI, and ready to
  back a future box-hosted web UI — see [ROADMAP.md](ROADMAP.md)).
- **mpv** renders via DRM/KMS on the Pi's V3D GPU (`vo=gpu`, no X/Wayland);
  **yt-dlp** resolves YouTube. Output is pinned to **720x480** / **H.264 <=480p**
  for hardware decode with no expensive upscaling. See `config/mpv.conf`.

## Roadmap

See [ROADMAP.md](ROADMAP.md) — the long-term frontend-agnostic architecture
(telnet now, web later), more ways to get video onto the box (casting target +
non-DRM sources, and the DRM wall that blocks Paramount+/Hulu/HBO), a video
queue, and idle-screen / accurate-`now` improvements.
