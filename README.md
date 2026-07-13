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

Then log out and back in once (so the `video`/`render` group membership takes
effect) and you're ready.

## Usage

```bash
play 'https://www.youtube.com/watch?v=...'   # play a URL
play space oddity david bowie                 # search, play the first hit
stop                                          # stop playback
```

Playback is detached from your SSH session, so it keeps going after you log out.

## Remote control (telnet)

`setup.sh` also installs **CRT Player**, a no-login telnet control server
(systemd service `crt-player`, port 23) so anyone on the network can drive the
TV:

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
```

Commands: `search <words>`, `play <n|url|words>`, `stop`, `now`, `help`, `quit`.

It's an **open, unauthenticated** service — anyone who can reach the Pi can
control playback (a home-network remote, by design). It only ever runs
`play`/`stop`/`yt-dlp` with argument lists, never a shell, so search terms
can't inject commands. Manage it with `systemctl status/restart crt-player`.

## How it works

- **mpv** renders directly to the display via DRM/KMS using the Pi's V3D GPU
  (`vo=gpu`), so no X/Wayland is needed.
- **yt-dlp** resolves YouTube URLs; mpv calls it under the hood.
- Output is pinned to **720x480** and video is fetched as **H.264 <=480p** so the
  Pi's hardware decoder can be used and there's no expensive upscaling.

See `config/mpv.conf` for the tunables (resolution, aspect/panscan, format).

## Roadmap

- Queue / playlist support (play several in a row).
- `now` should read the actual mpv title rather than the last thing launched.
