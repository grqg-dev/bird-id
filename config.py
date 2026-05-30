"""Configuration for bird-id.

A tiny JSON-backed config so location and defaults are set once rather than
passed on every command. Resolution order for any value is:
    explicit CLI flag  >  config.json  >  built-in default

Portable by design (no Mac-specific bits here) so it carries over to a Pi.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# Built-in fallbacks used when neither a flag nor config.json provides a value.
DEFAULTS: dict[str, Any] = {
    "lat": None,        # e.g. 37.77  -> enables BirdNET's location/season filter
    "lon": None,        # e.g. -122.42
    "min_conf": 0.25,
    "db": "birdid.db",
    "recordings_dir": "recordings",
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


def has_location(cfg: dict[str, Any], lat: Optional[float] = None, lon: Optional[float] = None) -> bool:
    lat = resolve(lat, "lat", cfg)
    lon = resolve(lon, "lon", cfg)
    return lat is not None and lon is not None
