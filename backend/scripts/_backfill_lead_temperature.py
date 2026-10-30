"""One-shot: backfill Lead.lead_temperature from the most recent classified Call.

Phase 1 added Lead.lead_temperature as a mirror so the Inbox can filter without
joining calls every page-load — but the mirror is only written by NEW calls
landing in repo.finalize_call. Without this backfill, every existing lead
shows temperature=NULL and the kanban is empty on day one.

Also clears any stale lock from smoke tests.

Run from backend/ as:
  $env:PYTHONPATH = (Get-Location).Path
  ../venv/Scripts/python.exe scripts/_backfill_lead_temperature.py
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from app.db import models
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        # 1. Clear stale locks (from smoke tests etc).
        cleared = await db.execute(
            update(models.Lead).where(
                models.Lead.locked_by_user_id.is_not(None)
            ).values(
                locked_by_user_id=None,
                locked_until_at=None,
                lock_extensions=0,
            )
        )
        print(f"cleared {cleared.rowcount} stale lock(s)")

        # 2. For each Lead, find the most recent Call.lead_temperature.
        sub = (
            select(
                models.Call.lead_id,
                func.max(models.Call.id).label("latest_id"),
            )
            .where(models.Call.lead_temperature.is_not(None))
            .group_by(models.Call.lead_id)
            .subquery()
        )
        rows = (await db.execute(
            select(
                sub.c.lead_id,
                models.Call.lead_temperature,
                models.Call.lead_program,
                models.Call.lead_reasons,
                models.Call.id,
                models.Call.ended_at,
            )
            .join(models.Call, models.Call.id == sub.c.latest_id)
        )).all()

        print(f"found {len(rows)} classified leads to backfill")
        if not rows:
            return

        for lead_id, temp, program, reasons, call_id, ended_at in rows:
            await db.execute(
                update(models.Lead)
                .where(models.Lead.id == lead_id)
                .values(lead_temperature=temp)
            )
            # Emit a synthetic TEMPERATURE_SET event so the timeline isn't blank
            # for historical leads, AND so hot-since SLA math works.
            existing = (
                await db.execute(
                    select(func.count())
                    .select_from(models.LeadEvent)
                    .where(
                        models.LeadEvent.lead_id == lead_id,
                        models.LeadEvent.event_type == models.LeadEventType.TEMPERATURE_SET.value,
                    )
                )
            ).scalar_one()
            if existing == 0:
                ts = ended_at or datetime.now(timezone.utc)
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                db.add(models.LeadEvent(
                    lead_id=lead_id,
                    actor_kind=models.LeadEventActorKind.AI.value,
                    actor_user_id=None,
                    event_type=models.LeadEventType.TEMPERATURE_SET.value,
                    payload={
                        "from": None,
                        "to": temp,
                        "call_id": call_id,
                        "program": program,
                        "reasons": reasons,
                        "backfilled": True,
                    },
                    created_at=ts,
                ))

        await db.commit()

        # 3. Summary
        counts = (
            await db.execute(
                select(models.Lead.lead_temperature, func.count())
                .where(models.Lead.lead_temperature.is_not(None))
                .group_by(models.Lead.lead_temperature)
            )
        ).all()
        print("\npost-backfill counts:")
        for temp, n in counts:
            print(f"  {temp:10} {n}")


asyncio.run(main())
