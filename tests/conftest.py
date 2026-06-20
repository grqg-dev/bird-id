"""Shared fixtures for bird-id tests."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import identifier
import storage


@pytest.fixture
def db_conn(tmp_path):
    """Empty SQLite database with schema applied."""
    path = tmp_path / "test.db"
    conn = storage.connect(path)
    yield conn
    conn.close()


@pytest.fixture
def seeded_conn(db_conn):
    """Database with one segment and two species on a fixed day."""
    day = datetime(2026, 5, 30, 8, 0, 0)
    detections = [
        identifier.Detection(
            "Bewick's Wren",
            "Thryomanes bewickii",
            0.92,
            0.0,
            3.0,
        ),
        identifier.Detection(
            "Oak Titmouse",
            "Baeolophus inornatus",
            0.75,
            3.0,
            6.0,
        ),
    ]
    storage.record_segment(
        db_conn,
        started_at=day,
        ended_at=day + timedelta(seconds=6),
        duration=6.0,
        detections=detections,
        wav_path="/tmp/seg1.wav",
    )
    return db_conn


@pytest.fixture
def client(seeded_conn, monkeypatch):
    """Flask test client backed by the seeded DB (fresh connection per call)."""
    import dashboard

    db_path = next(
        r["file"]
        for r in seeded_conn.execute("PRAGMA database_list").fetchall()
        if r["name"] == "main"
    )

    def _fresh_db():
        return storage.connect(db_path)

    monkeypatch.setattr(dashboard, "_db", _fresh_db)
    monkeypatch.setitem(dashboard._CFG, "min_conf", 0.3)
    monkeypatch.setitem(dashboard._CFG, "lat", 34.42)
    monkeypatch.setitem(dashboard._CFG, "lon", -119.70)
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()
