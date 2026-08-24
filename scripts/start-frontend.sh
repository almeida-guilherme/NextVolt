#!/usr/bin/env bash
# Dashboard dev server. Proxies /api and /ws to the backend, so no CORS setup.
#   ./scripts/start-frontend.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"

if [[ ! -d node_modules ]]; then
  echo "==> installing frontend dependencies"
  npm install --no-audit --no-fund
fi

export VITE_PROXY_TARGET="${VITE_PROXY_TARGET:-http://127.0.0.1:8000}"
echo "==> dashboard http://localhost:5173  (API proxied to $VITE_PROXY_TARGET)"
npm run dev
