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
