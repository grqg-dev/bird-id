"""deploy.sh behaviour with stubbed launchctl/pip, in a throwaway git repo."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LABELS = ("com.birdid.monitor", "com.birdid.dashboard")


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "bird-id"
    mini = repo / "deploy" / "mac-mini"
    mini.mkdir(parents=True)
    shutil.copy(ROOT / "deploy/mac-mini/deploy.sh", mini / "deploy.sh")
    for label in LABELS:
        (mini / f"{label}.plist").write_text(f"<plist>{label} v1</plist>")
    _exe(mini / "build-monitor-app.sh", "exit 0\n")
    _exe(repo / ".venv/bin/python", 'echo "python $*" >> "$CALLS"\n')
    _exe(repo / ".venv/bin/pip", "exit 0\n")
    home = tmp_path / "home"
    (home / "Library/LaunchAgents").mkdir(parents=True)
    state = tmp_path / "loaded"
    state.mkdir()
    calls = tmp_path / "calls.log"
    calls.touch()
    # fake launchctl: `print` succeeds only for labels with a state file
    _exe(
        tmp_path / "bin" / "launchctl",
        'echo "launchctl $*" >> "$CALLS"\n'
        'label="${2##*/}"\n'
        'case "$1" in\n'
        '  print) [[ -e "$STATE/$label" ]] ;;\n'
        '  bootout) rm -f "$STATE/$label" ;;\n'
        '  bootstrap) touch "$STATE/$(basename "$3" .plist)" ;;\n'
        "esac\n",
    )
    git = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "initial deploy fixture")
    e = {
        **os.environ,
        "BIRD_ID": str(repo),
        "HOME": str(home),
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "STATE": str(state),
    }
    return repo, home, state, calls, e


def _run(repo, e):
    return subprocess.run(
        ["bash", str(repo / "deploy/mac-mini/deploy.sh")], env=e, capture_output=True, text=True
    )


def test_refuses_dirty_checkout_without_side_effects(env):
    repo, home, state, calls, e = env
    (repo / "deploy/mac-mini/com.birdid.monitor.plist").write_text("edited")
    r = _run(repo, e)
    assert r.returncode == 1 and "uncommitted changes" in r.stderr
    assert calls.read_text() == ""  # nothing ran
    assert not (home / "Library/LaunchAgents/com.birdid.monitor.plist").exists()


def test_prints_commit_and_installs_clean(env):
    repo, home, state, calls, e = env
    r = _run(repo, e)
    assert r.returncode == 0, r.stderr
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert f"deploying {sha}" in r.stdout and f"deploy: ok ({sha})" in r.stdout
    assert (home / "Library/LaunchAgents/com.birdid.monitor.plist").exists()


def test_monitor_not_loaded_is_left_alone(env):
    repo, home, state, calls, e = env
    _run(repo, e)
    out = calls.read_text()
    assert "bootstrap" in out and "com.birdid.monitor" not in "".join(
        l for l in out.splitlines() if "kickstart" in l or "bootout" in l
    )


def test_loaded_monitor_with_changed_plist_is_bootout_bootstrap(env):
    repo, home, state, calls, e = env
    agents = home / "Library/LaunchAgents"
    (agents / "com.birdid.monitor.plist").write_text("<plist>old</plist>")  # differs from repo
    (agents / "com.birdid.dashboard.plist").write_text("<plist>com.birdid.dashboard v1</plist>")
    (state / "com.birdid.monitor").touch()
    (state / "com.birdid.dashboard").touch()
    r = _run(repo, e)
    assert r.returncode == 0, r.stderr
    lines = calls.read_text().splitlines()
    mon = [l for l in lines if "com.birdid.monitor" in l and "print" not in l]
    assert any("bootout" in l for l in mon) and any("bootstrap" in l for l in mon)
    assert not any("kickstart" in l for l in mon)
    assert [i for i, l in enumerate(mon) if "bootout" in l][0] < [i for i, l in enumerate(mon) if "bootstrap" in l][0]
    assert (agents / "com.birdid.monitor.plist").read_text() == "<plist>com.birdid.monitor v1</plist>"
    # unchanged dashboard plist still just kickstarts
    assert any("kickstart" in l and "dashboard" in l for l in lines)


def test_loaded_monitor_with_same_plist_is_kickstarted(env):
    repo, home, state, calls, e = env
    agents = home / "Library/LaunchAgents"
    for label in LABELS:
        shutil.copy(repo / "deploy/mac-mini" / f"{label}.plist", agents / f"{label}.plist")
        (state / label).touch()
    assert _run(repo, e).returncode == 0
    lines = calls.read_text().splitlines()
    assert any("kickstart" in l and "monitor" in l for l in lines)
    assert not any("bootout" in l for l in lines)
