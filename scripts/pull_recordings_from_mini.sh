#!/usr/bin/env bash
# Pull segment wavs from the Mac mini for local testing (identify replays).
#
# Note: prod currently drops segment wavs after clips are written. If no
# seg_*.wav files exist on the mini, use pull_clips_from_mini.sh instead.
#
# Default: 30 newest seg_*.wav files (~hundreds of MB, not the full dir).
#
# Usage:
#   ./scripts/pull_recordings_from_mini.sh
#   ./scripts/pull_recordings_from_mini.sh --recent 50
#   ./scripts/pull_recordings_from_mini.sh --all
#   ./scripts/pull_recordings_from_mini.sh seg_20260601_131822.wav
#
# For dashboard playback (clip MP3s):
#   ./scripts/pull_clips_from_mini.sh
#
# Env: BIRD_MINI_HOST, BIRD_MINI_DIR (same as pull_db_from_mini.sh)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${BIRD_MINI_HOST:-mac-mini}"
REMOTE_DIR="${BIRD_MINI_DIR:-/Users/matt/bird-id}"
REMOTE_REC="${BIRD_MINI_RECORDINGS:-recordings}"
LOCAL_REC="${BIRD_LOCAL_RECORDINGS:-$ROOT/recordings}"
MODE="recent"
RECENT=30
FILES=()

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recent)
      MODE="recent"
      RECENT="${2:?--recent needs a count}"
      shift 2
      ;;
    --all|-a)
      MODE="all"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "pull_recordings_from_mini: unknown option: $1" >&2
      exit 2
      ;;
    *)
      MODE="files"
      FILES+=("$1")
      shift
      ;;
  esac
done

mkdir -p "$LOCAL_REC"

if [[ "$MODE" == "recent" ]]; then
  FILES=()
  while IFS= read -r line; do
    [[ -n "$line" ]] && FILES+=("$line")
  done < <(
    ssh "$HOST" "cd '$REMOTE_DIR/$REMOTE_REC' && ls -t seg_*.wav 2>/dev/null | head -n $RECENT"
  )
  if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "pull_recordings_from_mini: no seg_*.wav on $HOST:$REMOTE_DIR/$REMOTE_REC" >&2
    echo "pull_recordings_from_mini: prod likely keeps clip MP3s only — try ./scripts/pull_clips_from_mini.sh" >&2
    exit 1
  fi
fi

if [[ "$MODE" == "all" ]]; then
  echo "pull_recordings_from_mini: rsync all seg_*.wav → $LOCAL_REC"
  rsync -avz --progress \
    --include='seg_*.wav' \
    --exclude='*' \
    "$HOST:$REMOTE_DIR/$REMOTE_REC/" \
    "$LOCAL_REC/"
  exit 0
fi

LIST="$(mktemp)"
printf '%s\n' "${FILES[@]}" >"$LIST"
COUNT="${#FILES[@]}"

echo "pull_recordings_from_mini: $COUNT file(s) from $HOST:$REMOTE_DIR/$REMOTE_REC → $LOCAL_REC"

rsync -avz --progress --files-from="$LIST" \
  "$HOST:$REMOTE_DIR/$REMOTE_REC/" \
  "$LOCAL_REC/"

rm -f "$LIST"

echo "pull_recordings_from_mini: ok ($(ls -1 "$LOCAL_REC"/seg_*.wav 2>/dev/null | wc -l | tr -d ' ') seg wavs local)"
