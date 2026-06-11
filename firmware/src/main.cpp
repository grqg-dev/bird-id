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

static int g_bird_streak = 0;

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
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print('.');
  }
  Serial.printf("\nWiFi: connected, ip=%s\n", WiFi.localIP().toString().c_str());
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
  for (size_t i = 0; i < samples; i++) pcm[i] = g_ring[(start + i) % CLIP_SAMPLES];

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
  http.begin(String(INGEST_BASE_URL) + "/api/register");
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

// POST one WAV as multipart/form-data. Returns the HTTP status (or <0 on error).
static int uploadWav(const uint8_t *wav, size_t len, const char *captured_at,
                     bool used_receipt_time) {
  HTTPClient http;
  http.begin(String(INGEST_BASE_URL) + "/api/ingest");
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

// Try to flush the retry queue (oldest first). Stops on the first failure.
static void flushPending() {
  while (g_pending_count > 0) {
    PendingUpload &p = g_pending[0];
    int code = uploadWav(p.wav, p.len, p.captured_at, p.used_receipt_time);
    if (code == 200) {
      free(p.wav);
      memmove(&g_pending[0], &g_pending[1], (g_pending_count - 1) * sizeof(PendingUpload));
      g_pending_count--;
    } else {
      break;  // server down / Wi-Fi blip — keep it (and its original timestamp) for later
    }
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
static void onBirdDetected() {
  char captured_at[28];
  bool synced = nowIso(captured_at, sizeof captured_at);
  Serial.printf("BIRD detected @ %s%s\n", captured_at, synced ? "" : " (clock unsynced)");

  size_t len = 0;
  uint8_t *wav = buildClipWav(&len);
  if (!wav) { Serial.println("buildClipWav: out of PSRAM"); return; }

  int code = uploadWav(wav, len, captured_at, !synced);
  if (code == 200) free(wav);
  else enqueuePending(wav, len, captured_at, !synced);  // keep its capture time for retry
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
  ntpSync();
  registerDevice();
  i2sInit();
  Serial.println("listening…");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) wifiConnect();

  if (captureChunk()) {              // a full 16 kHz window is ready
    float score = classifyBird();
    if (score >= BIRD_SCORE_THRESHOLD) {
      if (++g_bird_streak >= BIRD_TRIGGER_WINDOWS) {
        g_bird_streak = 0;
        onBirdDetected();
      }
    } else {
      g_bird_streak = 0;
    }
    g_win16k_count = 0;             // start the next detection window
  }

  if (g_pending_count > 0) flushPending();  // opportunistically drain the retry queue
}
