"""Campaign-specific DB helpers — separate file to keep app/db/repo.py focused."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models


async def create_campaign(
    db: AsyncSession,
    *,
    name: str,
    created_by_id: int,
    working_hours: dict | None = None,
    retry_policy: dict | None = None,
    script_template: str | None = None,
) -> models.Campaign:
    c = models.Campaign(
        name=name,
        created_by_id=created_by_id,
        status="draft",
        working_hours=working_hours,
        retry_policy=retry_policy,
        script_template=script_template,
    )
    db.add(c)
    await db.flush()
    return c


async def get_campaign(db: AsyncSession, cid: int) -> models.Campaign | None:
    row = await db.execute(select(models.Campaign).where(models.Campaign.id == cid))
    return row.scalar_one_or_none()


async def list_campaigns(
    db: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[models.Campaign], int]:
    stmt = select(models.Campaign)
    cnt = select(func.count()).select_from(models.Campaign)
    if status:
        stmt = stmt.where(models.Campaign.status == status)
        cnt = cnt.where(models.Campaign.status == status)
    stmt = stmt.order_by(models.Campaign.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(cnt)).scalar_one()
    return list(rows), int(total)


async def update_campaign(
    db: AsyncSession, cid: int, **fields: Any
) -> models.Campaign | None:
    c = await get_campaign(db, cid)
    if not c:
        return None
    for k, v in fields.items():
        if v is not None:
            setattr(c, k, v)
    await db.flush()
    return c


async def find_in_progress_for_lead(
    db: AsyncSession, lead_id: int
) -> models.CampaignLead | None:
    """Find the most recent CampaignLead with status=in_progress for a lead.

    Used by the call-end handler to resolve which campaign attempt a finishing
    call belongs to (the Call row stores lead_id but not campaign_lead_id, so
    we look it up by 'whichever campaign just dialled this lead'). If two
    campaigns happen to dial the same lead simultaneously — rare but
    possible — we settle the most-recently-attempted one, which is the right
    one in practice.
    """
    stmt = (
        select(models.CampaignLead)
        .where(
            models.CampaignLead.lead_id == lead_id,
            models.CampaignLead.status == "in_progress",
        )
        .order_by(models.CampaignLead.updated_at.desc())
        .limit(1)
    )
    row = await db.execute(stmt)
    return row.scalar_one_or_none()


async def delete_campaign(db: AsyncSession, cid: int) -> bool:
    """Delete a campaign + its CampaignLead join rows.

    Does NOT delete the underlying Lead records (they're shared across
    campaigns and persisted independently). Returns True if a campaign was
    actually deleted, False if it didn't exist.
    """
    c = await get_campaign(db, cid)
    if not c:
        return False
    # Drop the campaign_leads junction rows first to avoid FK violations on
    # databases that don't have ON DELETE CASCADE configured.
    await db.execute(
        delete(models.CampaignLead).where(models.CampaignLead.campaign_id == cid)
    )
    await db.execute(
        delete(models.Campaign).where(models.Campaign.id == cid)
    )
    await db.flush()
    return True


async def attach_leads(
    db: AsyncSession, campaign_id: int, lead_ids: list[int]
) -> int:
    """Add leads to a campaign (idempotent). Returns count added."""
    # Dedupe the incoming list — a CSV with the same phone repeated would
    # otherwise hit the UNIQUE(campaign_id, lead_id) constraint mid-flush
    # and tank the whole upload.
    unique_lead_ids = list(dict.fromkeys(lead_ids))
    existing_rows = await db.execute(
        select(models.CampaignLead.lead_id).where(
            and_(
                models.CampaignLead.campaign_id == campaign_id,
                models.CampaignLead.lead_id.in_(unique_lead_ids),
            )
        )
    )
    existing = {r[0] for r in existing_rows}
    to_add = [lid for lid in unique_lead_ids if lid not in existing]
    for lid in to_add:
        db.add(
            models.CampaignLead(
                campaign_id=campaign_id, lead_id=lid, status="queued"
            )
        )
    await db.flush()
    return len(to_add)


async def detach_lead(
    db: AsyncSession, campaign_id: int, lead_id: int
) -> bool:
    res = await db.execute(
        delete(models.CampaignLead).where(
            and_(
                models.CampaignLead.campaign_id == campaign_id,
                models.CampaignLead.lead_id == lead_id,
            )
        )
    )
    return res.rowcount > 0


async def list_campaign_leads(
    db: AsyncSession,
    campaign_id: int,
    *,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[models.CampaignLead], int]:
    stmt = select(models.CampaignLead).where(
        models.CampaignLead.campaign_id == campaign_id
    )
    cnt = select(func.count()).select_from(models.CampaignLead).where(
        models.CampaignLead.campaign_id == campaign_id
    )
    if status:
        stmt = stmt.where(models.CampaignLead.status == status)
        cnt = cnt.where(models.CampaignLead.status == status)
    stmt = stmt.order_by(models.CampaignLead.id.asc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(cnt)).scalar_one()
    return list(rows), int(total)


async def pick_next_eligible_lead(
    db: AsyncSession,
    campaign: models.Campaign,
    *,
    gap_hours: float = 4.0,
    max_attempts: int = 3,
) -> tuple[models.CampaignLead, models.Lead] | None:
    """Find the next campaign_lead eligible to be dialed.

    Eligibility:
      - campaign_lead.status in (queued, failed_retryable)
      - lead.dnd is False
      - campaign_lead.attempts < max_attempts (PER-CAMPAIGN counter)
      - lead.last_attempted_at older than `gap_hours` ago (or null)

    2026-05-28: switched the attempts cap from `Lead.attempt_count` (a GLOBAL
    counter that accumulates every call ever made to a phone, across all
    campaigns) to `CampaignLead.attempts` (the per-campaign counter, 0 on
    freshly attached leads). The old global check silently blocked freshly
    uploaded campaigns whenever the lead's phone had been dialed >= 3 times
    in earlier campaigns or test calls — e.g. the demo test phone, at 165
    historical attempts, was permanently un-dialable. The dialer logged
    nothing; the campaign just sat active forever with phones never ringing.
    Per-campaign is the right semantic: max_attempts is a retry budget for
    THIS campaign, not a lifetime ceiling on the lead.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=gap_hours)
    stmt = (
        select(models.CampaignLead, models.Lead)
        .join(models.Lead, models.Lead.id == models.CampaignLead.lead_id)
        .where(
            and_(
                models.CampaignLead.campaign_id == campaign.id,
                models.CampaignLead.status.in_(["queued", "failed_retryable"]),
                models.Lead.dnd.is_(False),
                models.CampaignLead.attempts < max_attempts,
                or_(
                    models.Lead.last_attempted_at.is_(None),
                    models.Lead.last_attempted_at < cutoff,
                ),
            )
        )
        .order_by(models.CampaignLead.attempts.asc(), models.CampaignLead.id.asc())
        .limit(1)
    )
    row = await db.execute(stmt)
    pair = row.first()
    if not pair:
        return None
    return pair[0], pair[1]


async def mark_attempt(
    db: AsyncSession,
    cl_id: int,
    *,
    new_status: str,
    increment_attempts: bool = True,
) -> None:
    await db.execute(
        update(models.CampaignLead)
        .where(models.CampaignLead.id == cl_id)
        .values(
            status=new_status,
            attempts=(
                models.CampaignLead.attempts + 1 if increment_attempts else models.CampaignLead.attempts
            ),
        )
    )
    # Mirror the attempt timestamp onto Lead.last_attempted_at so the inbox
    # cards stop showing "Never called" for leads we've already dialed.
    # (2026-06-24 — bug fix: this had been silently NULL for every lead.)
    # attempt_count is NOT updated here — the dialer's _dial_one already
    # bumps it after a successful originate, so doing it here would double.
    if increment_attempts:
        cl_row = (
            await db.execute(
                select(models.CampaignLead.lead_id).where(
                    models.CampaignLead.id == cl_id
                )
            )
        ).first()
        if cl_row is not None:
            await db.execute(
                update(models.Lead)
                .where(models.Lead.id == cl_row[0])
                .values(last_attempted_at=datetime.utcnow())
            )


async def reset_failed_leads(
    db: AsyncSession, campaign_id: int | None = None
) -> dict[str, int]:
    """Flip every failed / failed_retryable campaign_lead back to 'queued'
    so the dialer can pick them up again — without re-uploading the CSV.

    Three things are reset to make the leads IMMEDIATELY re-eligible
    under the existing dialer gate (pick_next_dialable):
      1. campaign_lead.status  = "queued"     (was failed / failed_retryable)
      2. campaign_lead.attempts = 0           (so attempts < max_attempts again)
      3. lead.last_attempted_at = NULL        (so the 4-hour gap check passes)

    Scope:
      - campaign_id given → only that campaign
      - campaign_id None  → ALL campaigns

    Returns: {"leads_reset": N, "campaigns_affected": M}.
    Caller is responsible for setting campaign.status="active" if they
    want dialing to start immediately.
    """
    # 1. Find every campaign_lead row to reset, capturing the lead_id set
    #    so we can null out Lead.last_attempted_at for the right leads only.
    target_stmt = select(
        models.CampaignLead.id,
        models.CampaignLead.lead_id,
        models.CampaignLead.campaign_id,
    ).where(models.CampaignLead.status.in_(["failed", "failed_retryable"]))
    if campaign_id is not None:
        target_stmt = target_stmt.where(models.CampaignLead.campaign_id == campaign_id)
    rows = (await db.execute(target_stmt)).all()
    if not rows:
        return {"leads_reset": 0, "campaigns_affected": 0}

    cl_ids = [r[0] for r in rows]
    lead_ids = {r[1] for r in rows}
    campaign_ids = {r[2] for r in rows}

    # 2. Bulk flip status + zero attempts on the campaign_lead rows.
    await db.execute(
        update(models.CampaignLead)
        .where(models.CampaignLead.id.in_(cl_ids))
        .values(status="queued", attempts=0)
    )

    # 3. Clear the global last_attempted_at on every affected Lead so the
    #    4-hour gap check doesn't gate them. Without this, freshly reset
    #    leads sit in the queue waiting for the clock — defeats the point.
    await db.execute(
        update(models.Lead)
        .where(models.Lead.id.in_(lead_ids))
        .values(last_attempted_at=None)
    )

    return {
        "leads_reset": len(cl_ids),
        "campaigns_affected": len(campaign_ids),
    }


async def campaign_progress(
    db: AsyncSession, campaign_id: int
) -> dict[str, int]:
    rows = await db.execute(
        select(models.CampaignLead.status, func.count(models.CampaignLead.id))
        .where(models.CampaignLead.campaign_id == campaign_id)
        .group_by(models.CampaignLead.status)
    )
    out = {"total": 0, "queued": 0, "in_progress": 0, "done": 0, "failed": 0, "failed_retryable": 0}
    for status, n in rows.all():
        out[status] = int(n)
        out["total"] += int(n)
    return out
