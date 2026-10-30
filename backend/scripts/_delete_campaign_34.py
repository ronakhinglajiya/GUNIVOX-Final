"""One-shot: surgically remove campaign 34 and the 248 broken-name leads
the last CSV upload created (`9.18348E+11` etc.).

Safe rules:
  • For each lead attached to campaign 34, only delete the LEAD itself if
    that lead is NOT also attached to any other campaign. Shared leads
    keep their row (and their other-campaign history); only their
    campaign-34 junction row is removed.
  • Exclusive-to-34 leads get fully scrubbed: their calls (and the turns
    that cascade), their lead_events, their callbacks, their campaign
    junctions, then the lead row itself.
  • Finally the campaign row is dropped.

Idempotent — re-running on a fresh DB will find 0 rows and exit clean.
"""
import sqlite3
from pathlib import Path

DB = Path("data/gunivox.db")
CAMPAIGN_ID = 34


def main() -> int:
    c = sqlite3.connect(DB)
    # Enable cascades on this connection so turns vanish when calls go.
    c.execute("PRAGMA foreign_keys = ON")
    cur = c.cursor()

    camp = cur.execute(
        "SELECT id, name, status FROM campaigns WHERE id = ?", (CAMPAIGN_ID,)
    ).fetchone()
    if not camp:
        print(f"Campaign {CAMPAIGN_ID} not found — nothing to do.")
        return 0
    print(f"Target campaign: id={camp[0]}  name={camp[1]!r}  status={camp[2]}")

    # 1. All lead_ids attached to campaign 34.
    lead_ids = [
        r[0] for r in cur.execute(
            "SELECT lead_id FROM campaign_leads WHERE campaign_id = ?", (CAMPAIGN_ID,)
        ).fetchall()
    ]
    print(f"Leads attached to campaign {CAMPAIGN_ID}: {len(lead_ids)}")

    # 2. Split into exclusive (only in 34) vs shared (also in others).
    if not lead_ids:
        exclusive_ids: list[int] = []
        shared_ids: list[int] = []
    else:
        # SQLite parameterise via expanding placeholder list.
        marks = ",".join("?" for _ in lead_ids)
        attach_counts = {
            row[0]: row[1] for row in cur.execute(
                f"SELECT lead_id, COUNT(*) FROM campaign_leads "
                f"WHERE lead_id IN ({marks}) GROUP BY lead_id",
                lead_ids,
            ).fetchall()
        }
        exclusive_ids = [lid for lid in lead_ids if attach_counts.get(lid, 0) <= 1]
        shared_ids = [lid for lid in lead_ids if attach_counts.get(lid, 0) > 1]

    print(f"  → exclusive to campaign 34 (full delete) : {len(exclusive_ids)}")
    print(f"  → shared with other campaigns (keep)     : {len(shared_ids)}")

    # 3. Full-scrub the exclusive leads.
    if exclusive_ids:
        marks = ",".join("?" for _ in exclusive_ids)

        # Calls — capture their ids so we can delete turns explicitly
        # in case the FK ON DELETE wasn't declared CASCADE in the schema.
        call_ids = [
            r[0] for r in cur.execute(
                f"SELECT id FROM calls WHERE lead_id IN ({marks})", exclusive_ids,
            ).fetchall()
        ]
        if call_ids:
            cmarks = ",".join("?" for _ in call_ids)
            n = cur.execute(
                f"DELETE FROM turns WHERE call_id IN ({cmarks})", call_ids,
            ).rowcount
            print(f"  deleted turns: {n}")
            n = cur.execute(
                f"DELETE FROM calls WHERE id IN ({cmarks})", call_ids,
            ).rowcount
            print(f"  deleted calls: {n}")

        n = cur.execute(
            f"DELETE FROM lead_events WHERE lead_id IN ({marks})", exclusive_ids,
        ).rowcount
        print(f"  deleted lead_events: {n}")

        n = cur.execute(
            f"DELETE FROM callbacks WHERE lead_id IN ({marks})", exclusive_ids,
        ).rowcount
        print(f"  deleted callbacks: {n}")

    # 4. Drop ALL campaign_leads rows for campaign 34 (shared + exclusive).
    n = cur.execute(
        "DELETE FROM campaign_leads WHERE campaign_id = ?", (CAMPAIGN_ID,),
    ).rowcount
    print(f"  deleted campaign_leads junctions: {n}")

    # 5. Now safe to drop the exclusive lead rows themselves.
    if exclusive_ids:
        marks = ",".join("?" for _ in exclusive_ids)
        n = cur.execute(
            f"DELETE FROM leads WHERE id IN ({marks})", exclusive_ids,
        ).rowcount
        print(f"  deleted leads: {n}")

    # 6. Finally the campaign itself.
    n = cur.execute(
        "DELETE FROM campaigns WHERE id = ?", (CAMPAIGN_ID,),
    ).rowcount
    print(f"  deleted campaign: {n}")

    c.commit()

    # Sanity-check totals after the run.
    print()
    print("=== After ===")
    print(f"  campaigns remaining: {cur.execute('SELECT COUNT(*) FROM campaigns').fetchone()[0]}")
    print(f"  leads remaining:     {cur.execute('SELECT COUNT(*) FROM leads').fetchone()[0]}")
    print(f"  calls remaining:     {cur.execute('SELECT COUNT(*) FROM calls').fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
