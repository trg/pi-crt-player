# ESP32 touchscreen remote — scrollable channel list design

Status: approved, not yet implemented.

## What this is

Replaces the hello-world's two fixed buttons ("CH 7" / "CH 13") with a paged,
scrollable list of every channel in the guide (`config/channels.txt`),
fetched live from pcpd. Tapping a channel's row tunes the CRT to it — same
underlying `POST /channel` call the hello-world already proved works.

Builds on `docs/superpowers/specs/2026-07-24-esp32-remote-control-design.md`
(hardware, WiFi, HTTP client, deploy workflow, error-only feedback philosophy
all carry over unchanged). This doc only covers what's new.

## Bug to fix as part of this work

The hello-world's two-zone version has a live bug: **every tap tunes channel
13, regardless of where the screen is touched.** Root cause not yet
diagnosed. Because this feature replaces the touch-to-screen-coordinate
pipeline entirely (see Calibration below), the fix has to land as part of
this work, not separately — there's no point calibrating a new 6-row + 2-button
hit-test grid on top of a coordinate mapping that's already known to be
broken. Verification for this feature (see Testing) must include tapping
multiple distinct rows/buttons and confirming *different* outcomes, not just
that taps register at all.

## Architecture

- **Channel data fetch** — once at boot, after WiFi connects:
  `GET http://<pcpd>:<port>/channels` (existing endpoint, no server change).
  Response shape (already implemented in `Controller.channels_info`):
  ```json
  {"channels": [{"num": 1, "name": "NASA", "current": false}, ...],
   "surfing": true, "now": {...}}
  ```
  Parsed with **ArduinoJson** (new `lib_deps` entry — the old hello-world
  avoided it since it only needed an HTTP status code; a name/number list
  needs real parsing). Stored as a fixed array of
  `struct Channel { uint8_t num; String name; bool current; }`, capped at
  `MAX_CHANNELS` (64 — headroom above today's 21). Fetched once; not
  refreshed again until reboot (editing `channels.txt` and wanting the remote
  to see it means power-cycling the remote — no periodic re-fetch, no manual
  refresh button, matching this iteration's YAGNI scope).

- **Screen layout** (240x320 portrait, unchanged rotation):
  - 6 channel rows, 45px tall each (270px total).
  - 1 bottom nav bar, 50px tall, split into two 120px-wide halves: PREV
    (left) / NEXT (right).
  - 270 + 50 = 320 — exact fit, no remainder/padding row.
  - `totalPages = ceil(channelCount / 6)`; current page tracked in a local
    `pageIdx`.

- **Row content**: `"<num>  <name>"`, left-aligned, truncated if too long for
  240px at the chosen font. The channel where `current == true` (whichever
  page it falls on) is drawn with inverted colors (white background, black
  text) so it's unmistakable against the plain rows — no other per-row
  state (no "now playing" title; that's out of scope, see below).

- **Interaction**:
  - Tap a channel row → `POST /channel {"num": <n>}` (existing call from the
    hello-world, unchanged). Success shows nothing (CRT switching is the
    confirmation, same philosophy as before); failure shows the existing
    transient error banner (`HTTP 404` / `no response`) for ~1.5s, then
    redraws the current page.
  - Tap PREV/NEXT → purely local, no network call: `pageIdx` decrements/
    increments and **wraps around** (last page's NEXT → page 0; page 0's
    PREV → last page), matching `ch up`/`ch down`'s existing wraparound
    behavior in `Controller.channel_step` — consistent feel with the rest of
    the system.

## Calibration (replaces the hello-world's top/bottom-only version)

The hello-world's 2-point calibration only ever needed to distinguish "top
half vs. bottom half" (1 axis, 2 zones) — provably insufficient for
hit-testing 6 rows plus 2 side-by-side nav buttons, which needs real X *and*
Y screen coordinates. It's also the prime suspect for the "always channel
13" bug: a calibration that only ever resolves to "closer to top or closer to
bottom" can't have been doing real coordinate mapping, and something in that
narrow logic is apparently always resolving to the bottom zone.

Replacement: a **2-corner calibration** — on first boot (or after NVS erase),
prompt for a tap near the top-left of the screen, then a tap near the
bottom-right. From those two raw `(x, y)` samples, derive independent linear
maps for each screen axis:

```
screenX = map(rawAxisForX, rawTopLeft.axisForX, rawBottomRight.axisForX, 0, SCREEN_W)
screenY = map(rawAxisForY, rawTopLeft.axisForY, rawBottomRight.axisForY, 0, SCREEN_H)
```

`axisForX`/`axisForY` (i.e. whether the touch controller's raw X or raw Y
corresponds to the screen's horizontal axis) are determined the same way the
old code picked "which axis separates top from bottom" — by comparing `|dx|`
vs `|dy|` between the two calibration taps — extended to cover both screen
axes instead of just one. Cached in NVS under new keys (the old
`calAxisIsX`/`calTop`/`calBottom` keys are no longer used and can be left
orphaned or cleared — not worth migration code for a hello-world-stage
device). Every subsequent tap runs through this map before hit-testing rows
or nav buttons.

## Error handling

- Channel-list fetch failure (mDNS resolution failure, timeout, non-200,
  JSON parse failure, or an empty list): show "Fetching channels..." then
  "Fetch failed, retrying..." and retry automatically every ~3s, indefinitely
  — no manual retry button, matching the existing WiFi-retry pattern already
  in the hello-world's `loop()`.
- Response contains more than `MAX_CHANNELS` entries: silently truncate,
  `Serial.println` a warning. No user-facing indication — this repo has no
  monitoring for this hardware today, and it's not expected to matter with a
  21-channel lineup.
- All existing hello-world error paths (bad/missing `config.txt`, WiFi
  connect failure) are unchanged.

## Testing & verification

Physical/manual, no unit tests (matches this repo's and the prior remote
design's testing philosophy):

- Flash, go through the new 2-corner calibration, confirm the two prompts
  appear and the device proceeds to the list afterward.
- Confirm the list's names/numbers/order match `config/channels.txt` and
  `pcp guide` / telnet's `guide` command output.
- Confirm the channel flagged current by `/channels` is visibly highlighted.
- **Regression-test the "always channel 13" bug**: tap at least 4 different
  rows across at least 2 different pages, and both PREV and NEXT, confirming
  each produces a *distinct* correct outcome (via `pcp now` on the Pi) —
  not just that taps register.
- Tap PREV on page 0 (confirm wrap to the last page) and NEXT on the last
  page (confirm wrap to page 0).
- Briefly stop `crt-player` on the Pi (or block the port) to confirm the
  channel-fetch retry loop behaves, then restart it and confirm the remote
  recovers without a reboot.

## Explicitly out of scope (YAGNI for this iteration)

- Drag-to-scroll / inertia — paged prev/next only.
- Periodic or on-demand refresh of the channel list after boot.
- "Now playing" title shown per row (only the current-channel highlight).
- Channel search/filter, favorites, or reordering.
- 4-corner (or higher-order) calibration — 2-corner linear mapping only.
- Landscape orientation.
- More than `MAX_CHANNELS` (64) channels.
