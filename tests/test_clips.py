"""Tests for clips.py."""

from __future__ import annotations

import shutil
import wave
from datetime import datetime
from pathlib import Path

import pytest

soundfile = pytest.importorskip("soundfile")

import clips  # noqa: E402


def _write_test_wav(path: Path, *, seconds: float = 6.0, sr: int = 48000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(sr * seconds))


def test_clip_dst_path_mp3(tmp_path):
    started = datetime(2026, 6, 5, 12, 0, 0)
    p = clips.clip_dst_path(tmp_path, started, 0.0, 3.0, fmt="mp3")
    assert p.suffix == ".mp3"
    assert "clip_20260605_120000_0_3000.mp3" == p.name


def test_write_clip_wav(tmp_path):
    src = tmp_path / "seg.wav"
    dst = tmp_path / "out.wav"
    _write_test_wav(src)
    path = clips.write_clip(src, dst, 0.0, 3.0, fmt="wav")
    assert path == str(dst)
    assert dst.is_file()
    assert dst.stat().st_size > 100


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_write_clip_mp3(tmp_path):
    src = tmp_path / "seg.wav"
    dst = tmp_path / "out.mp3"
    _write_test_wav(src)
    path = clips.write_clip(src, dst, 0.0, 3.0, fmt="mp3", mp3_bitrate="64k")
    assert path == str(dst)
    assert dst.is_file()
    assert dst.stat().st_size > 50
