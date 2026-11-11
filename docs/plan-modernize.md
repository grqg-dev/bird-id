# bird-id: capture, retention and dashboard plan

Written 2026-11-11 from a read-only survey of prod (Mac mini) and the repo at
`3f9186b`. Nothing in this plan has been applied. Each work package is sized
for one agent session and one PR. Read `AGENTS.md` first; its rules (no prod
deploys without Matt's OK, module boundaries, offline dashboard) still apply.

## Decisions Matt needs to make

| # | Decision | Recommendation |
|---|---|---|
| D1 | Retention tiers and disk budget (see WP2) | **Approved by Matt 2026-11-11.** 0.9+ kept 1 year, 0.7–0.9 for 30 days, below 0.7 for 7 days, best 10 per species forever, 10 GB cap |
| D2 | Backend direction (see WP5) | **Approved by Matt 2026-11-11.** Keep Flask + waitress, split `dashboard.py`, retire legacy pages. No framework swap. |
| D3 | Hardware: replace or re-seat the USB lavalier mic | Do the 2-minute capture test in WP0 first, then decide |
| D4 | What to do with implausible species (Northern Fulmar etc., see WP4) | Flag and hide by default, never delete |
| D5 | OK to deploy to prod after each WP | Prod is 6 commits behind `main` today |

## Findings

### F1. Capture has been delivering about 21% of real time since Aug 15

Evidence, all from prod `birdid.db` and process inspection:

| Period | Hours recorded per day | Detections per recorded hour |
|---|---|---|
| Jun 8 – Aug 7 | 24.0 | 94 |
| Aug 8 – Aug 15 | 0 (no data at all) | — |
| Aug 15 – now | 5.5 | 12 |

- Segments arrive as about 16 s of audio followed by a 60–70 s hole. In the
  last 3 days there were 3,282 such holes; in a July sample of 4 days there
  were 722, so the stall existed before but was much rarer.
- A stack sample of the live ffmpeg process shows its `avfoundation` demux
  thread asleep waiting for frames about 99% of the time. The Python monitor
  is idle on `queue.get`. So the stall is on the device / CoreAudio side, not
  in the segmenter, the pipe, or BirdNET.
- The ffmpeg process itself has not restarted in 14 days, so `Respawn` never
  fires. `recorder.stream_pcm()` blocks on `proc.stdout.read()` with no
  timeout, and only the 60 s drift resync in `segmenter._flush()` papers over
  the gap by jumping timestamps forward.
- The break lines up with the monitor moving from a Terminal session to the
  launchd app wrapper (`logs/` was created Aug 15 20:27, first segment 20:30).
  Whether the wrapper or something physical changed that week is not known.

### F2. Since Oct 19 the mic is also quieter and intermittently dead

- Loudness by hour of day was identical before and after Aug 15 (noon mean
  −44.9 vs −44.4 dBFS). From Oct 19 it drops 5–9 dB at every hour (noon −53.7).
- About 3,070 segments at −90 dBFS (digital silence) on Oct 23–27, and 421
  `Microphone appears permanently silent` crashes in `logs/monitor.err`.
- The monitor was restarted Oct 27 17:40 and silence stopped, but levels
  stayed low.
- Device: `USB Lavalier Microphone` (DCMT Technology), default input, 48 kHz.
- Comparison of June audio on dev with this week's clips found no dropouts or
  clicks inside clips. The June sample is small (9 clips, 30 wavs) and may not
  be the same mic placement, so treat that as weak evidence.

F1 and F2 together point at the mic, cable or USB port, which matches Matt's
hunch. A software watchdog (WP1) may recover much of F1 regardless.

### F3. Retention keeps almost nothing, and not the high-confidence clips

| Confidence | Detections | Still have audio |
|---|---|---|
| 0.9+ | 21,719 | 54 |
| 0.7–0.9 | 38,987 | 190 |
| 0.5–0.7 | 40,321 | 251 |
| 0.3–0.5 | 55,679 | 504 |

971 of 153,405 clips survive (22 MB). No clip from June–September exists.
That audio is gone and cannot be recovered. Disk is not the constraint: 20 GB
free, and a clip is about 23 KB.

Three mechanisms overlap, and prod runs an older mix of them:

1. `storage.expire_segment_audio` / `expire_track_clips`: 30-day cutoff.
2. High-confidence exemption (0.9+ gets 90 days), commit `448e196`. **Not on
   prod**, which is at `312206b`.
3. `storage.cleanup_dashboard_audio`: every 6 hours drops every clip outside a
   "protected" set (top 200 per species by confidence, best per species per
   day, last 24 h).

Why it looks inconsistent:

- Mechanism 3 ignores confidence tiers. A 0.95 clip outside its species' top
  200 is deleted within a day; a 0.31 clip of a rarely heard species is kept.
- The top-200 ranking is computed over all detections, including ones whose
  audio already expired. For common species the 200 slots are held by June and
  July rows with no audio, so new high-confidence clips never qualify. (This
  is read from the code, not reproduced.)
- Mechanism 1 then deletes even protected clips at 30 days.
- `_run_audio_cleanup` runs after every segment (about every 8 s), so all of
  this, plus a `Path.resolve()` per clip file, runs thousands of times a day.

### F4. Data quality has degraded with the audio

The 30-day view on `/dash` shows Anna's Hummingbird at 1,342 of 2,261
detections, then Northern Fulmar (154), Brant (87), Dunlin, Common Loon,
Horned Lark. Those seabirds and shorebirds are not plausible backyard regulars.
The location filter is on, so they pass BirdNET's species list but are almost
certainly noise. There is no plausibility layer.

### F5. Dashboard and ops

- `dashboard.py` is 3,367 lines with 30 routes: the React SPA at `/dash`, its
  two JSON APIs, and 12 legacy server-rendered templates.
- `dashboard-ui` is already current (React 19, Vite 8, TypeScript 6, no
  runtime deps). It needs features, not a rewrite.
- UI bugs seen on prod `/dash` with the 30d slice selected: the header and
  Field Log still span May 31 – Nov 11, and One-day Wonders lists June dates.
- Several species render initials instead of sprites (Northern Fulmar, Brant,
  Green Heron).
- No audio playback on the main dashboard; it is behind the "sure calls" link.
- Nothing shows capture health. The dashboard looked normal for three months
  while 79% of audio was missing.
- `ffmpeg` is spawned with `stderr=PIPE` that nobody reads.
- `logs/monitor.log` (6.8 MB) and `logs/dashboard.err` (1.9 MB of access
  lines) grow without rotation.
- Prod checkout has an uncommitted `AGENTS.md` change and is 6 commits behind.

---

## Work packages

Order: WP0 → WP1 → WP2 → WP3, then WP4–WP7 in any order.

### WP0. Capture diagnosis on prod (Matt, 10 minutes, no code)

1. In Screen Sharing on the mini, in Terminal:
   `ffmpeg -f avfoundation -i :0 -t 120 -ac 1 -ar 48000 /tmp/mictest.wav`
   then `ffprobe /tmp/mictest.wav`. A healthy mic gives 120 s of audio in
   about 120 s of wall time. Run it while the monitor is stopped, then again
   while it is running.
2. If the file is short or the command takes much longer than 2 minutes:
   move the mic to another USB port (no hub), repeat. Still bad → replace mic.
3. If the Terminal test is healthy but the launchd monitor still stalls, the
   app wrapper is the cause; WP1 step 4 covers that.

Record the result at the top of this file.

### WP1. Capture watchdog and health record

Files: `recorder.py`, `segmenter.py`, `storage.py`, `birdid.py`, tests.

1. `recorder.stream_pcm()`: read the pipe with a timeout (reader thread or
   `select` on the fd). If no full hop arrives for `capture_stall_seconds`
   (new config key, default 5), kill ffmpeg, yield `Respawn`, restart. Send
   ffmpeg stderr to a bounded buffer or a log file instead of an unread pipe.
2. New table `capture_health(hour TEXT PRIMARY KEY, audio_seconds REAL,
   stalls INTEGER, respawns INTEGER, mean_dbfs REAL, dead_seconds REAL)`,
   upserted by the monitor's main thread. Add to `storage._migrate()`.
3. Backfill script `scripts/backfill_capture_health.py` that derives
   `audio_seconds` and `mean_dbfs` per hour from existing `segments` rows
   (stalls and respawns stay NULL for history).
4. If WP0 pointed at the app wrapper: add `ProcessType = Interactive` to
   `deploy/mac-mini/com.birdid.monitor.plist` and re-test. Do not change the
   plist otherwise.
5. Monitor prints one line per hour: `[health] 14:00 audio 3540s/3600s,
   stalls 2, mean -47 dBFS`.

Done when: unit test with a fake hop source that stalls shows a `Respawn`
within the timeout; `pytest -q` passes; the monitor smoke test from
`AGENTS.md` shows "Model loaded" once and seg lines every 8–12 s; a manual
`kill -STOP` of ffmpeg during the smoke test triggers a respawn.

### WP2. One retention policy

Files: new `retention.py`, `storage.py`, `birdid.py`, `config.py`, tests,
`AGENTS.md`, `README.md`.

Policy (numbers are D1; all are config keys):

| Rule | Default |
|---|---|
| Track peak confidence 0.9+ | keep 365 days |
| 0.7 to 0.9 | keep 30 days |
| below 0.7 | keep 7 days |
| Best N clips per species **among clips that still have audio** | N = 10, kept forever |
| Last 24 h | always kept (live feed) |
| Total clip bytes cap | 10 GB; over the cap, evict lowest confidence then oldest, never the best-N set |
| Full segment wavs | deleted once clips are written (unchanged) |

Estimated size at summer rates: about 2,500 clips a week at 0.9+ is roughly
60 MB a week, 3 GB a year.

1. `retention.py`: a pure function
   `plan(tracks, now, policy) -> list[Decision(track_id, keep, reason)]`
   with no file or DB access, plus a thin `apply()` that unlinks files and
   nulls `clip_path`. Reasons: `high_conf`, `mid_conf`, `low_conf`,
   `species_best`, `live_window`, `over_budget`.
2. Add `tracks.max_conf REAL` (migration + backfill from `detections`) so the
   plan needs no correlated subquery.
3. Delete `cleanup_dashboard_audio`, `protected_dashboard_detection_ids`,
   `_protected_dashboard_*`, `expire_track_clips`, the
   `scripts/cleanup_dashboard_audio.py` wrapper, and config keys
   `proactive_cleanup`, `dashboard_cleanup_hours`, `dashboard_live_hours`,
   `high_conf_retention_days`. Keep `expire_segment_audio`, orphan purges and
   cache purge.
4. Monitor runs retention once an hour, not per segment.
5. `birdid.py cleanup --dry-run` prints counts and bytes per reason.
6. Check every dashboard query that assumed the old protected set still
   filters on `clip_path IS NOT NULL` (`/prevalence`, `/api/dash/sure`,
   species clip list).

Done when: table-driven tests cover each rule, the budget eviction order, and
the "best N only counts clips with audio" case; `pytest -q` passes; a dry run
against a pulled prod DB (`scripts/pull_db_from_mini.sh`) reports zero
deletions of 0.9+ clips younger than a year.

Deploy note: on first prod run nothing old comes back. The dry-run output
goes to Matt before the real run.

### WP3. Capture health in the dashboard

Files: `dashboard.py` (or the blueprint from WP5), `dashboard-ui/src`.

1. `/api/dash/health`: last 24 h and last 30 days from `capture_health`.
2. Banner at the top of `/dash` when the last 24 h has under 90% coverage, or
   the last segment is over 10 minutes old, or mean level is 6 dB below the
   30-day median. Plain text with the number, e.g. "Mic captured 5.4 of the
   last 24 hours".
3. Field Log and Punchcard cells show coverage: hatch or dim days and hours
   with under 50% audio so a quiet day and a deaf day look different.
4. Rates in the UI (calls per hour, leaderboard deltas "vs prior window")
   divide by recorded hours, not wall hours.

Done when: with a seeded DB that has a 6-hour hole, the banner appears and
the hole is visible on the punchcard; `npm run build` and `pytest -q` pass.

### WP4. Species plausibility

Files: `identifier.py` (read-only check), `storage.py`, `dashboard.py`,
`docs/`, UI.

1. New `docs/yard_list.json`: per species `status` of `expected`, `possible`
   or `unlikely`. Seed it from prod: species with 0.9+ detections on 5 or more
   days are `expected`; pelagic and shorebird families are `unlikely`. Matt
   reviews the list (154 species).
2. Dashboard hides `unlikely` by default, with a toggle and a count
   ("12 unlikely species hidden"). Stored detections are never deleted.
3. "New encounters" requires either one detection at 0.7+ or detections on
   two separate days.
4. Check whether `birdnetlib`'s species-list threshold is exposed; if so add a
   config key. Do not lower `min_conf`.

Done when: Northern Fulmar and Brant are absent from the default 30-day view
and present with the toggle on; tests cover the filter.

### WP5. Backend cleanup (keep Flask)

The jank is one 3,367-line file serving two generations of UI, not Flask
itself. Flask + waitress has no arm64/Pi problems and the test seam
(`dashboard._db()`) already works. A move to FastAPI would add dependencies
and rewrite 30 routes for no user-visible gain.

1. Split into a `dashboard/` package: `api.py` (JSON), `media.py` (audio,
   spectrogram, sprites, callviz), `legacy.py` (templates), `app.py`
   (factory, `_db`). Pure move, no behavior change, tests unchanged except
   imports.
2. Version the JSON API under `/api/v1/…`; keep old paths as aliases.
3. Retire legacy pages once the SPA has an equivalent (WP6): `/dex`, `/bird`,
   `/heard`, `/timeline`, `/trends`, `/prevalence`, `/live`, `/realtime`,
   `/twitter`, `/data`. Keep `/call` and `/vibe` (Three.js views). Each
   retired route redirects to its SPA equivalent.
4. Access log off stderr; rotate `logs/*.log` at 5 MB with
   `logging.handlers.RotatingFileHandler`.
5. `deploy/mac-mini/deploy.sh` refuses to run on a dirty checkout and prints
   the commit it deployed.

Done when: `pytest -q` passes after each step; every route in the old file
still answers 200 or a redirect.

### WP6. Dashboard UI enhancements

Files: `dashboard-ui/src`. Use the `frontend-design` skill; keep the existing
warm palette and the Game mode skin; no new runtime dependencies; no CDN.

1. Fix slicing: header range, Field Log, One-day Wonders and New Encounters
   must respect the selected window (`lib/slice.ts`).
2. Inline audio: a play button and spectrogram on Leaderboard rows and species
   cards, using the best retained clip. Show the retention reason and expiry
   ("kept until Nov 2027 · 0.94").
3. Species page inside the SPA (replaces `/bird/<slug>`): field notes from
   `docs/bird_info.json`, hour-of-day chart, clip list.
4. Live feed panel (replaces `/live`), polling `/api/recent`, paused when the
   tab is hidden.
5. Client-side routing with the History API so species and day views have
   URLs; no router library needed at this size.
6. Phone layout pass at 375 px.
7. Missing sprites: list species with detections and no sprite, add slugs to
   `docs/birds.json`. Generating artwork is a separate task for Matt.

Done when: `npm run build` and `npm run lint` pass; each item checked in the
browser against a pulled prod DB at desktop and 375 px.

### WP7. Housekeeping

1. Prod checkout: show Matt the `AGENTS.md` diff on the mini, then stash or
   commit it so `git pull` is clean.
2. `AGENTS.md` and the memory note still say the prod monitor runs in a
   Terminal session; it runs under launchd (`com.birdid.monitor`). Update.
3. `segments` has 809,616 rows, most with no detection. Leave as is (144 MB)
   unless it reaches 1 GB; then roll empty segments older than 90 days into
   `capture_health`.
4. Remove stray repo files after Matt confirms: `biglog.sh` (462 KB),
   `birdid.db.local-backup-*`, `.ndemo/`, `demo/`.

## Suggested agent assignment

| WP | Model | Notes |
|---|---|---|
| WP0 | Matt | Physical access needed |
| WP1 | Sonnet 5.5 | Threading in `stream_pcm` is the only subtle part; review the diff |
| WP2 | Sonnet 5.5 | Pure function plus tests; review the dry-run before prod |
| WP3, WP5, WP7 | Sonnet 5.5 | Mechanical |
| WP4 | Sonnet 5.5 + Matt | Matt reviews the yard list |
| WP6 | Sonnet 5.5, or a stronger model for the design pass | Largest judgment component |
