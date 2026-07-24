// pi-crt-player remote control — ESP32-2432S028R ("Cheap Yellow Display")
// 2.8" touchscreen board.
//
// Shows a paged, scrollable list of every channel from pcpd's /channels
// endpoint (the same guide `pcp guide`/telnet's `guide` command shows).
// Tapping a row POSTs straight to pcpd's existing /channel endpoint (the
// same one the `pcp ch <n>` CLI uses) — no server-side logic here, only a
// thin frontend (see root CLAUDE.md's control-core/frontend split).
//
// Config (WiFi + pcpd address) lives in data/config.txt on LittleFS —
// upload it with `pio run -t uploadfs` before flashing, or use scripts/flash.sh
// which does both in one shot.
#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <TFT_eSPI.h>
#include <Preferences.h>
#include <SPI.h>
#include <XPT2046_Touchscreen.h>

TFT_eSPI tft = TFT_eSPI();
Preferences prefs;

// The touch controller (XPT2046) on this board is wired to its OWN SPI bus,
// separate from the display's (MOSI 32, MISO 39, SCK 25, CS 33, IRQ 36) —
// confirmed on real hardware after TFT_eSPI's built-in touch support (which
// assumes a shared bus) read a flat zero on every sample. The
// XPT2046_Touchscreen library hardcodes the global `SPI` object (VSPI), so
// platformio.ini forces the display onto HSPI instead (USE_HSPI_PORT) to
// keep the two buses on genuinely separate physical peripherals. See
// 2.8-in-lcd-display/CLAUDE.md (sibling sandbox project) for the full
// writeup of this pitfall.
#define TOUCH_CS 33
#define TOUCH_IRQ 36
#define TOUCH_SCK 25
#define TOUCH_MISO 39
#define TOUCH_MOSI 32

XPT2046_Touchscreen ts(TOUCH_CS, TOUCH_IRQ);

const int SCREEN_W = 240;
const int SCREEN_H = 320; // portrait

struct Config {
  String wifiSsid;
  String wifiPass;
  String pcpHost;
  uint16_t pcpPort = 8677;
};

Config cfg;

// ---------- Channel list ----------

const int MAX_CHANNELS = 64;   // headroom above today's ~21-channel lineup

struct Channel {
  uint8_t num;
  String name;
  bool current;
};

Channel channels[MAX_CHANNELS];
int channelCount = 0;

const int ROWS_PER_PAGE = 6;
const int ROW_H = 45;
const int NAV_H = 50;   // ROWS_PER_PAGE*ROW_H + NAV_H == SCREEN_H exactly
int pageIdx = 0;

int totalPages() {
  return (channelCount + ROWS_PER_PAGE - 1) / ROWS_PER_PAGE;
}

// Resistive touch contact-bounce debounce: a single physical tap can
// register as touched/released/touched within milliseconds, so a simple
// "was it pressed last loop" latch isn't enough — require a minimum gap
// between fires instead.
unsigned long lastFireMs = 0;
const unsigned long TAP_COOLDOWN_MS = 400;

// ---------- Touch calibration ----------
//
// Three taps — top-left, top-right, bottom-left — rather than a simple
// diagonal top-left/bottom-right pair. A true diagonal pair can't reliably
// tell us which raw touch axis (x or y) tracks the screen's horizontal vs.
// vertical direction: on this 240x320 panel, a diagonal corner-to-corner tap
// covers ~83% of the screen's width and ~88% of its height — too close to
// tell apart by comparing raw deltas. Isolating one screen axis per pair
// (TL->TR only moves horizontally, TL->BL only moves vertically) makes the
// swap/inversion detection unambiguous — the same trick the very first
// version of this firmware used for its single top/bottom split, just
// applied to both axes now that we need real X/Y hit-testing.
const int16_t CAL_MARGIN = 30;

bool xUsesRawX = true;
int16_t rawXLeft = 0, rawXRight = 0;
int16_t rawYTop = 0, rawYBottom = 0;

// ---------- Config loading ----------

static String trimmed(const String &s) {
  String r = s;
  r.trim();
  if (r.length() >= 2 && r.startsWith("\"") && r.endsWith("\"")) {
    r = r.substring(1, r.length() - 1);
  }
  return r;
}

bool loadConfig(Config &out) {
  if (!LittleFS.begin(true)) {
    Serial.println("LittleFS mount failed");
    return false;
  }
  File f = LittleFS.open("/config.txt", "r");
  if (!f) {
    Serial.println("config.txt not found");
    return false;
  }
  while (f.available()) {
    String line = trimmed(f.readStringUntil('\n'));
    if (line.length() == 0 || line.startsWith("#")) continue;
    int eq = line.indexOf('=');
    if (eq < 0) continue;
    String key = trimmed(line.substring(0, eq));
    String val = trimmed(line.substring(eq + 1));
    if (key == "WIFI_SSID") out.wifiSsid = val;
    else if (key == "WIFI_PASS") out.wifiPass = val;
    else if (key == "PCP_HOST") out.pcpHost = val;
    else if (key == "PCP_PORT") out.pcpPort = (uint16_t) val.toInt();
  }
  f.close();
  return out.wifiSsid.length() > 0 && out.pcpHost.length() > 0;
}

// ---------- Networking ----------

bool connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(cfg.wifiSsid.c_str(), cfg.wifiPass.c_str());
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(250);
  }
  bool ok = WiFi.status() == WL_CONNECTED;
  if (ok) {
    Serial.printf("WiFi connected, IP=%s\n", WiFi.localIP().toString().c_str());
    MDNS.begin("pcp-remote"); // required init step even for lookups only
  } else {
    Serial.printf("WiFi connect failed, status=%d\n", WiFi.status());
  }
  return ok;
}

// PCP_HOST is a bare mDNS hostname (e.g. "crt-station", no ".local"), not a
// fixed IP — the Pi's DHCP lease isn't stable, so we re-resolve on every
// request instead of caching an address that can go stale.
static String stripLocalSuffix(const String &h) {
  if (h.endsWith(".local")) return h.substring(0, h.length() - 6);
  return h;
}

// Returns the HTTP status code (200 = success), or a negative code if the
// request never got a response (mDNS resolution failure, unreachable,
// timeout). Body is deliberately not parsed — only pass/fail is needed.
int tuneChannel(int num) {
  IPAddress ip = MDNS.queryHost(stripLocalSuffix(cfg.pcpHost), 3000);
  if (ip == IPAddress()) { // queryHost returns 0.0.0.0 on failure
    Serial.printf("mDNS lookup failed for %s\n", cfg.pcpHost.c_str());
    return -1;
  }

  HTTPClient http;
  String url = "http://" + ip.toString() + ":" + String(cfg.pcpPort) + "/channel";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(4000);
  String body = "{\"num\":" + String(num) + "}";
  int code = http.POST(body);
  Serial.printf("POST %s num=%d -> %d\n", url.c_str(), num, code);
  http.end();
  return code;
}

// Fetches the channel lineup once. Returns false on any failure (mDNS,
// network, non-200, bad JSON, empty list) — caller decides whether to retry.
bool fetchChannelsOnce() {
  IPAddress ip = MDNS.queryHost(stripLocalSuffix(cfg.pcpHost), 3000);
  if (ip == IPAddress()) {
    Serial.println("mDNS lookup failed for channel fetch");
    return false;
  }

  HTTPClient http;
  String url = "http://" + ip.toString() + ":" + String(cfg.pcpPort) + "/channels";
  http.begin(url);
  http.setTimeout(4000);
  int code = http.GET();
  if (code != 200) {
    Serial.printf("GET %s -> %d\n", url.c_str(), code);
    http.end();
    return false;
  }

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, http.getStream());
  http.end();
  if (err) {
    Serial.printf("channels JSON parse failed: %s\n", err.c_str());
    return false;
  }

  channelCount = 0;
  for (JsonObject c : doc["channels"].as<JsonArray>()) {
    if (channelCount >= MAX_CHANNELS) {
      Serial.println("channel list truncated at MAX_CHANNELS");
      break;
    }
    channels[channelCount].num = c["num"] | 0;
    channels[channelCount].name = String((const char *) (c["name"] | ""));
    channels[channelCount].current = c["current"] | false;
    channelCount++;
  }
  return channelCount > 0;
}

// ---------- UI ----------

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

void showMessage(const String &msg) {
  tft.fillScreen(COLOR_BG);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(COLOR_SIDEBAR, COLOR_BG);
  tft.setTextFont(2);
  tft.drawString(msg, SCREEN_W / 2, SCREEN_H / 2);
}

String truncateToWidth(const String &s, int maxW) {
  if (tft.textWidth(s) <= maxW) return s;
  String out = s;
  while (out.length() > 1 && tft.textWidth(out + "..") > maxW) {
    out.remove(out.length() - 1);
  }
  return out + "..";
}

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

void drawCalTarget(int16_t tx, int16_t ty, const String &label) {
  tft.fillScreen(COLOR_BG);
  tft.drawFastHLine(tx - 10, ty, 20, COLOR_HIGHLIGHT);
  tft.drawFastVLine(tx, ty - 10, 20, COLOR_HIGHLIGHT);
  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(2);
  tft.setTextColor(COLOR_SIDEBAR, COLOR_BG);
  tft.drawString(label, SCREEN_W / 2, SCREEN_H / 2);
}

// Blocks until a touch is sampled, returning its raw point (debounced with
// a second consistent sample 30ms later). Only used during calibration,
// where blocking is fine — the runtime tap path below uses a shorter,
// non-blocking-friendly version.
//
// Also waits for the finger to lift before returning: calibration draws
// targets back-to-back with no other UI event between them, so without this
// a single physical tap that's still resting on the glass when the next
// target appears immediately satisfies that target's touch-wait too (with
// nearly identical raw coordinates) — one tap registering as several.
TS_Point waitForStableRawTouch() {
  TS_Point result;
  while (true) {
    if (ts.touched()) {
      TS_Point p = ts.getPoint();
      delay(30);
      if (ts.touched()) {
        TS_Point p2 = ts.getPoint();
        if (abs(p.x - p2.x) < 200 && abs(p.y - p2.y) < 200) {
          result = p2;
          break;
        }
      }
    }
    delay(20);
  }
  while (ts.touched()) delay(20);
  delay(150); // let contact-bounce settle after lift-off
  return result;
}

// A resistive touch controller's very first sample after contact is often
// noisy/unsettled — this was very likely the reason every tap in the
// original two-button version resolved to the same button regardless of
// where the screen was pressed: the old code trusted a single immediate
// `ts.getPoint()` in the loop, unlike calibration, which already read twice.
// Runtime taps now go through the same double-sample check.
bool getStableTouch(TS_Point &out) {
  if (!ts.touched()) return false;
  TS_Point p1 = ts.getPoint();
  delay(15);
  if (!ts.touched()) return false;
  TS_Point p2 = ts.getPoint();
  if (abs(p1.x - p2.x) > 150 || abs(p1.y - p2.y) > 150) return false;
  out = p2;
  return true;
}

void toScreenXY(const TS_Point &p, int16_t &sx, int16_t &sy) {
  int16_t rawX = xUsesRawX ? p.x : p.y;
  int16_t rawY = xUsesRawX ? p.y : p.x;
  long x = map(rawX, rawXLeft, rawXRight, CAL_MARGIN, SCREEN_W - CAL_MARGIN);
  long y = map(rawY, rawYTop, rawYBottom, CAL_MARGIN, SCREEN_H - CAL_MARGIN);
  sx = constrain(x, 0, SCREEN_W - 1);
  sy = constrain(y, 0, SCREEN_H - 1);
}

// Three-point calibration: top-left, top-right, bottom-left. TL->TR isolates
// the screen's horizontal axis (screen Y is ~constant between those two
// taps); TL->BL isolates the vertical axis. Whichever raw axis (x or y)
// changes more between a given pair is the one that tracks that screen
// direction — deciding both independently would risk an inconsistent
// diagonal result, so only the horizontal pair's result is trusted and the
// vertical axis is taken as its complement (there are only two raw axes).
// Cached in NVS so later boots skip straight to the channel list.
void calibrateTouchIfNeeded() {
  prefs.begin("remote-control", false);
  bool haveCal = prefs.isKey("xUsesRawX") && prefs.isKey("rXL") &&
                 prefs.isKey("rXR") && prefs.isKey("rYT") && prefs.isKey("rYB");
  if (haveCal) {
    xUsesRawX = prefs.getBool("xUsesRawX");
    rawXLeft = prefs.getShort("rXL");
    rawXRight = prefs.getShort("rXR");
    rawYTop = prefs.getShort("rYT");
    rawYBottom = prefs.getShort("rYB");
    prefs.end();
    return;
  }

  drawCalTarget(CAL_MARGIN, CAL_MARGIN, "Tap TOP-LEFT");
  TS_Point tl = waitForStableRawTouch();

  drawCalTarget(SCREEN_W - CAL_MARGIN, CAL_MARGIN, "Tap TOP-RIGHT");
  TS_Point tr = waitForStableRawTouch();

  drawCalTarget(CAL_MARGIN, SCREEN_H - CAL_MARGIN, "Tap BOTTOM-LEFT");
  TS_Point bl = waitForStableRawTouch();

  int16_t dxTR = abs(tr.x - tl.x), dyTR = abs(tr.y - tl.y);
  xUsesRawX = dxTR >= dyTR;

  rawXLeft = xUsesRawX ? tl.x : tl.y;
  rawXRight = xUsesRawX ? tr.x : tr.y;
  rawYTop = xUsesRawX ? tl.y : tl.x;
  rawYBottom = xUsesRawX ? bl.y : bl.x;

  prefs.putBool("xUsesRawX", xUsesRawX);
  prefs.putShort("rXL", rawXLeft);
  prefs.putShort("rXR", rawXRight);
  prefs.putShort("rYT", rawYTop);
  prefs.putShort("rYB", rawYBottom);
  prefs.end();

  Serial.printf("Touch calibrated: xUsesRawX=%d rawX[%d,%d] rawY[%d,%d]\n",
                xUsesRawX, rawXLeft, rawXRight, rawYTop, rawYBottom);
}

// ---------- Tap handling ----------

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

// ---------- Main ----------

void setup() {
  Serial.begin(115200);

  pinMode(21, OUTPUT); // TFT backlight
  digitalWrite(21, HIGH);

  tft.init();
  tft.setRotation(0); // 240x320 portrait, USB connector at the bottom
  initLcarsColors();
  tft.fillScreen(COLOR_BG);

  SPI.begin(TOUCH_SCK, TOUCH_MISO, TOUCH_MOSI, TOUCH_CS);
  ts.begin();

  showMessage("Loading config...");
  if (!loadConfig(cfg)) {
    showMessage("Bad/missing config.txt");
    while (true) delay(1000); // nothing to retry without a reflash
  }

  calibrateTouchIfNeeded();

  showMessage("Connecting WiFi...");
  if (!connectWiFi()) {
    showMessage("WiFi connect failed");
    while (!connectWiFi()) {
      delay(3000);
    }
  }

  while (true) {
    showMessage("Fetching channels...");
    if (fetchChannelsOnce()) break;
    showMessage("Fetch failed, retrying...");
    delay(3000);
  }

  drawPage(pageIdx);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    showMessage("Reconnecting WiFi...");
    if (connectWiFi()) {
      drawPage(pageIdx);
    } else {
      delay(5000);
    }
    return;
  }

  if (millis() - lastFireMs > TAP_COOLDOWN_MS) {
    TS_Point p;
    if (getStableTouch(p)) {
      lastFireMs = millis();
      int16_t sx, sy;
      toScreenXY(p, sx, sy);
      handleTap(sx, sy);
    }
  }

  delay(20);
}
