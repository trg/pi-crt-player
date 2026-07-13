# Roadmap

Ideas and design notes for where `pi-crt-player` goes next. Nothing here is
built yet; this is the thinking/planning doc.

---

## 1. Multiple providers

**Goal:** play from more than just YouTube — candidates named: Paramount+,
Hulu, HBO Max.

**The hard reality (researched):** our whole architecture is `mpv` + `yt-dlp`,
and **yt-dlp cannot play DRM-protected streams.** Paramount+, Hulu, HBO Max,
Netflix, Disney+, etc. all encrypt their video with **Widevine DRM**; yt-dlp
returns `This video is DRM protected` and refuses by design (it ships no
decryption module and won't circumvent DRM). So none of the three named
services will ever work through the current player. This is *why* the sibling
CRT project (the Intel/Ubuntu box) runs Google Chrome instead of mpv.

Providers therefore fall into two very different buckets:

### 1a. Non-DRM sources — cheap, fits what we have

yt-dlp already supports 1000+ sites, many with no DRM. These would work with
little more than relaxing the current YouTube-only assumptions in `play`:

- Twitch, Vimeo, Internet Archive, PBS, direct HLS/`.m3u8` streams, most
  IPTV playlists, and many free/ad-supported catalogs.
- Work needed: let `play`/`search` target a chosen site (yt-dlp's
  `--default-search`, or per-provider search prefixes), and surface the
  provider in the telnet UI. Low effort, no architecture change.

### 1b. DRM services (Paramount+, Hulu, HBO Max) — expensive, new stack

Requires a **Widevine-capable browser**, not mpv:

- Install Chromium + `libwidevinecdm0` (Raspberry Pi OS packages it). Note:
  software Widevine = **Level 3 → SD/720p ceiling**, no 1080p/HDR.
- Chromium needs a display server. Pi OS Lite has none, so this means adding a
  minimal X/Wayland + window manager (openbox) stack — essentially porting the
  sibling project's approach onto the Pi.
- Each service needs its own **login/persistent profile**, and navigation is
  per-service (deep-links + `xdotool`), so the clean `search`/`play` model
  doesn't map onto it cleanly.

**Options to decide between:**
- **A. Stay focused.** Keep the Pi as a lean YouTube + non-DRM appliance; do
  the DRM services on the existing Chrome box. Least work, keeps this project
  clean.
- **B. Full port.** Add the Chromium+Widevine+X stack here too. Big lift;
  largely duplicates the sibling project.
- **C. Hybrid.** mpv for YouTube/non-DRM, Chromium only when a DRM service is
  requested, switching the display between them. Most flexible, most complex.

**Recommendation:** do **1a** (non-DRM providers) now — it's genuinely easy and
high-value — and treat **1b** as a separate, deliberate decision (lean toward
option A unless the Pi specifically needs to replace the Chrome box).

Sources:
- [yt-dlp cannot download DRM-protected streams (Hulu/Paramount+/Netflix)](https://en.vidjuice.com/how-to/online-downloader/how-to-resolve-yt-dlp-error-this-video-is-drm-protected/)
- [Installing Widevine DRM on Raspberry Pi (Chromium, Level 3)](https://pimylifeup.com/raspberry-pi-widevine/)
- [Official Raspberry Pi Widevine support — Netflix/Hulu/Prime](https://www.tomshardware.com/news/raspberry-pi-widevine)
- [pivine — Chromium + Widevine installer for Pi](https://github.com/xesco/pivine)

---

## 2. Video queue & basic queue management

**Current limitation:** `play` and `stop` are one-shot processes — each `play`
kills the old mpv and `setsid`s a new one. There's no shared, queryable player
state (the telnet server just keeps a `now_playing` string in memory), so
there's nowhere for a queue to live.

**Proposed architecture — one persistent mpv driven over its IPC socket.**
mpv can run idle and accept commands on a JSON socket
(`mpv --idle=yes --force-window=yes --input-ipc-server=/run/user/<uid>/mpv.sock`).
Then everything becomes an IPC message instead of a new process:

| Action        | mpv IPC                                             |
|---------------|-----------------------------------------------------|
| play (now)    | `loadfile <url> replace`                            |
| queue / add   | `loadfile <url> append`                             |
| list          | read `playlist` property                            |
| next / skip   | `playlist-next`                                     |
| remove / clear| `playlist-remove` / `playlist-clear`               |
| stop          | `stop` (stays idle, doesn't exit)                   |

This is the clean foundation: `play`/`stop`/`server` all talk to the one mpv
instead of spawning processes, and it *also* fixes item #3 below.

**New commands (telnet + CLI):** `queue <url|search>` (add), `list`, `next`,
`clear`, maybe `shuffle`. yt-dlp resolves each entry to a stream before
`loadfile` (or let mpv's ytdl hook do it on append).

---

## 3. End-of-video behavior + accurate `now`

**What happens today:** when a video ends, mpv exits, and the CRT drops back to
the console login prompt. Also, `now` reports the last thing *launched* because
the telnet server sets `now_playing` on `play` and never clears it — so after a
video ends it still claims to be playing. As noted, this isn't a bug so much as
a consequence of the one-shot design.

**Quick, standalone fix (no rearchitecting):** make `now` reflect reality —
check whether an mpv process is actually running (`pgrep -x mpv`); if not,
report "nothing playing." Cheap, immediately more honest.

**Better fix (falls out of #2 for free):** with one persistent idle mpv:
- It **never exits**, so the CRT never drops to the console login — when the
  queue empties, mpv sits on an idle window instead.
- `now` reads the true state over IPC (`media-title` + `core-idle`/`idle-active`),
  so it's always accurate.
- The idle screen becomes a feature: show a branded "attract mode" —
  a logo/splash image, a clock, "telnet me to play something" instructions, or
  a looping default playlist. (mpv `--idle` with a background image, or an
  `--image-display-duration` splash.)

**Related polish:** if we don't go persistent, at least replace the bare
console login on idle with a splash/attract screen (hide the getty on tty1 or
draw an image to the framebuffer).

> Items #2 and #3 share the same solution — a single long-lived mpv controlled
> over IPC — so they should probably be built together.
