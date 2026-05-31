"""Tests for config.py."""

from __future__ import annotations

import json

import pytest

import config


def test_defaults_include_min_conf():
    assert config.DEFAULTS["min_conf"] == 0.3


def test_load_merges_over_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"min_conf": 0.5, "db": "custom.db"}))
    cfg = config.load(path)
    assert cfg["min_conf"] == 0.5
    assert cfg["db"] == "custom.db"
    assert cfg["recordings_dir"] == config.DEFAULTS["recordings_dir"]


def test_load_missing_file_uses_defaults(tmp_path):
    cfg = config.load(tmp_path / "missing.json")
    assert cfg == config.DEFAULTS


def test_load_invalid_json_exits(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    with pytest.raises(SystemExit):
        config.load(path)


def test_resolve_prefers_flag():
    cfg = {"min_conf": 0.3, "lat": 1.0}
    assert config.resolve(0.5, "min_conf", cfg) == 0.5
    assert config.resolve(None, "min_conf", cfg) == 0.3
    assert config.resolve(None, "lon", cfg) is None
