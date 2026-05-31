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
