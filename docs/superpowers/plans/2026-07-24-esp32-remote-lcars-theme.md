# LCARS Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin the ESP32 remote's channel-list UI with an LCARS (Star Trek: TNG) look — classic TNG color palette, a curved orange sidebar with header/nav elbow joints, and all-caps text mimicking the Okuda font — with zero change to networking, calibration, debounce, or paging semantics.

**Architecture:** Everything lives in the single existing file, `remote-control/src/main.cpp`. Two changes: (1) recolor the existing status/calibration screens to the new palette, (2) replace the plain 6-row/bottom-bar grid with a 4-region layout (sidebar, header, 5-row list, nav bar) built from a single reusable "round one corner" primitive, and update the row list and tap hit-testing to match.

**Tech Stack:** PlatformIO, Arduino framework, TFT_eSPI (`fillRect`, `fillCircle`, `drawString` — no new library dependency).

## Global Constraints

- No custom/Okuda font this iteration — built-in TFT_eSPI fonts only, channel names uppercased at render time to mimic the look (per `docs/superpowers/specs/2026-07-24-esp32-remote-lcars-theme-design.md`, "Typography").
- Exact palette (same doc, "Colors"): background `#000000`, sidebar/row-text `#FF9900`, header/separators `#CC99CC`, current-channel highlight `#9999FF`, nav bar `#CC6699`, text-on-color-blocks `#000000`.
- Geometry must sum to exactly 320px vertically with no remainder: header 36px + 5 rows × 44px (220px) + nav bar 64px = 320px.
- No changes to: touch calibration math, `TAP_COOLDOWN_MS`/debounce, `fetchChannelsOnce`/`tuneChannel`/mDNS networking, retry timing, or the channel-fetch-once-at-boot behavior. This is a pure re-theme plus the one derived geometry change (rows-per-page).
- Build/compile check for every task: from `remote-control/`, run `pio run` (compiles the `cyd` env; catches syntax/type errors without needing the physical device).
- Flashing (final on-device check only): `pio run -t upload` from `remote-control/` (add `--upload-port /dev/cu.usbserial-10` if auto-detection picks the wrong device — see README). No config/filesystem changes in this feature, so `uploadfs` is not needed.
- Actual visual/behavioral confirmation on the physical CRT/board must come from the user — the agent executing this plan cannot see the hardware screen itself.

---

### Task 1: Recolor status and calibration screens to the LCARS palette

**Files:**
- Modify: `remote-control/src/main.cpp:231-239` (`showMessage`)
- Modify: `remote-control/src/main.cpp:281-289` (`drawCalTarget`)
- Modify: `remote-control/src/main.cpp:435-443` (`setup()`, display init block)

**Interfaces:**
- Consumes: `TFT_eSPI tft` (existing global, `main.cpp:24`), `TFT_BLACK` (TFT_eSPI built-in constant).
- Produces: `uint16_t COLOR_BG, COLOR_SIDEBAR, COLOR_HEADER, COLOR_HIGHLIGHT, COLOR_NAV, COLOR_BLOCK_TEXT` (new globals) and `void initLcarsColors()` — consumed by Task 2's chrome-drawing functions.

- [ ] **Step 1: Add the color globals and init function**

Insert immediately after the `// ---------- UI ----------` comment (`main.cpp:231`), before `showMessage`:

```cpp
// ---------- LCARS theme colors ----------
//
// Classic TNG (Okuda) palette. Computed via tft.color565() at boot rather
// than as static consts — color565() needs no hardware init, but keeping
// all color setup in one place inside setup() avoids relying on
// cross-global static-initialization ordering.
uint16_t COLOR_BG;         // black background
uint16_t COLOR_SIDEBAR;    // orange — sidebar, row text
uint16_t COLOR_HEADER;     // lilac — header bar, row separators
uint16_t COLOR_HIGHLIGHT;  // ice blue — current-channel row, cal crosshair
uint16_t COLOR_NAV;        // dusty rose — PREV/NEXT nav bar
uint16_t COLOR_BLOCK_TEXT; // black — text drawn on any colored block

void initLcarsColors() {
  COLOR_BG = TFT_BLACK;
  COLOR_SIDEBAR = tft.color565(0xFF, 0x99, 0x00);
  COLOR_HEADER = tft.color565(0xCC, 0x99, 0xCC);
  COLOR_HIGHLIGHT = tft.color565(0x99, 0x99, 0xFF);
  COLOR_NAV = tft.color565(0xCC, 0x66, 0x99);
  COLOR_BLOCK_TEXT = TFT_BLACK;
}
```

- [ ] **Step 2: Call `initLcarsColors()` in `setup()` before first use**

In `setup()` (`main.cpp:441-443`), change:

```cpp
  tft.init();
  tft.setRotation(0); // 240x320 portrait, USB connector at the bottom
  tft.fillScreen(TFT_BLACK);
```

to:

```cpp
  tft.init();
  tft.setRotation(0); // 240x320 portrait, USB connector at the bottom
  initLcarsColors();
  tft.fillScreen(COLOR_BG);
```

- [ ] **Step 3: Recolor `showMessage`**

Replace `main.cpp:233-239`:

```cpp
void showMessage(const String &msg) {
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextFont(2);
  tft.drawString(msg, SCREEN_W / 2, SCREEN_H / 2);
}
```

with:

```cpp
void showMessage(const String &msg) {
  tft.fillScreen(COLOR_BG);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(COLOR_SIDEBAR, COLOR_BG);
  tft.setTextFont(2);
  tft.drawString(msg, SCREEN_W / 2, SCREEN_H / 2);
}
```

- [ ] **Step 4: Recolor `drawCalTarget`**

Replace `main.cpp:281-289`:

```cpp
void drawCalTarget(int16_t tx, int16_t ty, const String &label) {
  tft.fillScreen(TFT_BLACK);
  tft.drawFastHLine(tx - 10, ty, 20, TFT_WHITE);
  tft.drawFastVLine(tx, ty - 10, 20, TFT_WHITE);
  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(2);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawString(label, SCREEN_W / 2, SCREEN_H / 2);
}
```

with:

```cpp
void drawCalTarget(int16_t tx, int16_t ty, const String &label) {
  tft.fillScreen(COLOR_BG);
  tft.drawFastHLine(tx - 10, ty, 20, COLOR_HIGHLIGHT);
  tft.drawFastVLine(tx, ty - 10, 20, COLOR_HIGHLIGHT);
  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(2);
  tft.setTextColor(COLOR_SIDEBAR, COLOR_BG);
  tft.drawString(label, SCREEN_W / 2, SCREEN_H / 2);
}
```

- [ ] **Step 5: Compile check**

Run: `cd remote-control && pio run`
Expected: `SUCCESS` — no errors. (`drawPage`/`handleTap` still reference the old `TFT_WHITE`/`TFT_NAVY`/etc. constants at this point — that's fine, Task 2 replaces them. This step only confirms the new color globals and their two call sites compile.)

Note: no flash/on-device check yet — this task's visual effect (status/calibration screens in orange-on-black) is confirmed together with Task 2's on-device check below, to avoid a redundant ~30s flash cycle for a change this small.

- [ ] **Step 6: Commit**

```bash
cd remote-control
git add src/main.cpp
git commit -m "Recolor status/calibration screens to LCARS palette"
```

---

### Task 2: Rebuild the channel-list screen as LCARS chrome (sidebar, header, nav elbows)

**Files:**
- Modify: `remote-control/src/main.cpp:69-76` (geometry constants)
- Modify: `remote-control/src/main.cpp:250-279` (`drawPage`)
- Modify: `remote-control/src/main.cpp:400-431` (`handleTap`)
- New functions added to `remote-control/src/main.cpp`, placed immediately before `drawPage` (i.e. after the Task 1 `drawCalTarget` edit, before the current `main.cpp:250`): `roundCorner`, `drawSidebar`, `drawHeader`, `drawNavBar`.

**Interfaces:**
- Consumes: `COLOR_BG, COLOR_SIDEBAR, COLOR_HEADER, COLOR_HIGHLIGHT, COLOR_NAV, COLOR_BLOCK_TEXT` (Task 1), `tft`, `channels[]`/`channelCount`/`Channel` (existing), `truncateToWidth`, `tuneChannel`, `showMessage` (existing, unchanged).
- Produces: Final on-device firmware state for this feature — no later task depends on this one.

Deviation from the design doc: the spec's "Construction" section describes carving elbow fillets with `fillArc` (pie wedges). Step 3 below instead uses `fillRect` (blank the corner box) + `fillCircle` (restore an inset quarter-disk) — the same technique TFT_eSPI's own `fillRoundRect` uses internally. Same visual result, simpler to get right than reasoning about `fillArc`'s start/end angle winding.

- [ ] **Step 1: Replace the row/nav geometry constants**

Replace `main.cpp:69-76`:

```cpp
const int ROWS_PER_PAGE = 6;
const int ROW_H = 45;
const int NAV_H = 50;   // ROWS_PER_PAGE*ROW_H + NAV_H == SCREEN_H exactly
int pageIdx = 0;

int totalPages() {
  return (channelCount + ROWS_PER_PAGE - 1) / ROWS_PER_PAGE;
}
```

with:

```cpp
const int SIDEBAR_W = 44;   // decorative LCARS sidebar, not tappable
const int SIDEBAR_R = 16;   // sidebar's outer (left-side) corner radius
const int CONTENT_X = SIDEBAR_W;
const int CONTENT_W = SCREEN_W - SIDEBAR_W; // 196
const int ELBOW_R = 14;     // header/nav inner-corner radius, joins sidebar

const int HEADER_H = 36;
const int ROWS_PER_PAGE = 5;  // was 6 — header eats the 6th row's space
const int ROW_H = 44;         // was 45
const int NAV_H = 64;         // was 50
// HEADER_H + ROWS_PER_PAGE*ROW_H + NAV_H == SCREEN_H exactly (36+220+64=320)
int pageIdx = 0;

int totalPages() {
  return (channelCount + ROWS_PER_PAGE - 1) / ROWS_PER_PAGE;
}
```

- [ ] **Step 2: Compile check**

Run: `cd remote-control && pio run`
Expected: `SUCCESS`. (`drawPage`/`handleTap` still use the old 0-based, full-width layout math at this point, so on-device behavior would look wrong if flashed now — don't flash yet, just confirm it compiles.)

- [ ] **Step 3: Add the corner-rounding primitive and the three chrome-drawing functions**

Insert immediately before `drawPage` (currently `main.cpp:250`, i.e. right after the Task-1-edited `drawCalTarget`):

```cpp
// ---------- LCARS chrome ----------
//
// Rounds exactly one corner of an already solid-filled rect: blank that
// corner's r x r box to the background color, then restore an inset
// quarter-disk of the foreground color — the same body-plus-corner-circle
// construction TFT_eSPI's own fillRoundRect uses internally, applied to a
// single corner instead of all 4. LCARS "elbow" joints need only the
// corner facing the curve rounded — the others stay flush against
// neighboring chrome (screen edge, sidebar, adjacent bar).
enum Corner { CORNER_TL, CORNER_TR, CORNER_BR, CORNER_BL };

void roundCorner(int x, int y, int w, int h, int r, Corner c, uint16_t fg, uint16_t bg) {
  int bx, by, cx, cy;
  switch (c) {
    case CORNER_TL: bx = x;         by = y;         cx = x + r;     cy = y + r;     break;
    case CORNER_TR: bx = x + w - r; by = y;         cx = x + w - r; cy = y + r;     break;
    case CORNER_BR: bx = x + w - r; by = y + h - r; cx = x + w - r; cy = y + h - r; break;
    default:        bx = x;         by = y + h - r; cx = x + r;     cy = y + h - r; break; // CORNER_BL
  }
  tft.fillRect(bx, by, r, r, bg);
  tft.fillCircle(cx, cy, r, fg);
}

// Full-height orange bar, rounded on its two outer (left) corners only —
// the inner edge stays flush against the content column.
void drawSidebar() {
  tft.fillRect(0, 0, SIDEBAR_W, SCREEN_H, COLOR_SIDEBAR);
  roundCorner(0, 0, SIDEBAR_W, SCREEN_H, SIDEBAR_R, CORNER_TL, COLOR_SIDEBAR, COLOR_BG);
  roundCorner(0, 0, SIDEBAR_W, SCREEN_H, SIDEBAR_R, CORNER_BL, COLOR_SIDEBAR, COLOR_BG);
}

// Lilac header bar spanning the content column. Only its bottom-left
// corner rounds (the elbow joint into the row list) — top-left stays
// square, flush against the sidebar's own rounded top-left.
void drawHeader() {
  tft.fillRect(CONTENT_X, 0, CONTENT_W, HEADER_H, COLOR_HEADER);
  roundCorner(CONTENT_X, 0, CONTENT_W, HEADER_H, ELBOW_R, CORNER_BL, COLOR_HEADER, COLOR_BG);
  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(2);
  tft.setTextColor(COLOR_BLOCK_TEXT, COLOR_HEADER);
  tft.drawString("CHANNELS", CONTENT_X + CONTENT_W / 2, HEADER_H / 2);
}

// Rose nav bar spanning the content column, split PREV/NEXT by a thin
// black divider. Only its top-left corner rounds (the elbow joint out of
// the row list).
void drawNavBar() {
  int navY = HEADER_H + ROWS_PER_PAGE * ROW_H;
  tft.fillRect(CONTENT_X, navY, CONTENT_W, NAV_H, COLOR_NAV);
  roundCorner(CONTENT_X, navY, CONTENT_W, NAV_H, ELBOW_R, CORNER_TL, COLOR_NAV, COLOR_BG);
  tft.drawFastVLine(CONTENT_X + CONTENT_W / 2, navY, NAV_H, COLOR_BG);
  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(2);
  tft.setTextColor(COLOR_BLOCK_TEXT, COLOR_NAV);
  tft.drawString("< PREV", CONTENT_X + CONTENT_W / 4, navY + NAV_H / 2);
  tft.drawString("NEXT >", CONTENT_X + CONTENT_W * 3 / 4, navY + NAV_H / 2);
}
```

- [ ] **Step 4: Compile check**

Run: `cd remote-control && pio run`
Expected: `SUCCESS`. (These functions aren't called from `drawPage` yet — this step only confirms they're well-formed.)

- [ ] **Step 5: Rewrite `drawPage` to use the new chrome and content column**

Replace `main.cpp:250-279`:

```cpp
void drawPage(int page) {
  tft.fillScreen(TFT_BLACK);
  tft.setTextFont(2);
  tft.setTextDatum(ML_DATUM);

  int start = page * ROWS_PER_PAGE;
  for (int i = 0; i < ROWS_PER_PAGE; i++) {
    int idx = start + i;
    int y = i * ROW_H;
    if (idx < channelCount) {
      bool cur = channels[idx].current;
      uint16_t bg = cur ? TFT_WHITE : TFT_BLACK;
      uint16_t fg = cur ? TFT_BLACK : TFT_WHITE;
      tft.fillRect(0, y, SCREEN_W, ROW_H, bg);
      tft.setTextColor(fg, bg);
      String label = String(channels[idx].num) + "  " + channels[idx].name;
      tft.drawString(truncateToWidth(label, SCREEN_W - 16), 8, y + ROW_H / 2);
    }
    tft.drawFastHLine(0, y, SCREEN_W, TFT_DARKGREY);
  }

  int navY = ROWS_PER_PAGE * ROW_H;
  tft.fillRect(0, navY, SCREEN_W / 2, NAV_H, TFT_NAVY);
  tft.fillRect(SCREEN_W / 2, navY, SCREEN_W / 2, NAV_H, TFT_DARKGREEN);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_WHITE, TFT_NAVY);
  tft.drawString("< PREV", SCREEN_W / 4, navY + NAV_H / 2);
  tft.setTextColor(TFT_WHITE, TFT_DARKGREEN);
  tft.drawString("NEXT >", SCREEN_W * 3 / 4, navY + NAV_H / 2);
}
```

with:

```cpp
void drawPage(int page) {
  tft.fillScreen(COLOR_BG);
  drawSidebar();
  drawHeader();

  tft.setTextFont(2);
  tft.setTextDatum(ML_DATUM);

  int start = page * ROWS_PER_PAGE;
  for (int i = 0; i < ROWS_PER_PAGE; i++) {
    int idx = start + i;
    int y = HEADER_H + i * ROW_H;
    if (idx < channelCount) {
      bool cur = channels[idx].current;
      uint16_t bg = cur ? COLOR_HIGHLIGHT : COLOR_BG;
      uint16_t fg = cur ? COLOR_BLOCK_TEXT : COLOR_SIDEBAR;
      tft.fillRect(CONTENT_X, y, CONTENT_W, ROW_H, bg);
      tft.setTextColor(fg, bg);
      String label = String(channels[idx].num) + "  " + channels[idx].name;
      label.toUpperCase();
      tft.drawString(truncateToWidth(label, CONTENT_W - 16), CONTENT_X + 8, y + ROW_H / 2);
    }
    tft.drawFastHLine(CONTENT_X, y, CONTENT_W, COLOR_HEADER);
  }

  drawNavBar();
}
```

- [ ] **Step 6: Compile check**

Run: `cd remote-control && pio run`
Expected: `SUCCESS`.

- [ ] **Step 7: Update `handleTap` hit-testing for the new layout**

Replace `main.cpp:400-431`:

```cpp
void handleTap(int16_t sx, int16_t sy) {
  int navY = ROWS_PER_PAGE * ROW_H;
  int pages = totalPages();

  if (sy >= navY) {
    if (sx < SCREEN_W / 2) {
      pageIdx = (pageIdx - 1 + pages) % pages;
    } else {
      pageIdx = (pageIdx + 1) % pages;
    }
    drawPage(pageIdx);
    return;
  }

  int row = sy / ROW_H;
  int idx = pageIdx * ROWS_PER_PAGE + row;
  if (idx >= channelCount) return;

  int code = tuneChannel(channels[idx].num);
  if (code != 200) {
    showMessage(code > 0 ? ("HTTP " + String(code)) : "no response");
    delay(1500);
  } else {
    // The channel list is fetched once at boot and never re-polled (see
    // design doc), so its "current" flags would otherwise stay frozen at
    // whatever was airing at boot forever. Update the highlight locally on
    // a successful tune instead of leaving it stale.
    for (int i = 0; i < channelCount; i++) channels[i].current = false;
    channels[idx].current = true;
  }
  drawPage(pageIdx);
}
```

with:

```cpp
void handleTap(int16_t sx, int16_t sy) {
  if (sx < CONTENT_X) return; // sidebar is decorative, not a tap target

  int navY = HEADER_H + ROWS_PER_PAGE * ROW_H;
  int pages = totalPages();

  if (sy >= navY) {
    if (sx < CONTENT_X + CONTENT_W / 2) {
      pageIdx = (pageIdx - 1 + pages) % pages;
    } else {
      pageIdx = (pageIdx + 1) % pages;
    }
    drawPage(pageIdx);
    return;
  }

  if (sy < HEADER_H) return; // header bar, not a tap target

  int row = (sy - HEADER_H) / ROW_H;
  int idx = pageIdx * ROWS_PER_PAGE + row;
  if (idx >= channelCount) return;

  int code = tuneChannel(channels[idx].num);
  if (code != 200) {
    showMessage(code > 0 ? ("HTTP " + String(code)) : "no response");
    delay(1500);
  } else {
    // The channel list is fetched once at boot and never re-polled (see
    // design doc), so its "current" flags would otherwise stay frozen at
    // whatever was airing at boot forever. Update the highlight locally on
    // a successful tune instead of leaving it stale.
    for (int i = 0; i < channelCount; i++) channels[i].current = false;
    channels[idx].current = true;
  }
  drawPage(pageIdx);
}
```

- [ ] **Step 8: Final compile check**

Run: `cd remote-control && pio run`
Expected: `SUCCESS`.

- [ ] **Step 9: Flash to the physical board**

Run: `cd remote-control && pio run -t upload` (append `--upload-port /dev/cu.usbserial-10` if the wrong port gets picked, or find the port per the README's "Flashing" section)
Expected: build succeeds and upload reports success (matches the pattern from earlier in this project's development — a ~30s upload).

- [ ] **Step 10: On-device verification (ask the user to confirm each item — the agent cannot see the physical screen)**

Covers this feature's spec (`docs/superpowers/specs/2026-07-24-esp32-remote-lcars-theme-design.md`, "Testing & verification") plus a regression check against the prior channel-list feature's spec:

- Boot sequence ("Loading config...", calibration prompts if NVS was cleared, "Connecting WiFi...", "Fetching channels...") shows orange text on black, no leftover white.
- The sidebar, header ("CHANNELS"), 5-row list, and nav bar render with no gaps, overlaps, or stray pixels at the two elbow joints (header/sidebar, nav/sidebar).
- Exactly 5 rows show per page (not 6); paging via PREV/NEXT still works, including wraparound at the first and last page.
- The current-channel row shows the ice-blue highlight; tapping a different channel moves the highlight to that row (regression check for the existing local-update-on-tune logic).
- Channel names render in capital letters.
- A long channel name still truncates correctly (with `..`) at the new, narrower 196px content width.
- Tapping the sidebar itself (leftmost ~44px) does nothing (not a live control).

- [ ] **Step 11: Commit**

```bash
cd remote-control
git add src/main.cpp
git commit -m "Rebuild channel-list screen as LCARS chrome"
```

---

## Post-implementation documentation

Not a plan task (no code change), but flag for follow-up once Task 2 is confirmed working: `remote-control/README.md`'s "First boot" section (screen layout description) and this repo's `CLAUDE.md`/spec docs list should get a short update to describe the LCARS look, following this session's established pattern of keeping `README.md` current for future agents.
