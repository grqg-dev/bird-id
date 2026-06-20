# bird-id Codebase Guide

Deep reference for agents and contributors. Covers module APIs, the DB schema,
data flow, config keys, dashboard routes, and internal patterns. Start with
`AGENTS.md` for environment rules and deployment workflow; come here for code
details.

---

## Table of Contents

1. [Data Flow](#data-flow)
2. [Module API Reference](#module-api-reference)
3. [Database Schema](#database-schema)
4. [Config Keys](#config-keys)
5. [Dashboard Routes](#dashboard-routes)
6. [Internal Patterns](#internal-patterns)
7. [Testing Seams](#testing-seams)
8. [File Map](#file-map)

---

## Data Flow

```
mic
 │
 ▼
recorder.stream_pcm()          → raw np.int16 hops (100 ms each) + Respawn sentinels
 │
 ▼
segmenter.segment_stream()     → Segment objects (~8–12 s, adaptive silence-gated)
 │
 ▼
birdid._write_segment_wav()    → seg_YYYYMMDD_HHMMSS.wav  (recordings/)
 │
 ├──▶ identifier.identify()    → [Detection, ...] via BirdNET
 │
 ├──▶ clips.write_clip()       → per-window clip_*.mp3 (recordings/clips/)
 │
 ├──▶ storage.record_segment() → segments + tracks + detections rows in birdid.db
 │
 └──▶ storage.expire_*/purge_* → audio retention / orphan cleanup
```

**One-shot identify flow** (`birdid.py identify`):

```
existing wav file
 │
 ├──▶ identifier.identify()     → detections
 ├──▶ (optional) _import_to_wav() → recordings/import_YYYYMMDD_HHMMSS.wav
 └──▶ storage.record_segment()  → DB (with --save)
```

**Two-thread monitor design** (`birdid.py monitor`):

- **Capture thread** — `recorder.stream_pcm()` → `segmenter.segment_stream()` →
  write wav → `seg_queue.put_nowait()`
- **Processor (main thread)** — `seg_queue.get()` → `identifier.identify()` →
  `clips.write_clip()` → `storage.record_segment()` → cleanup
- Queue is bounded (`maxsize=20`); if it fills, the capture thread drops segments
  and logs to stderr.
- `_CaptureError` sentinel signals a fatal capture failure from the capture
  thread; `None` signals normal EOF.

---

## Module API Reference

### `recorder.py`

**Constants:**

| Name | Value | Meaning |
|---|---|---|
| `TARGET_SAMPLE_RATE` | `48_000` | Hz — BirdNET's native rate (no resample needed) |
| `TARGET_CHANNELS` | `1` | mono |
| `SILENCE_DBFS_THRESHOLD` | `-70.0` | mean dBFS below which capture is "silent" |

**Classes:**

- `RecordingResult(path, seconds, mean_volume_dbfs, max_volume_dbfs)` — return from `record()`
- `Respawn(wall_time: datetime)` — sentinel yielded by `stream_pcm()` on ffmpeg restart
- `RecordingError(RuntimeError)` — raised on ffmpeg failure or silent capture

**Functions:**

```python
record(seconds, out_path, *, device="0", check_silence=True, progress=None) → RecordingResult
```
Records `seconds` of audio from AVFoundation device `device`. Raises
`RecordingError` if ffmpeg fails or the result is silent (check_silence=True).

```python
stream_pcm(device="0", *, hop_ms=100) → Iterator[np.ndarray | Respawn]
```
Yields `np.int16` arrays of `hop_ms` milliseconds each, indefinitely. Yields
`Respawn` when ffmpeg exits and is restarted (exponential backoff: 1s → 60s
max). Raises `RecordingError` after 5 consecutive quick restarts (<5 s each).

```python
list_input_devices() → str
```
Returns ffmpeg's AVFoundation device list (stderr output).

---

### `segmenter.py`

**Classes:**

```python
@dataclass
class Segment:
    samples: np.ndarray   # float32, normalised -1..1
    sr: int
    started_at: datetime  # wall-clock time of first sample in this segment
    mean_dbfs: float
    max_dbfs: float
```

- `MicDead(Exception)` — raised when input RMS stays ≤ `mic_dead_dbfs` for
  `mic_dead_window_seconds`.

**Functions:**

```python
segment_stream(
    hops,                         # Iterable of np.int16 chunks or Respawn sentinels
    sr,                           # sample rate (int)
    stream_start,                 # datetime — wall time at capture start
    *,
    hop_ms=100,
    seg_target_seconds=8.0,       # flush near this length at a quiet boundary
    seg_max_seconds=12.0,         # hard flush regardless of quiet state
    floor_window_seconds=30.0,    # EMA warmup window for adaptive noise floor
    activity_margin_db=6.0,       # dB above floor = "active" hop
    quiet_hold_ms=300.0,          # consecutive quiet hops before flush is allowed
    tail_carry_seconds=1.0,       # overlap prepended to next segment
    mic_dead_dbfs=-85.0,
    mic_dead_window_seconds=60.0,
    drift_resync_seconds=60.0,    # wall clock drift threshold → re-anchor sample clock
    wall_now=None,                # override datetime.now for tests
) → Iterator[Segment]
```

**Adaptive floor algorithm:** EMA-min tracking with `alpha_down=0.08` (fast) and
`alpha_up=0.002` (slow). A hop is "active" when its RMS exceeds
`floor_ema + activity_margin_db`. The system only flushes at a quiet boundary
after `warmup_hops = floor_window_seconds * 1000 / hop_ms` hops.

**Respawn handling:** When `stream_pcm` restarts ffmpeg, it yields a `Respawn`
sentinel. `segment_stream` detects this (by attribute `wall_time`, not isinstance,
to stay mic-free), calls `_reset(respawn.wall_time)`, and discards any partial
buffer.

**Drift resync:** At flush time, if wall clock has run >60 s ahead of the audio
sample clock (e.g., after Mac sleep), `_stream_start` is bumped forward so
subsequent `Segment.started_at` values are correct. Timestamps only jump
forward, never back.

---

### `identifier.py`

**Classes:**

```python
@dataclass
class Detection:
    common_name: str
    scientific_name: str
    confidence: float     # 0.0–1.0
    start_time: float     # seconds from start of wav
    end_time: float       # always start_time + 3 (BirdNET 3 s windows)

@dataclass
class SpeciesSummary:
    common_name: str
    scientific_name: str
    count: int            # windows detected
    max_confidence: float
    first_time: float
    last_time: float
```

**Functions:**

```python
identify(wav_path, *, min_conf=0.3, lat=None, lon=None, when=None) → list[Detection]
```
Runs BirdNET on `wav_path`. When `lat`/`lon` are provided, also passes
`when` (default `date.today()`) so BirdNET applies location/season filtering.
Returns detections sorted by confidence descending.

**Module-level cache:** `_ANALYZER` is a module global — BirdNET's `Analyzer`
object is loaded once and reused. "Model loaded" should appear exactly once per
process. Never shell out to `birdid.py identify` per segment; that reloads TF
every time.

```python
summarize(detections: list[Detection]) → list[SpeciesSummary]
```
Collapses per-window detections into one row per species (count, peak, span).

---

### `storage.py`

`connect(db_path=DEFAULT_DB) → sqlite3.Connection`

Opens/creates the DB with `WAL` journal mode, `row_factory = sqlite3.Row`,
foreign keys ON, runs `_SCHEMA` (CREATE TABLE IF NOT EXISTS), then `_migrate()`.

**`_migrate()`** adds the `track_id` column to detections if missing, creates
`tracks` rows for any detection windows without one, and links them.

**Key write function:**

```python
record_segment(
    conn, *,
    started_at: datetime,
    ended_at: datetime,
    duration: float,
    detections: Iterable,       # identifier.Detection objects
    wav_path: Optional[str] = None,
    mean_dbfs: Optional[float] = None,
    max_dbfs: Optional[float] = None,
    clip_paths: Optional[dict[tuple[float,float], str]] = None,
) → int  # segment id
```
Single transaction: inserts segment row → one `tracks` row per unique
`(start_time, end_time)` window → one `detections` row per detection.
Returns `segment_id`.

**Query functions (all return `sqlite3.Row` or list thereof):**

| Function | Returns |
|---|---|
| `species_summary(conn, since=None)` | All-time per-species rollup, peak conf |
| `species_dex(conn)` | One row per species: peak-confidence detection + audio flag + window count |
| `species_dex_day(conn, day)` | Same, scoped to one day |
| `day_overview(conn, day)` | Totals for one day |
| `day_species(conn, day)` | Per-species for one day |
| `day_hourly(conn, day)` | Detections per hour |
| `new_species_on(conn, day)` | Species whose first detection was on `day` |
| `species_stats(conn, name, day=None, show_all=True)` | Agg stats for one species |
| `species_detections(conn, name, …, limit, offset)` | Detection rows for one species |
| `species_hourly(conn, name, …)` | Per-hour count for one species |
| `species_daily(conn, name)` | Per-day count for one species |
| `recent_feed(conn, hours, limit, since, min_conf)` | Chronological feed for /live |
| `detection_feed(conn, day, show_all, min_conf, limit, offset, sort)` | Paginated feed for /twitter |
| `timeline_occurrences(conn, day, min_conf)` | All detections on day (swimlane) |
| `totals(conn)` | Segments/detections/species counts + date range |
| `get_segment(conn, id)` | One segment row |
| `tracks_for_segment(conn, id)` | All track rows for a segment |
| `tracks_without_clip(conn)` | Tracks needing clip backfill |

**Audio cleanup chain** (called by `_run_audio_cleanup` in `birdid.py`):

1. `expire_segment_audio(conn, retention_days)` — NULL `wav_path` + delete file for
   segments older than N days.
2. `expire_track_clips(conn, retention_days)` — NULL `clip_path` + delete for old clips.
3. `cleanup_dashboard_audio(conn, rec_dir, live_hours)` — trims audio not needed by
   the dashboard (keeps peak-confidence detections per species per day, top-200
   all-time, last `live_hours` of audio).
4. `purge_orphan_recordings(conn, recordings_dir)` — deletes wav files under
   `recordings/` not in DB (skips files <10 min old).
5. `purge_orphan_clips(conn, clips_dir)` — same for `recordings/clips/`.

---

### `clips.py`

```python
clip_dst_path(clips_dir, started_at, start, end, *, fmt="wav") → Path
```
Returns canonical path: `clip_YYYYMMDD_HHMMSS_{start_ms}_{end_ms}.{ext}`.

```python
write_clip(src_wav, dst, start, end, *, fmt="wav", mp3_bitrate="64k") → str | None
```
Extracts `[start, end)` seconds from `src_wav` using `soundfile`, writes to
`dst`. If `fmt="mp3"`, encodes via ffmpeg (falls back to wav if ffmpeg missing).
Returns `dst` path or `None` on failure/empty window.

```python
transcode_to_mp3(src, dst, *, bitrate="64k") → str | None
```
Converts an existing audio file to MP3 via ffmpeg. Used by the backfill script.

---

### `config.py`

```python
load(path=CONFIG_PATH) → dict
```
Returns `DEFAULTS` merged with `config.json` (if it exists). Bad JSON → `SystemExit`.

```python
resolve(flag_value, key, cfg) → Any
```
Returns `flag_value` if not None, else `cfg[key]`, else `DEFAULTS[key]`. This
is the standard pattern for CLI flag > config file > default precedence.

`CONFIG_PATH` = `<repo-root>/config.json`. It is gitignored; `config.example.json`
is the committed template.

---

### `birdid.py`

Entry point. Each subcommand maps to a `cmd_*` function:

| Command | Function | What it does |
|---|---|---|
| `record` | `cmd_record` | mic → wav via `recorder.record()` |
| `identify` | `cmd_identify` | wav → detections; optional `--save` to DB |
| `listen` | `cmd_listen` | record + identify in one step |
| `monitor` | `cmd_monitor` | two-thread streaming loop |
| `stats` | `cmd_stats` | print species summary from DB |
| `digest` | `cmd_digest` | daily summary (species, timing, new birds) |
| `dashboard` | `cmd_dashboard` | launch Flask UI |
| `cleanup` | `cmd_cleanup` | expire old audio, purge orphans |

**`_resolve_id_params(args)`** — resolves `min_conf`/`lat`/`lon` from CLI flag
→ config → default. Called by identify, listen, and monitor.

**`_clip_format(cfg)`** — returns `"mp3"` or `"wav"` from `clip_format` config key.

**`_run_audio_cleanup(conn, rec_dir, args, *, label, dash_state)`** — full cleanup
chain (expire + dashboard trim + orphan purge). The monitor calls this after
every segment; the cleanup command calls it once.

**`_MonitorProgress`** — terminal UX for interactive (TTY) vs redirected stdout.
Uses `\r` overwrite and a spinner in TTY mode; milestone percentages in non-TTY.

---

## Database Schema

```sql
segments (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,        -- local ISO 8601, e.g. "2026-06-01T06:42:13"
    ended_at        TEXT NOT NULL,
    duration        REAL NOT NULL,        -- seconds
    wav_path        TEXT,                 -- NULL after audio is discarded/expired
    mean_dbfs       REAL,
    max_dbfs        REAL,
    num_detections  INTEGER DEFAULT 0
)

tracks (
    id          INTEGER PRIMARY KEY,
    segment_id  INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    start_time  REAL NOT NULL,    -- offset within segment (seconds)
    end_time    REAL NOT NULL,
    duration    REAL NOT NULL,    -- = end_time - start_time (always 3.0 for BirdNET)
    clip_path   TEXT,             -- NULL if clip not written or expired
    created_at  TEXT NOT NULL,
    UNIQUE(segment_id, start_time, end_time)
)

detections (
    id              INTEGER PRIMARY KEY,
    segment_id      INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    common_name     TEXT NOT NULL,
    scientific_name TEXT NOT NULL,
    confidence      REAL NOT NULL,
    start_time      REAL NOT NULL,   -- window offset within segment (s)
    end_time        REAL NOT NULL,
    heard_at        TEXT NOT NULL,   -- = segment.started_at + start_time (absolute wall clock)
    track_id        INTEGER REFERENCES tracks(id)
)

-- Indexes
idx_tracks_segment         ON tracks(segment_id)
idx_detections_species     ON detections(common_name)
idx_detections_heard_at    ON detections(heard_at)
```

**Design decisions:**
- All timestamps are local-time ISO 8601 strings. `heard_at` is the query
  anchor for day/hour filtering (use `date(heard_at)` and `strftime('%H', heard_at)`).
- One row per detection window, not per species. Roll up with `GROUP BY common_name`.
- `tracks` is the join table between `segments` and `detections`, keyed by
  `(segment_id, start_time, end_time)`.
- `wav_path` and `clip_path` may be NULL once audio is expired or trimmed —
  detection metadata persists indefinitely.
- `_HAS_AUDIO` SQL expression: `CASE WHEN s.wav_path IS NOT NULL OR t.clip_path IS NOT NULL THEN 1 ELSE 0 END`
  used throughout `storage.py`.

---

## Config Keys

All keys live in `config.json` (gitignored). `config.DEFAULTS` is the canonical
fallback.

| Key | Default | Meaning |
|---|---|---|
| `lat` | `null` | Latitude; enables BirdNET location/season filter |
| `lon` | `null` | Longitude |
| `min_conf` | `0.3` | Minimum confidence threshold (0–1) |
| `db` | `"birdid.db"` | SQLite path (relative to CWD) |
| `recordings_dir` | `"recordings"` | Where segment wavs are written |
| `retention_days` | `30` | Days to keep segment wavs (0 = forever) |
| `clip_format` | `"mp3"` | Per-window clips: `"mp3"` or `"wav"` |
| `clip_mp3_bitrate` | `"64k"` | Bitrate for MP3 clips |
| `hop_ms` | `100` | PCM read chunk size (milliseconds) |
| `seg_target_seconds` | `8` | Target segment length at quiet boundary |
| `seg_max_seconds` | `12` | Hard max segment length |
| `floor_window_seconds` | `30` | EMA warmup period for adaptive noise floor |
| `activity_margin_db` | `6` | dB above floor = active hop |
| `quiet_hold_ms` | `300` | Consecutive quiet time before flush |
| `tail_carry_seconds` | `1.0` | Overlap carried into next segment |
| `mic_dead_dbfs` | `-85.0` | RMS at or below this = "silent" hop |
| `mic_dead_window_seconds` | `60` | Sustained silence → `MicDead` exception |
| `proactive_cleanup` | `true` | Enable dashboard trim during monitor |
| `dashboard_cleanup_hours` | `6` | How often dashboard trim runs (0 = every segment) |
| `dashboard_live_hours` | `24` | Live-feed window protected from trim |
| `drop_segment_after_clips` | `true` | Delete full segment wav once clips exist |

Prod config (`/Users/matt/bird-id/config.json` on the mini) is never committed.
The Santa Barbara defaults: `lat: 34.4208, lon: -119.6982, min_conf: 0.3`.

---

## Dashboard Routes

All routes are in `dashboard.py`. The app is a single Flask file; no blueprints.

| Route | Method | What it does |
|---|---|---|
| `/` | GET | Bird-Dex: species grid, today or all-time; `?day=YYYY-MM-DD`, `?all=1`, `?mode=gallery` |
| `/bird/<slug>` | GET | Species drill-down: stats, hourly chart, clip list, spectrogram |
| `/timeline` | GET | Swimlane timeline for a day; `?day=YYYY-MM-DD` |
| `/data` | GET | Data report: detection counts, confidence strips; `?day=YYYY-MM-DD` |
| `/live` | GET | Rolling 24h feed; JS polls `/api/recent` every 5s |
| `/realtime` | GET | Realtime dashboard with detection feed (streaming) |
| `/twitter` | GET | All-time scrollable detection timeline; dark/light, sort by latest or top confidence |
| `/api/twitter` | GET | JSON paginated feed for `/twitter`; params: `day`, `page`, `sort`, `hide_low` |
| `/call/<segment_id>` | GET | Call Chamber: 3D Three.js frequency-pole visualization |
| `/api/recent` | GET | JSON feed for `/live`; params: `hours`, `since`, `min_conf`, `limit` |
| `/callviz/<segment_id>.json` | GET | Pre-computed mel matrix + waveform + pitch for `/call` |
| `/vibeviz/<segment_id>.json` | GET | Pre-computed node/edge graph for `/vibe` (v2) |
| `/vibe/<segment_id>` | GET | Fable: 3D ember-constellation sculpture of bird song |
| `/sprite/<slug>.png` | GET | Serve a species illustration from `realistic-sprites/` |
| `/audio/<segment_id>` | GET | Serve segment wav or clip for browser playback |
| `/spectrogram/<segment_id>.png` | GET | Render spectrogram PNG; `?start=`, `?end=` params |
| `/__dev/ping` | GET | Dev-mode reload sentinel |

**Key dashboard internals:**

- `_db()` — opens a new connection per request. Tests monkeypatch this.
- `_slug_map()` / `_slug_to_name()` — cached `common_name ↔ slug` from `docs/birds.json`.
- `_bird_info()` — cached field notes from `docs/bird_info.json` (keyed by slug).
- `_sprite_slug(common_name, slugs)` — checks `realistic-sprites/{slug}.png` exists.
- `_common_to_slug(name)` — `name.lower().replace("'","")` → `re.sub(r"[^a-z0-9]+","_")`.
- Spectrograms and callviz JSON are disk-cached under `recordings/cache/` and
  reused on subsequent requests.
- Three.js (`static/three.min.js`, `static/OrbitControls.js`) is **vendored**
  (r134 UMD build). Never replace with a CDN link.

**`PEAK_CONF_FLOOR = 0.7`** — Bird-Dex hides species whose peak confidence is below
this (they appear in DB but are filtered in the `/` route).

**`TIMELINE_CONF_FLOOR = 0.7`** — Timeline swimlane only plots detections ≥ 0.7.

---

## Internal Patterns

### Audio sample clock vs wall clock

`Segment.started_at` is computed from a sample counter, not `datetime.now()`.
This is intentional: it gives sub-second accuracy and is immune to CPU scheduling
jitter. But it drifts from wall time if:

1. **ffmpeg restarts** — handled by `Respawn` → `_reset(wall_time)` in
   `segmenter.segment_stream`.
2. **Machine sleeps while ffmpeg stays alive** — sample clock freezes, wall time
   advances. Handled by the drift resync in `segmenter._flush()`: if
   `wall_elapsed - audio_elapsed > drift_resync_seconds` (default 60 s), the
   `_stream_start` is re-anchored. Timestamps only jump forward, never back.

The monitor logs `(processed Xh Ym late)` when a segment is dequeued significantly
after its capture time (`_processing_lag_note(started_at, threshold=120s)`).

### Adaptive noise floor

`segment_stream` maintains a per-stream EMA-min (`floor_ema`). It tracks the
quietest recent ambient level with fast fall (`alpha_down=0.08`) and slow rise
(`alpha_up=0.002`). A hop is "active" when `rms > floor_ema + activity_margin_db`.
The system will not flush until `warmup_hops` (default 300) have been consumed,
to let the floor stabilize.

### Mic-dead detection

`_silent_streak` counts consecutive hops with `rms ≤ mic_dead_dbfs (-85 dBFS)`.
If the streak reaches `mic_dead_hops`, raises `MicDead`. On macOS, a blocked mic
returns clean silence (zeros → ~-91 dBFS), so this is the primary permission
guard in the streaming path (as opposed to `recorder.record()` which has a
one-shot volume check).

### Dashboard audio protection policy

`protected_dashboard_detection_ids()` returns a set of detection IDs whose audio
the dashboard needs. The policy keeps:
1. The peak-confidence detection for each species (all-time).
2. The peak-confidence detection for each species *per day*.
3. The top-200 most-confident detections all-time.
4. All detections heard in the last `live_hours` (for `/live`).

Audio associated with any other detection ID can be trimmed by
`cleanup_dashboard_audio()`.

### Spectrogram/callviz/vibeviz caching

Routes that compute audio visualizations write results to
`recordings/cache/spectrograms/`, `recordings/cache/callviz/`, and
`recordings/cache/vibeviz/`. Cache keys include `segment_id` and the
`start`/`end` window offsets. Vibeviz also includes a version number
(`_VIBEVIZ_VERSION`) to invalidate when the algorithm changes.

### Slug ↔ common_name mapping

Two sources:
1. `docs/birds.json` — canonical list of ~110 known species, each with
   `{common_name, slug}`. Used to build `_SLUG_MAP_CACHE` and `_SLUG_TO_NAME_CACHE`.
2. Fallback: `_common_to_slug(name)` — slugifies any name BirdNET might return
   that isn't in `birds.json`.

When adding a new species detected in prod: add a `{common_name, slug}` entry to
`birds.json`, add a sprite PNG to `realistic-sprites/`, and add field notes to
`bird_info.json`.

---

## Testing Seams

**`tests/conftest.py`** — creates an in-memory SQLite DB seeded with fixture data
and monkeypatches `dashboard._db` to return it. This lets route tests run with no
filesystem, mic, or BirdNET.

**Heavy imports are lazy:** `matplotlib`, `librosa`, `soundfile` are imported
only inside the functions that need them (`_compute_callviz`, spectrogram render,
etc.). The test suite's `requirements-dev.txt` omits these, so tests run fast.

**Test files:**

| File | Coverage |
|---|---|
| `tests/test_storage.py` | Schema, `record_segment`, rollup queries, retention, `recent_feed` |
| `tests/test_config.py` | `load`, `resolve`, bad JSON |
| `tests/test_identifier_summarize.py` | `summarize()`, default `min_conf` |
| `tests/test_dashboard.py` | Helpers + all routes via `test_client` |
| `tests/test_birdid.py` | `cmd_stats`, `_resolve_id_params` |
| `tests/test_clips.py` | `clip_dst_path`, `write_clip` |
| `tests/test_segmenter.py` | `segment_stream` with synthetic PCM |

**Run the full suite:**
```bash
./.venv/bin/python -m pytest -q
```

**Dev fixture:** `~/Desktop/bird.wav` (3 s clip → Bewick's Wren, ~0.92 confidence).
Use for manual smoke tests of `identify`, `monitor`, and `listen`.

---

## File Map

```
birdid.py            CLI entry point — wires all modules; defines cmd_* functions
recorder.py          mic → wav/PCM stream (macOS AVFoundation + ffmpeg)
segmenter.py         PCM hops → adaptive Segment objects (pure numpy, no mic)
identifier.py        wav → [Detection] via BirdNET (pure file-in/list-out)
storage.py           SQLite CRUD — schema, record_segment, queries, cleanup
clips.py             Extract detection-window clips from segment wavs
config.py            load config.json, resolve() flag > config > default
dashboard.py         Flask web UI — all routes, template strings, viz computations

config.json          Runtime config (gitignored; see config.example.json)
config.example.json  Committed template with Santa Barbara defaults
birdid.db            SQLite database (gitignored)

docs/birds.json          Canonical species list (common_name + slug)
docs/bird_info.json      Field notes per species (keyed by slug)
realistic-sprites/       Per-species illustration PNGs (field-guide style)
sprites/                 Older pixel-art sprite PNGs (legacy)
static/three.min.js      Vendored Three.js r134 UMD (never replace with CDN)
static/OrbitControls.js  Vendored Three.js OrbitControls

deploy/mac-mini/deploy.sh         Deploy script (run on the mini, not dev)
deploy/mac-mini/com.birdid.*.plist  launchd service definitions

scripts/pull_db_from_mini.sh       Pull prod DB snapshot to dev
scripts/pull_recordings_from_mini.sh  Pull prod segment wavs to dev
scripts/backfill_track_clips.py    Write missing clips for existing DB tracks
scripts/backfill_clip_mp3.py       Transcode existing wav clips to MP3
scripts/chop_realistic_sprites*.py Slice Gemini sprite sheets into individual PNGs

tests/conftest.py        Shared fixtures (in-memory DB, dashboard monkeypatch)
tests/test_*.py          Pytest suite (no mic, no BirdNET, no TF)

requirements.txt             Apple Silicon runtime deps
requirements-intel-mac.txt   Intel Mac runtime deps (TF ≤ 2.16)
requirements-dev.txt         Test-only deps (pytest + Flask; no TF)
pyproject.toml               Pytest config (testpaths, markers)

recordings/                  Segment wavs + clips (gitignored)
recordings/clips/            Per-window clip files (clip_*.mp3 or .wav)
recordings/cache/            Server-rendered spectrogram/callviz/vibeviz cache
recordings/ab_corpus/        Manually curated segments for AB testing
```
