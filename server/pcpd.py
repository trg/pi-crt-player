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

MPV = "/usr/bin/mpv"
YT_DLP = "/usr/local/bin/yt-dlp"
MPV_SOCK = os.environ.get("MPV_SOCK", "/run/pi-crt-player/mpv.sock")
TELNET_PORT = int(os.environ.get("PLAYER_PORT", "23"))
HTTP_HOST = os.environ.get("HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8677"))
IDLE_IMAGE = os.environ.get("IDLE_IMAGE", "/usr/local/lib/pi-crt-player/idle.png")
SEARCH_COUNT = 8

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
    "  now                  what's playing + queue\r\n"
    "  help                 show this help\r\n"
    "  quit                 disconnect\r\n"
)


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

    # ---- playback primitives (hold the lock) ----
    async def _load(self, item):
        self.now = item
        self.paused = False
        await self.mpv.command("loadfile", item["url"], "replace")
        await self.mpv.set_property("pause", False)

    async def _go_idle(self):
        self.now = None
        self.paused = False
        if os.path.exists(IDLE_IMAGE):
            await self.mpv.command("loadfile", IDLE_IMAGE, "replace")
        else:
            await self.mpv.command("stop")

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
        return {
            "now": self.now,
            "playing": self.now is not None,
            "paused": self.paused,
            "queue": list(self.queue),
        }

    # ---- mpv events: auto-advance when a video finishes ----
    async def on_event(self, ev):
        if ev.get("event") == "end-file" and ev.get("reason") in ("eof", "error"):
            # Natural end (or a broken file) -> move on. Our own loadfile/stop
            # produce reason "stop"/"redirect", which we deliberately ignore.
            await self.advance()


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
    q = st.get("queue", [])
    if q:
        lines.append("up next:")
        for i, it in enumerate(q, 1):
            lines.append(f"  {i}) {it['title']}")
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
