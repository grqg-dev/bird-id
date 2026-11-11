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
    "min_conf": 0.3,
    # BirdNET location/season species-list cutoff (birdnetlib `filter_threshold`, its default is
    # 0.03). Only used with lat/lon. Raising it (e.g. 0.1) shrinks the list to species more
    # likely at this place/week; None keeps birdnetlib's default. Separate from min_conf.
    "location_filter_threshold": None,
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
    "capture_stall_seconds": 5,   # no full hop for this long → kill + respawn ffmpeg
    "db": "birdid.db",
    "log_dir": "logs",            # relative to CWD, like db
    "log_max_mb": 5,              # rotate logs/*.log and *.err past this size
    "log_backups": 3,
    "recordings_dir": "recordings",
    "retention_days": 30,   # drop kept segment wavs after N days; 0 = keep forever
    "clip_format": "mp3",   # mp3 | wav — per-window clips written by the monitor
    "clip_mp3_bitrate": "64k",
    # Clip retention (retention.py); full segment wavs use retention_days above.
    "retention_high_conf": 0.9,       # track peak confidence at/above → high tier
    "retention_mid_conf": 0.7,        # at/above (and below high) → mid tier
    "retention_high_days": 365,       # keep high-tier clips this long
    "retention_mid_days": 30,
    "retention_low_days": 7,
    "retention_best_per_species": 10, # best N clips per species (that still have audio) kept forever
    "retention_live_hours": 24,       # newest clips always kept (live feed)
    "retention_max_clip_gb": 10,      # clip budget; over it, evict lowest confidence then oldest
    "retention_interval_minutes": 60, # how often the monitor runs retention
    "drop_segment_after_clips": True,  # delete full segment wav once clips exist (or no hits)
}


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
