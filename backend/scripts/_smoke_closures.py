"""Smoke-test /api/closures end-to-end + the disposition mapping flow."""
import asyncio
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update

from app.api.closures import closures_endpoint
from app.api.inbox import lock_lead, unlock_lead, UnlockBody
from app.db import models
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        admin = (
            await db.execute(select(models.User).where(models.User.role == "admin"))
        ).scalar_one()

        # 1) Pick an inquiry lead and walk it through the funnel.
        lead = (
            await db.execute(
                select(models.Lead).where(
                    models.Lead.funnel_stage == "inquiry",
                    models.Lead.dnd.is_(False),
                ).limit(1)
            )
        ).scalar_one()
        print(f"using lead id={lead.id} (funnel={lead.funnel_stage}, status={lead.status})")

        async def cycle(disp: str, note: str | None = "Spoke with caller, confirmed interest.", cb: datetime | None = None):
            await lock_lead(lead.id, user=admin, db=db)
            await unlock_lead(
                lead.id,
                UnlockBody(disposition=disp, notes=note, callback_at=cb, place_manual_call=True),
                user=admin,
                db=db,
            )
            await db.refresh(lead)
            print(f"  after {disp:25} → funnel={lead.funnel_stage:14} status={lead.status:20} dnd={lead.dnd}")

        # Walk through each axis: engaged → applied → lost (rolled back)
        await cycle("wants_brochure")
        await cycle("application_started")
        await cycle("callback_requested", cb=datetime.now(timezone.utc) + timedelta(days=1))
        await cycle("not_interested_fee", note="Father said budget too tight.")

        # 2) Mark a DIFFERENT lead as DND via disposition
        other = (
            await db.execute(
                select(models.Lead).where(
                    models.Lead.id != lead.id,
                    models.Lead.dnd.is_(False),
                ).limit(1)
            )
        ).scalar_one()
        await lock_lead(other.id, user=admin, db=db)
        await unlock_lead(
            other.id,
            UnlockBody(disposition="do_not_call", notes="Asked explicitly not to be called."),
            user=admin,
            db=db,
        )
        await db.refresh(other)
        print(f"  other lead id={other.id}: funnel={other.funnel_stage} dnd={other.dnd}")

        # 3) Call /api/closures
        print("\n=== closures payload ===")
        resp = await closures_endpoint(limit_per_stage=100, _user=admin, db=db)
        print(f"totals: {resp.totals}")
        for b in resp.closing:
            print(f"  closing[{b.stage}]: count={b.count}, sample lead names: {[l.name for l in b.leads[:3]]}")
        print(f"  dnc count: {len(resp.dnc)}, sample: {[l.name for l in resp.dnc[:3]]}")


asyncio.run(main())
