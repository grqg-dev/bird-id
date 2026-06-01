"""Tests for birdid.py CLI helpers."""

from __future__ import annotations

from types import SimpleNamespace

import birdid
import config
import recorder


def test_resolve_id_params_uses_config_defaults():
    args = SimpleNamespace(
        min_conf=None,
        lat=None,
        lon=None,
        cfg=dict(config.DEFAULTS),
    )
    min_conf, lat, lon, loc = birdid._resolve_id_params(args)
    assert min_conf == config.DEFAULTS["min_conf"]
    assert lat is None
    assert loc == ""


def test_resolve_id_params_flag_overrides():
    args = SimpleNamespace(
        min_conf=0.9,
        lat=34.0,
        lon=-120.0,
        cfg=config.load(),
    )
    min_conf, lat, lon, loc = birdid._resolve_id_params(args)
    assert min_conf == 0.9
    assert "location filter" in loc


def test_progress_bar():
    assert birdid._progress_bar(0.0) == "--------------------"
    assert birdid._progress_bar(0.5) == "##########----------"
    assert birdid._progress_bar(1.0) == "####################"


def test_expected_wav_bytes():
    assert recorder.expected_wav_bytes(1.0) == 44 + 48_000 * 2


def test_cmd_stats_with_injected_conn(seeded_conn, capsys):
    args = SimpleNamespace(db=None, cfg=config.load())
    rc = birdid.cmd_stats(args, conn=seeded_conn)
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 segments" in out
    assert "Bewick" in out


def test_cmd_stats_empty_db(db_conn, capsys):
    args = SimpleNamespace(db=None, cfg={"db": ":memory:"})
    rc = birdid.cmd_stats(args, conn=db_conn)
    out = capsys.readouterr().out
    assert rc == 0
    assert "No data yet" in out


def test_cmd_cleanup_orphans(tmp_path, capsys):
    import os
    import time

    rec_dir = tmp_path / "recordings"
    rec_dir.mkdir()
    orphan = rec_dir / "orphan.wav"
    orphan.write_bytes(b"x" * 100)
    old_mtime = time.time() - 3600
    os.utime(orphan, (old_mtime, old_mtime))
    db_path = tmp_path / "birdid.db"
    cfg = {
        "db": str(db_path),
        "recordings_dir": str(rec_dir),
        "retention_days": 0,
    }
    args = SimpleNamespace(db=None, dir=None, retention_days=None, cfg=cfg)
    rc = birdid.cmd_cleanup(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "removed 1 orphan" in out
    assert not orphan.exists()
