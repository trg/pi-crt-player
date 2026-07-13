# Roadmap

Ideas and design notes for where `pi-crt-player` goes next. Nothing here is
built yet; this is the thinking/planning doc.

---

## 0. Long-term architecture (keep in mind while building)

Today the control surface is telnet. Tomorrow it might be a **web page the box
hosts**, or a native app. So we should stop treating "telnet" as the product
and instead build a clean separation:

- **Control core** — a single long-lived daemon that owns playback + queue
  state (this is the persistent-mpv/IPC design in §2/§3). It exposes a stable
  command API: `play`, `stop`, `queue`, `list`, `next`, `now`, etc.
- **Frontends** — thin adapters that translate a UI into control-core commands:
  - *telnet* (today) — the line-based server becomes a thin adapter.
  - *web* (likely next) — a small page served by the box; easiest for guests,
    no telnet client needed.
  - *native app / API clients* (someday) — same API.

**Concrete recommendation:** make the control core speak **HTTP + JSON on
localhost** from the start. Even while only telnet uses it, that same API
*is* the future web app — a static page + a few `fetch()` calls served by the
box, no rewrite. The telnet server and a web UI then both just call the API.
Casting inputs (§1a) sit alongside as another way media arrives, independent of
the frontends.

Guiding rule: **no business logic in a frontend.** If telnet knows how to do
something the web UI can't, we've built it in the wrong layer.

---

## 1. More ways to get video onto the box

Two directions: **the box pulls** (search/fetch — today's model) and **the
phone pushes** (casting target — the new idea). Both hit the same wall for the
big paid services, so keep that front and center.

### The DRM wall (applies to pull *and* push)

Netflix, Hulu, HBO Max, Paramount+ protect video with **Widevine** (and Apple
**FairPlay**). Neither direction can get around it on a homemade box:

- **Pull:** yt-dlp refuses DRM streams (`This video is DRM protected`).
- **Push via Google Cast:** those apps only cast to *certified* Widevine
  devices; a DIY Pi receiver isn't certified, so they won't cast to it.
- **Push via AirPlay:** UxPlay (the open AirPlay receiver) explicitly cannot
  show DRM content — protected video "only plays back on the device itself"
  (black screen when mirrored).

So **Netflix/Hulu/HBO/Paramount+ are not achievable** without a licensed,
certified stack. Everything below is about doing well with what's actually
possible.

### 1a. Casting target — "throw it at the TV" (new idea, promising)

Make the station a receiver so the **phone becomes the source/remote**. Great
UX for the non-DRM world:

- **AirPlay receiver via [UxPlay](https://github.com/FDH2/UxPlay)** — runs on
  the Pi (GStreamer), tested on Pi 4/5. Mirrors the phone screen + audio, and
  receives AirPlay video for non-DRM content (personal videos, photos, web,
  YouTube-via-AirPlay). This is the standout option.
- **DLNA/UPnP renderer** (e.g. `gmediarender`) — "Play to" from apps/servers
  that expose non-DRM media.
- **Google Cast receiver** — low priority; the useful apps need certification.

Reality check: excellent for YouTube / personal media / AirPlay-able apps; will
**not** receive Netflix/Hulu/HBO (DRM, see above).

### 1b. Non-DRM sources via yt-dlp (still easy, fits current stack)

yt-dlp supports 1000+ sites, many DRM-free: Twitch, Vimeo, Internet Archive,
PBS, direct HLS/`.m3u8`, most IPTV playlists, free/ad-supported catalogs. Work
needed: relax the YouTube-only assumptions in `play`/`search` (per-provider
search prefixes) and surface the provider in the UI. No architecture change.

### 1c. DRM services natively (Paramount+/Hulu/HBO) — the browser stack

The *only* legitimate route, and a heavy one: **Chromium + Widevine CDM**
(`libwidevinecdm0`), which needs a display server (X/Wayland + a WM) added to
Pi OS Lite — essentially porting the sibling Chrome-box project onto the Pi.
Software Widevine = **Level 3 → SD/720p ceiling**, per-service logins, and
`xdotool`-style navigation. Options: **A.** leave DRM services on the existing
Chrome box; **B.** full port here (big lift, duplicates that project);
**C.** hybrid (mpv for non-DRM, Chromium only when a DRM service is asked for).

**Recommendation:** pursue **1a (AirPlay receiver)** + **1b (non-DRM sources)**
— the realistic, high-value directions. Treat **1c** as a separate, deliberate
call (lean toward option A). Casting DRM apps to the box is simply not possible.

Sources:
- [yt-dlp refuses DRM-protected streams (Hulu/Paramount+/Netflix)](https://en.vidjuice.com/how-to/online-downloader/how-to-resolve-yt-dlp-error-this-video-is-drm-protected/)
- [UxPlay — open AirPlay receiver; DRM content can't be mirrored](https://github.com/FDH2/UxPlay)
- [Installing Widevine DRM on Raspberry Pi (Chromium, Level 3)](https://pimylifeup.com/raspberry-pi-widevine/)

---

## 2. Video queue & basic queue management

**Current limitation:** `play` and `stop` are one-shot processes — each `play`
kills the old mpv and `setsid`s a new one. There's no shared, queryable player
state (the telnet server just keeps a `now_playing` string in memory), so
there's nowhere for a queue to live.

**Proposed architecture — one persistent mpv driven over its IPC socket.**
This is the "control core" from §0. mpv can run idle and accept commands on a
JSON socket
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

The control-core daemon wraps this socket and exposes the HTTP/JSON API; telnet
(and later web) are thin clients. New commands: `queue <url|search>`, `list`,
`next`, `clear`, maybe `shuffle`.

---

## 3. End-of-video behavior + accurate `now`

**What happens today:** when a video ends, mpv exits, and the CRT drops back to
the console login prompt. Also, `now` reports the last thing *launched* because
the telnet server sets `now_playing` on `play` and never clears it — so after a
video ends it still claims to be playing. This is a consequence of the one-shot
design, not a bug per se.

**Quick, standalone fix (no rearchitecting):** make `now` reflect reality —
check whether an mpv process is actually running (`pgrep -x mpv`); if not,
report "nothing playing." Cheap, immediately more honest.

**Better fix (falls out of §2 for free):** with one persistent idle mpv:
- It **never exits**, so the CRT never drops to the console login — when the
  queue empties, mpv sits on an idle window instead.
- `now` reads true state over IPC (`media-title` + `core-idle`/`idle-active`),
  always accurate.
- The idle screen becomes a feature: an "attract mode" — logo/splash, a clock,
  "telnet/visit me to play something" instructions, or a looping default
  playlist.

> §2 and §3 share the same solution — a single long-lived mpv (the control
> core) — so they should be built together, and that core is also what a future
> web UI (§0) will talk to.
