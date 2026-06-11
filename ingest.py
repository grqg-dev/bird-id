"""HTTP ingest service for distributed ESP32-S3 bird sensors.

Sensors run a tiny on-device "bird vs. no bird" classifier and, only when a bird
is likely present, upload the audio clip here over Wi-Fi. This service runs the
*same* BirdNET pipeline the local monitor uses (identify -> clips -> store), so
each sensor's audio becomes species + a playable clip in the Bird-Dex, attributed
to the device that heard it.

This is the one server component that owns the BirdNET model (it imports
`identifier` lazily, mirroring how `birdid.py` defers librosa/TF). The dashboard
stays deliberately TF-free; keep that boundary.

Run:
    ./.venv/bin/python birdid.py ingest-server            # http://127.0.0.1:8081
    ./.venv/bin/python birdid.py ingest-server --host 0.0.0.0

Timestamps: the *device* stamps `captured_at` (NTP-synced) at the start of the
clip, and we treat it as authoritative. A buffered/retried/queued upload keeps its
original time, so a detection shows when the bird *called*, not when we received
it. Only a never-synced device (clock_unsynced) falls back to receipt time.
"""

from __future__ import annotations

import hashlib
import secrets
import wave
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, request

import clips
import config
import storage

app = Flask(__name__)
_CFG = config.load()


def _db():
    """Open a DB connection (the test seam — monkeypatched in tests)."""
    return storage.connect(_CFG["db"])


def _identify(wav_path, *, min_conf, lat, lon):
    """BirdNET seam. Imported lazily so `import ingest` stays TF-free (tests
    monkeypatch this with a stub)."""
    import identifier

    return identifier.identify(wav_path, min_conf=min_conf, lat=lat, lon=lon)


# --- auth -----------------------------------------------------------------

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _check_key(device_row, api_key: str) -> bool:
    stored = device_row["api_key_hash"]
    if not stored or not api_key:
        return False
    return secrets.compare_digest(stored, _hash_key(api_key))


# --- timestamps -----------------------------------------------------------

def _parse_instant(value: str) -> datetime:
    """Parse a device timestamp into a naive *local* datetime (matching the
    monitor's stored format). Accepts epoch seconds/millis or ISO-8601 (with or
    without offset)."""
    value = value.strip()
    # Epoch (seconds or milliseconds)?
    try:
        num = float(value)
        if num > 1e12:  # milliseconds
            num /= 1000.0
        return datetime.fromtimestamp(num)
    except ValueError:
        pass
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)  # to local wall clock, naive
    return dt


def _resolve_started_at(captured_at: str | None, clock_unsynced: bool, duration: float):
    """Return (started_at, used_fallback). Trust the device's captured_at unless
    it never synced its clock or omitted it — only then use receipt time."""
    if clock_unsynced or not captured_at:
        app.logger.warning(
            "ingest: using receipt time (captured_at missing or device clock unsynced)"
        )
        return datetime.now() - timedelta(seconds=duration), True
    try:
        return _parse_instant(captured_at), False
    except (ValueError, OverflowError, OSError):
        app.logger.warning("ingest: unparseable captured_at %r — using receipt time", captured_at)
        return datetime.now() - timedelta(seconds=duration), True


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        frames, rate = w.getnframes(), w.getframerate()
    if rate <= 0:
        raise ValueError("wav has zero sample rate")
    return frames / float(rate)


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _maybe_drop_segment_wav(conn, segment_id, detections, clip_paths) -> None:
    """Drop the full uploaded wav once clips exist (parity with the monitor's
    drop_segment_after_clips). Clips remain for playback; cleanup handles the rest."""
    if not config.resolve(None, "drop_segment_after_clips", _CFG):
        return
    if detections:
        windows = {(d.start_time, d.end_time) for d in detections}
        if not windows.issubset(clip_paths.keys()):
            return
    storage.clear_segment_wav(conn, segment_id)


# --- routes ---------------------------------------------------------------

@app.post("/api/register")
def register():
    """Register (or re-register) a sensor. Returns an API key on first
    registration only — the key is stored hashed and can't be recovered later."""
    data = request.get_json(silent=True) or request.form
    uid = (data.get("device_uid") or "").strip()
    if not uid:
        abort(400, "device_uid required")
    name = data.get("name") or None
    location = data.get("location") or None

    conn = _db()
    try:
        existing = storage.get_device_by_uid(conn, uid)
        new_key = None
        if not (existing and existing["api_key_hash"]):
            new_key = secrets.token_urlsafe(24)
            storage.register_device(
                conn, uid, name=name, location=location, api_key_hash=_hash_key(new_key)
            )
        else:
            storage.register_device(conn, uid, name=name, location=location)
        dev = storage.get_device_by_uid(conn, uid)
    finally:
        conn.close()

    resp = {"device_uid": uid, "device_id": dev["id"]}
    if new_key is not None:
        resp["api_key"] = new_key
    else:
        resp["api_key"] = None
        resp["note"] = "already registered; api_key is not recoverable"
    return jsonify(resp)


@app.post("/api/ingest")
def ingest():
    """Accept an uploaded clip from a registered sensor, run BirdNET, and store
    the detections attributed to that device."""
    uid = (request.form.get("device_uid") or "").strip()
    api_key = request.form.get("api_key") or ""
    audio = request.files.get("audio")
    if not uid or audio is None:
        abort(400, "device_uid and audio file are required")

    conn = _db()
    try:
        dev = storage.get_device_by_uid(conn, uid)
        if dev is None:
            abort(404, "unknown device — register it first")
        if not _check_key(dev, api_key):
            abort(401, "invalid api_key")

        rec_dir = Path(_CFG["recordings_dir"]).expanduser()
        rec_dir.mkdir(parents=True, exist_ok=True)
        # Stamp the saved file with receipt time; the *detection* time comes from
        # captured_at below, so a delayed upload still files under the true moment.
        tmp_name = f"ingest_{uid}_{datetime.now():%Y%m%d_%H%M%S_%f}.wav"
        wav_path = rec_dir / tmp_name
        audio.save(str(wav_path))

        try:
            duration = _wav_duration(wav_path)
        except (wave.Error, ValueError, EOFError):
            wav_path.unlink(missing_ok=True)
            abort(400, "audio must be a valid PCM WAV")

        started_at, used_fallback = _resolve_started_at(
            request.form.get("captured_at"),
            _truthy(request.form.get("clock_unsynced")),
            duration,
        )
        ended_at = started_at + timedelta(seconds=duration)

        min_conf = config.resolve(request.form.get("min_conf", type=float), "min_conf", _CFG)
        lat = config.resolve(request.form.get("lat", type=float), "lat", _CFG)
        lon = config.resolve(request.form.get("lon", type=float), "lon", _CFG)

        detections = _identify(wav_path, min_conf=min_conf, lat=lat, lon=lon)
        clip_paths = clips.clip_paths_for_detections(
            wav_path, rec_dir / "clips", started_at, detections, cfg=_CFG
        )
        seg_id = storage.record_segment(
            conn,
            started_at=started_at,
            ended_at=ended_at,
            duration=duration,
            detections=detections,
            wav_path=str(wav_path),
            clip_paths=clip_paths,
            device_id=dev["id"],
        )
        _maybe_drop_segment_wav(conn, seg_id, detections, clip_paths)
        storage.touch_device(conn, dev["id"], ip=request.remote_addr)
    finally:
        conn.close()

    return jsonify(
        {
            "segment_id": seg_id,
            "started_at": started_at.isoformat(timespec="seconds"),
            "used_receipt_time": used_fallback,
            "detections": [
                {
                    "common_name": d.common_name,
                    "scientific_name": d.scientific_name,
                    "confidence": round(d.confidence, 4),
                }
                for d in detections
            ],
        }
    )


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


def main(host: str = "127.0.0.1", port: int = 8081):
    print(f"bird-id ingest service at http://{host}:{port}  (db: {_CFG['db']})")
    app.run(host=host, port=port)
