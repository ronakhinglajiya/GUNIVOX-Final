#!/usr/bin/env bash
# Linux/Ubuntu equivalent of start-all.bat.
# Starts backend (uvicorn) + frontend (next dev) in the foreground.
# For production / unattended deployment use systemd units instead — see
# deploy/systemd/ for templates.

set -euo pipefail

cd "$(dirname "$0")"

# Detect this host's primary LAN IPv4 (best-effort; falls back to localhost).
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "${LAN_IP:-}" ] && LAN_IP="localhost"

# Pick the right venv python regardless of which platform created the venv.
if [ -x "venv/bin/python" ]; then
    PY="$(pwd)/venv/bin/python"
elif [ -x "venv/Scripts/python.exe" ]; then
    PY="$(pwd)/venv/Scripts/python.exe"
else
    echo "no venv found at ./venv — run: python3 -m venv venv && venv/bin/pip install -r backend/requirements.txt" >&2
    exit 1
fi

echo "================================================================"
echo "  GUNIVOX :: starting backend + frontend"
echo "================================================================"
echo "  Backend  : http://localhost:8000   (also  http://${LAN_IP}:8000)"
echo "  Frontend : http://localhost:3000   (also  http://${LAN_IP}:3000)"
echo "  Swagger  : http://localhost:8000/docs"
echo ""
echo "  Other PCs on the LAN can open:  http://${LAN_IP}:3000"
echo "  Make sure the firewall allows ports 8000 and 3000."
echo ""

# Preserve a hand-configured tunnel URL across runs — only write a fresh
# .env.local if one doesn't already exist.
if [ ! -f "frontend/.env.local" ]; then
    echo "NEXT_PUBLIC_API_URL=http://${LAN_IP}:8000" > frontend/.env.local
fi

cleanup() {
    echo ""
    echo "shutting down…"
    kill ${BACK_PID:-} ${FRONT_PID:-} 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Backend
( cd backend && "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info ) &
BACK_PID=$!

# Give backend a moment to start its prewarm before frontend connects.
sleep 2

# Frontend (dev). For prod use `npm run start` after `npm run build`.
( cd frontend && npm run dev ) &
FRONT_PID=$!

echo "  backend pid=$BACK_PID  frontend pid=$FRONT_PID"
echo "  Ctrl-C to stop both."
echo "================================================================"

wait
