"""Audit-log helper. Writes one row per administrative action.

Use:
    await write_audit(db, user_id=u.id, action="campaign.start", entity="campaign", entity_id=c.id)
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models

log = logging.getLogger("gunivox.audit")


async def write_audit(
    db: AsyncSession,
    *,
    user_id: int | None,
    action: str,
    entity: str | None = None,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        row = models.AuditLog(
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            details=details,
        )
        db.add(row)
        await db.flush()
    except Exception:
        log.exception("audit write failed (non-fatal)")
