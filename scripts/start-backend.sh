#!/usr/bin/env bash
# Backend + telemetry simulator in one command.
#   ./scripts/start-backend.sh              plain stream
#   ./scripts/start-backend.sh --demo       scripted end-to-end scenario
#   NO_SIM=1 ./scripts/start-backend.sh     API only (real ESP32 hardware)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

if [[ ! -d .venv ]]; then
  echo "==> creating backend/.venv"
  python3 -m venv .venv
fi
echo "==> installing backend dependencies"
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

echo "==> API      http://$HOST:$PORT      (docs at /docs)"
./.venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" &
PIDS+=($!)

if [[ -z "${NO_SIM:-}" ]]; then
  # Give the control loop a moment to open the WebSocket endpoint.
  sleep 2
  echo "==> simulator streaming telemetry (Ctrl-C stops everything)"
  ./.venv/bin/python "$ROOT/simulator/mock_esp32.py" \
    --url "ws://$HOST:$PORT/ws/telemetry" \
    --api "http://$HOST:$PORT" \
    "$@" &
  PIDS+=($!)
fi

wait -n
