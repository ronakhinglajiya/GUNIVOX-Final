"""Verify reset_failed_leads() actually works against the live DB.

Picks the smallest campaign that has failed leads, prints before/after
status counts, and confirms attempts + last_attempted_at were cleared.
Does NOT start the campaign — that's the endpoint's job, not the helper's.
"""
import asyncio
import sqlite3
from sqlalchemy import select
from app.db import models
from app.db.session import SessionLocal
from app.db.campaigns_repo import reset_failed_leads, campaign_progress


def _status_breakdown(cid: int | None) -> dict:
    c = sqlite3.connect("data/gunivox.db")
    q = (
        "SELECT status, COUNT(*) FROM campaign_leads "
        + (f"WHERE campaign_id = {cid} " if cid else "")
        + "GROUP BY status"
    )
    return {row[0]: row[1] for row in c.execute(q)}


async def main() -> None:
    # Find the smallest campaign that has failed leads so we don't reset
    # thousands of rows in a smoke test.
    c = sqlite3.connect("data/gunivox.db")
    row = c.execute(
        "SELECT campaign_id, COUNT(*) FROM campaign_leads "
        "WHERE status IN ('failed', 'failed_retryable') "
        "GROUP BY campaign_id ORDER BY COUNT(*) ASC LIMIT 1"
    ).fetchone()
    if not row:
        print("No campaigns have failed leads — nothing to test.")
        return
    cid, fail_count = row
    print(f"Targeting campaign id={cid} ({fail_count} failed leads)\n")

    print("BEFORE — status counts (this campaign):")
    for s, n in sorted(_status_breakdown(cid).items()):
        print(f"  {s:18} {n}")

    # Sample one lead from this campaign to confirm Lead.last_attempted_at
    # is non-null before, and null after.
    sample_lead_id = c.execute(
        "SELECT lead_id FROM campaign_leads "
        f"WHERE campaign_id = {cid} AND status IN ('failed','failed_retryable') "
        "LIMIT 1"
    ).fetchone()[0]
    before_lat = c.execute(
        f"SELECT last_attempted_at FROM leads WHERE id = {sample_lead_id}"
    ).fetchone()[0]
    print(f"\nSample lead id={sample_lead_id} last_attempted_at BEFORE: {before_lat!r}")

    # Run the reset.
    async with SessionLocal() as db:
        result = await reset_failed_leads(db, campaign_id=cid)
        await db.commit()
        print(f"\nreset_failed_leads(campaign_id={cid}) → {result}")

    print("\nAFTER — status counts (this campaign):")
    after = _status_breakdown(cid)
    for s, n in sorted(after.items()):
        print(f"  {s:18} {n}")

    after_lat = c.execute(
        f"SELECT last_attempted_at FROM leads WHERE id = {sample_lead_id}"
    ).fetchone()[0]
    print(f"\nSample lead id={sample_lead_id} last_attempted_at AFTER:  {after_lat!r}")

    # Sanity: should be exactly fail_count moved from failed*/failed_retryable to queued.
    expected_queued_delta = fail_count
    assert after.get("failed", 0) == 0 and after.get("failed_retryable", 0) == 0, (
        "Expected 0 failed rows after reset"
    )
    assert after_lat is None, "Expected last_attempted_at to be NULL after reset"
    print(
        f"\nPASS · {expected_queued_delta} leads moved from failed* → queued, "
        "attempts=0, last_attempted_at=NULL."
    )


asyncio.run(main())
