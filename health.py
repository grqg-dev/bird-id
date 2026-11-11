"""Capture-health accounting for the monitor.

Pure bookkeeping: tallies audio seconds, stalls, respawns, level and dead
(digital-silence) time per wall-clock hour as hops pass through the capture
thread. No mic, no DB — the monitor's main thread drains completed hours and
hands them to `storage.upsert_capture_health`.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Iterator

import numpy as np

_INT16_MAX = 32768.0
_FLOOR_DBFS = -90.0


def hour_key(when: datetime) -> str:
    """Hour bucket label, e.g. '2026-11-11T14:00:00' (sorts like segments.started_at)."""
    return when.replace(minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


@dataclass
class HourStats:
    hour: str
    audio_seconds: float = 0.0
    stalls: int = 0
    respawns: int = 0
    dead_seconds: float = 0.0
    dbfs_sum: float = 0.0     # sum of per-hop dBFS (mean = dbfs_sum / hops)
    hops: int = 0

    @property
    def mean_dbfs(self) -> float | None:
        return self.dbfs_sum / self.hops if self.hops else None


def hop_dbfs(hop: np.ndarray) -> float:
    f = hop.astype(np.float32)
    rms = math.sqrt(float(np.mean(f * f)))
    if rms < 1e-8:
        return _FLOOR_DBFS
    return max(_FLOOR_DBFS, 20.0 * math.log10(rms / _INT16_MAX))


class CaptureHealth:
    """Thread-safe per-hour tally; written by the capture thread, drained by main."""

    def __init__(
        self,
        sr: int,
        *,
        mic_dead_dbfs: float = -85.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sr = sr
        self._dead = mic_dead_dbfs
        self._now = now or datetime.now
        self._lock = threading.Lock()
        self._hours: dict[str, HourStats] = {}

    def _bucket(self) -> HourStats:
        key = hour_key(self._now())
        st = self._hours.get(key)
        if st is None:
            st = self._hours[key] = HourStats(hour=key)
        return st

    def observe_hop(self, hop: np.ndarray) -> None:
        secs = len(hop) / self._sr
        db = hop_dbfs(hop)
        with self._lock:
            st = self._bucket()
            st.audio_seconds += secs
            st.dbfs_sum += db
            st.hops += 1
            if db <= self._dead:
                st.dead_seconds += secs

    def observe_respawn(self, reason: str) -> None:
        with self._lock:
            st = self._bucket()
            st.respawns += 1
            if reason == "stall":
                st.stalls += 1

    def tap(self, hops: Iterable) -> Iterator:
        """Pass-through generator that tallies hops and Respawn sentinels."""
        for hop in hops:
            if isinstance(hop, np.ndarray):
                self.observe_hop(hop)
            elif hasattr(hop, "wall_time"):
                self.observe_respawn(getattr(hop, "reason", "exit"))
            yield hop

    def drain(self, *, include_current: bool = False) -> list[HourStats]:
        """Remove and return finished hours (and the current one if asked), oldest first."""
        current = hour_key(self._now())
        with self._lock:
            keys = sorted(
                k for k in self._hours if include_current or k < current
            )
            return [self._hours.pop(k) for k in keys]


def format_line(st: HourStats, hour_seconds: float = 3600.0) -> str:
    """'[health] 14:00 audio 3540s/3600s, stalls 2, mean -47 dBFS'."""
    hhmm = st.hour[11:16]
    mean = st.mean_dbfs
    mean_txt = f"{mean:.0f} dBFS" if mean is not None else "n/a"
    return (
        f"[health] {hhmm} audio {st.audio_seconds:.0f}s/{hour_seconds:.0f}s, "
        f"stalls {st.stalls}, mean {mean_txt}"
    )
