"""Audio, spectrogram, sprite, and Three.js visualisation routes (`/audio`, `/spectrogram`, `/sprite`, `/call`, `/vibe`, ...)."""

from __future__ import annotations
import io
import json
import os
from pathlib import Path

from flask import Response, abort, render_template, request, send_file

import storage
from .app import (
    _CFG,
    _DEV_RELOAD_SCRIPT,
    _PIXEL_SPRITES_DIR,
    _SPRITES_DIR,
    _dev_mode,
    _slug_map,
    _sprite_slug,
    app,
)
from .legacy import (
    _call_href,
    _clip_nav,
    _dex_call_order,
    _normalize_sort,
    _parse_day_arg,
    _parse_hide_low,
    _qs,
    _resolve_call_clip,
)

import dashboard as _pkg  # late-bound access so tests can monkeypatch dashboard._db etc.


def _db():
    return _pkg._db()


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

        back_href = "/dex" + _qs(
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
        back_href = "/dex" + _qs(
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
