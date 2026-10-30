"""One-shot: drop the Demo Counsellor row and any orphans pointing to it.

Run from backend/ via:  ../venv/Scripts/python.exe scripts/_delete_demo_counsellor.py
"""
import sqlite3
import sys

DB = "data/gunivox.db"
EMAIL = "counsellor@gunivox.online"


def main() -> int:
    c = sqlite3.connect(DB)
    c.execute("PRAGMA foreign_keys = ON")
    cur = c.cursor()

    row = cur.execute(
        "SELECT id, email, name, role FROM users WHERE email = ?", (EMAIL,)
    ).fetchone()
    if not row:
        print(f"No user with email {EMAIL!r} — already gone.")
        return 0

    uid, email, name, role = row
    print(f"Found: id={uid} email={email!r} name={name!r} role={role!r}")

    calls = cur.execute(
        "SELECT COUNT(*) FROM calls WHERE assigned_counsellor_id = ?", (uid,)
    ).fetchone()[0]
    callbacks = cur.execute(
        "SELECT COUNT(*) FROM callbacks WHERE counsellor_id = ?", (uid,)
    ).fetchone()[0]
    audits = cur.execute(
        "SELECT COUNT(*) FROM audit_log WHERE user_id = ?", (uid,)
    ).fetchone()[0]
    print(f"  calls assigned   : {calls}")
    print(f"  callbacks owned  : {callbacks}")
    print(f"  audit log rows   : {audits}")

    # Null out the FKs (the columns are all nullable already) and then delete
    # the user row. Safer than ON DELETE SET NULL which isn't configured.
    cur.execute(
        "UPDATE calls SET assigned_counsellor_id = NULL WHERE assigned_counsellor_id = ?",
        (uid,),
    )
    cur.execute(
        "UPDATE callbacks SET counsellor_id = NULL WHERE counsellor_id = ?",
        (uid,),
    )
    cur.execute("UPDATE audit_log SET user_id = NULL WHERE user_id = ?", (uid,))
    cur.execute("DELETE FROM users WHERE id = ?", (uid,))
    c.commit()
    print(f"Deleted user id={uid}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
