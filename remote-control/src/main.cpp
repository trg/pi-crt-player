// pi-crt-player remote control — hello world firmware for the
// ESP32-2432S028R ("Cheap Yellow Display") 2.8" touchscreen board.
//
// Two touch zones fill the screen: top half tunes the CRT player to
// channel 7, bottom half to channel 13. Each tap POSTs straight to pcpd's
// existing HTTP API (the same /channel endpoint the `pcp ch <n>` CLI uses) —
// no new server endpoint, no business logic here (see root CLAUDE.md's
// control-core/frontend split: this firmware is a thin frontend, same as
// telnet and the CLI).
//
// Config (WiFi + pcpd address) lives in data/config.txt on LittleFS —
// upload it with `pio run -t uploadfs` before flashing, or use scripts/flash.sh
// which does both in one shot.
#include <Arduino.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
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

// Resistive touch contact-bounce debounce: a single physical tap can
// register as touched/released/touched within milliseconds, so a simple
// "was it pressed last loop" latch isn't enough — require a minimum gap
// between fires instead.
unsigned long lastFireMs = 0;
const unsigned long TAP_COOLDOWN_MS = 400;

// Two-point touch calibration: tap the top button, then the bottom button,
// and see which raw touch axis (X or Y — the panel's wiring doesn't
// necessarily line up with the screen's rotation) actually separates them.
// Enough to tell which half was pressed on this hello-world's two
// full-width zones — no need for a full 4-corner, screen-coordinate
// calibration.
bool calAxisIsX = false;
int16_t calTop = 0;
int16_t calBottom = 0;

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
// timeout). Body is deliberately not parsed — the hello-world only needs
// pass/fail.
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

// ---------- UI ----------

void drawButtons() {
  tft.fillScreen(TFT_BLACK);
  tft.fillRect(0, 0, SCREEN_W, SCREEN_H / 2, TFT_DARKGREEN);
  tft.fillRect(0, SCREEN_H / 2, SCREEN_W, SCREEN_H / 2, TFT_NAVY);
  tft.drawFastHLine(0, SCREEN_H / 2, SCREEN_W, TFT_WHITE);

  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(4);
  tft.setTextColor(TFT_WHITE, TFT_DARKGREEN);
  tft.drawString("CH 7", SCREEN_W / 2, SCREEN_H / 4);

  tft.setTextColor(TFT_WHITE, TFT_NAVY);
  tft.drawString("CH 13", SCREEN_W / 2, SCREEN_H * 3 / 4);
}

void showMessage(const String &msg) {
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextFont(2);
  tft.drawString(msg, SCREEN_W / 2, SCREEN_H / 2);
}

// Blocks until a touch is sampled, returning its raw point (debounced with
// a second consistent sample).
TS_Point waitForRawTouch() {
  while (true) {
    if (ts.touched()) {
      TS_Point p = ts.getPoint();
      delay(30);
      if (ts.touched()) {
        TS_Point p2 = ts.getPoint();
        if (abs(p.x - p2.x) < 200 && abs(p.y - p2.y) < 200) return p2;
      }
    }
    delay(20);
  }
}

// Two-point calibration: tap the top button, then the bottom button. We
// don't assume which raw axis (X or Y) corresponds to "vertical" on
// screen — the touch panel's wiring/rotation relationship isn't something
// we've verified, so we just use whichever axis actually separates the two
// taps. Cached in NVS (Preferences) so later boots skip straight to the
// buttons.
void calibrateTouchIfNeeded() {
  prefs.begin("remote-control", false);
  bool haveCal = prefs.isKey("calAxisIsX") && prefs.isKey("calTop") && prefs.isKey("calBottom");
  if (haveCal) {
    calAxisIsX = prefs.getBool("calAxisIsX");
    calTop = prefs.getShort("calTop");
    calBottom = prefs.getShort("calBottom");
    prefs.end();
    return;
  }

  drawButtons();
  showMessage("Tap the TOP button");
  TS_Point top = waitForRawTouch();

  showMessage("Tap the BOTTOM button");
  TS_Point bottom = waitForRawTouch();

  int16_t dx = abs(top.x - bottom.x);
  int16_t dy = abs(top.y - bottom.y);
  calAxisIsX = dx >= dy;
  calTop = calAxisIsX ? top.x : top.y;
  calBottom = calAxisIsX ? bottom.x : bottom.y;

  prefs.putBool("calAxisIsX", calAxisIsX);
  prefs.putShort("calTop", calTop);
  prefs.putShort("calBottom", calBottom);
  prefs.end();
  Serial.printf("Touch calibrated: axis=%s top=%d bottom=%d\n",
                calAxisIsX ? "x" : "y", calTop, calBottom);
}

// ---------- Main ----------

void setup() {
  Serial.begin(115200);

  pinMode(21, OUTPUT); // TFT backlight
  digitalWrite(21, HIGH);

  tft.init();
  tft.setRotation(0); // 240x320 portrait, USB connector at the bottom
  tft.fillScreen(TFT_BLACK);

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
  }

  drawButtons();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    showMessage("Reconnecting WiFi...");
    if (connectWiFi()) {
      drawButtons();
    } else {
      delay(5000);
    }
    return;
  }

  if (ts.touched() && millis() - lastFireMs > TAP_COOLDOWN_MS) {
    lastFireMs = millis();
    TS_Point p = ts.getPoint();
    int16_t value = calAxisIsX ? p.x : p.y;
    // Pick whichever calibration point this reading is closer to, rather
    // than assuming a fixed raw-value direction — handles either axis
    // orientation without needing to hardcode inversion.
    int channel = (abs(value - calTop) < abs(value - calBottom)) ? 7 : 13;
    int code = tuneChannel(channel);
    if (code != 200) {
      showMessage(code > 0 ? ("HTTP " + String(code)) : "no response");
      delay(1500);
      drawButtons();
    }
  }

  delay(20);
}
