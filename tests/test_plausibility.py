"""Tests for species plausibility (docs/yard_list.json) and the new-encounter rule."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import config
import dashboard
import identifier
import storage

DAY = datetime(2026, 6, 2, 7, 0, 0)


def _add(conn, name: str, conf: float, when: datetime, sci: str | None = None):
    storage.record_segment(
        conn,
        started_at=when,
        ended_at=when + timedelta(seconds=3),
        duration=3.0,
        detections=[identifier.Detection(name, sci or name, conf, 0.0, 3.0)],
        wav_path=None,
    )


@pytest.fixture
def mixed(seeded_conn, monkeypatch):
    """Seeded DB plus two unlikely species and encounter-rule probes."""
    monkeypatch.setattr(dashboard, "_CACHE", {})
    _add(seeded_conn, "Northern Fulmar", 0.8, DAY)
    _add(seeded_conn, "Brant", 0.6, DAY + timedelta(hours=1))
    _add(seeded_conn, "Stray Low", 0.4, DAY)  # one weak detection: not an encounter yet
    _add(seeded_conn, "Strong Once", 0.75, DAY)  # one 0.7+ detection: encounter
    _add(seeded_conn, "Two Days Low", 0.4, DAY)
    _add(seeded_conn, "Two Days Low", 0.4, DAY + timedelta(days=2, hours=1))
    return seeded_conn


def _names(data):
    return [s["name"] for s in data["species"]]


def test_summary_hides_unlikely_by_default(client, mixed):
    data = client.get("/api/dash/summary").get_json()
    names = _names(data)
    assert "Northern Fulmar" not in names and "Brant" not in names
    assert "Bewick's Wren" in names
    assert data["meta"]["unlikely_hidden"] == 2
    assert data["meta"]["include_unlikely"] is False
    # cube only references species that are present
    assert all(row[2] < len(names) for row in data["cube"])


def test_summary_toggle_includes_unlikely(client, mixed):
    data = client.get("/api/dash/summary?unlikely=1").get_json()
    by_name = {s["name"]: s for s in data["species"]}
    assert by_name["Northern Fulmar"]["status"] == "unlikely"
    assert by_name["Brant"]["status"] == "unlikely"
    assert data["meta"]["include_unlikely"] is True
    assert data["meta"]["unlikely_hidden"] == 0 and data["meta"]["unlikely_total"] == 2
    fulmar_idx = _names(data).index("Northern Fulmar")
    assert any(row[2] == fulmar_idx for row in data["cube"])


def test_hiding_never_touches_stored_detections(client, mixed):
    before = mixed.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
    client.get("/api/dash/summary")
    client.get("/api/dash/sure")
    after = mixed.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
    assert before == after
    assert mixed.execute(
        "SELECT COUNT(*) FROM detections WHERE common_name='Northern Fulmar'"
    ).fetchone()[0] == 1


def test_sure_calls_hide_unlikely(client, mixed):
    _add(mixed, "Northern Fulmar", 0.95, DAY + timedelta(hours=2))
    hidden = client.get("/api/dash/sure").get_json()
    assert "Northern Fulmar" not in _names(hidden)
    assert hidden["meta"]["unlikely_hidden"] == 1
    shown = client.get("/api/dash/sure?unlikely=1").get_json()
    assert "Northern Fulmar" in _names(shown)


def test_digest_hides_unlikely(client, mixed):
    day = DAY.date().isoformat()
    hidden = client.get(f"/api/digest?day={day}").get_json()
    assert "Northern Fulmar" not in [s["common_name"] for s in hidden["species"]]
    assert hidden["unlikely_hidden"] == 2
    shown = client.get(f"/api/digest?day={day}&unlikely=1").get_json()
    assert "Northern Fulmar" in [s["common_name"] for s in shown["species"]]


def test_new_encounter_rule(client, mixed):
    data = client.get("/api/dash/summary").get_json()
    by = {s["name"]: s for s in data["species"]}
    assert by["Stray Low"]["encounter"] is None
    assert by["Strong Once"]["encounter"] == by["Strong Once"]["first"]
    two = by["Two Days Low"]
    assert two["days"] == 2
    assert two["encounter"].startswith("2026-06-04")  # the second day it was heard
    # the original 0.92 Bewick's Wren qualifies on its first detection
    assert by["Bewick's Wren"]["encounter"] == by["Bewick's Wren"]["first"]


def test_unlisted_species_default_to_possible():
    assert dashboard._yard_status("Totally Unlisted Bird") == "possible"


def test_custom_yard_list_is_used(client, mixed, monkeypatch):
    import sys

    app_mod = sys.modules["dashboard.app"]  # `dashboard.app` attribute is the Flask object

    monkeypatch.setattr(app_mod, "_YARD_LIST_CACHE", {"Oak Titmouse": {"status": "unlikely"}})
    data = client.get("/api/dash/summary").get_json()
    assert "Oak Titmouse" not in _names(data)
    assert "Northern Fulmar" in _names(data)  # not in the custom list → possible


def test_yard_list_file_is_valid_and_seeded():
    path = Path(__file__).resolve().parent.parent / "docs" / "yard_list.json"
    doc = json.loads(path.read_text())
    species = doc["species"]
    assert len(species) >= 100
    assert {v["status"] for v in species.values()} <= {"expected", "possible", "unlikely"}
    assert species["Northern Fulmar"]["status"] == "unlikely"
    assert species["Brant"]["status"] == "unlikely"
    assert species["American Crow"]["status"] == "expected"
    # sanity: every plausible backyard regular stays visible
    for name in ("Oak Titmouse", "Anna's Hummingbird", "House Finch", "Bushtit"):
        assert species[name]["status"] != "unlikely"


def test_location_filter_threshold_config_key():
    assert config.DEFAULTS["location_filter_threshold"] is None  # birdnetlib default unchanged
    import inspect

    sig = inspect.signature(identifier.identify)
    assert sig.parameters["filter_threshold"].default is None
    assert sig.parameters["min_conf"].default == 0.3  # min_conf untouched


def test_birdnetlib_exposes_filter_threshold():
    birdnetlib = pytest.importorskip("birdnetlib")
    import inspect

    assert "filter_threshold" in inspect.signature(birdnetlib.Recording.__init__).parameters
