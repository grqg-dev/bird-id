"""Extract detection-window clips from segment wav files (no mic, no BirdNET)."""

from __future__ import annotations

import io
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import config


def _ffmpeg_path() -> str | None:
    """Resolve ffmpeg on PATH or ~/bin (launchd on the Mac mini omits the latter)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    home_bin = Path.home() / "bin" / "ffmpeg"
    if home_bin.is_file():
        return str(home_bin)
    return None


def clip_format(cfg: dict) -> str:
    """Resolve the per-window clip format from config ('mp3' or 'wav')."""
    fmt = str(config.resolve(None, "clip_format", cfg) or "wav").lower()
    return "mp3" if fmt == "mp3" else "wav"


def clip_paths_for_detections(
    src_wav: str | Path,
    clips_dir: str | Path,
    started_at: datetime,
    detections,
    *,
    cfg: dict,
) -> dict[tuple[float, float], str]:
    """Write a clip file per unique detection window; return the
    (start, end) -> clip_path map that storage.record_segment expects.

    Shared by the local monitor (birdid.py) and the sensor ingest service
    (ingest.py) so both turn a segment wav into clips the same way.
    """
    clips_dir = Path(clips_dir).expanduser()
    clips_dir.mkdir(parents=True, exist_ok=True)
    fmt = clip_format(cfg)
    bitrate = str(config.resolve(None, "clip_mp3_bitrate", cfg) or "64k")
    clip_paths: dict[tuple[float, float], str] = {}
    for start, end in {(d.start_time, d.end_time) for d in detections}:
        dst = clip_dst_path(clips_dir, started_at, start, end, fmt=fmt)
        path = write_clip(src_wav, dst, start, end, fmt=fmt, mp3_bitrate=bitrate)
        if path:
            clip_paths[(start, end)] = path
    return clip_paths


def clip_dst_path(
    clips_dir: str | Path,
    started_at: datetime,
    start: float,
    end: float,
    *,
    fmt: str = "wav",
) -> Path:
    """Destination path for a detection-window clip (same naming as the monitor)."""
    clips_dir = Path(clips_dir).expanduser()
    ext = ".mp3" if fmt == "mp3" else ".wav"
    return clips_dir / (
        f"clip_{started_at:%Y%m%d_%H%M%S}_{int(start * 1000)}_{int(end * 1000)}{ext}"
    )


def _read_window(src_wav: Path, start: float, end: float):
    import soundfile as sf

    info = sf.info(str(src_wav))
    sr = info.samplerate
    start_frame = max(0, int(start * sr))
    end_frame = min(info.frames, int(end * sr))
    if end_frame <= start_frame:
        return None, None
    data, _ = sf.read(
        str(src_wav), start=start_frame, stop=end_frame, dtype="float32"
    )
    if data.size == 0:
        return None, None
    return data, sr


def _write_wav(dst_wav: Path, data, sr: int) -> str:
    import soundfile as sf

    dst_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst_wav), data, sr, format="WAV")
    return str(dst_wav)


def _write_mp3(dst_mp3: Path, data, sr: int, *, bitrate: str = "64k") -> str | None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None

    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, data, sr, format="WAV")
    wav_bytes = buf.getvalue()

    dst_mp3.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-b:a",
            bitrate,
            "-y",
            str(dst_mp3),
        ],
        input=wav_bytes,
        capture_output=True,
    )
    if proc.returncode != 0 or not dst_mp3.is_file():
        return None
    return str(dst_mp3)


def transcode_to_mp3(
    src: str | Path,
    dst: str | Path,
    *,
    bitrate: str = "64k",
) -> str | None:
    """Convert an existing audio file to mp3. Returns dst path, or None on failure."""
    ffmpeg = _ffmpeg_path()
    src = Path(src).expanduser()
    dst = Path(dst).expanduser()
    if not ffmpeg or not src.is_file():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-b:a",
            bitrate,
            "-y",
            str(dst),
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        return None
    return str(dst)


def write_clip(
    src_wav: str | Path,
    dst: str | Path,
    start: float,
    end: float,
    *,
    fmt: str = "wav",
    mp3_bitrate: str = "64k",
) -> str | None:
    """Write [start, end) of src_wav to dst. Returns dst path, or None if empty."""
    if end <= start:
        return None

    try:
        import soundfile as sf  # noqa: F401 — ensure backend is available
    except ImportError:
        return None

    src_wav = Path(src_wav).expanduser()
    if not src_wav.is_file():
        return None

    try:
        data, sr = _read_window(src_wav, start, end)
        if data is None:
            return None

        dst = Path(dst).expanduser()
        if fmt == "mp3":
            path = _write_mp3(dst, data, sr, bitrate=mp3_bitrate)
            if path:
                return path
            # ffmpeg missing or failed — fall back to wav beside the mp3 path
            dst = dst.with_suffix(".wav")

        return _write_wav(dst, data, sr)
    except (OSError, RuntimeError, ValueError):
        return None
