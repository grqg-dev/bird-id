"""Local web dashboard for bird-id.

A single-file Flask app that reads the SQLite database and shows: today's
overview, an hourly activity chart, an all-time species table, and a feed of
recent detections with a play button and an on-the-fly spectrogram per clip.

Deliberately offline-friendly (no CDN / JS chart libs): charts are server-
rendered with plain CSS bars, so it works on a headless Pi with no internet.

Run:
    ./.venv/bin/python dashboard.py            # http://127.0.0.1:8080
    ./.venv/bin/python birdid.py dashboard
"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless / no display
import matplotlib.pyplot as plt
import numpy as np
from flask import Flask, Response, abort, render_template_string, request, send_file

import config
import storage

_ROOT = Path(__file__).resolve().parent
_SPRITES_DIR = _ROOT / "realistic-sprites"
_BIRDS_JSON = _ROOT / "docs" / "birds.json"

app = Flask(__name__)
_CFG = config.load()


def _slug_map() -> dict[str, str]:
    with _BIRDS_JSON.open() as f:
        return {b["common_name"]: b["slug"] for b in json.load(f)}


def _sprite_slug(common_name: str, slugs: dict[str, str]) -> str | None:
    slug = slugs.get(common_name)
    if slug and (_SPRITES_DIR / f"{slug}.png").is_file():
        return slug
    return None


def _db():
    return storage.connect(_CFG["db"])


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
  @media(max-width:1100px){.grid{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:680px){.grid{grid-template-columns:1fr}}
  .entry{background:#fff;border-radius:12px;border:1px solid #e2e0d8;overflow:hidden;
         box-shadow:0 1px 4px rgba(0,0,0,.08);display:flex;flex-direction:column}
  .entry .top{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;
              background:#f7f6f2;border-bottom:1px solid #eceae3}
  .no{font-weight:800;color:var(--ink);letter-spacing:1px}
  .sprite{background:#fff;border-bottom:1px solid #eceae3}
  .sprite img.art{display:block;width:100%;height:150px;object-fit:contain;padding:10px 14px 6px}
  .spectro{background:#0d1b14}
  .spectro img{display:block;width:100%;height:88px;object-fit:cover}
  .play{background:#f7f6f2;padding:8px 10px;border-top:1px solid #eceae3}
  .play audio{display:block;width:100%;height:32px}
  .body{padding:10px 12px 12px}
  .name{font-size:17px;font-weight:800;margin:0;letter-spacing:.3px}
  .latin{font-style:italic;color:#7a766a;font-size:12px;margin:1px 0 9px}
  .cp{display:flex;align-items:center;gap:8px;margin-bottom:8px}
  .cp .barbg{flex:1;height:8px;background:#dcd7c6;border-radius:5px;overflow:hidden}
  .cp .barfill{height:100%;border-radius:5px}
  .cp .val{font-weight:800;font-size:13px}
  .meta{display:flex;justify-content:space-between;font-size:11px;color:#8a857a;letter-spacing:.5px}
  .empty{background:#f7f6f2;border:1px solid #e2e0d8;border-radius:12px;padding:46px;text-align:center;color:#7a766a;font-size:15px}
  .modes{display:flex;gap:8px;margin-bottom:14px}
  .modes a{font-size:12px;letter-spacing:.5px;text-decoration:none;padding:6px 12px;border-radius:8px;
           border:1px solid var(--border);color:#7a766a;background:#fff}
  .modes a.on{background:var(--surface);border-color:#d7d2c0;color:var(--ink);font-weight:700}
  .modes a:hover{border-color:#c8c4ba;color:var(--ink);background:var(--surface)}
  .no-art{display:flex;align-items:center;justify-content:center;height:150px;padding:8px 12px;
          color:#b0aca2;font-size:12px;letter-spacing:.5px;text-align:center}
  .gallery .grid{grid-template-columns:repeat(3,1fr);gap:14px}
  .gallery .sprite img.art{height:140px;padding:10px 12px 8px}
  .gallery .sprite{border-bottom:none}
</style></head><body>

<div class="wrap">
  <div class="modes mono">
    <a href="/" class="{{ 'on' if mode == 'dex' else '' }}">Full dex</a>
    <a href="/?mode=gallery" class="{{ 'on' if mode == 'gallery' else '' }}">Gallery</a>
  </div>
  <div class="screen-bar">
    <div><div class="count mono">Nº {{ "%03d"|format(total) }}</div><div class="lbl">Species discovered</div></div>
    {% if ov.detections %}
    <div class="seg"><div class="count mono">{{ ov.detections }}</div><div class="lbl">Detections today</div></div>
    <div class="seg"><div class="count mono">{{ ov.species }}</div><div class="lbl">Seen today · busiest {{ peak }}</div></div>
    {% else %}<div class="seg lbl">No sightings logged today yet</div>{% endif %}
    <div class="when"><b>Bird-Dex</b>Santa Barbara · {{ day }}</div>
  </div>

  {% if dex %}
  <div class="grid{{ ' gallery' if mode == 'gallery' else '' }}">
    {% for e in dex %}
    {% set qs = "?start=%.1f&end=%.1f"|format(e.start_time, e.end_time) %}
    {% set pct = (e.peak_conf*100)|round|int %}
    {% set col = "#46c66b" if e.peak_conf>=0.6 else ("#e0a92a" if e.peak_conf>=0.4 else "#d83a36") %}
    <div class="entry">
      <div class="top"><span class="no mono">Nº {{ "%03d"|format(loop.index) }}</span></div>
      <div class="sprite">
        {% if e.sprite_slug %}
        <img class="art" loading="lazy" src="/sprite/{{ e.sprite_slug }}.png" alt="{{ e.common_name }}">
        {% elif mode == 'gallery' %}
        <div class="no-art lbl">NO ILLUSTRATION</div>
        {% endif %}
        {% if mode != 'gallery' and e.wav_path %}
        <div class="spectro">
          <img loading="lazy" src="/spectrogram/{{ e.segment_id }}.png{{ qs }}" alt="call spectrogram">
        </div>
        <div class="play"><audio controls preload="none" src="/audio/{{ e.segment_id }}{{ qs }}"></audio></div>
        {% endif %}
      </div>
      <div class="body">
        <p class="name">{{ e.common_name }}</p>
        <p class="latin">{{ e.scientific_name }}</p>
        <div class="cp"><span class="lbl mono">PEAK</span>
          <span class="barbg"><span class="barfill" style="width:{{ pct }}%;background:{{ col }}"></span></span>
          <span class="val mono">{{ pct }}%</span></div>
        <div class="meta mono"><span>SEEN ×{{ e.windows }}</span>
          <span>{{ e.first_heard[5:10] }} → {{ e.last_heard[5:10] }}</span></div>
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">No species discovered yet — run <b>birdid.py monitor</b> to start filling the dex.</div>
  {% endif %}
</div>
</body></html>
"""


@app.route("/")
def index():
    day = datetime.now().strftime("%Y-%m-%d")
    conn = _db()
    try:
        ov = storage.day_overview(conn, day)
        hourly = {r["hour"]: r["n"] for r in storage.day_hourly(conn, day)}
        peak_hour = max(hourly, key=hourly.get) if hourly else None
        peak = f"{peak_hour}:00" if peak_hour else "n/a"
        # Dex entries in discovery order (Nº001 = first species ever recorded).
        slugs = _slug_map()
        dex = [
            dict(r, sprite_slug=_sprite_slug(r["common_name"], slugs))
            for r in sorted(storage.species_dex(conn), key=lambda r: r["first_heard"])
        ]
        mode = "gallery" if request.args.get("mode") == "gallery" else "dex"
        return render_template_string(
            PAGE,
            day=day,
            ov=ov,
            peak=peak,
            total=len(dex),
            dex=dex,
            mode=mode,
        )
    finally:
        conn.close()


@app.route("/sprite/<slug>.png")
def sprite(slug: str):
    path = _SPRITES_DIR / f"{slug}.png"
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/png")


@app.route("/audio/<int:segment_id>")
def audio(segment_id: int):
    conn = _db()
    try:
        seg = storage.get_segment(conn, segment_id)
    finally:
        conn.close()
    if not seg or not seg["wav_path"] or not os.path.exists(seg["wav_path"]):
        abort(404)

    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    if start is not None and end is not None and end > start:
        try:
            clip = _clip_wav_bytes(seg["wav_path"], start, end)
        except ValueError:
            abort(404)
        return Response(clip, mimetype="audio/wav")

    return send_file(seg["wav_path"], mimetype="audio/wav")


@app.route("/spectrogram/<int:segment_id>.png")
def spectrogram(segment_id: int):
    conn = _db()
    try:
        seg = storage.get_segment(conn, segment_id)
    finally:
        conn.close()
    if not seg or not seg["wav_path"] or not os.path.exists(seg["wav_path"]):
        abort(404)

    import librosa  # imported lazily so the rest of the app starts fast
    import librosa.display

    # Optional window: render just the detection's slice instead of the whole segment.
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    offset = start if start is not None else 0.0
    duration = (end - start) if (start is not None and end is not None and end > start) else None

    y, sr = librosa.load(seg["wav_path"], sr=None, offset=offset, duration=duration)
    if y.size == 0:  # window outside the audio — fall back to the whole segment
        y, sr = librosa.load(seg["wav_path"], sr=None)
    S = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128, fmax=sr // 2)
    S_db = librosa.power_to_db(S, ref=np.max)

    fig, ax = plt.subplots(figsize=(6, 2.2), dpi=100)
    librosa.display.specshow(S_db, sr=sr, x_axis="time", y_axis="mel", fmax=sr // 2, ax=ax)
    ax.set(title=None)
    fig.tight_layout(pad=0.2)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")


def main(host: str = "127.0.0.1", port: int = 8080):
    print(f"bird-id dashboard at http://{host}:{port}  (db: {_CFG['db']})")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="bird-id web dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    main(args.host, args.port)
