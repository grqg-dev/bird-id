#!/bin/bash
# Fetch origin/main; pull and deploy when the Mac mini is behind GitHub.
set -euo pipefail

BIRD_ID="${BIRD_ID:-$HOME/bird-id}"
LOG="$BIRD_ID/logs/deploy.log"
BRANCH="${BIRD_ID_BRANCH:-main}"

mkdir -p "$BIRD_ID/logs"
exec >>"$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="

cd "$BIRD_ID"

git fetch origin "$BRANCH"
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

if [[ "$LOCAL" == "$REMOTE" ]]; then
  echo "poll: already at $LOCAL"
  exit 0
fi

echo "poll: $LOCAL -> $REMOTE"

if git diff --quiet config.json 2>/dev/null; then
  git pull --ff-only origin "$BRANCH"
else
  git stash push -m "deploy: local config" -- config.json
  git pull --ff-only origin "$BRANCH"
  git stash pop || true
fi

"$BIRD_ID/deploy/mac-mini/deploy.sh"
