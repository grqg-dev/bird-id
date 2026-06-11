"""Tests for the sensor ingest service (ingest.py).

Mic-free and TF-free: the BirdNET seam (ingest._identify) is stubbed, so these
exercise registration, device auth, the clip->store wiring, and — critically —
that a delayed upload is filed under the device's capture time, not receipt time.
"""

from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

import ingest
import storage


@dataclass
class FakeDetection:
    common_name: str = "Bewick's Wren"
    scientific_name: str = "Thryomanes bewickii"
    confidence: float = 0.92
    start_time: float = 0.0
    end_time: float = 3.0


def _wav_bytes(seconds: float = 3.0, sr: int = 48000) -> bytes:
    """A valid mono 16-bit PCM WAV of the given length (a quiet tone)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x01" * int(sr * seconds))
    return buf.getvalue()


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """Ingest Flask test client backed by a throwaway DB + recordings dir, with
    BirdNET stubbed to return one detection by default."""
    monkeypatch.setitem(ingest._CFG, "db", str(tmp_path / "birdid.db"))
    monkeypatch.setitem(ingest._CFG, "recordings_dir", str(tmp_path / "recordings"))
    monkeypatch.setitem(ingest._CFG, "clip_format", "wav")
    monkeypatch.setitem(ingest._CFG, "drop_segment_after_clips", False)
    monkeypatch.setattr(ingest, "_identify", lambda w, **k: [FakeDetection()])
    ingest.app.config["TESTING"] = True
    return ingest.app.test_client()


def _register(svc, uid="esp-1", **fields):
    resp = svc.post("/api/register", json={"device_uid": uid, **fields})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _ingest(svc, key, *, uid="esp-1", captured_at=None, clock_unsynced=None, audio=None):
    data = {"device_uid": uid, "api_key": key}
    if captured_at is not None:
        data["captured_at"] = captured_at
    if clock_unsynced is not None:
        data["clock_unsynced"] = clock_unsynced
    data["audio"] = (io.BytesIO(audio if audio is not None else _wav_bytes()), "clip.wav")
    return svc.post("/api/ingest", data=data, content_type="multipart/form-data")


# --- registration ---------------------------------------------------------

def test_register_returns_key_once_then_idempotent(svc):
    first = _register(svc, name="Bench", location="Yard")
    assert first["api_key"], "first registration must return a usable key"
    again = _register(svc, location="Patio")
    assert again["device_id"] == first["device_id"], "device id is stable across re-register"
    assert again["api_key"] is None, "key is not recoverable on re-register"


def test_register_requires_uid(svc):
    assert svc.post("/api/register", json={}).status_code == 400


# --- the 12:00-vs-12:02 problem -------------------------------------------

def test_delayed_upload_keeps_capture_time(svc):
    """A clip captured at 12:00 but uploaded 2 minutes late must be stored at
    12:00 (capture time + detection offset), not at receipt time."""
    key = _register(svc)["api_key"]
    resp = _ingest(svc, key, captured_at="2026-06-11T12:00:00")
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["used_receipt_time"] is False
    assert body["detections"][0]["common_name"] == "Bewick's Wren"

    conn = storage.connect(ingest._CFG["db"])
    try:
        row = conn.execute("SELECT heard_at, segment_id FROM detections").fetchone()
        seg = conn.execute("SELECT device_id FROM segments WHERE id = ?", (row["segment_id"],)).fetchone()
    finally:
        conn.close()
    assert row["heard_at"] == "2026-06-11T12:00:00"  # offset 0.0 within the clip
    assert seg["device_id"] is not None, "detection attributed to the device"


def test_capture_time_offset_within_clip(svc, monkeypatch):
    """heard_at = captured_at + the detection's start offset."""
    key = _register(svc)["api_key"]
    offset = FakeDetection(start_time=1.0, end_time=4.0)
    monkeypatch.setattr(ingest, "_identify", lambda w, **k: [offset])
    _ingest(svc, key, captured_at="2026-06-11T12:00:00", audio=_wav_bytes(5.0))
    conn = storage.connect(ingest._CFG["db"])
    try:
        heard = conn.execute("SELECT heard_at FROM detections").fetchone()["heard_at"]
    finally:
        conn.close()
    assert heard == "2026-06-11T12:00:01"


def test_missing_captured_at_falls_back_to_receipt(svc):
    key = _register(svc)["api_key"]
    body = _ingest(svc, key, captured_at=None).get_json()
    assert body["used_receipt_time"] is True


def test_unsynced_clock_flag_falls_back(svc):
    key = _register(svc)["api_key"]
    body = _ingest(svc, key, captured_at="2026-06-11T12:00:00", clock_unsynced="1").get_json()
    assert body["used_receipt_time"] is True


def test_epoch_millis_captured_at(svc):
    key = _register(svc)["api_key"]
    # 2026-06-11T12:00:00 local -> epoch is environment-dependent, so just assert
    # parsing succeeds and we did NOT fall back to receipt time.
    epoch_ms = int(datetime(2026, 6, 11, 12, 0, 0).timestamp() * 1000)
    body = _ingest(svc, key, captured_at=str(epoch_ms)).get_json()
    assert body["used_receipt_time"] is False
    assert body["started_at"].startswith("2026-06-11T12:00:00")


# --- auth & validation ----------------------------------------------------

def test_bad_api_key_rejected(svc):
    _register(svc)
    assert _ingest(svc, "wrong-key").status_code == 401


def test_unknown_device_rejected(svc):
    key = _register(svc)["api_key"]
    assert _ingest(svc, key, uid="ghost").status_code == 404


def test_missing_audio_rejected(svc):
    key = _register(svc)["api_key"]
    assert svc.post(
        "/api/ingest", data={"device_uid": "esp-1", "api_key": key}
    ).status_code == 400


def test_non_wav_payload_rejected(svc):
    key = _register(svc)["api_key"]
    assert _ingest(svc, key, audio=b"not a wav").status_code == 400


def test_touch_updates_last_seen_and_ip(svc):
    key = _register(svc)["api_key"]
    _ingest(svc, key, captured_at="2026-06-11T12:00:00")
    conn = storage.connect(ingest._CFG["db"])
    try:
        dev = storage.get_device_by_uid(conn, "esp-1")
    finally:
        conn.close()
    assert dev["last_seen_at"] is not None
    assert dev["last_ip"] is not None
