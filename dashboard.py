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
import math
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, Response, abort, render_template, request, send_file

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


_CLIPS_PER_PAGE = 50
PEAK_CONF_FLOOR = 0.7  # Bird-Dex "hide low confidence" cutoff (peak conf per species)
TIMELINE_CONF_FLOOR = 0.7  # Timeline only plots detections at/above this confidence
TIMELINE_BINS = 96  # Per-lane histogram buckets across 24h (15-minute bars)

# --- Trends page ---
# Days with known timing faults (e.g. segmenter clock was wrong). Hidden entirely.
EXCLUDED_DAYS: set[str] = {"2026-06-08"}
# Days with fewer detections than this are auto-excluded as startup/gap days.
MIN_DAY_DETECTIONS = 200
_CALLVIZ_ROWS = 96
_CALLVIZ_COLS = 192
_CALLVIZ_HSCALE = 8  # horizontal upscale for scrolling spectrogram + scope

# --- Fable page (3D ember-constellation song sculpture, route: /vibe) -------
_VIBEVIZ_VERSION = 2  # bump to invalidate cached payloads when features change
_VIBE_NFFT = 2048
_VIBE_HOP = 512
_VIBE_FMIN = 700.0  # ignore sub-bass rumble (wind/handling) below this (Hz)
_VIBE_FMAX = 10000.0  # ceiling for nodes + legend (Hz); legend auto-shrinks
_VIBE_MAX_NODES = 4200  # cap total points so the browser stays smooth
_VIBE_PEAKS_PER_FRAME = 7  # spectral peaks kept per analysed time frame
_VIBE_NOISE_MARGIN = 1.6  # per-bin floor multiplier for the viz-only denoise


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


def _vibeviz_cache_path(
    segment_id: int,
    start: float | None,
    end: float | None,
    base_dir: str | Path,
) -> Path:
    """Disk path for a cached vibe-viz JSON payload (pure path logic for tests)."""
    base = Path(base_dir).expanduser() / "cache" / "vibeviz"
    v = _VIBEVIZ_VERSION
    if start is not None and end is not None and end > start:
        name = f"vibe_v{v}_{segment_id}_{int(start * 1000)}_{int(end * 1000)}.json"
    else:
        name = f"vibe_v{v}_{segment_id}_full.json"
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
    """Downsampled mel matrix, waveform, and pitch for the Call Chamber view."""
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

    wave_pts = _resample_cols(
        y.reshape(1, -1), _CALLVIZ_COLS * _CALLVIZ_HSCALE
    ).reshape(-1)
    peak = float(np.max(np.abs(wave_pts))) or 1e-8
    wave_norm = (wave_pts / peak).tolist()

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
        "wave": wave_norm,
        "pitch": [float(p) if p > 0 else 0.0 for p in pitch],
    }


def _denoised_magnitude(y, sr):
    """STFT magnitude with a per-clip spectral gate (viz-only, never rewrites files).

    Estimates a per-frequency-bin noise floor from the quietest frames in this
    clip, then applies a soft Wiener-style mask so steady background hum/wind is
    suppressed and only genuine song peaks survive into the point cloud.
    """
    import librosa
    import numpy as np

    S = np.abs(librosa.stft(y, n_fft=_VIBE_NFFT, hop_length=_VIBE_HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=_VIBE_NFFT)
    if S.size == 0:
        return S, freqs
    energy = S.sum(axis=0)
    thresh = np.percentile(energy, 25)
    quiet = S[:, energy <= thresh]
    floor = (
        quiet.mean(axis=1, keepdims=True)
        if quiet.shape[1] >= 3
        else S.min(axis=1, keepdims=True)
    )
    fl = (_VIBE_NOISE_MARGIN * floor) ** 2
    mask = S**2 / (S**2 + fl + 1e-12)
    return S * mask, freqs


def _compute_vibeviz(audio_path: str, start: float | None, end: float | None) -> dict:
    """Nodes, edges, and amplitude for the 3D point-network 'vibe' sculpture.

    Each analysed time frame contributes a few spectral peaks; every peak is a
    node at (time, frequency, amplitude). Nodes are linked to the nearest-pitch
    nodes in the following frame so the song reads as a flowing network.
    """
    import librosa
    import numpy as np
    from scipy.signal import find_peaks

    has_window = start is not None and end is not None and end > start
    is_clip = Path(audio_path).name.startswith("clip_")
    if is_clip or not has_window:
        y, sr = librosa.load(audio_path, sr=None)
    else:
        y, sr = librosa.load(audio_path, sr=None, offset=start, duration=end - start)
        if y.size == 0:
            y, sr = librosa.load(audio_path, sr=None)

    duration = float(len(y) / sr) if sr else 0.0
    Sd, freqs = _denoised_magnitude(y, sr)
    empty = {
        "version": _VIBEVIZ_VERSION,
        "duration": round(duration, 4),
        "sr": int(sr or 0),
        "fmax": _VIBE_FMAX,
        "nodes": [],
        "edges": [],
        "amp": [],
        "ampTimes": [],
    }
    if Sd.size == 0 or duration <= 0:
        return empty

    n_frames = Sd.shape[1]
    frame_times = librosa.frames_to_time(
        np.arange(n_frames), sr=sr, hop_length=_VIBE_HOP
    )
    fbins = np.where((freqs >= _VIBE_FMIN) & (freqs <= _VIBE_FMAX))[0]
    fvals = freqs[fbins]
    Sf = Sd[fbins, :]
    smax = float(Sf.max()) or 1e-8
    Sn = Sf / smax  # normalise magnitudes to 0..1

    target_frames = max(1, _VIBE_MAX_NODES // _VIBE_PEAKS_PER_FRAME)
    stride = max(1, round(n_frames / target_frames))

    nodes: list[list[float]] = []  # [t, f, a]
    frame_nodes: list[list[tuple[int, float]]] = []  # (node_index, freq) per frame
    for fi in range(0, n_frames, stride):
        col = Sn[:, fi]
        peaks, props = find_peaks(col, height=0.06, prominence=0.03)
        if peaks.size == 0:
            frame_nodes.append([])
            continue
        order = np.argsort(props["peak_heights"])[::-1][:_VIBE_PEAKS_PER_FRAME]
        t = float(frame_times[fi]) if fi < frame_times.size else 0.0
        this: list[tuple[int, float]] = []
        for p in peaks[order]:
            f = float(fvals[p])
            this.append((len(nodes), f))
            nodes.append([round(t, 4), round(f, 1), round(float(col[p]), 4)])
        frame_nodes.append(this)

    edges: list[list[int]] = []
    for k in range(len(frame_nodes) - 1):
        a_nodes, b_nodes = frame_nodes[k], frame_nodes[k + 1]
        if not a_nodes or not b_nodes:
            continue
        b_f = np.array([bf for (_, bf) in b_nodes])
        for ai, af in a_nodes:
            for nb in np.argsort(np.abs(b_f - af))[:2]:
                edges.append([ai, b_nodes[int(nb)][0]])

    rms = librosa.feature.rms(y=y, frame_length=_VIBE_NFFT, hop_length=_VIBE_HOP)[0]
    rmax = float(rms.max()) or 1e-8
    rms_n = rms / rmax
    amp_times = librosa.frames_to_time(
        np.arange(rms_n.size), sr=sr, hop_length=_VIBE_HOP
    )
    step = max(1, rms_n.size // 240)
    amp = [round(float(v), 4) for v in rms_n[::step]]
    amp_t = [round(float(v), 3) for v in amp_times[::step]]

    node_fmax = max((n[1] for n in nodes), default=2000.0)
    legend_fmax = float(
        min(_VIBE_FMAX, max(4000.0, np.ceil(node_fmax / 1000.0) * 1000.0))
    )

    return {
        "version": _VIBEVIZ_VERSION,
        "duration": round(duration, 4),
        "sr": int(sr),
        "fmax": legend_fmax,
        "nodes": nodes,
        "edges": edges,
        "amp": amp,
        "ampTimes": amp_t,
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




# ---------------------------------------------------------------------------
# Trends helpers
# ---------------------------------------------------------------------------

def _visible_days(conn, end_day: str, span: int = 7) -> tuple[str, ...]:
    """Return usable dates in the rolling window ending at end_day.

    Drops days in EXCLUDED_DAYS and days with fewer than MIN_DAY_DETECTIONS
    detections (startup / gap days). Result is sorted ascending.
    """
    end = date.fromisoformat(end_day)
    candidates = [(end - timedelta(days=i)).isoformat() for i in range(span)]
    if not candidates:
        return ()
    ph = ",".join("?" * len(candidates))
    rows = conn.execute(
        f"""
        SELECT date(heard_at) AS day, COUNT(*) AS n
        FROM detections WHERE date(heard_at) IN ({ph})
        GROUP BY day
        """,
        candidates,
    ).fetchall()
    counts = {r["day"]: r["n"] for r in rows}
    visible = sorted(
        d for d in candidates
        if d not in EXCLUDED_DAYS and counts.get(d, 0) >= MIN_DAY_DETECTIONS
    )
    return tuple(visible)


def _build_clock_svg(hourly: dict) -> str:
    """Radial 24-hour Activity Clock as an inline SVG string."""
    cx, cy = 200, 200
    r_min, r_max = 38, 158

    rates = {}
    for h, data in hourly.items():
        sec = data.get("audio_sec") or 0
        n = data.get("n") or 0
        rates[h] = n / sec if sec > 0 else (n if n > 0 else 0)
    max_rate = max(rates.values(), default=1) or 1
    normed = {h: v / max_rate for h, v in rates.items()}

    # Dawn peak: highest normalized rate in hours 04-09
    peak_dawn = max(range(4, 10), key=lambda h: normed.get(h, 0), default=6)

    parts: list[str] = []
    parts.append(
        '<svg viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg"'
        ' style="max-width:320px;width:100%;display:block;margin:0 auto">'
    )
    # Background rings
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_max}" fill="#f7f6f2" stroke="#e2e0d8" stroke-width="1"/>')
    for pct in (0.33, 0.66):
        r = r_min + pct * (r_max - r_min)
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="#e8e6de" stroke-width="0.5" stroke-dasharray="3,5"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_min}" fill="#f7f6f2" stroke="#e2e0d8" stroke-width="1"/>')

    # Sectors
    for h in range(24):
        n = normed.get(h, 0)
        a_start = math.radians(h * 15 - 90)
        a_end = math.radians((h + 1) * 15 - 90)
        r_outer = r_min + n * (r_max - r_min)
        if r_outer <= r_min + 1:
            continue
        # Color by time of day
        if 5 <= h <= 8:
            color = "#d98a4a"  # dawn gold
        elif h == peak_dawn:
            color = "#d98a4a"
        elif 9 <= h <= 17:
            color = "#7fbfd4"  # day blue
        elif h >= 20 or h <= 3:
            color = "#1d2238"  # night dark
        else:
            color = "#a09080"  # dusk/transition
        cos_s, sin_s = math.cos(a_start), math.sin(a_start)
        cos_e, sin_e = math.cos(a_end), math.sin(a_end)
        ix_s = cx + r_min * cos_s
        iy_s = cy + r_min * sin_s
        ox_s = cx + r_outer * cos_s
        oy_s = cy + r_outer * sin_s
        ox_e = cx + r_outer * cos_e
        oy_e = cy + r_outer * sin_e
        ix_e = cx + r_min * cos_e
        iy_e = cy + r_min * sin_e
        opacity = 0.35 + 0.65 * n
        d = (
            f"M {ix_s:.1f} {iy_s:.1f} L {ox_s:.1f} {oy_s:.1f} "
            f"A {r_outer:.1f} {r_outer:.1f} 0 0 1 {ox_e:.1f} {oy_e:.1f} "
            f"L {ix_e:.1f} {iy_e:.1f} "
            f"A {r_min} {r_min} 0 0 0 {ix_s:.1f} {iy_s:.1f} Z"
        )
        parts.append(f'<path d="{d}" fill="{color}" opacity="{opacity:.2f}"/>')

    # Hour labels every 3 h
    for h in (0, 3, 6, 9, 12, 15, 18, 21):
        angle = math.radians(h * 15 - 90)
        r_lbl = r_max + 18
        lx = cx + r_lbl * math.cos(angle)
        ly = cy + r_lbl * math.sin(angle) + 4
        lbl = f"{h:02d}"
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
            f'font-size="11" fill="#8a857a" '
            f'font-family="SF Mono,ui-monospace,monospace">{lbl}</text>'
        )

    # Dawn peak annotation
    a_mid = math.radians(peak_dawn * 15 + 7.5 - 90)
    n_pk = normed.get(peak_dawn, 0)
    r_tip = r_min + n_pk * (r_max - r_min)
    ax = cx + (r_tip + 10) * math.cos(a_mid)
    ay = cy + (r_tip + 10) * math.sin(a_mid) + 3
    parts.append(
        f'<text x="{ax:.1f}" y="{ay:.1f}" text-anchor="middle" '
        f'font-size="10" fill="#d98a4a" font-weight="700">dawn</text>'
    )

    # Center label
    parts.append(
        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" '
        f'font-size="12" fill="#8a857a" '
        f'font-family="SF Mono,ui-monospace,monospace">24h</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _sparkline_svg(hour_counts: dict, width: int = 72, height: int = 20) -> str:
    """Tiny 24-bar histogram SVG for species chronotype display."""
    max_n = max(hour_counts.values(), default=1) or 1
    bar_w = width / 24
    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"'
        f' style="width:{width}px;height:{height}px;display:inline-block;vertical-align:middle">'
    ]
    for h in range(24):
        n = hour_counts.get(h, 0)
        if not n:
            continue
        bar_h = max((n / max_n) * height, 1)
        x = h * bar_w
        y = height - bar_h
        if 5 <= h <= 8:
            color = "#d98a4a"
        elif 9 <= h <= 17:
            color = "#7fbfd4"
        elif h >= 20 or h <= 3:
            color = "#1d2238"
        else:
            color = "#a09080"
        parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{bar_h:.2f}" fill="{color}"/>')
    parts.append("</svg>")
    return "".join(parts)


def _classify_chronotype(hour_counts: dict) -> str:
    """Classify a species as Early Riser / Midday / Evening / Night Owl / All-day."""
    total = sum(hour_counts.values())
    if not total:
        return "All-day"
    early = sum(hour_counts.get(h, 0) for h in range(4, 9)) / total
    mid = sum(hour_counts.get(h, 0) for h in range(9, 16)) / total
    evening = sum(hour_counts.get(h, 0) for h in range(16, 21)) / total
    night = sum(hour_counts.get(h, 0) for h in list(range(21, 24)) + list(range(0, 4))) / total
    dominant = max(early, mid, evening, night)
    if dominant < 0.35:
        return "All-day"
    if dominant == early:
        return "Early Riser"
    if dominant == mid:
        return "Midday"
    if dominant == evening:
        return "Evening"
    return "Night Owl"


def _build_briefing(conn, species_rows: list, hourly: dict, visible_days: tuple, slugs: dict) -> str:
    """Generate warm naturalist prose summary of the visible week."""
    if not visible_days or not species_rows:
        return "No data available for this period yet — check back once the system has been running for a few days."

    total_detections = sum(r["total"] for r in species_rows)
    total_species = len(species_rows)

    # Busiest day from daily_rates, filtered to visible window
    dr = storage.daily_rates(conn)
    day_counts = {r["day"]: r["detections"] for r in dr if r["day"] in visible_days}
    if day_counts:
        busiest_day = max(day_counts, key=day_counts.get)
        busiest_n = day_counts[busiest_day]
        try:
            bd = date.fromisoformat(busiest_day)
            busiest_label = bd.strftime("%A, %B %-d")
        except ValueError:
            busiest_label = busiest_day
    else:
        busiest_label = "the peak day"
        busiest_n = 0

    # Dawn chorus peak hour (04–09, normalized by audio if possible)
    dawn_rates = {}
    for h in range(4, 10):
        data = hourly.get(h, {"n": 0, "audio_sec": 0})
        sec = data.get("audio_sec") or 0
        n = data.get("n") or 0
        dawn_rates[h] = n / sec if sec > 0 else n
    peak_dawn_h = max(dawn_rates, key=dawn_rates.get) if dawn_rates else 6
    peak_dawn_label = f"{peak_dawn_h:02d}:00"

    # Star species (most detections)
    star = species_rows[0] if species_rows else None
    star_name = star["common_name"] if star else "Unknown"
    star_count = star["total"] if star else 0

    # Newcomer: species whose all-time first detection falls within visible window
    window_start = min(visible_days)
    first_seen_rows = conn.execute(
        "SELECT common_name, MIN(date(heard_at)) AS first FROM detections GROUP BY common_name"
    ).fetchall()
    newcomer_set = {
        r["common_name"] for r in first_seen_rows
        if r["first"] and r["first"] >= window_start
    }
    # Sort newcomers by activity this week (species_rows is already DESC by total)
    newcomers_sorted = [r["common_name"] for r in species_rows if r["common_name"] in newcomer_set]
    # Append any newcomers with zero detections this week (shouldn't happen, but guard)
    newcomers_sorted += [n for n in newcomer_set if n not in newcomers_sorted]

    try:
        ws = date.fromisoformat(min(visible_days)).strftime("%B %-d")
        we = date.fromisoformat(max(visible_days)).strftime("%B %-d")
    except ValueError:
        ws, we = min(visible_days), max(visible_days)

    sentences: list[str] = []
    sentences.append(
        f"The yard was alive this week — {total_species} species contributed "
        f"{total_detections:,} detections across {len(visible_days)} days ({ws}–{we})."
    )
    if busiest_n:
        sentences.append(
            f"The soundscape peaked on {busiest_label} with {busiest_n:,} calls logged."
        )
    sentences.append(
        f"The dawn chorus crested around {peak_dawn_label}, "
        f"when the yard was at its most vocal."
    )
    if star:
        sentences.append(
            f"The week's standout performer was the {star_name}, "
            f"which accounted for {star_count:,} detections — the loudest voice in the canopy."
        )
    if newcomers_sorted:
        first_new = newcomers_sorted[0]
        others = len(newcomers_sorted) - 1
        if others > 0:
            sentences.append(
                f"New arrivals this week: the {first_new} "
                f"(and {others} other{'s' if others > 1 else ''}) heard for the first time "
                f"in the recording history."
            )
        else:
            sentences.append(
                f"New to the yard this week: the {first_new}, "
                f"heard for the first time in the recording history."
            )

    return " ".join(sentences)


def _build_risers_fallers(this_counts: dict, prior_counts: dict) -> dict:
    all_species = set(this_counts) | set(prior_counts)
    movers: list[dict] = []
    for name in all_species:
        this = this_counts.get(name, 0)
        prior = prior_counts.get(name, 0)
        if prior == 0 and this > 0:
            movers.append({"name": name, "this": this, "prior": 0, "kind": "new", "pct": None})
        elif prior > 0 and this == 0:
            movers.append({"name": name, "this": 0, "prior": prior, "kind": "gone", "pct": None})
        elif prior > 0:
            pct = (this - prior) / prior * 100
            movers.append({"name": name, "this": this, "prior": prior, "kind": "move", "pct": pct})
    risers = sorted(
        [m for m in movers if m["kind"] == "move" and (m["pct"] or 0) > 20],
        key=lambda m: -(m["pct"] or 0),
    )[:5]
    fallers = sorted(
        [m for m in movers if m["kind"] == "move" and (m["pct"] or 0) < -20],
        key=lambda m: (m["pct"] or 0),
    )[:5]
    return {
        "risers": risers,
        "fallers": fallers,
        "new": [m for m in movers if m["kind"] == "new"][:5],
        "gone": [m for m in movers if m["kind"] == "gone"][:5],
    }


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
    return True, _today()


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


def _feed_payload(conn, *, since: str | None = None, limit: int = 100, hours: int = 24) -> dict:
    min_conf = _CFG.get("min_conf", config.DEFAULTS["min_conf"])
    rows = storage.recent_feed(conn, limit=limit, since=since, min_conf=min_conf, hours=hours)
    slugs = _slug_map()
    events = [_feed_event_dict(r, slugs) for r in rows]
    latest = events[0]["heard_at"] if events else (since or "")
    return {"events": events, "latest": latest}


_TWITTER_PER_PAGE = 50


def _build_twitter_feed(
    conn,
    *,
    selected_day: str,
    show_all: bool,
    page: int = 1,
    hide_low: bool = False,
    sort: str = "time",
) -> dict:
    floor = TIMELINE_CONF_FLOOR if hide_low else _CFG.get("min_conf", config.DEFAULTS["min_conf"])
    offset = (page - 1) * _TWITTER_PER_PAGE
    rows = storage.detection_feed(
        conn,
        day=None if show_all else selected_day,
        show_all=show_all,
        min_conf=floor,
        limit=_TWITTER_PER_PAGE + 1,
        offset=offset,
        sort=sort,
    )
    has_more = len(rows) > _TWITTER_PER_PAGE
    rows = rows[:_TWITTER_PER_PAGE]

    slugs = _slug_map()
    events = []
    for r in rows:
        slug = slugs.get(r["common_name"]) or _common_to_slug(r["common_name"])
        events.append({
            "common_name": r["common_name"],
            "scientific_name": r["scientific_name"],
            "slug": slug,
            "sprite_slug": _sprite_slug(r["common_name"], slugs),
            "initials": _species_initials(r["common_name"]),
            "peak_conf": r["peak_conf"],
            "heard_at": r["heard_at"],
            "time_label": _format_heard_clock(r["heard_at"]),
            "segment_id": r["segment_id"],
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "has_audio": bool(r["has_audio"]),
            "bird_url": f"/bird/{slug}" + _qs(day=selected_day if not show_all else None, show_all=show_all),
            "call_url": _call_href(
                r["segment_id"], r["start_time"], r["end_time"], slug,
                show_all=show_all, selected_day=selected_day,
                sort=sort, hide_low=hide_low,
            ),
        })

    return {
        "events": events,
        "page": page,
        "has_more": has_more,
        "has_prev": page > 1,
        "sort": sort,
    }


@app.route("/api/twitter")
def api_twitter():
    show_all, selected_day = _parse_day_arg(request.args.get("day"))
    page = max(1, request.args.get("page", 1, type=int))
    hide_low = _parse_hide_low(request.args.get("hide_low"))
    sort = "conf" if request.args.get("sort") == "conf" else "time"

    conn = _db()
    try:
        feed = _build_twitter_feed(
            conn,
            selected_day=selected_day,
            show_all=show_all,
            page=page,
            hide_low=hide_low,
            sort=sort,
        )
        return Response(
            json.dumps({
                "events": feed["events"],
                "page": feed["page"],
                "has_more": feed["has_more"],
                "sort": feed["sort"],
            }),
            mimetype="application/json",
        )
    finally:
        conn.close()


@app.route("/twitter")
def twitter_view():
    today = _today()
    show_all, selected_day = _parse_day_arg(request.args.get("day"))
    page = max(1, request.args.get("page", 1, type=int))
    hide_low = _parse_hide_low(request.args.get("hide_low"))
    sort = "conf" if request.args.get("sort") == "conf" else "time"

    conn = _db()
    try:
        feed = _build_twitter_feed(
            conn,
            selected_day=selected_day,
            show_all=show_all,
            page=page,
            hide_low=hide_low,
            sort=sort,
        )

        if show_all:
            stats = storage.totals(conn)
            total_detections = stats["detections"] or 0
            total_species = stats["species"] or 0
        else:
            ov = storage.day_overview(conn, selected_day)
            total_detections = ov["detections"] or 0
            total_species = ov["species"] or 0

        slugs = _slug_map()
        trending = []
        for r in storage.species_dex_since(
            conn,
            (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds"),
            min_conf=PEAK_CONF_FLOOR,
        )[:10]:
            slug = slugs.get(r["common_name"]) or _common_to_slug(r["common_name"])
            trending.append({
                "common_name": r["common_name"],
                "slug": slug,
                "windows": r["windows"],
                "peak_conf": r["peak_conf"],
            })

        sel = date.fromisoformat(selected_day)
        prev_day = (sel - timedelta(days=1)).isoformat()
        next_day = (sel + timedelta(days=1)).isoformat()

        def qs(**kw):
            parts = []
            if not show_all or kw.get("day"):
                d = kw.get("day", selected_day)
                if d and d != today:
                    parts.append(f"day={d}")
                elif kw.get("day") == "all" or show_all:
                    parts.append("day=all")
            if kw.get("sort", sort) != "time":
                parts.append(f"sort={kw.get('sort', sort)}")
            if kw.get("hide_low", hide_low):
                parts.append("hide_low=1")
            if not parts:
                return "/twitter"
            return "/twitter?" + "&".join(parts)

        return render_template(
            "twitter.html",
            today=today,
            selected_day=selected_day,
            show_all=show_all,
            prev_day=prev_day,
            next_day=next_day,
            page=page,
            feed=feed,
            hide_low=hide_low,
            conf_floor=TIMELINE_CONF_FLOOR,
            sort=sort,
            total_detections=total_detections,
            total_species=total_species,
            trending=trending,
            qs=qs,
            dev=_dev_mode(),
            dev_script=_DEV_RELOAD_SCRIPT,
        )
    finally:
        conn.close()


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


def _resolve_call_clip(
    conn,
    segment_id: int,
    start: float | None,
    end: float | None,
) -> dict | None:
    """Return one detection window for the call chamber, or None."""
    if start is None or end is None or end <= start:
        return None
    row = conn.execute(
        """
        SELECT d.common_name, d.confidence, d.start_time, d.end_time,
               d.heard_at, d.segment_id
        FROM detections d
        WHERE d.segment_id = ?
          AND ABS(d.start_time - ?) < 0.05
          AND ABS(d.end_time - ?) < 0.05
        LIMIT 1
        """,
        (segment_id, start, end),
    ).fetchone()
    if not row:
        return None
    slugs = _slug_map()
    common_name = row["common_name"]
    return {
        "slug": slugs.get(common_name) or _common_to_slug(common_name),
        "name": common_name,
        "segment_id": row["segment_id"],
        "start": row["start_time"],
        "end": row["end_time"],
        "peak_conf": row["confidence"],
        "heard_at": row["heard_at"],
    }


def _species_call_order(
    conn,
    common_name: str,
    *,
    show_all: bool,
    selected_day: str,
) -> list[dict]:
    """All detection windows for one species, highest confidence first."""
    slugs = _slug_map()
    bird_slug = slugs.get(common_name) or _common_to_slug(common_name)
    rows = storage.species_detections(
        conn,
        common_name,
        day=None if show_all else selected_day,
        show_all=show_all,
        limit=500,
    )
    return [
        {
            "slug": bird_slug,
            "name": common_name,
            "segment_id": r["segment_id"],
            "start": r["start_time"],
            "end": r["end_time"],
            "peak_conf": r["confidence"],
            "heard_at": r["heard_at"],
        }
        for r in rows
    ]


def _call_clip_index(calls: list[dict], segment_id: int, start: float, end: float) -> int | None:
    for i, e in enumerate(calls):
        if (
            e["segment_id"] == segment_id
            and abs(e["start"] - start) < 0.05
            and abs(e["end"] - end) < 0.05
        ):
            return i
    return None


def _clip_nav(
    conn,
    common_name: str,
    segment_id: int,
    start: float,
    end: float,
    *,
    show_all: bool,
    selected_day: str,
) -> tuple[dict | None, dict | None, int, int]:
    """Prev/next clip within a species plus 1-based position."""
    calls = _species_call_order(
        conn, common_name, show_all=show_all, selected_day=selected_day
    )
    total = len(calls)
    if not total:
        return None, None, 0, 0
    idx = _call_clip_index(calls, segment_id, start, end)
    if idx is None:
        idx = 0
    return (
        calls[idx - 1] if idx > 0 else None,
        calls[idx + 1] if idx < total - 1 else None,
        idx + 1,
        total,
    )


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
        return render_template(
            "live.html",
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


@app.route("/realtime")
def realtime_feed():
    conn = _db()
    try:
        payload = _feed_payload(conn, hours=1)
        events = payload["events"]
        latest = payload["latest"]
        return render_template(
            "realtime.html",
            events=events,
            latest_json=json.dumps(latest),
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
        dex = []
        for r in _sort_dex_rows(rows, sort):
            prev_clip, next_clip, clip_no, clip_total = _clip_nav(
                conn,
                r["common_name"],
                r["segment_id"],
                r["start_time"],
                r["end_time"],
                show_all=show_all,
                selected_day=selected_day,
            )
            dex.append(
                dict(
                    r,
                    sprite_slug=_sprite_slug(r["common_name"], slugs),
                    bird_slug=slugs.get(r["common_name"]) or _common_to_slug(r["common_name"]),
                    prev_clip=prev_clip,
                    next_clip=next_clip,
                    clip_no=clip_no,
                    clip_total=clip_total,
                )
            )
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

        return render_template(
            "index.html",
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


@app.route("/heard")
def heard():
    days = request.args.get("range")
    days = int(days) if days in ("1", "3", "7") else 1
    hi = request.args.get("hi") != "0"
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    conn = _db()
    try:
        rows = storage.species_dex_since(
            conn, since, min_conf=PEAK_CONF_FLOOR if hi else None
        )
    finally:
        conn.close()
    slugs = _slug_map()
    birds = [
        dict(
            r,
            sprite_slug=_sprite_slug(r["common_name"], slugs),
            bird_slug=slugs.get(r["common_name"]) or _common_to_slug(r["common_name"]),
        )
        for r in rows
    ]

    def hqs(range: int = days, hi: bool = hi) -> str:
        parts = []
        if range != 1:
            parts.append(f"range={range}")
        if not hi:
            parts.append("hi=0")
        return "?" + "&".join(parts) if parts else "/heard"

    return render_template(
        "heard.html",
        birds=birds,
        days=days,
        hi=hi,
        conf_floor=PEAK_CONF_FLOOR,
        hqs=hqs,
        dev=_dev_mode(),
        dev_script=_DEV_RELOAD_SCRIPT,
    )


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

        species_calls = _species_call_order(
            conn,
            common_name,
            show_all=show_all,
            selected_day=selected_day,
        )
        if species_calls:
            peak = species_calls[0]
            prev_clip, next_clip, clip_no, clip_total = _clip_nav(
                conn,
                common_name,
                peak["segment_id"],
                peak["start"],
                peak["end"],
                show_all=show_all,
                selected_day=selected_day,
            )
        else:
            prev_clip = next_clip = None
            clip_no = clip_total = 0

        def call_href(e: dict) -> str:
            return _call_href(
                e["segment_id"],
                e["start"],
                e["end"],
                slug,
                show_all=show_all,
                selected_day=selected_day,
                sort=sort,
                hide_low=hide_low,
            )

        return render_template(
            "bird.html",
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
            prev_clip=prev_clip,
            next_clip=next_clip,
            clip_no=clip_no,
            clip_total=clip_total,
            dex_no=(dex_idx + 1) if dex_idx is not None else 0,
            dex_total=len(dex_birds),
            bird_href=bird_href,
            call_href=call_href,
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
        return render_template(
            "timeline.html",
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
        return render_template(
            "data.html",
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


@app.route("/prevalence")
def prevalence_view():
    conn = _db()
    try:
        slugs = _slug_map()
        rows = storage.species_dex(conn)
        rows = sorted(rows, key=lambda r: (-r["windows"], -r["peak_conf"]))
        birds = [
            dict(
                r,
                rank=i + 1,
                bird_slug=slugs.get(r["common_name"]) or _common_to_slug(r["common_name"]),
                sprite_slug=_sprite_slug(r["common_name"], slugs),
            )
            for i, r in enumerate(rows)
        ]
        all_totals = storage.totals(conn)
        return render_template(
            "prevalence.html",
            birds=birds,
            total_species=len(birds),
            all_totals=all_totals,
            dev=_dev_mode(),
            dev_script=_DEV_RELOAD_SCRIPT,
        )
    finally:
        conn.close()


@app.route("/trends")
def trends_view():
    today = _today()
    conn = _db()
    try:
        visible = _visible_days(conn, today, span=7)
        prior_end = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
        prior_visible = _visible_days(conn, prior_end, span=7)

        if not visible:
            return render_template(
                "trends.html",
                visible_days=visible,
                span=7,
                window_label="no usable days",
                total_species=0,
                total_detections=0,
                busiest_day=None,
                briefing="No usable data in the current window.",
                clock_svg="",
                dawn_leaders=[],
                night_owls=[],
                chronotypes=[],
                has_prior_week=False,
                risers_fallers=None,
                dev=_dev_mode(),
                dev_script=_DEV_RELOAD_SCRIPT,
            )

        hourly_rows = storage.hourly_in_range(conn, visible)
        species_rows = storage.species_in_range(conn, visible)
        hour_matrix_rows = storage.species_hour_matrix(conn, visible)
        slugs = _slug_map()

        # Hourly dict {h: {n, audio_sec}}, all 24 hours filled
        hourly: dict[int, dict] = {h: {"n": 0, "audio_sec": 0.0} for h in range(24)}
        for r in hourly_rows:
            hourly[int(r["hour"])] = {"n": r["n"], "audio_sec": r["audio_sec"] or 0.0}

        # Per-species hour matrix
        matrix: dict[str, dict[int, int]] = {}
        for r in hour_matrix_rows:
            matrix.setdefault(r["common_name"], {})[int(r["hour"])] = r["n"]

        total_detections = sum(r["total"] for r in species_rows)
        total_species = len(species_rows)

        # Busiest day
        dr = storage.daily_rates(conn)
        day_counts = {r["day"]: r["detections"] for r in dr if r["day"] in visible}
        busiest_day = max(day_counts, key=day_counts.get) if day_counts else (visible[-1] if visible else None)

        # Window label
        try:
            ws = date.fromisoformat(min(visible)).strftime("%b %-d")
            we = date.fromisoformat(max(visible)).strftime("%b %-d")
            window_label = f"{ws}–{we}"
        except (ValueError, TypeError):
            window_label = f"{len(visible)} days"

        # Activity Clock SVG
        clock_svg = _build_clock_svg(hourly)

        # Dawn Chorus leaderboard (morning_share = morning/total, min 5 detections)
        dawn_leaders = []
        for r in sorted(
            (r for r in species_rows if r["total"] >= 5),
            key=lambda r: r["morning"] / max(r["total"], 1),
            reverse=True,
        )[:10]:
            dawn_leaders.append({
                "common_name": r["common_name"],
                "bird_slug": slugs.get(r["common_name"]) or _common_to_slug(r["common_name"]),
                "sprite_slug": _sprite_slug(r["common_name"], slugs),
                "morning_share": r["morning"] / max(r["total"], 1),
                "total": r["total"],
            })

        # Night Shift leaderboard
        night_owls = []
        for r in sorted(
            (r for r in species_rows if r["total"] >= 5),
            key=lambda r: r["night"] / max(r["total"], 1),
            reverse=True,
        )[:8]:
            night_owls.append({
                "common_name": r["common_name"],
                "bird_slug": slugs.get(r["common_name"]) or _common_to_slug(r["common_name"]),
                "sprite_slug": _sprite_slug(r["common_name"], slugs),
                "night_share": r["night"] / max(r["total"], 1),
                "total": r["total"],
            })

        # Chronotypes — top 24 species by total
        chronotypes = []
        for r in species_rows[:24]:
            name = r["common_name"]
            hcounts = matrix.get(name, {})
            chrono_type = _classify_chronotype(hcounts)
            peak_h = max(hcounts, key=hcounts.get) if hcounts else None
            peak_hour_label = f"{peak_h:02d}:00" if peak_h is not None else "n/a"
            chronotypes.append({
                "common_name": name,
                "bird_slug": slugs.get(name) or _common_to_slug(name),
                "chrono_type": chrono_type,
                "peak_hour_label": peak_hour_label,
                "total": r["total"],
                "sparkline": _sparkline_svg(hcounts),
            })

        # Briefing prose
        briefing = _build_briefing(conn, species_rows, hourly, visible, slugs)

        # Risers & Fallers
        has_prior_week = len(prior_visible) >= 3
        risers_fallers = None
        if has_prior_week:
            prior_raw = storage.species_range_counts(conn, prior_visible)
            this_counts = {r["common_name"]: r["total"] for r in species_rows}
            prior_counts = {r["common_name"]: r["total"] for r in prior_raw}
            risers_fallers = _build_risers_fallers(this_counts, prior_counts)

        return render_template(
            "trends.html",
            visible_days=visible,
            span=7,
            window_label=window_label,
            total_species=total_species,
            total_detections=total_detections,
            busiest_day=busiest_day,
            briefing=briefing,
            clock_svg=clock_svg,
            dawn_leaders=dawn_leaders,
            night_owls=night_owls,
            chronotypes=chronotypes,
            has_prior_week=has_prior_week,
            risers_fallers=risers_fallers,
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

        entry = _resolve_call_clip(conn, segment_id, start, end)
        if entry is None:
            abort(404)

        common_name = entry["name"]
        bird_slug = slug or entry["slug"]
        meta = storage.species_meta(conn, common_name)

        call_order = _dex_call_order(
            conn,
            show_all=show_all,
            selected_day=selected_day,
            sort=sort,
            hide_low=hide_low,
        )
        dex_idx = next(
            (i for i, e in enumerate(call_order) if e["slug"] == bird_slug),
            None,
        )
        prev_call = (
            call_order[dex_idx - 1]
            if dex_idx is not None and dex_idx > 0
            else None
        )
        next_call = (
            call_order[dex_idx + 1]
            if dex_idx is not None and dex_idx < len(call_order) - 1
            else None
        )

        prev_clip, next_clip, clip_no, clip_total = _clip_nav(
            conn,
            common_name,
            segment_id,
            start,
            end,
            show_all=show_all,
            selected_day=selected_day,
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

        return render_template(
            "call.html",
            segment_id=segment_id,
            start=start,
            end=end,
            slug=bird_slug,
            common_name=common_name,
            scientific_name=meta["scientific_name"] if meta else "",
            peak_conf=entry["peak_conf"],
            dex_no=(dex_idx + 1) if dex_idx is not None else 0,
            dex_total=len(call_order),
            clip_no=clip_no,
            clip_total=clip_total,
            prev_call=prev_call,
            next_call=next_call,
            prev_clip=prev_clip,
            next_clip=next_clip,
            call_href=call_href,
            back_href=back_href,
            sprite_slug=_sprite_slug(common_name, _slug_map()),
            clip_seconds=(end - start)
            if start is not None and end is not None and end > start
            else None,
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
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("wave"):
                return Response(json.dumps(cached), mimetype="application/json")
        except (json.JSONDecodeError, TypeError):
            pass

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


@app.route("/vibeviz/<int:segment_id>.json")
def vibeviz_json(segment_id: int):
    start = request.args.get("start", type=float)
    end = request.args.get("end", type=float)
    cache_path = _vibeviz_cache_path(segment_id, start, end, _CFG["recordings_dir"])
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("version") == _VIBEVIZ_VERSION:
                return Response(json.dumps(cached), mimetype="application/json")
        except (json.JSONDecodeError, TypeError):
            pass

    conn = _db()
    try:
        audio_path = _resolve_call_audio(conn, segment_id, start, end)
    finally:
        conn.close()
    if not audio_path:
        abort(404)

    payload = _compute_vibeviz(audio_path, start, end)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload))
    return Response(json.dumps(payload), mimetype="application/json")


@app.route("/vibe/<int:segment_id>")
def vibe_view(segment_id: int):
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
        entry = _resolve_call_clip(conn, segment_id, start, end)
        if entry is None:
            abort(404)
        common_name = entry["name"]
        bird_slug = slug or entry["slug"]
        meta = storage.species_meta(conn, common_name)
        call_href = _call_href(
            segment_id,
            start,
            end,
            bird_slug,
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
        return render_template(
            "vibe.html",
            segment_id=segment_id,
            start=start,
            end=end,
            slug=bird_slug,
            common_name=common_name,
            scientific_name=meta["scientific_name"] if meta else "",
            sprite_slug=_sprite_slug(common_name, _slug_map()),
            call_href=call_href,
            back_href=back_href,
            dev=_dev_mode(),
            dev_script=_DEV_RELOAD_SCRIPT,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /dash — React dashboard (SPA built from dashboard-ui/, one JSON cube API)
# ---------------------------------------------------------------------------

_DASH_DIST = _ROOT / "dashboard-ui" / "dist"
_PIXEL_SPRITES_DIR = _ROOT / "sprites"
# Confidence bucket edges: bucket 0 = [min_conf, .5), 1 = [.5, .7), 2 = [.7, .9), 3 = [.9, 1]
_DASH_CONF_EDGES = (0.5, 0.7, 0.9)

_dash_summary_cache: dict | None = None  # {"key": (count, max_id), "body": bytes}
_dash_sure_cache: dict | None = None


def _dash_cache_key(conn) -> tuple:
    """Invalidate when detections change *or* retention clears audio paths."""
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM detections) AS n,
          (SELECT COALESCE(MAX(id), 0) FROM detections) AS m,
          (SELECT COUNT(*) FROM segments WHERE wav_path IS NOT NULL) AS segs,
          (SELECT COUNT(*) FROM tracks WHERE clip_path IS NOT NULL) AS clips
        """
    ).fetchone()
    return (row["n"], row["m"], row["segs"], row["clips"])


def _dash_sample_clips(
    conn,
    *,
    min_conf: float | None = None,
    per_species: int = 1,
    excluded_days: tuple[str, ...] = (),
) -> dict[str, list[dict]]:
    """Highest-confidence *still-kept* playable windows per species.

    Retention NULLs `wav_path` / `clip_path` but leaves detection rows. Sample
    selection only considers rows that still have a path so aged-out peaks do
    not hide (or get offered as) recordings. Totals stay elsewhere.
    """
    filters = ["(t.clip_path IS NOT NULL OR sg.wav_path IS NOT NULL)"]
    params: list[object] = []
    if min_conf is not None:
        filters.append("d.confidence >= ?")
        params.append(min_conf)
    if excluded_days:
        placeholders = ",".join("?" * len(excluded_days))
        filters.append(f"date(d.heard_at) NOT IN ({placeholders})")
        params.extend(excluded_days)
    where = "WHERE " + " AND ".join(filters)
    # Enough candidates per species to survive missing-on-disk path checks.
    candidate_limit = max(8, per_species * 4)

    rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT d.common_name AS name, d.segment_id AS seg,
                 d.start_time AS s, d.end_time AS e, d.confidence AS conf,
                 t.clip_path AS clip_path, sg.wav_path AS wav_path,
                 ROW_NUMBER() OVER (
                   PARTITION BY d.common_name
                   ORDER BY d.confidence DESC, d.id DESC
                 ) AS rn
          FROM detections d
          LEFT JOIN tracks t ON t.id = d.track_id
          JOIN segments sg ON sg.id = d.segment_id
          {where}
        )
        SELECT * FROM ranked WHERE rn <= ?
        """,
        (*params, candidate_limit),
    ).fetchall()
    samples: dict[str, list[dict]] = {}
    for r in rows:
        species_samples = samples.setdefault(r["name"], [])
        if len(species_samples) >= per_species:
            continue
        playable = (r["clip_path"] and os.path.exists(r["clip_path"])) or (
            r["wav_path"] and os.path.exists(r["wav_path"])
        )
        if playable:
            species_samples.append(
                {
                    "seg": r["seg"],
                    "s": round(r["s"], 2),
                    "e": round(r["e"], 2),
                    "conf": round(r["conf"], 3),
                }
            )
    return {name: clips for name, clips in samples.items() if clips}


def _dash_best_clips(conn) -> dict[str, dict]:
    """Highest-confidence playable window per species."""
    return {
        name: clips[0]
        for name, clips in _dash_sample_clips(conn).items()
    }


def _dash_conf_bucket_sql() -> str:
    lo, mid, hi = _DASH_CONF_EDGES
    return (
        f"CASE WHEN confidence >= {hi} THEN 3 "
        f"WHEN confidence >= {mid} THEN 2 "
        f"WHEN confidence >= {lo} THEN 1 ELSE 0 END"
    )


def _build_dash_summary(conn) -> dict:
    excluded = sorted(EXCLUDED_DAYS)
    not_excluded = "date(heard_at) NOT IN (%s)" % ",".join("?" * len(excluded)) if excluded else "1=1"

    sp_rows = conn.execute(
        f"""
        SELECT common_name AS name, scientific_name AS sci, COUNT(*) AS total,
               MAX(confidence) AS peak, MIN(heard_at) AS first, MAX(heard_at) AS last
        FROM detections WHERE {not_excluded}
        GROUP BY common_name ORDER BY total DESC, name
        """,
        excluded,
    ).fetchall()

    slugs = _slug_map()
    info = _bird_info()
    clips = _dash_best_clips(conn)
    species = []
    index_of: dict[str, int] = {}
    for i, r in enumerate(sp_rows):
        slug = slugs.get(r["name"]) or _common_to_slug(r["name"])
        entry = {
            "name": r["name"],
            "sci": r["sci"],
            "slug": slug,
            "art": (_SPRITES_DIR / f"{slug}.png").is_file(),
            "pixel": (_PIXEL_SPRITES_DIR / f"{slug}.png").is_file(),
            "total": r["total"],
            "peak": round(r["peak"], 3),
            "first": r["first"],
            "last": r["last"],
        }
        clip = clips.get(r["name"])
        if clip:
            entry["clip"] = clip
        blurb = info.get(slug)
        if blurb:
            entry["info"] = blurb
        species.append(entry)
        index_of[r["name"]] = i

    day_row = conn.execute(
        "SELECT date(MIN(heard_at)) AS lo, date(MAX(heard_at)) AS hi FROM detections"
    ).fetchone()
    days: list[str] = []
    sun: list[dict] = []
    if day_row["lo"]:
        d = date.fromisoformat(day_row["lo"])
        hi = date.fromisoformat(day_row["hi"])
        lat = _CFG.get("lat") or 34.4208
        lon = _CFG.get("lon") or -119.6982
        while d <= hi:
            days.append(d.isoformat())
            arc = _solar_arc(lat, lon, d)
            sun.append(
                {
                    "rise": round(arc["sunrise_pct"] / 100 * 24, 2),
                    "set": round(arc["sunset_pct"] / 100 * 24, 2),
                }
            )
            d += timedelta(days=1)
    day_index = {day: i for i, day in enumerate(days)}

    cube_rows = conn.execute(
        f"""
        SELECT date(heard_at) AS day, CAST(strftime('%H', heard_at) AS INTEGER) AS hour,
               common_name AS name, {_dash_conf_bucket_sql()} AS cb, COUNT(*) AS n
        FROM detections WHERE {not_excluded}
        GROUP BY day, hour, name, cb
        """,
        excluded,
    ).fetchall()
    cube = [
        [day_index[r["day"]], r["hour"], index_of[r["name"]], r["cb"], r["n"]]
        for r in cube_rows
        if r["day"] in day_index and r["name"] in index_of
    ]

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "location": "Santa Barbara, CA",
            "tz": "America/Los_Angeles",
            "min_conf": _CFG.get("min_conf", 0.3),
            "conf_edges": list(_DASH_CONF_EDGES),
            "excluded_days": excluded,
        },
        "days": days,
        "sun": sun,
        "species": species,
        "cube": cube,
    }


def _build_dash_sure(conn) -> dict:
    """All-time species leaderboard for detections at 0.9+ confidence."""
    min_conf = _DASH_CONF_EDGES[-1]
    excluded = tuple(sorted(EXCLUDED_DAYS))
    not_excluded = (
        "date(heard_at) NOT IN (%s)" % ",".join("?" * len(excluded))
        if excluded
        else "1=1"
    )
    rows = conn.execute(
        f"""
        SELECT common_name AS name, scientific_name AS sci, COUNT(*) AS count,
               MAX(confidence) AS peak
        FROM detections
        WHERE confidence >= ? AND {not_excluded}
        GROUP BY common_name
        ORDER BY count DESC, name
        """,
        (min_conf, *excluded),
    ).fetchall()

    slugs = _slug_map()
    info = _bird_info()
    clips = _dash_sample_clips(
        conn,
        min_conf=min_conf,
        per_species=3,
        excluded_days=excluded,
    )
    species = []
    for row in rows:
        slug = slugs.get(row["name"]) or _common_to_slug(row["name"])
        entry = {
            "name": row["name"],
            "sci": row["sci"],
            "slug": slug,
            "art": (_SPRITES_DIR / f"{slug}.png").is_file(),
            "pixel": (_PIXEL_SPRITES_DIR / f"{slug}.png").is_file(),
            "count": row["count"],
            "peak": round(row["peak"], 3),
            "clips": clips.get(row["name"], []),
        }
        blurb = info.get(slug)
        if blurb:
            entry["info"] = blurb
        species.append(entry)

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "location": "Santa Barbara, CA",
            "min_conf": min_conf,
            "excluded_days": list(excluded),
        },
        "species": species,
    }


@app.route("/api/dash/summary")
def api_dash_summary():
    global _dash_summary_cache
    conn = _db()
    try:
        key = _dash_cache_key(conn)
        if _dash_summary_cache is None or _dash_summary_cache["key"] != key:
            body = json.dumps(_build_dash_summary(conn), separators=(",", ":"))
            _dash_summary_cache = {"key": key, "body": body.encode()}
    finally:
        conn.close()

    etag = f'"{key[0]}-{key[1]}-{key[2]}-{key[3]}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    return Response(
        _dash_summary_cache["body"],
        mimetype="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@app.route("/api/dash/sure")
def api_dash_sure():
    global _dash_sure_cache
    conn = _db()
    try:
        key = _dash_cache_key(conn)
        if _dash_sure_cache is None or _dash_sure_cache["key"] != key:
            body = json.dumps(_build_dash_sure(conn), separators=(",", ":"))
            _dash_sure_cache = {"key": key, "body": body.encode()}
    finally:
        conn.close()

    etag = f'"sure-{key[0]}-{key[1]}-{key[2]}-{key[3]}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    return Response(
        _dash_sure_cache["body"],
        mimetype="application/json",
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@app.route("/dash")
@app.route("/dash/")
def dash_index():
    index = _DASH_DIST / "index.html"
    if not index.is_file():
        return Response(
            "dashboard UI not built yet — run: cd dashboard-ui && npm install && npm run build",
            status=503,
            mimetype="text/plain",
        )
    return send_file(index)


@app.route("/dash/<path:relpath>")
def dash_asset(relpath: str):
    from flask import send_from_directory

    if (_DASH_DIST / relpath).is_file():
        return send_from_directory(_DASH_DIST, relpath)
    if relpath.startswith("assets/") or Path(relpath).suffix:
        abort(404)
    return dash_index()


@app.route("/psprite/<slug>.png")
def pixel_sprite(slug: str):
    path = _PIXEL_SPRITES_DIR / f"{slug}.png"
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/png")


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

    S = librosa.feature.melspectrogram(
        y=y, sr=sr, n_fft=2048, hop_length=256, n_mels=256, fmax=sr // 2
    )
    S_db = librosa.power_to_db(S, ref=np.max)

    fig, ax = plt.subplots(figsize=(6, 2.2), dpi=300)
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


_DEMOS_DIR = Path.home() / "drray" / "demos"


@app.route("/demos/", defaults={"relpath": ""})
@app.route("/demos/<path:relpath>")
def demos(relpath: str):
    root = _DEMOS_DIR.resolve()
    if not root.is_dir():
        abort(404)
    if not relpath:
        links = []
        for p in sorted(root.iterdir()):
            if p.name.startswith("."):
                continue
            if p.is_file() and p.suffix == ".html":
                links.append(f'<li><a href="/demos/{p.name}">{p.stem}</a></li>')
            elif p.is_dir() and (p / "index.html").is_file():
                label = p.name.replace("-", " ").title()
                links.append(f'<li><a href="/demos/{p.name}/">{label}</a></li>')
        body = (
            "<!doctype html><html><head><meta charset=utf-8>"
            "<title>Demos</title></head><body><h1>Demos</h1><ul>"
            + "".join(links)
            + "</ul></body></html>"
        )
        return Response(body, mimetype="text/html")
    target = (root / relpath).resolve()
    if not str(target).startswith(str(root)):
        abort(403)
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        abort(404)
    return send_file(target)


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
