"""stream_pcm watchdog tests with a fake ffmpeg — no mic, no ffmpeg binary."""

from __future__ import annotations

import io
import os
import subprocess
import threading
import time

import numpy as np

import recorder

HOP_BYTES = 4800 * 2


class FakeProc:
    """Popen stand-in. stdout is a real pipe so reads genuinely block."""

    def __init__(self, hops_before_stall: int, then: str = "stall"):
        r, w = os.pipe()
        self.stdout = os.fdopen(r, "rb", buffering=0)
        self._w = w
        self.stderr = io.BytesIO(b"fake ffmpeg complaint\n")
        self.killed = False
        self._then = then
        threading.Thread(target=self._feed, args=(hops_before_stall,), daemon=True).start()

    def _feed(self, n: int) -> None:
        try:
            for _ in range(n):
                os.write(self._w, b"\x01\x00" * (HOP_BYTES // 2))
                time.sleep(0.005)
            if self._then == "exit":
                os.close(self._w)
                self._w = None
        except OSError:
            pass

    def kill(self) -> None:
        self.killed = True
        if self._w is not None:
            try:
                os.close(self._w)
            except OSError:
                pass
            self._w = None

    def wait(self, timeout=None) -> int:
        return 0


def test_stall_triggers_respawn_within_timeout(monkeypatch):
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)
    procs: list[FakeProc] = []

    def spawn(cmd):
        # first process delivers 3 hops then hangs; second delivers 2 more
        p = FakeProc(3 if not procs else 2)
        procs.append(p)
        return p

    gen = recorder.stream_pcm(
        stall_seconds=0.3, startup_grace_seconds=0.3, spawn=spawn
    )
    out = []
    t0 = time.monotonic()
    for item in gen:
        out.append(item)
        if len(out) >= 7:
            break
    elapsed = time.monotonic() - t0

    kinds = ["R" if isinstance(x, recorder.Respawn) else "H" for x in out]
    assert kinds[:4] == ["H", "H", "H", "R"]
    resp = out[3]
    assert resp.reason == "stall"
    assert procs[0].killed
    assert len(procs) == 2
    assert isinstance(out[4], np.ndarray)
    assert elapsed < 5  # respawn arrived ~stall_seconds after the last hop
    gen.close()


def test_exit_respawn_has_exit_reason(monkeypatch):
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)

    def spawn(cmd):
        return FakeProc(2, then="exit")

    gen = recorder.stream_pcm(stall_seconds=1.0, spawn=spawn)
    items = [next(gen) for _ in range(3)]
    assert isinstance(items[2], recorder.Respawn)
    assert items[2].reason == "exit"
    gen.close()


def test_stalls_do_not_count_as_quick_failures(monkeypatch):
    """A device that never delivers must keep respawning, not raise RecordingError."""
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)
    gen = recorder.stream_pcm(
        stall_seconds=0.05, startup_grace_seconds=0.05, spawn=lambda c: FakeProc(0)
    )
    items = [next(gen) for _ in range(8)]
    assert all(isinstance(i, recorder.Respawn) and i.reason == "stall" for i in items)
    gen.close()
