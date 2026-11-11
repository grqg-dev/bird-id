"""CaptureHealth accounting, capture_health storage, and the history backfill."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import health
import recorder
import storage

SR = 48_000
HOP = np.full(4800, 1000, dtype=np.int16)   # 0.1 s, about -30 dBFS
DEAD = np.zeros(4800, dtype=np.int16)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_tally_and_drain_by_hour():
    clock = Clock(datetime(2026, 11, 11, 14, 59, 0))
    h = health.CaptureHealth(SR, now=clock)
    list(h.tap([HOP, HOP, DEAD, recorder.Respawn(clock.t, reason="stall"),
                recorder.Respawn(clock.t, reason="exit")]))
    clock.t = datetime(2026, 11, 11, 15, 0, 5)
    list(h.tap([HOP]))

    rows = h.drain()                      # only finished hours
    assert [r.hour for r in rows] == ["2026-11-11T14:00:00"]
    r = rows[0]
    assert abs(r.audio_seconds - 0.3) < 1e-9
    assert (r.stalls, r.respawns) == (1, 2)
    assert abs(r.dead_seconds - 0.1) < 1e-9
    assert -60 < r.mean_dbfs < -40       # mean of -30, -30, -90
    assert h.drain() == []
    assert [x.hour for x in h.drain(include_current=True)] == ["2026-11-11T15:00:00"]


def test_format_line():
    st = health.HourStats(hour="2026-11-11T14:00:00", audio_seconds=3540, stalls=2,
                          dbfs_sum=-47.0 * 10, hops=10)
    assert health.format_line(st) == "[health] 14:00 audio 3540s/3600s, stalls 2, mean -47 dBFS"


def test_migration_creates_table(db_conn):
    cols = {r[1] for r in db_conn.execute("PRAGMA table_info(capture_health)")}
    assert cols == {"hour", "audio_seconds", "stalls", "respawns", "mean_dbfs", "dead_seconds"}


def test_upsert_is_additive(db_conn):
    a = health.HourStats("2026-11-11T14:00:00", 100.0, 1, 1, 10.0, 10, ) 
    a.dbfs_sum, a.hops = -50.0 * 10, 10
    b = health.HourStats("2026-11-11T14:00:00", 300.0, 2, 3, 0.0, 30)
    b.dbfs_sum, b.hops = -30.0 * 30, 30
    storage.upsert_capture_health(db_conn, [a])
    storage.upsert_capture_health(db_conn, [b])
    r = db_conn.execute("SELECT * FROM capture_health").fetchone()
    assert r["audio_seconds"] == 400.0
    assert (r["stalls"], r["respawns"]) == (3, 4)
    assert abs(r["dead_seconds"] - 10.0) < 1e-9
    assert abs(r["mean_dbfs"] - (-35.0)) < 1e-9   # (-50*100 + -30*300)/400


def _backfill_module():
    path = Path(__file__).resolve().parent.parent / "scripts" / "backfill_capture_health.py"
    spec = importlib.util.spec_from_file_location("backfill_capture_health", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_derives_hours_and_is_idempotent(db_conn):
    t = datetime(2026, 8, 20, 9, 0, 0)
    for i in range(3):   # three 10 s segments in hour 09
        db_conn.execute(
            "INSERT INTO segments (started_at, ended_at, duration, mean_dbfs) VALUES (?,?,?,?)",
            ((t + timedelta(minutes=i)).isoformat(), t.isoformat(), 10.0, -50.0 + 10 * i),
        )
    db_conn.execute(
        "INSERT INTO segments (started_at, ended_at, duration, mean_dbfs) VALUES (?,?,?,?)",
        ((t + timedelta(hours=1)).isoformat(), t.isoformat(), 6.0, -40.0),
    )
    db_conn.commit()
    bf = _backfill_module()

    assert bf.backfill(db_conn, 1.0, dry_run=True) == (2, 2)
    assert db_conn.execute("SELECT COUNT(*) FROM capture_health").fetchone()[0] == 0

    assert bf.backfill(db_conn, 1.0) == (2, 2)
    assert bf.backfill(db_conn, 1.0) == (2, 0)
    r = db_conn.execute(
        "SELECT * FROM capture_health WHERE hour = '2026-08-20T09:00:00'"
    ).fetchone()
    assert r["audio_seconds"] == 27.0          # 3 * (10 - 1)
    assert abs(r["mean_dbfs"] - (-40.0)) < 1e-9
    assert r["stalls"] is None and r["respawns"] is None and r["dead_seconds"] is None
