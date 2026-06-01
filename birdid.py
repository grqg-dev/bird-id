#!/usr/bin/env python3
"""bird-id prototype CLI.

Two independent chunks wired together:
  record   - capture audio from the mic to a wav file        (recorder.py)
  identify - run BirdNET on a wav file                        (identifier.py)
  listen   - record, then identify (the live end-to-end path)

The identify path is mic-free, so the dev test loop is just:
    python birdid.py identify ~/Desktop/bird.wav
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import config
import recorder
import identifier
import storage


def _print_detections(detections, summary: bool = False) -> None:
    if not detections:
        print("No birds detected above the confidence threshold.")
        return
    if summary:
        rows = identifier.summarize(detections)
        for s in rows:
            print(
                f"  {s.common_name} ({s.scientific_name})  "
                f"peak={s.max_confidence:.3f}  x{s.count} windows  "
                f"[{s.first_time:.0f}-{s.last_time:.0f}s]"
            )
        print(f"{len(rows)} species, {len(detections)} window detection(s).")
        return
    for d in detections:
        print(
            f"  {d.common_name} ({d.scientific_name})  "
            f"conf={d.confidence:.3f}  [{d.start_time:.0f}-{d.end_time:.0f}s]"
        )
    print(f"{len(detections)} detection(s).")


def cmd_record(args) -> int:
    print(f"Recording {args.seconds}s from device {args.device} -> {args.out} ...")
    result = recorder.record(args.seconds, args.out, device=args.device)
    print(
        f"Saved {result.path} ({result.seconds:.1f}s, "
        f"mean {result.mean_volume_dbfs:.1f} dBFS, peak {result.max_volume_dbfs:.1f} dBFS)"
    )
    return 0


def _resolve_id_params(args):
    cfg = args.cfg
    min_conf = config.resolve(args.min_conf, "min_conf", cfg)
    lat = config.resolve(args.lat, "lat", cfg)
    lon = config.resolve(args.lon, "lon", cfg)
    loc = f", location filter @ ({lat}, {lon})" if (lat is not None and lon is not None) else ""
    return min_conf, lat, lon, loc


def _audio_duration(path: Path) -> float:
    import librosa

    return float(librosa.get_duration(path=str(path)))


def _db_paths(args) -> tuple[Path, Path]:
    db_path = Path(config.resolve(args.db, "db", args.cfg)).expanduser()
    rec_dir = Path(config.resolve(args.dir, "recordings_dir", args.cfg)).expanduser()
    return db_path, rec_dir


def _retention_days(args) -> int:
    return int(config.resolve(getattr(args, "retention_days", None), "retention_days", args.cfg) or 0)


def _run_audio_cleanup(conn, rec_dir: Path, args, *, label: str = "retention") -> None:
    """Expire old kept wavs and drop unreferenced files in recordings_dir."""
    days = _retention_days(args)
    expired, freed = storage.expire_segment_audio(conn, retention_days=days)
    orphans, orphan_freed = storage.purge_orphan_recordings(conn, rec_dir)
    if expired:
        print(f"[{label}] expired {expired} segment wav(s), freed {freed / 1e6:.1f} MB")
    if orphans:
        print(f"[{label}] removed {orphans} orphan wav(s), freed {orphan_freed / 1e6:.1f} MB")


def _import_to_wav(src: Path, dest_dir: Path, started_at: datetime) -> Path:
    """Copy or convert source audio to a 48 kHz mono wav under dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"import_{started_at:%Y%m%d_%H%M%S}.wav"
    if src.suffix.lower() == ".wav":
        shutil.copy2(src, dest)
        return dest

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("ffmpeg not found on PATH (needed to convert non-wav imports).")
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", str(recorder.TARGET_SAMPLE_RATE), "-y", str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to convert {src}:\n{proc.stderr.strip()}")
    return dest


def cmd_identify(args) -> int:
    min_conf, lat, lon, loc = _resolve_id_params(args)
    print(f"Analyzing {args.wav} (min_conf={min_conf}{loc}) ...")
    detections = identifier.identify(args.wav, min_conf=min_conf, lat=lat, lon=lon)
    _print_detections(detections, summary=args.summary)

    if args.save:
        src = Path(args.wav).expanduser()
        duration = _audio_duration(src)
        if args.when:
            started_at = datetime.fromisoformat(args.when)
        else:
            ended_at = datetime.fromtimestamp(src.stat().st_mtime)
            started_at = ended_at - timedelta(seconds=duration)
        ended_at = started_at + timedelta(seconds=duration)

        db_path, rec_dir = _db_paths(args)
        kept_path = _import_to_wav(src, rec_dir, started_at) if detections else None

        conn = storage.connect(db_path)
        try:
            seg_id = storage.record_segment(
                conn,
                started_at=started_at,
                ended_at=ended_at,
                duration=duration,
                detections=detections,
                wav_path=str(kept_path) if kept_path else None,
            )
        finally:
            conn.close()

        if detections:
            print(f"Saved segment {seg_id} ({len(detections)} detections, "
                  f"{duration:.0f}s) -> {db_path}")
        else:
            print(f"Saved empty segment {seg_id} -> {db_path} (no audio kept).")

    return 0


def cmd_listen(args) -> int:
    min_conf, lat, lon, loc = _resolve_id_params(args)
    print(f"Recording {args.seconds}s from device {args.device} -> {args.out} ...")
    result = recorder.record(args.seconds, args.out, device=args.device)
    print(f"Saved {result.path} (peak {result.max_volume_dbfs:.1f} dBFS). Identifying{loc} ...")
    detections = identifier.identify(result.path, min_conf=min_conf, lat=lat, lon=lon)
    _print_detections(detections)
    return 0


def cmd_monitor(args) -> int:
    """Continuous loop: every interval, record -> identify -> store to the DB.

    Sequential by design (no threading): a few seconds of processing gap between
    segments is fine, and a plain loop self-heals across laptop sleep. The cached
    BirdNET model is reused across iterations because we stay in one process.
    """
    # Line-buffer stdout so per-segment progress shows immediately even when the
    # monitor's output is redirected to a file (block-buffered by default).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    min_conf, lat, lon, loc = _resolve_id_params(args)
    db_path, rec_dir = _db_paths(args)
    conn = storage.connect(db_path)
    rec_dir.mkdir(parents=True, exist_ok=True)
    interval_s = args.minutes * 60.0
    retention = _retention_days(args)
    retention_note = f", audio retention {retention}d" if retention > 0 else ""

    print(
        f"Monitoring: {args.minutes:g}-min segments -> {db_path} "
        f"(min_conf={min_conf}{loc}{retention_note}). Ctrl-C to stop.\n"
    )
    _run_audio_cleanup(conn, rec_dir, args)
    segment_no = 0
    try:
        while True:
            segment_no += 1
            started_at = datetime.now()
            wav_path = rec_dir / f"seg_{started_at:%Y%m%d_%H%M%S}.wav"
            try:
                rec = recorder.record(interval_s, wav_path, device=args.device)
            except recorder.RecordingError as e:
                print(f"[{started_at:%H:%M:%S}] recording failed: {e}", file=sys.stderr)
                return 1  # a recording failure is persistent (mic/permission) — stop
            ended_at = datetime.now()

            detections = identifier.identify(rec.path, min_conf=min_conf,
                                             lat=lat, lon=lon)

            # Retention: keep audio only for segments that found something.
            kept_path = str(rec.path)
            if not detections:
                rec.path.unlink(missing_ok=True)
                kept_path = None

            storage.record_segment(
                conn,
                started_at=started_at,
                ended_at=ended_at,
                duration=rec.seconds,
                detections=detections,
                wav_path=kept_path,
                mean_dbfs=rec.mean_volume_dbfs,
                max_dbfs=rec.max_volume_dbfs,
            )

            stamp = started_at.strftime("%H:%M:%S")
            if detections:
                species = sorted({d.common_name for d in detections})
                print(f"[{stamp}] seg {segment_no}: {len(detections)} hit(s) — "
                      f"{', '.join(species)}")
            else:
                print(f"[{stamp}] seg {segment_no}: nothing detected (audio discarded)")
            _run_audio_cleanup(conn, rec_dir, args)
    except KeyboardInterrupt:
        print("\nStopping. Final tally:")
        cmd_stats(args, conn=conn)
    finally:
        conn.close()
    return 0


def cmd_stats(args, conn=None) -> int:
    own = conn is None
    db_path = config.resolve(args.db, "db", args.cfg)
    if own:
        conn = storage.connect(db_path)
    try:
        t = storage.totals(conn)
        if not t["segments"]:
            print(f"No data yet in {db_path}.")
            return 0
        print(f"\n{t['segments']} segments, {t['detections']} detections, "
              f"{t['species']} species  ({t['first_segment']} → {t['last_segment']})")
        for r in storage.species_summary(conn):
            print(f"  {r['common_name']} ({r['scientific_name']})  "
                  f"peak={r['peak_conf']:.3f}  x{r['windows']}  "
                  f"[{r['first_heard']} → {r['last_heard']}]")
    finally:
        if own:
            conn.close()
    return 0


def cmd_digest(args) -> int:
    day = args.date or datetime.now().strftime("%Y-%m-%d")
    db_path = config.resolve(args.db, "db", args.cfg)
    conn = storage.connect(db_path)
    try:
        ov = storage.day_overview(conn, day)
        print(f"\nDaily digest — {day}")
        if not ov["detections"]:
            print("  No detections recorded this day.")
            return 0

        first = ov["first_heard"][11:16] if ov["first_heard"] else "?"
        last = ov["last_heard"][11:16] if ov["last_heard"] else "?"
        hourly = storage.day_hourly(conn, day)
        peak = max(hourly, key=lambda r: r["n"]) if hourly else None
        peak_str = f"{peak['hour']}:00 ({peak['n']} detections)" if peak else "n/a"

        print(f"  {ov['species']} species, {ov['detections']} detections")
        print(f"  First bird {first}, last {last}, busiest hour {peak_str}")

        new = storage.new_species_on(conn, day)
        if new:
            print(f"  New to your records: {', '.join(new)}")

        print("\n  Species (by peak confidence):")
        for r in storage.day_species(conn, day):
            flag = "  NEW" if r["common_name"] in new else ""
            print(f"    {r['common_name']:<28} peak={r['peak_conf']:.3f}  "
                  f"x{r['windows']:<3} first {r['first_heard'][11:16]}{flag}")
    finally:
        conn.close()
    return 0


def cmd_dashboard(args) -> int:
    import dashboard  # imported here so other commands don't pay Flask's import cost
    dashboard.main(host=args.host, port=args.port, dev=args.dev)
    return 0


def cmd_cleanup(args) -> int:
    """Drop expired segment wavs and unreferenced files in recordings_dir."""
    db_path, rec_dir = _db_paths(args)
    conn = storage.connect(db_path)
    try:
        _run_audio_cleanup(conn, rec_dir, args, label="cleanup")
    finally:
        conn.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="birdid", description="Record and identify bird sounds.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="record audio from the mic to a wav file")
    p_rec.add_argument("out", nargs="?", default="recordings/sample.wav")
    p_rec.add_argument("-t", "--seconds", type=float, default=5.0)
    p_rec.add_argument("-d", "--device", default="0")
    p_rec.set_defaults(func=cmd_record)

    p_id = sub.add_parser("identify", help="identify birds in an existing wav file")
    p_id.add_argument("wav")
    p_id.add_argument("-c", "--min-conf", type=float, help="min confidence (overrides config)")
    p_id.add_argument("--lat", type=float)
    p_id.add_argument("--lon", type=float)
    p_id.add_argument(
        "-s", "--summary", action="store_true",
        help="roll per-window hits up into one row per species (use for big files)",
    )
    p_id.add_argument("--save", action="store_true", help="write results to the database")
    p_id.add_argument("--db", help="SQLite database path (overrides config)")
    p_id.add_argument("--dir", help="where imported wavs are written (overrides config)")
    p_id.add_argument(
        "--when",
        help="segment start as ISO datetime (default: file mtime minus duration)",
    )
    p_id.set_defaults(func=cmd_identify)

    p_listen = sub.add_parser("listen", help="record from the mic, then identify")
    p_listen.add_argument("out", nargs="?", default="recordings/listen.wav")
    p_listen.add_argument("-t", "--seconds", type=float, default=5.0)
    p_listen.add_argument("-d", "--device", default="0")
    p_listen.add_argument("-c", "--min-conf", type=float, help="min confidence (overrides config)")
    p_listen.add_argument("--lat", type=float)
    p_listen.add_argument("--lon", type=float)
    p_listen.set_defaults(func=cmd_listen)

    p_mon = sub.add_parser("monitor", help="continuously record N-min segments and store results")
    p_mon.add_argument("-m", "--minutes", type=float, default=5.0, help="segment length in minutes")
    p_mon.add_argument("-d", "--device", default="0")
    p_mon.add_argument("-c", "--min-conf", type=float, help="min confidence (overrides config)")
    p_mon.add_argument("--db", help="SQLite database path (overrides config)")
    p_mon.add_argument("--dir", help="where segment wavs are written (overrides config)")
    p_mon.add_argument("--lat", type=float)
    p_mon.add_argument("--lon", type=float)
    p_mon.add_argument(
        "--retention-days", type=int,
        help="days to keep segment wav files (0 = forever; overrides config)",
    )
    p_mon.set_defaults(func=cmd_monitor)

    p_clean = sub.add_parser(
        "cleanup",
        help="delete expired segment wavs and orphan files in recordings_dir",
    )
    p_clean.add_argument("--db", help="SQLite database path (overrides config)")
    p_clean.add_argument("--dir", help="recordings directory (overrides config)")
    p_clean.add_argument(
        "--retention-days", type=int,
        help="days to keep segment wav files (0 = forever; overrides config)",
    )
    p_clean.set_defaults(func=cmd_cleanup)

    p_stats = sub.add_parser("stats", help="show running results from the database")
    p_stats.add_argument("--db", help="SQLite database path (overrides config)")
    p_stats.set_defaults(func=cmd_stats)

    p_dig = sub.add_parser("digest", help="daily summary: species, timing, new birds")
    p_dig.add_argument("--date", help="day as YYYY-MM-DD (default: today)")
    p_dig.add_argument("--db", help="SQLite database path (overrides config)")
    p_dig.set_defaults(func=cmd_digest)

    p_dash = sub.add_parser("dashboard", help="launch the local web dashboard")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", type=int, default=8080)
    p_dash.add_argument(
        "--dev", action="store_true",
        help="auto-reload on code changes (local dev only)",
    )
    p_dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args(argv)
    args.cfg = config.load()
    try:
        return args.func(args)
    except (recorder.RecordingError, FileNotFoundError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
