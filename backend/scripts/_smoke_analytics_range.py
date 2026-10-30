"""Verify the new date-range params land in the right UTC window."""
import asyncio
from sqlalchemy import select

from app.api.analytics import analytics_endpoint
from app.db import models
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        admin = (await db.execute(select(models.User).where(models.User.role == "admin"))).scalar_one()

        for from_d, to_d in [
            ("2026-06-03", "2026-06-03"),  # single day
            ("2026-06-03", "2026-06-05"),  # 3-day range
            ("2026-06-02", "2026-06-06"),  # full activity window
        ]:
            r = await analytics_endpoint(
                window="7d", date_from=from_d, date_to=to_d, _admin=admin, db=db
            )
            print(f"\nfrom={from_d}  to={to_d}")
            print(f"  echoed: filter_from={r.filter_from}  filter_to={r.filter_to}")
            print(f"  utc window: {r.window_start_utc.isoformat()} → {r.window_end_utc.isoformat()}")
            print(f"  funnel total: {r.funnel.total_leads}")
            print(f"  AI dials: {r.ai.total_dials}, pickup%: {r.ai.pickup_rate_pct}")
            print(f"  outcomes: {[(o.outcome, o.count) for o in r.outcomes]}")


asyncio.run(main())
