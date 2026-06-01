#!/usr/bin/env bash
# Pull a consistent snapshot of birdid.db from the Mac mini to this machine.
# (Wavs are separate — see pull_recordings_from_mini.sh.)
#
# Uses SQLite's online backup API on the mini (safe while monitor is writing).
# Default output: birdid.db.local-backup-YYYYMMDD-HHMMSS in the repo root.
#
# Usage:
#   ./scripts/pull_db_from_mini.sh
#   ./scripts/pull_db_from_mini.sh --activate    # also copy → ./birdid.db
#
# Override host/path:
#   BIRD_MINI_HOST=mac-mini BIRD_MINI_DIR=/Users/matt/bird-id ./scripts/pull_db_from_mini.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${BIRD_MINI_HOST:-mac-mini}"
REMOTE_DIR="${BIRD_MINI_DIR:-/Users/matt/bird-id}"
REMOTE_DB="${BIRD_MINI_DB:-birdid.db}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${BIRD_LOCAL_DB:-$ROOT/birdid.db.local-backup-$STAMP}"
ACTIVATE=0

for arg in "$@"; do
  case "$arg" in
    --activate|-a) ACTIVATE=1 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "pull_db_from_mini: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

REMOTE_TMP="/tmp/birdid-pull-$$.db"

cleanup_remote() {
  ssh "$HOST" "rm -f '$REMOTE_TMP'" 2>/dev/null || true
}
trap cleanup_remote EXIT

echo "pull_db_from_mini: $HOST:$REMOTE_DIR/$REMOTE_DB → $DEST"

ssh "$HOST" "cd '$REMOTE_DIR' && test -f '$REMOTE_DB' && sqlite3 '$REMOTE_DB' \".backup '$REMOTE_TMP'\""

scp -q "$HOST:$REMOTE_TMP" "$DEST"
cleanup_remote
trap - EXIT

BYTES="$(wc -c <"$DEST" | tr -d ' ')"
echo "pull_db_from_mini: ok ($BYTES bytes)"

if [[ "$ACTIVATE" -eq 1 ]]; then
  cp "$DEST" "$ROOT/birdid.db"
  echo "pull_db_from_mini: activated → $ROOT/birdid.db"
fi
