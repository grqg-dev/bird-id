"""Tests for birdid.py CLI helpers."""

from __future__ import annotations

from types import SimpleNamespace

import birdid
import config


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
