#!/usr/bin/env python3
"""Derive per-hour capture_health rows from existing `segments` history.

audio_seconds = sum of segment durations per local hour (each minus the
`tail_carry_seconds` overlap that every segment re-includes), capped at 3600;
mean_dbfs = duration-weighted mean of segments.mean_dbfs. stalls, respawns and
dead_seconds stay NULL (not measured for history). Hours that already have a
row are left alone, so it is safe to re-run and never fights the live monitor.

    ./.venv/bin/python scripts/backfill_capture_health.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import storage  # noqa: E402


def derive(conn, tail_carry: float) -> list[tuple[str, float, float | None]]:
    """Return (hour, audio_seconds, mean_dbfs) for every hour with segments."""
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m-%dT%H:00:00', started_at) AS hour,
               SUM(MAX(duration - ?, 0)) AS audio,
               SUM(CASE WHEN mean_dbfs IS NOT NULL THEN mean_dbfs * duration END)
                 / NULLIF(SUM(CASE WHEN mean_dbfs IS NOT NULL THEN duration END), 0) AS mean_db
        FROM segments
        GROUP BY hour
        ORDER BY hour
        """,
        (tail_carry,),
    ).fetchall()
    return [(r["hour"], min(r["audio"] or 0.0, 3600.0), r["mean_db"]) for r in rows]


def backfill(conn, tail_carry: float, *, dry_run: bool = False) -> tuple[int, int]:
    """Insert missing hours. Returns (hours_derived, hours_inserted)."""
    derived = derive(conn, tail_carry)
    existing = {r[0] for r in conn.execute("SELECT hour FROM capture_health")}
    todo = [d for d in derived if d[0] not in existing]
    if not dry_run and todo:
        conn.executemany(
            "INSERT OR IGNORE INTO capture_health "
            "(hour, audio_seconds, stalls, respawns, mean_dbfs, dead_seconds) "
            "VALUES (?, ?, NULL, NULL, ?, NULL)",
            todo,
        )
        conn.commit()
    return len(derived), len(todo)


def main(argv=None) -> int:
    cfg = config.load()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", help="SQLite database (default: config db)")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument(
        "--tail-carry", type=float, default=float(cfg.get("tail_carry_seconds", 1.0)),
        help="per-segment overlap to subtract (default: config tail_carry_seconds)",
    )
    args = ap.parse_args(argv)
    db = args.db or cfg["db"]
    conn = storage.connect(db)
    try:
        derived, inserted = backfill(conn, args.tail_carry, dry_run=args.dry_run)
    finally:
        conn.close()
    verb = "would insert" if args.dry_run else "inserted"
    print(f"{derived} hour(s) derived from segments; {verb} {inserted} new row(s) in {db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
