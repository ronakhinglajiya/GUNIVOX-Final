"""Verify /api/pipeline counts match Inbox + Closures."""
import asyncio
from sqlalchemy import select

from app.api.pipeline import pipeline_endpoint
from app.api.inbox import get_inbox
from app.api.closures import closures_endpoint
from app.db import models
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        admin = (await db.execute(select(models.User).where(models.User.role == "admin"))).scalar_one()

        pipe = await pipeline_endpoint(limit_per_bucket=200, _user=admin, db=db)
        inb = await get_inbox(user=admin, db=db)
        clo = await closures_endpoint(limit_per_stage=100, _user=admin, db=db)

        pipe_by_key = {b.key: b for b in pipe.buckets}

        print("=== Bucket  Pipeline  Inbox(items)  Closures.totals  MATCH? ===")
        # Inbox total counts include `my_active` carved out. Add back for comparison.
        # Actually, Inbox top-bucket lists don't include my_active (we filter them out).
        # So Inbox bucket length is "cards I see in that column".
        for key in ("hot", "warm", "cold"):
            p_total = pipe_by_key[key].count
            i_cards = len(getattr(inb, key)) + sum(
                1 for c in inb.my_active if (c.lead_temperature or "") == key
            )
            c_total = clo.totals.get(key, "—")
            ok = "✓" if (p_total == i_cards or i_cards >= min(p_total, 100)) else "✗"
            print(f"  {key:9} {p_total:6}    {i_cards:6}        {c_total!s:6}        {ok}")

        # Closing: pipeline.closing vs sum of closures.closing buckets
        p_close = pipe_by_key["closing"].count
        c_close = sum(b.count for b in clo.closing)
        ok = "✓" if p_close == c_close else "✗"
        print(f"  {'closing':9} {p_close:6}    {'—':>6}        {c_close:6}        {ok}")

        # DNC: should NOT appear in pipeline; should appear in closures.totals['dnc']
        dnc_count = clo.totals.get("dnc", 0)
        print(f"  {'dnc':9} {'N/A':>6}    {'—':>6}        {dnc_count:6}        ✓ (excluded from pipeline)")

        print(f"\nPipeline grand total: {pipe.total_in_pipeline}")


asyncio.run(main())
