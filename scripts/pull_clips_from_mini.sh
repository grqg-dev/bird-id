#!/usr/bin/env bash
# Pull track clip MP3s from the Mac mini for local dashboard playback testing.
#
# Prod drops full segment wavs after clips are written; clips are what remain.
#
# Usage:
#   ./scripts/pull_clips_from_mini.sh                  # 30 newest clips
#   ./scripts/pull_clips_from_mini.sh --recent 50
#   ./scripts/pull_clips_from_mini.sh clip_20260714_214253_0_3000.mp3
#
# After pulling clips, also pull a DB snapshot and activate it so clip_path rows
# resolve locally:
#   ./scripts/pull_db_from_mini.sh --activate
#
# Env: BIRD_MINI_HOST, BIRD_MINI_DIR (same as pull_db_from_mini.sh)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${BIRD_MINI_HOST:-mac-mini}"
REMOTE_DIR="${BIRD_MINI_DIR:-/Users/matt/bird-id}"
REMOTE_CLIPS="${BIRD_MINI_CLIPS:-recordings/clips}"
LOCAL_CLIPS="${BIRD_LOCAL_CLIPS:-$ROOT/recordings/clips}"
MODE="recent"
RECENT=30
FILES=()

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recent)
      MODE="recent"
      RECENT="${2:?--recent needs a count}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "pull_clips_from_mini: unknown option: $1" >&2
      exit 2
      ;;
    *)
      MODE="files"
      FILES+=("$1")
      shift
      ;;
  esac
done

mkdir -p "$LOCAL_CLIPS"

if [[ "$MODE" == "recent" ]]; then
  FILES=()
  while IFS= read -r line; do
    [[ -n "$line" ]] && FILES+=("$line")
  done < <(
    ssh "$HOST" "cd '$REMOTE_DIR/$REMOTE_CLIPS' && ls -t clip_*.mp3 2>/dev/null | head -n $RECENT"
  )
  if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "pull_clips_from_mini: no clip_*.mp3 on $HOST:$REMOTE_DIR/$REMOTE_CLIPS" >&2
    exit 1
  fi
fi

LIST="$(mktemp)"
printf '%s\n' "${FILES[@]}" >"$LIST"
COUNT="${#FILES[@]}"

echo "pull_clips_from_mini: $COUNT clip(s) from $HOST:$REMOTE_DIR/$REMOTE_CLIPS → $LOCAL_CLIPS"

rsync -avz --progress --files-from="$LIST" \
  "$HOST:$REMOTE_DIR/$REMOTE_CLIPS/" \
  "$LOCAL_CLIPS/"

rm -f "$LIST"

echo "pull_clips_from_mini: ok ($(ls -1 "$LOCAL_CLIPS"/clip_*.mp3 2>/dev/null | wc -l | tr -d ' ') clips local)"
