#!/usr/bin/env bash
# Production start: backend with multiple workers + frontend served from
# the pre-built Next.js output. Requires `cd frontend && npm run build` first.

set -euo pipefail

cd "$(dirname "$0")"

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "${LAN_IP:-}" ] && LAN_IP="localhost"

if [ -x "venv/bin/python" ]; then
    PY="$(pwd)/venv/bin/python"
elif [ -x "venv/Scripts/python.exe" ]; then
    PY="$(pwd)/venv/Scripts/python.exe"
else
    echo "no venv found at ./venv" >&2
    exit 1
fi

if [ ! -d "frontend/.next" ]; then
    echo "No production build found. Run: ( cd frontend && npm run build )"
    exit 1
fi

# Configurable knobs.
: "${WORKERS:=2}"        # uvicorn workers (1 worker per ~2-3 concurrent calls)
: "${BACKEND_PORT:=8000}"
: "${FRONTEND_PORT:=3000}"

echo "================================================================"
echo "  GUNIVOX :: PRODUCTION"
echo "================================================================"
echo "  Backend  : http://localhost:${BACKEND_PORT}  (LAN: http://${LAN_IP}:${BACKEND_PORT})"
echo "  Frontend : http://localhost:${FRONTEND_PORT} (LAN: http://${LAN_IP}:${FRONTEND_PORT})"
echo "  Workers  : ${WORKERS}"
echo ""

cleanup() {
    echo ""; echo "shutting down…"
    kill ${BACK_PID:-} ${FRONT_PID:-} 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

( cd backend && "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --workers "$WORKERS" --log-level info ) &
BACK_PID=$!

sleep 2

( cd frontend && PORT="$FRONTEND_PORT" npm run start ) &
FRONT_PID=$!

echo "  backend pid=$BACK_PID  frontend pid=$FRONT_PID"
echo "================================================================"

wait
