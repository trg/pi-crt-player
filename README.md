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

## How it works

- **mpv** renders directly to the display via DRM/KMS using the Pi's V3D GPU
  (`vo=gpu`), so no X/Wayland is needed.
- **yt-dlp** resolves YouTube URLs; mpv calls it under the hood.
- Output is pinned to **720x480** and video is fetched as **H.264 <=480p** so the
  Pi's hardware decoder can be used and there's no expensive upscaling.

See `config/mpv.conf` for the tunables (resolution, aspect/panscan, format).

## Roadmap

- ncurses UI over telnet to search and queue videos.
