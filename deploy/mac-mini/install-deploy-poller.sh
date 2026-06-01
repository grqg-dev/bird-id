#!/bin/bash
# One-time setup: enable git-pull deploy polling on the Mac mini.
set -euo pipefail

BIRD_ID="${BIRD_ID:-$HOME/bird-id}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GUIUID="$(id -u)"
PLIST="$HOME/Library/LaunchAgents/com.birdid.deploy.plist"

chmod +x "$SCRIPT_DIR/deploy.sh" "$SCRIPT_DIR/poll-deploy.sh"
mkdir -p "$BIRD_ID/logs"
cp "$SCRIPT_DIR/com.birdid.deploy.plist" "$PLIST"

launchctl bootout "gui/$GUIUID/com.birdid.deploy" 2>/dev/null || true
launchctl bootstrap "gui/$GUIUID" "$PLIST"

echo "Installed deploy poller (every 5 min + at login)."
echo "Log: $BIRD_ID/logs/deploy.log"
echo "Manual deploy: $SCRIPT_DIR/deploy.sh"
