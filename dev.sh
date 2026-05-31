#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── check venv ──────────────────────────────────────────────────────────────
if [ ! -f "$ROOT/venv/bin/activate" ]; then
  echo "venv not found. setting it up..."
  python3 -m venv "$ROOT/venv"
  source "$ROOT/venv/bin/activate"
  pip install -q -r "$ROOT/backend/requirements.txt"
else
  source "$ROOT/venv/bin/activate"
fi

# ── check .env ───────────────────────────────────────────────────────────────
if [ -f "$ROOT/.env" ]; then
  ENV_FILE="$ROOT/.env"
elif [ -f "$ROOT/backend/.env" ]; then
  ENV_FILE="$ROOT/backend/.env"
else
  echo "⚠  no .env file found (checked .env and backend/.env) — fill one in before running."
  exit 1
fi

# ── start backend ────────────────────────────────────────────────────────────
echo "starting backend on http://localhost:5000 ..."
cd "$ROOT/backend"
python app.py &
BACKEND_PID=$!

# ── start frontend ───────────────────────────────────────────────────────────
echo "starting frontend on http://localhost:3000 ..."
cd "$ROOT/frontend"
python3 -m http.server 3000 --bind 127.0.0.1 > /dev/null 2>&1 &
FRONTEND_PID=$!

# ── open browser (after a short pause for servers to start) ─────────────────
sleep 1
open "http://localhost:3000" 2>/dev/null || true

echo ""
echo "margin is running → http://localhost:3000"
echo "press Ctrl+C to stop."
echo ""

# ── wait and clean up on exit ────────────────────────────────────────────────
cleanup() {
  echo ""
  echo "shutting down..."
  kill "$BACKEND_PID"  2>/dev/null || true
  kill "$FRONTEND_PID" 2>/dev/null || true
  exit 0
}

trap cleanup INT TERM
wait
