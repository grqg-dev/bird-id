# Handoff: /dash — React dashboard (clean + Pokedex/Gameboy skins)

State as of 2026-07-02 late night. **All code written, compiles, builds, serves.
Not yet visually verified, tested, or committed.**

## What it is

New flagship dashboard at `/dash`. Vite + React + TS SPA in `dashboard-ui/`,
built to `dashboard-ui/dist/`, served by Flask. One JSON endpoint
(`/api/dash/summary`) returns a `[dayIdx, hour, speciesIdx, confBucket, count]`
cube (12k rows, ~234KB) + species meta (sprites, best clip, `bird_info.json`
blurbs) + per-day sun times; all slicing is client-side and instant.
Two skins, same DOM: `clean` (default, warm paper + terracotta) and `game`
(Pokedex red + DMG green, Press Start 2P, pixelated sprites), toggled in the
masthead, persisted as localStorage `dash-theme`.

Widgets: sticky Slicer (range/day/hours/conf/species search) · Headline strip ·
Field log calendar · Day rhythm (24h + sun markers) · Punchcard (day×hour) ·
The regulars/Your party (top 6) · Leaderboard (trend vs prior window) ·
New encounters (discovery curve) · One-day wonders + Night shift · Dex grid
(№ = discovery order, ★new ◐night ♦rare badges, 4 sorts) · Dex entry drawer
(flavor text, 24h profile, daily sparkline, audio via `/audio/…`, `/bird/…` link).
"Field note" under the header = one data-derived story line per slice, click to
cycle (`src/lib/fable.ts` — user nixed calling it a fable in the UI).

## Verified

- `npx tsc --noEmit` clean; `npm run build` clean (69KB gz JS, 3.2KB CSS, 4.7KB font).
- Flask test client: `/dash` 200, hashed assets 200, `/psprite/<slug>.png` 200/404,
  `/api/dash/summary` 200 (0.66s cold, 30ms cached, ETag/304 works).
- Local `config.json` db → `birdid.prod.db` (80MB prod snapshot pulled 7/2 via
  `VACUUM INTO` over ssh). Revert to `birdid.db` for old local data.

## Not done (next session, in order)

1. **Look at it.** `./.venv/bin/python dashboard.py` then open
   `http://127.0.0.1:8080/dash` (built) or `cd dashboard-ui && npm run dev` →
   `http://localhost:5173/dash/` (hot reload, proxies to Flask). Check both skins,
   mobile width, drawer, tooltips. Screenshot via agent-browser skill.
   Expect layout nits (game-skin font sizes, punchcard density) — iterate.
2. Palette check (dataviz skill validator) on clean heat ramp vs white.
3. Tests: `tests/` pytest for summary shape/ETag/excluded-day + psprite/dash routes.
4. Add `/dash` link to `templates/includes/site-nav.html`; note build workflow in
   AGENTS.md / codebase-guide (dev: npm run dev + Flask; deploy: commit `dist/`).
5. Commit (repo commits straight to main): `dashboard.py` (new section between
   `_dev_mode` area and `/sprite` route: `_build_dash_summary`, `api_dash_summary`,
   `dash_index`, `dash_asset`, `pixel_sprite`), `dashboard-ui/` incl. `dist/`
   (deploy artifact — check .gitignore doesn't exclude dist), docs.
   **git add: beware repo-root untracked junk (`biglog.sh`, `.ndemo/`, `demo/`,
   `birdid.prod.db` ~80MB — do NOT commit the DB).** Add `birdid.prod.db` +
   `dashboard-ui/node_modules` to .gitignore.
6. **No prod deploy without explicit user OK** (AGENTS.md). Deploy = git pull on
   mac-mini + confirm launchd dashboard picks up new routes (it imports dashboard.py,
   so restart of the dashboard launch agent is needed — see deploy/mac-mini/).
   Prod monitor runs in a Terminal session Matt restarts himself (memory note).

## Gotchas

- `sliced.speciesDaily()` only has rows for the focused species when a species
  filter is on — sparkline components already guard with `?? []`.
- `clip` refs are absent locally (audio lives on prod); drawer shows
  "no recording kept on this machine".
- Cube conf buckets: 0=[.3,.5) 1=[.5,.7) 2=[.7,.9) 3=[.9,1]; `EXCLUDED_DAYS`
  (2026-06-08) filtered server-side, shown as × in calendar/punchcard.
- Sideshow surface open: mockup post `0X9MWGlbzdA`, session `Wd2WXZGzvm8`
  (approved). Publish screenshots there; drain comments with
  `sideshow wait --session Wd2WXZGzvm8 --timeout 1`.
- No em-dashes in visible UI copy; keep it high-signal, no slop.
