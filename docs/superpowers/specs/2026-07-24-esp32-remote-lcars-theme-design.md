# ESP32 touchscreen remote — LCARS theme

Status: implemented, simplified from this doc after hardware review. The
color palette and typography sections below shipped as written. The
sidebar/header/elbow layout described in "Layout" and "Construction" did
not: after seeing it on the physical device, the sidebar and header bar were
dropped entirely, the row grid reverted to the original full-width 6
rows/page (45px rows + 50px nav, matching the pre-LCARS geometry) instead of
5, and the nav bar became two plain, differently-colored rectangles (rose
PREV, orange NEXT) instead of one rose bar with a divider. See
`remote-control/src/main.cpp`'s `drawPage`/`drawNavBar` for the as-built
layout.

## What this is

Re-skins the channel-list remote (`remote-control/`) with an LCARS look (Star
Trek: TNG's console UI) — colors, a curved sidebar, and caps-lock typography.
Purely visual: no change to the HTTP calls, channel-fetch behavior,
calibration math, debounce logic, or paging/wraparound semantics described in
`2026-07-24-esp32-remote-channel-list-design.md`. That doc's architecture
still applies; this one only covers what's drawn on screen and the one
functional side-effect of adding a header (fewer rows fit per page).

## Layout

Replaces the current 6-row-list + bottom-nav-bar grid with a 4-region layout
on the 240×320 portrait screen:

```
╭─╮──────────────────
│▓│  C H A N N E L S     <- header, 36px
│▓╰──────────────────
│▓│ 01  NASA
│▓│ 02  CBS               <- row list, 5 x 44px = 220px
│▓│ 03  HISTORY
│▓│ 04  DISCOVERY
│▓│ 05  COMEDY
│▓╭──────────────────
│▓│  ◄ PREV  │ NEXT ►    <- nav bar, 64px
╰─╯──────────────────
```

- **Sidebar**: 44px wide, full 320px height, orange (`#FF9900`), rounded
  outer corners (top-left, bottom-left only — the inner edge against the
  content column stays flush/square).
- **Header**: 36px tall, spans the remaining 196px-wide content column,
  lilac (`#CC99CC`) background, black "CHANNELS" caption, elbow-joined to the
  sidebar (see Construction below).
- **Row list**: 5 rows × 44px = 220px (down from 6 — the new header eats
  the space; see "Rows-per-page" below).
- **Nav bar**: 64px tall, rose (`#CC6699`), split into PREV (left) / NEXT
  (right) halves, elbow-joined to the sidebar at the bottom.
- 36 + 220 + 64 = 320px — exact fit, no remainder, matching the existing
  design's constraint.

Row/nav-bar *tap targets* keep the same hit-testing shape as today (full-width
horizontal bands within the content column) — only their pixel bounds shift
to account for the header and narrower (196px vs 240px) width. Sidebar itself
is not tappable.

## Construction (how the curves get drawn)

TFT_eSPI (already pinned to `^2.5.43`) has `fillRoundRect` and `fillArc`
(filled pie-wedge, start/end angle + radius) — enough to build a real curved
elbow, not just colored rectangles:

1. **Sidebar rounded corners**: draw the full-height sidebar as
   `fillRoundRect` (rounds all 4 corners), then `fillRect` over its right
   half to re-square the two corners facing the content column — leaves only
   the two outer (left) corners rounded.
2. **Elbow fillets** (where the header/nav dividers meet the sidebar): draw
   the sidebar rect and the horizontal divider bar as a solid overlapping
   L-shape, then `fillArc` a quarter-circle wedge in the background color
   (black) at the inner corner of the joint to carve the concave curve real
   LCARS elbows have. Two of these: header-to-sidebar joint, nav-bar-to-sidebar
   joint.
3. All of this is drawn once per full-screen redraw (boot, page change, tap
   result) — never per-frame/animated — so the extra draw calls (vs. the
   current plain-rectangle version) have no perceptible cost.

## Colors

Classic TNG (Okuda) palette:

| Element | Color | Hex |
|---|---|---|
| Background | Black | `#000000` |
| Sidebar, header caption bg | Orange | `#FF9900` |
| Header bar bg | Lilac / African violet | `#CC99CC` |
| Current-channel row highlight | Ice blue | `#9999FF` |
| Nav bar (PREV/NEXT) bg | Dusty rose | `#CC6699` |
| Row text (non-current) | Orange | `#FF9900` |
| Text on colored blocks (header, highlight, nav) | Black | `#000000` |
| Row separators | Lilac | `#CC99CC` |

Same semantics as the current white-highlight design (current channel =
inverted/distinct block color; everything else = plain row), just remapped
to this palette instead of black/white.

## Typography

No custom font (ruled out — avoids a font-licensing question and a
conversion/embedding pipeline for this iteration). Channel names are
uppercased at render time only (`channels[i].name` stays as fetched — for
`tuneChannel`/logging — a local uppercased copy is used just for
`drawPage`'s `tft.drawString` calls) using the existing built-in fonts
already loaded in `platformio.ini`. This mimics the Okuda font's all-caps
convention without needing the actual typeface.

## Rows-per-page change

`ROWS_PER_PAGE` changes from 6 to 5 (the only non-cosmetic change in this
doc) — the header eats the vertical space the 6th row used to occupy. This
only changes the constant; `totalPages()`'s `ceil` math, wraparound in
`handleTap`'s PREV/NEXT branch, and the current-channel local-update logic
in `handleTap` are all untouched (they're already parameterized on
`ROWS_PER_PAGE`/`channelCount`, not hardcoded to 6).

## Status / calibration / error screens

For visual consistency, `showMessage()` (WiFi/fetch-status text, transient
tap-error banner) and the calibration crosshair prompts (`drawCalTarget`)
switch from their current styling to black background + orange text —
**no sidebar/header chrome** on these screens (they're single centered
lines, not the list view; adding full LCARS chrome to a "Connecting WiFi..."
message is unnecessary scope). Calibration crosshair color becomes ice blue
for visibility against black.

## Error handling

Unchanged from the existing design — this is a pure re-theme. No new error
paths, no behavior changes to retry timing, debounce, or calibration logic.

## Testing & verification

Physical/manual, matching this repo's existing testing philosophy:

- Flash and confirm the sidebar/header/row/nav layout renders with no gaps,
  overlaps, or off-by-one pixel seams at the two elbow joints.
- Confirm 5 rows/page display correctly and paging (`PREV`/`NEXT`, including
  wraparound at page 0 and the last page) still works with the new row count.
- Confirm the current-channel row shows the ice-blue highlight, and that
  tapping a different channel moves the highlight (regression check for the
  existing "local update on tune" logic, now just with new colors).
- Confirm long channel names still truncate correctly at the new, narrower
  196px content width (`truncateToWidth` is already width-driven via
  `tft.textWidth()`, not hardcoded, so this should need no code change — but
  verify on-device since the available width shrank).
- Confirm calibration prompts and WiFi/fetch status messages render in the
  new colors and remain legible.

## Explicitly out of scope

- Custom/Okuda-style font (ruled out this iteration — see Typography).
- Sound effects (LCARS beeps) — no speaker wiring in this project.
- Animated sweeps/transitions between pages — redraws are instant, as today.
- Sidebar as a tap target (e.g. a channel-number jump control) — sidebar is
  decorative only.
- Changing calibration's 3-point math, debounce/cooldown timing, or the
  channel-fetch-once-at-boot behavior — all unchanged from the existing
  design.
