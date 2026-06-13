"""Configuration for bird-id.

A tiny JSON-backed config so location and defaults are set once rather than
passed on every command. Resolution order for any value is:
    explicit CLI flag  >  config.json  >  built-in default

Portable by design (no Mac-specific bits here) so it carries over to a Pi.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# Built-in fallbacks used when neither a flag nor config.json provides a value.
DEFAULTS: dict[str, Any] = {
    "lat": None,        # e.g. 37.77  -> enables BirdNET's location/season filter
    "lon": None,        # e.g. -122.42
    "place": None,      # display name shown on the dashboard (e.g. "Monroeville")
    "timezone": None,   # IANA tz (e.g. "America/New_York") for clip times + sun arc;
                        #   None = use the host/container local time
    "min_conf": 0.3,
    "segment_minutes": 1.0,       # retired — ignored by streaming monitor
    # Streaming monitor (cmd_monitor)
    "hop_ms": 100,                # PCM read size: 100 ms per hop
    "seg_target_seconds": 8,      # flush near this length at a quiet boundary
    "seg_max_seconds": 12,        # hard flush even if never quiet
    "floor_window_seconds": 30,   # EMA-min window for adaptive noise floor
    "activity_margin_db": 6,      # dB above floor = "active" hop
    "quiet_hold_ms": 300,         # consecutive quiet time required before flush
    "tail_carry_seconds": 1.0,    # overlap prepended to next segment
    "mic_dead_dbfs": -85.0,       # RMS at or below this = "silent" hop
    "mic_dead_window_seconds": 60, # sustained silence window → mic-dead error
    "db": "birdid.db",
    "recordings_dir": "recordings",
    # Sensor ingest service (cmd_ingest_server) — receives ESP32-S3 clip uploads
    "ingest_host": "127.0.0.1",
    "ingest_port": 8081,
    "retention_days": 30,   # drop kept segment wavs after N days; 0 = keep forever
    "clip_format": "mp3",   # mp3 | wav — per-window clips written by the monitor
    "clip_mp3_bitrate": "64k",
    "proactive_cleanup": True,       # trim non-dashboard audio during monitor/deploy cleanup
    "dashboard_cleanup_hours": 6,    # how often the monitor runs dashboard trim (0 = every segment)
    "dashboard_live_hours": 24,      # live-feed window protected from trim
    "drop_segment_after_clips": True,  # delete full segment wav once clips exist (or no hits)
}


def apply_timezone(cfg: dict | None = None) -> None:
    """Set the process-local timezone from cfg['timezone'] (an IANA name) so naive
    timestamps and time formatting use the configured zone — important in a UTC
    Docker container. No-op if unset. Needs OS tzdata installed."""
    import os
    import time

    tz = (cfg if cfg is not None else load()).get("timezone")
    if tz:
        os.environ["TZ"] = tz
        if hasattr(time, "tzset"):
            time.tzset()


def load(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    """Return config merged over DEFAULTS. Missing file is fine (uses defaults)."""
    cfg = dict(DEFAULTS)
    p = Path(path).expanduser()
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text()))
        except json.JSONDecodeError as e:
            raise SystemExit(f"config error: {p} is not valid JSON ({e})")
    return cfg


def resolve(flag_value: Any, key: str, cfg: dict[str, Any]) -> Any:
    """Pick the flag value if given (not None), else the config/default value."""
    return flag_value if flag_value is not None else cfg.get(key, DEFAULTS.get(key))
