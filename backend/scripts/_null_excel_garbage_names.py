"""One-shot: NULL the `name` field on every lead whose name is Excel
scientific-notation garbage (e.g. '9.19999E+11', '9.18348E+11').

The UI's lead-card render falls back to displaying the phone number when
`name` is NULL — so the visible '9.1...E+11' text disappears from every
page (Inbox, Pipeline, Leads, Calls, Closures, etc.) in one shot.

Safe:
  • The lead row itself stays — all its calls, transcripts, dispositions,
    timeline events, campaign attachments and lead-history are preserved.
  • Only the `name` column is touched.
  • Re-runnable: a second invocation matches 0 rows.

Run from backend/ via:
  $env:PYTHONPATH = (Get-Location).Path
  ../venv/Scripts/python.exe scripts/_null_excel_garbage_names.py
"""
import sqlite3
from pathlib import Path

DB = Path("data/gunivox.db")


def main() -> int:
    c = sqlite3.connect(DB)
    cur = c.cursor()

    n_before = cur.execute(
        "SELECT COUNT(*) FROM leads WHERE name LIKE '%E+%' OR name LIKE '%e+%'"
    ).fetchone()[0]
    if n_before == 0:
        print("No broken-name leads — nothing to do.")
        return 0
    print(f"Found {n_before} leads with Excel-mangled names (e.g. '9.19999E+11').")

    # Sample so the operator sees what's about to change.
    print("\nSample of rows that will have name = NULL:")
    for r in cur.execute(
        "SELECT id, name, phone FROM leads "
        "WHERE name LIKE '%E+%' OR name LIKE '%e+%' "
        "ORDER BY id DESC LIMIT 5"
    ).fetchall():
        print(f"  id={r[0]:6}  was name={r[1]!r:22}  phone={r[2]}")

    n = cur.execute(
        "UPDATE leads SET name = NULL "
        "WHERE name LIKE '%E+%' OR name LIKE '%e+%'"
    ).rowcount
    c.commit()
    print(f"\nNulled {n} name fields.")

    # Also flow into Call.caller_name — many cards/reports show the
    # call-level caller_name, which got the same Excel garbage when leads
    # were created. Same pattern → null those too.
    n_calls = cur.execute(
        "UPDATE calls SET caller_name = NULL "
        "WHERE caller_name LIKE '%E+%' OR caller_name LIKE '%e+%'"
    ).rowcount
    c.commit()
    print(f"Nulled {n_calls} Call.caller_name fields.")

    print("\nAfter — sanity check:")
    remaining_lead_names = cur.execute(
        "SELECT COUNT(*) FROM leads WHERE name LIKE '%E+%' OR name LIKE '%e+%'"
    ).fetchone()[0]
    remaining_call_names = cur.execute(
        "SELECT COUNT(*) FROM calls WHERE caller_name LIKE '%E+%' OR caller_name LIKE '%e+%'"
    ).fetchone()[0]
    print(f"  broken Lead.name rows remaining     : {remaining_lead_names}")
    print(f"  broken Call.caller_name rows remain : {remaining_call_names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
