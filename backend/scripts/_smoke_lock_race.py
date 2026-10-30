"""Race test: two concurrent lock attempts — only one must succeed."""
import asyncio
from fastapi import HTTPException
from sqlalchemy import select, update

from app.api.inbox import lock_lead
from app.db import models
from app.db.session import SessionLocal


async def main() -> None:
    # Pick two distinct users.
    async with SessionLocal() as db:
        users = (await db.execute(select(models.User).limit(2))).scalars().all()
        if len(users) < 2:
            print("Need at least 2 users in DB for race test."); return
        u1, u2 = users[0], users[1]
        any_lead = (await db.execute(select(models.Lead).limit(1))).scalar_one()
        lid = any_lead.id
        # clear any pre-existing lock
        await db.execute(
            update(models.Lead).where(models.Lead.id == lid).values(
                locked_by_user_id=None, locked_until_at=None, lock_extensions=0
            )
        )
        await db.commit()
        print(f"using lead id={lid}, racing users {u1.email!r} vs {u2.email!r}")

    async def attempt(user: models.User, tag: str) -> str:
        async with SessionLocal() as db:
            try:
                lock = await lock_lead(lid, user=user, db=db)
                return f"{tag} → SUCCESS (holder={lock.locked_by_user_id})"
            except HTTPException as e:
                return f"{tag} → REJECTED ({e.status_code}, {e.detail})"

    results = await asyncio.gather(
        attempt(u1, f"u1({u1.id})"),
        attempt(u2, f"u2({u2.id})"),
    )
    for r in results:
        print(" ", r)
    successes = sum(1 for r in results if "SUCCESS" in r)
    print(f"\n{successes} / 2 succeeded — expected exactly 1")
    assert successes == 1, "RACE BUG: more than one user acquired the lock"
    print("PASS")


asyncio.run(main())
