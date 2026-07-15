#!/usr/bin/env bash
# Bootstrap a local bird-id dev checkout (Python 3.12 venv + test deps).
# Run from the repo root after cloning.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "dev-setup: python3.12 not found on PATH" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3.12 -m venv .venv
fi

PY="./.venv/bin/python"
"$PY" -m pip install -q -U pip
"$PY" -m pip install -q -r requirements.txt -r requirements-dev.txt

if [[ ! -f config.json ]]; then
  cp config.example.json config.json
  echo "dev-setup: created config.json from config.example.json"
fi

chmod +x scripts/*.sh 2>/dev/null || true

echo "dev-setup: ok"
echo "  Fast tests:  $PY -m pytest -q"
echo "  Fixture:     tests/fixtures/bewicks_wren.wav"
echo "  BirdNET:     ./scripts/smoke_identify.sh  (needs TensorFlow runtime)"
echo "  Dashboard:   $PY birdid.py dashboard --dev"
echo "  React /dash: cd dashboard-ui && npm ci && npm run dev"
