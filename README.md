# bird-id (prototype)

Record bird sounds and identify them with Cornell Lab's **BirdNET** model, run
locally via [`birdnetlib`](https://github.com/joeweiss/birdnetlib). No API key,
no network — the model runs on your machine.

> Note: There is no free, official Cornell *REST API* for bird-sound ID. BirdNET
> is the actual Cornell model, and `birdnetlib` is the standard way to run it.
> `identify()` is kept behind a clean function boundary so it could later be
> swapped for a hosted HTTP API without touching the recorder or CLI.

## Layout

| File            | Chunk                                                            |
|-----------------|-----------------------------------------------------------------|
| `recorder.py`   | Capture mic audio → wav (48 kHz mono). Knows nothing about ID.   |
| `identifier.py` | `identify(wav_path)` → detections via BirdNET. Never uses mic.   |
| `birdid.py`     | CLI gluing them: `record`, `identify`, `listen`.                 |

The two chunks are independent: `identify` takes a file in and gives detections
out, so you can iterate on it with a fixed local file and never touch the mic.

## Setup

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

(Built and tested on macOS / Apple Silicon, Python 3.12. The backend is
`tensorflow` + `librosa` + `resampy`; `tflite_runtime` has no mac arm64 wheels.)

## The dev test loop (no mic)

```bash
./.venv/bin/python birdid.py identify ~/Desktop/bird.wav -c 0.1
```

Expected on the sample file:

```
  Bewick's Wren (Thryomanes bewickii)  conf=0.918  [0-3s]
  Oak Titmouse (Baeolophus inornatus)  conf=0.233  [0-3s]
```

## Recording from the mic

```bash
./.venv/bin/python birdid.py record recordings/sample.wav -t 5
./.venv/bin/python birdid.py listen -t 5 -c 0.25      # record then identify
```

`record()` measures volume and **fails loudly if the capture is silent**, which
is the usual symptom of a missing macOS mic permission:
*System Settings → Privacy & Security → Microphone → enable your terminal app.*

List input devices / pick a different mic:

```bash
./.venv/bin/python recorder.py --list-devices
./.venv/bin/python birdid.py record -d 1 -t 5
```

## Big / long files

BirdNET analyzes audio in fixed **3-second windows**, and `birdnetlib` slices a
file of any length into those windows automatically and reads it in batches — so
**you never chop the file up yourself**. Just point `identify` at it.

The catch: a long file emits one detection *per 3s window per species*, so a
10-minute recording is hundreds of near-duplicate rows. Use `--summary` to roll
them up into one row per species (count of windows, peak confidence, time span):

```bash
./.venv/bin/python birdid.py identify big_field_recording.wav -c 0.25 --summary
#   Bewick's Wren (Thryomanes bewickii)  peak=0.807  x9 windows  [0-27s]
#   Oak Titmouse (Baeolophus inornatus)  peak=0.563  x9 windows  [0-27s]
```

## Continuous monitoring (the main mode)

Record back-to-back N-minute segments, identify each, and log results to a local
SQLite database (`birdid.db`). Sequential by design — a few seconds of analysis
gap between segments is fine, and the loop self-heals across laptop sleep.

```bash
./.venv/bin/python birdid.py monitor -m 5 -c 0.5      # 5-min segments, conf>=0.5
./.venv/bin/python birdid.py stats                    # running per-species tally
```

- Segment audio is kept **only if it had detections**; empty segments are deleted.
- Each detection is stored with an absolute `heard_at` timestamp (local time).
- `stats` reads through WAL, so you can run it in another terminal while the
  monitor is writing.
- Ctrl-C stops cleanly and prints a final tally.

Tip: BirdNET's default model also has non-bird classes (Human, Dog, Gun, etc.)
and will emit low-confidence false birds from ambient noise. Use a higher
`-c/--min-conf` (~0.5) and `--lat/--lon` for trustworthy outdoor results.

## Configuration (set location once)

Copy `config.example.json` to `config.json` and set your location. Resolution
order for any value is **CLI flag → config.json → built-in default**.

```json
{ "lat": 34.4208, "lon": -119.6982, "min_conf": 0.5,
  "db": "birdid.db", "recordings_dir": "recordings" }
```

With `lat`/`lon` set, BirdNET restricts predictions to species plausible at your
location and time of year — this is the biggest accuracy win (no more Whooper
Swans in a California backyard). Current config is set to Santa Barbara, CA.

## Daily digest

```bash
./.venv/bin/python birdid.py digest            # today
./.venv/bin/python birdid.py digest --date 2026-05-30
```

Shows species count, first-bird / last-bird times, busiest hour, and which
species are **new to your records**.

## Web dashboard

```bash
./.venv/bin/python birdid.py dashboard         # http://127.0.0.1:8080
```

Today's overview + hourly activity chart, all-time species table, and a feed of
recent detections with a play button and an on-the-fly **spectrogram** per clip.
Server-rendered (no internet/CDN needed) so it runs fine on a headless Pi —
use `--host 0.0.0.0` to reach it from another device on your network.

## Useful options

- `-c / --min-conf` — confidence threshold (0–1); default from config (0.5).
- `--lat` / `--lon` — override the configured location for one run.

## Status / next ideas

- [x] Record audio (48 kHz mono) with silence guard
- [x] Identify via BirdNET behind a clean `identify()` boundary
- [x] CLI: record / identify / listen
- [x] Big-file support (auto-chunked) with `--summary` per-species rollup
- [x] Continuous `monitor` mode → SQLite, with `stats` reporting
- [x] Location/season filter via `config.json` (set to Santa Barbara, CA)
- [x] Daily `digest` (species, timing, new-to-you birds)
- [x] Web `dashboard` (overview, hourly chart, clips + spectrograms)
- [ ] Raspberry Pi deployment: ALSA capture path in `recorder.py` (currently macOS/AVFoundation only)
- [ ] Rare/new-species alerts (desktop or email)
- [ ] Swap-in HTTP backend behind `identify()` if a hosted API is ever wanted
