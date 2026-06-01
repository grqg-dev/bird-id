#!/usr/bin/env python3
"""Backfill per-detection clip wavs for tracks that have clip_path NULL.

Run once after deploying the tracks schema (connect() already backfills track
rows; this writes recordings/clips/*.wav and updates tracks.clip_path).

Safe to re-run: only processes tracks still missing clip_path.

Examples:
  ./.venv/bin/python scripts/backfill_track_clips.py
  ./.venv/bin/python scripts/backfill_track_clips.py --dry-run
  ./.venv/bin/python scripts/backfill_track_clips.py --db birdid.db --limit 10
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import clips  # noqa: E402
import config  # noqa: E402
import storage  # noqa: E402


def _parse_started_at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def backfill(
    conn,
    clips_dir: Path,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    commit_every: int = 50,
) -> tuple[int, int, int]:
    """Returns (written, skipped_missing_wav, failed_slice)."""
    rows = storage.tracks_without_clip(conn)
    if limit is not None:
        rows = rows[:limit]

    written = skipped = failed = 0
    pending = 0
    for row in rows:
        src = Path(row["wav_path"]).expanduser()
        if not src.is_file():
            skipped += 1
            continue

        started_at = _parse_started_at(row["started_at"])
        dst = clips.clip_dst_path(clips_dir, started_at, row["start_time"], row["end_time"])
        if dry_run:
            print(f"would write track {row['id']}: {dst}")
            written += 1
            continue

        path = clips.write_clip(src, dst, row["start_time"], row["end_time"])
        if not path:
            failed += 1
            continue

        storage.set_track_clip_path(conn, row["id"], path)
        written += 1
        pending += 1
        if pending >= commit_every:
            conn.commit()
            pending = 0

    if not dry_run and pending:
        conn.commit()
    return written, skipped, failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=config.CONFIG_PATH,
        help="config.json path (default: repo config.json)",
    )
    parser.add_argument("--db", help="database path (default: from config)")
    parser.add_argument(
        "--clips-dir",
        type=Path,
        help="clip output directory (default: <recordings_dir>/clips)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print actions without writing files or updating the DB",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="process at most N tracks (for testing)",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=50,
        help="commit DB every N successful clips (default: 50)",
    )
    args = parser.parse_args()

    cfg = config.load(args.config)
    db_path = Path(config.resolve(args.db, "db", cfg)).expanduser()
    rec_dir = Path(config.resolve(None, "recordings_dir", cfg)).expanduser()
    clips_dir = (args.clips_dir or rec_dir / "clips").expanduser()

    conn = storage.connect(db_path)
    try:
        pending = len(storage.tracks_without_clip(conn))
        if pending == 0:
            print(f"backfill_track_clips: nothing to do ({db_path})")
            return 0

        print(
            f"backfill_track_clips: {pending} track(s) without clip "
            f"-> {clips_dir}  (db: {db_path})"
        )
        if args.dry_run:
            print("backfill_track_clips: dry-run")

        written, skipped, failed = backfill(
            conn,
            clips_dir,
            dry_run=args.dry_run,
            limit=args.limit,
            commit_every=args.commit_every,
        )
    finally:
        conn.close()

    print(
        f"backfill_track_clips: done — wrote {written}, "
        f"skipped {skipped} (segment wav missing), failed {failed} (empty slice)"
    )
    if failed and not args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
