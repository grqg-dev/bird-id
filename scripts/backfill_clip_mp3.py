#!/usr/bin/env python3
"""One-time backfill: convert existing .wav track clips to .mp3.

Updates tracks.clip_path and removes the old wav after a verified encode.
Optional --drop-segments removes full segment wavs when every track on that
segment already has a playable clip file (dashboard serves clips first).

Dry-run by default; pass --apply to write.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import clips  # noqa: E402
import config  # noqa: E402
import storage  # noqa: E402


def _wav_clips(conn) -> list:
    rows = conn.execute(
        "SELECT id, clip_path FROM tracks WHERE clip_path IS NOT NULL"
    ).fetchall()
    out = []
    for row in rows:
        path = Path(row["clip_path"])
        if path.suffix.lower() != ".wav":
            continue
        if not path.is_file():
            continue
        out.append(row)
    return out


def _segment_can_drop_wav(conn, segment_id: int) -> bool:
    row = conn.execute(
        "SELECT wav_path FROM segments WHERE id = ?", (segment_id,)
    ).fetchone()
    if not row or not row["wav_path"]:
        return False
    tracks = conn.execute(
        "SELECT clip_path FROM tracks WHERE segment_id = ?", (segment_id,)
    ).fetchall()
    if not tracks:
        return True
    for t in tracks:
        if not t["clip_path"]:
            return False
        if not Path(t["clip_path"]).is_file():
            return False
    return True


def run(
    *,
    apply: bool,
    cfg_path: str | None,
    drop_segments: bool,
    limit: int | None,
) -> int:
    cfg = config.load(cfg_path or config.CONFIG_PATH)
    bitrate = str(config.resolve(None, "clip_mp3_bitrate", cfg) or "64k")
    if apply and not clips._ffmpeg_path():
        print("error: ffmpeg not found (PATH or ~/bin)", file=sys.stderr)
        return 1

    conn = storage.connect(Path(cfg["db"]).expanduser())
    try:
        rows = _wav_clips(conn)
        if limit is not None:
            rows = rows[:limit]

        wav_bytes = sum(Path(r["clip_path"]).stat().st_size for r in rows)
        converted = 0
        mp3_bytes = 0
        skipped = 0

        for row in rows:
            wav_path = Path(row["clip_path"])
            mp3_path = wav_path.with_suffix(".mp3")
            if mp3_path.is_file() and mp3_path.stat().st_size > 0:
                if apply:
                    conn.execute(
                        "UPDATE tracks SET clip_path = ? WHERE id = ?",
                        (str(mp3_path), row["id"]),
                    )
                    if wav_path.is_file():
                        wav_path.unlink()
                converted += 1
                mp3_bytes += mp3_path.stat().st_size
                continue

            if not apply:
                converted += 1
                continue

            result = clips.transcode_to_mp3(wav_path, mp3_path, bitrate=bitrate)
            if not result:
                skipped += 1
                print(f"skip track {row['id']}: encode failed ({wav_path.name})")
                continue
            mp3_path = Path(result)
            conn.execute(
                "UPDATE tracks SET clip_path = ? WHERE id = ?",
                (result, row["id"]),
            )
            wav_path.unlink()
            converted += 1
            mp3_bytes += mp3_path.stat().st_size

        if apply and converted:
            conn.commit()

        print(f"wav clips found: {len(rows)} ({wav_bytes / 1e9:.2f} GB)")
        print(f"to convert:      {converted}")
        if apply:
            print(f"encoded ok:      {converted - skipped}")
            print(f"failed:          {skipped}")
            print(f"mp3 total:       {mp3_bytes / 1e6:.1f} MB")

        seg_drop = 0
        seg_freed = 0
        if drop_segments:
            seg_ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM segments WHERE wav_path IS NOT NULL"
                ).fetchall()
            ]
            for sid in seg_ids:
                if not _segment_can_drop_wav(conn, sid):
                    continue
                row = conn.execute(
                    "SELECT wav_path FROM segments WHERE id = ?", (sid,)
                ).fetchone()
                path = Path(row["wav_path"])
                size = path.stat().st_size if path.is_file() else 0
                seg_drop += 1
                seg_freed += size
                if apply:
                    storage.clear_segment_wav(conn, sid)

            print(
                f"segment wavs to drop: {seg_drop} ({seg_freed / 1e9:.2f} GB)"
            )

        if not apply:
            print("dry-run only — pass --apply to convert")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="encode mp3, update DB, delete wavs",
    )
    parser.add_argument(
        "--drop-segments",
        action="store_true",
        help="after clips are mp3, remove full segment wavs with full clip coverage",
    )
    parser.add_argument("--limit", type=int, default=None, help="max clips to process")
    parser.add_argument("--config", dest="cfg_path", default=None)
    args = parser.parse_args(argv)
    return run(
        apply=args.apply,
        cfg_path=args.cfg_path,
        drop_segments=args.drop_segments,
        limit=args.limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
