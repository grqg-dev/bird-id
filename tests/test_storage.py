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
    assert tables >= {"segments", "detections"}


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
