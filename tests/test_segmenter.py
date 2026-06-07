"""Unit tests for segmenter.py using synthetic PCM — no mic, no BirdNET."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

import segmenter
from segmenter import MicDead, Segment, segment_stream


SR = 48_000
HOP_MS = 100
HOP = SR * HOP_MS // 1000  # 4800 samples

_T0 = datetime(2026, 1, 1, 0, 0, 0)

# Small params so tests don't need many seconds of synthetic audio
_FAST_CFG = dict(
    hop_ms=HOP_MS,
    seg_target_seconds=1.0,
    seg_max_seconds=2.0,
    floor_window_seconds=0.5,   # only 5 hops of warmup
    activity_margin_db=6.0,
    quiet_hold_ms=200.0,        # 2 quiet hops
    tail_carry_seconds=0.2,
    mic_dead_dbfs=-85.0,
    mic_dead_window_seconds=0.5,  # 5 silent hops → mic dead
)


def _noise_hop(amplitude: int = 500) -> np.ndarray:
    """Reproducible pink-ish noise at given amplitude (int16)."""
    rng = np.random.default_rng(42)
    return rng.integers(-amplitude, amplitude, size=HOP, dtype=np.int16)


def _loud_hop(amplitude: int = 8000) -> np.ndarray:
    return _noise_hop(amplitude)


def _silent_hop() -> np.ndarray:
    return np.zeros(HOP, dtype=np.int16)


def _hops(n: int, amplitude: int = 500) -> list[np.ndarray]:
    return [_noise_hop(amplitude) for _ in range(n)]


# ── Test 1: steady moderate noise flushes near seg_target_seconds ────────────

def test_steady_noise_flushes_near_target():
    """After warmup, steady background noise should flush around target size."""
    # warmup = 5 hops (0.5s / 0.1s). Target = 1s = 10 hops. Max = 2s = 20 hops.
    # We need at least warmup + target hops to see a flush.
    hops = _hops(30, amplitude=500)  # 3 seconds of steady noise
    segs = list(segment_stream(hops, SR, _T0, **_FAST_CFG))
    assert len(segs) >= 1, "should produce at least one segment"
    for s in segs:
        assert isinstance(s, Segment)
        assert s.sr == SR
        assert len(s.samples) > 0
        # Duration should be roughly target..max (allowing tail carry rounding)
        dur = len(s.samples) / s.sr
        assert dur <= _FAST_CFG["seg_max_seconds"] + 0.5


# ── Test 2: mid-buffer burst → waits for quiet before flushing ───────────────

def test_burst_prevents_quiet_flush_before_max():
    """A mid-buffer loud burst should prevent a quiet-flush; flush comes at max."""
    # With target=1s and max=2s: warmup hops + target fill + burst should
    # suppress quiet-flush, forcing a hard-max flush instead.
    warmup = _hops(6, amplitude=200)      # below floor+margin → quiet
    target_fill = _hops(5, amplitude=200) # fills past warmup, nears target
    burst = [_loud_hop(16000)] * 10       # loud: resets quiet-streak past target
    quiet_tail = _hops(4, amplitude=200)  # quiet again (not enough for another flush)

    hops = warmup + target_fill + burst + quiet_tail
    segs = list(segment_stream(hops, SR, _T0, **_FAST_CFG))
    assert len(segs) >= 1
    # Hard-max flush at 2.0s — segment should be ~2s, not flushed at 1s
    first = segs[0]
    dur = len(first.samples) / first.sr
    # Should be at least ~1.8s (close to max, not an early 1s quiet-flush)
    assert dur >= 1.5, f"flush too early at {dur:.2f}s — burst should have suppressed quiet-flush"


# ── Test 3: sustained loud hits max + tail carry works ───────────────────────

def test_sustained_loud_hits_max_and_tail_carry():
    """Non-stop loud audio must flush at seg_max_seconds; tail carry non-empty."""
    loud = [_loud_hop(16000)] * 30  # 3s of loud — well past 2s max
    segs = list(segment_stream(loud, SR, _T0, **_FAST_CFG))
    assert len(segs) >= 1
    first = segs[0]
    dur = len(first.samples) / first.sr
    # Hard max is 2.0s; with 100ms hops allow one hop of slop
    assert dur <= _FAST_CFG["seg_max_seconds"] + HOP_MS / 1000 + 0.05
    # Tail carry: subsequent segment should start with overlap
    if len(segs) > 1:
        tail_s = _FAST_CFG["tail_carry_seconds"]
        # second segment begins with at least tail_carry samples
        assert len(segs[1].samples) / segs[1].sr >= tail_s * 0.9


# ── Test 4: all-zeros stream → MicDead ───────────────────────────────────────

def test_all_zeros_raises_mic_dead():
    """An all-zeros PCM stream should raise MicDead within the dead window."""
    silent = [_silent_hop()] * 20  # 2 seconds; dead window = 0.5s = 5 hops
    with pytest.raises(MicDead):
        list(segment_stream(silent, SR, _T0, **_FAST_CFG))


# ── Test 5: started_at is derived from stream_start, not wall clock ──────────

def test_started_at_from_stream_start():
    """Segment.started_at should equal stream_start + (elapsed samples / sr)."""
    hops = _hops(25, amplitude=500)
    segs = list(segment_stream(hops, SR, _T0, **_FAST_CFG))
    assert segs, "expected at least one segment"
    first = segs[0]
    # started_at should be at or after _T0 (never before stream started)
    assert first.started_at >= _T0
    # And bounded by end of stream (25 hops * 100ms = 2.5s)
    from datetime import timedelta
    assert first.started_at <= _T0 + timedelta(seconds=2.5)


# ── Test 6: Respawn sentinel resets state ────────────────────────────────────

def test_respawn_resets_state():
    """A Respawn sentinel should discard the current buffer and reset."""
    from recorder import Respawn

    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t1 = datetime(2026, 1, 1, 12, 1, 0)

    # loud hops before respawn (partial buffer), then fresh quiet after
    before = [_loud_hop(16000)] * 5
    after = _hops(25, amplitude=500)
    hops = before + [Respawn(t1)] + after

    segs = list(segment_stream(hops, SR, t0, **_FAST_CFG))
    # All segments should be stamped at or after t1 (pre-respawn buffer discarded)
    for s in segs:
        assert s.started_at >= t1, f"segment before respawn leaked: {s.started_at}"


# ── Test 7: dbfs helpers clamp at silence floor ──────────────────────────────

def test_rms_dbfs_zero_guard():
    zeros = np.zeros(HOP, dtype=np.int16)
    assert segmenter._rms_dbfs(zeros) == segmenter._SILENCE_FLOOR_DBFS


def test_peak_dbfs_zero_guard():
    zeros = np.zeros(HOP, dtype=np.int16)
    assert segmenter._peak_dbfs(zeros) == segmenter._SILENCE_FLOOR_DBFS
