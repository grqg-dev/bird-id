#!/usr/bin/env python3
"""Drop segment wavs and track clips not needed by the dashboard.

Thin wrapper around storage.cleanup_dashboard_audio(). Dry-run by default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import storage  # noqa: E402


def run(*, apply: bool, cfg_path: str | None) -> int:
    cfg = config.load(cfg_path or config.CONFIG_PATH)
    db_path = Path(cfg["db"]).expanduser()
    rec_dir = Path(cfg["recordings_dir"]).expanduser()
    live_hours = int(config.resolve(None, "dashboard_live_hours", cfg) or 24)
    conn = storage.connect(db_path)
    try:
        stats = storage.cleanup_dashboard_audio(
            conn, rec_dir, dry_run=not apply, live_hours=live_hours
        )
        print(f"protected detections: {stats['protected_detections']}")
        print(f"protected segments:   {stats['protected_segments']}")
        print(f"protected tracks:     {stats['protected_tracks']}")
        print(
            f"segment wavs to drop: {stats['segments_dropped']} "
            f"({stats['bytes_freed'] / 1e9:.2f} GB)"
        )
        print(f"track clips to drop:  {stats['clips_dropped']}")
        if not apply:
            print("dry-run only — pass --apply to delete")
        else:
            print(f"done — freed {stats['bytes_freed'] / 1e9:.2f} GB")
            if stats.get("orphan_segments"):
                print(f"orphan segment wavs removed: {stats['orphan_segments']}")
            if stats.get("orphan_clips"):
                print(f"orphan clips removed: {stats['orphan_clips']}")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete files and clear DB paths (default is dry-run)",
    )
    parser.add_argument(
        "--config",
        dest="cfg_path",
        default=None,
        help="path to config.json (default: repo config.json)",
    )
    args = parser.parse_args(argv)
    return run(apply=args.apply, cfg_path=args.cfg_path)


if __name__ == "__main__":
    raise SystemExit(main())
