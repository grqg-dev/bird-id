"""Audio recording for the bird-id prototype.

Records from the macOS microphone via ffmpeg/AVFoundation into a wav file in
the exact format BirdNET wants natively (48 kHz, mono, 16-bit PCM), so no
resampling is needed downstream.

This module knows nothing about identification — it just produces wav files.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# BirdNET runs on 48 kHz mono. Recording natively at this rate avoids resampling.
TARGET_SAMPLE_RATE = 48_000
TARGET_CHANNELS = 1
WAV_HEADER_BYTES = 44
BYTES_PER_SECOND = TARGET_SAMPLE_RATE * 2  # 16-bit mono PCM

# Below this mean volume (dBFS) we treat the capture as effectively silent.
# A blocked microphone on macOS yields a clean file full of zeros, which reads
# as roughly -91 dBFS for 16-bit audio.
SILENCE_DBFS_THRESHOLD = -70.0


class RecordingError(RuntimeError):
    """Raised when recording fails or produces unusable (silent) audio."""


@dataclass
class RecordingResult:
    path: Path
    seconds: float
    mean_volume_dbfs: float
    max_volume_dbfs: float


def _require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RecordingError(
            "ffmpeg not found on PATH. Install it with: brew install ffmpeg"
        )
    return ffmpeg


def list_input_devices() -> str:
    """Return ffmpeg's list of AVFoundation audio input devices (for debugging)."""
    ffmpeg = _require_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True,
        text=True,
    )
    # ffmpeg prints the device list to stderr and exits non-zero by design.
    return proc.stderr


def _measure_volume(path: Path) -> tuple[float, float]:
    """Return (mean_volume_dbfs, max_volume_dbfs) using ffmpeg's volumedetect."""
    ffmpeg = _require_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    mean = max_ = float("-inf")
    for line in proc.stderr.splitlines():
        if "mean_volume:" in line:
            mean = float(line.split("mean_volume:")[1].strip().split()[0])
        elif "max_volume:" in line:
            max_ = float(line.split("max_volume:")[1].strip().split()[0])
    return mean, max_


def expected_wav_bytes(seconds: float) -> int:
    """Approximate on-disk size of a finished segment wav."""
    return WAV_HEADER_BYTES + int(BYTES_PER_SECOND * seconds)


def _run_ffmpeg_record(
    cmd: list[str],
    out_path: Path,
    seconds: float,
    progress: Callable[[float], None] | None,
) -> subprocess.CompletedProcess:
    """Run ffmpeg and optionally report capture progress from the growing wav file."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stop = threading.Event()

    def poll() -> None:
        expected = expected_wav_bytes(seconds)
        while not stop.wait(0.25):
            try:
                size = out_path.stat().st_size if out_path.exists() else 0
            except OSError:
                size = 0
            progress(min(size / expected, 0.99))

    if progress:
        threading.Thread(target=poll, daemon=True).start()
    stdout, stderr = proc.communicate()
    if progress:
        stop.set()
        progress(1.0)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def record(
    seconds: float,
    out_path: str | Path,
    *,
    device: str = "0",
    check_silence: bool = True,
    progress: Callable[[float], None] | None = None,
) -> RecordingResult:
    """Record `seconds` of audio from the mic to `out_path` (a wav file).

    Args:
        seconds: Duration to record.
        out_path: Where to write the wav.
        device: AVFoundation audio device index (see list_input_devices()).
        check_silence: If True, raise RecordingError when the capture is silent
            (the classic symptom of missing macOS mic permission).
        progress: Optional callback invoked with capture fraction 0.0–1.0 while
            ffmpeg is running (estimated from the growing wav file size).

    Returns:
        RecordingResult with the path and measured volume levels.
    """
    ffmpeg = _require_ffmpeg()
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "avfoundation",
        "-i", f":{device}",          # ":N" = no video, audio device N
        "-t", str(seconds),
        "-ac", str(TARGET_CHANNELS),
        "-ar", str(TARGET_SAMPLE_RATE),
        "-y", str(out_path),
    ]
    proc = _run_ffmpeg_record(cmd, out_path, seconds, progress)
    if proc.returncode != 0:
        raise RecordingError(
            "ffmpeg failed to record. This is often a microphone-permission issue:\n"
            "  System Settings -> Privacy & Security -> Microphone -> enable your terminal app.\n"
            f"ffmpeg said:\n{proc.stderr.strip()}"
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RecordingError(f"Recording produced no output file at {out_path}.")

    mean_dbfs, max_dbfs = _measure_volume(out_path)
    if check_silence and mean_dbfs <= SILENCE_DBFS_THRESHOLD:
        raise RecordingError(
            f"Captured audio is effectively silent (mean {mean_dbfs:.1f} dBFS). "
            "The recording 'succeeded' but no sound was picked up — most likely the "
            "microphone permission is not granted to your terminal app "
            "(System Settings -> Privacy & Security -> Microphone)."
        )

    return RecordingResult(
        path=out_path,
        seconds=float(seconds),
        mean_volume_dbfs=mean_dbfs,
        max_volume_dbfs=max_dbfs,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Record audio from the mic to a wav file.")
    parser.add_argument("out", nargs="?", default="recordings/sample.wav", help="output wav path")
    parser.add_argument("-t", "--seconds", type=float, default=5.0, help="duration in seconds")
    parser.add_argument("-d", "--device", default="0", help="AVFoundation audio device index")
    parser.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    parser.add_argument("--allow-silence", action="store_true", help="don't fail on silent capture")
    args = parser.parse_args()

    if args.list_devices:
        print(list_input_devices())
        raise SystemExit(0)

    print(f"Recording {args.seconds}s from device {args.device} -> {args.out} ...")
    result = record(
        args.seconds, args.out, device=args.device, check_silence=not args.allow_silence
    )
    print(
        f"Saved {result.path} ({result.seconds:.1f}s, "
        f"mean {result.mean_volume_dbfs:.1f} dBFS, peak {result.max_volume_dbfs:.1f} dBFS)"
    )
