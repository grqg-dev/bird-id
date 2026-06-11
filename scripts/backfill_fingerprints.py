#!/usr/bin/env python3
"""Backfill acoustic fingerprints for tracks that have audio but no vector.

For each track missing a current-version fingerprint, embeds the detection
window through the BirdNET model (see fingerprint.py). Prefers the segment wav
(lossless, one load per segment); falls back to the track's clip file.

Safe to re-run: only processes tracks still missing a fingerprint at
fingerprint.FINGERPRINT_VERSION. Bumping the version re-fingerprints everything.

Examples:
  ./.venv/bin/python scripts/backfill_fingerprints.py
  ./.venv/bin/python scripts/backfill_fingerprints.py --dry-run
  ./.venv/bin/python scripts/backfill_fingerprints.py --db birdid.db --limit 10
"""

from __future__ import annotations

import argparse
import sys
from itertools import groupby
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import fingerprint  # noqa: E402
import storage  # noqa: E402


def backfill(
    conn,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    commit_every: int = 50,
) -> tuple[int, int, int]:
    """Returns (written, skipped_missing_audio, failed)."""
    rows = storage.tracks_without_fingerprint(
        conn, version=fingerprint.FINGERPRINT_VERSION
    )
    if limit is not None:
        rows = rows[:limit]

    written = skipped = failed = 0
    pending = 0
    # Rows arrive ordered by segment; embed each segment wav once for all its
    # windows, falling back to per-track clips when the wav is gone.
    for _seg_id, seg_rows in groupby(rows, key=lambda r: r["segment_id"]):
        seg_rows = list(seg_rows)
        wav = seg_rows[0]["wav_path"]
        wav_ok = wav and Path(wav).expanduser().is_file()

        from_wav: dict[tuple[float, float], object] = {}
        if wav_ok and not dry_run:
            try:
                from_wav = fingerprint.embed_windows(
                    wav, [(r["start_time"], r["end_time"]) for r in seg_rows]
                )
            except Exception as exc:  # noqa: BLE001 — bad wav shouldn't stop the run
                print(f"  segment wav failed ({wav}): {exc}", file=sys.stderr)

        for row in seg_rows:
            clip = row["clip_path"]
            clip_ok = clip and Path(clip).expanduser().is_file()
            if dry_run:
                if wav_ok or clip_ok:
                    written += 1
                else:
                    skipped += 1
                continue

            vec = from_wav.get((row["start_time"], row["end_time"]))
            if vec is None and clip_ok:
                try:
                    vec = fingerprint.embed_clip(clip)
                except Exception as exc:  # noqa: BLE001
                    print(f"  clip failed ({clip}): {exc}", file=sys.stderr)
                    failed += 1
                    continue
            if vec is None:
                skipped += 1
                continue

            storage.save_fingerprint(
                conn,
                row["id"],
                fingerprint.pack(vec),
                dim=len(vec),
                version=fingerprint.FINGERPRINT_VERSION,
            )
            written += 1
            pending += 1
            if pending >= commit_every:
                conn.commit()
                pending = 0
                print(f"  ... {written} fingerprinted", flush=True)

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
        "--dry-run",
        action="store_true",
        help="count work without loading the model or writing the DB",
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
        help="commit DB every N fingerprints (default: 50)",
    )
    args = parser.parse_args()

    cfg = config.load(args.config)
    db_path = Path(config.resolve(args.db, "db", cfg)).expanduser()

    conn = storage.connect(db_path)
    try:
        todo = len(
            storage.tracks_without_fingerprint(
                conn, version=fingerprint.FINGERPRINT_VERSION
            )
        )
        if todo == 0:
            print(f"backfill_fingerprints: nothing to do ({db_path})")
            return 0
        print(
            f"backfill_fingerprints: {todo} track(s) without v{fingerprint.FINGERPRINT_VERSION} "
            f"fingerprint  (db: {db_path})"
        )
        if args.dry_run:
            print("dry run — no model load, no DB writes")

        written, skipped, failed = backfill(
            conn,
            dry_run=args.dry_run,
            limit=args.limit,
            commit_every=args.commit_every,
        )
        print(
            f"done: {written} fingerprinted, {skipped} skipped (no audio on disk), "
            f"{failed} failed"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
