"""Admin cleanup endpoints — destructive one-off actions surfaced in the UI.

Currently:
  DELETE /api/admin/unnamed_leads
    Removes every Lead with name IS NULL, plus the rows that cascade off
    it: turns → calls → lead_events → callbacks → campaign_leads junction.
    Used by the "Delete unnamed leads" button on the Inbox top bar after
    we nulled Excel-mangled names from a bad CSV upload (2026-06-11).

Admin-only. Returns counts of every row tier that was removed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.audit import write_audit
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])
log = logging.getLogger("gunivox.admin_cleanup")


class UnnamedDeleteResult(BaseModel):
    leads_deleted: int
    calls_deleted: int
    turns_deleted: int
    lead_events_deleted: int
    callbacks_deleted: int
    campaign_lead_junctions_deleted: int


@router.get("/unnamed_leads/preview")
async def unnamed_leads_preview(
    _: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Cheap pre-flight count so the UI confirm dialog can show the
    operator exactly what's about to vanish."""
    n_leads = (await db.execute(
        select(func.count()).select_from(models.Lead).where(models.Lead.name.is_(None))
    )).scalar_one()
    n_calls = (await db.execute(
        select(func.count()).select_from(models.Call).where(
            models.Call.lead_id.in_(
                select(models.Lead.id).where(models.Lead.name.is_(None))
            )
        )
    )).scalar_one()
    return {"leads_to_delete": int(n_leads), "calls_to_delete": int(n_calls)}


@router.delete("/unnamed_leads", response_model=UnnamedDeleteResult)
async def delete_unnamed_leads(
    user: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UnnamedDeleteResult:
    """Hard-delete every Lead with name IS NULL + all the rows that hang
    off it. Cascade order matters (children → parents) so the FK
    constraints don't complain mid-transaction.

    Idempotent — re-running on a clean DB matches 0 rows and returns
    zeros for every count.
    """
    # 1. Find the target lead_ids in a single fetch.
    lead_ids = list((await db.execute(
        select(models.Lead.id).where(models.Lead.name.is_(None))
    )).scalars().all())
    if not lead_ids:
        return UnnamedDeleteResult(
            leads_deleted=0, calls_deleted=0, turns_deleted=0,
            lead_events_deleted=0, callbacks_deleted=0,
            campaign_lead_junctions_deleted=0,
        )

    # 2. Pull the call_ids for turn-deletion (turns FK call_id).
    call_ids = list((await db.execute(
        select(models.Call.id).where(models.Call.lead_id.in_(lead_ids))
    )).scalars().all())

    # 3. Children first.
    n_turns = 0
    if call_ids:
        res = await db.execute(
            delete(models.Turn).where(models.Turn.call_id.in_(call_ids))
        )
        n_turns = int(res.rowcount or 0)

    n_calls = 0
    if call_ids:
        res = await db.execute(
            delete(models.Call).where(models.Call.id.in_(call_ids))
        )
        n_calls = int(res.rowcount or 0)

    res = await db.execute(
        delete(models.LeadEvent).where(models.LeadEvent.lead_id.in_(lead_ids))
    )
    n_events = int(res.rowcount or 0)

    res = await db.execute(
        delete(models.Callback).where(models.Callback.lead_id.in_(lead_ids))
    )
    n_callbacks = int(res.rowcount or 0)

    res = await db.execute(
        delete(models.CampaignLead).where(models.CampaignLead.lead_id.in_(lead_ids))
    )
    n_cl = int(res.rowcount or 0)

    # 4. Finally the lead rows.
    res = await db.execute(
        delete(models.Lead).where(models.Lead.id.in_(lead_ids))
    )
    n_leads = int(res.rowcount or 0)

    await write_audit(
        db, user_id=user.id,
        action="admin.delete_unnamed_leads",
        entity="lead", entity_id=None,
        details={
            "leads_deleted": n_leads,
            "calls_deleted": n_calls,
            "turns_deleted": n_turns,
            "lead_events_deleted": n_events,
            "callbacks_deleted": n_callbacks,
            "campaign_lead_junctions_deleted": n_cl,
        },
    )
    await db.commit()

    log.warning(
        "admin %s deleted %d unnamed leads (%d calls, %d turns, %d events, "
        "%d callbacks, %d junctions)",
        user.email, n_leads, n_calls, n_turns, n_events, n_callbacks, n_cl,
    )
    return UnnamedDeleteResult(
        leads_deleted=n_leads,
        calls_deleted=n_calls,
        turns_deleted=n_turns,
        lead_events_deleted=n_events,
        callbacks_deleted=n_callbacks,
        campaign_lead_junctions_deleted=n_cl,
    )
