# Pre-refactor baseline

Recorded **2026-07-14** before large refactor work. Re-run this gate after
significant infra changes.

## Git

| Item | Value |
|------|-------|
| Branch | `pre-refactor-readiness` @ `24df1fd` (from `main` @ `6e124ba`) |
| Parent commit | `6e124ba` — feat: add /dash React dashboard |
| Prod mini HEAD | `6e124ba` (synced with `origin/main`) |

## Prod service state (Mac mini, read-only audit)

| Service | State |
|---------|-------|
| `com.birdid.dashboard` | launchd loaded, `:8080` returns 200 |
| `com.birdid.monitor` | **Not loaded** — monitor running as manual Terminal process (PID observed 2026-07-14) |
| `birdid.db` | integrity `ok`; ~401k segments, ~109k detections, 124 species |
| Segment wavs on disk | **0** — `drop_segment_after_clips: true` |
| Clip MP3s on disk | ~6,361 under `recordings/clips/` |

**Caveat:** Do not restart monitor launchd without user confirmation — may conflict
with the intentional Terminal session.

## Dev verification (local Apple Silicon checkout)

Commands run from repo root with `./.venv/bin/python` unless noted.

| Check | Command | Result |
|-------|---------|--------|
| Fast tests | `pytest -q` | **111 passed** (includes 2 new fixture tests) |
| Diff hygiene | `git diff --check` | clean |
| BirdNET smoke | `./scripts/smoke_identify.sh` | Bewick's Wren **0.918** @ `-c 0.1` |
| Dashboard routes | `pytest tests/test_dashboard.py -q` | **46 passed** |
| React lint | `cd dashboard-ui && npm run lint` | warnings in committed `dist/` only; src clean |
| React build | `cd dashboard-ui && npm run build` | **ok** (~69 KB gz JS) |

## Fixture inventory

| Path | SHA-256 (first 16) | Purpose |
|------|-------------------|---------|
| `tests/fixtures/bewicks_wren.wav` | `3994dea49ba07378` | BirdNET integration smoke; tracked in git |

## Prod → dev data pulls

```bash
./scripts/pull_db_from_mini.sh --activate
./scripts/pull_clips_from_mini.sh --recent 30
```

Never push local DB or recordings to prod.

## Ready for refactor when

- [x] `logs/` gitignored; `config.json` untracked (local only)
- [x] Tracked audio fixture replaces `~/Desktop/bird.wav` references
- [x] `pull_clips_from_mini.sh` for dashboard playback samples
- [x] Dev setup documented (`scripts/dev-setup.sh`, AGENTS.md, README.md)
- [x] Prod monitor/dashboard split documented
- [x] Baseline tests green locally
