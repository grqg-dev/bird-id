#!/usr/bin/env bash
# Optional integration smoke: run BirdNET identify against the tracked fixture.
# Requires the full runtime venv (requirements.txt / TensorFlow). Not run in CI.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/bewicks_wren.wav"
PY="$ROOT/.venv/bin/python"
MIN_CONF="${SMOKE_MIN_CONF:-0.1}"

if [[ ! -x "$PY" ]]; then
  echo "smoke_identify: missing venv at $ROOT/.venv" >&2
  exit 1
fi
if [[ ! -f "$FIXTURE" ]]; then
  echo "smoke_identify: missing fixture $FIXTURE" >&2
  exit 1
fi

echo "smoke_identify: $FIXTURE (min_conf=$MIN_CONF)"
"$PY" "$ROOT/birdid.py" identify "$FIXTURE" -c "$MIN_CONF"
