"""Tests for storage.py."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

import identifier
import storage


def test_connect_creates_schema(db_conn):
    tables = {
        row[0]
        for row in db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert tables >= {"segments", "detections", "tracks"}


def test_connect_uses_wal(db_conn):
    mode = db_conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_record_segment_returns_id_and_stores_detections(seeded_conn):
    t = storage.totals(seeded_conn)
    assert t["segments"] == 1
    assert t["detections"] == 2
    assert t["species"] == 2


def test_species_summary(seeded_conn):
    rows = storage.species_summary(seeded_conn)
    assert len(rows) == 2
    assert rows[0]["common_name"] == "Bewick's Wren"
    assert rows[0]["peak_conf"] == pytest.approx(0.92)


def test_day_species(seeded_conn):
    rows = storage.day_species(seeded_conn, "2026-05-30")
    assert len(rows) == 2
    assert storage.day_species(seeded_conn, "2026-01-01") == []


def test_timeline_occurrences(seeded_conn):
    rows = storage.timeline_occurrences(seeded_conn, "2026-05-30")
    assert len(rows) == 2
    assert rows[0]["common_name"] == "Bewick's Wren"


def test_timeline_by_day(seeded_conn):
    rows = storage.timeline_by_day(seeded_conn)
    assert len(rows) == 1
    assert rows[0]["day"] == "2026-05-30"
    assert rows[0]["n"] == 2
    assert rows[0]["species"] == 2


def test_day_overview(seeded_conn):
    ov = storage.day_overview(seeded_conn, "2026-05-30")
    assert ov["detections"] == 2
    assert ov["species"] == 2


def test_new_species_on(seeded_conn):
    assert storage.new_species_on(seeded_conn, "2026-05-30") == [
        "Bewick's Wren",
        "Oak Titmouse",
    ]
    assert storage.new_species_on(seeded_conn, "2026-01-01") == []


def test_totals(seeded_conn):
    t = storage.totals(seeded_conn)
    assert t["segments"] == 1
    assert t["detections"] == 2
    assert t["species"] == 2


def test_longest_heard_streak():
    base = datetime(2026, 5, 30, 8, 0, 0)
    times = [
        (base + timedelta(seconds=i * 5)).isoformat(timespec="seconds")
        for i in range(4)
    ]
    assert storage.longest_heard_streak(times) == 4
    assert storage.longest_heard_streak([]) == 0


def test_species_dex_day(seeded_conn):
    rows = storage.species_dex_day(seeded_conn, "2026-05-30")
    assert len(rows) == 2
    assert rows[0]["common_name"] == "Bewick's Wren"


def test_expire_segment_audio_drops_old_wav(db_conn, tmp_path):
    wav_old = tmp_path / "old.wav"
    wav_new = tmp_path / "new.wav"
    wav_old.write_bytes(b"x" * 1000)
    wav_new.write_bytes(b"y" * 2000)

    old_start = datetime(2026, 1, 1, 8, 0, 0)
    new_start = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=old_start,
        ended_at=old_start + timedelta(seconds=60),
        duration=60.0,
        detections=[],
        wav_path=str(wav_old),
    )
    storage.record_segment(
        db_conn,
        started_at=new_start,
        ended_at=new_start + timedelta(seconds=60),
        duration=60.0,
        detections=[],
        wav_path=str(wav_new),
    )

    n, freed = storage.expire_segment_audio(
        db_conn, retention_days=30, now=datetime(2026, 5, 31, 12, 0, 0)
    )
    assert n == 1
    assert freed == 1000
    assert not wav_old.exists()
    assert wav_new.exists()
    row = db_conn.execute(
        "SELECT wav_path FROM segments WHERE wav_path IS NOT NULL"
    ).fetchone()
    assert row["wav_path"] == str(wav_new)


def test_expire_segment_audio_disabled(db_conn, tmp_path):
    wav = tmp_path / "keep.wav"
    wav.write_bytes(b"z" * 500)
    started = datetime(2026, 1, 1, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[],
        wav_path=str(wav),
    )
    n, freed = storage.expire_segment_audio(
        db_conn, retention_days=0, now=datetime(2026, 5, 31, 12, 0, 0)
    )
    assert n == 0
    assert freed == 0
    assert wav.exists()


def test_purge_orphan_recordings(db_conn, tmp_path):
    import os
    import time

    kept = tmp_path / "kept.wav"
    orphan = tmp_path / "orphan.wav"
    kept.write_bytes(b"a" * 100)
    orphan.write_bytes(b"b" * 400)
    old_mtime = time.time() - 3600
    os.utime(orphan, (old_mtime, old_mtime))
    started = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[],
        wav_path=str(kept),
    )
    removed, freed = storage.purge_orphan_recordings(db_conn, tmp_path)
    assert removed == 1
    assert freed == 400
    assert kept.exists()
    assert not orphan.exists()


def test_purge_orphan_recordings_skips_recent(db_conn, tmp_path):
    kept = tmp_path / "kept.wav"
    recent = tmp_path / "recording.wav"
    old = tmp_path / "old.wav"
    kept.write_bytes(b"a" * 100)
    recent.write_bytes(b"b" * 200)
    old.write_bytes(b"c" * 300)
    started = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[],
        wav_path=str(kept),
    )
    import os
    import time

    old_mtime = time.time() - 3600
    os.utime(old, (old_mtime, old_mtime))
    removed, _ = storage.purge_orphan_recordings(db_conn, tmp_path, min_age_sec=600)
    assert removed == 1
    assert recent.exists()
    assert not old.exists()


def _record_with_detections(db_conn, started, wav_path, detections):
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=detections,
        wav_path=wav_path,
    )


def test_recent_feed_orders_newest_first(db_conn, tmp_path):
    wav = str(tmp_path / "seg.wav")
    Path(wav).write_bytes(b"x")
    now = datetime(2026, 6, 1, 14, 0, 0)
    older = now - timedelta(hours=2)
    newer = now - timedelta(minutes=30)

    _record_with_detections(
        db_conn,
        older,
        wav,
        [
            identifier.Detection("Bewick's Wren", "Thryomanes bewickii", 0.92, 0.0, 3.0),
        ],
    )
    _record_with_detections(
        db_conn,
        newer,
        wav,
        [
            identifier.Detection("Oak Titmouse", "Baeolophus inornatus", 0.75, 0.0, 3.0),
        ],
    )

    rows = storage.recent_feed(db_conn, now=now)
    assert len(rows) == 2
    assert rows[0]["common_name"] == "Oak Titmouse"
    assert rows[1]["common_name"] == "Bewick's Wren"


def test_recent_feed_24h_cutoff(db_conn, tmp_path):
    wav = str(tmp_path / "seg.wav")
    Path(wav).write_bytes(b"x")
    now = datetime(2026, 6, 1, 14, 0, 0)
    recent = now - timedelta(hours=23)
    stale = now - timedelta(hours=25)

    _record_with_detections(
        db_conn,
        stale,
        wav,
        [identifier.Detection("Old Bird", "Old sp", 0.9, 0.0, 3.0)],
    )
    _record_with_detections(
        db_conn,
        recent,
        wav,
        [identifier.Detection("New Bird", "New sp", 0.9, 0.0, 3.0)],
    )

    rows = storage.recent_feed(db_conn, now=now)
    assert len(rows) == 1
    assert rows[0]["common_name"] == "New Bird"


def test_recent_feed_since_filter(db_conn, tmp_path):
    wav = str(tmp_path / "seg.wav")
    Path(wav).write_bytes(b"x")
    now = datetime(2026, 6, 1, 14, 0, 0)
    t1 = now - timedelta(hours=1)
    t2 = now - timedelta(minutes=30)

    _record_with_detections(
        db_conn,
        t1,
        wav,
        [identifier.Detection("First", "Sp1", 0.9, 0.0, 3.0)],
    )
    _record_with_detections(
        db_conn,
        t2,
        wav,
        [identifier.Detection("Second", "Sp2", 0.8, 0.0, 3.0)],
    )

    since = (t1 + timedelta(seconds=30)).isoformat(timespec="seconds")
    rows = storage.recent_feed(db_conn, since=since, now=now)
    assert len(rows) == 1
    assert rows[0]["common_name"] == "Second"


def test_recent_feed_peak_clip_window(db_conn, tmp_path):
    wav = str(tmp_path / "seg.wav")
    Path(wav).write_bytes(b"x")
    now = datetime(2026, 6, 1, 14, 0, 0)
    started = now - timedelta(minutes=10)

    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[
            identifier.Detection("Bewick's Wren", "Thryomanes bewickii", 0.55, 0.0, 3.0),
            identifier.Detection("Bewick's Wren", "Thryomanes bewickii", 0.92, 6.0, 9.0),
        ],
        wav_path=wav,
    )

    rows = storage.recent_feed(db_conn, now=now)
    assert len(rows) == 1
    assert rows[0]["peak_conf"] == pytest.approx(0.92)
    assert rows[0]["start_time"] == pytest.approx(6.0)
    assert rows[0]["end_time"] == pytest.approx(9.0)


def test_record_segment_creates_tracks_and_track_ids(db_conn, tmp_path):
    wav = str(tmp_path / "seg.wav")
    Path(wav).write_bytes(b"x")
    started = datetime(2026, 5, 30, 8, 0, 0)
    detections = [
        identifier.Detection("A", "Sp a", 0.9, 0.0, 3.0),
        identifier.Detection("B", "Sp b", 0.8, 0.0, 3.0),
        identifier.Detection("C", "Sp c", 0.7, 6.0, 9.0),
    ]
    seg_id = storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=detections,
        wav_path=wav,
    )
    tracks = storage.tracks_for_segment(db_conn, seg_id)
    assert len(tracks) == 2
    dets = db_conn.execute(
        "SELECT track_id, start_time, end_time FROM detections WHERE segment_id = ?",
        (seg_id,),
    ).fetchall()
    assert len(dets) == 3
    assert all(r["track_id"] is not None for r in dets)
    assert {r["track_id"] for r in dets if r["start_time"] == 0.0} == {
        tracks[0]["id"]
    }


def test_record_segment_clip_paths(db_conn, tmp_path):
    wav = str(tmp_path / "seg.wav")
    clip = tmp_path / "clip_0_3000.wav"
    clip.write_bytes(b"clip")
    started = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[
            identifier.Detection("A", "Sp a", 0.9, 0.0, 3.0),
        ],
        wav_path=wav,
        clip_paths={(0.0, 3.0): str(clip)},
    )
    track = storage.get_track(db_conn, 1, 0.0, 3.0)
    assert track["clip_path"] == str(clip)


def test_expire_track_clips(db_conn, tmp_path):
    clip_old = tmp_path / "old_clip.wav"
    clip_new = tmp_path / "new_clip.wav"
    clip_old.write_bytes(b"x" * 100)
    clip_new.write_bytes(b"y" * 200)
    old_start = datetime(2026, 1, 1, 8, 0, 0)
    new_start = datetime(2026, 5, 30, 8, 0, 0)
    for started, clip in ((old_start, clip_old), (new_start, clip_new)):
        storage.record_segment(
            db_conn,
            started_at=started,
            ended_at=started + timedelta(seconds=60),
            duration=60.0,
            detections=[
                identifier.Detection("Bird", "Sp", 0.9, 0.0, 3.0),
            ],
            wav_path=str(tmp_path / f"seg_{started.day}.wav"),
            clip_paths={(0.0, 3.0): str(clip)},
        )

    n, freed = storage.expire_track_clips(
        db_conn, retention_days=30, now=datetime(2026, 5, 31, 12, 0, 0)
    )
    assert n == 1
    assert freed == 100
    assert not clip_old.exists()
    assert clip_new.exists()
    row = db_conn.execute(
        "SELECT clip_path FROM tracks WHERE clip_path IS NOT NULL"
    ).fetchone()
    assert row["clip_path"] == str(clip_new)


def test_expire_track_clips_disabled(db_conn, tmp_path):
    clip = tmp_path / "keep_clip.wav"
    clip.write_bytes(b"z" * 50)
    started = datetime(2026, 1, 1, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[identifier.Detection("Bird", "Sp", 0.9, 0.0, 3.0)],
        clip_paths={(0.0, 3.0): str(clip)},
    )
    n, freed = storage.expire_track_clips(
        db_conn, retention_days=0, now=datetime(2026, 5, 31, 12, 0, 0)
    )
    assert n == 0
    assert freed == 0
    assert clip.exists()


def test_expire_track_clips_high_confidence_exemption(db_conn, tmp_path):
    clip_weak = tmp_path / "weak_clip.wav"
    clip_strong = tmp_path / "strong_clip.wav"
    clip_weak.write_bytes(b"x" * 100)
    clip_strong.write_bytes(b"y" * 200)
    started = datetime(2026, 1, 1, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[identifier.Detection("Weak", "Sp", 0.4, 0.0, 3.0)],
        wav_path=str(tmp_path / "seg_weak.wav"),
        clip_paths={(0.0, 3.0): str(clip_weak)},
    )
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[identifier.Detection("Strong", "Sp", 0.99, 0.0, 3.0)],
        wav_path=str(tmp_path / "seg_strong.wav"),
        clip_paths={(0.0, 3.0): str(clip_strong)},
    )

    # 60 days out: past the normal 30-day retention, but short of the 90-day
    # high-confidence floor. The weak clip is expired; the strong one is kept.
    n, freed = storage.expire_track_clips(
        db_conn,
        retention_days=30,
        now=datetime(2026, 3, 2, 12, 0, 0),
        high_conf_threshold=0.9,
        high_conf_retention_days=90,
    )
    assert n == 1
    assert freed == 100
    assert not clip_weak.exists()
    assert clip_strong.exists()

    # Past the 90-day floor too: the strong clip is finally expired.
    n, freed = storage.expire_track_clips(
        db_conn,
        retention_days=30,
        now=datetime(2026, 4, 5, 12, 0, 0),
        high_conf_threshold=0.9,
        high_conf_retention_days=90,
    )
    assert n == 1
    assert freed == 200
    assert not clip_strong.exists()


def test_purge_orphan_clips(db_conn, tmp_path):
    import os
    import time

    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    kept = clips_dir / "kept.wav"
    orphan = clips_dir / "orphan.wav"
    kept.write_bytes(b"a" * 100)
    orphan.write_bytes(b"b" * 400)
    old_mtime = time.time() - 3600
    os.utime(orphan, (old_mtime, old_mtime))
    started = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[identifier.Detection("Bird", "Sp", 0.9, 0.0, 3.0)],
        clip_paths={(0.0, 3.0): str(kept)},
    )
    removed, freed = storage.purge_orphan_clips(db_conn, clips_dir)
    assert removed == 1
    assert freed == 400
    assert kept.exists()
    assert not orphan.exists()


def test_tracks_without_clip(db_conn, tmp_path):
    wav = str(tmp_path / "seg.wav")
    Path(wav).write_bytes(b"x")
    started = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[identifier.Detection("Bird", "Sp", 0.9, 0.0, 3.0)],
        wav_path=wav,
    )
    rows = storage.tracks_without_clip(db_conn)
    assert len(rows) == 1
    assert rows[0]["wav_path"] == wav


def test_set_track_clip_path(db_conn, tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"c")
    started = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[identifier.Detection("Bird", "Sp", 0.9, 0.0, 3.0)],
    )
    track = storage.get_track(db_conn, 1, 0.0, 3.0)
    storage.set_track_clip_path(db_conn, track["id"], str(clip))
    db_conn.commit()
    assert storage.get_track(db_conn, 1, 0.0, 3.0)["clip_path"] == str(clip)
    assert storage.tracks_without_clip(db_conn) == []


def test_migrate_backfills_tracks_and_track_id(tmp_path):
    """Legacy DB without tracks gets upgraded on connect()."""
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE segments (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            duration REAL NOT NULL,
            wav_path TEXT,
            mean_dbfs REAL,
            max_dbfs REAL,
            num_detections INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE detections (
            id INTEGER PRIMARY KEY,
            segment_id INTEGER NOT NULL,
            common_name TEXT NOT NULL,
            scientific_name TEXT NOT NULL,
            confidence REAL NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL NOT NULL,
            heard_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO segments (started_at, ended_at, duration, num_detections) "
        "VALUES ('2026-05-30T08:00:00', '2026-05-30T08:01:00', 60, 1)"
    )
    conn.execute(
        "INSERT INTO detections (segment_id, common_name, scientific_name, "
        "confidence, start_time, end_time, heard_at) "
        "VALUES (1, 'Legacy Bird', 'Sp', 0.9, 0, 3, '2026-05-30T08:00:00')"
    )
    conn.commit()
    conn.close()

    upgraded = storage.connect(path)
    try:
        track = upgraded.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()
        assert track["n"] == 1
        det = upgraded.execute(
            "SELECT track_id FROM detections WHERE id = 1"
        ).fetchone()
        assert det["track_id"] is not None
    finally:
        upgraded.close()


def test_clear_segment_wav(db_conn, tmp_path):
    wav = tmp_path / "seg.wav"
    wav.write_bytes(b"wav" * 100)
    started = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[],
        wav_path=str(wav),
    )
    assert storage.clear_segment_wav(db_conn, 1) is True
    assert not wav.exists()
    row = db_conn.execute("SELECT wav_path FROM segments WHERE id = 1").fetchone()
    assert row["wav_path"] is None


def test_cleanup_dashboard_audio_drops_unprotected(db_conn, tmp_path):
    started = datetime(2026, 6, 5, 12, 0, 0)
    keep_wav = tmp_path / "keep.wav"
    drop_wav = tmp_path / "drop.wav"
    keep_wav.write_bytes(b"k" * 500)
    drop_wav.write_bytes(b"d" * 800)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[
            identifier.Detection("Keeper", "Sp k", 0.95, 0.0, 3.0),
        ],
        wav_path=str(keep_wav),
    )
    storage.record_segment(
        db_conn,
        started_at=started + timedelta(minutes=1),
        ended_at=started + timedelta(minutes=2),
        duration=60.0,
        detections=[],
        wav_path=str(drop_wav),
    )
    stats = storage.cleanup_dashboard_audio(
        db_conn, tmp_path, dry_run=False, live_hours=24
    )
    assert stats["segments_dropped"] == 1
    assert keep_wav.exists()
    assert not drop_wav.exists()
    row = db_conn.execute(
        "SELECT wav_path FROM segments WHERE id = 2"
    ).fetchone()
    assert row["wav_path"] is None


def test_recent_feed_skips_no_audio(db_conn, tmp_path):
    now = datetime(2026, 6, 1, 14, 0, 0)
    started = now - timedelta(minutes=5)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[
            identifier.Detection("Ghost Bird", "Sp", 0.9, 0.0, 3.0),
        ],
        wav_path=None,
    )
    assert storage.recent_feed(db_conn, now=now) == []


def test_recent_feed_includes_clip_without_segment_wav(db_conn, tmp_path):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"mp3")
    now = datetime(2026, 6, 1, 14, 0, 0)
    started = now - timedelta(minutes=5)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[
            identifier.Detection("Clip Bird", "Sp clip", 0.9, 0.0, 3.0),
        ],
        wav_path=None,
        clip_paths={(0.0, 3.0): str(clip)},
    )
    rows = storage.recent_feed(db_conn, now=now)
    assert len(rows) == 1
    assert rows[0]["common_name"] == "Clip Bird"
    assert rows[0]["has_audio"] == 1


def test_detection_feed_all_time(seeded_conn):
    rows = storage.detection_feed(seeded_conn, show_all=True, limit=10)
    assert len(rows) == 2


def test_detection_feed_day_scope(seeded_conn):
    rows = storage.detection_feed(seeded_conn, day="2026-05-30", show_all=False, limit=10)
    assert len(rows) == 2


def test_detection_feed_sort_by_confidence(seeded_conn):
    rows = storage.detection_feed(seeded_conn, show_all=True, sort="conf", limit=1)
    assert rows[0]["common_name"] == "Bewick's Wren"
    assert rows[0]["peak_conf"] == pytest.approx(0.92)


def test_species_detections_has_audio_from_clip(db_conn, tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"wav")
    started = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[
            identifier.Detection("Clipper", "Sp c", 0.9, 0.0, 3.0),
        ],
        wav_path=None,
        clip_paths={(0.0, 3.0): str(clip)},
    )
    rows = storage.species_detections(db_conn, "Clipper")
    assert len(rows) == 1
    assert rows[0]["has_audio"] == 1


def test_species_detections_excludes_discarded_audio(db_conn, tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"wav")
    started = datetime(2026, 5, 30, 8, 0, 0)
    storage.record_segment(
        db_conn,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        duration=60.0,
        detections=[
            identifier.Detection("HasAudio", "Sp a", 0.9, 0.0, 3.0),
            identifier.Detection("NoAudio", "Sp b", 0.95, 10.0, 13.0),
        ],
        wav_path=None,
        clip_paths={(0.0, 3.0): str(clip)},
    )
    # HasAudio's clip exists on disk; NoAudio's detection was recorded with no
    # clip_paths entry, so its track has clip_path = NULL and no segment wav.
    assert storage.species_detection_count(db_conn, "NoAudio") == 0
    assert storage.species_detections(db_conn, "NoAudio") == []
    assert storage.species_detection_count(db_conn, "HasAudio") == 1
    rows = storage.species_detections(db_conn, "HasAudio")
    assert len(rows) == 1
    assert rows[0]["has_audio"] == 1
