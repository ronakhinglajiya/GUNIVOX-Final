"""Verify date-filtered pipeline mode reads from lead_events correctly."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import func, select

from app.api.pipeline import pipeline_endpoint
from app.db import models
from app.db.session import SessionLocal

IST = ZoneInfo("Asia/Kolkata")


async def main() -> None:
    today_ist = datetime.now(IST).strftime("%Y-%m-%d")
    print(f"today IST = {today_ist}\n")

    async with SessionLocal() as db:
        admin = (
            await db.execute(select(models.User).where(models.User.role == "admin"))
        ).scalar_one()

        # 1) Default mode (no date)
        print("=== default mode (no date) ===")
        r = await pipeline_endpoint(date=None, limit_per_bucket=200, _user=admin, db=db)
        for b in r.buckets:
            print(f"  {b.key:9} {b.count:6}")
        print(f"  total: {r.total_in_pipeline}")

        # 2) Date-filtered mode (today)
        print(f"\n=== date-filtered mode ({today_ist}) ===")
        r2 = await pipeline_endpoint(date=today_ist, limit_per_bucket=200, _user=admin, db=db)
        for b in r2.buckets:
            print(f"  {b.key:9} {b.count:6}")
        print(f"  total: {r2.total_in_pipeline}")
        print(f"  filter window: {r2.filter_window_start_utc.isoformat()} → "
              f"{r2.filter_window_end_utc.isoformat()}")

        # 3) Sanity: count temperature_set + funnel_stage_changed events in DB
        ev_counts = (await db.execute(
            select(models.LeadEvent.event_type, func.count())
            .where(models.LeadEvent.event_type.in_([
                models.LeadEventType.TEMPERATURE_SET.value,
                models.LeadEventType.FUNNEL_STAGE_CHANGED.value,
            ]))
            .group_by(models.LeadEvent.event_type)
        )).all()
        print(f"\n=== event totals in DB ===")
        for row in ev_counts:
            print(f"  {row[0]:25} {row[1]}")


asyncio.run(main())
