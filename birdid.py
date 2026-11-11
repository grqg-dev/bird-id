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
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import clips
import config
import health
import recorder
import logrotate
import retention
import segmenter
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


def _clip_format(cfg: dict) -> str:
    fmt = str(config.resolve(None, "clip_format", cfg) or "wav").lower()
    return "mp3" if fmt == "mp3" else "wav"


def _run_audio_cleanup(
    conn,
    rec_dir: Path,
    args,
    *,
    label: str = "retention",
    dry_run: bool = False,
    assume_clips_present: bool = False,
    now: datetime | None = None,
) -> dict:
    """Expire old segment wavs, apply the clip retention policy, drop orphans.

    With dry_run, only the clip policy is evaluated (nothing is touched) and its
    per-reason report is printed.
    """
    policy = retention.Policy.from_config(args.cfg)
    if dry_run:
        report = retention.run(
            conn, policy, now=now, dry_run=True, assume_present=assume_clips_present
        )
        for line in retention.format_report(report, dry_run=True):
            print(f"[{label}] {line}")
        return report

    days = _retention_days(args)
    expired, freed = storage.expire_segment_audio(conn, retention_days=days)
    report = retention.run(conn, policy, now=now)
    orphans, orphan_freed = storage.purge_orphan_recordings(conn, rec_dir)
    clip_orphans, clip_orphan_freed = storage.purge_orphan_clips(conn, rec_dir / "clips")
    cache_removed, cache_freed = storage.purge_stale_cache(
        rec_dir / "cache", retention_days=days
    )
    try:  # keep launchd-owned logs from growing without bound (copy-truncate at log_max_mb)
        rotated = logrotate.rotate_logs(
            args.cfg.get("log_dir", "logs"),
            max_bytes=int(float(args.cfg.get("log_max_mb", 5)) * 1_000_000),
            backups=int(args.cfg.get("log_backups", 3)),
        )
        for f in rotated:
            print(f"[{label}] rotated {f.name}")
    except Exception as e:  # never let log hygiene break retention
        print(f"[{label}] log rotation failed: {e}")
    if expired:
        print(f"[{label}] expired {expired} segment wav(s), freed {freed / 1e6:.1f} MB")
    if report["deleted"]:
        by_reason = ", ".join(
            f"{r} {v['count']}"
            for r, v in report["summary"]["delete"].items()
        )
        print(
            f"[{label}] deleted {report['deleted']} clip(s) ({by_reason}), "
            f"freed {report['bytes_freed'] / 1e6:.1f} MB"
        )
    if orphans:
        print(f"[{label}] removed {orphans} orphan wav(s), freed {orphan_freed / 1e6:.1f} MB")
    if clip_orphans:
        print(
            f"[{label}] removed {clip_orphans} orphan clip(s), "
            f"freed {clip_orphan_freed / 1e6:.1f} MB"
        )
    if cache_removed:
        print(
            f"[{label}] purged {cache_removed} stale cache file(s), "
            f"freed {cache_freed / 1e6:.1f} MB"
        )
    return report


def _clip_paths_for_detections(
    src_wav: Path,
    clips_dir: Path,
    started_at: datetime,
    detections,
    *,
    cfg: dict,
) -> dict[tuple[float, float], str]:
    """Write per-window clip files; return map for storage.record_segment."""
    clips_dir.mkdir(parents=True, exist_ok=True)
    fmt = _clip_format(cfg)
    bitrate = str(config.resolve(None, "clip_mp3_bitrate", cfg) or "64k")
    clip_paths: dict[tuple[float, float], str] = {}
    for start, end in {(d.start_time, d.end_time) for d in detections}:
        dst = clips.clip_dst_path(clips_dir, started_at, start, end, fmt=fmt)
        path = clips.write_clip(
            src_wav, dst, start, end, fmt=fmt, mp3_bitrate=bitrate
        )
        if path:
            clip_paths[(start, end)] = path
    return clip_paths


def _maybe_drop_segment_wav(
    conn,
    segment_id: int,
    wav_path: Path,
    detections,
    clip_paths: dict[tuple[float, float], str],
    cfg: dict,
) -> None:
    """Drop the full segment wav once clips exist (or the segment had no hits)."""
    if not config.resolve(None, "drop_segment_after_clips", cfg):
        return
    if detections:
        windows = {(d.start_time, d.end_time) for d in detections}
        if not windows.issubset(clip_paths.keys()):
            return
    storage.clear_segment_wav(conn, segment_id)


def _fmt_mmss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _progress_bar(fraction: float, width: int = 20) -> str:
    filled = int(width * max(0.0, min(1.0, fraction)))
    return "#" * filled + "-" * (width - filled)


class _MonitorProgress:
    """Terminal progress for one monitor segment (record → analyze → result)."""

    _SPIN = "|/-\\"

    def __init__(self, stamp: str, seg_no: int, total_s: float) -> None:
        self.stamp = stamp
        self.seg_no = seg_no
        self.total_s = total_s
        self.interactive = sys.stdout.isatty()
        self._last_milestone = -1
        self._spin_i = 0
        self._analyze_start = 0.0

    def _emit(self, line: str, *, end: str = "\n") -> None:
        print(line, end=end, flush=True)

    def recording(self, fraction: float) -> None:
        elapsed = fraction * self.total_s
        clock = f"{_fmt_mmss(elapsed)}/{_fmt_mmss(self.total_s)}"
        if self.interactive:
            bar = _progress_bar(fraction)
            pct = int(fraction * 100)
            self._emit(
                f"\r[{self.stamp}] seg {self.seg_no}  record   [{bar}] {pct:3d}%  {clock}",
                end="",
            )
            return
        milestone = int(fraction * 100) // 25 * 25
        if milestone > self._last_milestone:
            self._last_milestone = milestone
            self._emit(f"[{self.stamp}] seg {self.seg_no}  recording  {milestone:3d}%  {clock}")

    def start_analyzing(self) -> None:
        self._analyze_start = time.monotonic()
        if not self.interactive:
            self._emit(f"[{self.stamp}] seg {self.seg_no}  analyzing...")

    def analyzing(self) -> None:
        if not self.interactive:
            return
        elapsed = time.monotonic() - self._analyze_start
        spin = self._SPIN[self._spin_i % len(self._SPIN)]
        self._spin_i += 1
        self._emit(
            f"\r[{self.stamp}] seg {self.seg_no}  analyze  [{spin}]  {elapsed:4.1f}s   ",
            end="",
        )

    def finish(self, line: str) -> None:
        if self.interactive:
            self._emit("\r" + " " * 78 + "\r", end="")
        self._emit(line)


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
    detections = identifier.identify(
        args.wav, min_conf=min_conf, lat=lat, lon=lon,
        filter_threshold=args.cfg.get("location_filter_threshold"),
    )
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
        kept_path = _import_to_wav(src, rec_dir, started_at)

        clip_paths = _clip_paths_for_detections(
            kept_path, rec_dir / "clips", started_at, detections, cfg=args.cfg
        )
        conn = storage.connect(db_path)
        try:
            seg_id = storage.record_segment(
                conn,
                started_at=started_at,
                ended_at=ended_at,
                duration=duration,
                detections=detections,
                wav_path=str(kept_path),
                clip_paths=clip_paths,
            )
            _maybe_drop_segment_wav(
                conn, seg_id, kept_path, detections, clip_paths, args.cfg
            )
        finally:
            conn.close()

        if detections:
            print(f"Saved segment {seg_id} ({len(detections)} detections, "
                  f"{duration:.0f}s) -> {db_path}")
        else:
            print(f"Saved segment {seg_id} (no detections, "
                  f"{duration:.0f}s) -> {db_path}")

    return 0


def cmd_listen(args) -> int:
    min_conf, lat, lon, loc = _resolve_id_params(args)
    print(f"Recording {args.seconds}s from device {args.device} -> {args.out} ...")
    result = recorder.record(args.seconds, args.out, device=args.device)
    print(f"Saved {result.path} (peak {result.max_volume_dbfs:.1f} dBFS). Identifying{loc} ...")
    detections = identifier.identify(
        result.path, min_conf=min_conf, lat=lat, lon=lon,
        filter_threshold=args.cfg.get("location_filter_threshold"),
    )
    _print_detections(detections)
    return 0


def _write_segment_wav(seg: segmenter.Segment, path: Path) -> None:
    """Write a segmenter.Segment to a 16-bit mono PCM wav file."""
    samples_i16 = (seg.samples * 32767.0).clip(-32768, 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(seg.sr)
        wf.writeframes(samples_i16.tobytes())


# Sentinel placed on the segment queue to signal capture-thread exit.
class _CaptureError:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc


def _processing_lag_note(started_at: datetime, threshold_seconds: float = 120.0) -> str:
    """Return ' (processed Xh Ym late)' when a segment is dequeued long after its
    capture time, else ''. A large lag means capture stalled (e.g. Mac sleep) and
    we're draining stale audio — surfaced in the log so it's obvious at a glance.
    """
    lag = (datetime.now() - started_at).total_seconds()
    if lag <= threshold_seconds:
        return ""
    mins = int(lag // 60)
    if mins >= 60:
        return f"  (processed {mins // 60}h{mins % 60:02d}m late)"
    return f"  (processed {mins}m late)"


def _flush_health(
    conn, cap_health: health.CaptureHealth, *, include_current: bool = False, quiet: bool = False
) -> None:
    """Write finished hours to capture_health and print one [health] line per hour."""
    rows = cap_health.drain(include_current=include_current)
    if not rows:
        return
    storage.upsert_capture_health(conn, rows)
    if not quiet:
        for st in rows:
            print(health.format_line(st))


def cmd_monitor(args) -> int:
    """Always-on streaming monitor: continuous capture → adaptive segmentation → identify → store.

    Two-thread design (one process so _ANALYZER stays cached):
      capture thread  — streams PCM from ffmpeg, segments adaptively, writes wavs, enqueues paths
      processor (main) — pulls paths from queue, identifies, stores, cleans up
    """
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    min_conf, lat, lon, loc = _resolve_id_params(args)
    db_path, rec_dir = _db_paths(args)
    conn = storage.connect(db_path)
    rec_dir.mkdir(parents=True, exist_ok=True)
    retention = _retention_days(args)
    retention_note = f", audio retention {retention}d" if retention > 0 else ""

    cfg = args.cfg
    hop_ms = int(config.resolve(None, "hop_ms", cfg))
    seg_cfg = dict(
        hop_ms=hop_ms,
        seg_target_seconds=float(config.resolve(None, "seg_target_seconds", cfg)),
        seg_max_seconds=float(config.resolve(None, "seg_max_seconds", cfg)),
        floor_window_seconds=float(config.resolve(None, "floor_window_seconds", cfg)),
        activity_margin_db=float(config.resolve(None, "activity_margin_db", cfg)),
        quiet_hold_ms=float(config.resolve(None, "quiet_hold_ms", cfg)),
        tail_carry_seconds=float(config.resolve(None, "tail_carry_seconds", cfg)),
        mic_dead_dbfs=float(config.resolve(None, "mic_dead_dbfs", cfg)),
        mic_dead_window_seconds=float(config.resolve(None, "mic_dead_window_seconds", cfg)),
    )

    print(
        f"Monitoring (streaming): segments ~{seg_cfg['seg_target_seconds']:.0f}–"
        f"{seg_cfg['seg_max_seconds']:.0f}s → {db_path} "
        f"(min_conf={min_conf}{loc}{retention_note}). Ctrl-C to stop.\n"
    )

    # Bounded queue — capture thread uses put_nowait; never blocks on processor.
    seg_queue: queue.Queue = queue.Queue(maxsize=20)
    stop_event = threading.Event()
    retention_every = 60.0 * float(config.resolve(None, "retention_interval_minutes", cfg))
    last_retention = time.monotonic()   # initial run happens just below
    stall_seconds = float(config.resolve(None, "capture_stall_seconds", cfg))
    cap_health = health.CaptureHealth(
        recorder.TARGET_SAMPLE_RATE, mic_dead_dbfs=seg_cfg["mic_dead_dbfs"]
    )
    _run_audio_cleanup(conn, rec_dir, args)

    def _capture() -> None:
        seg_no = 0
        try:
            hops = recorder.stream_pcm(
                args.device, hop_ms=hop_ms, stall_seconds=stall_seconds
            )
            for seg in segmenter.segment_stream(
                cap_health.tap(hops),
                recorder.TARGET_SAMPLE_RATE,
                datetime.now(),
                **seg_cfg,
            ):
                if stop_event.is_set():
                    return
                seg_no += 1
                duration = len(seg.samples) / seg.sr
                wav_path = rec_dir / f"seg_{seg.started_at:%Y%m%d_%H%M%S}_{seg_no:05d}.wav"
                _write_segment_wav(seg, wav_path)
                item = (wav_path, seg.started_at, duration, seg.mean_dbfs, seg.max_dbfs)
                try:
                    seg_queue.put_nowait(item)
                except queue.Full:
                    print(
                        f"[capture] queue full — dropping segment {wav_path.name}",
                        file=sys.stderr,
                    )
        except segmenter.MicDead as exc:
            seg_queue.put(_CaptureError(exc))
        except recorder.RecordingError as exc:
            seg_queue.put(_CaptureError(exc))
        except Exception as exc:  # noqa: BLE001
            seg_queue.put(_CaptureError(exc))
        finally:
            seg_queue.put(None)  # EOF sentinel

    capture_thread = threading.Thread(target=_capture, daemon=True, name="bird-capture")
    capture_thread.start()

    segment_no = 0
    try:
        while True:
            try:
                item = seg_queue.get(timeout=30)
            except queue.Empty:
                # No segment lately (e.g. total stall) — still record health.
                _flush_health(conn, cap_health)
                if time.monotonic() - last_retention >= retention_every:
                    _run_audio_cleanup(conn, rec_dir, args)
                    last_retention = time.monotonic()
                continue
            if item is None:
                # Capture thread exited (shouldn't happen in normal operation)
                print("\n[monitor] capture thread ended unexpectedly.", file=sys.stderr)
                break
            if isinstance(item, _CaptureError):
                print(f"\n[monitor] {item.exc}", file=sys.stderr)
                return 1
            wav_path, started_at, duration, mean_dbfs, max_dbfs = item
            segment_no += 1
            stamp = started_at.strftime("%H:%M:%S")
            late = _processing_lag_note(started_at)

            detections = identifier.identify(
                wav_path, min_conf=min_conf, lat=lat, lon=lon,
                filter_threshold=cfg.get("location_filter_threshold"),
            )
            ended_at = started_at + timedelta(seconds=duration)

            clip_paths = _clip_paths_for_detections(
                wav_path, rec_dir / "clips", started_at, detections, cfg=cfg
            )
            seg_id = storage.record_segment(
                conn,
                started_at=started_at,
                ended_at=ended_at,
                duration=duration,
                detections=detections,
                wav_path=str(wav_path),
                mean_dbfs=mean_dbfs,
                max_dbfs=max_dbfs,
                clip_paths=clip_paths,
            )
            _maybe_drop_segment_wav(conn, seg_id, wav_path, detections, clip_paths, cfg)

            if detections:
                species = sorted({d.common_name for d in detections})
                print(f"[{stamp}] seg {segment_no} ({duration:.0f}s): {', '.join(species)}{late}")
            else:
                print(f"[{stamp}] seg {segment_no} ({duration:.0f}s): nothing detected{late}")

            _flush_health(conn, cap_health)
            if time.monotonic() - last_retention >= retention_every:
                _run_audio_cleanup(conn, rec_dir, args)
                last_retention = time.monotonic()
    except KeyboardInterrupt:
        stop_event.set()
        print("\nStopping. Final tally:")
        cmd_stats(args, conn=conn)
    finally:
        stop_event.set()
        try:
            _flush_health(conn, cap_health, include_current=True, quiet=True)
        except Exception:  # noqa: BLE001 - never mask the real exit reason
            pass
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
        if args.json:
            import dashboard  # imported here so other commands don't pay Flask's import cost

            print(json.dumps(dashboard.build_digest(conn, day, gap=args.gap, limit=args.limit)))
            return 0

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
    if args.db:
        dashboard._CFG["db"] = args.db
    dashboard.main(host=args.host, port=args.port, dev=args.dev)
    return 0


def cmd_cleanup(args) -> int:
    """Apply the retention policy (clips + segment wavs) and purge orphans.

    --dry-run prints what the clip policy would keep/delete per reason, touching nothing.
    """
    db_path, rec_dir = _db_paths(args)
    conn = storage.connect(db_path)
    try:
        _run_audio_cleanup(
            conn, rec_dir, args, label="cleanup",
            dry_run=args.dry_run, assume_clips_present=args.assume_clips_present,
        )
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

    p_mon = sub.add_parser("monitor", help="continuously record and identify bird sounds (streaming)")
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
        help="apply the retention policy: clips by tier/budget, expired segment wavs, orphans",
    )
    p_clean.add_argument("--db", help="SQLite database path (overrides config)")
    p_clean.add_argument("--dir", help="recordings directory (overrides config)")
    p_clean.add_argument(
        "--retention-days", type=int,
        help="days to keep segment wav files (0 = forever; overrides config)",
    )
    p_clean.add_argument(
        "--dry-run", action="store_true",
        help="print counts and bytes per retention reason; delete nothing",
    )
    p_clean.add_argument(
        "--assume-clips-present", action="store_true",
        help="with --dry-run: don't stat clip files (for a DB pulled without its audio)",
    )
    p_clean.set_defaults(func=cmd_cleanup)

    p_stats = sub.add_parser("stats", help="show running results from the database")
    p_stats.add_argument("--db", help="SQLite database path (overrides config)")
    p_stats.set_defaults(func=cmd_stats)

    p_dig = sub.add_parser("digest", help="daily summary: species, timing, new birds")
    p_dig.add_argument("--date", help="day as YYYY-MM-DD (default: today)")
    p_dig.add_argument("--db", help="SQLite database path (overrides config)")
    p_dig.add_argument(
        "--json", action="store_true",
        help="print the same JSON as GET /api/digest instead of plain text",
    )
    p_dig.add_argument(
        "--gap", type=int, default=14,
        help="days absent before a species counts as 'returning' (--json only, default: 14)",
    )
    p_dig.add_argument(
        "--limit", type=int, default=10,
        help="max top detections to include (--json only, default: 10)",
    )
    p_dig.set_defaults(func=cmd_digest)

    p_dash = sub.add_parser("dashboard", help="launch the local web dashboard")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", type=int, default=8080)
    p_dash.add_argument("--db", help="SQLite database path (overrides config)")
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
