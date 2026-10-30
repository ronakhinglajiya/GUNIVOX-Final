"""Smoke-test the inbox lock/unlock/extend/timeline flow end-to-end."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.api.inbox import (
    get_inbox, lock_lead, extend_lock, unlock_lead, get_timeline,
    UnlockBody, LOCK_DURATION,
)
from app.db import models
from app.db.session import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        admin = (
            await db.execute(select(models.User).where(models.User.role == "admin"))
        ).scalar_one()

        # Pick any lead and make sure it has no stale lock.
        any_lead = (
            await db.execute(select(models.Lead).limit(1))
        ).scalar_one()
        lid = any_lead.id
        any_lead.locked_by_user_id = None
        any_lead.locked_until_at = None
        any_lead.lock_extensions = 0
        await db.commit()
        print(f"using lead id={lid}")

        # Inbox view (just confirm it runs)
        inbox = await get_inbox(user=admin, db=db)
        print(f"  inbox  : hot={len(inbox.hot)} warm={len(inbox.warm)} "
              f"cold={len(inbox.cold)} my_active={len(inbox.my_active)}")

        # LOCK
        lock = await lock_lead(lid, user=admin, db=db)
        print(f"  lock   : holder={lock.locked_by_user_id} "
              f"until={lock.locked_until_at.isoformat()} ext={lock.lock_extensions}")

        # EXTEND
        ext = await extend_lock(lid, user=admin, db=db)
        print(f"  extend : until={ext.locked_until_at.isoformat()} ext={ext.lock_extensions}")

        # UNLOCK with disposition + notes
        unlocked = await unlock_lead(
            lid,
            UnlockBody(
                disposition=models.Disposition.WANTS_BROCHURE.value,
                notes="Spoke with father, will send brochure on WhatsApp.",
                place_manual_call=True,
            ),
            user=admin,
            db=db,
        )
        print(f"  unlock : locked_by={unlocked.locked_by_user_id} (None=released ok) "
              f"disposition={unlocked.last_disposition}")

        # TIMELINE
        tl = await get_timeline(lid, limit=20, _user=admin, db=db)
        print(f"  timeline ({len(tl.events)} events):")
        for ev in tl.events:
            print(f"    {ev.created_at.isoformat()} {ev.event_type:25} "
                  f"actor={ev.actor_kind}/{ev.actor_name or '-'}")


asyncio.run(main())
