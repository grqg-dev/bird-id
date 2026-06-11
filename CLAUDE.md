# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

**[`AGENTS.md`](AGENTS.md) is the authoritative agent guide** — environments (dev
vs prod), architecture boundaries, invariants, the manual monitor smoke test, the
deploy flow, and hard-won gotchas. Read it before changing code. `README.md` is
user-facing usage. This file is a quick orientation; AGENTS.md has the depth.

## What this is

A local bird-call identifier: a mic streams audio → adaptive DSP cuts it into
segments → Cornell Lab's **BirdNET** (via `birdnetlib`, no API key, no network)
labels species → SQLite stores raw per-window detections → a Flask dashboard
renders a browsable "Bird-Dex." Prod is an always-on Mac mini in Santa Barbara;
the longer-term target is a Raspberry Pi.

## Commands

Always run via `./.venv/bin/python`, never system Python.

```bash
# Setup
python3.12 -m venv .venv                                        # 3.12, NOT 3.13 (TF/numba wheels)
./.venv/bin/python -m pip install -r requirements.txt           # Apple Silicon
./.venv/bin/python -m pip install -r requirements-intel-mac.txt # Intel Mac (TF ≤2.16)

# Tests (mic-free, no BirdNET/TensorFlow — CI installs only requirements-dev.txt)
./.venv/bin/python -m pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q
./.venv/bin/python -m pytest tests/test_storage.py -q           # single file
./.venv/bin/python -m pytest tests/test_dashboard.py::test_name # single test

# CLI (subcommands: record, identify, listen, monitor, cleanup, stats, digest, dashboard)
./.venv/bin/python birdid.py identify ~/Desktop/bird.wav -c 0.1 # mic-free check (fastest)
./.venv/bin/python birdid.py monitor                            # streaming record + identify loop
./.venv/bin/python birdid.py dashboard                          # http://127.0.0.1:8080
```

After touching `cmd_monitor`, the unit tests are not enough — **run the monitor
smoke test in AGENTS.md** ("How to test"). History shows running the loop catches
bugs (e.g. stdout buffering) that reading the code does not.

## Architecture

Single-purpose modules wired together by `birdid.py`. Keep the boundaries — they
are what make the project testable and Pi-portable:

- `recorder.py` — mic → 48kHz mono wav + `stream_pcm()` continuous pipe. **Only
  mic-dependent, platform-specific module** (macOS AVFoundation; ALSA path TODO).
- `segmenter.py` — adaptive DSP: PCM hops → variable-length `Segment` objects.
- `identifier.py` — `identify(wav) → detections` via BirdNET. **Pure file-in →
  detections-out; never touches the mic and never imports `config`** (so the
  backend could be swapped for an HTTP API without changing callers).
- `storage.py` — SQLite: segments, tracks, detections, queries.
- `clips.py` — slice a segment wav into per-window clip files.
- `config.py` — load `config.json`; resolution is **flag > config.json > DEFAULTS**.
- `dashboard.py` — Flask web UI (server-rendered, offline-friendly).
- `ingest.py` — Flask HTTP service receiving ESP32-S3 sensor clips (`firmware/`) →
  `identify` → store with a `device_id`. **Owns BirdNET** (lazy `_identify` seam);
  `birdid.py ingest-server`. The dashboard stays TF-free — don't import `identifier`
  there. Self-hostable via `Dockerfile`/`docker-compose.yml` (ingest + dashboard).

### Invariants that bite if broken

- **Monitor is one process, two threads.** A capture thread runs
  `stream_pcm()` + `segment_stream()`; the main thread processes segments so the
  mic pipe never blocks on BirdNET. Never shell out to `birdid.py identify` per
  segment — that reloads the TF model every time. Verify "Model loaded" prints once.
- **Store raw, aggregate at query.** One row per 3s-window detection; roll up with
  `GROUP BY` at read time (`species_summary`, `day_species`).
- **Tracks + clips.** Each `(start,end)` window in a segment gets a `tracks` row
  (optional `clip_path`); detections link via `track_id`. `birdid.py` writes clip
  files via `clips.write_clip()`; `storage.py` only stores paths.
- **Timestamps survive sleep.** `started_at` comes from an audio-sample clock;
  `segmenter._flush()` resyncs forward-only when wall time drifts >60s ahead.
- **Dashboard is offline.** No CDN / JS chart libs — charts are server-rendered
  CSS. Exceptions: `/live` polls `/api/recent`, and `/call` uses **vendored**
  `static/three.min.js` + `OrbitControls.js` (pinned three.js r134) — never a CDN.
- **`db` / `recordings_dir` are relative to CWD.** A launchd/systemd unit with a
  different working dir silently uses a *different* `birdid.db`.

### Dashboard test seam

Every route calls `dashboard._db()`; tests monkeypatch it to a seeded SQLite
connection (`tests/conftest.py`). Heavy imports (`matplotlib`, `librosa`,
`soundfile`) are lazy so only spectrogram/audio routes pull them in.

## Prod safety (Mac mini)

Prod is the source of truth for `birdid.db` and `recordings/`. **Data flows prod →
dev only; code/content flows dev → prod via git + deploy.** Do not `git pull`,
deploy, restart launch agents, or run backfills on prod without explicit user
confirmation. Querying prod: `ssh mac-mini 'cd ~/bird-id && ...'`. See AGENTS.md
for the full deploy flow and pull scripts.

## Conventions

- Standard library first; new runtime deps must have arm64 Linux (Pi) wheels.
  Test-only deps go in `requirements-dev.txt`; after adding a runtime dep,
  regenerate `requirements.txt` via `pip freeze`.
- `min_conf` is **0.3** operationally — lowering it lets in junk (BirdNET emits
  non-bird classes like Human/Dog and low-confidence false birds from noise).
- Keep `README.md` (usage) and `AGENTS.md` (architecture/testing/deploy) in sync
  when commands or defaults change.
