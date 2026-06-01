# AGENTS.md — working on bird-id

Guidance for AI agents (and humans) contributing to this project. Read this
before changing code. For *usage*, see `README.md`.

## What this is

A prototype that records bird sounds and identifies them with Cornell Lab's
**BirdNET**, run locally via `birdnetlib` (no API key, no network). It runs on a
Mac today; the intended home is an **always-on Raspberry Pi** in Santa Barbara, CA.

## Architecture — keep these boundaries

Each module does one thing. Do not blur these lines; they're what make the
project testable and portable.

| Module | Responsibility | Depends on mic? | Platform-specific? |
|---|---|---|---|
| `recorder.py` | mic → wav (48kHz mono) + silence guard | **yes** | **yes — macOS AVFoundation** |
| `identifier.py` | `identify(wav)` → detections via BirdNET | no | no |
| `storage.py` | SQLite: segments + detections, queries | no | no |
| `config.py` | load `config.json`, resolve flag>config>default | no | no |
| `dashboard.py` | Flask web UI (offline, server-rendered) | no | no |
| `birdid.py` | CLI that wires the above together | — | — |

Key invariants:
- **`identify()` never touches the microphone.** The dev test loop depends on
  running it against a fixed file. Keep it a pure `wav path in → detections out`
  function so the BirdNET backend could later be swapped for an HTTP API without
  changing any caller.
- **The monitor loop stays in one process** so it reuses the cached `_ANALYZER`
  in `identifier.py`. Never shell out to `birdid.py identify` per segment — that
  reloads the whole TF model every time. Verify: "Model loaded" prints once.
- **Store raw, aggregate at query.** Write one row per 3s-window detection; roll
  up with `GROUP BY` at read time (see `species_summary`, `day_species`).
- **Dashboard is offline-friendly.** No CDN / JS chart libraries — charts are
  server-rendered CSS. It must work on a headless Pi with no internet. **`/live`**
  is the exception: ~40 lines of inline JS poll `/api/recent` every 5s (pauses when
  the tab is hidden) for the rolling 24h companion feed.

## Setup

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt          # Apple Silicon
./.venv/bin/python -m pip install -r requirements-intel-mac.txt  # Intel Mac (TF ≤2.16)
```

Always run via `./.venv/bin/python`, not the system Python.

**Config:** `config.DEFAULTS` in `config.py` is the built-in fallback. Operational
`min_conf` is **0.3** (`config.json`, dashboard, docs). `identifier.identify()` uses
the same default as a literal (it does not import `config` — keep that boundary).

## How to test (do this before claiming a change works)

The fixed dev fixture is `~/Desktop/bird.wav` (a 3s clip → Bewick's Wren ~0.92).

```bash
# mic-free identify loop — fastest check
./.venv/bin/python birdid.py identify ~/Desktop/bird.wav -c 0.1

# monitor smoke test (6s segments); confirm model loads once, seg lines appear,
# rows land in the CONFIGURED db (audio kept for all segments)
rm -f birdid.db && rm -rf recordings
(./.venv/bin/python birdid.py monitor -m 0.05 > /tmp/m.log 2>&1 &)
until [ "$(grep -c 'seg ' /tmp/m.log)" -ge 2 ]; do sleep 1; done
./.venv/bin/python birdid.py stats          # WAL: reads while monitor writes
pkill -INT -f "birdid.py monitor"

# dashboard
./.venv/bin/python birdid.py dashboard      # http://127.0.0.1:8080
```

## Automated tests

Fast unit/route tests — no mic, BirdNET, or TensorFlow (`requirements-dev.txt` is
pytest + Flask only; CI does not install `requirements.txt`):

```bash
./.venv/bin/python -m pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q
```

| Area | What is covered |
|---|---|
| `tests/test_storage.py` | Schema, `record_segment`, rollups, streaks, `recent_feed` |
| `tests/test_config.py` | `load`, `resolve`, bad JSON |
| `tests/test_identifier_summarize.py` | `summarize()`, default `min_conf` |
| `tests/test_dashboard.py` | Helpers + `/`, `/bird/…`, `/data`, `/live`, `/api/recent` via `test_client` |
| `tests/test_birdid.py` | `cmd_stats`, `_resolve_id_params` |

**Dashboard test seam:** every route uses `dashboard._db()`. Tests monkeypatch it to
return a seeded SQLite connection (see `tests/conftest.py`). Heavy imports
(`matplotlib`, `librosa`, `soundfile`) are lazy — only the spectrogram/audio routes
need them.

Run pytest after any change to `storage.py`, `config.py`, `dashboard.py`, or
`birdid.py`. Manual smokes above still apply after touching `cmd_monitor`.

**Lesson from history:** every time the monitor loop was changed, *running* it
found a bug (stdout buffering) that reading the code did not. Re-run the monitor
smoke test after touching `cmd_monitor`.

## Gotchas (these cost real debugging time)

- **Python 3.12, not 3.13** — TF/numba wheels.
- **Backend = `tensorflow` + `librosa` + `resampy`** on macOS; `tflite_runtime`
  has no mac arm64 wheels. On a Pi, birdnetlib auto-uses the lighter
  `tflite_runtime` instead. `resampy` is separately required by librosa.
- **macOS mic permission**: a blocked mic yields a *silent* wav with no error.
  `recorder.record()` measures volume and raises on silence — keep that guard.
- **Redirected stdout is block-buffered.** `cmd_monitor` calls
  `sys.stdout.reconfigure(line_buffering=True)` so live progress shows. Don't
  remove it.
- **`db` / `recordings_dir` are relative to CWD.** A systemd/cron service with a
  different working dir will silently use a *different* `birdid.db`. On the Pi,
  set `WorkingDirectory=` or use absolute paths in `config.json`.
- **BirdNET's default model includes non-bird classes** (Human, Dog, Gun,
  Fireworks) and emits low-confidence false birds from noise. The Santa Barbara
  location filter + `min_conf` (currently 0.3) handle this — don't lower the
  threshold without expecting junk.

## BirdNET detection fields

Per detection BirdNET returns exactly: `common_name`, `scientific_name`,
`confidence`, `start_time`, `end_time`, `label` (Latin_English). bird-id adds
`heard_at` (absolute local timestamp) and segment-level volume/path.

## Conventions

- Standard library first; add a dependency only when it earns its place. New
  runtime deps must have arm64 Linux (Pi) wheels; test-only deps go in
  `requirements-dev.txt`.
- Keep modules small and single-purpose. Match the existing comment density and
  docstring style.
- After adding a runtime dependency, regenerate `requirements.txt` via `pip freeze`.
- User-facing usage lives in `README.md`; architecture, testing, and gotchas live
  here. Keep both in sync when commands or defaults change.

## Roadmap / open work

- [ ] **Pi: ALSA capture path in `recorder.py`** (only non-portable module).
- [ ] Rare/new-species alerts (desktop or email).
- [ ] Optional HTTP backend behind `identify()`.
