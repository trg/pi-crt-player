#!/usr/bin/env python3
"""pi-crt-player control daemon (pcpd) — the "control core".

Owns a single persistent mpv instance (driven over its JSON IPC socket),
manages the video queue, and exposes that state through two thin frontends:

  * a line-based telnet server (port 23) — the retro remote
  * an HTTP/JSON API (localhost) — used by the play/stop/queue/next/now CLI
    tools today, and ready to back a box-hosted web UI tomorrow

All playback logic lives in Controller; the frontends only translate input
into Controller calls, so adding a web UI later needs no changes here.

Because mpv stays alive (idle) between videos, the screen never drops back to
the console login, and `now` always reflects true state.
"""
import asyncio
import json
import os
import socket
import time

MPV = "/usr/bin/mpv"
YT_DLP = "/usr/local/bin/yt-dlp"
MPV_SOCK = os.environ.get("MPV_SOCK", "/run/pi-crt-player/mpv.sock")
TELNET_PORT = int(os.environ.get("PLAYER_PORT", "23"))
HTTP_HOST = os.environ.get("HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8677"))
IDLE_IMAGE = os.environ.get("IDLE_IMAGE", "/usr/local/lib/pi-crt-player/idle.png")
SEARCH_COUNT = 8

# ---- channel surfing (see ROADMAP §4) ----
# A "channel" is a YouTube channel; its uploads become a never-ending stream.
# The lineup is a small, hardcoded config file so it's deterministic, needs no
# auth, and survives reboots. Edit CHANNELS_FILE to change it.
CHANNELS_FILE = os.environ.get(
    "CHANNELS_FILE", "/usr/local/lib/pi-crt-player/channels.json")
CHANNEL_TTL = int(os.environ.get("CHANNEL_TTL", str(6 * 3600)))  # cache uploads
CHANNEL_MAX = 60           # videos pulled per channel (the programming loop)
DEFAULT_PROG_SECS = 600    # assumed length when yt-dlp reports no duration
# Starter lineup — REPLACE with your own channels (or edit channels.json).
DEFAULT_CHANNELS = [
    {"name": "NASA", "handle": "@NASA"},
    {"name": "Kurzgesagt", "handle": "@kurzgesagt"},
    {"name": "Veritasium", "handle": "@veritasium"},
    {"name": "NatGeo", "handle": "@NatGeo"},
    {"name": "Tiny Desk", "handle": "@nprmusic"},
]

# Channel-info banner: a channel/title overlay that pops up when you tune in and
# fades out after a few seconds, like a TV. Drawn on the same small virtual
# canvas as the idle screen (see Controller._idle_ass) for CRT-readable glyphs.
INFO_OVERLAY_ID = 48
INFO_HOLD = float(os.environ.get("INFO_HOLD", "5"))   # seconds fully visible
INFO_FADE = float(os.environ.get("INFO_FADE", "1.2"))  # seconds to fade out

# Idle "attract" screen: big green text drawn by mpv's OSD (libass) when
# nothing is playing. Small virtual canvas => large, CRT-readable glyphs; text
# is centred to stay clear of CRT overscan. See Controller._idle_overlay.
IDLE_OVERLAY_ID = 47
IDLE_RES_X = 320
IDLE_RES_Y = 240
IDLE_FONT = os.environ.get("IDLE_FONT", "Press Start 2P")  # installed by setup.sh

BANNER = (
    "\r\n"
    "======================================\r\n"
    "        C R T   P L A Y E R\r\n"
    "======================================\r\n"
    "Play YouTube videos on the TV. No login needed.\r\n"
)

HELP = (
    "Commands:\r\n"
    "  search <words>       search YouTube (numbered list)\r\n"
    "  play <n|url|words>   play now (n = from last search)\r\n"
    "  queue <n|url|words>  add to the queue\r\n"
    "  list                 show the queue\r\n"
    "  next                 skip to the next video\r\n"
    "  pause                pause / resume\r\n"
    "  stop                 stop and clear the queue\r\n"
    "  clear                empty the queue, keep the current video\r\n"
    "  now                  what's playing + queue\r\n"
    "  surf                 channel-surfing mode (live TV feel)\r\n"
    "  ch up | ch down      flip channels while surfing\r\n"
    "  channels | guide     the TV guide (list channels)\r\n"
    "  help                 show this help\r\n"
    "  quit                 disconnect\r\n"
)


def load_channels():
    """Load the channel lineup from CHANNELS_FILE, falling back to defaults.

    Each entry needs a display "name" and either a full "url" (any yt-dlp
    playlist/channel URL) or a YouTube "handle" (e.g. "@NASA"), which we turn
    into the channel's uploads (/videos) feed.
    """
    raw = None
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                raw = data.get("channels")
        except (OSError, ValueError):
            raw = None
    if not raw:
        raw = DEFAULT_CHANNELS

    channels = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        url = (c.get("url") or "").strip()
        handle = (c.get("handle") or "").strip()
        if not url and handle:
            h = handle if handle.startswith("@") else "@" + handle
            url = f"https://www.youtube.com/{h}/videos"
        if url:
            channels.append({"name": name or url, "url": url})
    return channels


# --------------------------------------------------------------------------
# mpv JSON IPC client
# --------------------------------------------------------------------------
class Mpv:
    def __init__(self, sock_path):
        self.sock_path = sock_path
        self.reader = None
        self.writer = None
        self._id = 0
        self._pending = {}
        self.on_event = None

    async def connect(self, retries=200):
        last = None
        for _ in range(retries):
            try:
                self.reader, self.writer = await asyncio.open_unix_connection(
                    self.sock_path)
                break
            except (FileNotFoundError, ConnectionRefusedError) as e:
                last = e
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError(f"cannot connect to mpv socket {self.sock_path}: {last}")
        asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        while True:
            try:
                line = await self.reader.readline()
            except (ConnectionResetError, asyncio.IncompleteReadError):
                break
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            rid = msg.get("request_id")
            if rid in self._pending:
                fut = self._pending.pop(rid)
                if not fut.done():
                    fut.set_result(msg)
            elif "event" in msg and self.on_event:
                await self.on_event(msg)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("mpv socket closed"))
        self._pending.clear()

    async def command(self, *args):
        self._id += 1
        rid = self._id
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = json.dumps({"command": list(args), "request_id": rid}) + "\n"
        self.writer.write(payload.encode("utf-8"))
        await self.writer.drain()
        try:
            msg = await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return None
        return msg.get("data")

    async def set_property(self, name, value):
        return await self.command("set_property", name, value)


# --------------------------------------------------------------------------
# Playback control core
# --------------------------------------------------------------------------
class Controller:
    def __init__(self, mpv):
        self.mpv = mpv
        self.lock = asyncio.Lock()
        self.queue = []      # upcoming: list of {"title", "url"}
        self.now = None      # currently playing {"title", "url"} or None
        self.paused = False

        # channel surfing
        self.channels = load_channels()
        self.surfing = False
        self.channel_idx = 0           # which channel we're tuned to
        self.prog_idx = 0              # position within that channel's loop
        self._chan_cache = {}          # channel_idx -> (fetched_ts, [videos])
        self._pending_seek = None      # seconds to seek to once the file loads
        self._info_task = None         # background task drawing the info banner

    # ---- yt-dlp resolution (network; kept outside the lock) ----
    async def _yt(self, *args, timeout=45):
        proc = await asyncio.create_subprocess_exec(
            YT_DLP, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return ""
        return out.decode("utf-8", "replace")

    async def search(self, q, count=SEARCH_COUNT):
        out = await self._yt(
            "--flat-playlist", "--no-warnings",
            "--print", "%(title)s\t%(id)s",
            f"ytsearch{count}:{q}",
        )
        results = []
        for line in out.splitlines():
            if "\t" in line:
                title, vid = line.rsplit("\t", 1)
                if vid.strip():
                    results.append({
                        "title": title.strip(),
                        "url": f"https://www.youtube.com/watch?v={vid.strip()}",
                    })
        return results

    async def resolve(self, q):
        q = q.strip()
        if q.startswith("http"):
            out = await self._yt("--no-warnings", "--print", "%(title)s", q,
                                  timeout=30)
            title = out.strip().splitlines()[0] if out.strip() else q
            return {"title": title, "url": q}
        hits = await self.search(q, 1)
        return hits[0] if hits else None

    # ---- idle "attract" screen (green text over a black window) ----
    def _idle_ass(self):
        host = socket.gethostname() or "this-box"
        cx = IDLE_RES_X // 2
        # Press Start 2P is an ~square, wide pixel font, so text is laid out in
        # two centred lines and the (variable-length) command line is auto-shrunk
        # to fit the tube. \fn falls back to the default face if the font is
        # missing, so the screen still works before setup installs it.
        base = (r"\an5\fn%s\bord2\shad0\blur0.6"
                r"\1c&H00FF00&\3c&H001500&") % IDLE_FONT
        cmd = "$ telnet " + host
        cmd_fs = max(9, min(20, (IDLE_RES_X - 24) // max(len(cmd), 1)))
        line1 = r"{%s\pos(%d,%d)\fs26}play videos" % (base, cx, 96)
        line2 = r"{%s\pos(%d,%d)\fs%d}%s" % (base, cx, 152, cmd_fs, cmd)
        return line1 + "\n" + line2

    async def _show_idle_overlay(self):
        await self.mpv.command(
            "osd-overlay", IDLE_OVERLAY_ID, "ass-events", self._idle_ass(),
            IDLE_RES_X, IDLE_RES_Y, 0, False)

    async def _hide_idle_overlay(self):
        await self.mpv.command(
            "osd-overlay", IDLE_OVERLAY_ID, "none", "",
            IDLE_RES_X, IDLE_RES_Y, 0, False)

    # ---- playback primitives (hold the lock) ----
    async def _load(self, item):
        self.now = item
        self.paused = False
        self.surfing = False          # any explicit play/queue leaves surf mode
        self._pending_seek = None
        await self._clear_info()
        await self._hide_idle_overlay()
        await self.mpv.command("loadfile", item["url"], "replace")
        await self.mpv.set_property("pause", False)

    async def _go_idle(self):
        self.now = None
        self.paused = False
        self.surfing = False
        self._pending_seek = None
        await self._clear_info()
        if os.path.exists(IDLE_IMAGE):
            await self.mpv.command("loadfile", IDLE_IMAGE, "replace")
        else:
            await self.mpv.command("stop")
        await self._show_idle_overlay()

    # ---- public actions ----
    async def play(self, q):
        item = await self.resolve(q)
        if not item:
            return {"error": "no results"}
        return await self.play_item(item)

    async def play_item(self, item):
        async with self.lock:
            await self._load(item)   # interrupt now; queue is left intact
        return {"now": item}

    async def enqueue(self, q):
        item = await self.resolve(q)
        if not item:
            return {"error": "no results"}
        return await self.enqueue_item(item)

    async def enqueue_item(self, item):
        async with self.lock:
            if self.now is None:
                await self._load(item)
                return {"now": item}
            self.queue.append(item)
            return {"queued": item, "position": len(self.queue)}

    async def advance(self):
        async with self.lock:
            if self.queue:
                await self._load(self.queue.pop(0))
            else:
                await self._go_idle()

    async def next(self):
        if self.surfing:
            await self._advance_program()   # skip to the next programme
        else:
            await self.advance()
        return self.status()

    async def stop(self):
        async with self.lock:
            self.queue.clear()
            await self._go_idle()
        return {"ok": True}

    async def clear(self):
        async with self.lock:
            self.queue.clear()
        return {"ok": True}

    async def pause(self):
        async with self.lock:
            if self.now is None:
                return {"error": "nothing playing"}
            self.paused = not self.paused
            await self.mpv.set_property("pause", self.paused)
            return {"paused": self.paused}

    def status(self):
        st = {
            "now": self.now,
            "playing": self.now is not None,
            "paused": self.paused,
            "queue": list(self.queue),
            "surfing": self.surfing,
        }
        if self.surfing and self.channels:
            ch = self.channels[self.channel_idx]
            st["channel"] = {"num": self.channel_idx + 1, "name": ch["name"]}
        return st

    # ------------------------------------------------------------------
    # channel surfing (see ROADMAP §4)
    # ------------------------------------------------------------------
    async def _yt_channel(self, url):
        """Resolve a channel URL to its programming loop: [{title,url,dur,est}]."""
        out = await self._yt(
            "--flat-playlist", "--no-warnings",
            "--playlist-end", str(CHANNEL_MAX),
            "--print", "%(title)s\t%(id)s\t%(duration)s",
            url, timeout=60,
        )
        videos = []
        for line in out.splitlines():
            cols = line.split("\t")
            if len(cols) < 2 or not cols[1].strip():
                continue
            vid = cols[1].strip()
            try:
                dur = int(float(cols[2]))
            except (ValueError, IndexError):
                dur = 0
            est = dur <= 0            # unknown length -> we only estimate it
            videos.append({
                "title": cols[0].strip() or "(untitled)",
                "url": f"https://www.youtube.com/watch?v={vid}",
                "dur": dur if dur > 0 else DEFAULT_PROG_SECS,
                "est": est,
            })
        return videos

    async def _channel_videos(self, idx):
        """Cached programming for a channel (TTL) so surfing doesn't re-fetch."""
        ent = self._chan_cache.get(idx)
        if ent and (time.time() - ent[0]) < CHANNEL_TTL and ent[1]:
            return ent[1]
        videos = await self._yt_channel(self.channels[idx]["url"])
        if videos:
            self._chan_cache[idx] = (time.time(), videos)
        return videos

    def _schedule(self, videos):
        """Deterministic (programme index, offset) from the wall clock.

        The channel's loop is treated as an endless broadcast: total runtime is
        the sum of the videos' durations, and 'now' maps to a fixed position in
        that timeline. Everyone surfing at the same moment sees the same thing
        'airing' — no per-viewer randomness. Videos with unknown length still
        occupy a slot (DEFAULT_PROG_SECS) but we don't seek into them (est).
        """
        total = sum(v["dur"] for v in videos)
        if total <= 0:
            return 0, 0
        pos = time.time() % total
        for i, v in enumerate(videos):
            if pos < v["dur"]:
                return i, (0 if v["est"] else int(pos))
            pos -= v["dur"]
        return 0, 0

    async def _load_program(self, item, offset):
        """Load a channel programme, seeking to `offset` once it has loaded."""
        self.now = {"title": item["title"], "url": item["url"]}
        self.paused = False
        self._pending_seek = offset if offset > 0 else None
        await self._hide_idle_overlay()
        await self.mpv.command("loadfile", item["url"], "replace")
        await self.mpv.set_property("pause", False)

    async def _tune(self, idx):
        """Tune to channel `idx`: play whatever is 'airing' there right now."""
        if not self.channels:
            return {"error": "no channels configured"}
        videos = await self._channel_videos(idx)   # network; outside the lock
        if not videos:
            return {"error": f"channel '{self.channels[idx]['name']}' unavailable"}
        self.channel_idx = idx
        self.surfing = True
        self.prog_idx, offset = self._schedule(videos)
        item = videos[self.prog_idx]
        async with self.lock:
            await self._load_program(item, offset)
        self._announce(idx, item)
        return {"channel": {"num": idx + 1, "name": self.channels[idx]["name"]},
                "now": {"title": item["title"], "url": item["url"]}}

    async def surf(self):
        """Enter channel-surfing mode, tuned to the current channel."""
        return await self._tune(self.channel_idx)

    async def channel_step(self, delta):
        """Flip channels (delta +1 = up, -1 = down); wraps around the lineup."""
        if not self.channels:
            return {"error": "no channels configured"}
        base = self.channel_idx if self.surfing else self.channel_idx - delta
        return await self._tune((base + delta) % len(self.channels))

    async def _advance_program(self):
        """A programme ended (or `next` while surfing) -> next one on-channel."""
        videos = await self._channel_videos(self.channel_idx)
        if not videos:
            async with self.lock:
                await self._go_idle()
            return
        self.prog_idx = (self.prog_idx + 1) % len(videos)
        item = videos[self.prog_idx]
        async with self.lock:
            await self._load_program(item, 0)
        self._announce(self.channel_idx, item)

    def channels_info(self):
        """TV guide data: the lineup, which channel is on, what's airing."""
        cur = self.channel_idx if self.surfing else None
        chans = [{"num": i + 1, "name": c["name"], "current": (i == cur)}
                 for i, c in enumerate(self.channels)]
        return {"channels": chans, "surfing": self.surfing,
                "now": self.now if self.surfing else None}

    # ---- channel-info banner (pops up on tune, then fades like a TV) ----
    @staticmethod
    def _ass_escape(s):
        # Keep user/video text from breaking the ASS override syntax.
        return s.replace("\\", "/").replace("{", "(").replace("}", ")")

    def _info_ass(self, idx, item, alpha="00"):
        ch = self.channels[idx]
        cx = IDLE_RES_X // 2
        base = (r"\an5\fn%s\bord2\shad0\blur0.6\alpha&H%s&"
                r"\1c&H00FF00&\3c&H001500&") % (IDLE_FONT, alpha)
        header = self._ass_escape("CH%d  %s" % (idx + 1, ch["name"].upper()))
        title = self._ass_escape(item["title"])
        h_fs = max(9, min(18, (IDLE_RES_X - 24) // max(len(header), 1)))
        t_fs = max(8, min(14, (IDLE_RES_X - 16) // max(len(title), 1)))
        l1 = r"{%s\pos(%d,%d)\fs%d}%s" % (base, cx, 176, h_fs, header)
        l2 = r"{%s\pos(%d,%d)\fs%d}%s" % (base, cx, 204, t_fs, title)
        return l1 + "\n" + l2

    async def _show_info(self, ass):
        await self.mpv.command(
            "osd-overlay", INFO_OVERLAY_ID, "ass-events", ass,
            IDLE_RES_X, IDLE_RES_Y, 0, False)

    async def _hide_info(self):
        await self.mpv.command(
            "osd-overlay", INFO_OVERLAY_ID, "none", "",
            IDLE_RES_X, IDLE_RES_Y, 0, False)

    async def _clear_info(self):
        if self._info_task and not self._info_task.done():
            self._info_task.cancel()
        self._info_task = None
        await self._hide_info()

    def _announce(self, idx, item):
        # Replace any in-flight banner (fast surfing) with the new one.
        if self._info_task and not self._info_task.done():
            self._info_task.cancel()
        self._info_task = asyncio.create_task(self._info_banner(idx, item))

    async def _info_banner(self, idx, item):
        try:
            await self._show_info(self._info_ass(idx, item, "00"))
            await asyncio.sleep(INFO_HOLD)
            steps = 8
            for s in range(1, steps + 1):
                alpha = "%02X" % min(255, int(255 * s / steps))
                await self._show_info(self._info_ass(idx, item, alpha))
                await asyncio.sleep(INFO_FADE / steps)
            await self._hide_info()
        except asyncio.CancelledError:
            # A newer banner is taking over — it will redraw the overlay.
            raise

    # ---- mpv events: seek on load, auto-advance when a video finishes ----
    async def on_event(self, ev):
        # This runs inside the mpv socket read loop, so it must NOT await any
        # mpv command (the reply is read by this same loop) or a slow network
        # fetch — either would stall event delivery. Hand the work to a task.
        e = ev.get("event")
        if e == "file-loaded" and self._pending_seek is not None:
            # Drop into the programme mid-broadcast (the "live TV" illusion).
            offset, self._pending_seek = self._pending_seek, None
            asyncio.create_task(self.mpv.command("seek", offset, "absolute"))
        elif e == "end-file" and ev.get("reason") in ("eof", "error"):
            # Natural end (or a broken file) -> move on. Our own loadfile/stop
            # produce reason "stop"/"redirect", which we deliberately ignore.
            asyncio.create_task(
                self._advance_program() if self.surfing else self.advance())


# --------------------------------------------------------------------------
# telnet frontend
# --------------------------------------------------------------------------
def strip_telnet(data: bytes) -> bytes:
    """Drop telnet IAC negotiation sequences so they don't leak into commands."""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 255:
            nxt = data[i + 1] if i + 1 < len(data) else 0
            if nxt == 255:
                out.append(255)
                i += 2
            elif nxt in (251, 252, 253, 254):
                i += 3
            else:
                i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


def fmt_action(r):
    if r.get("error"):
        return f"error: {r['error']}\r\n"
    if r.get("now"):
        return f"playing: {r['now']['title']}\r\n"
    if "queued" in r:
        return f"queued (#{r['position']}): {r['queued']['title']}\r\n"
    return "ok\r\n"


def fmt_status(st):
    now = st.get("now")
    if now:
        lines = ["now playing: " + now["title"]
                 + (" (paused)" if st.get("paused") else "")]
    else:
        lines = ["nothing playing"]
    ch = st.get("channel")
    if st.get("surfing") and ch:
        lines[0] = f"surfing CH {ch['num']} ({ch['name']}) — " + (
            now["title"] if now else "…")
    q = st.get("queue", [])
    if q:
        lines.append("up next:")
        for i, it in enumerate(q, 1):
            lines.append(f"  {i}) {it['title']}")
    return "\r\n".join(lines) + "\r\n"


def fmt_surf(r):
    if r.get("error"):
        return f"error: {r['error']}\r\n"
    ch, now = r.get("channel"), r.get("now")
    out = ""
    if ch:
        out += f"CH {ch['num']} — {ch['name']}\r\n"
    if now:
        out += f"now airing: {now['title']}\r\n"
    return out or "ok\r\n"


def fmt_channels(info):
    lines = ["TV guide:"]
    for c in info.get("channels", []):
        mark = ">" if c["current"] else " "
        lines.append(f" {mark} CH {c['num']:>2}  {c['name']}")
    if info.get("surfing") and info.get("now"):
        lines.append("now airing: " + info["now"]["title"])
    else:
        lines.append("type 'surf' to start, then 'ch up' / 'ch down'.")
    return "\r\n".join(lines) + "\r\n"


def telnet_handler(controller):
    async def handle(reader, writer):
        results = []

        def send(text):
            writer.write(text.encode("utf-8", "replace"))

        async def flush():
            try:
                await writer.drain()
                return True
            except (ConnectionResetError, BrokenPipeError):
                return False

        send(BANNER)
        send(HELP)
        send("\r\n> ")
        if not await flush():
            return

        while True:
            try:
                raw = await reader.readuntil(b"\n")
            except asyncio.IncompleteReadError:
                break
            except asyncio.LimitOverrunError:
                send("line too long.\r\n> ")
                if not await flush():
                    break
                continue
            except (ConnectionResetError, BrokenPipeError):
                break
            if not raw:
                break

            line = strip_telnet(raw).decode("utf-8", "replace").strip()
            if not line:
                send("> ")
                if not await flush():
                    break
                continue

            parts = line.split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("help", "?"):
                send(HELP)
            elif cmd in ("quit", "exit", "q"):
                send("bye!\r\n")
                break
            elif cmd == "search":
                if not arg:
                    send("usage: search <words>\r\n")
                else:
                    send(f"searching for: {arg} ...\r\n")
                    if not await flush():
                        break
                    results = await controller.search(arg)
                    if not results:
                        send("no results.\r\n")
                    else:
                        for i, it in enumerate(results, 1):
                            send(f"{i:2}) {it['title']}\r\n")
                        send("play <n> to play, queue <n> to add.\r\n")
            elif cmd in ("play", "queue"):
                item = use_q = None
                if not arg:
                    send(f"usage: {cmd} <n | url | words>\r\n")
                elif arg.isdigit():
                    n = int(arg)
                    if 1 <= n <= len(results):
                        item = results[n - 1]
                    else:
                        send("no such result — search first.\r\n")
                else:
                    use_q = arg
                if item is not None or use_q is not None:
                    send("working...\r\n")
                    if not await flush():
                        break
                    if item is not None:
                        r = await (controller.play_item(item) if cmd == "play"
                                   else controller.enqueue_item(item))
                    else:
                        r = await (controller.play(use_q) if cmd == "play"
                                   else controller.enqueue(use_q))
                    send(fmt_action(r))
            elif cmd in ("list", "now"):
                send(fmt_status(controller.status()))
            elif cmd == "next":
                send(fmt_status(await controller.next()))
            elif cmd == "stop":
                await controller.stop()
                send("stopped.\r\n")
            elif cmd == "clear":
                await controller.clear()
                send("queue cleared.\r\n")
            elif cmd == "pause":
                r = await controller.pause()
                if r.get("error"):
                    send(f"error: {r['error']}\r\n")
                else:
                    send("paused.\r\n" if r["paused"] else "resumed.\r\n")
            elif cmd == "surf":
                send("tuning in...\r\n")
                if not await flush():
                    break
                send(fmt_surf(await controller.surf()))
            elif cmd == "ch":
                a = arg.lower()
                if a in ("up", "+", "u"):
                    delta = 1
                elif a in ("down", "-", "d", "dn"):
                    delta = -1
                else:
                    delta = None
                    send("usage: ch up | ch down\r\n")
                if delta is not None:
                    send("tuning...\r\n")
                    if not await flush():
                        break
                    send(fmt_surf(await controller.channel_step(delta)))
            elif cmd in ("channels", "guide"):
                send(fmt_channels(controller.channels_info()))
            else:
                send(f"unknown command: {cmd}  (try 'help')\r\n")

            send("> ")
            if not await flush():
                break

        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    return handle


# --------------------------------------------------------------------------
# HTTP/JSON frontend (minimal, stdlib only) — CLI today, web UI tomorrow
# --------------------------------------------------------------------------
PHRASES = {200: "OK", 400: "Bad Request", 404: "Not Found"}


async def route(c, method, path, data):
    q = (data.get("q") or "").strip()
    if method == "GET" and path.startswith("/status"):
        return c.status(), 200
    if method == "POST" and path == "/play":
        return ({"error": "missing q"} if not q else await c.play(q)), 200
    if method == "POST" and path == "/enqueue":
        return ({"error": "missing q"} if not q else await c.enqueue(q)), 200
    if method == "POST" and path == "/search":
        return {"results": await c.search(q, int(data.get("count", SEARCH_COUNT)))}, 200
    if method == "POST" and path == "/next":
        return await c.next(), 200
    if method == "POST" and path == "/stop":
        return await c.stop(), 200
    if method == "POST" and path == "/clear":
        return await c.clear(), 200
    if method == "POST" and path == "/pause":
        return await c.pause(), 200
    if method == "POST" and path == "/surf":
        return await c.surf(), 200
    if method == "POST" and path == "/channel":
        d = (data.get("dir") or "").lower()
        if d in ("up", "+"):
            return await c.channel_step(1), 200
        if d in ("down", "-"):
            return await c.channel_step(-1), 200
        return {"error": "dir must be up or down"}, 200
    if (method in ("GET", "POST")) and path.startswith("/channels"):
        return c.channels_info(), 200
    return {"error": "not found"}, 404


def http_handler(controller):
    async def handle(reader, writer):
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            try:
                method, path, _ = request_line.decode("latin1").split()
            except ValueError:
                return
            headers = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                k, _, v = line.decode("latin1").partition(":")
                headers[k.strip().lower()] = v.strip()
            n = int(headers.get("content-length", "0") or "0")
            body = await reader.readexactly(n) if n else b""
            data = {}
            if body:
                try:
                    data = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    data = {}
            result, code = await route(controller, method, path, data)
            payload = json.dumps(result).encode("utf-8")
            head = (
                f"HTTP/1.1 {code} {PHRASES.get(code, 'OK')}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("latin1")
            writer.write(head + payload)
            await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    return handle


# --------------------------------------------------------------------------
async def main():
    os.makedirs(os.path.dirname(MPV_SOCK), exist_ok=True)
    try:
        os.unlink(MPV_SOCK)
    except FileNotFoundError:
        pass

    mpv_proc = await asyncio.create_subprocess_exec(
        MPV,
        "--idle=yes",
        "--force-window=yes",
        "--image-display-duration=inf",
        "--no-terminal",
        "--no-osc",
        f"--input-ipc-server={MPV_SOCK}",
    )

    mpv = Mpv(MPV_SOCK)
    await mpv.connect()
    controller = Controller(mpv)
    mpv.on_event = controller.on_event
    await controller._go_idle()

    telnet_srv = await asyncio.start_server(
        telnet_handler(controller), "0.0.0.0", TELNET_PORT)
    http_srv = await asyncio.start_server(
        http_handler(controller), HTTP_HOST, HTTP_PORT)

    async def watch_mpv():
        await mpv_proc.wait()
        os._exit(1)   # mpv died -> exit so systemd restarts the whole stack

    asyncio.create_task(watch_mpv())

    async with telnet_srv, http_srv:
        await asyncio.gather(telnet_srv.serve_forever(),
                             http_srv.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
