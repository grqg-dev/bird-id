#!/bin/bash
# Apply the current checkout on the Mac mini: deps, app wrapper, launch agents, cleanup.
set -euo pipefail

BIRD_ID="${BIRD_ID:-$HOME/bird-id}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUIUID="$(id -u)"
VENV="$BIRD_ID/.venv/bin/python"
REQ="$BIRD_ID/requirements-intel-mac.txt"

cd "$BIRD_ID"
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
cp "$SCRIPT_DIR/com.birdid.monitor.plist" "$HOME/Library/LaunchAgents/"
cp "$SCRIPT_DIR/com.birdid.dashboard.plist" "$HOME/Library/LaunchAgents/"

PATH="$HOME/bin:$PATH" "$VENV" "$BIRD_ID/birdid.py" cleanup || true

# Dashboard: safe to bounce on every deploy.
if launchctl print "gui/$GUIUID/com.birdid.dashboard" &>/dev/null; then
  launchctl kickstart -k "gui/$GUIUID/com.birdid.dashboard"
else
  launchctl bootstrap "gui/$GUIUID" "$HOME/Library/LaunchAgents/com.birdid.dashboard.plist"
fi

# Monitor: only restart if the launch agent is already loaded (skip when running in Terminal).
if launchctl print "gui/$GUIUID/com.birdid.monitor" &>/dev/null; then
  launchctl kickstart -k "gui/$GUIUID/com.birdid.monitor"
fi

echo "deploy: ok ($(git rev-parse --short HEAD))"
