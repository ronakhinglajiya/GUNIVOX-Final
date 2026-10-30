# Gunivox systemd deployment (Ubuntu)

Auto-restarting unit files for the backend + frontend. After every code change
the services restart on their own — no manual `nohup` or `screen` needed.

## One-time install

```bash
# As root, after cloning the repo to /opt/gunivox and creating a 'gunivox' user
sudo useradd -r -s /bin/false -d /opt/gunivox gunivox || true
sudo chown -R gunivox:gunivox /opt/gunivox

# Backend deps (creates /opt/gunivox/venv)
sudo -u gunivox python3 -m venv /opt/gunivox/venv
sudo -u gunivox /opt/gunivox/venv/bin/pip install -r /opt/gunivox/backend/requirements.txt

# Frontend build (one-off; rerun after each frontend change)
sudo -u gunivox -- bash -c 'cd /opt/gunivox/frontend && npm ci && npm run build'

# Install + enable the systemd units
sudo cp /opt/gunivox/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gunivox-backend gunivox-frontend
```

## Day to day

```bash
# Logs (Ctrl-C to exit)
journalctl -u gunivox-backend -f
journalctl -u gunivox-frontend -f

# Restart after pulling new code
sudo systemctl restart gunivox-backend gunivox-frontend

# Status
systemctl status gunivox-backend
```

## Notes

- Backend runs uvicorn with `--workers 2`. Each worker holds its own
  in-memory caches (RAG index, filler audio) — bump workers for higher
  concurrency, but each worker uses ~400-500MB. 2 workers = ~5 concurrent calls.
- Frontend assumes you've already run `npm run build` once. Restart the
  service whenever you re-build.
- `.env` lives at `/opt/gunivox/.env`. Edit it and `systemctl restart
  gunivox-backend` to pick up new values.
- If the backend crashes, systemd restarts it after 3 seconds. If it crashes
  10 times in 60 seconds it gives up — `systemctl reset-failed
  gunivox-backend` to force a retry.
