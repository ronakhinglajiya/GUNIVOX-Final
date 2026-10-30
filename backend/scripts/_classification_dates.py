"""List which IST dates have any temperature_set events (most recent 14)."""
import sqlite3

c = sqlite3.connect("data/gunivox.db")
print("Days with classification activity (IST date):")
rows = c.execute("""
    SELECT DATE(datetime(created_at, '+5 hours', '30 minutes')) AS d,
           COUNT(*) AS n
    FROM lead_events
    WHERE event_type = 'temperature_set'
    GROUP BY d
    ORDER BY d DESC
    LIMIT 14
""").fetchall()
for d, n in rows:
    print(f"  {d}  {n} classification(s)")
