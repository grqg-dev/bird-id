"""Tests for /api/dash/health and the banner conditions."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import dashboard

NOW = datetime(2026, 11, 11, 12, 30, 0)


def _hours(conn, end: datetime, n: int, *, audio=3600.0, dbfs=-45.0, skip=()):
    """Insert `n` hourly capture_health rows ending at the hour of `end`."""
    top = end.replace(minute=0, second=0, microsecond=0)
    for i in range(n):
        h = top - timedelta(hours=i)
        if i in skip:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO capture_health(hour, audio_seconds, mean_dbfs) VALUES (?,?,?)",
            (h.isoformat(timespec="seconds"), audio, dbfs),
        )
    conn.commit()


def _segment(conn, when: datetime):
    conn.execute(
        "INSERT INTO segments(started_at, ended_at, duration) VALUES (?,?,?)",
        (when.isoformat(timespec="seconds"), when.isoformat(timespec="seconds"), 10.0),
    )
    conn.commit()


def test_healthy_capture_has_no_alerts(db_conn):
    _hours(db_conn, NOW, 24 * 5)
    _segment(db_conn, NOW - timedelta(minutes=2))
    out = dashboard.build_dash_health(db_conn, now=NOW)
    assert out["alerts"] == []
    assert out["last_24h"]["coverage"] >= 0.95
    assert out["last_30d"]["median_dbfs"] == -45.0


def test_low_coverage_banner_includes_hours(db_conn):
    # 6-hour hole inside the last 24 h
    _hours(db_conn, NOW, 24 * 5, skip=range(2, 8))
    _segment(db_conn, NOW - timedelta(minutes=1))
    out = dashboard.build_dash_health(db_conn, now=NOW)
    assert out["last_24h"]["coverage"] < 0.9
    assert out["alerts"][0].startswith("Mic captured ")
    assert " of the last 24 hours" in out["alerts"][0]
    assert "18.0" in out["alerts"][0]  # 24 hour rows minus the 6-hour hole


def test_stale_segment_banner(db_conn):
    _hours(db_conn, NOW, 24 * 3)
    _segment(db_conn, NOW - timedelta(minutes=45))
    out = dashboard.build_dash_health(db_conn, now=NOW)
    assert any(a.startswith("No new audio for 45 minutes") for a in out["alerts"])
    _segment(db_conn, NOW - timedelta(minutes=9))
    out = dashboard.build_dash_health(db_conn, now=NOW)
    assert not any(a.startswith("No new audio") for a in out["alerts"])


def test_level_drop_banner(db_conn):
    # 29 days at -45 dBFS, then the last 24 h at -53
    _hours(db_conn, NOW - timedelta(hours=24), 24 * 29, dbfs=-45.0)
    _hours(db_conn, NOW, 24, dbfs=-53.0)
    _segment(db_conn, NOW - timedelta(minutes=1))
    out = dashboard.build_dash_health(db_conn, now=NOW)
    msgs = [a for a in out["alerts"] if "dB below" in a]
    assert msgs and "8.0 dB below" in msgs[0]


def test_small_level_drop_is_not_flagged(db_conn):
    _hours(db_conn, NOW - timedelta(hours=24), 24 * 29, dbfs=-45.0)
    _hours(db_conn, NOW, 24, dbfs=-49.0)
    _segment(db_conn, NOW - timedelta(minutes=1))
    assert dashboard.build_dash_health(db_conn, now=NOW)["alerts"] == []


def test_empty_capture_health_means_unknown_not_deaf(db_conn):
    out = dashboard.build_dash_health(db_conn, now=NOW)
    assert out["alerts"] == [] and out["last_24h"] is None and out["hourly_seconds"] == {}


def test_history_before_first_row_is_not_counted_as_missing(db_conn):
    _hours(db_conn, NOW, 6)  # only 6 h of history exist
    _segment(db_conn, NOW - timedelta(minutes=1))
    out = dashboard.build_dash_health(db_conn, now=NOW)
    assert out["last_24h"]["expected_hours"] < 7
    assert out["alerts"] == []


def test_hourly_seconds_grid(db_conn):
    _hours(db_conn, NOW, 3, audio=1800.0)
    out = dashboard.build_dash_health(db_conn, now=NOW)
    day = out["hourly_seconds"]["2026-11-11"]
    assert len(day) == 24 and day[12] == 1800.0 and day[11] == 1800.0 and day[3] == 0.0


def test_health_route(client, monkeypatch):
    monkeypatch.setattr(dashboard, "_now", lambda: NOW)
    resp = client.get("/api/dash/health")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-cache"
    data = resp.get_json()
    assert set(data) >= {"last_24h", "last_30d", "hourly_seconds", "alerts", "newest_segment_at"}
