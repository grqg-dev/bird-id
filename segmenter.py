"""Pure audio segmenter: adaptive silence-gated chunking of a PCM hop stream.

No microphone access, no config imports. All thresholds are caller-passed
parameters so unit tests can drive this with synthetic numpy arrays.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterator, Iterable

import numpy as np

_INT16_MAX = 32768.0
_SILENCE_FLOOR_DBFS = -90.0  # clamp for log10(0) guard


@dataclass
class Segment:
    samples: np.ndarray   # float32, normalised -1..1
    sr: int
    started_at: datetime
    mean_dbfs: float
    max_dbfs: float


def _rms_dbfs(samples: np.ndarray) -> float:
    f = samples.astype(np.float32)
    rms = math.sqrt(float(np.mean(f * f)))
    if rms < 1e-8:
        return _SILENCE_FLOOR_DBFS
    return max(_SILENCE_FLOOR_DBFS, 20.0 * math.log10(rms / _INT16_MAX))


def _peak_dbfs(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples.astype(np.float32))))
    if peak < 1.0:
        return _SILENCE_FLOOR_DBFS
    return max(_SILENCE_FLOOR_DBFS, 20.0 * math.log10(peak / _INT16_MAX))


class MicDead(Exception):
    """Raised when input is stuck near silence — blocked mic or no device."""


def segment_stream(
    hops: Iterable,   # yields np.int16 chunks, or Respawn sentinels from recorder
    sr: int,
    stream_start: datetime,
    *,
    hop_ms: int = 100,
    seg_target_seconds: float = 8.0,
    seg_max_seconds: float = 12.0,
    floor_window_seconds: float = 30.0,
    activity_margin_db: float = 6.0,
    quiet_hold_ms: float = 300.0,
    tail_carry_seconds: float = 1.0,
    mic_dead_dbfs: float = -85.0,
    mic_dead_window_seconds: float = 60.0,
    drift_resync_seconds: float = 60.0,
    wall_now: Callable[[], datetime] | None = None,
) -> Iterator[Segment]:
    """Consume PCM hops and emit variable-length Segment objects.

    Raises MicDead if the stream is permanently silent (blocked permission).
    Handles Respawn sentinels from recorder.stream_pcm by flushing/discarding
    the current buffer and resetting timestamps.

    Timestamps come from an audio-sample clock (stream_start + samples/sr), which
    only equals wall-clock time while capture runs continuously. If the machine
    sleeps (or the audio pipeline stalls) without ffmpeg exiting, the sample clock
    freezes while wall time advances. When that drift exceeds drift_resync_seconds
    at flush time, _stream_start is re-anchored to wall time so subsequent segments
    are stamped correctly. ffmpeg restarts are still handled separately via Respawn.

    wall_now defaults to datetime.now; inject a fake clock in tests.
    """
    _wall_now = wall_now or datetime.now
    target_samples = int(sr * seg_target_seconds)
    max_samples = int(sr * seg_max_seconds)
    tail_samples = int(sr * tail_carry_seconds)
    quiet_hold_hops = max(1, int(quiet_hold_ms / hop_ms))
    warmup_hops = int(floor_window_seconds * 1000 / hop_ms)
    mic_dead_hops = int(mic_dead_window_seconds * 1000 / hop_ms)

    # Adaptive floor: EMA-min with fast-down / slow-up tracking
    floor_ema = _SILENCE_FLOOR_DBFS
    alpha_down = 0.08
    alpha_up = 0.002

    # Per-reset state
    _stream_start = stream_start
    _stream_samples = 0       # samples consumed from stream (excl. tail re-use)
    _seg_start_ss = 0         # stream_samples value at start of current buffer
    _buf: list[np.ndarray] = []
    _buf_len = 0
    _quiet_streak = 0
    _hop_count = 0
    _silent_streak = 0
    _tail: np.ndarray | None = None

    def _reset(wall_time: datetime) -> None:
        nonlocal _stream_start, _stream_samples, _seg_start_ss
        nonlocal _buf, _buf_len, _quiet_streak, _hop_count, _silent_streak
        nonlocal _tail, floor_ema
        _stream_start = wall_time
        _stream_samples = 0
        _seg_start_ss = 0
        _buf = []
        _buf_len = 0
        _quiet_streak = 0
        _hop_count = 0
        _silent_streak = 0
        _tail = None
        floor_ema = _SILENCE_FLOOR_DBFS

    def _flush() -> Segment | None:
        nonlocal _buf, _buf_len, _seg_start_ss, _tail, _quiet_streak, _stream_start
        if not _buf:
            return None
        all_s = np.concatenate(_buf)
        new_tail = all_s[-tail_samples:].copy() if len(all_s) > tail_samples else all_s.copy()

        # Re-anchor to wall clock if the sample clock has fallen behind (sleep/stall).
        # Restores the invariant _stream_start + _stream_samples/sr == now, so this
        # and future segments get correct wall time. Drift is one-directional (wall
        # only runs ahead of audio), so timestamps only ever jump forward.
        now = _wall_now()
        audio_elapsed = _stream_samples / sr
        wall_elapsed = (now - _stream_start).total_seconds()
        if wall_elapsed - audio_elapsed > drift_resync_seconds:
            _stream_start = now - timedelta(seconds=audio_elapsed)

        offset_secs = _seg_start_ss / sr
        started_at = _stream_start + timedelta(seconds=offset_secs)
        f32 = all_s.astype(np.float32) / _INT16_MAX

        seg = Segment(
            samples=f32,
            sr=sr,
            started_at=started_at,
            mean_dbfs=_rms_dbfs(all_s),
            max_dbfs=_peak_dbfs(all_s),
        )

        _tail = new_tail
        _buf = [new_tail]
        _buf_len = len(new_tail)
        _seg_start_ss = _stream_samples - len(new_tail)
        _quiet_streak = 0
        return seg

    for hop in hops:
        # Respawn sentinel — import check by attribute so segmenter stays mic-free
        if hasattr(hop, "wall_time") and not isinstance(hop, np.ndarray):
            _reset(hop.wall_time)
            continue

        # hop is np.int16 array
        _hop_count += 1
        _stream_samples += len(hop)

        rms = _rms_dbfs(hop)

        # Update adaptive floor (fast down, slow up)
        if rms < floor_ema:
            floor_ema = alpha_down * rms + (1 - alpha_down) * floor_ema
        else:
            floor_ema = alpha_up * rms + (1 - alpha_up) * floor_ema

        # Mic-dead detection
        if rms <= mic_dead_dbfs:
            _silent_streak += 1
        else:
            _silent_streak = 0
        if _silent_streak >= mic_dead_hops:
            raise MicDead(
                "Microphone appears permanently silent. "
                "Check System Settings → Privacy & Security → Microphone."
            )

        # Accumulate
        _buf.append(hop)
        _buf_len += len(hop)

        # Quiet-streak tracking
        threshold = floor_ema + activity_margin_db
        if rms <= threshold:
            _quiet_streak += 1
        else:
            _quiet_streak = 0

        warmed_up = _hop_count >= warmup_hops

        # Flush on hard max regardless of quiet state
        if _buf_len >= max_samples:
            seg = _flush()
            if seg is not None:
                yield seg
        # Flush on target + quiet hold after warmup
        elif warmed_up and _buf_len >= target_samples and _quiet_streak >= quiet_hold_hops:
            seg = _flush()
            if seg is not None:
                yield seg
