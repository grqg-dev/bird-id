"""Tests for storage.py."""

from __future__ import annotations

from datetime import datetime, timedelta

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
