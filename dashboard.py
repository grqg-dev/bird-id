"""Local web dashboard for bird-id.

A single-file Flask app that reads the SQLite database and shows a species dex
(default: today only), optional all-time view, day navigation, and per-species
spectrogram/audio from the peak detection clip.

Deliberately offline-friendly (no CDN / JS chart libs): charts are server-
rendered with plain CSS bars, so it works on a headless Pi with no internet.

Run:
    ./.venv/bin/python dashboard.py            # http://127.0.0.1:8080
    ./.venv/bin/python birdid.py dashboard
    ./.venv/bin/python birdid.py dashboard --dev   # auto-reload on code changes
"""

from __future__ import annotations

import io
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, Response, abort, render_template_string, request, send_file

import config
import storage

_ROOT = Path(__file__).resolve().parent
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

app = Flask(__name__)
_CFG = config.load()

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


_CLIPS_PER_PAGE = 50
PEAK_CONF_FLOOR = 0.7  # Bird-Dex "hide low confidence" cutoff (peak conf per species)
TIMELINE_CONF_FLOOR = 0.7  # Timeline only plots detections at/above this confidence
TIMELINE_BINS = 96  # Per-lane histogram buckets across 24h (15-minute bars)
_CALLVIZ_ROWS = 48
_CALLVIZ_COLS = 96


def _db():
    return storage.connect(_CFG["db"])


def _dev_mode() -> bool:
    return bool(app.config.get("DEV_MODE"))


def _spec_cache_path(
    segment_id: int,
    start: float | None,
    end: float | None,
    base_dir: str | Path,
) -> Path:
    """Disk path for a cached spectrogram PNG (pure path logic for tests)."""
    base = Path(base_dir).expanduser() / "cache" / "spectrograms"
    if start is not None and end is not None and end > start:
        name = f"spec_{segment_id}_{int(start * 1000)}_{int(end * 1000)}.png"
    else:
        name = f"spec_{segment_id}_full.png"
    return base / name


def _callviz_cache_path(
    segment_id: int,
    start: float | None,
    end: float | None,
    base_dir: str | Path,
) -> Path:
    """Disk path for a cached call-viz JSON matrix (pure path logic for tests)."""
    base = Path(base_dir).expanduser() / "cache" / "callviz"
    if start is not None and end is not None and end > start:
        name = f"viz_{segment_id}_{int(start * 1000)}_{int(end * 1000)}.json"
    else:
        name = f"viz_{segment_id}_full.json"
    return base / name


def _resample_cols(matrix, target_cols: int):
    """Resample a 2-D array along the time (column) axis."""
    import numpy as np

    mat = np.asarray(matrix, dtype=np.float32)
    rows, cols = mat.shape
    if cols == target_cols:
        return mat
    x_old = np.linspace(0.0, 1.0, cols)
    x_new = np.linspace(0.0, 1.0, target_cols)
    out = np.zeros((rows, target_cols), dtype=np.float32)
    for i in range(rows):
        out[i] = np.interp(x_new, x_old, mat[i])
    return out


def _compute_callviz(audio_path: str, start: float | None, end: float | None) -> dict:
    """Downsampled mel magnitude matrix + per-frame pitch for the 3D call view."""
    import librosa
    import numpy as np

    has_window = start is not None and end is not None and end > start
    is_clip = Path(audio_path).name.startswith("clip_")
    if is_clip or not has_window:
        y, sr = librosa.load(audio_path, sr=None)
    elif has_window:
        y, sr = librosa.load(audio_path, sr=None, offset=start, duration=end - start)
        if y.size == 0:
            y, sr = librosa.load(audio_path, sr=None)
    else:
        y, sr = librosa.load(audio_path, sr=None)

    duration = float(len(y) / sr) if sr else 0.0
    n_mels = _CALLVIZ_ROWS
    S = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=n_mels, fmax=sr // 2)
    S_db = librosa.power_to_db(S, ref=np.max)
    mag = _resample_cols(S_db, _CALLVIZ_COLS)
    lo, hi = float(mag.min()), float(mag.max())
    mag_norm = (mag - lo) / (hi - lo + 1e-8)

    fmin = librosa.note_to_hz("C2")
    fmax = librosa.note_to_hz("C7")
    pitch = librosa.yin(y, fmin=fmin, fmax=fmax, sr=sr, frame_length=2048)
    pitch = _resample_cols(pitch.reshape(1, -1), _CALLVIZ_COLS).reshape(-1)

    freqs = librosa.mel_frequencies(n_mels=n_mels, fmax=sr // 2).tolist()
    times = np.linspace(0.0, duration, _CALLVIZ_COLS).tolist()
    return {
        "sr": int(sr),
        "duration": duration,
        "freqs": freqs,
        "times": times,
        "rows": _CALLVIZ_ROWS,
        "cols": _CALLVIZ_COLS,
        "mag": mag_norm.tolist(),
        "pitch": [float(p) if p > 0 else 0.0 for p in pitch],
    }


def _resolve_call_audio(
    conn,
    segment_id: int,
    start: float | None,
    end: float | None,
) -> str | None:
    """Return playable audio path for a detection window (mirrors spectrogram route)."""
    has_window = start is not None and end is not None and end > start
    seg = storage.get_segment(conn, segment_id)
    track = storage.get_track(conn, segment_id, start, end) if has_window else None

    if track and track["clip_path"] and os.path.exists(track["clip_path"]):
        return track["clip_path"]
    if seg and seg["wav_path"] and os.path.exists(seg["wav_path"]):
        return seg["wav_path"]
    return None


def _audio_mimetype(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".mp3":
        return "audio/mpeg"
    return "audio/wav"


def _clip_wav_bytes(wav_path: str, start: float, end: float) -> bytes:
    """Return a wav containing only [start, end) seconds of the source file."""
    import soundfile as sf

    info = sf.info(wav_path)
    sr = info.samplerate
    start_frame = max(0, int(start * sr))
    end_frame = min(info.frames, int(end * sr))
    if end_frame <= start_frame:
        raise ValueError("empty clip window")

    data, _ = sf.read(wav_path, start=start_frame, stop=end_frame, dtype="float32")
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="WAV")
    buf.seek(0)
    return buf.read()


PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>Bird-Dex</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--red:#d83a36;--red-dark:#a82826;--red-deep:#7d1c1b;--cream:#f3efe2;--ink:#21232a;
        --gold:#ffcf3f;--grn:#46c66b;--surface:#f7f6f2;--border:#e2e0d8}
  *{box-sizing:border-box}
  body{font-family:"Helvetica Neue",Arial,sans-serif;margin:0;color:var(--ink);min-height:100vh;
       background:#fff}
  .mono{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace}
  .lbl{font-size:11px;letter-spacing:1.5px;color:#8a857a}

  .wrap{max-width:1280px;margin:0 auto;padding:28px 22px}
  .when{margin-left:auto;text-align:right;color:#8a857a}
  .when b{display:block;font-size:13px;letter-spacing:1.5px;color:var(--ink);font-weight:700}

  /* summary strip */
  .screen-bar{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 20px;margin-bottom:22px;
              display:flex;gap:26px;align-items:center;box-shadow:0 1px 4px rgba(0,0,0,.06)}
  .screen-bar .count{font-size:28px;font-weight:800;color:var(--ink);letter-spacing:-.5px}
  .screen-bar .lbl{color:#8a857a}
  .screen-bar .seg{border-left:1px solid var(--border);padding-left:26px}

  /* dex grid — ~3 cards per row on desktop */
  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
  @media(max-width:1100px){.grid:not(.gallery){grid-template-columns:repeat(2,1fr)}}
  @media(max-width:680px){.grid:not(.gallery){grid-template-columns:1fr}}
  .entry{background:#fff;border-radius:12px;border:1px solid #e2e0d8;overflow:hidden;
         box-shadow:0 1px 4px rgba(0,0,0,.08);display:flex;flex-direction:column}
  .entry .top{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;
              background:#f7f6f2;border-bottom:1px solid #eceae3}
  .no{font-weight:800;color:var(--ink);letter-spacing:1px}
  .peak-conf{font-weight:700;color:var(--red-deep);letter-spacing:.3px}
  .top-right{display:flex;align-items:center;gap:8px}
  .viz-btn{font-size:10px;font-weight:700;letter-spacing:.4px;text-decoration:none;
           padding:3px 7px;border-radius:6px;border:1px solid var(--red-dark);color:var(--red-dark);
           background:#fff;line-height:1.2}
  .viz-btn:hover{background:var(--surface);color:var(--red-deep);border-color:var(--red-deep)}
  .sprite{background:#fff;border-bottom:1px solid #eceae3}
  .sprite img.art{display:block;width:200px;height:200px;margin:0 auto;object-fit:contain;padding:10px 14px 6px}
  .spectro{background:#0d1b14}
  .spectro img{display:block;width:100%;height:88px;object-fit:cover}
  .play{background:#f7f6f2;padding:8px 10px;border-top:1px solid #eceae3}
  .play audio{display:block;width:100%;height:32px}
  .body{padding:10px 12px 12px}
  .name{font-size:17px;font-weight:800;margin:0;letter-spacing:.3px}
  .latin{font-style:italic;color:#7a766a;font-size:12px;margin:1px 0 9px}
  .meta{display:flex;justify-content:space-between;font-size:11px;color:#8a857a;letter-spacing:.5px;margin-top:4px}
  .empty{background:#f7f6f2;border:1px solid #e2e0d8;border-radius:12px;padding:46px;text-align:center;color:#7a766a;font-size:15px}
  .modes{display:flex;gap:8px;margin-bottom:14px}
  .modes a{font-size:12px;letter-spacing:.5px;text-decoration:none;padding:6px 12px;border-radius:8px;
           border:1px solid var(--border);color:#7a766a;background:#fff}
  .modes a.on{background:var(--surface);border-color:#d7d2c0;color:var(--ink);font-weight:700}
  .modes a:hover{border-color:#c8c4ba;color:var(--ink);background:var(--surface)}
  .entry.low-conf{border-color:#e8dcc0;background:#fffcf5}
  .entry.low-conf .peak-conf{color:#b8860b}
  .day-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px;padding:10px 14px;
           background:var(--surface);border:1px solid var(--border);border-radius:10px}
  .day-bar a{font-size:12px;letter-spacing:.5px;text-decoration:none;padding:6px 12px;border-radius:8px;
             border:1px solid var(--border);color:#7a766a;background:#fff}
  .day-bar a.on{background:#fff;border-color:var(--red);color:var(--red-deep);font-weight:700}
  .day-bar a:hover{border-color:#c8c4ba;color:var(--ink)}
  .day-bar form{display:flex;align-items:center;gap:8px;margin:0}
  .day-bar input[type=date]{font-size:12px;padding:6px 10px;border:1px solid var(--border);border-radius:8px;
                            font-family:inherit;background:#fff}
  .day-bar button{font-size:12px;padding:6px 12px;border-radius:8px;border:1px solid var(--border);
                 background:#fff;cursor:pointer}
  .day-bar .spacer{flex:1}
  .no-art{display:flex;align-items:center;justify-content:center;width:200px;height:200px;margin:0 auto;padding:8px 12px;
          color:#b0aca2;font-size:12px;letter-spacing:.5px;text-align:center}
  /* gallery — fixed square art box, auto columns (3–4+ on typical screens) */
  .grid.gallery{grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
  @media(max-width:480px){.grid.gallery{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
  .grid.gallery .entry .top{padding:5px 8px}
  .grid.gallery .body{padding:6px 8px 8px}
  .grid.gallery .latin{margin:1px 0 4px}
  .grid.gallery .sprite{padding:6px 8px 4px;border-bottom:none;background:#fff}
  .grid.gallery .art-box{
    aspect-ratio:1;width:100%;border:1px solid #eceae3;border-radius:4px;
    background:#fff;display:flex;align-items:center;justify-content:center;overflow:hidden}
  .grid.gallery .art-box img.art{width:100%;height:100%;padding:6px;object-fit:contain;display:block}
  .grid.gallery .art-box.no-art{height:auto;font-size:10px;color:#b0aca2;letter-spacing:.4px}
  .name a{color:inherit;text-decoration:none}
  .name a:hover{color:var(--red-deep);text-decoration:underline}
</style></head><body>

<div class="wrap">
  <div class="day-bar mono">
    {% if not show_all %}
    <a href="{{ qs(day=prev_day) }}">← Prev</a>
    <a href="{{ qs(day=next_day) }}">Next →</a>
    {% endif %}
    <form method="get" action="/">
      {% if mode == 'gallery' %}<input type="hidden" name="mode" value="gallery">{% endif %}
      {% if sort != 'discovered' %}<input type="hidden" name="sort" value="{{ sort }}">{% endif %}
      {% if hide_low %}<input type="hidden" name="hide_low" value="1">{% endif %}
      <label class="lbl" for="day-pick">Day</label>
      <input type="date" id="day-pick" name="day" value="{{ selected_day }}" max="{{ today }}">
      <button type="submit">Go</button>
    </form>
    {% if not show_all and selected_day != today %}
    <a href="{{ qs(day=today) }}">Today</a>
    {% endif %}
    <span class="spacer"></span>
    <a href="/live">Live feed</a>
    <a href="/timeline{{ timeline_qs }}">Timeline</a>
    <a href="/data{{ data_qs }}">Data report</a>
    {% if show_all %}
    <a href="{{ qs(day=today) }}">Daily (today)</a>
    {% else %}
    <a href="{{ qs(day='all') }}">Show all</a>
    {% endif %}
    {% if show_all %}
    <span class="lbl">All-time · every day</span>
    {% else %}
    <span class="lbl">{{ day_label }}</span>
    {% endif %}
  </div>
  <div class="modes mono">
    <a href="{{ qs(mode='dex') }}" class="{{ 'on' if mode == 'dex' else '' }}">Full dex</a>
    <a href="{{ qs(mode='gallery') }}" class="{{ 'on' if mode == 'gallery' else '' }}">Gallery</a>
    <span style="flex:1"></span>
    <a href="{{ qs(sort='discovered') }}" class="{{ 'on' if sort == 'discovered' else '' }}">Discovered</a>
    <a href="{{ qs(sort='heard') }}" class="{{ 'on' if sort == 'heard' else '' }}">Most heard</a>
    <a href="{{ qs(sort='peak') }}" class="{{ 'on' if sort == 'peak' else '' }}">Peak conf</a>
    <a href="{{ qs(hide_low=not hide_low) }}" class="{{ 'on' if hide_low else '' }}">Peak ≥ {{ "%.1f"|format(peak_floor) }}</a>
    {% if hide_low and hidden_count %}<span class="lbl">{{ hidden_count }} hidden</span>{% endif %}
  </div>
  <div class="screen-bar">
    <div><div class="count mono">Nº {{ "%03d"|format(total) }}</div><div class="lbl">{{ species_lbl }}</div></div>
    {% if show_all %}
    {% if all_totals.detections %}
    <div class="seg"><div class="count mono">{{ all_totals.detections }}</div><div class="lbl">Detections (all time)</div></div>
    <div class="seg"><div class="count mono">{{ all_totals.species }}</div><div class="lbl">Species (all time)</div></div>
    {% else %}<div class="seg lbl">No sightings logged yet</div>{% endif %}
    {% elif ov.detections %}
    <div class="seg"><div class="count mono">{{ ov.detections }}</div><div class="lbl">Detections {{ day_scope }}</div></div>
    <div class="seg"><div class="count mono">{{ ov.species }}</div><div class="lbl">Heard {{ day_scope }} · busiest {{ peak }}</div></div>
    {% else %}<div class="seg lbl">No sightings logged {{ day_scope }} yet</div>{% endif %}
    <div class="when"><b>Bird-Dex</b>Santa Barbara · {{ header_day }}</div>
    <div class="seg"><a href="/data{{ data_qs }}" style="font-size:12px;color:var(--red-dark);text-decoration:none;font-weight:700">Data report →</a></div>
  </div>

  {% if dex %}
  <div class="grid{{ ' gallery' if mode == 'gallery' else '' }}">
    {% for e in dex %}
    {% set clip_qs = "?start=%.1f&end=%.1f"|format(e.start_time, e.end_time) %}
    <div class="entry{{ ' low-conf' if not hide_low and e.peak_conf < peak_floor else '' }}">
      <div class="top">
        <span class="no mono">Nº {{ "%03d"|format(loop.index) }}</span>
        <div class="top-right">
          {% if e.has_audio and mode != 'gallery' %}
          <a class="viz-btn mono" title="3D call view"
             href="{{ call_href(e.segment_id, e.start_time, e.end_time, e.bird_slug) }}">◳ Viz</a>
          {% endif %}
          <span class="peak-conf mono">{{ "%.3f"|format(e.peak_conf) }}</span>
        </div>
      </div>
      <div class="sprite">
        {% if e.sprite_slug %}
        {% if mode == 'gallery' %}<div class="art-box">{% endif %}
        <img class="art" loading="lazy" src="/sprite/{{ e.sprite_slug }}.png" alt="{{ e.common_name }}">
        {% if mode == 'gallery' %}</div>{% endif %}
        {% elif mode == 'gallery' %}
        <div class="art-box no-art lbl">NO ILLUSTRATION</div>
        {% endif %}
        {% if mode != 'gallery' and e.has_audio %}
        <div class="spectro">
          <img loading="lazy" src="/spectrogram/{{ e.segment_id }}.png{{ clip_qs }}" alt="call spectrogram">
        </div>
        <div class="play"><audio controls preload="none" src="/audio/{{ e.segment_id }}{{ clip_qs }}"></audio></div>
        {% endif %}
      </div>
      <div class="body">
        <p class="name"><a href="/bird/{{ e.bird_slug }}{{ bird_qs }}">{{ e.common_name }}</a></p>
        <p class="latin">{{ e.scientific_name }}</p>
        <div class="meta mono"><span>HEARD ×{{ e.windows }}</span>
          <span>{{ e.first_heard[5:10] }} → {{ e.last_heard[5:10] }}</span></div>
        {% if e.windows > 1 and mode != 'gallery' %}
        <div class="meta mono" style="margin-top:6px"><a href="/bird/{{ e.bird_slug }}{{ bird_qs }}" style="color:var(--red-dark)">All {{ e.windows }} clips →</a></div>
        {% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">{{ empty_msg }}</div>
  {% endif %}
</div>
{% if dev %}{{ dev_script|safe }}{% endif %}
</body></html>
"""


VISUALIZE_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>{{ common_name }} · Call Chamber</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="/static/three.min.js"></script>
<script src="/static/OrbitControls.js"></script>
<style>
  :root{--red:#d83a36;--red-dark:#a82826;--red-deep:#7d1c1b;--cream:#f3efe2;--ink:#21232a;
        --gold:#ffcf3f;--surface:#f7f6f2;--border:#e2e0d8}
  *{box-sizing:border-box}
  body{margin:0;font-family:"Helvetica Neue",Arial,sans-serif;color:var(--cream);background:#060a0e;min-height:100vh}
  .mono{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace}
  .lbl{font-size:11px;letter-spacing:1.5px;color:#7a8a94;text-transform:uppercase}
  .wrap{max-width:1280px;margin:0 auto;padding:18px 20px 28px}
  .hdr{display:flex;flex-wrap:wrap;gap:12px 18px;align-items:center;margin-bottom:14px}
  .hdr a{color:#9ab0bc;text-decoration:none;font-size:12px;font-weight:700;letter-spacing:.4px}
  .hdr a:hover{color:var(--cream)}
  .hdr .spacer{flex:1}
  .hdr .pos{font-size:12px;color:#7a8a94}
  .title{margin:0;font-size:22px;font-weight:800;letter-spacing:.2px}
  .latin{margin:2px 0 0;font-style:italic;color:#8a9aa4;font-size:13px}
  .conf{font-size:18px;font-weight:800;color:var(--red)}
  .nav{display:flex;gap:8px}
  .nav a{padding:7px 12px;border:1px solid #243038;border-radius:8px;background:#0c1218;color:#b8c8d0;
         text-decoration:none;font-size:12px;font-weight:700}
  .nav a:hover{border-color:#3a4a54;color:var(--cream)}
  .nav a.off{opacity:.35;pointer-events:none}
  .stage{position:relative;border:1px solid #1a2830;border-radius:14px;overflow:hidden;
         background:radial-gradient(ellipse at 50% 20%,#12202a 0%,#060a0e 70%);min-height:420px}
  #chamber-canvas{display:block;width:100%;height:min(62vh,520px)}
  #chamber-fallback{display:none;position:absolute;inset:0;align-items:center;justify-content:center;
                    color:#7a8a94;font-size:14px;padding:24px;text-align:center}
  .controls{margin-top:14px;padding:12px 14px;border:1px solid #1a2830;border-radius:12px;background:#0a1016}
  .controls audio{display:block;width:100%;height:36px}
  .hint{margin-top:10px;font-size:11px;color:#5a6a74;letter-spacing:.3px}
  .sprite-sm{width:48px;height:48px;object-fit:contain;border-radius:8px;background:#0c1218;
             border:1px solid #1a2830;padding:4px}
</style></head><body>
<div class="wrap">
  <div class="hdr mono">
    <a href="{{ back_href }}">← Bird-Dex</a>
    <span class="pos">Nº {{ "%03d"|format(dex_no) }} of {{ "%03d"|format(dex_total) }}</span>
    <span class="spacer"></span>
    <div class="nav">
      {% if prev_call %}
      <a href="{{ call_href(prev_call) }}">← {{ prev_call.name }}</a>
      {% else %}<a class="off" href="#">← Prev</a>{% endif %}
      {% if next_call %}
      <a href="{{ call_href(next_call) }}">{{ next_call.name }} →</a>
      {% else %}<a class="off" href="#">Next →</a>{% endif %}
    </div>
  </div>
  <div class="hdr" style="margin-bottom:10px">
    {% if sprite_slug %}<img class="sprite-sm" src="/sprite/{{ sprite_slug }}.png" alt="">{% endif %}
    <div>
      <h1 class="title">{{ common_name }}</h1>
      {% if scientific_name %}<p class="latin">{{ scientific_name }}</p>{% endif %}
    </div>
    <span class="spacer"></span>
    <span class="conf mono">{{ "%.3f"|format(peak_conf) }}</span>
  </div>
  <div class="stage">
    <canvas id="chamber-canvas"></canvas>
    <div id="chamber-fallback">WebGL unavailable — audio playback still works below.</div>
  </div>
  <div class="controls">
    <audio id="chamber-audio" controls preload="metadata"
           src="/audio/{{ segment_id }}?start={{ start }}&end={{ end }}"></audio>
    <p class="hint mono">Drag to orbit · scroll to zoom · press play to animate the call through time</p>
  </div>
</div>
<script>
(function(){
  var canvas=document.getElementById('chamber-canvas');
  var fallback=document.getElementById('chamber-fallback');
  var audio=document.getElementById('chamber-audio');
  var vizUrl='/callviz/{{ segment_id }}.json?start={{ start }}&end={{ end }}';
  if(!canvas||!window.THREE){
    fallback.style.display='flex';
    return;
  }
  fetch(vizUrl).then(function(r){
    if(!r.ok)throw new Error('viz');
    return r.json();
  }).then(function(data){
    try{initScene(data);}catch(e){
      fallback.style.display='flex';
      fallback.textContent='WebGL unavailable — audio playback still works below.';
    }
  }).catch(function(){
    fallback.style.display='flex';
    fallback.textContent='Visualization data unavailable — audio playback still works below.';
  });

  function initScene(data){
    var THREE=window.THREE;
    var rows=data.rows,cols=data.cols,mag=data.mag,pitch=data.pitch||[];
    var duration=data.duration||1;
    var renderer=new THREE.WebGLRenderer({canvas:canvas,antialias:true,preserveDrawingBuffer:true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
    renderer.autoClear=false;
    renderer.setClearColor(0x0a0c16,1);
    var scene=new THREE.Scene();
    scene.background = new THREE.Color(0x0a0c16);
    scene.fog=new THREE.FogExp2(0x0a0c16,0.022);
    var camera=new THREE.PerspectiveCamera(45,1,0.1,200);
    camera.position.set(12,14,16);
    var controls=new THREE.OrbitControls(camera,canvas);
    controls.enableDamping=true;
    controls.dampingFactor=0.06;
    controls.autoRotate=true;
    controls.autoRotateSpeed=0.35;
    controls.target.set(0,2,0);
    var gridW=10,gridD=14,maxH=4;
    var count=rows*cols;
    var geometry=new THREE.CylinderGeometry(0.06,0.08,1,6);
    geometry.translate(0,0.5,0);
    var material=new THREE.MeshPhongMaterial({
      color:0xffffff,shininess:110,specular:0xffffff,flatShading:true
    });
    var mesh=new THREE.InstancedMesh(geometry,material,count);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    var dummy=new THREE.Object3D();
    var color=new THREE.Color();
    var cLow=new THREE.Color(0x2a1b6a),cMid=new THREE.Color(0xff007f),cHi=new THREE.Color(0x00f3ff);
    var instances=[];
    var idx=0;
    for(var c=0;c<cols;c++){
      var z=(c/(cols-1)-0.5)*gridD;
      for(var r=0;r<rows;r++){
        var x=(r/(rows-1)-0.5)*gridW;
        var m=(mag[r]&&mag[r][c]!=null)?mag[r][c]:0;
        var h=0.12+m*maxH;
        dummy.position.set(x,0,z);
        dummy.scale.set(1,h,1);
        dummy.updateMatrix();
        mesh.setMatrixAt(idx,dummy.matrix);
        if(m<0.5)color.copy(cLow).lerp(cMid,m*2);
        else color.copy(cMid).lerp(cHi,(m-0.5)*2);
        mesh.setColorAt(idx,color);
        instances.push({idx:idx,col:c,row:r,mag:m,z:z,h:h});
        idx++;
      }
    }
    scene.add(mesh);
    var playheadGeo=new THREE.PlaneGeometry(gridW+2,maxH+3);
    var playheadMat=new THREE.MeshBasicMaterial({
      color:0xff007f,transparent:true,opacity:0.08,side:THREE.DoubleSide
    });
    var playhead=new THREE.Mesh(playheadGeo,playheadMat);
    playhead.rotation.y=Math.PI/2;
    playhead.position.set(0,(maxH+3)/2,-gridD/2);
    scene.add(playhead);
    var laser=new THREE.Mesh(
      new THREE.BoxGeometry(gridW+0.4,0.06,0.1),
      new THREE.MeshBasicMaterial({color:0xff007f})
    );
    laser.position.set(0,0.03,-gridD/2);
    scene.add(laser);
    var pitchGeom=new THREE.SphereGeometry(0.25,16,16);
    var pitchMat=new THREE.MeshPhongMaterial({color:0x00f3ff,emissive:0x00f3ff,emissiveIntensity:0.6});
    var pitchMarker=new THREE.Mesh(pitchGeom,pitchMat);
    pitchMarker.visible=false;
    scene.add(pitchMarker);
    var pitchLight=new THREE.PointLight(0x00f3ff,2.2,8);
    pitchLight.visible=false;
    scene.add(pitchLight);
    var partCount=140;
    var partGeo=new THREE.BufferGeometry();
    var partPos=new Float32Array(partCount*3);
    var partVels=[];
    for(var i=0;i<partCount;i++){
      partPos[i*3]=(Math.random()-0.5)*12;
      partPos[i*3+1]=Math.random()*8;
      partPos[i*3+2]=(Math.random()-0.5)*16;
      partVels.push({
        y:0.02+Math.random()*0.03,
        x:(Math.random()-0.5)*0.01,
        z:(Math.random()-0.5)*0.01
      });
    }
    partGeo.setAttribute('position',new THREE.BufferAttribute(partPos,3));
    var partMat=new THREE.PointsMaterial({
      color:0x00f3ff,size:0.12,transparent:true,opacity:0.5,blending:THREE.AdditiveBlending
    });
    var particles=new THREE.Points(partGeo,partMat);
    scene.add(particles);
    scene.add(new THREE.AmbientLight(0x354060,0.9));
    var key=new THREE.DirectionalLight(0xfff0d8,1.2);
    key.position.set(6,16,10);
    scene.add(key);
    var fill=new THREE.DirectionalLight(0x00f3ff,0.6);
    fill.position.set(-10,8,8);
    scene.add(fill);
    var rim=new THREE.DirectionalLight(0xff007f,0.7);
    rim.position.set(-8,4,-10);
    scene.add(rim);
    var gridHelper=new THREE.GridHelper(gridW+4,14,0x432060,0x1a1030);
    gridHelper.position.y=0.01;
    scene.add(gridHelper);
    var fadeGeo=new THREE.PlaneGeometry(2,2);
    var fadeMat=new THREE.MeshBasicMaterial({
      color:0x0a0c16,transparent:true,opacity:0.15,depthWrite:false,depthTest:false
    });
    var fadeMesh=new THREE.Mesh(fadeGeo,fadeMat);
    var fadeScene=new THREE.Scene();
    fadeScene.add(fadeMesh);
    var fadeCamera=new THREE.OrthographicCamera(-1,1,1,-1,0,1);
    function resize(){
      var w=canvas.clientWidth,h=canvas.clientHeight;
      if(!w||!h)return;
      renderer.setSize(w,h,false);
      camera.aspect=w/h;
      camera.updateProjectionMatrix();
      renderer.clear();
    }
    window.addEventListener('resize',resize);
    resize();
    function magColor(m,near,isPlaying,col,row){
      var color=new THREE.Color();
      var baseHue=(Date.now()*0.00004+col*0.002+row*0.001)%1.0;
      if(isPlaying){
        if(near){
          color.setHSL((baseHue+0.2)%1.0,1.0,0.65);
          color.addScalar(0.15);
        }else{
          color.setHSL(baseHue,0.9,0.18+m*0.2);
        }
      }else{
        color.setHSL((baseHue+m*0.1)%1.0,0.95,0.22+m*0.35);
      }
      return color;
    }
    function updatePlayhead(){
      var t=(audio.duration&&!isNaN(audio.duration))?audio.currentTime/audio.duration:0;
      var z=(t-0.5)*gridD;
      playhead.position.z=z;
      laser.position.z=z;
      if(pitch.length&&data.freqs){
        var playCol=Math.min(cols-1,Math.max(0,Math.floor(t*(cols-1))));
        var hz=pitch[playCol];
        if(hz&&hz>0){
          var freqs=data.freqs,bestR=0,bestD=Infinity;
          for(var i=0;i<freqs.length;i++){
            var d=Math.abs(freqs[i]-hz);
            if(d<bestD){bestD=d;bestR=i;}
          }
          var px=(bestR/(rows-1)-0.5)*gridW;
          var pm=(mag[bestR]&&mag[bestR][playCol]!=null)?mag[bestR][playCol]:0;
          var py=0.12+pm*maxH+0.45;
          pitchMarker.position.set(px,py,z);
          pitchMarker.visible=true;
          pitchLight.position.set(px,py,z);
          pitchLight.visible=true;
        }else{
          pitchMarker.visible=false;
          pitchLight.visible=false;
        }
      }
    }
    audio.addEventListener('timeupdate',updatePlayhead);
    audio.addEventListener('play',function(){controls.autoRotate=false;});
    audio.addEventListener('pause',function(){controls.autoRotate=true;});
    audio.addEventListener('ended',function(){controls.autoRotate=true;});
    function animate(){
      requestAnimationFrame(animate);
      var t=(audio.duration&&!isNaN(audio.duration))?audio.currentTime/audio.duration:0;
      var playCol=Math.min(cols-1,Math.max(0,Math.floor(t*(cols-1))));
      var isPlaying=!audio.paused&&!audio.ended;
      var idx=0;
      for(var c=0;c<cols;c++){
        var z=(c/(cols-1)-0.5)*gridD;
        var distToPlayhead=Math.abs(c-playCol);
        for(var r=0;r<rows;r++){
          var x=(r/(rows-1)-0.5)*gridW;
          var m=(mag[r]&&mag[r][c]!=null)?mag[r][c]:0;
          var scaleFactor=1.0;
          if(isPlaying){
            if(distToPlayhead<4){
              var factor=1.0-(distToPlayhead/4);
              scaleFactor=0.2+factor*2.2*(1.0+Math.sin(Date.now()*0.02+r*0.5)*0.25);
            }else{
              scaleFactor=0.15+(Math.sin(Date.now()*0.005+c*0.2+r*0.3)*0.1);
            }
          }else{
            scaleFactor=0.6+Math.sin(Date.now()*0.002+c*0.1+r*0.15)*0.15;
          }
          var h=0.08+m*maxH*scaleFactor;
          dummy.position.set(x,0,z);
          dummy.scale.set(1.0+m*0.5*scaleFactor,h,1.0+m*0.5*scaleFactor);
          dummy.updateMatrix();
          mesh.setMatrixAt(idx,dummy.matrix);
          var near=distToPlayhead<=1.5;
          mesh.setColorAt(idx,magColor(m,near,isPlaying,c,r));
          idx++;
        }
      }
      mesh.instanceMatrix.needsUpdate=true;
      if(mesh.instanceColor)mesh.instanceColor.needsUpdate=true;
      if(particles){
        var posAttr=particles.geometry.attributes.position;
        var pTime=Date.now()*0.001;
        for(var i=0;i<partCount;i++){
          posAttr.array[i*3+1]+=partVels[i].y;
          posAttr.array[i*3]+=Math.sin(pTime+i)*0.02;
          posAttr.array[i*3+2]+=Math.cos(pTime+i*0.5)*0.02;
          if(posAttr.array[i*3+1]>8){
            posAttr.array[i*3+1]=0;
            posAttr.array[i*3]=(Math.random()-0.5)*12;
            posAttr.array[i*3+2]=(Math.random()-0.5)*16;
          }
        }
        posAttr.needsUpdate=true;
      }
      laser.scale.y=1.0+Math.sin(Date.now()*0.05)*0.4;
      if(isPlaying){
        var time=Date.now()*0.0006;
        var radius=16+Math.sin(time*0.5)*3;
        camera.position.x=Math.cos(time)*radius;
        camera.position.z=Math.sin(time)*radius;
        camera.position.y=10+Math.sin(time*0.8)*5;
        controls.target.set(0,1+Math.sin(time*0.3)*1.5,0);
      }else{
        controls.update();
      }
      renderer.render(fadeScene,fadeCamera);
      renderer.render(scene,camera);
    }
    animate();
  }
})();
</script>
{% if dev %}{{ dev_script|safe }}{% endif %}
</body></html>
"""


BIRD_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>{{ common_name }} · Bird-Dex</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--red:#d83a36;--red-dark:#a82826;--red-deep:#7d1c1b;--cream:#f3efe2;--ink:#21232a;
        --gold:#ffcf3f;--grn:#46c66b;--surface:#f7f6f2;--border:#e2e0d8}
  *{box-sizing:border-box}
  body{font-family:"Helvetica Neue",Arial,sans-serif;margin:0;color:var(--ink);min-height:100vh;background:#fff}
  .mono{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace}
  .lbl{font-size:11px;letter-spacing:1.5px;color:#8a857a;text-transform:uppercase}
  .wrap{max-width:960px;margin:0 auto;padding:28px 22px}
  .top-bar{margin-bottom:22px}
  .bird-nav{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;
             padding:12px 16px;background:var(--surface);border:1px solid var(--border);border-radius:10px;font-size:13px}
  .bird-nav a{color:var(--red-dark);text-decoration:none;letter-spacing:.3px;font-weight:600}
  .bird-nav a:hover{text-decoration:underline}
  .bird-nav .prev,.bird-nav .next{flex:1;min-width:0}
  .bird-nav .prev{text-align:left}
  .bird-nav .next{text-align:right}
  .bird-nav .pos{flex:0 0 auto;color:#8a857a;letter-spacing:.5px;font-size:12px;white-space:nowrap}
  .bird-nav .off{color:#b0aca2}
  .back{font-size:12px;letter-spacing:.5px}
  .back a{color:var(--red-dark);text-decoration:none}
  .back a:hover{text-decoration:underline}
  .hero{display:grid;grid-template-columns:auto 1fr;gap:22px;align-items:start;margin-bottom:24px}
  @media(max-width:640px){.hero{grid-template-columns:1fr}}
  .hero-art{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px;text-align:center}
  .hero-art img{display:block;width:254px;height:254px;object-fit:contain}
  @media(max-width:640px){.hero-art img{width:100%;height:auto;max-width:254px}}
  .hero-art .no-art{color:#b0aca2;font-size:12px;padding:40px 8px}
  .hero h1{margin:0 0 4px;font-size:28px;font-weight:800;letter-spacing:.2px}
  .hero .latin{font-style:italic;color:#7a766a;font-size:14px;margin:0 0 14px}
  .scope{font-size:12px;color:#8a857a;letter-spacing:.5px}
  .stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:22px}
  @media(max-width:800px){.stat-grid{grid-template-columns:repeat(2,1fr)}}
  .stat{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
  .stat .val{font-size:22px;font-weight:800;letter-spacing:-.3px}
  .stat .lbl{margin-top:2px}
  .panel{background:#fff;border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin-bottom:18px;
         box-shadow:0 1px 4px rgba(0,0,0,.05)}
  .panel h2{margin:0 0 14px;font-size:13px;letter-spacing:1.2px;text-transform:uppercase;color:#7a766a;font-weight:700}
  .bars{display:flex;align-items:flex-end;gap:3px;height:72px;padding-top:4px}
  .bars .bar{flex:1;background:var(--red);border-radius:3px 3px 0 0;min-width:4px;position:relative}
  .bars .bar.zero{background:#eceae3;min-height:2px}
  .bar-lbls{display:flex;gap:3px;margin-top:4px}
  .bar-lbls span{flex:1;text-align:center;font-size:9px;color:#8a857a;letter-spacing:.3px}
  .conf-row{display:flex;align-items:center;gap:10px;margin-bottom:8px;font-size:12px}
  .conf-row .bucket{width:72px;color:#7a766a}
  .conf-row .track{flex:1;height:14px;background:#eceae3;border-radius:4px;overflow:hidden}
  .conf-row .fill{height:100%;background:var(--red);border-radius:4px}
  .conf-row .n{width:36px;text-align:right;color:#8a857a}
  .daily-table{width:100%;border-collapse:collapse;font-size:12px}
  .daily-table th,.daily-table td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)}
  .daily-table th{color:#8a857a;font-weight:600;font-size:11px;letter-spacing:.5px}
  .daily-table .mono{text-align:right}
  .clips{display:flex;flex-direction:column;gap:14px}
  .clip{border:1px solid var(--border);border-radius:10px;overflow:hidden;background:#fff}
  .clip-hdr{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;
            background:var(--surface);border-bottom:1px solid var(--border);font-size:12px}
  .clip-hdr .conf{font-weight:700;color:var(--red-deep)}
  .clip .spectro{background:#0d1b14}
  .clip .spectro img{display:block;width:100%;height:72px;object-fit:cover}
  .clip .play{padding:8px 10px;background:#f7f6f2;border-top:1px solid var(--border)}
  .clip .play audio{display:block;width:100%;height:32px}
  .pager{display:flex;gap:10px;justify-content:center;margin-top:16px;font-size:12px}
  .pager a{color:var(--red-dark);text-decoration:none;padding:6px 12px;border:1px solid var(--border);border-radius:8px}
  .pager a:hover{background:var(--surface)}
  .pager .off{color:#b0aca2;padding:6px 12px}
  .extra-stats{display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:12px;color:#7a766a}
  @media(max-width:640px){.extra-stats{grid-template-columns:1fr}}
  .extra-stats b{color:var(--ink)}
  .tags{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
  .tag{font-size:11px;letter-spacing:.5px;padding:4px 10px;border-radius:20px;border:1px solid var(--border);
       background:#fff;color:#7a766a}
  .tag.group{background:var(--red);border-color:var(--red);color:#fff;font-weight:700}
  .tag.season{background:var(--surface)}
  .notes-grid{display:flex;flex-direction:column;gap:14px}
  .note-item{display:grid;grid-template-columns:88px 1fr;gap:14px;align-items:baseline}
  @media(max-width:640px){.note-item{grid-template-columns:1fr;gap:2px}}
  .note-item .k{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#8a857a;font-weight:700;padding-top:1px}
  .note-item .v{font-size:14px;line-height:1.55;color:var(--ink)}
  .field-notes h2 .src{float:right;font-size:9px;letter-spacing:.5px;color:#b0aca2;font-weight:600;text-transform:none}
</style></head><body>
<div class="wrap">
  <div class="top-bar">
    <div class="bird-nav mono">
      <span class="prev">
        {% if prev_bird %}
        <a href="{{ bird_href(prev_bird.slug) }}">← {{ prev_bird.name }}</a>
        {% else %}
        <span class="off">←</span>
        {% endif %}
      </span>
      <span class="pos">Nº {{ dex_no }} of {{ dex_total }}</span>
      <span class="next">
        {% if next_bird %}
        <a href="{{ bird_href(next_bird.slug) }}">{{ next_bird.name }} →</a>
        {% else %}
        <span class="off">→</span>
        {% endif %}
      </span>
    </div>
    <div class="back mono"><a href="{{ back_href }}">← Back to Bird-Dex</a> · <a href="/live">Live feed</a> · <a href="/timeline{{ timeline_qs }}">Timeline</a></div>
  </div>

  <div class="hero">
    <div class="hero-art">
      {% if sprite_slug %}
      <img src="/sprite/{{ sprite_slug }}.png" alt="{{ common_name }}">
      {% else %}
      <div class="no-art lbl">No illustration</div>
      {% endif %}
    </div>
    <div>
      <h1>{{ common_name }}</h1>
      <p class="latin">{{ scientific_name }}</p>
      <p class="scope mono">{{ scope_label }}</p>
      {% if discovered_at %}
      <p class="scope mono" style="margin-top:6px">Discovered {{ discovered_at[:10] }}</p>
      {% endif %}
    </div>
  </div>

  {% if info %}
  <div class="panel field-notes">
    <h2>Field notes <span class="src">Santa Barbara soundscape</span></h2>
    <div class="notes-grid">
      {% if info.sound %}
      <div class="note-item"><div class="k">Sound</div><div class="v">{{ info.sound }}</div></div>
      {% endif %}
      {% if info.habitat %}
      <div class="note-item"><div class="k">Where</div><div class="v">{{ info.habitat }}</div></div>
      {% endif %}
      {% if info.note %}
      <div class="note-item"><div class="k">Notes</div><div class="v">{{ info.note }}</div></div>
      {% endif %}
    </div>
    <div class="tags">
      {% if info.group %}<span class="tag group">{{ info.group }}</span>{% endif %}
      {% if info.season %}<span class="tag season">{{ info.season }}</span>{% endif %}
    </div>
  </div>
  {% endif %}

  <div class="stat-grid">
    <div class="stat"><div class="val mono">{{ stats.windows }}</div><div class="lbl">Windows heard</div></div>
    <div class="stat"><div class="val mono">{{ "%.3f"|format(stats.peak_conf) }}</div><div class="lbl">Peak conf</div></div>
    <div class="stat"><div class="val mono">{{ "%.3f"|format(stats.avg_conf) }}</div><div class="lbl">Avg conf</div></div>
    <div class="stat"><div class="val mono">{{ stats.segments }}</div><div class="lbl">Segments</div></div>
    <div class="stat"><div class="val mono">{{ "%.3f"|format(stats.min_conf) }}</div><div class="lbl">Min conf</div></div>
    <div class="stat"><div class="val mono">{{ streak }}</div><div class="lbl">Longest streak</div></div>
    <div class="stat"><div class="val mono">{{ per_seg }}</div><div class="lbl">Win / segment</div></div>
    <div class="stat"><div class="val mono">{{ peak_hour }}</div><div class="lbl">Peak hour</div></div>
  </div>

  <div class="panel">
    <h2>Timing</h2>
    <div class="extra-stats mono">
      <div><b>First heard</b> {{ stats.first_heard[11:19] }} · {{ stats.first_heard[:10] }}</div>
      <div><b>Last heard</b> {{ stats.last_heard[11:19] }} · {{ stats.last_heard[:10] }}</div>
      <div><b>Active span</b> {{ span_label }}</div>
      <div><b>All-time windows</b> {{ all_time_windows }}</div>
    </div>
  </div>

  <div class="panel">
    <h2>Hourly activity</h2>
    <div class="bars">
      {% for h in range(24) %}
      {% set n = hourly.get("%02d"|format(h), 0) %}
      {% set pct = (n * 100 / hourly_max) if hourly_max else 0 %}
      <div class="bar{{ ' zero' if not n else '' }}" style="height:{{ pct if n else 2 }}%" title="{{ h }}:00 — {{ n }}"></div>
      {% endfor %}
    </div>
    <div class="bar-lbls mono">
      {% for h in [0,6,12,18,23] %}
      <span style="flex:6">{{ h }}h</span>
      {% endfor %}
    </div>
  </div>

  <div class="panel">
    <h2>Confidence distribution</h2>
    {% for b in conf_buckets %}
    <div class="conf-row mono">
      <span class="bucket">{{ b.bucket }}</span>
      <div class="track"><div class="fill" style="width:{{ (b.n * 100 / conf_max) if conf_max else 0 }}%"></div></div>
      <span class="n">{{ b.n }}</span>
    </div>
    {% endfor %}
  </div>

  {% if daily %}
  <div class="panel">
    <h2>Daily breakdown</h2>
    <table class="daily-table mono">
      <thead><tr><th>Day</th><th class="mono">Windows</th><th class="mono">Peak conf</th></tr></thead>
      <tbody>
        {% for d in daily %}
        <tr>
          <td><a href="/bird/{{ slug }}?day={{ d.day }}" style="color:var(--red-dark);text-decoration:none">{{ d.day }}</a></td>
          <td class="mono">{{ d.n }}</td>
          <td class="mono">{{ "%.3f"|format(d.peak_conf) }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  <div class="panel">
    <h2>All clips ({{ total_clips }}) · sorted by confidence</h2>
    <div class="clips">
      {% for c in clips %}
      {% set clip_qs = "?start=%.1f&end=%.1f"|format(c.start_time, c.end_time) %}
      <div class="clip">
        <div class="clip-hdr mono">
          <span>#{{ loop.index + clip_offset }} · {{ c.heard_at[11:19] }} · seg {{ c.segment_id }}</span>
          <span class="conf">{{ "%.3f"|format(c.confidence) }}</span>
        </div>
        {% if c.has_audio %}
        <div class="spectro">
          <img loading="lazy" src="/spectrogram/{{ c.segment_id }}.png{{ clip_qs }}" alt="spectrogram">
        </div>
        <div class="play"><audio controls preload="none" src="/audio/{{ c.segment_id }}{{ clip_qs }}"></audio></div>
        {% else %}
        <div class="clip-hdr" style="border-top:1px solid var(--border);color:#b0aca2">Audio discarded</div>
        {% endif %}
      </div>
      {% endfor %}
    </div>
    {% if page > 1 or has_more %}
    <div class="pager mono">
      {% if page > 1 %}<a href="{{ page_qs(page - 1) }}">← Prev</a>{% else %}<span class="off">← Prev</span>{% endif %}
      <span class="off">Page {{ page }}</span>
      {% if has_more %}<a href="{{ page_qs(page + 1) }}">Next →</a>{% else %}<span class="off">Next →</span>{% endif %}
    </div>
    {% endif %}
  </div>
</div>
{% if dev %}{{ dev_script|safe }}{% endif %}
</body></html>
"""


DATA_VIEW = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bird-ID — Data report</title>
<style>
  :root{
    --ink:#1a1a1a;--body:#33363d;--bg:#ffffff;
    --teal:#1f7a8c;--orange:#e8590c;--accent:#b3261e;
    --paper:#fafaf7;--rule:#e7e9ec;--gray-d:#6b7079;
    --g1:#efece4;--g2:#c8c2b5;--g3:#6b665d;
  }
  *{box-sizing:border-box}
  html{background:#eceef1}
  body{margin:0 auto;max-width:820px;background:var(--bg);color:var(--body);
       font:16px/1.5 -apple-system,"Helvetica Neue",Arial,sans-serif;
       padding:0 0 80px;-webkit-font-smoothing:antialiased}
  .topbar{height:7px;background:linear-gradient(90deg,var(--teal) 0 70%,var(--orange) 70% 100%)}
  .wrap{padding:34px 44px 0}
  .nav{font-size:12px;margin-bottom:18px}
  .nav a{color:var(--teal);text-decoration:none;font-weight:700}
  .nav a:hover{text-decoration:underline}
  .day-nav{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px;font-size:12px}
  .day-nav a{color:var(--teal);text-decoration:none;padding:4px 10px;border:1px solid var(--rule);border-radius:6px}
  .day-nav a.on{border-color:var(--accent);color:var(--accent);font-weight:700}
  .kicker{font-size:11px;font-weight:800;letter-spacing:.13em;text-transform:uppercase;color:var(--teal);margin:0 0 12px}
  .kicker b{color:var(--orange)}
  h1{font-size:32px;line-height:1.08;font-weight:800;letter-spacing:-.02em;color:var(--ink);margin:0 0 16px}
  .lede{font-size:17px;line-height:1.45;color:var(--ink);margin:0 0 6px}
  .lede b{font-weight:800}
  .brev{margin:18px 0;padding:0;list-style:none}
  .brev li{position:relative;padding-left:22px;margin:0 0 9px;font-size:15px;line-height:1.45}
  .brev li::before{content:"";position:absolute;left:0;top:7px;width:9px;height:9px;background:var(--teal)}
  .brev li.o::before{background:var(--orange)}
  .brev b{color:var(--ink);font-weight:800}
  h2{font-size:13px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--ink);
     margin:46px 0 2px;padding-top:18px;border-top:2px solid var(--ink);display:flex;align-items:center;gap:9px}
  h2::before{content:"";width:11px;height:11px;background:var(--orange);flex:none}
  .say{font-size:14px;color:var(--body);margin:6px 0 18px}
  .say b{color:var(--ink);font-weight:800}
  table{border-collapse:collapse;width:100%}
  th{font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--gray-d);
     text-align:right;padding:0 0 8px;vertical-align:bottom;border-bottom:2px solid var(--ink)}
  th.l{text-align:left}
  td{padding:7px 0;border-bottom:1px solid var(--rule);vertical-align:middle}
  td.name{font-size:14px;font-weight:700;color:var(--ink)}
  td.sci{font-style:italic;color:var(--gray-d);font-size:12px;padding-left:8px;font-weight:400}
  td.num{text-align:right;font-size:12px;font-weight:600;color:var(--ink);white-space:nowrap;font-family:"SF Mono",ui-monospace,monospace}
  td.num.dim{color:var(--gray-d);font-weight:500}
  td.num.low{color:var(--accent)}
  svg{display:block;overflow:visible}
  .foot{margin-top:54px;padding-top:18px;border-top:1px solid var(--ink);font-size:12.5px;line-height:1.5;color:var(--g3)}
  .foot ol{margin:0;padding-left:20px}
  .foot li{margin:0 0 9px}
  .foot b{color:var(--ink);font-weight:600}
  .mono{font-family:"SF Mono",ui-monospace,monospace;font-size:.92em}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:24px 40px;align-items:start}
  .empty-box{margin-top:40px;padding:32px;background:var(--paper);border:1px solid var(--rule);text-align:center;color:var(--gray-d)}
  .meta{font-family:"SF Mono",ui-monospace,monospace;font-size:11px;color:var(--g3);
        border-top:1px solid var(--ink);border-bottom:1px solid var(--rule);padding:9px 0;margin:0 0 32px;
        display:flex;flex-wrap:wrap;gap:4px 18px}
  .meta b{color:var(--ink);font-weight:600}
  @media(max-width:660px){.grid2{grid-template-columns:1fr}.wrap{padding:24px 20px 0}}
</style>
</head>
<body>
<div class="topbar"></div>
<div class="wrap">
  <div class="nav"><a href="{{ back_href }}">← Bird-Dex</a> · <a href="/live">Live feed</a> · <a href="/timeline{{ timeline_qs }}">Timeline</a></div>
  <div class="day-nav">
    {% if not show_all %}
    <a href="/data?day={{ prev_day }}">← Prev</a>
    <a href="/data?day={{ next_day }}">Next →</a>
    {% if selected_day != today %}<a href="/data">Today</a>{% endif %}
    {% endif %}
    {% if show_all %}<a href="/data" class="on">All time</a>{% else %}<a href="/data?day=all">All time</a>{% endif %}
    <span style="color:var(--gray-d);margin-left:auto">{{ scope_label }}</span>
  </div>

  {% if empty %}
  <div class="empty-box">
    <p>No detections {{ scope_label }}.</p>
    <p><a href="{{ back_href }}">Back to Bird-Dex</a></p>
  </div>
  {% else %}
  <p class="kicker">Bird-ID · backyard acoustics &nbsp;//&nbsp; <b>Data report</b></p>
  <h1>{{ headline }}</h1>
  <p class="lede">{{ lede|safe }}</p>
  <ul class="brev">
    {% for b in bullets %}
    <li class="{{ 'o' if b.orange else '' }}">{{ b.text|safe }}</li>
    {% endfor %}
  </ul>
  <div class="meta">
    {% if not show_all %}<span><b>{{ selected_day }}</b></span>{% else %}<span><b>All time</b></span>{% endif %}
    <span>{{ "%.2f"|format(lat) }}°N {{ "%.2f"|format(lon) }}°W</span>
    <span><b>{{ meta.windows }}</b> windows</span>
    <span><b>{{ meta.audio_min }}</b> min audio</span>
    <span><b>{{ meta.detections }}</b> detections</span>
    <span><b>{{ meta.species }}</b> species</span>
    <span>BirdNET · min_conf <b>{{ min_conf }}</b></span>
  </div>

  <h2>Who showed up</h2>
  <p class="say"><b>The big picture:</b> Detections per species, with every call plotted at the model's confidence ({{ min_conf }}&ndash;1.00). The red dot is each bird's best hit.</p>
  <table id="roll"><thead><tr>
    <th class="l">Species</th><th class="l">&nbsp;</th><th>n</th>
    <th class="l" style="padding-left:14px">count</th>
    <th class="l" style="padding-left:14px">confidence</th>
    <th>best</th><th>wins</th>
  </tr></thead><tbody></tbody></table>

  <h2>Morning bird or afternoon bird?</h2>
  <p class="say"><b>Between the lines:</b> Share of each species' calls from the morning block vs the afternoon window. The dashed line is the expected morning share given recording duration alone.</p>
  <svg id="diel" width="100%" viewBox="0 0 760 300"></svg>

  <div class="grid2">
  <div>
  <h2 id="panel3-title">Detections per minute</h2>
  <p class="say" id="panel3-say"></p>
  <svg id="rate" width="100%" viewBox="0 0 360 230"></svg>
  </div>
  <div>
  <h2 id="panel4-title">Activity curve</h2>
  <p class="say" id="panel4-say"></p>
  <svg id="curve" width="100%" viewBox="0 0 360 230"></svg>
  </div>
  </div>

  <div class="foot">
  <ol>
    <li><b>Recording is discontinuous.</b> Gaps between windows are <b>unrecorded, not quiet</b>.</li>
    <li><b>Counts are calls, not birds.</b> One persistent bird near the mic inflates counts freely.</li>
    <li><b>Confidence ≠ correctness.</b> Left edge is <b>censored at <span class="mono">min_conf {{ min_conf }}</span></b> — pileups there reflect the cutoff.</li>
    {% if tentative %}<li><b>Tentative IDs:</b> {{ tentative|join(', ') }} — treat as unconfirmed.</li>{% endif %}
    {% if not show_all %}<li><b>One day, one yard.</b> Morning vs afternoon here means which recording, not bird behavior.</li>{% endif %}
  </ol>
  </div>
  {% endif %}
</div>
{% if not empty %}
<script>
const R={{ report_json|safe }};
const T={ink:"#1a1a1a",accent:"#b3261e",teal:"#1f7a8c",orange:"#e8590c",g1:"#efece4",g2:"#c8c2b5",g3:"#6b665d",gray:"#c2c6cd"};
const NS="http://www.w3.org/2000/svg";
function el(t,a){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;}
function txt(p,x,y,s,a){const t=el("text",Object.assign({x,y},a));t.textContent=s;p.appendChild(t);return t;}
const FM='"SF Mono",ui-monospace,monospace';

(function(){
 const tb=document.querySelector("#roll tbody");
 const SP=R.species, CW=120, cMax=Math.max(...SP.map(s=>s.c),1), SW=260, lo=R.min_conf, hi=1.0;
 const sx=c=>Math.max(0,(c-lo)/(hi-lo)*SW);
 SP.forEach(s=>{
   const tr=document.createElement("tr");
   tr.innerHTML=`<td class="name">${s.n}</td><td class="sci">${s.sci}</td><td class="num">${s.c}</td>`;
   const tdC=document.createElement("td");tdC.style.paddingLeft="14px";
   const svgC=el("svg",{width:CW,height:14,viewBox:`0 0 ${CW} 14`});
   svgC.appendChild(el("line",{x1:0,y1:7,x2:s.c/cMax*CW,y2:7,stroke:T.ink,"stroke-width":6,opacity:.85}));
   tdC.appendChild(svgC);tr.appendChild(tdC);
   const tdS=document.createElement("td");tdS.style.paddingLeft="14px";const H=16;
   const svgS=el("svg",{width:SW+8,height:H,viewBox:`0 0 ${SW+8} ${H}`});
   svgS.appendChild(el("line",{x1:0,y1:H-2,x2:SW,y2:H-2,stroke:T.g2,"stroke-width":.5}));
   s.conf.forEach(c=>svgS.appendChild(el("line",{x1:sx(c),y1:2,x2:sx(c),y2:H-2,stroke:T.g3,"stroke-width":1,opacity:.32})));
   svgS.appendChild(el("circle",{cx:sx(s.max),cy:H/2,r:2.6,fill:T.accent}));
   tdS.appendChild(svgS);tr.appendChild(tdS);
   const tdB=document.createElement("td");tdB.className="num"+(s.tentative?" low":"");tdB.textContent=s.max.toFixed(3);
   tr.appendChild(tdB);
   const tdW=document.createElement("td");tdW.className="num dim";
   const sw=el("svg",{width:Math.max(34,s.wins.length*8+10),height:10,viewBox:`0 0 ${Math.max(34,s.wins.length*8+10)} 10`});
   s.wins.forEach((w,i)=>sw.appendChild(el("circle",{cx:5+i*8,cy:5,r:2.4,fill:w?T.ink:"none",stroke:T.g2,"stroke-width":w?0:1})));
   tdW.appendChild(sw);tr.appendChild(tdW);
   tb.appendChild(tr);
 });
})();

(function(){
 const svg=document.getElementById("diel");
 const x0=190,x1=620,top=26,rh=23,baseAM=R.base_am_pct/100,W=x1-x0;
 const data=[...R.species].filter(s=>s.am+s.pm>0).sort((a,b)=>(b.am/(b.am+b.pm))-(a.am/(a.am+b.pm))||b.c-a.c);
 const bx=x0+baseAM*W;
 svg.appendChild(el("line",{x1:bx,y1:top-9,x2:bx,y2:top+data.length*rh+2,stroke:T.accent,"stroke-width":.8,"stroke-dasharray":"3 3"}));
 txt(svg,bx,top-13,R.base_am_pct+"% = expected morning share",{"text-anchor":"middle","font-family":FM,"font-size":9.5,fill:T.accent});
 txt(svg,x0,top-13,"morning",{"font-family":FM,"font-size":9.5,fill:T.orange});
 txt(svg,x1,top-13,"afternoon",{"font-family":FM,"font-size":9.5,fill:T.teal,"text-anchor":"end"});
 data.forEach((s,i)=>{
   const y=top+i*rh,tot=s.am+s.pm,amShare=tot?s.am/tot:0,split=x0+amShare*W;
   svg.appendChild(el("line",{x1:x0,y1:y+8,x2:split,y2:y+8,stroke:T.orange,"stroke-width":7,opacity:.85}));
   svg.appendChild(el("line",{x1:split,y1:y+8,x2:x1,y2:y+8,stroke:T.teal,"stroke-width":7,opacity:.85}));
   txt(svg,x0-10,y+11,s.n,{"text-anchor":"end","font-size":12,fill:T.ink});
   if(s.am)txt(svg,x0+3,y+11,s.am,{"font-family":FM,"font-size":9.5,fill:"#fff"});
   if(s.pm)txt(svg,x1-3,y+11,s.pm,{"font-family":FM,"font-size":9.5,fill:"#fff","text-anchor":"end"});
 });
})();

if(R.show_all){
 document.getElementById("panel3-title").textContent="Activity by day";
 document.getElementById("panel3-say").innerHTML="<b>Zoom out:</b> Detections per minute of tape, by calendar day.";
 document.getElementById("panel4-title").textContent="Daily rhythm";
 document.getElementById("panel4-say").innerHTML="<b>All hours:</b> Aggregate detections by clock hour across every recorded day.";
 (function(){
   const svg=document.getElementById("rate");
   const DR=R.daily_rates,x0=58,x1=300,top=18,rh=Math.min(34,180/Math.max(DR.length,1)),rMax=Math.max(...DR.map(d=>d.rate),1);
   const sx=v=>x0+v/rMax*(x1-x0);
   [0,Math.round(rMax/2),Math.round(rMax)].filter((v,i,a)=>a.indexOf(v)===i).forEach(v=>txt(svg,sx(v),top-8,v,{"text-anchor":"middle","font-family":FM,"font-size":9,fill:T.g3}));
   txt(svg,x0+(x1-x0)/2,top+DR.length*rh+4,"det / min",{"text-anchor":"middle","font-family":FM,"font-size":9,fill:T.g3});
   DR.forEach((d,i)=>{
     const y=top+i*rh+8;
     svg.appendChild(el("line",{x1:x0,y1:y,x2:sx(d.rate),y2:y,stroke:T.g2,"stroke-width":.5}));
     svg.appendChild(el("circle",{cx:sx(d.rate),cy:y,r:3.5,fill:T.accent}));
     txt(svg,x0-8,y+3,d.day.slice(5),{"text-anchor":"end","font-family":FM,"font-size":9,fill:T.ink});
     txt(svg,sx(d.rate)+8,y+3,d.rate.toFixed(1),{"font-family":FM,"font-size":10,fill:T.ink,"font-weight":600});
   });
 })();
 (function(){
   const svg=document.getElementById("curve");
   const H=R.hourly,x0=30,x1=345,y0=170,y1=20,yMax=Math.max(...H,1);
   const sx=i=>x0+i/23*(x1-x0),sy=v=>y0-v/yMax*(y0-y1);
   [0,Math.round(yMax/2),yMax].filter((v,i,a)=>a.indexOf(v)===i).forEach(v=>txt(svg,x0-7,sy(v)+3,v,{"text-anchor":"end","font-family":FM,"font-size":9,fill:T.g3}));
   svg.appendChild(el("line",{x1:x0,y1:y0,x2:x0,y2:sy(yMax),stroke:T.g3,"stroke-width":.5}));
   let l="";H.forEach((v,i)=>l+=(i?"L":"M")+` ${sx(i)} ${sy(v)} `);
   svg.appendChild(el("path",{d:l,fill:"none",stroke:T.ink,"stroke-width":1.4}));
   const pk=H.indexOf(yMax);
   svg.appendChild(el("circle",{cx:sx(pk),cy:sy(yMax),r:3,fill:T.accent}));
   txt(svg,sx(pk),sy(yMax)-7,yMax+" @ "+pk+":00",{"text-anchor":"middle","font-family":FM,"font-size":9,fill:T.accent});
   txt(svg,x0,y0+14,"0h",{"font-family":FM,"font-size":9,fill:T.g3});
   txt(svg,x1,y0+14,"23h",{"text-anchor":"end","font-family":FM,"font-size":9,fill:T.g3});
 })();
}else{
 document.getElementById("panel3-say").innerHTML="Rate per recording window — dawn blocks vs the long afternoon window.";
 document.getElementById("panel4-say").innerHTML="Calls per minute across the longest afternoon segment ("+R.curve.start+"&ndash;"+R.curve.end+").";
 (function(){
   const svg=document.getElementById("rate");
   const WIN=R.windows,x0=58,x1=300,top=18,rh=40,rMax=Math.max(...WIN.map(w=>w.det/w.min),1);
   const sx=v=>x0+v/rMax*(x1-x0);
   [0,Math.round(rMax/2),Math.round(rMax)].filter((v,i,a)=>a.indexOf(v)===i).forEach(v=>txt(svg,sx(v),top-8,v,{"text-anchor":"middle","font-family":FM,"font-size":9,fill:T.g3}));
   txt(svg,x0+(x1-x0)/2,top+WIN.length*rh+4,"det / min",{"text-anchor":"middle","font-family":FM,"font-size":9,fill:T.g3});
   WIN.forEach((w,i)=>{
     const y=top+i*rh+8,r=w.det/w.min;
     svg.appendChild(el("line",{x1:x0,y1:y,x2:sx(r),y2:y,stroke:T.g2,"stroke-width":.5}));
     svg.appendChild(el("circle",{cx:sx(r),cy:y,r:4,fill:i===WIN.length-1?T.accent:T.ink}));
     txt(svg,x0-8,y+3,w.clock,{"text-anchor":"end","font-family":FM,"font-size":10,fill:T.ink});
     txt(svg,sx(r)+8,y+3,r.toFixed(1),{"font-family":FM,"font-size":10,fill:T.ink,"font-weight":600});
   });
 })();
 (function(){
   const svg=document.getElementById("curve");
   const SEG4=R.curve.bins,x0=30,x1=345,y0=170,y1=20,n=SEG4.length,yMax=Math.max(...SEG4,1);
   const sx=i=>n>1?x0+i/(n-1)*(x1-x0):x0,sy=v=>y0-v/yMax*(y0-y1);
   [0,Math.round(yMax/2),yMax].filter((v,i,a)=>a.indexOf(v)===i).forEach(v=>txt(svg,x0-7,sy(v)+3,v,{"text-anchor":"end","font-family":FM,"font-size":9,fill:T.g3}));
   svg.appendChild(el("line",{x1:x0,y1:y0,x2:x0,y2:sy(yMax),stroke:T.g3,"stroke-width":.5}));
   let l="";SEG4.forEach((v,i)=>l+=(i?"L":"M")+` ${sx(i)} ${sy(v)} `);
   svg.appendChild(el("path",{d:l,fill:"none",stroke:T.ink,"stroke-width":1.4}));
   const pk=SEG4.indexOf(yMax);
   svg.appendChild(el("circle",{cx:sx(pk),cy:sy(yMax),r:3,fill:T.accent}));
   txt(svg,sx(pk),sy(yMax)-7,String(yMax),{"text-anchor":"middle","font-family":FM,"font-size":10,fill:T.accent});
   txt(svg,x0,y0+14,R.curve.start,{"font-family":FM,"font-size":9,fill:T.g3});
   txt(svg,x1,y0+14,R.curve.end,{"text-anchor":"end","font-family":FM,"font-size":9,fill:T.g3});
 })();
}
</script>
{% endif %}
{% if dev %}{{ dev_script|safe }}{% endif %}
</body></html>
"""

TIMELINE_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>Timeline · Bird-Dex</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--red:#d83a36;--red-dark:#a82826;--red-deep:#7d1c1b;--cream:#f3efe2;--ink:#21232a;
        --gold:#ffcf3f;--grn:#46c66b;--surface:#f7f6f2;--border:#e2e0d8;
        --night:#10131f;--night2:#1d2238;--dawn:#d98a4a;--dusk:#c0563f;--day:#f5eedd;
        --lane-w:188px}
  *{box-sizing:border-box}
  body{font-family:"Helvetica Neue",Arial,sans-serif;margin:0;color:var(--ink);min-height:100vh;
       background:radial-gradient(1200px 480px at 50% -8%,#fbfaf6 0,#f2f1ec 60%,#edece6 100%)}
  .mono{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace}
  .lbl{font-size:11px;letter-spacing:1.5px;color:#8a857a}
  .wrap{max-width:1040px;margin:0 auto;padding:26px 22px 60px}
  .day-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:16px;padding:10px 14px;
           background:rgba(255,255,255,.7);backdrop-filter:blur(6px);border:1px solid var(--border);border-radius:12px}
  .day-bar a{font-size:12px;letter-spacing:.5px;text-decoration:none;padding:6px 12px;border-radius:8px;
             border:1px solid var(--border);color:#7a766a;background:#fff;transition:.15s}
  .day-bar a.on{background:#fff;border-color:var(--red);color:var(--red-deep);font-weight:700}
  .day-bar a:hover{border-color:#c8c4ba;color:var(--ink);transform:translateY(-1px)}
  .day-bar form{display:flex;align-items:center;gap:8px;margin:0}
  .day-bar input[type=date]{font-size:12px;padding:6px 10px;border:1px solid var(--border);border-radius:8px;
                            font-family:inherit;background:#fff}
  .day-bar button{font-size:12px;padding:6px 12px;border-radius:8px;border:1px solid var(--border);
                 background:#fff;cursor:pointer}
  .day-bar .spacer{flex:1}
  .hdr{margin-bottom:18px}
  .hdr h1{margin:0 0 6px;font-size:30px;letter-spacing:-.6px;font-weight:800;
          background:linear-gradient(92deg,var(--ink),var(--red-dark));-webkit-background-clip:text;
          background-clip:text;-webkit-text-fill-color:transparent}
  .hdr p{margin:0;color:#8a857a;font-size:14px;line-height:1.5}
  .hdr .pill{display:inline-block;font-size:11px;letter-spacing:.4px;color:var(--red-deep);font-weight:700;
             background:#fff;border:1px solid #ecd9c2;border-radius:20px;padding:2px 10px;margin-left:6px}

  /* solar arc + dawn-chorus ribbon */
  .arc{position:relative;border-radius:16px;overflow:hidden;border:1px solid #d9d6cc;margin-bottom:20px;
       box-shadow:0 8px 26px rgba(20,18,14,.10)}
  .arc-sky{position:relative;height:118px;
    background:linear-gradient(90deg,
      var(--night) 0%, var(--night2) calc(var(--sr) - 7%),
      var(--dawn) var(--sr), var(--day) calc(var(--sr) + 6%),
      var(--day) calc(var(--ss) - 6%), var(--dusk) var(--ss),
      var(--night2) calc(var(--ss) + 7%), var(--night) 100%)}
  .stars{position:absolute;inset:0;opacity:.7;pointer-events:none;
    background-image:radial-gradient(1px 1px at 6% 30%,#fff,transparent),
      radial-gradient(1px 1px at 12% 60%,#fff,transparent),
      radial-gradient(1px 1px at 90% 40%,#fff,transparent),
      radial-gradient(1px 1px at 95% 70%,#fff,transparent),
      radial-gradient(1px 1px at 3% 70%,#fff,transparent)}
  .sun{position:absolute;top:24px;width:30px;height:30px;border-radius:50%;transform:translateX(-50%);
       background:radial-gradient(circle,#fff8e1 0,#ffd34d 55%,#ffb43d 100%);
       box-shadow:0 0 22px 6px rgba(255,196,70,.55)}
  .chorus{position:absolute;inset:0;display:flex;align-items:flex-end;gap:1px;padding:0 1px}
  .chorus .bar{flex:1;min-height:1px;border-radius:2px 2px 0 0;
               background:linear-gradient(180deg,rgba(255,255,255,.95),rgba(255,255,255,.32));
               box-shadow:0 0 0 .5px rgba(0,0,0,.04);transition:height .6s cubic-bezier(.2,.8,.2,1)}
  .chorus .bar.peak{background:linear-gradient(180deg,var(--gold),rgba(255,207,63,.45));
                    box-shadow:0 0 10px rgba(255,207,63,.7)}
  .arc-foot{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 14px;
            background:#fff;border-top:1px solid #ecebe4;font-size:11px;color:#8a857a;letter-spacing:.4px}
  .arc-foot .sr,.arc-foot .ss{display:flex;align-items:center;gap:6px;font-weight:600;color:#6c6a62}
  .arc-foot .dot{width:8px;height:8px;border-radius:50%}
  .arc-foot .sr .dot{background:var(--dawn)}.arc-foot .ss .dot{background:var(--dusk)}
  .arc-foot .mid{color:var(--red-deep);font-weight:700}

  /* stat cards */
  .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:22px}
  @media(max-width:620px){.cards{grid-template-columns:repeat(2,1fr)}}
  .card{background:#fff;border:1px solid var(--border);border-radius:13px;padding:13px 15px;
        box-shadow:0 2px 8px rgba(0,0,0,.05)}
  .card .n{font-size:24px;font-weight:800;letter-spacing:-.4px;line-height:1}
  .card .n small{font-size:13px;color:#b0aca2;font-weight:700}
  .card .lbl{margin-top:5px}
  .card.accent{background:linear-gradient(150deg,#fff,#fff6f4);border-color:#f0d6cf}
  .card.accent .n{color:var(--red-deep)}

  .legend{display:flex;align-items:center;gap:10px;margin:0 0 12px;font-size:11px;color:#8a857a;letter-spacing:.4px}
  .legend .ramp{height:8px;width:120px;border-radius:5px;
    background:linear-gradient(90deg,#f0a500,#d83a36,#7d1c1b)}

  /* swimlanes */
  .lanes{display:flex;flex-direction:column;gap:7px}
  .lane{display:grid;grid-template-columns:var(--lane-w) 1fr;gap:14px;align-items:center;
        background:#fff;border:1px solid var(--border);border-radius:12px;padding:8px 12px 8px 8px;
        box-shadow:0 1px 3px rgba(0,0,0,.04);transition:.18s}
  .lane:hover{border-color:#d8b9b2;box-shadow:0 4px 14px rgba(125,28,27,.10);transform:translateY(-1px)}
  .lane-name{display:flex;align-items:center;gap:9px;min-width:0}
  .lane-rank{flex:0 0 22px;font-size:12px;font-weight:800;color:#c3beb2;text-align:right}
  .lane-thumb{width:38px;height:38px;border-radius:50%;border:1px solid var(--border);flex-shrink:0;
              display:flex;align-items:center;justify-content:center;overflow:hidden;
              background:radial-gradient(circle at 30% 25%,#fff,#f1efe7)}
  .lane-thumb img{width:100%;height:100%;object-fit:contain;padding:3px}
  .lane-thumb .ini{font-size:12px;font-weight:800;color:#b8b3a7}
  .lane-meta{min-width:0}
  .lane-meta a{color:var(--ink);text-decoration:none;font-size:13.5px;font-weight:700;display:block;
               white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lane-meta a:hover{color:var(--red-deep)}
  .lane-sub{font-size:10.5px;color:#9a958a;letter-spacing:.3px;margin-top:1px}
  .lane-sub b{color:var(--red-dark)}
  .lane-track{position:relative;height:34px;border-radius:9px;border:1px solid #e7e5dc;overflow:hidden;
    background:linear-gradient(90deg,
      #edeef4 0%, #f0f0f5 calc(var(--sr) - 6%),
      #fbf1e2 var(--sr), #ffffff calc(var(--sr) + 5%),
      #ffffff calc(var(--ss) - 5%), #fbeee6 var(--ss),
      #f0f0f5 calc(var(--ss) + 6%), #edeef4 100%)}
  .lane-base{position:absolute;left:0;right:0;bottom:0;height:1px;background:rgba(0,0,0,.06)}
  .lane-bar{position:absolute;bottom:0;border-radius:2px 2px 0 0;min-height:2px;cursor:default;
            transform:scaleY(0);transform-origin:bottom;box-shadow:0 0 0 .5px rgba(255,255,255,.35);
            animation:grow .5s cubic-bezier(.2,.9,.3,1) forwards}
  @keyframes grow{to{transform:scaleY(1)}}
  .now-line{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--grn);z-index:3;
            box-shadow:0 0 7px rgba(70,198,107,.8)}
  .now-line::after{content:"";position:absolute;top:-3px;left:-2px;width:6px;height:6px;border-radius:50%;background:var(--grn)}
  .axis{position:relative;height:16px;margin:8px 0 0;margin-left:calc(var(--lane-w) + 14px)}
  .axis span{position:absolute;transform:translateX(-50%);font-size:10px;color:#9a958a}
  .axis .sun-tick{color:var(--red-deep);font-weight:700}
  @media(prefers-reduced-motion:reduce){
    .lane-bar{animation:none;transform:scaleY(1)}
    .chorus .bar{transition:none}
    .lane:hover,.day-bar a:hover{transform:none}
  }

  /* all-time day bars */
  .day-rows{display:flex;flex-direction:column;gap:6px}
  .day-row{display:grid;grid-template-columns:84px 1fr 54px 46px;gap:12px;align-items:center;font-size:13px;
           background:#fff;border:1px solid var(--border);border-radius:10px;padding:7px 12px}
  .day-row a.day-link{color:var(--red-dark);text-decoration:none;font-weight:700}
  .day-row a.day-link:hover{text-decoration:underline}
  .day-track{height:18px;background:#f3f1ea;border-radius:6px;overflow:hidden}
  .day-fill{height:100%;background:linear-gradient(90deg,#f0a500,#d83a36);border-radius:6px;min-width:3px}
  .day-num{text-align:right;font-weight:800}
  .day-sp{font-size:11px;color:#8a857a;text-align:right}
  .empty{background:#fff;border:1px solid var(--border);border-radius:14px;padding:50px;
         text-align:center;color:#7a766a;font-size:15px}
  .nav-top{font-size:12px;margin-bottom:14px}
  .nav-top a{color:var(--red-dark);text-decoration:none;font-weight:600;margin-right:14px}
  .nav-top a:hover{text-decoration:underline}
  @media(max-width:600px){
    :root{--lane-w:1fr}
    .lane{grid-template-columns:1fr;gap:6px}
    .axis{margin-left:0}
    .day-row{grid-template-columns:70px 1fr 44px 40px;gap:8px;font-size:12px}
  }
</style></head><body>
<div class="wrap">
  <div class="nav-top mono"><a href="{{ back_href }}">← Bird-Dex</a><a href="/live">Live feed</a><a href="/data{{ data_qs }}">Data report</a></div>
  <div class="day-bar mono">
    {% if not show_all %}
    <a href="/timeline?day={{ prev_day }}">← Prev</a>
    <a href="/timeline?day={{ next_day }}">Next →</a>
    {% endif %}
    <form method="get" action="/timeline">
      <label class="lbl" for="day-pick">Day</label>
      <input type="date" id="day-pick" name="day" value="{{ selected_day }}" max="{{ today }}">
      <button type="submit">Go</button>
    </form>
    {% if not show_all and selected_day != today %}
    <a href="/timeline">Today</a>
    {% endif %}
    <span class="spacer"></span>
    {% if show_all %}
    <a href="/timeline" class="on">All time</a>
    {% else %}
    <a href="/timeline?day=all">All time</a>
    {% endif %}
    <span class="lbl">{{ scope_label }}</span>
  </div>

  <div class="hdr">
    <h1>Timeline</h1>
    {% if show_all %}
    <p>Occurrences grouped by day — click a date to see the hour-by-hour swimlanes.<span class="pill">confidence ≥ {{ "%.1f"|format(conf_floor) }}</span></p>
    {% else %}
    <p>When each species sang {{ scope_label }}, mapped against the day's light. Bars are 15-minute counts — taller means busier, warmer means more confident.<span class="pill">confidence ≥ {{ "%.1f"|format(conf_floor) }}</span></p>
    {% endif %}
  </div>

  {% if empty %}
  <div class="empty">No detections {{ scope_label }} above {{ "%.1f"|format(conf_floor) }} confidence.</div>
  {% elif tl.mode == 'hours' %}
  <div class="arc" style="--sr:{{ '%.2f'|format(tl.sun.sunrise_pct) }}%;--ss:{{ '%.2f'|format(tl.sun.sunset_pct) }}%">
    <div class="arc-sky">
      <div class="stars"></div>
      <div class="sun" style="left:{{ '%.2f'|format(tl.sun.noon_pct) }}%"></div>
      <div class="chorus">
        {% for n in tl.hourly %}
        <div class="bar{{ ' peak' if loop.index0 == tl.busiest_hour else '' }}"
             style="height:{{ (12 + n * 86 / tl.hourly_max) if n else 0 }}%"
             title="{{ loop.index0 }}:00 — {{ n }} detection{{ '' if n == 1 else 's' }}"></div>
        {% endfor %}
      </div>
      {% if tl.now_pct is not none %}<div class="now-line" style="left:{{ '%.2f'|format(tl.now_pct) }}%"></div>{% endif %}
    </div>
    <div class="arc-foot mono">
      <span class="sr"><span class="dot"></span>Sunrise {{ tl.sun.sunrise_label }}</span>
      <span class="mid">{% if tl.busiest_hour is not none %}Dawn chorus peaks ~{{ tl.busiest_hour }}:00{% endif %}</span>
      <span class="ss">Sunset {{ tl.sun.sunset_label }}<span class="dot"></span></span>
    </div>
  </div>

  <div class="cards mono">
    <div class="card accent"><div class="n">{{ tl.total }}</div><div class="lbl">Detections</div></div>
    <div class="card"><div class="n">{{ tl.species }}</div><div class="lbl">Species</div></div>
    <div class="card"><div class="n">{% if tl.busiest_hour is not none %}{{ tl.busiest_hour }}<small>:00</small>{% else %}—{% endif %}</div><div class="lbl">Busiest hour</div></div>
    <div class="card"><div class="n" style="font-size:15px;font-weight:700;line-height:1.25">{{ tl.peak_lane or '—' }}</div><div class="lbl">Most vocal</div></div>
  </div>

  <div class="legend mono">
    <span>LOW</span><span class="ramp"></span><span>HIGH CONFIDENCE</span>
    <span style="flex:1"></span><span>{{ "%.1f"|format(conf_floor) }} → 1.00</span>
  </div>

  <div class="lanes" style="--sr:{{ '%.2f'|format(tl.sun.sunrise_pct) }}%;--ss:{{ '%.2f'|format(tl.sun.sunset_pct) }}%">
    {% for lane in tl.lanes %}
    <div class="lane">
      <div class="lane-name">
        <span class="lane-rank">{{ loop.index }}</span>
        <span class="lane-thumb">
          {% if lane.sprite %}<img src="/sprite/{{ lane.sprite }}.png" alt="" loading="lazy">
          {% else %}<span class="ini">{{ lane.name[:2]|upper }}</span>{% endif %}
        </span>
        <span class="lane-meta">
          <a href="/bird/{{ lane.slug }}?day={{ selected_day }}">{{ lane.name }}</a>
          <span class="lane-sub mono"><b>{{ lane.count }}×</b> · {{ lane.first_label }}–{{ lane.last_label }} · peak {{ "%.2f"|format(lane.peak_conf) }}</span>
        </span>
      </div>
      <div class="lane-track">
        <div class="lane-base"></div>
        {% for b in lane.bars %}
        <span class="lane-bar"
              style="left:{{ '%.3f'|format(b.left) }}%;width:{{ '%.3f'|format(b.width) }}%;height:{{ '%.1f'|format(b.h) }}%;background:{{ b.color }};animation-delay:{{ '%.0f'|format(loop.index0 * 12) }}ms"
              title="{{ b.title }}"></span>
        {% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
  <div class="axis mono">
    <span style="left:0">12 AM</span>
    <span style="left:{{ '%.2f'|format(tl.sun.sunrise_pct) }}%" class="sun-tick">↑ dawn</span>
    <span style="left:50%">noon</span>
    <span style="left:{{ '%.2f'|format(tl.sun.sunset_pct) }}%" class="sun-tick">dusk ↓</span>
    <span style="left:100%">12 AM</span>
  </div>
  {% else %}
  <div class="cards mono">
    <div class="card accent"><div class="n">{{ tl.total }}</div><div class="lbl">Detections</div></div>
    <div class="card"><div class="n">{{ tl.days|length }}</div><div class="lbl">Days recorded</div></div>
  </div>
  <div class="day-rows">
    {% for d in tl.days %}
    <div class="day-row mono">
      <a class="day-link" href="/timeline?day={{ d.day }}">{{ d.label }}</a>
      <div class="day-track">
        <div class="day-fill" style="width:{{ (d.n * 100 / tl.max_n) if tl.max_n else 0 }}%"></div>
      </div>
      <span class="day-num">{{ d.n }}</span>
      <span class="day-sp">{{ d.species }}sp</span>
    </div>
    {% endfor %}
  </div>
  {% endif %}
</div>
{% if dev %}{{ dev_script|safe }}{% endif %}
</body></html>
"""

LIVE_PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>Live feed · Bird-Dex</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--red:#d83a36;--red-dark:#a82826;--cream:#f3efe2;--ink:#21232a;
        --gold:#ffcf3f;--grn:#46c66b;--surface:#f7f6f2;--border:#e2e0d8}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{font-family:"Helvetica Neue",Arial,sans-serif;margin:0;color:var(--ink);min-height:100vh;
       background:var(--surface)}
  .mono{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace}
  .wrap{max-width:640px;margin:0 auto;padding:0 0 32px}
  .hdr{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid var(--border);
       padding:14px 16px 12px}
  .hdr h1{margin:0;font-size:20px;letter-spacing:-.3px}
  .hdr-meta{display:flex;align-items:center;gap:10px;margin-top:6px;font-size:12px;color:#8a857a}
  .live-dot{width:8px;height:8px;border-radius:50%;background:var(--grn);flex-shrink:0;
            animation:pulse 2s ease-in-out infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
  @keyframes flash{from{background:#e8f8ed}to{background:#fff}}
  .nav{font-size:12px;margin-top:10px}
  .nav a{color:var(--red-dark);text-decoration:none;font-weight:600;margin-right:14px}
  .nav a:hover{text-decoration:underline}
  .filters{display:flex;align-items:center;gap:10px;margin-top:10px;font-size:12px}
  .filters a{color:var(--red-dark);text-decoration:none;font-weight:600;padding:4px 10px;
             border:1px solid var(--border);border-radius:999px;background:#fff}
  .filters a.on{background:var(--ink);color:#fff;border-color:var(--ink)}
  .feed{list-style:none;margin:0;padding:0}
  .evt{background:#fff;border-bottom:1px solid var(--border);padding:12px 16px;
       animation:flash .8s ease-out}
  .evt-row{display:flex;align-items:center;gap:10px;min-height:44px}
  .evt-time{flex:0 0 58px;font-size:12px;color:#8a857a;text-align:right}
  .evt-main{flex:1;min-width:0}
  .evt-name{margin:0;font-size:15px;font-weight:700;line-height:1.25}
  .evt-name a{color:inherit;text-decoration:none}
  .evt-name a:hover{color:var(--red-dark)}
  .evt-conf{font-size:11px;color:#8a857a;margin-top:2px}
  .evt-conf b{color:var(--ink)}
  .evt-thumb{flex:0 0 56px;width:56px;height:56px;border-radius:8px;border:1px solid var(--border);
             background:#fff;display:flex;align-items:center;justify-content:center;overflow:hidden}
  .evt-thumb img{width:100%;height:100%;object-fit:contain;padding:2px}
  .evt-thumb .ini{font-size:13px;font-weight:700;color:#b0aca2}
  .evt-spec{background:#0d1b14;margin-top:8px;border-radius:4px;overflow:hidden}
  .evt-spec img{display:block;width:100%;height:72px;object-fit:cover}
  .evt audio{width:100%;margin-top:8px;height:32px}
  .empty{padding:48px 16px;text-align:center;color:#8a857a;font-size:14px;line-height:1.5}
  @media(max-width:480px){
    .hdr{padding:12px 14px 10px}
    .evt{padding:10px 14px}
    .evt-time{flex:0 0 52px;font-size:11px}
    .evt-thumb{flex:0 0 48px;width:48px;height:48px}
  }
</style></head><body>
<div class="wrap">
  <div class="hdr">
    <h1>Live feed</h1>
    <div class="hdr-meta mono">
      <span class="live-dot" aria-hidden="true"></span>
      <span>Last 24h</span>
      <span>·</span>
      <span id="updated">Updated just now</span>
    </div>
    <div class="nav mono">
      <a href="/">← Bird-Dex</a>
      <a href="/timeline">Timeline</a>
      <a href="/data">Data report</a>
    </div>
    <div class="filters mono">
      <a href="/live{% if not hide_low %}?hide_low=1{% endif %}" class="{{ 'on' if hide_low else '' }}">Conf ≥ {{ "%.1f"|format(conf_floor) }}</a>
    </div>
  </div>
  {% if events %}
  <ul class="feed" id="feed">
    {% for e in events %}
    <li class="evt" data-seg="{{ e.segment_id }}" data-name="{{ e.common_name }}">
      <div class="evt-row">
        <time class="evt-time mono">{{ e.time_label }}</time>
        <div class="evt-main">
          <p class="evt-name"><a href="/bird/{{ e.slug }}">{{ e.common_name }}</a></p>
          <div class="evt-conf mono"><b>{{ "%.0f"|format(e.peak_conf * 100) }}%</b> confidence</div>
        </div>
        <div class="evt-thumb">
          {% if e.sprite_slug %}
          <img src="/sprite/{{ e.sprite_slug }}.png" alt="" loading="lazy">
          {% else %}
          <span class="ini">{{ e.initials }}</span>
          {% endif %}
        </div>
      </div>
      <div class="evt-spec">
        <img loading="lazy" src="/spectrogram/{{ e.segment_id }}.png?start={{ e.start_time }}&end={{ e.end_time }}" alt="call spectrogram">
      </div>
      <audio controls preload="none"
             src="/audio/{{ e.segment_id }}?start={{ e.start_time }}&end={{ e.end_time }}"></audio>
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="empty" id="empty">{% if hide_low %}No birds ≥ {{ "%.1f"|format(conf_floor) }} confidence in the last 24 hours yet.{% else %}No birds in the last 24 hours yet.{% endif %}</p>
  <ul class="feed" id="feed" hidden></ul>
  {% endif %}
</div>
<script>
(function(){
  var feed=document.getElementById("feed");
  var empty=document.getElementById("empty");
  var updatedEl=document.getElementById("updated");
  var since={{ latest_json|safe }};
  var hideLow={{ 'true' if hide_low else 'false' }};
  var confFloor={{ conf_floor }};
  var lastPoll=Date.now();
  var seen={};

  function key(e){return e.segment_id+"\0"+e.common_name}
  function markSeen(list){list.forEach(function(e){seen[key(e)]=1})}
  function fmtTime(iso){
    var d=new Date(iso.replace(" ","T"));
    if(isNaN(d))return iso.slice(11,16);
    var h=d.getHours(),m=d.getMinutes(),ap=h>=12?"PM":"AM";
    h=h%12;if(!h)h=12;
    return h+":"+(m<10?"0":"")+m+" "+ap;
  }
  function initials(name){
    return name.split(/\\s+/).map(function(w){return w[0]||""}).join("").slice(0,2).toUpperCase();
  }
  function tickUpdated(){
    var s=Math.floor((Date.now()-lastPoll)/1000);
    updatedEl.textContent=s<5?"Updated just now":"Updated "+s+"s ago";
  }
  function prependEvents(list){
    if(!list.length)return;
    if(empty){empty.hidden=true;feed.hidden=false}
    list.forEach(function(e){
      if(hideLow&&e.peak_conf<confFloor)return;
      var k=key(e);
      if(seen[k])return;
      seen[k]=1;
      var li=document.createElement("li");
      li.className="evt";
      li.dataset.seg=e.segment_id;
      li.dataset.name=e.common_name;
      var thumb=e.has_sprite
        ?'<img src="/sprite/'+e.slug+'.png" alt="" loading="lazy">'
        :'<span class="ini">'+initials(e.common_name)+'</span>';
      li.innerHTML=
        '<div class="evt-row">'+
          '<time class="evt-time mono">'+fmtTime(e.heard_at)+'</time>'+
          '<div class="evt-main">'+
            '<p class="evt-name"><a href="/bird/'+e.slug+'">'+e.common_name+'</a></p>'+
            '<div class="evt-conf mono"><b>'+Math.round(e.peak_conf*100)+'%</b> confidence</div>'+
          '</div>'+
          '<div class="evt-thumb">'+thumb+'</div>'+
        '</div>'+
        '<div class="evt-spec"><img loading="lazy" src="/spectrogram/'+e.segment_id+
          '.png?start='+e.start_time+'&end='+e.end_time+'" alt="call spectrogram"></div>'+
        '<audio controls preload="none" src="/audio/'+e.segment_id+
          '?start='+e.start_time+'&end='+e.end_time+'"></audio>';
      feed.insertBefore(li,feed.firstChild);
    });
  }
  markSeen({{ events_json|safe }});
  setInterval(tickUpdated,1000);
  function poll(){
    if(document.hidden)return;
    var url="/api/recent?since="+encodeURIComponent(since);
    if(hideLow)url+="&hide_low=1";
    fetch(url,{cache:"no-store"})
      .then(function(r){return r.json()})
      .then(function(data){
        lastPoll=Date.now();
        if(data.latest)since=data.latest;
        prependEvents(data.events||[]);
      })
      .catch(function(){});
  }
  setInterval(poll,5000);
  document.addEventListener("visibilitychange",function(){
    if(!document.hidden)poll();
  });
})();
</script>
{% if dev %}{{ dev_script|safe }}{% endif %}
</body></html>
"""


_SORT_KEYS = {
    "discovered": lambda r: r["first_heard"],
    "heard": lambda r: (-r["windows"], r["first_heard"]),
    "peak": lambda r: (-r["peak_conf"], r["first_heard"]),
}
_SORT_REVERSE = {"discovered": True}


def _sort_dex_rows(rows, sort: str):
    return sorted(rows, key=_SORT_KEYS[sort], reverse=_SORT_REVERSE.get(sort, False))


def _today() -> str:
    return date.today().isoformat()


def _parse_day_arg(raw: str | None) -> tuple[bool, str]:
    if raw == "all":
        return True, _today()
    if raw:
        try:
            date.fromisoformat(raw)
            return False, raw
        except ValueError:
            pass
    return False, _today()


def _parse_hide_low(raw: str | None) -> bool:
    return raw in ("1", "true", "yes", "on")


def _filter_dex_rows(rows, *, hide_low: bool):
    if not hide_low:
        return list(rows)
    return [r for r in rows if r["peak_conf"] >= PEAK_CONF_FLOOR]


def _filter_feed_events(events, *, hide_low: bool):
    if not hide_low:
        return events
    return [e for e in events if e["peak_conf"] >= PEAK_CONF_FLOOR]


def _qs(
    *,
    mode: str = "dex",
    sort: str = "discovered",
    day: str | None = None,
    show_all: bool = False,
    page: int | None = None,
    hide_low: bool = False,
    include_mode_sort: bool = True,
    empty: str = "",
) -> str:
    parts: list[str] = []
    if show_all:
        parts.append("day=all")
    elif day and day != _today():
        parts.append(f"day={day}")
    if include_mode_sort:
        if mode == "gallery":
            parts.append("mode=gallery")
        if sort != "discovered":
            parts.append(f"sort={sort}")
        if hide_low:
            parts.append("hide_low=1")
    if page and page > 1:
        parts.append(f"page={page}")
    if not parts:
        return empty
    return "?" + "&".join(parts)


def _format_span(first: str, last: str) -> str:
    t0 = datetime.fromisoformat(first)
    t1 = datetime.fromisoformat(last)
    delta = t1 - t0
    if delta.total_seconds() < 60:
        return f"{int(delta.total_seconds())}s"
    if delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() // 60)}m"
    if delta.days == 0:
        h = int(delta.total_seconds() // 3600)
        m = int((delta.total_seconds() % 3600) // 60)
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{delta.days}d {delta.seconds // 3600}h"


def _format_heard_clock(iso: str) -> str:
    """Local time label for a heard_at ISO string (e.g. '2:32 PM')."""
    t = datetime.fromisoformat(iso)
    h = t.hour % 12 or 12
    ap = "AM" if t.hour < 12 else "PM"
    return f"{h}:{t.minute:02d} {ap}"


def _species_initials(common_name: str) -> str:
    parts = common_name.split()
    return "".join(p[0] for p in parts if p)[:2].upper()


def _feed_event_dict(row, slugs: dict[str, str]) -> dict:
    slug = slugs.get(row["common_name"]) or _common_to_slug(row["common_name"])
    sprite = _sprite_slug(row["common_name"], slugs)
    return {
        "heard_at": row["heard_at"],
        "common_name": row["common_name"],
        "slug": slug,
        "peak_conf": row["peak_conf"],
        "segment_id": row["segment_id"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "has_sprite": sprite is not None,
        "sprite_slug": sprite,
        "time_label": _format_heard_clock(row["heard_at"]),
        "initials": _species_initials(row["common_name"]),
    }


def _feed_payload(conn, *, since: str | None = None, limit: int = 100) -> dict:
    min_conf = _CFG.get("min_conf", config.DEFAULTS["min_conf"])
    rows = storage.recent_feed(conn, limit=limit, since=since, min_conf=min_conf)
    slugs = _slug_map()
    events = [_feed_event_dict(r, slugs) for r in rows]
    latest = events[0]["heard_at"] if events else (since or "")
    return {"events": events, "latest": latest}


def _heard_pct(heard_at: str) -> float:
    """Map ISO heard_at to 0–100 position on a 24h axis."""
    t = heard_at[11:19]
    h, m, s = (int(x) for x in t.split(":"))
    return (h * 3600 + m * 60 + s) / 86400 * 100


def _clock_label(decimal_hour: float) -> str:
    h = int(decimal_hour) % 24
    m = int(round((decimal_hour - int(decimal_hour)) * 60)) % 60
    ap = "AM" if h < 12 else "PM"
    hh = h % 12 or 12
    return f"{hh}:{m:02d} {ap}"


def _solar_arc(lat: float, lon: float, d: date, tzname: str = "America/Los_Angeles") -> dict:
    """Local sunrise/sunset for the day as positions on a 24h axis (NOAA formula).

    Returns dawn/dusk percentages and labels so the timeline can shade night vs
    day. Falls back to coarse fixed bands if the calculation can't run.
    """
    import math

    try:
        from zoneinfo import ZoneInfo

        off = (
            datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo(tzname))
            .utcoffset()
            .total_seconds()
            / 3600
        )
        n = d.timetuple().tm_yday
        g = 2 * math.pi / 365 * (n - 1)
        eq = 229.18 * (
            0.000075
            + 0.001868 * math.cos(g)
            - 0.032077 * math.sin(g)
            - 0.014615 * math.cos(2 * g)
            - 0.040849 * math.sin(2 * g)
        )
        decl = (
            0.006918
            - 0.399912 * math.cos(g)
            + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g)
            + 0.00148 * math.sin(3 * g)
        )
        la = math.radians(lat)
        cos_ha = math.cos(math.radians(90.833)) / (
            math.cos(la) * math.cos(decl)
        ) - math.tan(la) * math.tan(decl)
        cos_ha = max(-1.0, min(1.0, cos_ha))
        ha = math.degrees(math.acos(cos_ha))
        sunrise = (720 - 4 * (lon + ha) - eq) / 60 + off
        sunset = (720 - 4 * (lon - ha) - eq) / 60 + off
    except Exception:
        sunrise, sunset = 6.0, 19.5

    sunrise = max(0.0, min(24.0, sunrise))
    sunset = max(0.0, min(24.0, sunset))
    return {
        "sunrise_pct": sunrise / 24 * 100,
        "sunset_pct": sunset / 24 * 100,
        "sunrise_label": _clock_label(sunrise),
        "sunset_label": _clock_label(sunset),
        "noon_pct": (sunrise + sunset) / 2 / 24 * 100,
        # Dawn chorus: the ~hour after first light, when songbirds are loudest.
        "chorus_pct": (sunrise + 0.5) / 24 * 100,
    }


def _conf_style(conf: float) -> tuple[str, float]:
    """Map a confidence (≥ floor) to a dot color (amber→deep red) and radius (px)."""
    floor = TIMELINE_CONF_FLOOR
    t = 0.0 if conf <= floor else min(1.0, (conf - floor) / (1.0 - floor))
    stops = [(0.0, (240, 165, 0)), (0.5, (216, 58, 54)), (1.0, (125, 28, 27))]
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            f = 0 if p1 == p0 else (t - p0) / (p1 - p0)
            rgb = tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
            break
    else:
        rgb = stops[-1][1]
    radius = 4.5 + t * 3.0
    return "#%02x%02x%02x" % rgb, radius


def _build_timeline(conn, *, selected_day: str, show_all: bool) -> dict:
    """Swimlanes for one day, or per-day bars for all-time (conf ≥ floor only)."""
    slugs = _slug_map()
    floor = TIMELINE_CONF_FLOOR
    if show_all:
        rows = storage.timeline_by_day(conn, min_conf=floor)
        days = [dict(r, label=r["day"][5:]) for r in rows]
        max_n = max((d["n"] for d in days), default=1)
        return {"mode": "days", "days": days, "max_n": max_n, "total": sum(d["n"] for d in days)}

    rows = storage.timeline_occurrences(conn, selected_day, min_conf=floor)
    bins = TIMELINE_BINS
    bin_secs = 86400 / bins
    lanes_map: dict[str, dict] = {}
    hourly = [0] * 24
    for r in rows:
        name = r["common_name"]
        conf = r["confidence"]
        secs = _heard_pct(r["heard_at"]) / 100 * 86400
        hourly[min(23, int(secs // 3600))] += 1
        if name not in lanes_map:
            lanes_map[name] = {
                "name": name,
                "slug": slugs.get(name) or _common_to_slug(name),
                "sprite": _sprite_slug(name, slugs),
                "bins": {},
                "count": 0,
                "peak_conf": 0.0,
                "first_secs": secs,
                "last_secs": secs,
            }
        lane = lanes_map[name]
        idx = min(bins - 1, int(secs // bin_secs))
        b = lane["bins"].setdefault(idx, {"count": 0, "conf": 0.0})
        b["count"] += 1
        b["conf"] = max(b["conf"], conf)
        lane["count"] += 1
        lane["peak_conf"] = max(lane["peak_conf"], conf)
        lane["first_secs"] = min(lane["first_secs"], secs)
        lane["last_secs"] = max(lane["last_secs"], secs)

    bin_pct = 100 / bins
    for lane in lanes_map.values():
        lane_max = max((b["count"] for b in lane["bins"].values()), default=1)
        lane["bar_max"] = lane_max
        lane["bars"] = []
        for idx, b in sorted(lane["bins"].items()):
            color, _ = _conf_style(b["conf"])
            start = _clock_label(idx * bin_secs / 3600)
            lane["bars"].append(
                {
                    "left": idx * bin_pct + bin_pct * 0.1,
                    "width": bin_pct * 0.8,
                    "h": 14 + b["count"] / lane_max * 86,
                    "count": b["count"],
                    "color": color,
                    "title": f"{start} · {b['count']}× · peak {b['conf']:.2f}",
                }
            )
        lane["first_label"] = _clock_label(lane["first_secs"] / 3600)
        lane["last_label"] = _clock_label(lane["last_secs"] / 3600)

    lanes = sorted(lanes_map.values(), key=lambda lane: (-lane["count"], lane["name"]))

    lat = float(_CFG.get("lat", 34.42))
    lon = float(_CFG.get("lon", -119.70))
    sun = _solar_arc(lat, lon, date.fromisoformat(selected_day))
    now_pct = None
    if selected_day == _today():
        now = datetime.now()
        now_pct = (now.hour * 3600 + now.minute * 60 + now.second) / 86400 * 100

    return {
        "mode": "hours",
        "lanes": lanes,
        "total": len(rows),
        "species": len(lanes),
        "hourly": hourly,
        "hourly_max": max(hourly) if rows else 1,
        "busiest_hour": (max(range(24), key=lambda h: hourly[h]) if rows else None),
        "sun": sun,
        "now_pct": now_pct,
        "peak_lane": lanes[0]["name"] if lanes else None,
    }


def _timeline_qs(*, day: str | None = None, show_all: bool = False) -> str:
    if show_all:
        return "?day=all"
    if day and day != _today():
        return f"?day={day}"
    return ""


def _qs_builder(mode: str, sort: str, show_all: bool, selected_day: str, hide_low: bool = False):
    def qs(**kw):
        m = kw.get("mode", mode)
        s = kw.get("sort", sort)
        hl = kw["hide_low"] if "hide_low" in kw else hide_low
        if kw.get("day") == "all":
            return _qs(mode=m, sort=s, show_all=True, hide_low=hl, empty="/")
        if "day" in kw:
            return _qs(mode=m, sort=s, day=kw["day"], show_all=False, hide_low=hl, empty="/")
        return _qs(mode=m, sort=s, day=selected_day, show_all=show_all, hide_low=hl, empty="/")

    return qs


def _normalize_sort(raw: str | None) -> str:
    sort = raw or "discovered"
    if sort == "seen":
        sort = "heard"
    if sort not in _SORT_KEYS:
        sort = "discovered"
    return sort


def _scope_label(selected_day: str, show_all: bool, *, style: str = "short") -> str:
    today = _today()
    if show_all:
        return {"short": "", "header": "All time", "bird": "All-time · every detection window"}[style]
    if selected_day == today:
        return {
            "short": "today",
            "header": f"Today · {today}",
            "bird": f"Today · {today}",
        }[style]
    return {
        "short": f"on {selected_day}",
        "header": selected_day,
        "bird": f"Day · {selected_day}",
    }[style]


def _sorted_dex_birds(
    conn,
    *,
    show_all: bool,
    selected_day: str,
    sort: str,
    hide_low: bool = False,
) -> list[tuple[str, str]]:
    """Return [(slug, common_name), ...] in current dex order."""
    slugs = _slug_map()
    rows = storage.species_dex(conn) if show_all else storage.species_dex_day(conn, selected_day)
    rows = _filter_dex_rows(rows, hide_low=hide_low)
    return [
        (
            slugs.get(r["common_name"]) or _common_to_slug(r["common_name"]),
            r["common_name"],
        )
        for r in _sort_dex_rows(rows, sort)
    ]


def _call_href(
    segment_id: int,
    start: float,
    end: float,
    slug: str,
    *,
    show_all: bool,
    selected_day: str,
    sort: str,
    hide_low: bool,
) -> str:
    """URL for the 3D call chamber view, preserving dex navigation context."""
    parts = [
        f"start={start:.1f}",
        f"end={end:.1f}",
        f"slug={slug}",
    ]
    if show_all:
        parts.append("day=all")
    elif selected_day != _today():
        parts.append(f"day={selected_day}")
    if sort != "discovered":
        parts.append(f"sort={sort}")
    if hide_low:
        parts.append("hide_low=1")
    return f"/call/{segment_id}?" + "&".join(parts)


def _dex_call_order(
    conn,
    *,
    show_all: bool,
    selected_day: str,
    sort: str,
    hide_low: bool = False,
) -> list[dict]:
    """Ordered peak-clip entries for prev/next navigation in the call chamber."""
    slugs = _slug_map()
    rows = storage.species_dex(conn) if show_all else storage.species_dex_day(conn, selected_day)
    rows = _filter_dex_rows(rows, hide_low=hide_low)
    return [
        {
            "slug": slugs.get(r["common_name"]) or _common_to_slug(r["common_name"]),
            "name": r["common_name"],
            "segment_id": r["segment_id"],
            "start": r["start_time"],
            "end": r["end_time"],
            "peak_conf": r["peak_conf"],
        }
        for r in _sort_dex_rows(rows, sort)
    ]


def _split_segments_am_pm(
    segments: list,
) -> tuple[list[int], list[int], float]:
    """Return (morning_ids, afternoon_ids, base_am_share) from ordered segments."""
    if not segments:
        return [], [], 0.5
    segs = [dict(s) for s in segments]
    if len(segs) == 1:
        return [segs[0]["id"]], [], 1.0

    max_gap = 0.0
    split_at = 0
    for i in range(len(segs) - 1):
        end = datetime.fromisoformat(segs[i]["ended_at"])
        start = datetime.fromisoformat(segs[i + 1]["started_at"])
        gap = (start - end).total_seconds()
        if gap > max_gap:
            max_gap = gap
            split_at = i

    if max_gap < 1800:
        am_ids: list[int] = []
        pm_ids: list[int] = []
        am_dur = pm_dur = 0.0
        for s in segs:
            t = datetime.fromisoformat(s["started_at"])
            if t.hour < 12:
                am_ids.append(s["id"])
                am_dur += s["duration"]
            else:
                pm_ids.append(s["id"])
                pm_dur += s["duration"]
        total = am_dur + pm_dur
        return am_ids, pm_ids, (am_dur / total if total else 0.5)

    am_segs = segs[: split_at + 1]
    pm_segs = segs[split_at + 1 :]
    am_dur = sum(s["duration"] for s in am_segs)
    pm_dur = sum(s["duration"] for s in pm_segs)
    total = am_dur + pm_dur
    return (
        [s["id"] for s in am_segs],
        [s["id"] for s in pm_segs],
        am_dur / total if total else 0.5,
    )


def _segment_ids_am_pm_all_time(segments: list) -> tuple[set[int], set[int], float]:
    """AM/PM segment sets across multiple days (split per calendar day)."""
    by_day: dict[str, list] = {}
    for s in segments:
        by_day.setdefault(s["started_at"][:10], []).append(s)
    am_ids: set[int] = set()
    pm_ids: set[int] = set()
    am_dur = pm_dur = 0.0
    for day_segs in by_day.values():
        am, pm, _ = _split_segments_am_pm(day_segs)
        am_ids.update(am)
        pm_ids.update(pm)
        am_dur += sum(s["duration"] for s in day_segs if s["id"] in am)
        pm_dur += sum(s["duration"] for s in day_segs if s["id"] in pm)
    total = am_dur + pm_dur
    return am_ids, pm_ids, (am_dur / total if total else 0.5)


def _is_tentative(peak_conf: float, windows: int) -> bool:
    return peak_conf < 0.6 or (windows == 1 and peak_conf < 0.5)


def _fmt_audio_minutes(seconds: float) -> str:
    mins = seconds / 60
    if mins < 10:
        return f"{mins:.1f}"
    return f"{mins:.0f}"


def _build_data_report(
    conn,
    *,
    selected_day: str,
    show_all: bool,
) -> dict:
    """Assemble JSON payload for the /data report page."""
    day_filter = None if show_all else selected_day
    min_conf = float(_CFG.get("min_conf", config.DEFAULTS["min_conf"]))
    segments = storage.segments_for_scope(conn, day=day_filter)
    species_rows = storage.report_species(conn, day=day_filter)

    if not species_rows:
        return {
            "empty": True,
            "show_all": show_all,
            "day": selected_day if not show_all else None,
            "min_conf": min_conf,
        }

    conf_by_name: dict[str, list[float]] = {}
    for r in storage.report_confidences(conn, day=day_filter):
        conf_by_name.setdefault(r["common_name"], []).append(r["confidence"])

    seg_by_name: dict[str, set[int]] = {}
    for r in storage.report_species_segments(conn, day=day_filter):
        seg_by_name.setdefault(r["common_name"], set()).add(r["segment_id"])

    if show_all:
        am_ids, pm_ids, base_am = _segment_ids_am_pm_all_time(segments)
    else:
        am_list, pm_list, base_am = _split_segments_am_pm(segments)
        am_ids, pm_ids = set(am_list), set(pm_list)

    det_rows = conn.execute(
        f"""
        SELECT common_name, segment_id FROM detections
        {"WHERE date(heard_at) = ?" if day_filter else ""}
        """,
        (day_filter,) if day_filter else (),
    ).fetchall()

    am_pm: dict[str, tuple[int, int]] = {}
    for r in det_rows:
        name = r["common_name"]
        am, pm = am_pm.get(name, (0, 0))
        if r["segment_id"] in am_ids:
            am += 1
        elif r["segment_id"] in pm_ids:
            pm += 1
        am_pm[name] = (am, pm)

    segment_order = [s["id"] for s in segments]
    total_det = sum(r["windows"] for r in species_rows)
    total_species = len(species_rows)
    total_audio_sec = sum(s["duration"] for s in segments)
    top = species_rows[0]
    top_share = round(100 * top["windows"] / total_det) if total_det else 0

    tentative = [
        r["common_name"]
        for r in species_rows
        if _is_tentative(r["peak_conf"], r["windows"])
    ]

    species = []
    for r in species_rows:
        name = r["common_name"]
        am, pm = am_pm.get(name, (0, 0))
        wins = [1 if sid in seg_by_name.get(name, set()) else 0 for sid in segment_order]
        species.append(
            {
                "n": name,
                "sci": r["scientific_name"],
                "c": r["windows"],
                "max": round(r["peak_conf"], 3),
                "am": am,
                "pm": pm,
                "wins": wins,
                "conf": [round(c, 3) for c in conf_by_name.get(name, [])],
                "tentative": _is_tentative(r["peak_conf"], r["windows"]),
            }
        )

    windows = []
    for i, s in enumerate(segments):
        t = datetime.fromisoformat(s["started_at"])
        windows.append(
            {
                "id": i + 1,
                "clock": t.strftime("%H:%M"),
                "min": round(s["duration"] / 60, 2),
                "det": s["detections"],
                "sp": s["species"],
            }
        )

    pm_segs = [s for s in segments if s["id"] in pm_ids]
    curve_seg = max(pm_segs or segments, key=lambda s: s["duration"])
    bin_rows = storage.minute_bins(conn, curve_seg["id"])
    max_min = max((r["minute"] for r in bin_rows), default=0)
    curve_bins = [0] * (max_min + 1)
    for r in bin_rows:
        curve_bins[r["minute"]] = r["n"]

    daily_rates = []
    hourly = [0] * 24
    if show_all:
        for r in storage.daily_rates(conn):
            sec = r["audio_sec"] or 1
            daily_rates.append(
                {
                    "day": r["day"],
                    "det": r["detections"],
                    "min": round(sec / 60, 1),
                    "rate": round(r["detections"] / (sec / 60), 2) if sec else 0,
                }
            )
        for r in storage.hourly_aggregate(conn):
            hourly[int(r["hour"])] = r["n"]

    audio_min = _fmt_audio_minutes(total_audio_sec)
    if show_all:
        headline = (
            f"{total_det} bird detections across {total_species} species, all time"
        )
        lede = (
            f"<b>Summary:</b> BirdNET logged <b>{total_det} detections</b> across "
            f"<b>{total_species} species</b> in {len(segments)} recording windows — "
            f"led by {top['common_name']} with {top['windows']} calls "
            f"({top_share}% of total)."
        )
    else:
        headline = (
            f"{total_det} bird detections across {total_species} species "
            f"in {audio_min} minutes"
        )
        lede = (
            f"<b>Summary:</b> On {selected_day}, BirdNET tagged "
            f"<b>{total_det} detections</b> across <b>{total_species} species</b> "
            f"in {len(segments)} recording windows — top species "
            f"{top['common_name']} ({top['windows']} calls)."
        )

    bullets = [
        {
            "text": (
                f"<b>Recording time:</b> {audio_min} min · {len(segments)} windows · "
                f"top species {top['common_name']} at {top['windows']} detections "
                f"({top_share}% of total)."
            ),
            "orange": True,
        },
        {
            "text": (
                "<b>Note:</b> Detections are model outputs per 3s window, not confirmed "
                "individual birds. Confidence scores below include low scores."
            ),
            "orange": False,
        },
    ]
    if tentative:
        bullets.append(
            {
                "text": (
                    f"<b>Low-confidence IDs:</b> {', '.join(tentative)} — "
                    f"peak confidence below 0.60 or a single low-confidence hit."
                ),
                "orange": True,
            }
        )

    lat = _CFG.get("lat", 34.42)
    lon = _CFG.get("lon", -119.70)

    return {
        "empty": False,
        "show_all": show_all,
        "day": selected_day if not show_all else None,
        "min_conf": min_conf,
        "lat": lat,
        "lon": lon,
        "headline": headline,
        "lede": lede,
        "bullets": bullets,
        "meta": {
            "detections": total_det,
            "species": total_species,
            "windows": len(segments),
            "audio_min": round(total_audio_sec / 60, 1),
        },
        "species": species,
        "windows": windows,
        "curve": {
            "bins": curve_bins,
            "start": datetime.fromisoformat(curve_seg["started_at"]).strftime("%H:%M"),
            "end": datetime.fromisoformat(curve_seg["ended_at"]).strftime("%H:%M"),
        },
        "base_am_pct": round(base_am * 100),
        "daily_rates": daily_rates,
        "hourly": hourly,
        "tentative": tentative,
    }


@app.route("/__dev/ping")
def dev_ping():
    """Dev-only heartbeat; pages poll this to reload after the server restarts."""
    if not _dev_mode():
        abort(404)
    return "", 204


@app.route("/api/recent")
def api_recent():
    since = request.args.get("since") or None
    hide_low = _parse_hide_low(request.args.get("hide_low"))
    limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
    conn = _db()
    try:
        payload = _feed_payload(conn, since=since, limit=limit)
        events = _filter_feed_events(payload["events"], hide_low=hide_low)
        # API returns slim events (no template-only fields).
        api_events = [
            {
                "heard_at": e["heard_at"],
                "common_name": e["common_name"],
                "slug": e["slug"],
                "peak_conf": e["peak_conf"],
                "segment_id": e["segment_id"],
                "start_time": e["start_time"],
                "end_time": e["end_time"],
                "has_sprite": e["has_sprite"],
            }
            for e in events
        ]
        resp = Response(
            json.dumps({"events": api_events, "latest": payload["latest"]}),
            mimetype="application/json",
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp
    finally:
        conn.close()


@app.route("/live")
def live_feed():
    hide_low = _parse_hide_low(request.args.get("hide_low"))
    conn = _db()
    try:
        payload = _feed_payload(conn)
        events = _filter_feed_events(payload["events"], hide_low=hide_low)
        latest = payload["latest"]
        return render_template_string(
            LIVE_PAGE,
            events=events,
            hide_low=hide_low,
            conf_floor=PEAK_CONF_FLOOR,
            latest_json=json.dumps(latest),
            events_json=json.dumps(
                [
                    {
                        "segment_id": e["segment_id"],
                        "common_name": e["common_name"],
                    }
                    for e in events
                ]
            ),
            dev=_dev_mode(),
            dev_script=_DEV_RELOAD_SCRIPT,
        )
    finally:
        conn.close()


@app.route("/")
def index():
    today = _today()
    show_all, selected_day = _parse_day_arg(request.args.get("day"))
    conn = _db()
    try:
        ov = storage.day_overview(conn, selected_day)
        hourly = {r["hour"]: r["n"] for r in storage.day_hourly(conn, selected_day)}
        peak_hour = max(hourly, key=hourly.get) if hourly else None
        peak = f"{peak_hour}:00" if peak_hour else "n/a"
        all_totals = storage.totals(conn)
        slugs = _slug_map()
        sort = _normalize_sort(request.args.get("sort"))
        hide_low = _parse_hide_low(request.args.get("hide_low"))
        rows = storage.species_dex(conn) if show_all else storage.species_dex_day(conn, selected_day)
        hidden_count = sum(1 for r in rows if r["peak_conf"] < PEAK_CONF_FLOOR)
        rows = _filter_dex_rows(rows, hide_low=hide_low)
        dex = [
            dict(
                r,
                sprite_slug=_sprite_slug(r["common_name"], slugs),
                bird_slug=slugs.get(r["common_name"]) or _common_to_slug(r["common_name"]),
            )
            for r in _sort_dex_rows(rows, sort)
        ]
        mode = "gallery" if request.args.get("mode") == "gallery" else "dex"
        day_scope = _scope_label(selected_day, show_all, style="short")
        if show_all:
            species_lbl = "Species discovered (all time)"
            header_day = "All time"
            empty_msg = (
                "No species discovered yet — run <b>birdid.py monitor</b> to start filling the dex."
            )
        else:
            species_lbl = f"Species {day_scope}".strip()
            header_day = _scope_label(selected_day, show_all, style="header")
            empty_msg = (
                f"No species detected {day_scope} — try another day or "
                f'<a href="{_qs(show_all=True, mode=mode, sort=sort, hide_low=hide_low, empty="/")}">show all</a>.'
            )
        if hide_low and hidden_count and not dex:
            empty_msg = (
                f"No species with peak confidence ≥ {PEAK_CONF_FLOOR:.1f} {day_scope or 'in this view'} — "
                f'<a href="{_qs(show_all=show_all, day=selected_day, mode=mode, sort=sort, empty="/")}">'
                f"show all {hidden_count} hidden</a>."
            )
        sel = date.fromisoformat(selected_day)
        prev_day = (sel - timedelta(days=1)).isoformat()
        next_day = (sel + timedelta(days=1)).isoformat()
        def call_href(seg_id: int, start: float, end: float, bird_slug: str) -> str:
            return _call_href(
                seg_id,
                start,
                end,
                bird_slug,
                show_all=show_all,
                selected_day=selected_day,
                sort=sort,
                hide_low=hide_low,
            )

        return render_template_string(
            PAGE,
            today=today,
            selected_day=selected_day,
            show_all=show_all,
            prev_day=prev_day,
            next_day=next_day,
            day_label=header_day if not show_all else "",
            header_day=header_day,
            ov=ov,
            all_totals=all_totals,
            peak=peak,
            total=len(dex),
            dex=dex,
            mode=mode,
            sort=sort,
            hide_low=hide_low,
            hidden_count=hidden_count if hide_low else 0,
            peak_floor=PEAK_CONF_FLOOR,
            species_lbl=species_lbl,
            day_scope=day_scope,
            empty_msg=empty_msg,
            qs=_qs_builder(mode, sort, show_all, selected_day, hide_low=hide_low),
            bird_qs=_qs(
                show_all=show_all, day=selected_day, mode=mode, sort=sort, hide_low=hide_low
            ),
            call_href=call_href,
            data_qs=_qs(day=selected_day, show_all=show_all, include_mode_sort=False),
            timeline_qs=_timeline_qs(day=selected_day, show_all=show_all),
            dev=_dev_mode(),
            dev_script=_DEV_RELOAD_SCRIPT,
        )
    finally:
        conn.close()


@app.route("/bird/<slug>")
def bird_detail(slug: str):
    today = _today()
    show_all, selected_day = _parse_day_arg(request.args.get("day"))
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * _CLIPS_PER_PAGE

    conn = _db()
    try:
        common_name = _resolve_bird_slug(slug, conn)
        if not common_name:
            abort(404)

        stats = storage.species_stats(conn, common_name, day=selected_day, show_all=show_all)
        if not stats or stats["windows"] == 0:
            abort(404)

        meta = storage.species_meta(conn, common_name)
        heard_times = storage.species_heard_times(
            conn, common_name, day=selected_day, show_all=show_all
        )
        hourly_rows = storage.species_hourly(
            conn, common_name, day=selected_day, show_all=show_all
        )
        hourly = {r["hour"]: r["n"] for r in hourly_rows}
        hourly_max = max(hourly.values()) if hourly else 0
        peak_hour_n = max(hourly, key=hourly.get) if hourly else None
        peak_hour = f"{int(peak_hour_n)}:00" if peak_hour_n is not None else "n/a"

        conf_buckets = [
            dict(r) for r in storage.species_confidence_buckets(
                conn, common_name, day=selected_day, show_all=show_all
            )
        ]
        conf_max = max((b["n"] for b in conf_buckets), default=0)

        daily = (
            [dict(r) for r in storage.species_daily(conn, common_name)]
            if show_all
            else []
        )

        total_clips = storage.species_detection_count(
            conn, common_name, day=selected_day, show_all=show_all
        )
        clips = storage.species_detections(
            conn,
            common_name,
            day=selected_day,
            show_all=show_all,
            limit=_CLIPS_PER_PAGE + 1,
            offset=offset,
        )
        has_more = len(clips) > _CLIPS_PER_PAGE
        clips = clips[:_CLIPS_PER_PAGE]

        slugs = _slug_map()
        canonical_slug = slugs.get(common_name) or _common_to_slug(common_name)
        info = _bird_info().get(canonical_slug)
        streak = storage.longest_heard_streak(heard_times)
        per_seg = f"{stats['windows'] / stats['segments']:.1f}" if stats["segments"] else "0"

        sort = _normalize_sort(request.args.get("sort"))
        hide_low = _parse_hide_low(request.args.get("hide_low"))
        mode = "gallery" if request.args.get("mode") == "gallery" else "dex"
        scope_label = _scope_label(selected_day, show_all, style="bird")

        def page_qs(p: int) -> str:
            return f"/bird/{slug}" + _qs(
                show_all=show_all,
                day=selected_day,
                page=p,
                mode=mode,
                sort=sort,
                hide_low=hide_low,
            )

        index_qs = _qs(
            mode=mode, sort=sort, day=selected_day, show_all=show_all, hide_low=hide_low
        )
        back_href = "/" + index_qs

        dex_birds = _sorted_dex_birds(
            conn,
            show_all=show_all,
            selected_day=selected_day,
            sort=sort,
            hide_low=hide_low,
        )
        dex_idx = next((i for i, (s, _) in enumerate(dex_birds) if s == slug), None)
        prev_bird = (
            {"slug": dex_birds[dex_idx - 1][0], "name": dex_birds[dex_idx - 1][1]}
            if dex_idx is not None and dex_idx > 0
            else None
        )
        next_bird = (
            {"slug": dex_birds[dex_idx + 1][0], "name": dex_birds[dex_idx + 1][1]}
            if dex_idx is not None and dex_idx < len(dex_birds) - 1
            else None
        )

        def bird_href(b_slug: str) -> str:
            return f"/bird/{b_slug}" + _qs(
                show_all=show_all,
                day=selected_day,
                mode=mode,
                sort=sort,
                hide_low=hide_low,
            )

        return render_template_string(
            BIRD_PAGE,
            slug=slug,
            common_name=common_name,
            scientific_name=meta["scientific_name"] if meta else "",
            info=info,
            discovered_at=meta["discovered_at"] if meta else None,
            all_time_windows=meta["all_time_windows"] if meta else stats["windows"],
            sprite_slug=_sprite_slug(common_name, slugs),
            stats=stats,
            streak=streak,
            per_seg=per_seg,
            peak_hour=peak_hour,
            span_label=_format_span(stats["first_heard"], stats["last_heard"]),
            scope_label=scope_label,
            hourly=hourly,
            hourly_max=hourly_max,
            conf_buckets=conf_buckets,
            conf_max=conf_max,
            daily=daily,
            clips=clips,
            total_clips=total_clips,
            clip_offset=offset,
            page=page,
            has_more=has_more,
            page_qs=page_qs,
            back_href=back_href,
            prev_bird=prev_bird,
            next_bird=next_bird,
            dex_no=(dex_idx + 1) if dex_idx is not None else 0,
            dex_total=len(dex_birds),
            bird_href=bird_href,
            timeline_qs=_timeline_qs(day=selected_day, show_all=show_all),
            dev=_dev_mode(),
            dev_script=_DEV_RELOAD_SCRIPT,
        )
    finally:
        conn.close()


@app.route("/timeline")
def timeline_view():
    today = _today()
    show_all, selected_day = _parse_day_arg(request.args.get("day"))
    conn = _db()
    try:
        tl = _build_timeline(conn, selected_day=selected_day, show_all=show_all)
        sel = date.fromisoformat(selected_day)
        prev_day = (sel - timedelta(days=1)).isoformat()
        next_day = (sel + timedelta(days=1)).isoformat()
        scope_label = _scope_label(selected_day, show_all, style="header")
        index_qs = _qs(day=selected_day, show_all=show_all, include_mode_sort=False)
        back_href = "/" + index_qs
        if show_all:
            empty = not tl.get("days")
        else:
            empty = tl.get("total", 0) == 0
        return render_template_string(
            TIMELINE_PAGE,
            today=today,
            selected_day=selected_day,
            show_all=show_all,
            prev_day=prev_day,
            next_day=next_day,
            scope_label=scope_label,
            back_href=back_href,
            data_qs=_qs(day=selected_day, show_all=show_all, include_mode_sort=False),
            conf_floor=TIMELINE_CONF_FLOOR,
            tl=tl,
            empty=empty,
            dev=_dev_mode(),
            dev_script=_DEV_RELOAD_SCRIPT,
        )
    finally:
        conn.close()


@app.route("/data")
def data_view():
    today = _today()
    show_all, selected_day = _parse_day_arg(request.args.get("day"))
    conn = _db()
    try:
        report = _build_data_report(conn, selected_day=selected_day, show_all=show_all)
        sel = date.fromisoformat(selected_day)
        prev_day = (sel - timedelta(days=1)).isoformat()
        next_day = (sel + timedelta(days=1)).isoformat()
        scope_label = _scope_label(selected_day, show_all, style="header")
        index_qs = _qs(day=selected_day, show_all=show_all, include_mode_sort=False)
        back_href = "/" + index_qs
        empty = report.get("empty", True)
        return render_template_string(
            DATA_VIEW,
            today=today,
            selected_day=selected_day,
            show_all=show_all,
            prev_day=prev_day,
            next_day=next_day,
            scope_label=scope_label,
            back_href=back_href,
            empty=empty,
            headline=report.get("headline", ""),
            lede=report.get("lede", ""),
            bullets=report.get("bullets", []),
            meta=report.get("meta", {}),
            min_conf=report.get("min_conf", _CFG.get("min_conf", config.DEFAULTS["min_conf"])),
            lat=report.get("lat", _CFG.get("lat", 34.42)),
            lon=report.get("lon", _CFG.get("lon", -119.70)),
            tentative=report.get("tentative", []),
            report_json=json.dumps(report) if not empty else "{}",
            timeline_qs=_timeline_qs(day=selected_day, show_all=show_all),
            dev=_dev_mode(),
            dev_script=_DEV_RELOAD_SCRIPT,
        )
    finally:
        conn.close()


@app.route("/call/<int:segment_id>")
def call_view(segment_id: int):
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    slug = request.args.get("slug", "")
    show_all, selected_day = _parse_day_arg(request.args.get("day"))
    sort = _normalize_sort(request.args.get("sort"))
    hide_low = _parse_hide_low(request.args.get("hide_low"))

    conn = _db()
    try:
        seg = storage.get_segment(conn, segment_id)
        if not seg:
            abort(404)

        call_order = _dex_call_order(
            conn,
            show_all=show_all,
            selected_day=selected_day,
            sort=sort,
            hide_low=hide_low,
        )
        dex_idx = None
        entry = None
        if slug:
            for i, e in enumerate(call_order):
                if e["slug"] == slug:
                    dex_idx = i
                    entry = e
                    break
        if entry is None and start is not None and end is not None:
            for i, e in enumerate(call_order):
                if (
                    e["segment_id"] == segment_id
                    and abs(e["start"] - start) < 0.05
                    and abs(e["end"] - end) < 0.05
                ):
                    dex_idx = i
                    entry = e
                    break
        if entry is None:
            abort(404)

        common_name = entry["name"]
        meta = storage.species_meta(conn, common_name)
        prev_call = call_order[dex_idx - 1] if dex_idx and dex_idx > 0 else None
        next_call = (
            call_order[dex_idx + 1]
            if dex_idx is not None and dex_idx < len(call_order) - 1
            else None
        )

        def call_href(e: dict) -> str:
            return _call_href(
                e["segment_id"],
                e["start"],
                e["end"],
                e["slug"],
                show_all=show_all,
                selected_day=selected_day,
                sort=sort,
                hide_low=hide_low,
            )

        back_href = "/" + _qs(
            show_all=show_all,
            day=selected_day,
            sort=sort,
            hide_low=hide_low,
            mode="dex",
        )

        return render_template_string(
            VISUALIZE_PAGE,
            segment_id=segment_id,
            start=start,
            end=end,
            slug=slug or entry["slug"],
            common_name=common_name,
            scientific_name=meta["scientific_name"] if meta else "",
            peak_conf=entry["peak_conf"],
            dex_no=(dex_idx + 1) if dex_idx is not None else 0,
            dex_total=len(call_order),
            prev_call=prev_call,
            next_call=next_call,
            call_href=call_href,
            back_href=back_href,
            sprite_slug=_sprite_slug(common_name, _slug_map()),
            dev=_dev_mode(),
            dev_script=_DEV_RELOAD_SCRIPT,
        )
    finally:
        conn.close()


@app.route("/callviz/<int:segment_id>.json")
def callviz_json(segment_id: int):
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    cache_path = _callviz_cache_path(
        segment_id, start, end, _CFG["recordings_dir"]
    )
    if cache_path.is_file():
        return Response(cache_path.read_text(), mimetype="application/json")

    conn = _db()
    try:
        audio_path = _resolve_call_audio(conn, segment_id, start, end)
    finally:
        conn.close()

    if not audio_path:
        abort(404)

    payload = _compute_callviz(audio_path, start, end)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload))
    return Response(json.dumps(payload), mimetype="application/json")


@app.route("/sprite/<slug>.png")
def sprite(slug: str):
    path = _SPRITES_DIR / f"{slug}.png"
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/png")


@app.route("/audio/<int:segment_id>")
def audio(segment_id: int):
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    has_window = start is not None and end is not None and end > start

    conn = _db()
    try:
        seg = storage.get_segment(conn, segment_id)
        track = (
            storage.get_track(conn, segment_id, start, end) if has_window else None
        )
    finally:
        conn.close()

    if track and track["clip_path"] and os.path.exists(track["clip_path"]):
        return send_file(
            track["clip_path"], mimetype=_audio_mimetype(track["clip_path"])
        )

    if not seg or not seg["wav_path"] or not os.path.exists(seg["wav_path"]):
        abort(404)

    if has_window:
        try:
            clip = _clip_wav_bytes(seg["wav_path"], start, end)
        except ValueError:
            abort(404)
        return Response(clip, mimetype="audio/wav")

    return send_file(
        seg["wav_path"], mimetype=_audio_mimetype(seg["wav_path"])
    )


@app.route("/spectrogram/<int:segment_id>.png")
def spectrogram(segment_id: int):
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    cache_path = _spec_cache_path(
        segment_id, start, end, _CFG["recordings_dir"]
    )
    if cache_path.is_file():
        return send_file(cache_path, mimetype="image/png")

    conn = _db()
    try:
        seg = storage.get_segment(conn, segment_id)
        track = None
        if start is not None and end is not None and end > start:
            track = storage.get_track(conn, segment_id, start, end)
    finally:
        conn.close()

    audio_path = None
    if track and track["clip_path"] and os.path.exists(track["clip_path"]):
        audio_path = track["clip_path"]
    elif seg and seg["wav_path"] and os.path.exists(seg["wav_path"]):
        audio_path = seg["wav_path"]
    if not audio_path:
        abort(404)

    import librosa  # imported lazily so the rest of the app starts fast
    import librosa.display
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    has_window = start is not None and end is not None and end > start
    if track and track["clip_path"] and audio_path == track["clip_path"]:
        y, sr = librosa.load(audio_path, sr=None)
    elif has_window:
        offset = start
        duration = end - start
        y, sr = librosa.load(audio_path, sr=None, offset=offset, duration=duration)
        if y.size == 0:
            y, sr = librosa.load(audio_path, sr=None)
    else:
        y, sr = librosa.load(audio_path, sr=None)

    S = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128, fmax=sr // 2)
    S_db = librosa.power_to_db(S, ref=np.max)

    fig, ax = plt.subplots(figsize=(6, 2.2), dpi=100)
    librosa.display.specshow(
        S_db, sr=sr, x_axis="time", y_axis="mel", fmax=sr // 2, ax=ax
    )
    ax.set_axis_off()
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        cache_path,
        format="png",
        bbox_inches="tight",
        pad_inches=0,
    )
    plt.close(fig)
    return send_file(cache_path, mimetype="image/png")


def main(host: str = "127.0.0.1", port: int = 8080, *, dev: bool = False):
    app.config["DEV_MODE"] = dev
    url = f"http://{host}:{port}"
    if dev:
        print(f"bird-id dashboard (dev) at {url}  (db: {_CFG['db']})")
        print("  auto-reload on code changes — leave a tab open to refresh automatically")
    else:
        print(f"bird-id dashboard at {url}  (db: {_CFG['db']})")
    extra_files = [str(_BIRD_INFO_JSON), str(_BIRDS_JSON)] if dev else None
    app.run(host=host, port=port, debug=dev, use_reloader=dev, extra_files=extra_files)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="bird-id web dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dev", action="store_true", help="auto-reload on code/data changes")
    args = ap.parse_args()
    main(args.host, args.port, dev=args.dev)
