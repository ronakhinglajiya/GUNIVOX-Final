"""Confirm new routes registered + DB state after Phase 1."""
import sqlite3
from app.main import app

print("=== Phase 1 routes ===")
routes = sorted(
    r.path for r in app.routes
    if any(x in r.path for x in ["inbox", "lock", "unlock", "timeline", "extend", "/team"])
)
for r in routes:
    print(" ", r)

print()
print("=== DB state ===")
c = sqlite3.connect("data/gunivox.db")
print("  lead_events rows         :", c.execute("SELECT COUNT(*) FROM lead_events").fetchone()[0])
print("  leads with temperature   :", c.execute(
    "SELECT COUNT(*) FROM leads WHERE lead_temperature IS NOT NULL"
).fetchone()[0])
print("  leads currently locked   :", c.execute(
    "SELECT COUNT(*) FROM leads WHERE locked_by_user_id IS NOT NULL"
).fetchone()[0])
