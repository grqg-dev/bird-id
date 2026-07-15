"""Tracked test fixtures — no BirdNET required."""

from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
BEWICKS_WREN_WAV = FIXTURE_DIR / "bewicks_wren.wav"


def test_bewicks_wren_fixture_exists():
    assert BEWICKS_WREN_WAV.is_file()
    assert BEWICKS_WREN_WAV.stat().st_size > 10_000


def test_fixture_readme_documents_wav():
    readme = FIXTURE_DIR / "README.md"
    assert readme.is_file()
    assert "bewicks_wren.wav" in readme.read_text()
