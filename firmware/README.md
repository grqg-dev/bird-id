# bird-id ESP32-S3 sensor firmware

A battery-or-USB powered microphone node that listens for birds and uploads only
the audio that matters. It runs a tiny **Edge Impulse "bird vs. no bird"**
classifier on-device; when a bird is likely present it POSTs a **48 kHz WAV** clip
to the bird-id **ingest server**, which runs full BirdNET and files the species
under the call's true time, attributed to this device. Multiple sensors can report
to one server — each shows up on the dashboard `/devices` page.

## Why 48 kHz upload but 16 kHz detection?

The detector only needs 16 kHz to answer "bird?", so we decimate 3:1 for it. But we
capture and **upload 48 kHz** — BirdNET's native rate — so the server identifies on
full-fidelity audio (high-frequency calls intact) and the stored clip/spectrogram
look great. The N16R8's 8 MB PSRAM holds the 48 kHz ring buffer comfortably.

## Hardware

- **ESP32-S3-N16R8** (16 MB flash, 8 MB OPI PSRAM). PSRAM is required.
- **I2S MEMS mic**: INMP441 or SPH0645. Default wiring (change in `config.h`):

  | Mic pin | ESP32-S3 |
  |---------|----------|
  | SCK/BCLK | GPIO5 |
  | WS/LRCL  | GPIO6 |
  | SD/DOUT  | GPIO4 |
  | VDD      | 3V3 |
  | GND / SEL/L | GND |

## Setup

1. Install [PlatformIO](https://platformio.org/) (`pip install platformio` or the
   VS Code extension).
2. `cp src/config.h.example src/config.h` and fill in Wi-Fi, `INGEST_BASE_URL`,
   and `DEVICE_UID`. Leave `DEVICE_API_KEY` empty for now.
3. Build the Edge Impulse model into `lib/ei-bird-model/` — see that folder's
   README. (You can skip this initially; detection is stubbed so you can verify the
   rest of the pipeline first.)
4. Flash and watch serial:

   ```bash
   pio run -t upload && pio device monitor
   ```

## First-boot registration

With `DEVICE_API_KEY` empty, the device calls `POST /api/register` on boot and
prints the returned key:

```
REGISTERED. Put this in config.h DEVICE_API_KEY and reflash:
  Xk9...long-key...
```

Paste it into `config.h` as `DEVICE_API_KEY` and reflash so the key survives
reboots/reflashes. (The server stores only a hash and can't return it again.)

## What you should see

- `WiFi: connected` → `NTP: synced` → `listening…`
- When a bird calls (or with the threshold lowered for testing):
  `BIRD detected @ 2026-06-11T19:00:00+00:00` then `upload: HTTP 200`
- The detection appears on the dashboard `/live` feed and `/devices`, timed at the
  **capture** moment — even if Wi-Fi hiccupped and the upload was retried from the
  PSRAM queue.

## Timestamp integrity

`captured_at` is stamped from the NTP-synced clock at the moment of detection and
sent with the upload; the server trusts it. A queued/retried clip keeps its
original `captured_at`. If NTP never synced, the upload is flagged
`clock_unsynced` and the server falls back to receipt time (and logs it).

## Notes / next steps

- Transport is plain Wi-Fi + HTTP for v1. MQTT/BLE, OTA updates, deep-sleep power
  management, and FLAC/Opus compression of the upload are deliberately out of scope
  here (see the project plan).
- The I2S path uses the legacy `driver/i2s.h` API (stable on Arduino-ESP32 2.x as
  shipped by PlatformIO's `espressif32`). If you move to Arduino-ESP32 3.x / IDF 5,
  migrate to the `ESP_I2S` / `i2s_std` driver.
