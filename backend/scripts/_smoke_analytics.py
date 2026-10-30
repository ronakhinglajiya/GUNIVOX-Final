"""End-to-end smoke of /api/admin/analytics — every widget runs cleanly."""
import asyncio
from sqlalchemy import select

from app.api.analytics import analytics_endpoint
from app.db import models
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        admin = (
            await db.execute(select(models.User).where(models.User.role == "admin"))
        ).scalar_one()
        for window in ("today", "7d", "30d", "all"):
            r = await analytics_endpoint(window=window, _admin=admin, db=db)
            print(f"\n─── window = {window} ───")
            print(f"funnel total leads: {r.funnel.total_leads}")
            print("  steps:")
            for s in r.funnel.steps:
                print(f"    {s.key:14} {s.count}")
            print(f"AI tile:  dials={r.ai.total_dials}  pickup%={r.ai.pickup_rate_pct}  "
                  f"avg_dur={r.ai.avg_duration_s}s  median_ttfa={r.ai.median_ttfa_ms}ms  "
                  f"long%={r.ai.pct_long_conversations}")
            print(f"leaderboard rows: {len(r.leaderboard)}")
            for row in r.leaderboard[:3]:
                print(f"  {row.name:22} calls={row.calls:4} talk={row.talk_time_sec}s  "
                      f"hot={row.hot_handled}  conv%={row.conversion_pct}")
            print(f"SLA: hot_unclaimed_30min={r.sla.hot_unclaimed_over_30min}  "
                  f"callbacks_overdue={r.sla.callbacks_overdue}  "
                  f"orphans={r.sla.locks_orphaned}")
            print(f"outcomes ({len(r.outcomes)}):")
            for o in r.outcomes:
                print(f"  {o.outcome:14} {o.count}")
            print(f"heatmap cells: {len(r.heatmap)}")
            print(f"top programs ({len(r.program_demand)}):")
            for p in r.program_demand[:5]:
                print(f"  {p.program[:40]:40} h={p.hot} w={p.warm} c={p.cold} total={p.total}")


asyncio.run(main())
