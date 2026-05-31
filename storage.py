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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id             INTEGER PRIMARY KEY,
    started_at     TEXT NOT NULL,
    ended_at       TEXT NOT NULL,
    duration       REAL NOT NULL,
    wav_path       TEXT,                 -- NULL if the segment audio was discarded
    mean_dbfs      REAL,
    max_dbfs       REAL,
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
    heard_at        TEXT NOT NULL         -- absolute local ISO time of the window
);

CREATE INDEX IF NOT EXISTS idx_detections_species ON detections(common_name);
CREATE INDEX IF NOT EXISTS idx_detections_heard_at ON detections(heard_at);
"""


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    """Open (creating if needed) the database with sane settings and schema."""
    conn = sqlite3.connect(str(Path(db_path).expanduser()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # let `stats` read while monitor writes
    conn.executescript(_SCHEMA)
    conn.commit()
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
) -> int:
    """Insert a segment and its detections in one transaction. Returns segment id.

    `detections` are identifier.Detection objects. Each detection's `heard_at` is
    computed as started_at + its window start offset.
    """
    detections = list(detections)
    cur = conn.execute(
        "INSERT INTO segments (started_at, ended_at, duration, wav_path, "
        "mean_dbfs, max_dbfs, num_detections) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            started_at.isoformat(timespec="seconds"),
            ended_at.isoformat(timespec="seconds"),
            duration,
            wav_path,
            mean_dbfs,
            max_dbfs,
            len(detections),
        ),
    )
    segment_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO detections (segment_id, common_name, scientific_name, "
        "confidence, start_time, end_time, heard_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                segment_id,
                d.common_name,
                d.scientific_name,
                d.confidence,
                d.start_time,
                d.end_time,
                (started_at + timedelta(seconds=d.start_time)).isoformat(timespec="seconds"),
            )
            for d in detections
        ],
    )
    conn.commit()
    return segment_id


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


_DEX_SELECT = """
        SELECT common_name, scientific_name, confidence AS peak_conf,
               segment_id, start_time, end_time, wav_path,
               windows, first_heard, last_heard
        FROM (
            SELECT d.common_name, d.scientific_name, d.confidence,
                   d.segment_id, d.start_time, d.end_time, s.wav_path,
                   ROW_NUMBER() OVER (PARTITION BY d.common_name ORDER BY d.confidence DESC) AS rn,
                   COUNT(*)      OVER (PARTITION BY d.common_name) AS windows,
                   MIN(d.heard_at) OVER (PARTITION BY d.common_name) AS first_heard,
                   MAX(d.heard_at) OVER (PARTITION BY d.common_name) AS last_heard
            FROM detections d JOIN segments s ON s.id = d.segment_id
            {where}
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


def recent_detections(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """Most recent detections joined to their segment (for the dashboard feed)."""
    return conn.execute(
        """
        SELECT d.id, d.common_name, d.scientific_name, d.confidence, d.heard_at,
               d.start_time, d.end_time, d.segment_id, s.wav_path
        FROM detections d JOIN segments s ON s.id = d.segment_id
        ORDER BY d.heard_at DESC LIMIT ?
        """,
        (limit,),
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
               d.segment_id, s.wav_path
        FROM detections d JOIN segments s ON s.id = d.segment_id
        WHERE d.common_name = ?{extra}
        ORDER BY d.confidence DESC, d.heard_at DESC
        LIMIT ? OFFSET ?
        """,
        (common_name, *params, limit, offset),
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


def detections_for_segments(
    conn: sqlite3.Connection,
    segment_ids: list[int],
    *,
    day: str | None = None,
) -> list[sqlite3.Row]:
    """Detections limited to given segments (for AM/PM bucketing)."""
    if not segment_ids:
        return []
    placeholders = ",".join("?" * len(segment_ids))
    extra = " AND date(heard_at) = ?" if day else ""
    params: tuple = (*segment_ids, day) if day else tuple(segment_ids)
    return conn.execute(
        f"""
        SELECT heard_at, common_name, segment_id
        FROM detections
        WHERE segment_id IN ({placeholders}){extra}
        """,
        params,
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
