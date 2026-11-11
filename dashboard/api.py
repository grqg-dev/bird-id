"""JSON APIs (`/api/dash/*`, `/api/digest`, `/api/recent`, `/api/twitter`) and the React SPA host (`/dash`)."""

from __future__ import annotations
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Response, abort, request, send_file

import retention
import storage
from .app import (
    EXCLUDED_DAYS,
    _CFG,
    _PIXEL_SPRITES_DIR,
    _ROOT,
    _SPRITES_DIR,
    _bird_info,
    _common_to_slug,
    _slug_map,
    _yard_status,
    app,
)
from .legacy import (
    _resolve_bird_slug,
    _build_twitter_feed,
    _feed_payload,
    _filter_feed_events,
    _parse_day_arg,
    _parse_hide_low,
    _solar_arc,
)

import dashboard as _pkg  # late-bound access so tests can monkeypatch dashboard._db etc.


def _db():
    return _pkg._db()


def _today() -> str:
    return _pkg._today()


def _now() -> datetime:
    return _pkg._now()


@app.route("/api/twitter")
def api_twitter():
    show_all, selected_day = _parse_day_arg(request.args.get("day"))
    page = max(1, request.args.get("page", 1, type=int))
    hide_low = _parse_hide_low(request.args.get("hide_low"))
    sort = "conf" if request.args.get("sort") == "conf" else "time"

    conn = _db()
    try:
        feed = _build_twitter_feed(
            conn,
            selected_day=selected_day,
            show_all=show_all,
            page=page,
            hide_low=hide_low,
            sort=sort,
        )
        return Response(
            json.dumps({
                "events": feed["events"],
                "page": feed["page"],
                "has_more": feed["has_more"],
                "sort": feed["sort"],
            }),
            mimetype="application/json",
        )
    finally:
        conn.close()


@app.route("/api/recent")
def api_recent():
    since = request.args.get("since") or None
    hide_low = _parse_hide_low(request.args.get("hide_low"))
    limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
    conn = _db()
    try:
        payload = _feed_payload(conn, since=since, limit=limit)
        events = _filter_feed_events(payload["events"], hide_low=hide_low)
        # API returns slim events (no template-only fields).
        api_events = [
            {
                "heard_at": e["heard_at"],
                "common_name": e["common_name"],
                "slug": e["slug"],
                "peak_conf": e["peak_conf"],
                "segment_id": e["segment_id"],
                "start_time": e["start_time"],
                "end_time": e["end_time"],
                "has_sprite": e["has_sprite"],
            }
            for e in events
        ]
        resp = Response(
            json.dumps({"events": api_events, "latest": payload["latest"]}),
            mimetype="application/json",
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /dash — React dashboard (SPA built from dashboard-ui/, one JSON cube API)
# ---------------------------------------------------------------------------

_DASH_DIST = _ROOT / "dashboard-ui" / "dist"


# Confidence bucket edges: bucket 0 = [min_conf, .5), 1 = [.5, .7), 2 = [.7, .9), 3 = [.9, 1]
_DASH_CONF_EDGES = (0.5, 0.7, 0.9)


_CACHE: dict[str, dict] = {}


def _cache_version(conn) -> tuple:
    """Cheap version key: bumps on new detections or when retention clears
    audio paths. All three counts are index-only (PK + the two partial
    indexes on wav_path/clip_path), unlike the old three-COUNT(*) key."""
    row = conn.execute(
        """
        SELECT
          (SELECT COALESCE(MAX(id), 0) FROM detections)              AS max_id,
          (SELECT COUNT(*) FROM segments WHERE wav_path IS NOT NULL)  AS segs,
          (SELECT COUNT(*) FROM tracks WHERE clip_path IS NOT NULL)   AS clips
        """
    ).fetchone()
    return (row["max_id"], row["segs"], row["clips"])


def _cached(
    name: str,
    conn,
    build_fn,
    *,
    extra_key: tuple = (),
) -> tuple[bytes, str]:
    """In-memory cache keyed by a cheap version tuple. Returns (json_bytes, etag).

    Rebuilds only when `_cache_version(conn) + extra_key` changes since the
    last call for this `(name, extra_key)` slot — used for anything expensive
    enough to be worth not recomputing on every request (dash summary/sure,
    digest). `extra_key` is part of the dict key, not just the comparison —
    otherwise distinct params sharing `name` (e.g. `/api/digest?day=A` vs
    `?day=B`) would evict each other on every request under a threaded server.
    """
    slot = (name, extra_key)
    key = _cache_version(conn) + extra_key
    entry = _pkg._CACHE.get(slot)
    if entry is None or entry["key"] != key:
        body = json.dumps(build_fn(conn), separators=(",", ":")).encode()
        entry = {"key": key, "body": body}
        _pkg._CACHE[slot] = entry
    etag = '"' + name + "-" + "-".join(str(k) for k in key) + '"'
    return entry["body"], etag


def _keep_dict(conf: float, started_at: str, rank: int, policy, now: datetime) -> dict:
    """{"reason", "until"} for a clip: why retention keeps it and until when
    (until = ISO date, None = no expiry). See retention.keep_info."""
    try:
        when = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return {"reason": "species_best", "until": None}
    reason, until = retention.keep_info(conf, when, rank, now, policy)
    return {"reason": reason, "until": until.date().isoformat() if until else None}


def _dash_sample_clips(
    conn,
    *,
    min_conf: float | None = None,
    per_species: int = 1,
    excluded_days: tuple[str, ...] = (),
) -> dict[str, list[dict]]:
    """Highest-confidence *still-kept* playable windows per species.

    Retention NULLs `wav_path` / `clip_path` but leaves detection rows. Sample
    selection only considers rows that still have a path so aged-out peaks do
    not hide (or get offered as) recordings. Totals stay elsewhere.
    """
    filters = ["(t.clip_path IS NOT NULL OR sg.wav_path IS NOT NULL)"]
    params: list[object] = []
    if min_conf is not None:
        filters.append("d.confidence >= ?")
        params.append(min_conf)
    if excluded_days:
        placeholders = ",".join("?" * len(excluded_days))
        filters.append(f"date(d.heard_at) NOT IN ({placeholders})")
        params.extend(excluded_days)
    where = "WHERE " + " AND ".join(filters)
    # Enough candidates per species to survive missing-on-disk path checks.
    candidate_limit = max(8, per_species * 4)

    rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT d.common_name AS name, d.segment_id AS seg,
                 d.start_time AS s, d.end_time AS e, d.confidence AS conf,
                 t.clip_path AS clip_path, sg.wav_path AS wav_path,
                 sg.started_at AS started_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY d.common_name
                   ORDER BY d.confidence DESC, d.id DESC
                 ) AS rn
          FROM detections d
          LEFT JOIN tracks t ON t.id = d.track_id
          JOIN segments sg ON sg.id = d.segment_id
          {where}
        )
        SELECT * FROM ranked WHERE rn <= ?
        """,
        (*params, candidate_limit),
    ).fetchall()
    samples: dict[str, list[dict]] = {}
    policy = retention.Policy.from_config(_CFG)
    now = _now()
    for r in rows:
        species_samples = samples.setdefault(r["name"], [])
        if len(species_samples) >= per_species:
            continue
        playable = (r["clip_path"] and os.path.exists(r["clip_path"])) or (
            r["wav_path"] and os.path.exists(r["wav_path"])
        )
        if playable:
            species_samples.append(
                {
                    "seg": r["seg"],
                    "s": round(r["s"], 2),
                    "e": round(r["e"], 2),
                    "conf": round(r["conf"], 3),
                    "keep": _keep_dict(
                        r["conf"], r["started_at"], len(species_samples), policy, now
                    ),
                }
            )
    return {name: clips for name, clips in samples.items() if clips}


def _dash_best_clips(conn) -> dict[str, dict]:
    """Highest-confidence playable window per species."""
    return {
        name: clips[0]
        for name, clips in _dash_sample_clips(conn).items()
    }


def _dash_conf_bucket_sql() -> str:
    lo, mid, hi = _DASH_CONF_EDGES
    return (
        f"CASE WHEN confidence >= {hi} THEN 3 "
        f"WHEN confidence >= {mid} THEN 2 "
        f"WHEN confidence >= {lo} THEN 1 ELSE 0 END"
    )


def _build_dash_summary(conn, include_unlikely: bool = False) -> dict:
    excluded = sorted(EXCLUDED_DAYS)
    not_excluded = "date(heard_at) NOT IN (%s)" % ",".join("?" * len(excluded)) if excluded else "1=1"

    sp_rows = conn.execute(
        f"""
        SELECT common_name AS name, scientific_name AS sci, COUNT(*) AS total,
               MAX(confidence) AS peak, MIN(heard_at) AS first, MAX(heard_at) AS last
        FROM detections WHERE {not_excluded}
        GROUP BY common_name ORDER BY total DESC, name
        """,
        excluded,
    ).fetchall()

    # "New encounters": a species counts once it has one detection at 0.7+ or
    # detections on two separate days — one stray low-confidence hit is not an arrival.
    encounter_conf = _DASH_CONF_EDGES[1]
    day_rows = conn.execute(
        f"""
        SELECT common_name AS name, MIN(heard_at) AS t,
               MIN(CASE WHEN confidence >= ? THEN heard_at END) AS hi
        FROM detections WHERE {not_excluded}
        GROUP BY common_name, date(heard_at) ORDER BY common_name, date(heard_at)
        """,
        (encounter_conf, *excluded),
    ).fetchall()
    per_day: dict[str, list] = {}
    for r in day_rows:
        per_day.setdefault(r["name"], []).append(r)

    def _encounter(name: str) -> str | None:
        rows = per_day.get(name, [])
        cands = [r["hi"] for r in rows if r["hi"]]
        if len(rows) >= 2:
            cands.append(rows[1]["t"])
        return min(cands) if cands else None

    unlikely_total = sum(1 for r in sp_rows if _yard_status(r["name"]) == "unlikely")
    visible = [r for r in sp_rows if include_unlikely or _yard_status(r["name"]) != "unlikely"]

    slugs = _slug_map()
    info = _bird_info()
    clips = _dash_best_clips(conn)
    species = []
    index_of: dict[str, int] = {}
    for i, r in enumerate(visible):
        slug = slugs.get(r["name"]) or _common_to_slug(r["name"])
        entry = {
            "name": r["name"],
            "sci": r["sci"],
            "slug": slug,
            "art": (_SPRITES_DIR / f"{slug}.png").is_file(),
            "pixel": (_PIXEL_SPRITES_DIR / f"{slug}.png").is_file(),
            "total": r["total"],
            "peak": round(r["peak"], 3),
            "first": r["first"],
            "last": r["last"],
            "days": len(per_day.get(r["name"], [])),
            "encounter": _encounter(r["name"]),
            "status": _yard_status(r["name"]),
        }
        clip = clips.get(r["name"])
        if clip:
            entry["clip"] = clip
        blurb = info.get(slug)
        if blurb:
            entry["info"] = blurb
        species.append(entry)
        index_of[r["name"]] = i

    day_row = conn.execute(
        "SELECT date(MIN(heard_at)) AS lo, date(MAX(heard_at)) AS hi FROM detections"
    ).fetchone()
    days: list[str] = []
    sun: list[dict] = []
    if day_row["lo"]:
        d = date.fromisoformat(day_row["lo"])
        hi = date.fromisoformat(day_row["hi"])
        lat = _CFG.get("lat") or 34.4208
        lon = _CFG.get("lon") or -119.6982
        while d <= hi:
            days.append(d.isoformat())
            arc = _solar_arc(lat, lon, d)
            sun.append(
                {
                    "rise": round(arc["sunrise_pct"] / 100 * 24, 2),
                    "set": round(arc["sunset_pct"] / 100 * 24, 2),
                }
            )
            d += timedelta(days=1)
    day_index = {day: i for i, day in enumerate(days)}

    cube_rows = conn.execute(
        f"""
        SELECT date(heard_at) AS day, CAST(strftime('%H', heard_at) AS INTEGER) AS hour,
               common_name AS name, {_dash_conf_bucket_sql()} AS cb, COUNT(*) AS n
        FROM detections WHERE {not_excluded}
        GROUP BY day, hour, name, cb
        """,
        excluded,
    ).fetchall()
    cube = [
        [day_index[r["day"]], r["hour"], index_of[r["name"]], r["cb"], r["n"]]
        for r in cube_rows
        if r["day"] in day_index and r["name"] in index_of
    ]

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "location": "Santa Barbara, CA",
            "tz": "America/Los_Angeles",
            "min_conf": _CFG.get("min_conf", 0.3),
            "conf_edges": list(_DASH_CONF_EDGES),
            "excluded_days": excluded,
            "include_unlikely": include_unlikely,
            "unlikely_total": unlikely_total,
            "unlikely_hidden": 0 if include_unlikely else unlikely_total,
        },
        "days": days,
        "sun": sun,
        "species": species,
        "cube": cube,
    }


def _build_dash_sure(conn, include_unlikely: bool = False) -> dict:
    """All-time species leaderboard for detections at 0.9+ confidence."""
    min_conf = _DASH_CONF_EDGES[-1]
    excluded = tuple(sorted(EXCLUDED_DAYS))
    not_excluded = (
        "date(heard_at) NOT IN (%s)" % ",".join("?" * len(excluded))
        if excluded
        else "1=1"
    )
    rows = conn.execute(
        f"""
        SELECT common_name AS name, scientific_name AS sci, COUNT(*) AS count,
               MAX(confidence) AS peak
        FROM detections
        WHERE confidence >= ? AND {not_excluded}
        GROUP BY common_name
        ORDER BY count DESC, name
        """,
        (min_conf, *excluded),
    ).fetchall()

    slugs = _slug_map()
    info = _bird_info()
    clips = _dash_sample_clips(
        conn,
        min_conf=min_conf,
        per_species=3,
        excluded_days=excluded,
    )
    species = []
    unlikely_total = sum(1 for r in rows if _yard_status(r["name"]) == "unlikely")
    for row in rows:
        if not include_unlikely and _yard_status(row["name"]) == "unlikely":
            continue
        slug = slugs.get(row["name"]) or _common_to_slug(row["name"])
        entry = {
            "name": row["name"],
            "sci": row["sci"],
            "slug": slug,
            "art": (_SPRITES_DIR / f"{slug}.png").is_file(),
            "pixel": (_PIXEL_SPRITES_DIR / f"{slug}.png").is_file(),
            "status": _yard_status(row["name"]),
            "count": row["count"],
            "peak": round(row["peak"], 3),
            "clips": clips.get(row["name"], []),
        }
        blurb = info.get(slug)
        if blurb:
            entry["info"] = blurb
        species.append(entry)

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "location": "Santa Barbara, CA",
            "min_conf": min_conf,
            "excluded_days": list(excluded),
            "include_unlikely": include_unlikely,
            "unlikely_total": unlikely_total,
            "unlikely_hidden": 0 if include_unlikely else unlikely_total,
        },
        "species": species,
    }


def _wants_unlikely() -> bool:
    """`?unlikely=1` includes species marked 'unlikely' in docs/yard_list.json."""
    return request.args.get("unlikely", "").lower() in ("1", "true", "yes", "on")


@app.route("/api/dash/summary")
def api_dash_summary():
    conn = _db()
    try:
        include = _wants_unlikely()
        body, etag = _cached(
            "dash_summary", conn, lambda c: _build_dash_summary(c, include),
            extra_key=(include,),
        )
    finally:
        conn.close()
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    return Response(
        body, mimetype="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@app.route("/api/dash/sure")
def api_dash_sure():
    conn = _db()
    try:
        include = _wants_unlikely()
        body, etag = _cached(
            "dash_sure", conn, lambda c: _build_dash_sure(c, include),
            extra_key=(include,),
        )
    finally:
        conn.close()
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    return Response(
        body, mimetype="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


def _digest_clip(row) -> dict | None:
    """Audio/spectrogram links for a dex-shaped row's peak window, only when
    that clip's audio still exists (retention may have cleared it)."""
    if not row["has_audio"]:
        return None
    qs = f"?start={row['start_time']}&end={row['end_time']}"
    return {
        "segment_id": row["segment_id"],
        "start": row["start_time"],
        "end": row["end_time"],
        "audio_url": f"/audio/{row['segment_id']}{qs}",
        "spectrogram_url": f"/spectrogram/{row['segment_id']}.png{qs}",
    }


def build_digest(
    conn, day: str, *, gap: int = 14, limit: int = 10, include_unlikely: bool = False
) -> dict:
    """Daily digest: totals, life-list firsts, species back after a gap, and
    the day's top detections. Shared by GET /api/digest and
    `birdid.py digest --json` so both read the same shape."""
    slugs = _slug_map()

    def slug_of(name: str) -> str:
        return slugs.get(name) or _common_to_slug(name)

    totals_row = storage.digest_totals(conn, day)
    all_dex_rows = storage.species_dex_day(conn, day)
    hide = lambda name: not include_unlikely and _yard_status(name) == "unlikely"  # noqa: E731
    dex_rows = [r for r in all_dex_rows if not hide(r["common_name"])]
    hidden_unlikely = len(all_dex_rows) - len(dex_rows)
    new_names = {n for n in storage.new_species_on(conn, day) if not hide(n)}

    new_species = [
        {
            "common_name": r["common_name"],
            "scientific_name": r["scientific_name"],
            "slug": slug_of(r["common_name"]),
            "first_heard": r["first_heard"],
            "peak_conf": round(r["peak_conf"], 3),
            "clip": _digest_clip(r),
        }
        for r in dex_rows
        if r["common_name"] in new_names
    ]

    returning_species = []
    for r in storage.digest_returning_species(conn, day, gap=gap):
        if hide(r["common_name"]):
            continue
        last_before = r["last_seen_before"]
        days_absent = (
            date.fromisoformat(day) - date.fromisoformat(last_before[:10])
        ).days
        returning_species.append(
            {
                "common_name": r["common_name"],
                "slug": slug_of(r["common_name"]),
                "last_seen_before": last_before,
                "days_absent": days_absent,
                "peak_conf": round(r["peak_conf"], 3),
            }
        )

    top_detections = []
    for r in dex_rows[: max(limit, 0)]:
        clip = _digest_clip(r)
        if clip is None:
            continue
        top_detections.append(
            {
                "common_name": r["common_name"],
                "slug": slug_of(r["common_name"]),
                "confidence": round(r["peak_conf"], 3),
                "heard_at": r["heard_at"],
                **clip,
            }
        )

    species = [
        {
            "common_name": r["common_name"],
            "slug": slug_of(r["common_name"]),
            "windows": r["windows"],
            "peak_conf": round(r["peak_conf"], 3),
            "first_heard": r["first_heard"],
            "last_heard": r["last_heard"],
        }
        for r in dex_rows
    ]

    return {
        "day": day,
        "totals": {
            "detections": totals_row["detections"],
            "species": totals_row["species"] - hidden_unlikely,
            "audio_min": round((totals_row["audio_sec"] or 0) / 60, 1),
        },
        "new_species": new_species,
        "returning_species": returning_species,
        "top_detections": top_detections,
        "species": species,
        "unlikely_hidden": hidden_unlikely,
    }


@app.route("/api/digest")
def api_digest():
    day = request.args.get("day") or _today()
    gap = request.args.get("gap", default=14, type=int)
    limit = request.args.get("limit", default=10, type=int)
    include = _wants_unlikely()
    conn = _db()
    try:
        body, etag = _cached(
            "digest", conn,
            lambda c: build_digest(c, day, gap=gap, limit=limit, include_unlikely=include),
            extra_key=(day, gap, limit, include),
        )
    finally:
        conn.close()
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    return Response(
        body, mimetype="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@app.route("/dash")
@app.route("/dash/")
def dash_index():
    index = _pkg._DASH_DIST / "index.html"
    if not index.is_file():
        return Response(
            "dashboard UI not built yet — run: cd dashboard-ui && npm install && npm run build",
            status=503,
            mimetype="text/plain",
        )
    return send_file(index)


@app.route("/dash/<path:relpath>")
def dash_asset(relpath: str):
    from flask import send_from_directory

    if (_pkg._DASH_DIST / relpath).is_file():
        return send_from_directory(_pkg._DASH_DIST, relpath)
    if relpath.startswith("assets/") or Path(relpath).suffix:
        abort(404)
    return dash_index()



# ---------------------------------------------------------------------------
# /api/dash/health — capture coverage + banner conditions (WP3)
# ---------------------------------------------------------------------------

HEALTH_MIN_COVERAGE = 0.9  # banner when the last 24 h recorded less than this
HEALTH_STALE_MINUTES = 10  # banner when the newest segment is older than this
HEALTH_LEVEL_DROP_DB = 6.0  # banner when mean level is this far below the 30-day median
HEALTH_LOW_CELL = 0.5  # UI marks days/hours with less audio than this fraction


def _hour_dt(hour: str) -> datetime:
    return datetime.fromisoformat(hour)


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _fmt_age(minutes: float) -> str:
    if minutes < 90:
        return f"{round(minutes)} minutes"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def build_dash_health(conn, now: datetime | None = None) -> dict:
    """Capture-health summary for `/dash`: last 24 h, last 30 days, per-hour
    audio for every recorded day (so the UI can mark deaf days/hours), and the
    banner text. Reads `capture_health` + `segments` only; never writes.

    Hours before the first `capture_health` row are *unknown*, not deaf, so
    coverage windows start at the first row.
    """
    now = now or _now()
    rows = conn.execute(
        "SELECT hour, audio_seconds, mean_dbfs FROM capture_health ORDER BY hour"
    ).fetchall()
    newest = conn.execute("SELECT MAX(started_at) AS t FROM segments").fetchone()["t"]
    out: dict = {
        "generated_at": now.isoformat(timespec="seconds"),
        "now": now.isoformat(timespec="seconds"),
        "first_hour": None,
        "newest_segment_at": newest,
        "newest_segment_age_minutes": None,
        "last_24h": None,
        "last_30d": None,
        "hourly_seconds": {},
        "alerts": [],
        "thresholds": {"low_cell": HEALTH_LOW_CELL},
    }
    if newest:
        try:
            out["newest_segment_age_minutes"] = round(
                max(0.0, (now - datetime.fromisoformat(newest)).total_seconds() / 60), 1
            )
        except ValueError:
            pass
    if not rows:
        return out

    first = _hour_dt(rows[0]["hour"])
    out["first_hour"] = rows[0]["hour"]
    hourly: dict[str, list[float]] = {}
    for r in rows:
        dt = _hour_dt(r["hour"])
        day = hourly.setdefault(dt.date().isoformat(), [0.0] * 24)
        day[dt.hour] = round(float(r["audio_seconds"] or 0.0), 1)
    out["hourly_seconds"] = hourly

    def window(span: timedelta) -> dict:
        cutoff = now - span
        # whole hours strictly inside the window, plus the current partial hour
        start = cutoff.replace(minute=0, second=0, microsecond=0)
        if start < cutoff:
            start += timedelta(hours=1)
        start = max(start, first)
        expected = max(0.0, (now - start).total_seconds())
        sel = [r for r in rows if _hour_dt(r["hour"]) >= start]
        audio = sum(float(r["audio_seconds"] or 0.0) for r in sel)
        audio = min(audio, expected) if expected else 0.0
        lv = [(float(r["audio_seconds"]), float(r["mean_dbfs"])) for r in sel
              if r["mean_dbfs"] is not None and (r["audio_seconds"] or 0) > 0]
        wsum = sum(w for w, _ in lv)
        return {
            "expected_hours": round(expected / 3600, 2),
            "recorded_hours": round(audio / 3600, 2),
            "coverage": round(audio / expected, 4) if expected else None,
            "mean_dbfs": round(sum(w * d for w, d in lv) / wsum, 1) if wsum else None,
            "_levels": [float(r["mean_dbfs"]) for r in sel
                        if r["mean_dbfs"] is not None and (r["audio_seconds"] or 0) >= 60],
        }

    w24, w30 = window(timedelta(hours=24)), window(timedelta(days=30))
    med = _median(w30.pop("_levels"))
    w24.pop("_levels")
    w30["median_dbfs"] = round(med, 1) if med is not None else None
    out["last_24h"], out["last_30d"] = w24, w30

    alerts: list[str] = []
    if w24["coverage"] is not None and w24["coverage"] < HEALTH_MIN_COVERAGE:
        alerts.append(
            f"Mic captured {w24['recorded_hours']:.1f} of the last "
            f"{w24['expected_hours']:.0f} hours"
            if w24["expected_hours"] >= 23.5
            else f"Mic captured {w24['recorded_hours']:.1f} of the last "
            f"{w24['expected_hours']:.1f} hours"
        )
    age = out["newest_segment_age_minutes"]
    if age is not None and age > HEALTH_STALE_MINUTES:
        alerts.append(f"No new audio for {_fmt_age(age)}")
    if (
        w24["mean_dbfs"] is not None
        and med is not None
        and med - w24["mean_dbfs"] >= HEALTH_LEVEL_DROP_DB
    ):
        alerts.append(
            f"Mic level is {med - w24['mean_dbfs']:.1f} dB below its 30-day median "
            f"({w24['mean_dbfs']:.0f} vs {med:.0f} dBFS)"
        )
    out["alerts"] = alerts
    return out


@app.route("/api/dash/health")
def api_dash_health():
    conn = _db()
    try:
        body = build_dash_health(conn)
    finally:
        conn.close()
    resp = Response(json.dumps(body, separators=(",", ":")), mimetype="application/json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------------------
# /api/dash/species/<slug> — species page inside the SPA (replaces /bird/<slug>)
# ---------------------------------------------------------------------------

_SPECIES_CLIPS_PER_PAGE = 30


def build_dash_species(conn, slug: str, page: int = 1) -> dict | None:
    name = _resolve_bird_slug(slug, conn)
    if not name:
        return None
    excluded = tuple(sorted(EXCLUDED_DAYS))
    not_excluded = (
        "AND date(heard_at) NOT IN (%s)" % ",".join("?" * len(excluded)) if excluded else ""
    )
    stats = conn.execute(
        f"""
        SELECT scientific_name AS sci, COUNT(*) AS total, MAX(confidence) AS peak,
               MIN(heard_at) AS first, MAX(heard_at) AS last,
               COUNT(DISTINCT date(heard_at)) AS days
        FROM detections WHERE common_name = ? {not_excluded}
        """,
        (name, *excluded),
    ).fetchone()
    if not stats or not stats["total"]:
        return None
    hourly = [0] * 24
    for r in conn.execute(
        f"""
        SELECT CAST(strftime('%H', heard_at) AS INTEGER) AS h, COUNT(*) AS n
        FROM detections WHERE common_name = ? {not_excluded} GROUP BY h
        """,
        (name, *excluded),
    ):
        hourly[r["h"]] = r["n"]
    daily = {
        r["d"]: r["n"]
        for r in conn.execute(
            f"""
            SELECT date(heard_at) AS d, COUNT(*) AS n
            FROM detections WHERE common_name = ? {not_excluded} GROUP BY d ORDER BY d
            """,
            (name, *excluded),
        )
    }

    # Clips that still have audio, best first. Rank (position in this order) feeds
    # the retention explanation: the best N per species are kept indefinitely.
    page = max(1, page)
    policy = retention.Policy.from_config(_CFG)
    now = _now()
    offset = (page - 1) * _SPECIES_CLIPS_PER_PAGE
    rows = conn.execute(
        """
        SELECT d.segment_id AS seg, d.start_time AS s, d.end_time AS e,
               d.confidence AS conf, d.heard_at AS heard_at,
               t.clip_path AS clip_path, sg.wav_path AS wav_path, sg.started_at AS started_at
        FROM detections d
        LEFT JOIN tracks t ON t.id = d.track_id
        JOIN segments sg ON sg.id = d.segment_id
        WHERE d.common_name = ? AND (t.clip_path IS NOT NULL OR sg.wav_path IS NOT NULL)
        ORDER BY d.confidence DESC, d.id DESC
        LIMIT ? OFFSET ?
        """,
        (name, _SPECIES_CLIPS_PER_PAGE + 1, offset),
    ).fetchall()
    has_more = len(rows) > _SPECIES_CLIPS_PER_PAGE
    clips = []
    for i, r in enumerate(rows[:_SPECIES_CLIPS_PER_PAGE]):
        playable = (r["clip_path"] and os.path.exists(r["clip_path"])) or (
            r["wav_path"] and os.path.exists(r["wav_path"])
        )
        if not playable:
            continue
        clips.append(
            {
                "seg": r["seg"],
                "s": round(r["s"], 2),
                "e": round(r["e"], 2),
                "conf": round(r["conf"], 3),
                "heard_at": r["heard_at"],
                "keep": _keep_dict(r["conf"], r["started_at"], offset + i, policy, now),
            }
        )
    slugs = _slug_map()
    canonical = slugs.get(name) or _common_to_slug(name)
    return {
        "name": name,
        "sci": stats["sci"],
        "slug": canonical,
        "art": (_SPRITES_DIR / f"{canonical}.png").is_file(),
        "pixel": (_PIXEL_SPRITES_DIR / f"{canonical}.png").is_file(),
        "status": _yard_status(name),
        "total": stats["total"],
        "peak": round(stats["peak"], 3),
        "first": stats["first"],
        "last": stats["last"],
        "days": stats["days"],
        "hourly": hourly,
        "daily": daily,
        "info": _bird_info().get(canonical),
        "clips": clips,
        "page": page,
        "has_more": has_more,
    }


@app.route("/api/dash/species/<slug>")
def api_dash_species(slug: str):
    conn = _db()
    try:
        body = build_dash_species(conn, slug, request.args.get("page", 1, type=int))
    finally:
        conn.close()
    if body is None:
        abort(404)
    resp = Response(json.dumps(body, separators=(",", ":")), mimetype="application/json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp
