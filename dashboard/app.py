"""Flask app object, shared config, slug helpers, and the `main` entry point."""

from __future__ import annotations
import json
import re
from datetime import date
from pathlib import Path

from flask import Flask, abort

import config
import storage


_ROOT = Path(__file__).resolve().parent.parent


_SPRITES_DIR = _ROOT / "realistic-sprites"


_BIRDS_JSON = _ROOT / "docs" / "birds.json"


_BIRD_INFO_JSON = _ROOT / "docs" / "bird_info.json"


_DEV_RELOAD_SCRIPT = """<script>
(function(){var up=1;setInterval(function(){
  fetch("/__dev/ping",{cache:"no-store"}).then(function(r){
    if(!r.ok)up=0;else if(!up){up=1;location.reload()}
  }).catch(function(){up=0})
},800)})();
</script>"""


app = Flask("dashboard", root_path=str(_ROOT))  # templates/ and static/ live at the repo root


_CFG = config.load()


@app.template_filter("format_int")
def _filter_format_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


_SLUG_MAP_CACHE: dict[str, str] | None = None


_SLUG_TO_NAME_CACHE: dict[str, str] | None = None


def _slug_map() -> dict[str, str]:
    global _SLUG_MAP_CACHE
    if _SLUG_MAP_CACHE is None:
        with _BIRDS_JSON.open() as f:
            _SLUG_MAP_CACHE = {b["common_name"]: b["slug"] for b in json.load(f)}
    return _SLUG_MAP_CACHE


_BIRD_INFO_CACHE: dict[str, dict] | None = None


def _bird_info() -> dict[str, dict]:
    """Per-species field notes keyed by slug (cached). See docs/bird_info.json."""
    global _BIRD_INFO_CACHE
    if _BIRD_INFO_CACHE is None:
        try:
            with _BIRD_INFO_JSON.open() as f:
                data = json.load(f)
            _BIRD_INFO_CACHE = {k: v for k, v in data.items() if not k.startswith("_")}
        except (FileNotFoundError, json.JSONDecodeError):
            _BIRD_INFO_CACHE = {}
    return _BIRD_INFO_CACHE


def _slug_to_name() -> dict[str, str]:
    global _SLUG_TO_NAME_CACHE
    if _SLUG_TO_NAME_CACHE is None:
        with _BIRDS_JSON.open() as f:
            _SLUG_TO_NAME_CACHE = {b["slug"]: b["common_name"] for b in json.load(f)}
    return _SLUG_TO_NAME_CACHE


def _sprite_slug(common_name: str, slugs: dict[str, str]) -> str | None:
    slug = slugs.get(common_name) or _common_to_slug(common_name)
    if (_SPRITES_DIR / f"{slug}.png").is_file():
        return slug
    return None


def _common_to_slug(common_name: str) -> str:
    s = common_name.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _resolve_bird_slug(slug: str, conn) -> str | None:
    """Map URL slug → common_name (birds.json first, then DB fallback)."""
    name = _slug_to_name().get(slug)
    if name and storage.species_exists(conn, name):
        return name
    rows = conn.execute("SELECT DISTINCT common_name FROM detections").fetchall()
    for r in rows:
        if _common_to_slug(r["common_name"]) == slug:
            return r["common_name"]
    return None


# --- Trends page ---
# Days with known timing faults (e.g. segmenter clock was wrong). Hidden entirely.
EXCLUDED_DAYS: set[str] = {"2026-06-08"}


# Days with fewer detections than this are auto-excluded as startup/gap days.
MIN_DAY_DETECTIONS = 200


def _db():
    return storage.connect_readonly(_CFG["db"])


def _dev_mode() -> bool:
    return bool(app.config.get("DEV_MODE"))


def _today() -> str:
    return date.today().isoformat()


@app.route("/__dev/ping")
def dev_ping():
    """Dev-only heartbeat; pages poll this to reload after the server restarts."""
    if not _dev_mode():
        abort(404)
    return "", 204


_PIXEL_SPRITES_DIR = _ROOT / "sprites"


def main(host: str = "127.0.0.1", port: int = 8080, *, dev: bool = False):
    app.config["DEV_MODE"] = dev
    storage.ensure_schema(_CFG["db"])
    url = f"http://{host}:{port}"
    if dev:
        print(f"bird-id dashboard (dev) at {url}  (db: {_CFG['db']})")
        print("  auto-reload on code changes — leave a tab open to refresh automatically")
        extra_files = [str(_BIRD_INFO_JSON), str(_BIRDS_JSON)]
        app.run(host=host, port=port, debug=True, use_reloader=True, extra_files=extra_files)
        return
    print(f"bird-id dashboard at {url}  (db: {_CFG['db']})")
    import waitress

    waitress.serve(app, host=host, port=port, threads=8)
