"""Local SQLite storage for the bird-id monitor.

Holds running results from continuous monitoring. We store raw per-window
detections (one row each) and aggregate at query time — you can always roll up
stored detail, but you can't recover detail you threw away.

Timestamps are local-time ISO 8601 (the program runs on the user's machine and
"heard at 6:42am" is the natural reading). `heard_at` on a detection is the
absolute wall-clock time of that 3s window = segment start + window offset.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_DB = "birdid.db"

# Orphans newer than this are left alone — the monitor may still be writing them.
ORPHAN_MIN_AGE_SEC = 600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id            INTEGER PRIMARY KEY,
    device_uid    TEXT NOT NULL UNIQUE,  -- stable hardware id the sensor sends
    name          TEXT,                  -- friendly label ("Oak feeder")
    location      TEXT,
    api_key_hash  TEXT,                  -- ingest auth (hash, never plaintext)
    registered_at TEXT NOT NULL,
    last_seen_at  TEXT,
    last_ip       TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id             INTEGER PRIMARY KEY,
    started_at     TEXT NOT NULL,
    ended_at       TEXT NOT NULL,
    duration       REAL NOT NULL,
    wav_path       TEXT,                 -- NULL if the segment audio was discarded
    mean_dbfs      REAL,
    max_dbfs       REAL,
    device_id      INTEGER REFERENCES devices(id),  -- NULL = local mic (monitor)
    num_detections INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS detections (
    id              INTEGER PRIMARY KEY,
    segment_id      INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    common_name     TEXT NOT NULL,
    scientific_name TEXT NOT NULL,
    confidence      REAL NOT NULL,
    start_time      REAL NOT NULL,        -- window offset within the segment (s)
    end_time        REAL NOT NULL,
    heard_at        TEXT NOT NULL,        -- absolute local ISO time of the window
    track_id        INTEGER REFERENCES tracks(id)
);

CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY,
    segment_id  INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    start_time  REAL NOT NULL,
    end_time    REAL NOT NULL,
    duration    REAL NOT NULL,
    clip_path   TEXT,                 -- NULL if clip audio discarded/expired
    created_at  TEXT NOT NULL,
    UNIQUE(segment_id, start_time, end_time)
);

CREATE INDEX IF NOT EXISTS idx_tracks_segment ON tracks(segment_id);
CREATE INDEX IF NOT EXISTS idx_detections_species ON detections(common_name);
CREATE INDEX IF NOT EXISTS idx_detections_heard_at ON detections(heard_at);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Upgrade older databases: track_id column, tracks rows, backfill links,
    and the segments.device_id column for multi-sensor attribution."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(detections)").fetchall()}
    if "track_id" not in cols:
        conn.execute("ALTER TABLE detections ADD COLUMN track_id INTEGER REFERENCES tracks(id)")

    seg_cols = {row[1] for row in conn.execute("PRAGMA table_info(segments)").fetchall()}
    if "device_id" not in seg_cols:
        # Existing rows stay NULL = local mic. The devices table itself is created
        # idempotently by executescript(_SCHEMA) in connect().
        conn.execute("ALTER TABLE segments ADD COLUMN device_id INTEGER REFERENCES devices(id)")
    # Index lives here (not in _SCHEMA): on an old DB the column doesn't exist until
    # the ALTER above, and executescript(_SCHEMA) runs before this migration.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_segments_device ON segments(device_id)")

    missing = conn.execute(
        """
        SELECT DISTINCT d.segment_id, d.start_time, d.end_time
        FROM detections d
        WHERE NOT EXISTS (
            SELECT 1 FROM tracks t
            WHERE t.segment_id = d.segment_id
              AND t.start_time = d.start_time
              AND t.end_time = d.end_time
        )
        """
    ).fetchall()
    for row in missing:
        seg = conn.execute(
            "SELECT started_at FROM segments WHERE id = ?", (row["segment_id"],)
        ).fetchone()
        created_at = (
            seg["started_at"]
            if seg
            else datetime.now().isoformat(timespec="seconds")
        )
        duration = row["end_time"] - row["start_time"]
        conn.execute(
            "INSERT INTO tracks (segment_id, start_time, end_time, duration, "
            "clip_path, created_at) VALUES (?, ?, ?, ?, NULL, ?)",
            (
                row["segment_id"],
                row["start_time"],
                row["end_time"],
                duration,
                created_at,
            ),
        )

    conn.execute(
        """
        UPDATE detections
        SET track_id = (
            SELECT t.id FROM tracks t
            WHERE t.segment_id = detections.segment_id
              AND t.start_time = detections.start_time
              AND t.end_time = detections.end_time
        )
        WHERE track_id IS NULL
        """
    )
    conn.commit()


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    """Open (creating if needed) the database with sane settings and schema."""
    conn = sqlite3.connect(str(Path(db_path).expanduser()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # let `stats` read while monitor writes
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def record_segment(
    conn: sqlite3.Connection,
    *,
    started_at: datetime,
    ended_at: datetime,
    duration: float,
    detections: Iterable,
    wav_path: Optional[str] = None,
    mean_dbfs: Optional[float] = None,
    max_dbfs: Optional[float] = None,
    clip_paths: Optional[dict[tuple[float, float], str]] = None,
    device_id: Optional[int] = None,
) -> int:
    """Insert a segment and its detections in one transaction. Returns segment id.

    `detections` are identifier.Detection objects. Each detection's `heard_at` is
    computed as started_at + its window start offset.

    Optional `clip_paths` maps (start_time, end_time) window keys to on-disk clip
    wav paths for the matching `tracks` row.

    Optional `device_id` attributes the segment to a registered sensor (NULL = the
    local mic / monitor).
    """
    detections = list(detections)
    paths = clip_paths or {}
    created_at = started_at.isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO segments (started_at, ended_at, duration, wav_path, "
        "mean_dbfs, max_dbfs, device_id, num_detections) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            created_at,
            ended_at.isoformat(timespec="seconds"),
            duration,
            wav_path,
            mean_dbfs,
            max_dbfs,
            device_id,
            len(detections),
        ),
    )
    segment_id = cur.lastrowid

    windows = {(d.start_time, d.end_time) for d in detections}
    window_to_track_id: dict[tuple[float, float], int] = {}
    for start, end in sorted(windows):
        tcur = conn.execute(
            "INSERT INTO tracks (segment_id, start_time, end_time, duration, "
            "clip_path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                segment_id,
                start,
                end,
                end - start,
                paths.get((start, end)),
                created_at,
            ),
        )
        window_to_track_id[(start, end)] = tcur.lastrowid

    conn.executemany(
        "INSERT INTO detections (segment_id, common_name, scientific_name, "
        "confidence, start_time, end_time, heard_at, track_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                segment_id,
                d.common_name,
                d.scientific_name,
                d.confidence,
                d.start_time,
                d.end_time,
                (started_at + timedelta(seconds=d.start_time)).isoformat(timespec="seconds"),
                window_to_track_id[(d.start_time, d.end_time)],
            )
            for d in detections
        ],
    )
    conn.commit()
    return segment_id


# --- Devices (multi-sensor registry + attribution) ------------------------

def register_device(
    conn: sqlite3.Connection,
    device_uid: str,
    *,
    name: Optional[str] = None,
    location: Optional[str] = None,
    api_key_hash: Optional[str] = None,
) -> int:
    """Upsert a sensor by its stable `device_uid`. Returns the device id.

    Re-registering an existing uid updates the provided fields (name/location/
    api_key_hash) and leaves the rest untouched. Idempotent.
    """
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO devices (device_uid, name, location, api_key_hash, registered_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(device_uid) DO UPDATE SET
            name         = COALESCE(excluded.name, devices.name),
            location     = COALESCE(excluded.location, devices.location),
            api_key_hash = COALESCE(excluded.api_key_hash, devices.api_key_hash)
        """,
        (device_uid, name, location, api_key_hash, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM devices WHERE device_uid = ?", (device_uid,)
    ).fetchone()
    return row["id"]


def get_device_by_uid(conn: sqlite3.Connection, device_uid: str) -> Optional[sqlite3.Row]:
    """Look up a registered device by its hardware uid (None if unknown)."""
    return conn.execute(
        "SELECT * FROM devices WHERE device_uid = ?", (device_uid,)
    ).fetchone()


def touch_device(
    conn: sqlite3.Connection, device_id: int, *, ip: Optional[str] = None
) -> None:
    """Record that a device just checked in (updates last_seen_at / last_ip)."""
    conn.execute(
        "UPDATE devices SET last_seen_at = ?, last_ip = COALESCE(?, last_ip) WHERE id = ?",
        (datetime.now().isoformat(timespec="seconds"), ip, device_id),
    )
    conn.commit()


def list_devices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All registered devices with detection/segment counts, newest-seen first."""
    return conn.execute(
        """
        SELECT dev.*,
               COUNT(DISTINCT s.id) AS segments,
               COUNT(d.id)          AS detections,
               COUNT(DISTINCT d.common_name) AS species
        FROM devices dev
        LEFT JOIN segments s   ON s.device_id = dev.id
        LEFT JOIN detections d ON d.segment_id = s.id
        GROUP BY dev.id
        ORDER BY dev.last_seen_at DESC NULLS LAST, dev.registered_at DESC
        """
    ).fetchall()


def device_species(
    conn: sqlite3.Connection, device_id: int, *, since: Optional[str] = None
) -> list[sqlite3.Row]:
    """Per-species rollup for one device, by peak confidence (mirrors species_summary)."""
    where = "WHERE s.device_id = ?"
    params: tuple = (device_id,)
    if since:
        where += " AND d.heard_at >= ?"
        params = (device_id, since)
    return conn.execute(
        f"""
        SELECT d.common_name, d.scientific_name,
               COUNT(*)          AS windows,
               MAX(d.confidence) AS peak_conf,
               MIN(d.heard_at)   AS first_heard,
               MAX(d.heard_at)   AS last_heard
        FROM detections d
        JOIN segments s ON s.id = d.segment_id
        {where}
        GROUP BY d.common_name, d.scientific_name
        ORDER BY peak_conf DESC
        """,
        params,
    ).fetchall()


def get_track(
    conn: sqlite3.Connection, segment_id: int, start: float, end: float
) -> Optional[sqlite3.Row]:
    """Look up the track for an exact detection window within a segment."""
    return conn.execute(
        "SELECT * FROM tracks WHERE segment_id = ? AND start_time = ? AND end_time = ?",
        (segment_id, start, end),
    ).fetchone()


def tracks_for_segment(conn: sqlite3.Connection, segment_id: int) -> list[sqlite3.Row]:
    """All tracks for one segment, ordered by window start."""
    return conn.execute(
        "SELECT * FROM tracks WHERE segment_id = ? ORDER BY start_time",
        (segment_id,),
    ).fetchall()


def tracks_without_clip(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Tracks missing clip_path whose segment still references a wav file."""
    return conn.execute(
        """
        SELECT t.id, t.segment_id, t.start_time, t.end_time,
               s.wav_path, s.started_at
        FROM tracks t
        JOIN segments s ON s.id = t.segment_id
        WHERE t.clip_path IS NULL AND s.wav_path IS NOT NULL
        ORDER BY s.started_at, t.start_time
        """
    ).fetchall()


def set_track_clip_path(
    conn: sqlite3.Connection, track_id: int, clip_path: str
) -> None:
    """Attach an on-disk clip file to a track row."""
    conn.execute(
        "UPDATE tracks SET clip_path = ? WHERE id = ?", (clip_path, track_id)
    )


def species_summary(conn: sqlite3.Connection, *, since: Optional[str] = None) -> list[sqlite3.Row]:
    """Per-species rollup across all stored detections, by peak confidence."""
    where = "WHERE heard_at >= ?" if since else ""
    params = (since,) if since else ()
    return conn.execute(
        f"""
        SELECT common_name, scientific_name,
               COUNT(*)        AS windows,
               MAX(confidence) AS peak_conf,
               MIN(heard_at)   AS first_heard,
               MAX(heard_at)   AS last_heard
        FROM detections
        {where}
        GROUP BY common_name, scientific_name
        ORDER BY peak_conf DESC
        """,
        params,
    ).fetchall()


def day_overview(conn: sqlite3.Connection, day: str) -> sqlite3.Row:
    """Totals for a single day (day = 'YYYY-MM-DD')."""
    return conn.execute(
        """
        SELECT COUNT(*)                    AS detections,
               COUNT(DISTINCT common_name) AS species,
               MIN(heard_at)               AS first_heard,
               MAX(heard_at)               AS last_heard
        FROM detections WHERE date(heard_at) = ?
        """,
        (day,),
    ).fetchone()


def day_species(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    """Per-species rollup for one day, ordered by peak confidence."""
    return conn.execute(
        """
        SELECT common_name, scientific_name,
               COUNT(*)        AS windows,
               MAX(confidence) AS peak_conf,
               MIN(heard_at)   AS first_heard
        FROM detections WHERE date(heard_at) = ?
        GROUP BY common_name, scientific_name
        ORDER BY peak_conf DESC
        """,
        (day,),
    ).fetchall()


def day_hourly(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    """Detection counts per hour for one day (for finding the peak-activity hour)."""
    return conn.execute(
        """
        SELECT strftime('%H', heard_at) AS hour, COUNT(*) AS n
        FROM detections WHERE date(heard_at) = ?
        GROUP BY hour ORDER BY hour
        """,
        (day,),
    ).fetchall()


def new_species_on(conn: sqlite3.Connection, day: str) -> list[str]:
    """Species whose first-ever detection (any day) falls on `day`."""
    rows = conn.execute(
        """
        SELECT common_name FROM detections
        GROUP BY common_name HAVING MIN(date(heard_at)) = ?
        ORDER BY common_name
        """,
        (day,),
    ).fetchall()
    return [r["common_name"] for r in rows]


_HAS_AUDIO = (
    "CASE WHEN s.wav_path IS NOT NULL OR t.clip_path IS NOT NULL "
    "THEN 1 ELSE 0 END"
)

_DEX_SELECT = f"""
        SELECT common_name, scientific_name, confidence AS peak_conf,
               segment_id, start_time, end_time, has_audio,
               windows, first_heard, last_heard
        FROM (
            SELECT d.common_name, d.scientific_name, d.confidence,
                   d.segment_id, d.start_time, d.end_time,
                   {_HAS_AUDIO} AS has_audio,
                   ROW_NUMBER() OVER (PARTITION BY d.common_name ORDER BY d.confidence DESC) AS rn,
                   COUNT(*)      OVER (PARTITION BY d.common_name) AS windows,
                   MIN(d.heard_at) OVER (PARTITION BY d.common_name) AS first_heard,
                   MAX(d.heard_at) OVER (PARTITION BY d.common_name) AS last_heard
            FROM detections d
            JOIN segments s ON s.id = d.segment_id
            LEFT JOIN tracks t ON t.id = d.track_id
            {{where}}
        )
        WHERE rn = 1
        ORDER BY peak_conf DESC
        """


def species_dex(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """One row per species = its highest-confidence detection (the 'dex entry'),
    plus how many windows it's been heard in and its first/last times.

    The peak detection carries segment_id + start/end so the UI can show that
    exact clip's spectrogram as the species' 'sprite' and play it.
    """
    return conn.execute(_DEX_SELECT.format(where="")).fetchall()


def species_dex_day(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    """Same shape as species_dex, but only detections on `day` (YYYY-MM-DD)."""
    return conn.execute(
        _DEX_SELECT.format(where="WHERE date(d.heard_at) = ?"),
        (day,),
    ).fetchall()


def get_segment(conn: sqlite3.Connection, segment_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()


def totals(conn: sqlite3.Connection) -> sqlite3.Row:
    """Overall counts for a quick status line."""
    return conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM segments)                       AS segments,
               (SELECT COUNT(*) FROM detections)                     AS detections,
               (SELECT COUNT(DISTINCT common_name) FROM detections)  AS species,
               (SELECT MIN(started_at) FROM segments)                AS first_segment,
               (SELECT MAX(ended_at) FROM segments)                  AS last_segment
        """
    ).fetchone()


def _species_day_clause(day: str | None, show_all: bool) -> tuple[str, tuple]:
    """SQL fragment + params for optional day filter on detections."""
    if show_all or not day:
        return "", ()
    return " AND date(d.heard_at) = ?", (day,)


def species_exists(conn: sqlite3.Connection, common_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM detections WHERE common_name = ? LIMIT 1", (common_name,)
    ).fetchone()
    return row is not None


def species_meta(conn: sqlite3.Connection, common_name: str) -> Optional[sqlite3.Row]:
    """Scientific name and first-ever detection for a species."""
    return conn.execute(
        """
        SELECT scientific_name,
               MIN(heard_at) AS discovered_at,
               COUNT(*)      AS all_time_windows
        FROM detections WHERE common_name = ?
        GROUP BY scientific_name
        """,
        (common_name,),
    ).fetchone()


def species_stats(
    conn: sqlite3.Connection,
    common_name: str,
    *,
    day: str | None = None,
    show_all: bool = True,
) -> Optional[sqlite3.Row]:
    """Aggregate stats for one species, optionally scoped to a single day."""
    extra, params = _species_day_clause(day, show_all)
    return conn.execute(
        f"""
        SELECT COUNT(*)                    AS windows,
               MAX(confidence)             AS peak_conf,
               AVG(confidence)             AS avg_conf,
               MIN(confidence)             AS min_conf,
               MIN(heard_at)               AS first_heard,
               MAX(heard_at)               AS last_heard,
               COUNT(DISTINCT segment_id)  AS segments
        FROM detections d
        WHERE d.common_name = ?{extra}
        """,
        (common_name, *params),
    ).fetchone()


def species_detections(
    conn: sqlite3.Connection,
    common_name: str,
    *,
    day: str | None = None,
    show_all: bool = True,
    limit: int = 200,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """All detection windows for a species, highest confidence first."""
    extra, params = _species_day_clause(day, show_all)
    return conn.execute(
        f"""
        SELECT d.id, d.confidence, d.heard_at, d.start_time, d.end_time,
               d.segment_id, {_HAS_AUDIO} AS has_audio
        FROM detections d
        JOIN segments s ON s.id = d.segment_id
        LEFT JOIN tracks t ON t.id = d.track_id
        WHERE d.common_name = ?{extra}
        ORDER BY d.confidence DESC, d.heard_at DESC
        LIMIT ? OFFSET ?
        """,
        (common_name, *params, limit, offset),
    ).fetchall()


_RECENT_FEED_SELECT = f"""
        SELECT common_name, scientific_name,
               segment_id, confidence AS peak_conf,
               heard_at, start_time, end_time, has_audio,
               device_id, device_name, device_uid
        FROM (
            SELECT d.common_name, d.scientific_name,
                   d.segment_id, d.confidence, d.heard_at,
                   d.start_time, d.end_time,
                   {_HAS_AUDIO} AS has_audio,
                   s.device_id AS device_id,
                   dev.name AS device_name,
                   dev.device_uid AS device_uid,
                   ROW_NUMBER() OVER (
                       PARTITION BY d.segment_id, d.common_name
                       ORDER BY d.confidence DESC, d.heard_at DESC
                   ) AS rn
            FROM detections d
            JOIN segments s ON s.id = d.segment_id
            LEFT JOIN devices dev ON dev.id = s.device_id
            LEFT JOIN tracks t ON t.id = d.track_id
            WHERE d.heard_at >= ?
              AND d.confidence >= ?
              AND (s.wav_path IS NOT NULL OR t.clip_path IS NOT NULL)
              {{since_clause}}
        )
        WHERE rn = 1
        ORDER BY heard_at DESC
        LIMIT ?
        """


def recent_feed(
    conn: sqlite3.Connection,
    *,
    hours: int = 24,
    limit: int = 100,
    since: Optional[str] = None,
    min_conf: float = 0.3,
    now: Optional[datetime] = None,
) -> list[sqlite3.Row]:
    """Chronological feed: one row per (segment, species) with peak confidence.

    Only includes detections within the last `hours` with playable audio.
    Optional `since` returns rows newer than that ISO timestamp (for polling).
    """
    ref = now or datetime.now()
    cutoff = (ref - timedelta(hours=hours)).isoformat(timespec="seconds")
    since_clause = "AND d.heard_at > ?" if since else ""
    params: list = [cutoff, min_conf]
    if since:
        params.append(since)
    params.append(limit)
    return conn.execute(
        _RECENT_FEED_SELECT.format(since_clause=since_clause),
        params,
    ).fetchall()


def species_detection_count(
    conn: sqlite3.Connection,
    common_name: str,
    *,
    day: str | None = None,
    show_all: bool = True,
) -> int:
    extra, params = _species_day_clause(day, show_all)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM detections d WHERE d.common_name = ?{extra}",
        (common_name, *params),
    ).fetchone()
    return row["n"]


def species_heard_times(
    conn: sqlite3.Connection,
    common_name: str,
    *,
    day: str | None = None,
    show_all: bool = True,
) -> list[str]:
    """All heard_at timestamps for streak / span calculations."""
    extra, params = _species_day_clause(day, show_all)
    rows = conn.execute(
        f"SELECT heard_at FROM detections d WHERE d.common_name = ?{extra} ORDER BY heard_at",
        (common_name, *params),
    ).fetchall()
    return [r["heard_at"] for r in rows]


def species_hourly(
    conn: sqlite3.Connection,
    common_name: str,
    *,
    day: str | None = None,
    show_all: bool = True,
) -> list[sqlite3.Row]:
    """Detection counts per hour (0–23). Single-day scope or all-time aggregate."""
    if show_all or not day:
        return conn.execute(
            """
            SELECT strftime('%H', heard_at) AS hour, COUNT(*) AS n
            FROM detections WHERE common_name = ?
            GROUP BY hour ORDER BY hour
            """,
            (common_name,),
        ).fetchall()
    return conn.execute(
        """
        SELECT strftime('%H', heard_at) AS hour, COUNT(*) AS n
        FROM detections WHERE common_name = ? AND date(heard_at) = ?
        GROUP BY hour ORDER BY hour
        """,
        (common_name, day),
    ).fetchall()


def species_daily(
    conn: sqlite3.Connection, common_name: str
) -> list[sqlite3.Row]:
    """Per-day detection counts for one species (all-time drill-down)."""
    return conn.execute(
        """
        SELECT date(heard_at) AS day, COUNT(*) AS n,
               MAX(confidence) AS peak_conf
        FROM detections WHERE common_name = ?
        GROUP BY day ORDER BY day DESC
        """,
        (common_name,),
    ).fetchall()


def timeline_occurrences(
    conn: sqlite3.Connection, day: str, *, min_conf: float = 0.0
) -> list[sqlite3.Row]:
    """All detections on one day, ordered by heard_at (for swimlane timeline)."""
    return conn.execute(
        """
        SELECT common_name, heard_at, confidence
        FROM detections WHERE date(heard_at) = ? AND confidence >= ?
        ORDER BY heard_at
        """,
        (day, min_conf),
    ).fetchall()


def timeline_by_day(
    conn: sqlite3.Connection, *, limit: int = 120, min_conf: float = 0.0
) -> list[sqlite3.Row]:
    """Per-day detection totals for the all-time timeline view."""
    return conn.execute(
        """
        SELECT date(heard_at) AS day,
               COUNT(*) AS n,
               COUNT(DISTINCT common_name) AS species
        FROM detections
        WHERE confidence >= ?
        GROUP BY day ORDER BY day DESC
        LIMIT ?
        """,
        (min_conf, limit),
    ).fetchall()


def species_confidence_buckets(
    conn: sqlite3.Connection,
    common_name: str,
    *,
    day: str | None = None,
    show_all: bool = True,
) -> list[sqlite3.Row]:
    """Histogram of confidence scores in fixed buckets."""
    extra, params = _species_day_clause(day, show_all)
    return conn.execute(
        f"""
        SELECT
            CASE
                WHEN confidence >= 0.9 THEN '0.90–1.00'
                WHEN confidence >= 0.7 THEN '0.70–0.89'
                WHEN confidence >= 0.5 THEN '0.50–0.69'
                ELSE '0.30–0.49'
            END AS bucket,
            CASE
                WHEN confidence >= 0.9 THEN 4
                WHEN confidence >= 0.7 THEN 3
                WHEN confidence >= 0.5 THEN 2
                ELSE 1
            END AS ord,
            COUNT(*) AS n
        FROM detections d
        WHERE d.common_name = ?{extra}
        GROUP BY bucket, ord
        ORDER BY ord
        """,
        (common_name, *params),
    ).fetchall()


def segments_for_scope(
    conn: sqlite3.Connection, *, day: str | None = None
) -> list[sqlite3.Row]:
    """Recording segments with live detection counts, ordered by start time."""
    if day:
        return conn.execute(
            """
            SELECT s.id, s.started_at, s.ended_at, s.duration,
                   COUNT(d.id) AS detections,
                   COUNT(DISTINCT d.common_name) AS species
            FROM segments s
            JOIN detections d ON d.segment_id = s.id
            WHERE date(d.heard_at) = ?
            GROUP BY s.id
            ORDER BY s.started_at
            """,
            (day,),
        ).fetchall()
    return conn.execute(
        """
        SELECT s.id, s.started_at, s.ended_at, s.duration,
               COUNT(d.id) AS detections,
               COUNT(DISTINCT d.common_name) AS species
        FROM segments s
        JOIN detections d ON d.segment_id = s.id
        GROUP BY s.id
        ORDER BY s.started_at
        """
    ).fetchall()


def report_species(
    conn: sqlite3.Connection, *, day: str | None = None
) -> list[sqlite3.Row]:
    """Per-species rollup for the data report, sorted by detection count."""
    if day:
        return conn.execute(
            """
            SELECT common_name, scientific_name,
                   COUNT(*)        AS windows,
                   MAX(confidence) AS peak_conf
            FROM detections WHERE date(heard_at) = ?
            GROUP BY common_name, scientific_name
            ORDER BY windows DESC
            """,
            (day,),
        ).fetchall()
    return conn.execute(
        """
        SELECT common_name, scientific_name,
               COUNT(*)        AS windows,
               MAX(confidence) AS peak_conf
        FROM detections
        GROUP BY common_name, scientific_name
        ORDER BY windows DESC
        """
    ).fetchall()


def report_confidences(
    conn: sqlite3.Connection, *, day: str | None = None
) -> list[sqlite3.Row]:
    """All confidence scores grouped by species (for confidence strips)."""
    if day:
        return conn.execute(
            """
            SELECT common_name, confidence
            FROM detections WHERE date(heard_at) = ?
            ORDER BY common_name, confidence
            """,
            (day,),
        ).fetchall()
    return conn.execute(
        """
        SELECT common_name, confidence
        FROM detections
        ORDER BY common_name, confidence
        """
    ).fetchall()


def report_species_segments(
    conn: sqlite3.Connection, *, day: str | None = None
) -> list[sqlite3.Row]:
    """Distinct segment ids per species (for window-presence dots)."""
    if day:
        return conn.execute(
            """
            SELECT common_name, segment_id
            FROM detections WHERE date(heard_at) = ?
            GROUP BY common_name, segment_id
            ORDER BY common_name, segment_id
            """,
            (day,),
        ).fetchall()
    return conn.execute(
        """
        SELECT common_name, segment_id
        FROM detections
        GROUP BY common_name, segment_id
        ORDER BY common_name, segment_id
        """
    ).fetchall()


def minute_bins(conn: sqlite3.Connection, segment_id: int) -> list[sqlite3.Row]:
    """Detection count per elapsed minute within one segment."""
    return conn.execute(
        """
        SELECT CAST(
                   (julianday(d.heard_at) - julianday(s.started_at)) * 86400 / 60
               AS INTEGER) AS minute,
               COUNT(*) AS n
        FROM detections d
        JOIN segments s ON s.id = d.segment_id
        WHERE d.segment_id = ?
        GROUP BY minute
        ORDER BY minute
        """,
        (segment_id,),
    ).fetchall()


def daily_rates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per calendar day: detection count and total recorded minutes."""
    return conn.execute(
        """
        SELECT date(d.heard_at) AS day,
               COUNT(d.id)       AS detections,
               COALESCE((
                   SELECT SUM(s2.duration)
                   FROM segments s2
                   WHERE date(s2.started_at) = date(d.heard_at)
               ), 0)             AS audio_sec
        FROM detections d
        GROUP BY day
        ORDER BY day
        """
    ).fetchall()


def hourly_aggregate(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Detection counts per hour (0–23) across all stored data."""
    return conn.execute(
        """
        SELECT strftime('%H', heard_at) AS hour, COUNT(*) AS n
        FROM detections
        GROUP BY hour ORDER BY hour
        """
    ).fetchall()


def protected_dashboard_detection_ids(
    conn: sqlite3.Connection, *, live_hours: int = 24
) -> set[int]:
    """Detection rows whose audio the dashboard can surface."""
    ids: set[int] = set()
    for row in conn.execute(
        """
        SELECT id FROM (
          SELECT d.id,
                 ROW_NUMBER() OVER (
                   PARTITION BY d.common_name ORDER BY d.confidence DESC
                 ) AS rn
          FROM detections d
        ) WHERE rn = 1
        """
    ):
        ids.add(row["id"])

    for (day,) in conn.execute("SELECT DISTINCT date(heard_at) FROM detections"):
        for row in conn.execute(
            """
            SELECT id FROM (
              SELECT d.id,
                     ROW_NUMBER() OVER (
                       PARTITION BY d.common_name ORDER BY d.confidence DESC
                     ) AS rn
              FROM detections d
              WHERE date(d.heard_at) = ?
            ) WHERE rn = 1
            """,
            (day,),
        ):
            ids.add(row["id"])

    for row in conn.execute(
        """
        SELECT id FROM (
          SELECT d.id,
                 ROW_NUMBER() OVER (
                   PARTITION BY d.common_name
                   ORDER BY d.confidence DESC, d.heard_at DESC
                 ) AS rn
          FROM detections d
        ) WHERE rn <= 200
        """
    ):
        ids.add(row["id"])

    cutoff = (datetime.now() - timedelta(hours=live_hours)).isoformat(
        timespec="seconds"
    )
    for row in conn.execute(
        "SELECT id FROM detections WHERE heard_at >= ?",
        (cutoff,),
    ):
        ids.add(row["id"])
    return ids


def _protected_dashboard_tracks(
    conn: sqlite3.Connection, protected_detections: set[int]
) -> set[int]:
    tracks: set[int] = set()
    for row in conn.execute(
        "SELECT id, track_id FROM detections WHERE track_id IS NOT NULL"
    ):
        if row["id"] in protected_detections:
            tracks.add(row["track_id"])
    return tracks


def _protected_dashboard_segments(
    conn: sqlite3.Connection, protected_detections: set[int]
) -> set[int]:
    segments: set[int] = set()
    for row in conn.execute("SELECT segment_id, id FROM detections"):
        if row["id"] in protected_detections:
            segments.add(row["segment_id"])
    return segments


def clear_segment_wav(conn: sqlite3.Connection, segment_id: int) -> bool:
    """Remove a segment's on-disk wav and clear wav_path. Returns True if dropped."""
    row = conn.execute(
        "SELECT wav_path FROM segments WHERE id = ?", (segment_id,)
    ).fetchone()
    if not row or not row["wav_path"]:
        return False
    path = Path(row["wav_path"])
    if path.is_file():
        path.unlink()
    conn.execute("UPDATE segments SET wav_path = NULL WHERE id = ?", (segment_id,))
    conn.commit()
    return True


def cleanup_dashboard_audio(
    conn: sqlite3.Connection,
    recordings_dir: str | Path,
    *,
    dry_run: bool = False,
    live_hours: int = 24,
) -> dict[str, int]:
    """Drop segment wavs and clips not needed by the dashboard.

    Also purges orphan files under recordings_dir. Returns byte/count stats.
    """
    recordings_dir = Path(recordings_dir).expanduser()
    protected_dets = protected_dashboard_detection_ids(conn, live_hours=live_hours)
    protected_track_ids = _protected_dashboard_tracks(conn, protected_dets)
    protected_segment_ids = _protected_dashboard_segments(conn, protected_dets)

    seg_rows = conn.execute(
        "SELECT id, wav_path FROM segments WHERE wav_path IS NOT NULL"
    ).fetchall()
    seg_drop = [row for row in seg_rows if row["id"] not in protected_segment_ids]

    track_rows = conn.execute(
        "SELECT id, clip_path FROM tracks WHERE clip_path IS NOT NULL"
    ).fetchall()
    clip_drop = [row for row in track_rows if row["id"] not in protected_track_ids]

    seg_freed = sum(
        Path(row["wav_path"]).stat().st_size
        for row in seg_drop
        if row["wav_path"] and Path(row["wav_path"]).is_file()
    )
    clip_freed = sum(
        Path(row["clip_path"]).stat().st_size
        for row in clip_drop
        if row["clip_path"] and Path(row["clip_path"]).is_file()
    )

    stats = {
        "protected_detections": len(protected_dets),
        "protected_segments": len(protected_segment_ids),
        "protected_tracks": len(protected_track_ids),
        "segments_dropped": len(seg_drop),
        "clips_dropped": len(clip_drop),
        "bytes_freed": seg_freed + clip_freed,
    }
    if dry_run:
        return stats

    for row in seg_drop:
        path = Path(row["wav_path"]) if row["wav_path"] else None
        if path and path.is_file():
            path.unlink()
        conn.execute("UPDATE segments SET wav_path = NULL WHERE id = ?", (row["id"],))

    for row in clip_drop:
        path = Path(row["clip_path"]) if row["clip_path"] else None
        if path and path.is_file():
            path.unlink()
        conn.execute("UPDATE tracks SET clip_path = NULL WHERE id = ?", (row["id"],))

    if seg_drop or clip_drop:
        conn.commit()

    orphans, orphan_freed = purge_orphan_recordings(conn, recordings_dir)
    clip_orphans, clip_orphan_freed = purge_orphan_clips(
        conn, recordings_dir / "clips"
    )
    stats["bytes_freed"] += orphan_freed + clip_orphan_freed
    stats["orphan_segments"] = orphans
    stats["orphan_clips"] = clip_orphans
    return stats


def expire_segment_audio(
    conn: sqlite3.Connection,
    *,
    retention_days: int,
    now: Optional[datetime] = None,
) -> tuple[int, int]:
    """Delete kept segment wav files older than `retention_days`.

    Detection rows and segment metadata stay in the DB; only `wav_path` is cleared
    and the file is removed from disk. Returns (segments_expired, bytes_freed).
    No-op when retention_days <= 0.
    """
    if retention_days <= 0:
        return 0, 0

    now = now or datetime.now()
    cutoff = (now - timedelta(days=retention_days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT id, wav_path FROM segments "
        "WHERE wav_path IS NOT NULL AND started_at < ?",
        (cutoff,),
    ).fetchall()

    freed = 0
    for row in rows:
        path = Path(row["wav_path"])
        if path.is_file():
            freed += path.stat().st_size
            path.unlink()
        conn.execute("UPDATE segments SET wav_path = NULL WHERE id = ?", (row["id"],))
    if rows:
        conn.commit()
    return len(rows), freed


def purge_orphan_recordings(
    conn: sqlite3.Connection,
    recordings_dir: str | Path,
    *,
    min_age_sec: float = ORPHAN_MIN_AGE_SEC,
) -> tuple[int, int]:
    """Remove wav files under `recordings_dir` not referenced by any segment.

    Skips files modified within `min_age_sec` so an in-progress monitor capture
    (not yet in the DB) is not unlinked while ffmpeg still has it open.
    Returns (files_removed, bytes_freed).
    """
    import time

    recordings_dir = Path(recordings_dir).expanduser()
    if not recordings_dir.is_dir():
        return 0, 0

    kept = {
        Path(row["wav_path"]).resolve()
        for row in conn.execute(
            "SELECT wav_path FROM segments WHERE wav_path IS NOT NULL"
        ).fetchall()
    }
    now = time.time()
    removed = freed = 0
    for path in recordings_dir.glob("*.wav"):
        if path.resolve() in kept:
            continue
        if now - path.stat().st_mtime < min_age_sec:
            continue
        freed += path.stat().st_size
        path.unlink()
        removed += 1
    return removed, freed


def expire_track_clips(
    conn: sqlite3.Connection,
    *,
    retention_days: int,
    now: Optional[datetime] = None,
) -> tuple[int, int]:
    """Delete per-detection clip wavs older than `retention_days`.

    Track rows stay; only `clip_path` is cleared and the file is removed.
    Returns (tracks_expired, bytes_freed). No-op when retention_days <= 0.
    """
    if retention_days <= 0:
        return 0, 0

    now = now or datetime.now()
    cutoff = (now - timedelta(days=retention_days)).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT t.id, t.clip_path
        FROM tracks t
        JOIN segments s ON s.id = t.segment_id
        WHERE t.clip_path IS NOT NULL AND s.started_at < ?
        """,
        (cutoff,),
    ).fetchall()

    freed = 0
    for row in rows:
        path = Path(row["clip_path"])
        if path.is_file():
            freed += path.stat().st_size
            path.unlink()
        conn.execute("UPDATE tracks SET clip_path = NULL WHERE id = ?", (row["id"],))
    if rows:
        conn.commit()
    return len(rows), freed


def purge_orphan_clips(
    conn: sqlite3.Connection,
    clips_dir: str | Path,
    *,
    min_age_sec: float = ORPHAN_MIN_AGE_SEC,
) -> tuple[int, int]:
    """Remove clip wav files under `clips_dir` not referenced by any track.

    Skips files modified within `min_age_sec`. Returns (files_removed, bytes_freed).
    """
    import time

    clips_dir = Path(clips_dir).expanduser()
    if not clips_dir.is_dir():
        return 0, 0

    kept = {
        Path(row["clip_path"]).resolve()
        for row in conn.execute(
            "SELECT clip_path FROM tracks WHERE clip_path IS NOT NULL"
        ).fetchall()
    }
    now = time.time()
    removed = freed = 0
    for path in clips_dir.glob("*.wav"):
        if path.resolve() in kept:
            continue
        if now - path.stat().st_mtime < min_age_sec:
            continue
        freed += path.stat().st_size
        path.unlink()
        removed += 1
    return removed, freed


def hourly_in_range(
    conn: sqlite3.Connection, days: tuple[str, ...]
) -> list[sqlite3.Row]:
    """Detection counts + audio-seconds per hour 0–23 across the given visible days.

    Audio-seconds is summed per start-hour of each segment (an approximation good
    enough for normalizing the Activity Clock). Hours with no activity are omitted;
    fill 0–23 in the caller.
    """
    if not days:
        return []
    ph = ",".join("?" * len(days))
    return conn.execute(
        f"""
        WITH det AS (
            SELECT CAST(strftime('%H', heard_at) AS INTEGER) AS hour,
                   COUNT(*) AS n
            FROM detections
            WHERE date(heard_at) IN ({ph})
            GROUP BY 1
        ), seg AS (
            SELECT CAST(strftime('%H', started_at) AS INTEGER) AS hour,
                   SUM(duration) AS audio_sec
            FROM segments
            WHERE date(started_at) IN ({ph})
            GROUP BY 1
        )
        SELECT COALESCE(d.hour, s.hour) AS hour,
               COALESCE(d.n, 0) AS n,
               COALESCE(s.audio_sec, 0.0) AS audio_sec
        FROM det d LEFT JOIN seg s ON d.hour = s.hour
        ORDER BY hour
        """,
        days + days,
    ).fetchall()


def species_in_range(
    conn: sqlite3.Connection, days: tuple[str, ...]
) -> list[sqlite3.Row]:
    """Per-species: total, morning (h05–08 inclusive), night (h20–23 + h00), first_in_window.

    Morning window deliberately matches the verification query in the plan (BETWEEN '05' AND '08').
    """
    if not days:
        return []
    ph = ",".join("?" * len(days))
    return conn.execute(
        f"""
        SELECT common_name,
               COUNT(*) AS total,
               SUM(CASE WHEN CAST(strftime('%H', heard_at) AS INTEGER) BETWEEN 5 AND 8
                        THEN 1 ELSE 0 END) AS morning,
               SUM(CASE WHEN CAST(strftime('%H', heard_at) AS INTEGER) IN (20,21,22,23,0)
                        THEN 1 ELSE 0 END) AS night,
               MIN(date(heard_at)) AS first_in_window
        FROM detections
        WHERE date(heard_at) IN ({ph})
        GROUP BY common_name
        ORDER BY total DESC
        """,
        days,
    ).fetchall()


def species_range_counts(
    conn: sqlite3.Connection, days: tuple[str, ...]
) -> list[sqlite3.Row]:
    """Per-species total detection count for a given window (used for Risers & Fallers)."""
    if not days:
        return []
    ph = ",".join("?" * len(days))
    return conn.execute(
        f"""
        SELECT common_name, COUNT(*) AS total
        FROM detections
        WHERE date(heard_at) IN ({ph})
        GROUP BY common_name
        ORDER BY total DESC
        """,
        days,
    ).fetchall()


def species_hour_matrix(
    conn: sqlite3.Connection, days: tuple[str, ...]
) -> list[sqlite3.Row]:
    """Per-species per-hour detection counts across visible days (for sparklines)."""
    if not days:
        return []
    ph = ",".join("?" * len(days))
    return conn.execute(
        f"""
        SELECT common_name,
               CAST(strftime('%H', heard_at) AS INTEGER) AS hour,
               COUNT(*) AS n
        FROM detections
        WHERE date(heard_at) IN ({ph})
        GROUP BY common_name, hour
        ORDER BY common_name, hour
        """,
        days,
    ).fetchall()


def longest_heard_streak(heard_times: list[str], *, max_gap_sec: float = 6.0) -> int:
    """Longest run of detections with gaps no larger than max_gap_sec."""
    if not heard_times:
        return 0
    times = sorted(datetime.fromisoformat(t) for t in heard_times)
    best = cur = 1
    for i in range(1, len(times)):
        gap = (times[i] - times[i - 1]).total_seconds()
        if gap <= max_gap_sec:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best
