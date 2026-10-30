"""Audit: what each page SHOULD show vs what it does show."""
import sqlite3

c = sqlite3.connect("data/gunivox.db")

print("=== Source of truth (Lead table) ===")
print()
print("Lead.lead_temperature buckets:")
for row in c.execute(
    "SELECT lead_temperature, COUNT(*) FROM leads GROUP BY lead_temperature"
).fetchall():
    print(f"  {row[0] or 'NULL':10} {row[1]}")
print()

print("Lead.funnel_stage buckets:")
for row in c.execute(
    "SELECT funnel_stage, COUNT(*) FROM leads GROUP BY funnel_stage"
).fetchall():
    print(f"  {row[0]:14} {row[1]}")
print()

print("Lead.dnd = True:")
n = c.execute("SELECT COUNT(*) FROM leads WHERE dnd = 1").fetchone()[0]
print(f"  {n}")
print()

print("=== Cross-axis (what Pipeline SHOULD show after fix) ===")
print()
print("Hot/Warm/Cold (excluding closing stages + DND):")
for row in c.execute("""
    SELECT lead_temperature, COUNT(*) FROM leads
    WHERE dnd = 0
      AND funnel_stage NOT IN ('applied', 'docs_received', 'enrolled')
    GROUP BY lead_temperature
""").fetchall():
    print(f"  {row[0] or 'NULL':10} {row[1]}")
print()

closing_n = c.execute("""
    SELECT COUNT(*) FROM leads
    WHERE funnel_stage IN ('applied', 'docs_received', 'enrolled')
""").fetchone()[0]
print(f"Closing (applied/docs/enrolled): {closing_n}")
print()

print(f"Total leads: {c.execute('SELECT COUNT(*) FROM leads').fetchone()[0]}")
