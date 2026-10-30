# Gunivox — Deployment & multi-PC access

A single host machine runs **backend** (FastAPI/uvicorn) + **frontend** (Next.js). Up to 5 other PCs on the same LAN open the frontend in their browser and everything works.

## One-time setup on the host machine

1. Python venv (already done) — verify `venv\Scripts\python.exe --version` works.
2. Node 18+ (we tested 22.14). `node --version` and `npm --version`.
3. Install backend deps if you skipped them: `venv\Scripts\python.exe -m pip install -r backend\requirements.txt`.
4. Install frontend deps: `cd frontend && npm install`.
5. Apply DB migrations: `cd backend && ..\venv\Scripts\python.exe -m alembic upgrade head`.
6. Seed admin + sample leads: `cd backend && ..\venv\Scripts\python.exe -m scripts.seed`.

## Day-to-day (dev mode)

```
start-all.bat
```

Opens two terminal windows. Frontend on http://localhost:3000, backend on :8000.

The script auto-detects your LAN IP and writes it into `frontend/.env.local` so other PCs can open `http://<LAN_IP>:3000` and the frontend will know to call the backend at `http://<LAN_IP>:8000`.

## Production mode (5-PC scale)

```
prod-build.bat      # bakes the LAN_IP into the frontend bundle
prod-start.bat      # serves the built frontend + runs uvicorn with --workers 2
```

The backend uses two uvicorn workers in production mode — comfortably handles 5 concurrent users plus background dialer activity.

## Windows Firewall

Open ports **8000** (API) and **3000** (UI) for inbound LAN connections:

```
netsh advfirewall firewall add rule name="Gunivox API"  dir=in action=allow protocol=TCP localport=8000
netsh advfirewall firewall add rule name="Gunivox UI"   dir=in action=allow protocol=TCP localport=3000
```

(Run those once as Administrator. Or use the Windows Defender Firewall GUI.)

## What each PC does

- **Host PC**: runs `prod-start.bat`. Acts as the always-on server.
- **Counsellor PCs (1–4)**: just open `http://<HOST_LAN_IP>:3000` in Chrome/Edge. No installs needed. Each user logs in with their own counsellor account; their JWT lives in browser localStorage.

To create more counsellor accounts: edit `backend/scripts/seed.py` (or add a `/api/users` admin endpoint in a later session).

## Public access (call-in from outside the LAN)

The Cloudflare tunnel (`api.gunivox.online`) already exposes the backend publicly — Vobiz uses it for webhooks. If you want the **frontend** also accessible publicly:

1. Create a second tunnel hostname, e.g. `app.gunivox.online` → `localhost:3000`
2. Add it via Zero Trust → your existing tunnel → Public Hostnames → Add.
3. Update `frontend/.env.production`: `NEXT_PUBLIC_API_URL=https://api.gunivox.online`
4. `prod-build.bat` + `prod-start.bat`.

## Switching to Postgres (for production scale beyond 5 PCs / persistent multi-host)

SQLite is fine for now. When you need it:
```
DATABASE_URL=postgresql+asyncpg://gunivox:gunivox@<host>:5432/gunivox
DATABASE_URL_SYNC=postgresql+psycopg://gunivox:gunivox@<host>:5432/gunivox
```
Then `alembic upgrade head` from the backend folder. Zero code changes.

## File map

- `start-all.bat` — dev mode (auto-reload on code changes)
- `prod-build.bat` — build frontend for production
- `prod-start.bat` — production servers with multi-worker uvicorn
- `stop-all.bat` — kill both servers and free the ports
- `scripts\dial.bat` — manual test call (works regardless of mode)

## Tunnel status

The Cloudflare tunnel runs as a Windows service and auto-starts. To check / restart:
```
sc query cloudflared
sc stop cloudflared
sc start cloudflared
```
