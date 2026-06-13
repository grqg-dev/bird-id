# AGENTS.md — working on bird-id

Guidance for AI agents (and humans) contributing to this project. Read this
before changing code. For *usage*, see `README.md`.

## Environments — dev vs prod

| | **Dev (this machine)** | **Prod (Mac mini)** |
|---|---|---|
| Role | Hack, test, write content | Always-on backyard monitor + dashboard |
| SSH | — | `mac-mini` (`~/.ssh/config`) |
| Path | local checkout | `/Users/matt/bird-id` (user `matt`) |
| Python | Apple Silicon → `requirements.txt` | **Intel** → `requirements-intel-mac.txt` |
| `birdid.db` | Small / stale unless pulled from prod | **Source of truth** — live detections |
| `recordings/` | Optional pull for playback tests | Full segment + clip history |
| `config.json` | Local copy (gitignored) | Prod copy on mini (gitignored) |
| Monitor | Manual smoke tests only | Runs 24/7 (`birdid.py monitor -d 0`) |
| Dashboard | `127.0.0.1:8080` when hacking | `0.0.0.0:8080` via launchd (LAN) |

**Rules for agents:**

- **Prod is the Mac mini.** Treat local data as dev-only. When asked "what birds
  do we have?" or "what's missing?", query **prod** (`ssh mac-mini …`), not the
  local `./birdid.db` unless the user explicitly wants dev data.
- **Do not deploy to prod without explicit user confirmation.** That includes
  `git pull`, `deploy/mac-mini/deploy.sh`, restarting launch agents, schema
  backfills, and overwriting prod files.
- **Never push dev DB or recordings to prod.** Data flows prod → dev only.
- **Code and static content flow dev → prod** via git + deploy script.

Santa Barbara coords (`34.4208`, `-119.6982`) and `min_conf` **0.3** apply on both
environments. Prod `config.json` lives only on the mini — do not commit it.

## What this is

A prototype that records bird sounds and identifies them with Cornell Lab's
**BirdNET**, run locally via `birdnetlib` (no API key, no network). **Prod today**
is an always-on **Mac mini** in Santa Barbara, CA; the longer-term target is a
**Raspberry Pi** with the same architecture.

## Architecture — keep these boundaries

Each module does one thing. Do not blur these lines; they're what make the
project testable and portable.

| Module | Responsibility | Depends on mic? | Platform-specific? |
|---|---|---|---|
| `recorder.py` | mic → wav (48kHz mono) + `stream_pcm()` continuous pipe | **yes** | **yes — macOS AVFoundation** |
| `segmenter.py` | adaptive DSP: PCM hops → variable-length `Segment` objects | no | no |
| `identifier.py` | `identify(wav)` → detections via BirdNET | no | no |
| `storage.py` | SQLite: segments, tracks, detections, queries | no | no |
| `clips.py` | slice segment wav → per-window clip files | no | no |
| `config.py` | load `config.json`, resolve flag>config>default | no | no |
| `dashboard.py` | Flask web UI (offline, server-rendered) | no | no |
| `ingest.py` | Flask HTTP service: receive ESP32-S3 sensor clips → identify → store | no | no |
| `birdid.py` | CLI that wires the above together | — | — |

Key invariants:
- **`identify()` never touches the microphone.** The dev test loop depends on
  running it against a fixed file. Keep it a pure `wav path in → detections out`
  function so the BirdNET backend could later be swapped for an HTTP API without
  changing any caller.
- **Only one server component owns BirdNET.** The local monitor and `ingest.py`
  each load the model; the **dashboard must stay TF-free** (it only reads the DB).
  `ingest.py` defers BirdNET behind `ingest._identify` (imported lazily) so
  `import ingest` stays light and tests stub it. Don't `import identifier` in
  `dashboard.py`.
- **The monitor loop stays in one process** so it reuses the cached `_ANALYZER`
  in `identifier.py`. Never shell out to `birdid.py identify` per segment — that
  reloads the whole TF model every time. Verify: "Model loaded" prints once.
- **Monitor is streaming (two-thread).** `recorder.stream_pcm()` + `segmenter.segment_stream()`
  run in a capture thread; the main thread processes segments. The mic pipe is never
  blocked by BirdNET analysis. Segments are ~8–12s, adapting to quiet boundaries.
- **Timestamps survive sleep.** `started_at` comes from an audio-sample clock
  (`stream_start + samples/sr`), which only equals wall time while capture runs
  continuously. ffmpeg restarts re-anchor via `Respawn`; system sleep / pipeline
  stalls (ffmpeg stays alive, sample clock freezes while wall time advances) are
  caught by a drift resync in `segmenter._flush()` — when wall runs >60s ahead of
  audio, `_stream_start` is bumped forward so new segments get correct wall time.
  Resync is one-directional (timestamps only jump forward, never back). The monitor
  log appends `(processed Xh Ym late)` when a dequeued segment is stale.
- **Store raw, aggregate at query.** Write one row per 3s-window detection; roll
  up with `GROUP BY` at read time (see `species_summary`, `day_species`).
- **Tracks + clips.** Each distinct `(start_time, end_time)` window within a
  segment gets a `tracks` row (optional `clip_path` under `recordings/clips/`).
  Detections link via `track_id`. Clip files are written in `birdid.py` via
  `clips.write_clip()`; `storage.py` only stores paths. Retention cleanup expires
  both segment wavs and track clips.
- **Dashboard is offline-friendly.** No CDN / JS chart libraries — charts are
  server-rendered CSS. It must work on a headless Pi with no internet. **`/live`**
  is the exception: ~40 lines of inline JS poll `/api/recent` every 5s (pauses when
  the tab is hidden) for the rolling 24h companion feed. **`/call`** is the second
  JS-heavy route: a Three.js “Call Chamber” 3D frequency-pole view driven by server-
  precomputed JSON (`/callviz/…`). **`static/three.min.js`** and **`static/OrbitControls.js`**
  are **vendored** (pinned three.js r134 UMD build) — download once in dev, never a
  runtime CDN.

## Distributed sensors (ESP32-S3 → ingest service)

Besides the local mic, bird-id accepts uploads from cheap **ESP32-S3 sensors**
(`firmware/`). A sensor captures a **48 kHz mono WAV** and POSTs it to the ingest
service, which runs the same BirdNET pipeline as the monitor. The eventual gate is
an on-device Edge Impulse "bird vs. no bird" model; until one is dropped into
`lib/ei-bird-model/` the classifier is stubbed and capture is **sound-activated**
instead (see "Edge gating" below).

- **`ingest.py`** = a Flask service that owns BirdNET. `POST /api/register` (mint a
  device + API key), `POST /api/ingest` (auth → identify → clips →
  `record_segment(device_id=…)`), and `GET /api/config` (auth → per-device gate
  overrides; see "Edge gating"). Run it with `birdid.py ingest-server` (config
  `ingest_host`/`ingest_port`, default `127.0.0.1:8081`).
- **Edge gating + remote config.** No on-device model yet, so the sensor is
  sound-activated: it high-passes the newest second (2-pole, ~300 Hz, to ignore DC
  drift / 50–120 Hz mains hum), and when it crosses a **noise floor** it captures a
  3 s clip *centered* on the sound (pre-roll buffered + post-roll) so events aren't
  sliced by the clip edge; a cooldown bounds upload rate. The three knobs
  (noise_floor / cooldown / post-roll) are firmware defaults but **overridable
  remotely from the dashboard** — the device polls `GET /api/config` at boot + every
  60 s and applies any per-device override (`devices.gate_*` columns, NULL = use the
  firmware default). Polling (not piggybacking on uploads) avoids a lockout when a
  too-high floor stops uploads.
- **Addressing + resilience.** The sensor finds the server by mDNS name
  (`INGEST_HOST`, e.g. `birdnet.local`, resolved via `ESPmDNS`) with an IP fallback,
  so a changing DHCP lease needs no reflash; advertise the name with
  `deploy/birdnet-mdns-alias.*` (Avahi). Wi-Fi connect has a timeout that reboots
  rather than hanging forever, so a router blip can't strand the sensor.
- **Multi-device.** `storage.devices` table + nullable `segments.device_id`
  (NULL = local mic). Detections inherit their device through the segment join.
  Dashboard `/devices` lists sensors + per-device species; the `/live` feed shows a
  📡 device badge. Helpers: `register_device`/`touch_device`/`list_devices`/`device_species`.
- **Capture-time, not receipt-time.** The *device* stamps `captured_at` (NTP) and
  the ingest endpoint treats it as authoritative (`started_at = captured_at`), so a
  delayed/retried upload still files under when the bird called. A never-synced
  device sends `clock_unsynced=1` and the server falls back to receipt time + logs.
  This is covered by `tests/test_ingest.py::test_delayed_upload_keeps_capture_time`.
- **Audio is dual-rate.** Sensors upload 48 kHz (BirdNET-native, full fidelity); the
  on-device detector runs on a 3:1-decimated 16 kHz copy. The pipeline is
  sample-rate agnostic, so no server change is needed.
- **Shared clip helper.** `clips.clip_paths_for_detections()` is the single copy
  used by both the monitor (`birdid.py`) and `ingest.py` — don't fork it.

## Self-hosting with Docker

`Dockerfile` + `docker-compose.yml` containerize the **server** (ingest + dashboard)
for homelab users who can't run macOS, and de-risk the Pi target (same Linux
`requirements.txt`). `docker compose up --build` starts both services sharing one
volume at `/data` for `birdid.db` + `recordings/`; `working_dir: /data` makes the
CWD-relative paths resolve there (the gotcha below). The **monitor is not
containerized** (it needs host mic hardware). Keep both services on the same
host/volume — SQLite over a network FS is unsafe; WAL handles the concurrent
ingest-write / dashboard-read.

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

# monitor smoke test; confirm model loads once, seg lines appear every ~8-12s,
# rows land in the CONFIGURED db
rm -f birdid.db && rm -rf recordings
(./.venv/bin/python birdid.py monitor > /tmp/m.log 2>&1 &)
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
| `tests/test_storage.py` | Schema, tracks/clips, `record_segment`, rollups, retention, `recent_feed` |
| `tests/test_config.py` | `load`, `resolve`, bad JSON |
| `tests/test_identifier_summarize.py` | `summarize()`, default `min_conf` |
| `tests/test_dashboard.py` | Helpers + `/`, `/bird/…`, `/call`, `/data`, `/live`, `/api/recent` via `test_client` |
| `tests/test_birdid.py` | `cmd_stats`, `_resolve_id_params` |
| `tests/test_devices.py` | Device registry helpers, `device_id` migration, dashboard `/devices` + live badge |
| `tests/test_ingest.py` | `/api/register` + `/api/ingest` (BirdNET stubbed), device auth, capture-time timestamps |

**Dashboard test seam:** every route uses `dashboard._db()`. Tests monkeypatch it to
return a seeded SQLite connection (see `tests/conftest.py`). Heavy imports
(`matplotlib`, `librosa`, `soundfile`) are lazy — only the spectrogram/audio routes
need them.

Run pytest after any change to `storage.py`, `config.py`, `dashboard.py`, or
`birdid.py`. Manual smokes above still apply after touching `cmd_monitor`.

**Lesson from history:** every time the monitor loop was changed, *running* it
found a bug (stdout buffering) that reading the code did not. Re-run the monitor
smoke test after touching `cmd_monitor`.

## Deploying to prod (Mac mini)

**Only after the user confirms.** Typical flow:

1. **Dev:** land changes locally, run `pytest -q`, manual smokes if needed.
2. **Push** to GitHub (`grqg-dev/bird-id`, branch `main`).
3. **On the mini:**

   ```bash
   ssh mac-mini
   cd ~/bird-id
   git pull
   ./deploy/mac-mini/deploy.sh
   ```

`deploy/mac-mini/deploy.sh` (run **on the mini**, not from dev):

- `pip install -r requirements-intel-mac.txt`
- Rebuilds `~/Applications/BirdID Monitor.app` (mic-permission wrapper)
- Installs launch-agent plists to `~/Library/LaunchAgents/`
- Runs `birdid.py cleanup` (retention)
- **Restarts dashboard** launch agent (`com.birdid.dashboard`)
- **Restarts monitor** launch agent only if already loaded — skips when monitor
  runs in a Terminal session (avoids killing a manual session)

### Prod services (launchd)

| Label | What | Logs |
|---|---|---|
| `com.birdid.monitor` | `BirdID Monitor.app` → `birdid.py monitor -d 0` | `logs/monitor.log`, `monitor.err` |
| `com.birdid.dashboard` | `birdid.py dashboard --host 0.0.0.0 --port 8080` | `logs/dashboard.log`, `dashboard.err` |

Both use `WorkingDirectory=/Users/matt/bird-id`. **CWD matters** — relative
`db` and `recordings_dir` in `config.json` resolve from there.

To accept ESP32-S3 sensor uploads in prod, add a third always-on service —
`com.birdid.ingest` running `birdid.py ingest-server --host 0.0.0.0` — alongside
the two above (same `WorkingDirectory`, so it writes the same `birdid.db`). No
plist exists yet; create one mirroring `com.birdid.dashboard` when sensors ship.
Unlike the monitor, the ingest service needs no mic permission.

**First-time monitor setup** (mic permission — macOS grants mic to `.app`
bundles, not bare Python under launchd):

```bash
ssh mac-mini
cd ~/bird-id
./deploy/mac-mini/install-monitor-app.sh
# Screen Sharing: open ~/Applications/BirdID Monitor.app once → allow mic
# Stop any Terminal monitor, then:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.birdid.monitor.plist
```

### Post-deploy: schema / clips / content

After code that touches DB schema or clips:

1. **Restart** monitor and/or dashboard (deploy script restarts launch agents;
   a Terminal monitor needs manual restart). `storage.connect()` runs `_migrate()`.
2. **Backfill clip files** (once per DB, idempotent — **on prod, only with user OK**):

   ```bash
   cd ~/bird-id
   ./.venv/bin/python scripts/backfill_track_clips.py
   ```

   Uses `config.json` for `db` and `recordings_dir`. Skips tracks whose segment
   `wav_path` is missing or expired. Safe to re-run; use `--dry-run` to preview.

3. **`docs/bird_info.json`** — field notes for the species drill-down card, keyed
   by slug from `docs/birds.json`. Dashboard caches this at import; **restart
   dashboard** after editing on prod. No DB migration needed.

New segments from the monitor get clips automatically; backfill is only for
history.

### Pulling prod data to dev

Read-only snapshots for local dashboard / identify testing:

```bash
# DB snapshot (SQLite .backup on mini — safe while monitor writes)
./scripts/pull_db_from_mini.sh
./scripts/pull_db_from_mini.sh --activate   # → ./birdid.db

# Segment wavs (default: 30 newest; --all for everything)
./scripts/pull_recordings_from_mini.sh
./scripts/pull_recordings_from_mini.sh --recent 50
```

Override host/path: `BIRD_MINI_HOST`, `BIRD_MINI_DIR`, `BIRD_MINI_DB`.

Pulled DB paths are absolute from the mini — dashboard playback needs
`--activate` **and** matching `seg_*.wav` files under `./recordings/`.

### Querying prod from dev

One-liners without pulling the whole DB:

```bash
ssh mac-mini 'cd ~/bird-id && ./.venv/bin/python birdid.py stats'
ssh mac-mini 'cd ~/bird-id && sqlite3 birdid.db "SELECT DISTINCT common_name FROM detections ORDER BY 1"'
```

To find species **missing field notes** (`docs/bird_info.json`), compare prod
detections to info slugs (see `_common_to_slug` / `_slug_map` in `dashboard.py`).

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
- **`db` / `recordings_dir` are relative to CWD.** A launchd agent or systemd unit with a
  different working dir will silently use a *different* `birdid.db`. On prod and Pi,
  set `WorkingDirectory=` (already `/Users/matt/bird-id` on the mini) or use absolute
  paths in `config.json`.
- **Dev `birdid.db` ≠ prod.** Local stats and "missing content" checks against
  `./birdid.db` are misleading unless you pulled prod first.
- **BirdNET's default model includes non-bird classes** (Human, Dog, Gun,
  Fireworks) and emits low-confidence false birds from noise. The Santa Barbara
  location filter + `min_conf` (currently 0.3) handle this — don't lower the
  threshold without expecting junk.

## BirdNET detection fields

Per detection BirdNET returns exactly: `common_name`, `scientific_name`,
`confidence`, `start_time`, `end_time`, `label` (Latin_English). bird-id adds
`heard_at` (absolute local timestamp), `track_id` (links to the window's track),
and segment-level volume/path. Each **track** stores the window bounds, optional
`clip_path` (a small extracted wav under `recordings/clips/`), and ties back to
the parent segment's full `wav_path` for fallback playback/spectrograms.

## Conventions

- Standard library first; add a dependency only when it earns its place. New
  runtime deps must have arm64 Linux (Pi) wheels; test-only deps go in
  `requirements-dev.txt`.
- Keep modules small and single-purpose. Match the existing comment density and
  docstring style.
- After adding a runtime dependency, regenerate `requirements.txt` via `pip freeze`.
- User-facing usage lives in `README.md`; architecture, testing, environments, and
  gotchas live here. Keep both in sync when commands or defaults change.
- **Static dex content:** `docs/birds.json` (top-50 slug list), `docs/bird_info.json`
  (field notes), `realistic-sprites/*.png` (illustrations). Prod may detect species
  outside `birds.json`; those still get dex cards but need slugs + sprites + info
  added in dev, then deployed.

## Roadmap / open work

- [ ] **Pi: ALSA capture path in `recorder.py`** (only non-portable module).
- [ ] Rare/new-species alerts (desktop or email).
- [ ] Optional HTTP backend behind `identify()`.
