"""Tests for the multi-sensor device registry: storage helpers, the device_id
migration, and the dashboard /devices page + live-feed attribution badge."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

import dashboard
import identifier
import storage


# --- storage: device registry ---------------------------------------------

def test_register_device_upsert_is_idempotent(db_conn):
    first = storage.register_device(db_conn, "esp-1", name="Bench", location="Yard")
    second = storage.register_device(db_conn, "esp-1", location="Patio")
    assert first == second, "re-registering the same uid keeps the same id"
    dev = storage.get_device_by_uid(db_conn, "esp-1")
    assert dev["name"] == "Bench"       # preserved (COALESCE)
    assert dev["location"] == "Patio"   # updated


def test_touch_device_sets_last_seen_and_ip(db_conn):
    did = storage.register_device(db_conn, "esp-1")
    assert storage.get_device_by_uid(db_conn, "esp-1")["last_seen_at"] is None
    storage.touch_device(db_conn, did, ip="10.0.0.5")
    dev = storage.get_device_by_uid(db_conn, "esp-1")
    assert dev["last_seen_at"] is not None
    assert dev["last_ip"] == "10.0.0.5"


def _record_for_device(conn, device_id, *, names=("Bewick's Wren",)):
    t0 = datetime(2026, 6, 11, 12, 0, 0)
    dets = [identifier.Detection(n, "Sci name", 0.9, 0.0, 3.0) for n in names]
    return storage.record_segment(
        conn,
        started_at=t0,
        ended_at=t0 + timedelta(seconds=3),
        duration=3.0,
        detections=dets,
        wav_path="/tmp/s.wav",
        device_id=device_id,
    )


def test_list_devices_counts_attribution(db_conn):
    a = storage.register_device(db_conn, "esp-a", name="A")
    b = storage.register_device(db_conn, "esp-b", name="B")
    _record_for_device(db_conn, a, names=("Bewick's Wren", "Oak Titmouse"))
    _record_for_device(db_conn, b, names=("California Towhee",))
    by_uid = {d["device_uid"]: d for d in storage.list_devices(db_conn)}
    assert by_uid["esp-a"]["species"] == 2
    assert by_uid["esp-a"]["detections"] == 2
    assert by_uid["esp-a"]["segments"] == 1
    assert by_uid["esp-b"]["species"] == 1


def test_device_species_rollup(db_conn):
    did = storage.register_device(db_conn, "esp-1")
    _record_for_device(db_conn, did, names=("Bewick's Wren", "Bewick's Wren", "Oak Titmouse"))
    rows = storage.device_species(db_conn, did)
    counts = {r["common_name"]: r["windows"] for r in rows}
    assert counts["Bewick's Wren"] == 2
    assert counts["Oak Titmouse"] == 1


def test_record_segment_without_device_is_local(db_conn):
    _record_for_device(db_conn, None)
    row = db_conn.execute("SELECT device_id FROM segments").fetchone()
    assert row["device_id"] is None


# --- migration from a pre-device database ----------------------------------

def test_migration_adds_device_id_to_old_db(tmp_path):
    """Opening an older DB (no devices table, no segments.device_id) must add the
    column without disturbing existing rows, and create the devices table."""
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE segments (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL, duration REAL NOT NULL, wav_path TEXT,
            mean_dbfs REAL, max_dbfs REAL, num_detections INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE detections (id INTEGER PRIMARY KEY, segment_id INTEGER,
            common_name TEXT, scientific_name TEXT, confidence REAL,
            start_time REAL, end_time REAL, heard_at TEXT);
        CREATE TABLE tracks (id INTEGER PRIMARY KEY, segment_id INTEGER,
            start_time REAL, end_time REAL, duration REAL, clip_path TEXT, created_at TEXT);
        INSERT INTO segments (started_at, ended_at, duration, num_detections)
            VALUES ('2026-01-01T08:00:00', '2026-01-01T08:00:03', 3.0, 0);
        """
    )
    raw.commit()
    raw.close()

    conn = storage.connect(path)
    try:
        seg_cols = {r[1] for r in conn.execute("PRAGMA table_info(segments)")}
        assert "device_id" in seg_cols
        row = conn.execute("SELECT COUNT(*) n, device_id FROM segments").fetchone()
        assert row["n"] == 1 and row["device_id"] is None  # existing row preserved, local
        # devices table is usable
        storage.register_device(conn, "esp-1")
        assert storage.get_device_by_uid(conn, "esp-1") is not None
    finally:
        conn.close()


# --- dashboard: /devices page + live badge ---------------------------------

@pytest.fixture
def dash_client(tmp_path, monkeypatch):
    """Dashboard test client whose _db() opens a fresh connection each call (the
    routes close their connection, so a reused one would be unusable)."""
    db_path = tmp_path / "birdid.db"
    storage.connect(db_path).close()
    monkeypatch.setattr(dashboard, "_db", lambda: storage.connect(db_path))
    monkeypatch.setitem(dashboard._CFG, "db", str(db_path))
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client(), db_path


def test_devices_page_lists_registered_sensors(dash_client):
    client, db_path = dash_client
    conn = storage.connect(db_path)
    did = storage.register_device(conn, "esp-1", name="Oak feeder", location="Backyard")
    storage.touch_device(conn, did, ip="10.0.0.9")
    _record_for_device(conn, did, names=("California Towhee",))
    conn.close()

    html = client.get("/devices").get_data(as_text=True)
    assert "Oak feeder" in html
    assert "Backyard" in html
    assert "esp-1" in html
    assert "California Towhee" in html
    assert "10.0.0.9" in html


def test_devices_page_empty_state(dash_client):
    client, _ = dash_client
    assert "No sensors registered yet" in client.get("/devices").get_data(as_text=True)


def test_live_feed_shows_device_badge(dash_client):
    client, db_path = dash_client
    conn = storage.connect(db_path)
    did = storage.register_device(conn, "esp-1", name="Oak feeder")
    # recent_feed only includes recent + playable detections
    t0 = datetime.now() - timedelta(minutes=2)
    storage.record_segment(
        conn,
        started_at=t0,
        ended_at=t0 + timedelta(seconds=3),
        duration=3.0,
        detections=[identifier.Detection("California Towhee", "Melozone crissalis", 0.9, 0.0, 3.0)],
        wav_path="/tmp/s.wav",
        device_id=did,
    )
    conn.close()
    html = client.get("/live").get_data(as_text=True)
    assert "Oak feeder" in html
