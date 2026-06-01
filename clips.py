"""Extract detection-window clips from segment wav files (no mic, no BirdNET)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def clip_dst_path(
    clips_dir: str | Path, started_at: datetime, start: float, end: float
) -> Path:
    """Destination path for a detection-window clip (same naming as the monitor)."""
    clips_dir = Path(clips_dir).expanduser()
    return clips_dir / (
        f"clip_{started_at:%Y%m%d_%H%M%S}_{int(start * 1000)}_{int(end * 1000)}.wav"
    )


def write_clip(
    src_wav: str | Path, dst_wav: str | Path, start: float, end: float
) -> str | None:
    """Write [start, end) of src_wav to dst_wav. Returns dst path, or None if empty/missing."""
    if end <= start:
        return None

    try:
        import soundfile as sf
    except ImportError:
        return None

    src_wav = Path(src_wav).expanduser()
    if not src_wav.is_file():
        return None

    try:
        info = sf.info(str(src_wav))
        sr = info.samplerate
        start_frame = max(0, int(start * sr))
        end_frame = min(info.frames, int(end * sr))
        if end_frame <= start_frame:
            return None

        data, _ = sf.read(
            str(src_wav), start=start_frame, stop=end_frame, dtype="float32"
        )
        if data.size == 0:
            return None

        dst_wav = Path(dst_wav).expanduser()
        dst_wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(dst_wav), data, sr, format="WAV")
        return str(dst_wav)
    except (OSError, RuntimeError, ValueError):
        return None
