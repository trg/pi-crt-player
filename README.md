# pi-crt-player

Play YouTube videos on a small 4:3 CRT connected to a Raspberry Pi 4 (over HDMI).
The TV is driven over the network by **telnet** — no login, no app to install,
nothing to plug in. Anyone on the home network just connects and types. Built
for Raspberry Pi OS Lite (trixie) — no desktop environment required. (SSH is
only used to install and administer the box; see the bottom.)

When nothing is playing, the CRT shows a retro green "attract" screen telling
guests exactly how to connect:

```
        play videos
      $ telnet station
```

## Using it (telnet)

From any machine on the network:

```
telnet <pi-host>
```

You're dropped straight into the remote — no password:

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

Commands:

| Command                | What it does                                     |
|------------------------|--------------------------------------------------|
| `search <words>`       | search YouTube, numbered results                 |
| `play <n\|url\|words>` | play now (`n` picks from the last search)        |
| `queue <n\|url\|words>`| add to the queue (plays now if nothing is on)    |
| `list` / `now`         | show what's playing + the queue                  |
| `next`                 | skip to the next queued video (next channel programme while surfing) |
| `pause`                | pause / resume                                   |
| `stop`                 | stop and clear the queue (back to attract screen)|
| `clear`                | empty the queue, keep the current video playing  |
| `surf`                 | channel-surfing mode — tune in like a TV         |
| `ch up` / `ch down`    | flip to the next / previous channel while surfing|
| `channels` / `guide`   | the TV guide — list channels, mark what's on     |
| `help`                 | show the command list                            |
| `quit`                 | disconnect                                        |

It's an **open, unauthenticated** service — anyone who can reach the Pi can
control playback (a home-network remote, by design). Search terms are passed to
yt-dlp as argument lists, never through a shell, so they can't inject commands.

## Channel surfing

Type `surf` to turn the box into a TV. Each **channel is a YouTube channel** —
its uploads become a never-ending programme. Flip through them like a remote:

```
> surf
CH 1 — NASA
now airing: Artemis II Mission Overview
> ch up
CH 2 — Kurzgesagt
now airing: What Is Life?
> channels
TV guide:
 > CH  2  Kurzgesagt
   CH  1  NASA
   CH  3  Veritasium
   CH  4  NatGeo
   CH  5  Tiny Desk
```

- `ch up` / `ch down` flip channels (they wrap around). `next` jumps to the next
  programme on the current channel; `stop` (or any `play`/`queue`) leaves surf
  mode and returns to normal.
- When you tune in, a green **channel banner** (number, name, title) pops up on
  the CRT for a few seconds and then fades out, the way a real TV does.
- What's "on" is **deterministic from the wall clock**: each channel's uploads
  are laid out as one long broadcast loop and the current moment maps to a fixed
  position in it, so tuning in drops you *mid-programme* and everyone surfing at
  the same time sees the same thing airing — no per-viewer randomness. It's an
  illusion of live TV, not a real schedule (YouTube has none).

**The lineup** lives in `config/channels.json` (installed to
`/usr/local/lib/pi-crt-player/channels.json`). Each entry is a display `name`
plus either a `handle` (e.g. `@NASA`) or a full `url` to any yt-dlp-supported
channel/playlist; the order there is the channel order. Edit it and
`systemctl restart crt-player` to change channels. `setup.sh` installs it with
`cp -n`, so it never overwrites a lineup you've customised on the box. The
shipped list is a starter set — **swap in your own channels.**

> The initial set is a hardcoded lineup (the MVP). Turning your private YouTube
> **subscriptions** into channels needs logged-in cookies on the box — see
> [ROADMAP.md](ROADMAP.md) §4.

## How it works

- A **control daemon** (`crt-player` systemd service, `server/pcpd.py`) owns a
  single **persistent mpv** instance and drives it over mpv's JSON IPC socket.
  Because mpv stays alive between videos, the screen never drops back to the
  console login, and `now` always reflects true state.
- The **queue** is managed by the daemon; when a video ends it auto-advances to
  the next one, or shows the idle "attract" screen — big green text (`play
  videos` / `$ telnet <host>`) in a retro arcade pixel font (Press Start 2P,
  installed by `setup.sh`). It's drawn by mpv's OSD, so no image tooling is
  needed. (Drop a PNG at `/usr/local/lib/pi-crt-player/idle.png` for a
  background behind the text.)
- The telnet server is a thin **frontend** over the daemon. Alongside it the
  daemon exposes an **HTTP/JSON API** on `127.0.0.1:8677` — used by the admin
  CLI below, and ready to back a box-hosted web remote later (see
  [ROADMAP.md](ROADMAP.md)). No playback logic lives in a frontend.
- **mpv** renders via DRM/KMS on the Pi's V3D GPU (`vo=gpu`, no X/Wayland);
  **yt-dlp** resolves YouTube. Output is pinned to **720x480** / **H.264 <=480p**
  for hardware decode with no expensive upscaling. See `config/mpv.conf`.

## Setup & administration (SSH)

SSH is only for setting up and managing the box — day-to-day use is telnet.

On a fresh Pi:

```bash
git clone https://github.com/trg/pi-crt-player.git
cd pi-crt-player
./setup.sh
```

That's it — `setup.sh` installs everything and starts the control daemon, so the
telnet remote is live immediately (and comes back on every boot).

Manage the service with `systemctl status/restart crt-player`.

The same actions the telnet remote offers are also available as a local CLI
(handy when you're already on the box over SSH):

```bash
play space oddity david bowie   # play now (URL or search terms)
queue another great song        # add to the queue
now                             # what's playing + queue
next                            # skip to the next video
stop                            # stop and clear the queue
```

`play`, `queue`, `now`, `next`, and `stop` are installed as standalone commands.
The rest are reached through `pcp`:

```bash
pcp search apollo 11 restored   # numbered search results
pcp pause                       # pause / resume
pcp clear                       # empty the queue, keep the current video
pcp status                      # same as `now`
pcp surf                        # start channel surfing
pcp ch up                       # flip channels (also: pcp ch down)
pcp channels                    # the TV guide
```

These are thin clients over the daemon's HTTP API, so they work regardless of
your SSH session and stay in sync with whatever telnet users are doing.

## Roadmap

See [ROADMAP.md](ROADMAP.md) — the long-term frontend-agnostic architecture
(telnet now, web later), more ways to get video onto the box (casting target +
non-DRM sources, and the DRM wall that blocks Paramount+/Hulu/HBO), a video
queue, and idle-screen / accurate-`now` improvements.
