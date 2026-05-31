"""Tests for identifier.summarize()."""

from __future__ import annotations

import inspect

import identifier


def _det(name: str, conf: float, start: float, end: float) -> identifier.Detection:
    return identifier.Detection(name, f"Genus {name}", conf, start, end)


def test_summarize_groups_by_species():
    detections = [
        _det("Wren", 0.9, 0, 3),
        _det("Wren", 0.8, 3, 6),
        _det("Titmouse", 0.7, 0, 3),
    ]
    rows = identifier.summarize(detections)
    assert len(rows) == 2
    assert rows[0].common_name == "Wren"
    assert rows[0].count == 2
    assert rows[0].max_confidence == 0.9
    assert rows[0].first_time == 0
    assert rows[0].last_time == 6


def test_summarize_empty():
    assert identifier.summarize([]) == []


def test_identify_default_min_conf_matches_config():
    """identifier keeps its own default (no config import) but must match DEFAULTS."""
    import config

    sig = inspect.signature(identifier.identify)
    assert sig.parameters["min_conf"].default == config.DEFAULTS["min_conf"]
