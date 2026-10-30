"""Pull the most recent few calls' transcripts and check whether the
opening AI turn is pitching BCA (per the new arts-campaign directive)."""
import sqlite3
from pathlib import Path

c = sqlite3.connect("data/gunivox.db")
print("=== Most recent 5 calls ===")
rows = c.execute("""
    SELECT id, lead_id, started_at, ended_at, duration_s, outcome, lead_temperature
    FROM calls
    ORDER BY id DESC
    LIMIT 5
""").fetchall()
for r in rows:
    print(f"  call={r[0]:5} lead={r[1]:5} started={r[2]} ended={r[3]} dur={r[4]} outcome={r[5]} temp={r[6]}")

print()
print("=== Opening turns of the most recent CALL (call.id desc) ===")
top_call = c.execute(
    "SELECT id, started_at FROM calls ORDER BY id DESC LIMIT 1"
).fetchone()
if not top_call:
    print("No calls yet.")
else:
    cid, started = top_call
    print(f"call_id={cid}  started_at={started}")
    print()
    for t in c.execute(
        "SELECT turn_index, role, text FROM turns "
        "WHERE call_id = ? ORDER BY turn_index LIMIT 8",
        (cid,),
    ).fetchall():
        idx, role, text = t
        text = (text or "").replace("\n", " ").strip()
        print(f"  [{idx:2}] {role:10}: {text[:200]}")

print()
print("=== Hunt: any recent assistant turn mentioning BCA vs the old list ===")
for hint, label in [
    ("BCA", "BCA mentions in recent assistant turns"),
    ("B.Sc IT", "B.Sc IT mentions (would suggest OLD flagship list)"),
    ("BBA", "BBA mentions (would suggest OLD flagship list)"),
]:
    n = c.execute(
        "SELECT COUNT(*) FROM turns "
        "WHERE role='assistant' AND text LIKE ? AND call_id IN ("
        "  SELECT id FROM calls ORDER BY id DESC LIMIT 5"
        ")",
        (f"%{hint}%",),
    ).fetchone()[0]
    print(f"  {n:3}  {label}")
