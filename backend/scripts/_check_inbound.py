"""One-shot: scan the DB for any inbound-style call activity."""
import sqlite3

c = sqlite3.connect("data/gunivox.db")

print("=== Callbacks (scheduled return-calls) ===")
n = c.execute("SELECT COUNT(*) FROM callbacks").fetchone()[0]
print(f"  total: {n}")
print()

print("=== Calls with outcome='callback_scheduled' ===")
rows = c.execute(
    "SELECT id, lead_id, outcome, started_at, summary "
    "FROM calls WHERE outcome='callback_scheduled' ORDER BY id DESC LIMIT 10"
).fetchall()
print(f"  total: {len(rows)}")
for r in rows:
    print(" ", r)
print()

print("=== Audit-log actions ===")
rows = c.execute(
    "SELECT action, COUNT(*) FROM audit_log GROUP BY action ORDER BY COUNT(*) DESC"
).fetchall()
for r in rows:
    print(f"  {r[0]:30} {r[1]}")
print()

print("=== Calls without a campaign (ad-hoc / manual / inbound-style) ===")
n = c.execute("SELECT COUNT(*) FROM calls WHERE campaign_id IS NULL").fetchone()[0]
print(f"  no campaign attached: {n}")
print()

print("=== Calls without an assigned counsellor AND without a campaign ===")
rows = c.execute(
    "SELECT id, lead_id, vobiz_call_id, started_at, outcome, summary "
    "FROM calls WHERE campaign_id IS NULL AND assigned_counsellor_id IS NULL "
    "ORDER BY id DESC LIMIT 10"
).fetchall()
print(f"  total: {len(rows)}")
for r in rows:
    print(" ", r)
