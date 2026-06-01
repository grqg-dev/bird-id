#!/bin/bash
# Install BirdID Monitor.app + launch agent on the Mac mini.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIRD_ID="${BIRD_ID:-$HOME/bird-id}"
GUIUID="$(id -u)"
PLIST="$HOME/Library/LaunchAgents/com.birdid.monitor.plist"

"$SCRIPT_DIR/build-monitor-app.sh"
mkdir -p "$BIRD_ID/logs"
cp "$SCRIPT_DIR/com.birdid.monitor.plist" "$PLIST"

# Stop old python-based launch agent if loaded; do not start yet (avoid clashing with a
# manual Terminal monitor or before mic permission is granted to the app).
launchctl bootout "gui/$GUIUID/com.birdid.monitor" 2>/dev/null || true

echo "Installed $PLIST (not started yet)."
echo "1. Grant mic: open \"$HOME/Applications/BirdID Monitor.app\" once via Screen Sharing."
echo "2. Stop your Terminal monitor (Ctrl-C)."
echo "3. Start launch agent: launchctl bootstrap gui/$GUIUID \"$PLIST\""
