# AGENTS.md — working on bird-id

Guidance for AI agents (and humans) contributing to this project. Read this
before changing code. For *usage*, see `README.md`.

**Deep code reference:** [`docs/codebase-guide.md`](docs/codebase-guide.md) —
module APIs, DB schema, config keys, dashboard routes, internal patterns, and
file map. Start here for environment rules; go there for code details.

## Environments — dev vs prod

| | **Dev (this machine)** | **Prod (Mac mini)** |
|---|---|---|
| Role | Hack, test, write content | Always-on backyard monitor + dashboard |
| SSH | — | `mac-mini` (`~/.ssh/config`) |
| Path | local checkout | `/Users/matt/bird-id` (user `matt`) |
| Python | Apple Silicon → `requirements.txt` | **Intel** → `requirements-intel-mac.txt` |
| `birdid.db` | Small / stale unless pulled from prod | **Source of truth** — live detections |
| `recordings/` | Optional pull for playback tests | Clip MP3s + caches; segment wavs dropped after clip |
| `config.json` | Local copy (gitignored) | Prod copy on mini (gitignored) |
| Monitor | Manual smoke tests only | **Often** a long-lived Terminal session (`birdid.py monitor -d 0`); launchd optional |
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

### Dev checkout (separate from prod)

Use a **local clone** for all refactor work — not the Mac mini checkout:

```bash
git clone git@github.com:grqg-dev/bird-id.git ~/bird-id
cd ~/bird-id
git checkout -b your-feature-branch
./scripts/dev-setup.sh
```

Prod (`ssh mac-mini`, `/Users/matt/bird-id`) receives changes only after explicit
user confirmation via `git pull` + `deploy/mac-mini/deploy.sh`.

**Prerequisites:**

| Tool | Dev | Prod (Intel mini) |
|------|-----|-------------------|
| Python | **3.12** (not 3.13) | 3.12 in `.venv` |
| ffmpeg | `brew install ffmpeg` — mic capture + clip encoding | `~/bin/ffmpeg` or PATH (launchd omits Homebrew) |
| Node.js | 20+ for `dashboard-ui/` (`npm ci`, `npm run dev`) | Not required unless rebuilding `/dash` dist |

Fast CI/tests install only `requirements-dev.txt` (pytest + Flask). Full BirdNET
smokes need `requirements.txt` (Apple Silicon) or `requirements-intel-mac.txt` (Intel).

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
| `birdid.py` | CLI that wires the above together | — | — |

Key invariants:
- **`identify()` never touches the microphone.** The dev test loop depends on
  running it against a fixed file. Keep it a pure `wav path in → detections out`
  function so the BirdNET backend could later be swapped for an HTTP API without
  changing any caller.
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

## Setup

```bash
./scripts/dev-setup.sh   # venv + requirements.txt + requirements-dev.txt + config.json
```

Or manually:

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt          # Apple Silicon
./.venv/bin/python -m pip install -r requirements-intel-mac.txt  # Intel Mac (TF ≤2.16)
./.venv/bin/python -m pip install -r requirements-dev.txt    # pytest (dev only)
cp config.example.json config.json   # first time
```

Always run via `./.venv/bin/python`, not the system Python.

**React `/dash` dashboard** (`dashboard-ui/`):

```bash
cd dashboard-ui && npm ci && npm run dev   # http://localhost:5173/dash/ (proxies API to Flask)
./.venv/bin/python birdid.py dashboard   # Flask on :8080 serves built dist/ at /dash
cd dashboard-ui && npm run build           # commit dist/ when shipping UI changes
```

**Config:** `config.DEFAULTS` in `config.py` is the built-in fallback. Operational
`min_conf` is **0.3** (`config.json`, dashboard, docs). `identifier.identify()` uses
the same default as a literal (it does not import `config` — keep that boundary).

Key defaults (see `config.example.json` for the full set):

| Key | Default | Notes |
|-----|---------|-------|
| `clip_format` | `mp3` | Per-window clips under `recordings/clips/` |
| `drop_segment_after_clips` | `true` | Prod has **no** `seg_*.wav` — only clip MP3s remain |
| `retention_days` | `30` | Segment wav expiry; clips trimmed separately |
| `db` / `recordings_dir` | relative paths | Resolve from **CWD** — launchd sets `WorkingDirectory` |

## How to test (do this before claiming a change works)

The tracked dev fixture is `tests/fixtures/bewicks_wren.wav` (~2.75 s → Bewick's Wren
~0.92 @ `-c 0.1`). See `tests/fixtures/README.md`.

```bash
# mic-free identify loop — fastest check (requires full runtime venv + TensorFlow)
./scripts/smoke_identify.sh
# equivalent:
./.venv/bin/python birdid.py identify tests/fixtures/bewicks_wren.wav -c 0.1

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

**Never point tests or smokes at prod.** Use a pulled DB snapshot (`pull_db_from_mini.sh`)
and optional clip sample (`pull_clips_from_mini.sh`) for local dashboard playback.

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

**Dashboard test seam:** every route uses `dashboard._db()`. Tests monkeypatch it to
return a seeded SQLite connection (see `tests/conftest.py`). Heavy imports
(`matplotlib`, `librosa`, `soundfile`) are lazy — only the spectrogram/audio routes
need them.

Run pytest after any change to `storage.py`, `config.py`, `dashboard.py`, or
`birdid.py`. Manual smokes above still apply after touching `cmd_monitor`.

**Optional integration** (not CI): `./scripts/smoke_identify.sh` or
`pytest -m integration` when integration tests exist. Mark slow BirdNET/mic tests
with `@pytest.mark.integration`.

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

| Label | What | Logs | Typical state |
|---|---|---|---|
| `com.birdid.monitor` | `BirdID Monitor.app` → `birdid.py monitor -d 0` | `logs/monitor.log`, `monitor.err` | **Often not loaded** — Matt runs monitor in Terminal instead |
| `com.birdid.dashboard` | `birdid.py dashboard --host 0.0.0.0 --port 8080` | `logs/dashboard.log`, `dashboard.err` | Loaded via launchd; survives reboot |

Both use `WorkingDirectory=/Users/matt/bird-id`. **CWD matters** — relative
`db` and `recordings_dir` in `config.json` resolve from there.

**Observed prod split (2026-07-14):** dashboard is launchd-managed and responds on
`:8080`; monitor may be a weeks-old manual Terminal process while
`com.birdid.monitor` is unloaded. Do not assume both are launchd-managed. Restarting
the monitor launch agent without user OK can kill an intentional Terminal session.

### Cloudflare Tunnel (public URL for dashboard)

The dashboard runs on `localhost:8080`. To expose it publicly via Cloudflare:

```bash
# Start tunnel (quick tunnel, no account needed)
/Users/matt/.local/bin/cloudflared tunnel --url http://localhost:8080 > /tmp/cloudflared-birdid.log 2>&1 &

# Get the URL (wait ~5-10s for it to appear)
grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared-birdid.log | head -1

# Check if tunnel is running
ps aux | grep cloudflared | grep -v grep

# Stop tunnel
pkill -f "cloudflared tunnel"
```

**Notes:**
- Quick tunnels (`trycloudflare.com`) are ephemeral — URL changes each restart
- Log file: `/tmp/cloudflared-birdid.log` (runtime; `logs/` is gitignored)
- Dashboard must be running on `:8080` before starting tunnel
- For persistent URL, set up a named tunnel with Cloudflare account

**First-time setup:** `cloudflared` is installed at `/Users/matt/.local/bin/cloudflared`

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

# Clip MP3s for dashboard playback (prod keeps clips, not segment wavs)
./scripts/pull_clips_from_mini.sh                  # 30 newest
./scripts/pull_clips_from_mini.sh --recent 50

# Legacy segment wavs (only if still present on mini)
./scripts/pull_recordings_from_mini.sh
```

Override host/path: `BIRD_MINI_HOST`, `BIRD_MINI_DIR`, `BIRD_MINI_DB`.

Pulled DB `clip_path` values are relative (`recordings/clips/...`) — they resolve
locally after `pull_clips_from_mini.sh`. Spectrogram routes may still need segment
wavs; most playback uses clip MP3s.

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

## Pre-refactor baseline

See [`docs/pre-refactor-baseline.md`](docs/pre-refactor-baseline.md) for the verified
gate before large refactors: commit SHA, test commands, and prod service caveats.
