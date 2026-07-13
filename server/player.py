#!/usr/bin/env python3
"""CRT Player — a tiny, no-login telnet control server for pi-crt-player.

Anyone who can reach the Pi on the network can connect and drive video
playback:

    telnet <pi-host>

Commands: search / play / stop / now / help / quit.

It shells out to the installed `play` / `stop` commands and to yt-dlp for
search, always with argument lists (never a shell string), so search terms
from the network cannot inject commands.
"""
import asyncio
import os

PORT = int(os.environ.get("PLAYER_PORT", "23"))
YT_DLP = "/usr/local/bin/yt-dlp"
PLAY = "/usr/local/bin/play"
STOP = "/usr/local/bin/stop"
SEARCH_COUNT = 8

BANNER = (
    "\r\n"
    "======================================\r\n"
    "        C R T   P L A Y E R\r\n"
    "======================================\r\n"
)

HELP = (
    "Commands:\r\n"
    "  search <words>   search YouTube (shows a numbered list)\r\n"
    "  play <n>         play result number <n> from your last search\r\n"
    "  play <url>       play a YouTube URL directly\r\n"
    "  play <words>     search and play the first hit\r\n"
    "  stop             stop playback\r\n"
    "  now              show what's playing\r\n"
    "  help             show this help\r\n"
    "  quit             disconnect\r\n"
)

# Shared across all connections — there's only one screen.
now_playing = "nothing"


async def run(*args, timeout=60):
    """Run a command (arg list, no shell) and return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, b""
    return proc.returncode, out


async def youtube_search(query):
    """Return a list of (title, video_id). --flat-playlist keeps it fast."""
    code, out = await run(
        YT_DLP, "--flat-playlist", "--no-warnings",
        "--print", "%(title)s\t%(id)s",
        f"ytsearch{SEARCH_COUNT}:{query}",
        timeout=45,
    )
    results = []
    if code == 0:
        for line in out.decode("utf-8", "replace").splitlines():
            if "\t" in line:
                title, vid = line.rsplit("\t", 1)
                if vid.strip():
                    results.append((title.strip(), vid.strip()))
    return results


def strip_telnet(data: bytes) -> bytes:
    """Drop telnet IAC negotiation sequences so they don't leak into commands.

    IAC == 255. IAC IAC is a literal 255; IAC WILL/WONT/DO/DONT <opt> is 3
    bytes; other IAC commands are 2 bytes.
    """
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


async def handle(reader, writer):
    global now_playing
    results = []

    def send(text):
        writer.write(text.encode("utf-8", "replace"))

    send(BANNER)
    send(HELP)
    send("\r\n> ")
    await writer.drain()

    while True:
        try:
            raw = await reader.readuntil(b"\n")
        except asyncio.IncompleteReadError:
            break
        except asyncio.LimitOverrunError:
            send("line too long.\r\n> ")
            await writer.drain()
            continue
        except (ConnectionResetError, BrokenPipeError):
            break
        if not raw:
            break

        line = strip_telnet(raw).decode("utf-8", "replace").strip()
        if not line:
            send("> ")
            await writer.drain()
            continue

        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("help", "?"):
            send(HELP)
        elif cmd in ("quit", "exit", "q"):
            send("bye!\r\n")
            break
        elif cmd == "stop":
            await run(STOP, timeout=15)
            now_playing = "nothing"
            send("stopped.\r\n")
        elif cmd == "now":
            send(f"now playing: {now_playing}\r\n")
        elif cmd == "search":
            if not arg:
                send("usage: search <words>\r\n")
            else:
                send(f"searching for: {arg} ...\r\n")
                await writer.drain()
                results = await youtube_search(arg)
                if not results:
                    send("no results.\r\n")
                else:
                    for i, (title, _) in enumerate(results, 1):
                        send(f"{i:2}) {title}\r\n")
                    send("type 'play <n>' to play one.\r\n")
        elif cmd == "play":
            title = None
            url = None
            if not arg:
                send("usage: play <n | url | words>\r\n")
            elif arg.isdigit():
                n = int(arg)
                if 1 <= n <= len(results):
                    title, vid = results[n - 1]
                    url = f"https://www.youtube.com/watch?v={vid}"
                else:
                    send("no such result — run 'search' first.\r\n")
            elif arg.startswith("http"):
                url, title = arg, arg
            else:
                hits = await youtube_search(arg)
                if not hits:
                    send("no results.\r\n")
                else:
                    title, vid = hits[0]
                    url = f"https://www.youtube.com/watch?v={vid}"
            if url:
                await run(PLAY, url, timeout=30)
                now_playing = title
                send(f"playing: {title}\r\n")
        else:
            send(f"unknown command: {cmd}  (try 'help')\r\n")

        send("> ")
        try:
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            break

    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
