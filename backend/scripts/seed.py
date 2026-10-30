"""Seed initial data: admin user + a few sample leads.

Idempotent — run multiple times safely. Existing rows are not duplicated.

Usage (from backend/):
    ../venv/Scripts/python.exe -m scripts.seed
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.password import hash_password  # noqa: E402
from app.db import repo  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("seed")

DEFAULT_ADMIN = {
    "email": "admin@gunivox.online",
    "password": "admin123",
    "name": "Gunivox Admin",
    "role": "admin",
}

SAMPLE_LEADS = [
    {
        "phone": "+917990267016",
        "name": "Ronak (test)",
        "program_interest": "B.Tech CSE",
        "preferred_language": "gu",
        "source": "seed",
    },
    {
        "phone": "+919876543210",
        "name": "Demo Student",
        "program_interest": "MBA",
        "preferred_language": "en",
        "source": "seed",
    },
]


async def main() -> None:
    async with SessionLocal() as db:
        # Admin user
        existing = await repo.get_user_by_email(db, DEFAULT_ADMIN["email"])
        if existing:
            log.info("admin already exists: %s", existing.email)
        else:
            await repo.create_user(
                db,
                email=DEFAULT_ADMIN["email"],
                password_hash=hash_password(DEFAULT_ADMIN["password"]),
                name=DEFAULT_ADMIN["name"],
                role=DEFAULT_ADMIN["role"],
            )
            log.info(
                "created admin: email=%s password=%s (CHANGE BEFORE PROD)",
                DEFAULT_ADMIN["email"],
                DEFAULT_ADMIN["password"],
            )

        # Counsellor user
        counsellor_email = "counsellor@gunivox.online"
        existing_c = await repo.get_user_by_email(db, counsellor_email)
        if not existing_c:
            await repo.create_user(
                db,
                email=counsellor_email,
                password_hash=hash_password("counsellor123"),
                name="Demo Counsellor",
                role="counsellor",
            )
            log.info("created counsellor: %s / counsellor123", counsellor_email)

        # Sample leads
        for ld in SAMPLE_LEADS:
            _, created = await repo.upsert_lead(db, **ld)
            log.info("lead %s phone=%s", "created" if created else "exists", ld["phone"])

        await db.commit()
        log.info("seed done")


if __name__ == "__main__":
    asyncio.run(main())
