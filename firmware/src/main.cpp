// bird-id ESP32-S3 sensor.
//
// Pipeline (see firmware/README.md and the project plan):
//   I2S mic @ 48 kHz mono  ->  PSRAM ring buffer (the clip we may upload)
//                          ->  3:1 decimated 16 kHz copy  ->  Edge Impulse "bird?" classifier
//   bird for N consecutive windows  ->  stamp captured_at (NTP)  ->  POST 48 kHz WAV to the
//   ingest server. Failed uploads go to a small PSRAM retry queue so a Wi-Fi blip never drops
//   a detection or rewrites its timestamp.
//
// The server runs full BirdNET on the upload, so this device only has to answer "bird or not."

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <time.h>
#include <driver/i2s.h>

#include "config.h"

// Edge Impulse exported Arduino library. Drop your export into lib/ei-bird-model/
// (see firmware/README.md). The header name follows your EI project name; adjust here.
#if __has_include("bird_inferencing.h")
  #include "bird_inferencing.h"
  #define HAVE_EI_MODEL 1
#else
  #warning "No Edge Impulse model found — build lib/ei-bird-model/ (see README). Detection is stubbed."
  #define HAVE_EI_MODEL 0
#endif

// ---------------------------------------------------------------------------
// Audio sizing
// ---------------------------------------------------------------------------
static constexpr int      DETECT_RATE   = 16000;                       // EI model input rate
static constexpr int      DECIMATE      = CAPTURE_SAMPLE_RATE / DETECT_RATE;  // 48k/16k = 3
static constexpr size_t   CLIP_SAMPLES  = (size_t)CAPTURE_SAMPLE_RATE * CLIP_SECONDS;  // 48k ring
static constexpr size_t   WINDOW_16K    = DETECT_RATE;                  // 1 s detection window
static constexpr i2s_port_t I2S_PORT    = I2S_NUM_0;

// 48 kHz int16 ring buffer (the audio we'd upload), kept in PSRAM.
static int16_t *g_ring = nullptr;
static volatile size_t g_ring_head = 0;   // next write index
static volatile size_t g_ring_filled = 0; // total samples written (saturates)

// Rolling 16 kHz detection window (decimated), refilled as 48 kHz samples arrive.
static int16_t g_win16k[WINDOW_16K];
static size_t  g_win16k_count = 0;
static int     g_decimate_phase = 0;

// Sound-activated capture state. When the newest second crosses the noise floor
// we "arm", keep capturing for a post-roll so the event lands centered (not
// sliced by the clip edge), then snapshot. A cooldown bounds upload rate.
static bool g_armed = false;
static int  g_postroll = 0;   // post-roll windows remaining for the armed capture
static int  g_cooldown = 0;   // cooldown windows remaining after a capture

// Live gate config: starts from the compile-time defaults, overridden at runtime
// by the server (GET /api/config) so the gates can be retuned from the dashboard.
static int g_cfg_noise_floor = NOISE_FLOOR_RMS;
static int g_cfg_postroll    = CAPTURE_POSTROLL_WINDOWS;
static int g_cfg_cooldown     = BIRD_TRIGGER_WINDOWS;

// ---------------------------------------------------------------------------
// Upload retry queue (PSRAM). Each entry owns a malloc'd WAV byte buffer + the
// capture timestamp string, so a retried upload keeps its ORIGINAL time.
// ---------------------------------------------------------------------------
struct PendingUpload {
  uint8_t *wav;
  size_t   len;
  char     captured_at[28];  // ISO-8601 UTC, e.g. 2026-06-11T19:00:00+00:00
  bool     used_receipt_time;
};
static constexpr int MAX_PENDING = 4;
static PendingUpload g_pending[MAX_PENDING];
static int g_pending_count = 0;

// ---------------------------------------------------------------------------
// Time helpers
// ---------------------------------------------------------------------------
static bool clockSynced() {
  return time(nullptr) > 1700000000;  // ~2023-11; anything earlier = NTP not yet applied
}

// Fill `out` with the current time in ISO-8601 UTC. Returns false if unsynced.
static bool nowIso(char *out, size_t n) {
  time_t t = time(nullptr);
  struct tm tm_utc;
  gmtime_r(&t, &tm_utc);
  strftime(out, n, "%Y-%m-%dT%H:%M:%S+00:00", &tm_utc);
  return clockSynced();
}

// ---------------------------------------------------------------------------
// Wi-Fi + NTP
// ---------------------------------------------------------------------------
static void wifiConnect() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("WiFi: connecting to %s", WIFI_SSID);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print('.');
    // No timeout = a router blip at boot strands the sensor forever. Reboot to
    // retry cleanly instead (also covers runtime drops via the loop() recheck).
    if (millis() - start > (uint32_t)WIFI_CONNECT_TIMEOUT_S * 1000) {
      Serial.println("\nWiFi: connect timed out — rebooting to retry");
      delay(100);
      ESP.restart();
    }
  }
  Serial.printf("\nWiFi: connected, ip=%s\n", WiFi.localIP().toString().c_str());
}

// Resolved "http://<ip>:<port>" base for the ingest server, filled by
// resolveIngestBase() once Wi-Fi is up; used by registerDevice()/uploadWav().
static String g_ingest_base;

// Resolve INGEST_HOST to a base URL. An mDNS ".local" name is looked up via
// ESPmDNS; anything else (plain IP / DNS name) is used verbatim. Falls back to
// INGEST_FALLBACK_IP if the lookup fails, so a multicast hiccup doesn't strand us.
static void resolveIngestBase() {
  String host = INGEST_HOST;
  String ip;
  if (host.endsWith(".local")) {
    MDNS.begin("birdid-sensor");                       // our own mDNS label (any valid name)
    String name = host.substring(0, host.length() - 6);  // strip ".local"
    Serial.printf("mDNS: resolving %s ...\n", host.c_str());
    IPAddress addr = MDNS.queryHost(name, 3000);       // 3 s timeout
    if (addr != IPAddress(0, 0, 0, 0)) ip = addr.toString();
    else Serial.printf("mDNS: '%s' not found — using fallback %s\n", host.c_str(), INGEST_FALLBACK_IP);
  } else {
    ip = host;                                         // plain IP or DNS name
  }
  if (ip.length() == 0) ip = INGEST_FALLBACK_IP;
  g_ingest_base = String("http://") + ip + ":" + String(INGEST_PORT);
  Serial.printf("ingest server: %s\n", g_ingest_base.c_str());
}

static void ntpSync() {
  configTime(0, 0, NTP_SERVER);  // UTC; the ingest server localizes
  Serial.print("NTP: syncing");
  for (int i = 0; i < 30 && !clockSynced(); i++) {
    delay(500);
    Serial.print('.');
  }
  if (clockSynced()) {
    char iso[28];
    nowIso(iso, sizeof iso);
    Serial.printf("\nNTP: synced, now=%s\n", iso);
  } else {
    Serial.println("\nNTP: NOT synced — uploads will be flagged clock_unsynced");
  }
}

// ---------------------------------------------------------------------------
// I2S capture (INMP441 / SPH0645: 24-bit sample in a 32-bit slot, mono)
// ---------------------------------------------------------------------------
static void i2sInit() {
  i2s_config_t cfg = {
      .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = CAPTURE_SAMPLE_RATE,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
      .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 8,
      .dma_buf_len = 256,
      .use_apll = true,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0,
  };
  i2s_pin_config_t pins = {
      .bck_io_num = I2S_SCK_PIN,
      .ws_io_num = I2S_WS_PIN,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = I2S_SD_PIN,
  };
  i2s_driver_install(I2S_PORT, &cfg, 0, nullptr);
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

// Read a chunk of 48 kHz samples, push to the 48 kHz ring, and feed every 3rd
// sample into the 16 kHz detection window. Returns true when a full 16 kHz
// detection window is ready (g_win16k filled).
static bool captureChunk() {
  static int32_t raw[256];
  size_t bytes_read = 0;
  i2s_read(I2S_PORT, raw, sizeof raw, &bytes_read, portMAX_DELAY);
  size_t n = bytes_read / sizeof(int32_t);

  bool window_ready = false;
  for (size_t i = 0; i < n; i++) {
    // 24-bit left-justified in 32 bits -> int16.
    int16_t s = (int16_t)(raw[i] >> 14);

    g_ring[g_ring_head] = s;
    g_ring_head = (g_ring_head + 1) % CLIP_SAMPLES;
    if (g_ring_filled < CLIP_SAMPLES) g_ring_filled++;

    if (++g_decimate_phase >= DECIMATE) {  // simple 3:1 decimation
      g_decimate_phase = 0;
      if (g_win16k_count < WINDOW_16K) {
        g_win16k[g_win16k_count++] = s;
        if (g_win16k_count == WINDOW_16K) window_ready = true;
      }
    }
  }
  return window_ready;
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------
#if HAVE_EI_MODEL
static int ei_get_data(size_t offset, size_t length, float *out) {
  for (size_t i = 0; i < length; i++) out[i] = (float)g_win16k[offset + i];
  return 0;
}
#endif

// Run the Edge Impulse classifier on the current 16 kHz window; return the
// "bird" class score in [0,1]. Stubbed to 0 until a model is built in.
static float classifyBird() {
#if HAVE_EI_MODEL
  signal_t signal;
  signal.total_length = WINDOW_16K;
  signal.get_data = &ei_get_data;
  ei_impulse_result_t result;
  if (run_classifier(&signal, &result, false) != EI_IMPULSE_OK) return 0.0f;
  for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
    if (strcmp(result.classification[i].label, BIRD_LABEL) == 0) {
      return result.classification[i].value;
    }
  }
  return 0.0f;
#else
  return 0.0f;  // no model yet — see README
#endif
}

// RMS of `count` ring samples starting at `start`, after a TWO-pole high-pass
// (cascaded one-poles, ~300 Hz/stage, 12 dB/oct). The high-pass strips the
// INMP441's DC drift AND the dominant 50-120 Hz mains/rumble pickup so the level
// reflects real audio-band sound, not hum. (A one-pole HPF is too shallow — 60 Hz
// leaks through and swamps the metric.) Birds live well above this corner.
static float acLevel(size_t start, size_t count) {
  if (count == 0) return 0.0f;
  const float R = 0.96f;                 // cascaded one-pole HPFs, ~300 Hz corner
  float x1 = 0.0f, y1 = 0.0f;            // stage 1 state
  float w1 = 0.0f, z1 = 0.0f;            // stage 2 state
  double acc = 0.0;
  for (size_t i = 0; i < count; i++) {
    float x = (float)g_ring[(start + i) % CLIP_SAMPLES];
    float y = x - x1 + R * y1;           // stage 1
    x1 = x; y1 = y;
    float z = y - w1 + R * z1;           // stage 2 (input = stage-1 output)
    w1 = y; z1 = z;
    acc += (double)z * z;
  }
  return (float)sqrt(acc / (double)count);
}

// Audio-band level of the whole clip we'd upload (ring, oldest-first like buildClipWav).
static float clipAcLevel() {
  size_t start = (g_ring_filled < CLIP_SAMPLES) ? 0 : g_ring_head;
  return acLevel(start, g_ring_filled);
}

// Audio-band level of just the NEWEST ~1 s of the ring — "is there sound right
// now?". Used to trigger sound-activated capture so events aren't sliced by the
// fixed clip boundary.
static float windowAcLevel() {
  size_t W = CAPTURE_SAMPLE_RATE;        // 1 second
  size_t avail = (g_ring_filled < W) ? g_ring_filled : W;
  if (avail == 0) return 0.0f;
  size_t start = (g_ring_head + CLIP_SAMPLES - avail) % CLIP_SAMPLES;
  return acLevel(start, avail);
}

// ---------------------------------------------------------------------------
// WAV building (48 kHz mono 16-bit, the full ring in chronological order)
// ---------------------------------------------------------------------------
static void putLE32(uint8_t *p, uint32_t v) { p[0]=v; p[1]=v>>8; p[2]=v>>16; p[3]=v>>24; }
static void putLE16(uint8_t *p, uint16_t v) { p[0]=v; p[1]=v>>8; }

// Allocate (PSRAM) and fill a WAV of the ring's contents. Caller frees.
static uint8_t *buildClipWav(size_t *out_len) {
  size_t samples = g_ring_filled;                 // up to CLIP_SAMPLES
  size_t data_bytes = samples * sizeof(int16_t);
  size_t total = 44 + data_bytes;
  uint8_t *buf = (uint8_t *)ps_malloc(total);
  if (!buf) return nullptr;

  memcpy(buf, "RIFF", 4);            putLE32(buf + 4, total - 8);
  memcpy(buf + 8, "WAVEfmt ", 8);    putLE32(buf + 16, 16);
  putLE16(buf + 20, 1);              putLE16(buf + 22, 1);            // PCM, mono
  putLE32(buf + 24, CAPTURE_SAMPLE_RATE);
  putLE32(buf + 28, CAPTURE_SAMPLE_RATE * 2);                        // byte rate
  putLE16(buf + 32, 2);             putLE16(buf + 34, 16);           // block align, bits
  memcpy(buf + 36, "data", 4);      putLE32(buf + 40, data_bytes);

  // Oldest-first: ring start is head when full, else 0.
  size_t start = (g_ring_filled < CLIP_SAMPLES) ? 0 : g_ring_head;
  int16_t *pcm = (int16_t *)(buf + 44);
  // High-pass the uploaded audio (~230 Hz, 2-pole) so low-frequency rumble —
  // traffic, wind, the mic's DC drift — doesn't dominate the clip BirdNET sees.
  // Birds sit well above this corner. NOTE this only cleans the *stored* audio;
  // the gate still measures the raw ring (acLevel), so triggering is unchanged.
  const float R = 0.97f;
  float x1 = 0.0f, y1 = 0.0f, w1 = 0.0f, z1 = 0.0f;
  for (size_t i = 0; i < samples; i++) {
    float x = (float)g_ring[(start + i) % CLIP_SAMPLES];
    float y = x - x1 + R * y1;  x1 = x; y1 = y;   // stage 1
    float z = y - w1 + R * z1;  w1 = y; z1 = z;   // stage 2
    int v = (int)(z >= 0 ? z + 0.5f : z - 0.5f);  // round to int16, clamped
    pcm[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
  }

  *out_len = total;
  return buf;
}

// ---------------------------------------------------------------------------
// HTTP: register + multipart ingest
// ---------------------------------------------------------------------------
static String g_api_key = DEVICE_API_KEY;

// First-boot registration when no API key is baked in. Prints the key to serial
// so you can paste it into config.h and reflash for persistence.
static void registerDevice() {
  if (g_api_key.length() > 0) return;
  HTTPClient http;
  http.begin(g_ingest_base + "/api/register");
  http.setTimeout(HTTP_TIMEOUT_S * 1000);
  http.addHeader("Content-Type", "application/json");
  String body = String("{\"device_uid\":\"") + DEVICE_UID + "\"}";
  int code = http.POST(body);
  if (code == 200) {
    String resp = http.getString();
    int k = resp.indexOf("\"api_key\":\"");
    if (k >= 0) {
      int s = k + 11, e = resp.indexOf('"', s);
      g_api_key = resp.substring(s, e);
      Serial.printf("REGISTERED. Put this in config.h DEVICE_API_KEY and reflash:\n  %s\n",
                    g_api_key.c_str());
    } else {
      Serial.println("Already registered but no key returned — set DEVICE_API_KEY in config.h.");
    }
  } else {
    Serial.printf("register failed: HTTP %d\n", code);
  }
  http.end();
}

// Pull an integer JSON field like "key":123 out of a flat response body. Returns
// false if the key is absent (so we keep the current value). Avoids pulling in a
// JSON parser for three small ints.
static bool jsonInt(const String &body, const char *key, int &out) {
  String pat = String("\"") + key + "\":";
  int k = body.indexOf(pat);
  if (k < 0) return false;
  out = (int)body.substring(k + pat.length()).toInt();  // toInt() stops at ',' or '}'
  return true;
}

// Poll the server for this device's gate overrides and apply them. Unset knobs are
// omitted by the server, so we keep the compile-time default for those. On any
// error we keep the current values (fail safe).
static uint32_t g_last_config_ms = 0;
static void fetchConfig() {
  g_last_config_ms = millis();
  HTTPClient http;
  http.begin(g_ingest_base + "/api/config?device_uid=" + DEVICE_UID + "&api_key=" + g_api_key
             + "&rssi=" + String(WiFi.RSSI()));   // report signal strength on each check-in
  http.setTimeout(HTTP_TIMEOUT_S * 1000);
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    jsonInt(body, "noise_floor", g_cfg_noise_floor);
    jsonInt(body, "cooldown_windows", g_cfg_cooldown);
    jsonInt(body, "postroll_windows", g_cfg_postroll);
    Serial.printf("config: noise_floor=%d cooldown=%d postroll=%d\n",
                  g_cfg_noise_floor, g_cfg_cooldown, g_cfg_postroll);
  } else {
    Serial.printf("config fetch: HTTP %d (keeping current gates)\n", code);
  }
  http.end();
}

// POST one WAV as multipart/form-data. Returns the HTTP status (or <0 on error).
static int uploadWav(const uint8_t *wav, size_t len, const char *captured_at,
                     bool used_receipt_time) {
  HTTPClient http;
  http.begin(g_ingest_base + "/api/ingest");
  http.setTimeout(HTTP_TIMEOUT_S * 1000);
  const char *boundary = "----birdid8s3boundary";
  http.addHeader("Content-Type", String("multipart/form-data; boundary=") + boundary);

  auto field = [&](const String &name, const String &val) {
    return String("--") + boundary + "\r\nContent-Disposition: form-data; name=\"" +
           name + "\"\r\n\r\n" + val + "\r\n";
  };
  String head = field("device_uid", DEVICE_UID) + field("api_key", g_api_key) +
                field("captured_at", captured_at);
  if (used_receipt_time) head += field("clock_unsynced", "1");
  head += String("--") + boundary +
          "\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"clip.wav\"\r\n" +
          "Content-Type: audio/wav\r\n\r\n";
  String tail = String("\r\n--") + boundary + "--\r\n";

  size_t total = head.length() + len + tail.length();
  uint8_t *body = (uint8_t *)ps_malloc(total);
  if (!body) { http.end(); return -1; }
  memcpy(body, head.c_str(), head.length());
  memcpy(body + head.length(), wav, len);
  memcpy(body + head.length() + len, tail.c_str(), tail.length());

  int code = http.POST(body, total);
  free(body);
  if (code > 0) Serial.printf("upload: HTTP %d %s\n", code, http.getString().c_str());
  else Serial.printf("upload error: %s\n", http.errorToString(code).c_str());
  http.end();
  return code;
}

// Whether a failed upload is worth retrying. Transient: connection errors (code<0),
// request timeout (408), rate limit (429), server errors (>=500). Anything else is a
// permanent 4xx (404 unknown device, 401 bad key, 400 bad request) — retrying just
// spams the server, so drop the clip instead.
static bool isRetryable(int code) {
  return code < 0 || code == 408 || code == 429 || code >= 500;
}

// Try to flush the retry queue (oldest first). Stops on the first transient failure;
// drops clips the server permanently rejected so they don't loop forever.
static void flushPending() {
  while (g_pending_count > 0) {
    PendingUpload &p = g_pending[0];
    int code = uploadWav(p.wav, p.len, p.captured_at, p.used_receipt_time);
    if (code != 200 && isRetryable(code)) {
      break;  // server down / Wi-Fi blip — keep it (and its original timestamp) for later
    }
    if (code != 200) Serial.printf("dropping rejected pending clip (HTTP %d)\n", code);
    free(p.wav);
    memmove(&g_pending[0], &g_pending[1], (g_pending_count - 1) * sizeof(PendingUpload));
    g_pending_count--;
  }
}

static void enqueuePending(uint8_t *wav, size_t len, const char *captured_at, bool urt) {
  if (g_pending_count >= MAX_PENDING) {  // drop the oldest to bound PSRAM use
    free(g_pending[0].wav);
    memmove(&g_pending[0], &g_pending[1], (MAX_PENDING - 1) * sizeof(PendingUpload));
    g_pending_count--;
    Serial.println("retry queue full — dropped oldest pending clip");
  }
  PendingUpload &p = g_pending[g_pending_count++];
  p.wav = wav;
  p.len = len;
  strncpy(p.captured_at, captured_at, sizeof p.captured_at - 1);
  p.captured_at[sizeof p.captured_at - 1] = '\0';
  p.used_receipt_time = urt;
}

// A bird was detected: stamp the time, build the clip, upload (or queue on failure).
static void captureAndUpload() {
  char captured_at[28];
  bool synced = nowIso(captured_at, sizeof captured_at);
  Serial.printf("capture @ %s (ac_rms %.0f)%s\n",
                captured_at, clipAcLevel(), synced ? "" : " (clock unsynced)");

  size_t len = 0;
  uint8_t *wav = buildClipWav(&len);
  if (!wav) { Serial.println("buildClipWav: out of PSRAM"); return; }

  int code = uploadWav(wav, len, captured_at, !synced);
  if (code == 200) {
    free(wav);
  } else if (isRetryable(code)) {
    enqueuePending(wav, len, captured_at, !synced);  // transient — keep its capture time for retry
  } else {
    Serial.printf("upload rejected (HTTP %d) — dropping clip\n", code);
    free(wav);  // permanent (e.g. 404 unknown device) — don't loop on it
  }
}

// ---------------------------------------------------------------------------
// Arduino entry points
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);

  if (!psramFound()) {
    Serial.println("FATAL: PSRAM not found — need an N16R8 (or BOARD_HAS_PSRAM).");
    while (true) delay(1000);
  }
  g_ring = (int16_t *)ps_malloc(CLIP_SAMPLES * sizeof(int16_t));
  if (!g_ring) { Serial.println("FATAL: ring alloc failed"); while (true) delay(1000); }

  wifiConnect();
  resolveIngestBase();
  ntpSync();
  registerDevice();
  fetchConfig();              // pull remote gate overrides (else keep defaults)
  i2sInit();
  Serial.println("listening…");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) wifiConnect();

  if (captureChunk()) {              // a full 16 kHz window is ready (~1 s)
    float score = classifyBird();
    bool hasSound = (g_cfg_noise_floor <= 0) || (windowAcLevel() >= (float)g_cfg_noise_floor);
    bool wantClip = hasSound && (score >= BIRD_SCORE_THRESHOLD);  // stub score=0, thr=-1 => sound-only

    if (g_cooldown > 0) g_cooldown--;

    if (g_armed) {
      // Keep capturing the post-roll, then snapshot so the triggering sound is
      // centered in the 3 s ring (pre-roll already buffered, post-roll just added).
      if (--g_postroll <= 0) {
        captureAndUpload();
        g_armed = false;
        g_cooldown = g_cfg_cooldown;    // min gap before the next capture
      }
    } else if (wantClip && g_cooldown == 0) {
      g_armed = true;
      g_postroll = g_cfg_postroll;
    }
    g_win16k_count = 0;             // start the next detection window
  }

  if (g_pending_count > 0) flushPending();  // opportunistically drain the retry queue

  // Periodically refresh remote gate config (lets the dashboard retune us live).
  if (millis() - g_last_config_ms > (uint32_t)CONFIG_POLL_S * 1000) fetchConfig();
}
