#!/bin/bash
# Apply the current checkout on the Mac mini: deps, app wrapper, launch agents, cleanup.
# Refuses to run on a dirty checkout so what is deployed is always a real commit.
set -euo pipefail

BIRD_ID="${BIRD_ID:-$HOME/bird-id}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUIUID="$(id -u)"
VENV="$BIRD_ID/.venv/bin/python"
REQ="$BIRD_ID/requirements-intel-mac.txt"
AGENTS_DIR="$HOME/Library/LaunchAgents"

cd "$BIRD_ID"

# Refuse before touching anything: tracked files must match HEAD.
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "deploy: refusing to run, the checkout has uncommitted changes:" >&2
  git status --short --untracked-files=no >&2
  echo "deploy: commit, stash or discard them first." >&2
  exit 1
fi
COMMIT="$(git rev-parse --short HEAD)"
echo "deploy: deploying $COMMIT — $(git log -1 --format=%s)"

mkdir -p logs recordings

if [[ ! -x "$VENV" ]]; then
  echo "deploy: missing venv at $BIRD_ID/.venv — create it first." >&2
  exit 1
fi

# Intel Mac mini uses the pinned requirements file.
if [[ -f "$REQ" ]]; then
  "$BIRD_ID/.venv/bin/pip" install -q -r "$REQ"
fi

"$SCRIPT_DIR/build-monitor-app.sh"

mkdir -p "$AGENTS_DIR"

# Did the repo's plist change from the installed one? `kickstart` restarts the
# job but does NOT re-read the plist, so a changed plist needs bootout + bootstrap.
plist_changed() { # label
  ! cmp -s "$SCRIPT_DIR/$1.plist" "$AGENTS_DIR/$1.plist"
}
MONITOR_CHANGED=0; plist_changed com.birdid.monitor && MONITOR_CHANGED=1
DASHBOARD_CHANGED=0; plist_changed com.birdid.dashboard && DASHBOARD_CHANGED=1

loaded() { launchctl print "gui/$GUIUID/$1" &>/dev/null; }

# Re-read a changed plist: bootout, wait for the job to go away, install, bootstrap.
reload_agent() { # label
  local label="$1" i
  launchctl bootout "gui/$GUIUID/$label" || true
  for i in $(seq 1 20); do
    loaded "$label" || break
    sleep 0.5
  done
  cp "$SCRIPT_DIR/$label.plist" "$AGENTS_DIR/"
  launchctl bootstrap "gui/$GUIUID" "$AGENTS_DIR/$label.plist"
}

# Monitor: only touch it if the launch agent is already loaded (skip when running in Terminal).
MONITOR_LOADED=0; loaded com.birdid.monitor && MONITOR_LOADED=1
DASHBOARD_LOADED=0; loaded com.birdid.dashboard && DASHBOARD_LOADED=1

# Install plists that are not being reloaded below.
[[ $MONITOR_LOADED == 1 && $MONITOR_CHANGED == 1 ]] || cp "$SCRIPT_DIR/com.birdid.monitor.plist" "$AGENTS_DIR/"
[[ $DASHBOARD_LOADED == 1 && $DASHBOARD_CHANGED == 1 ]] || cp "$SCRIPT_DIR/com.birdid.dashboard.plist" "$AGENTS_DIR/"

PATH="$HOME/bin:$PATH" "$VENV" "$BIRD_ID/birdid.py" cleanup || true

# Dashboard: safe to bounce on every deploy.
if [[ $DASHBOARD_LOADED == 1 ]]; then
  if [[ $DASHBOARD_CHANGED == 1 ]]; then reload_agent com.birdid.dashboard
  else launchctl kickstart -k "gui/$GUIUID/com.birdid.dashboard"; fi
else
  launchctl bootstrap "gui/$GUIUID" "$AGENTS_DIR/com.birdid.dashboard.plist"
fi

if [[ $MONITOR_LOADED == 1 ]]; then
  if [[ $MONITOR_CHANGED == 1 ]]; then
    echo "deploy: monitor plist changed, reloading with bootout + bootstrap"
    reload_agent com.birdid.monitor
  else
    launchctl kickstart -k "gui/$GUIUID/com.birdid.monitor"
  fi
fi

echo "deploy: ok ($COMMIT)"
