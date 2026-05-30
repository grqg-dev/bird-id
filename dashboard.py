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
import os
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # headless / no display
import matplotlib.pyplot as plt
import numpy as np
from flask import Flask, Response, abort, render_template_string, request, send_file

import config
import storage

app = Flask(__name__)
_CFG = config.load()


def _db():
    return storage.connect(_CFG["db"])


PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>bird-id</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1419;color:#e6e6e6}
  header{background:#1a8c5a;padding:16px 24px}
  header h1{margin:0;font-size:20px}
  .wrap{max-width:920px;margin:0 auto;padding:24px}
  .card{background:#1b2027;border-radius:10px;padding:16px 20px;margin-bottom:20px}
  h2{font-size:15px;color:#7fd1a8;margin:0 0 12px;text-transform:uppercase;letter-spacing:.5px}
  table{width:100%;border-collapse:collapse;font-size:14px}
  td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #2a313a}
  th{color:#9aa7b3;font-weight:600}
  .num{color:#9aa7b3;font-variant-numeric:tabular-nums}
  .bars{display:flex;align-items:flex-end;gap:3px;height:90px}
  .bar{flex:1;background:#1a8c5a;border-radius:2px 2px 0 0;min-height:2px}
  .bar span{display:block;text-align:center;font-size:10px;color:#6b7785;margin-top:90px}
  .hr{display:flex;gap:3px;margin-top:4px}.hr div{flex:1;text-align:center;font-size:9px;color:#6b7785}
  .new{color:#ffd166;font-weight:600}
  audio{height:28px;vertical-align:middle}
  img.spec{height:48px;border-radius:4px;vertical-align:middle;background:#000}
  .big{font-size:26px;font-weight:700}.muted{color:#9aa7b3;font-size:13px}
  a{color:#7fd1a8}
</style></head><body>
<header><h1>🐦 bird-id — {{ day }}</h1></header>
<div class="wrap">

  <div class="card">
    <h2>Today</h2>
    {% if ov.detections %}
    <span class="big">{{ ov.species }}</span> <span class="muted">species,
    {{ ov.detections }} detections · first {{ first }} · last {{ last }} · busiest {{ peak }}</span>
    {% if new %}<p class="new">✨ New to your records: {{ new|join(", ") }}</p>{% endif %}
    <div class="bars">
      {% for h in hours %}<div class="bar" style="height:{{ h.pct }}%" title="{{h.hour}}:00 — {{h.n}}"></div>{% endfor %}
    </div>
    <div class="hr">{% for h in hours %}<div>{{ h.hour }}</div>{% endfor %}</div>
    {% else %}<p class="muted">No detections recorded today.</p>{% endif %}
  </div>

  <div class="card">
    <h2>All-time species</h2>
    <table><tr><th>Species</th><th>Peak</th><th>Windows</th><th>First heard</th><th>Last heard</th></tr>
    {% for s in species %}
      <tr><td>{{ s.common_name }} <span class="muted">{{ s.scientific_name }}</span></td>
      <td class="num">{{ "%.3f"|format(s.peak_conf) }}</td>
      <td class="num">{{ s.windows }}</td>
      <td class="num">{{ s.first_heard.replace("T"," ") }}</td>
      <td class="num">{{ s.last_heard.replace("T"," ") }}</td></tr>
    {% else %}<tr><td colspan=5 class="muted">No data yet.</td></tr>{% endfor %}
    </table>
  </div>

  <div class="card">
    <h2>Recent detections</h2>
    <table><tr><th>Time</th><th>Species</th><th>Conf</th><th>Clip</th><th>Spectrogram</th></tr>
    {% for d in recent %}
      <tr><td class="num">{{ d.heard_at.replace("T"," ") }}</td>
      <td>{{ d.common_name }}</td>
      <td class="num">{{ "%.3f"|format(d.confidence) }}</td>
      {% set frag = "#t=%.1f,%.1f"|format(d.start_time, d.end_time) %}
      {% set qs = "?start=%.1f&end=%.1f"|format(d.start_time, d.end_time) %}
      <td>{% if d.wav_path %}<audio controls preload="none" src="/audio/{{ d.segment_id }}{{ frag }}"></audio>{% else %}<span class="muted">discarded</span>{% endif %}</td>
      <td>{% if d.wav_path %}<a href="/spectrogram/{{ d.segment_id }}.png{{ qs }}" target="_blank"><img class="spec" src="/spectrogram/{{ d.segment_id }}.png{{ qs }}"></a>{% endif %}</td></tr>
    {% else %}<tr><td colspan=5 class="muted">No detections yet — run <code>birdid.py monitor</code>.</td></tr>{% endfor %}
    </table>
  </div>

</div></body></html>
"""


@app.route("/")
def index():
    day = datetime.now().strftime("%Y-%m-%d")
    conn = _db()
    try:
        ov = storage.day_overview(conn, day)
        hourly = {r["hour"]: r["n"] for r in storage.day_hourly(conn, day)}
        peak_hour = max(hourly, key=hourly.get) if hourly else None
        peak = f"{peak_hour}:00 ({hourly[peak_hour]})" if peak_hour else "n/a"
        mx = max(hourly.values()) if hourly else 1
        hours = [
            {"hour": f"{h:02d}", "n": hourly.get(f"{h:02d}", 0),
             "pct": int(100 * hourly.get(f"{h:02d}", 0) / mx) if mx else 0}
            for h in range(24)
        ]
        return render_template_string(
            PAGE,
            day=day,
            ov=ov,
            first=ov["first_heard"][11:16] if ov["first_heard"] else "?",
            last=ov["last_heard"][11:16] if ov["last_heard"] else "?",
            peak=peak,
            hours=hours,
            new=storage.new_species_on(conn, day),
            species=storage.species_summary(conn),
            recent=storage.recent_detections(conn, limit=60),
        )
    finally:
        conn.close()


@app.route("/audio/<int:segment_id>")
def audio(segment_id: int):
    conn = _db()
    try:
        seg = storage.get_segment(conn, segment_id)
    finally:
        conn.close()
    if not seg or not seg["wav_path"] or not os.path.exists(seg["wav_path"]):
        abort(404)
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
