"""Campaign + dialer REST."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.leads import parse_and_upsert_leads_csv
from app.audit import write_audit
from app.auth import current_user, require_admin, require_counsellor_or_admin
from app.campaigns import get_dialer
from app.db import models
from app.db import campaigns_repo as crepo
from app.db.session import get_db
from app.schemas.campaign import (
    AttachLeadsIn,
    CampaignIn,
    CampaignLeadList,
    CampaignLeadOut,
    CampaignList,
    CampaignOut,
    CampaignUpdate,
)

log = logging.getLogger("gunivox.api.campaigns")

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


@router.get("", response_model=CampaignList)
async def list_endpoint(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CampaignList:
    rows, total = await crepo.list_campaigns(db, status=status, limit=limit, offset=offset)
    return CampaignList(total=total, items=[CampaignOut.model_validate(r) for r in rows])


@router.post("", response_model=CampaignOut, status_code=201)
async def create_endpoint(
    body: CampaignIn,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CampaignOut:
    c = await crepo.create_campaign(
        db,
        name=body.name,
        created_by_id=user.id,
        working_hours=body.working_hours.model_dump() if body.working_hours else None,
        retry_policy=body.retry_policy.model_dump() if body.retry_policy else None,
        script_template=body.script_template,
    )
    await db.commit()
    await db.refresh(c)
    return CampaignOut.model_validate(c)


@router.get("/{cid}", response_model=CampaignOut)
async def get_endpoint(
    cid: int,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CampaignOut:
    c = await crepo.get_campaign(db, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return CampaignOut.model_validate(c)


@router.put("/{cid}", response_model=CampaignOut)
async def update_endpoint(
    cid: int,
    body: CampaignUpdate,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CampaignOut:
    fields = body.model_dump(exclude_none=True)
    if "working_hours" in fields and fields["working_hours"] is not None:
        fields["working_hours"] = body.working_hours.model_dump()
    if "retry_policy" in fields and fields["retry_policy"] is not None:
        fields["retry_policy"] = body.retry_policy.model_dump()
    c = await crepo.update_campaign(db, cid, **fields)
    if not c:
        raise HTTPException(404, "Campaign not found")
    await db.commit()
    await db.refresh(c)
    return CampaignOut.model_validate(c)


@router.post("/{cid}/leads", response_model=dict)
async def attach_endpoint(
    cid: int,
    body: AttachLeadsIn,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    c = await crepo.get_campaign(db, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    n = await crepo.attach_leads(db, cid, body.lead_ids)
    await db.commit()
    return {"attached": n}


@router.delete("/{cid}/leads/{lead_id}", status_code=204)
async def detach_endpoint(
    cid: int,
    lead_id: int,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await crepo.detach_lead(db, cid, lead_id)
    if not ok:
        raise HTTPException(404, "Not in campaign")
    await db.commit()


@router.get("/{cid}/leads", response_model=CampaignLeadList)
async def list_campaign_leads_endpoint(
    cid: int,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CampaignLeadList:
    rows, total = await crepo.list_campaign_leads(
        db, cid, status=status, limit=limit, offset=offset
    )
    return CampaignLeadList(
        total=total, items=[CampaignLeadOut.model_validate(r) for r in rows]
    )


@router.get("/{cid}/progress")
async def progress_endpoint(
    cid: int,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await crepo.campaign_progress(db, cid)


@router.post("/{cid}/start", response_model=CampaignOut)
async def start_endpoint(
    cid: int,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CampaignOut:
    c = await crepo.update_campaign(db, cid, status="active")
    if not c:
        raise HTTPException(404, "Campaign not found")
    await write_audit(db, user_id=user.id, action="campaign.start", entity="campaign", entity_id=cid)
    await db.commit()
    await db.refresh(c)
    # Kick the dialer once so it picks the campaign up immediately.
    asyncio.create_task(_kick_dialer())
    return CampaignOut.model_validate(c)


@router.post("/{cid}/pause", response_model=CampaignOut)
async def pause_endpoint(
    cid: int,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CampaignOut:
    c = await crepo.update_campaign(db, cid, status="paused")
    if not c:
        raise HTTPException(404, "Campaign not found")
    await write_audit(db, user_id=user.id, action="campaign.pause", entity="campaign", entity_id=cid)
    await db.commit()
    await db.refresh(c)
    return CampaignOut.model_validate(c)


@router.post("/{cid}/retry_failed", response_model=CampaignOut)
async def retry_failed_endpoint(
    cid: int,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CampaignOut:
    """Flip every failed / failed_retryable lead in this campaign back to
    queued (attempts=0, lead.last_attempted_at=NULL) AND set the campaign
    to active so the dialer picks them up on the next tick.

    Lets the operator "re-run the failed calls" without re-uploading
    the CSV. Idempotent — running it again on a freshly-reset campaign
    just resets 0 leads.
    """
    c = await crepo.get_campaign(db, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    result = await crepo.reset_failed_leads(db, campaign_id=cid)
    # Auto-start so the dialer picks them up immediately.
    c = await crepo.update_campaign(db, cid, status="active")
    await write_audit(
        db, user_id=user.id, action="campaign.retry_failed",
        entity="campaign", entity_id=cid,
        details={"leads_reset": result["leads_reset"]},
    )
    await db.commit()
    await db.refresh(c)
    asyncio.create_task(_kick_dialer())
    log.info(
        "campaign %d retry_failed by user %d → reset %d leads",
        cid, user.id, result["leads_reset"],
    )
    return CampaignOut.model_validate(c)


@router.post("/retry_failed_all")
async def retry_failed_all_endpoint(
    user: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Same as /{cid}/retry_failed but applies to EVERY campaign at once.
    Admin-only. Sets each campaign that had leads reset to 'active' so
    they all start dialing again.
    """
    result = await crepo.reset_failed_leads(db, campaign_id=None)
    # If we actually reset some leads, flip every paused campaign that
    # owns failed leads to active. Touch only paused ones so we don't
    # accidentally restart a completed/draft campaign.
    started_campaign_ids: list[int] = []
    if result["leads_reset"] > 0:
        rows = (await db.execute(
            select(models.Campaign).where(models.Campaign.status == "paused")
        )).scalars().all()
        for c in rows:
            await crepo.update_campaign(db, c.id, status="active")
            started_campaign_ids.append(c.id)
    await write_audit(
        db, user_id=user.id, action="campaign.retry_failed_all",
        entity="campaign", entity_id=None,
        details={
            "leads_reset": result["leads_reset"],
            "campaigns_affected": result["campaigns_affected"],
            "campaigns_started": started_campaign_ids,
        },
    )
    await db.commit()
    asyncio.create_task(_kick_dialer())
    log.info(
        "campaign.retry_failed_all by user %d → reset %d leads across %d campaigns; started %d campaigns",
        user.id, result["leads_reset"], result["campaigns_affected"], len(started_campaign_ids),
    )
    return {
        "leads_reset": result["leads_reset"],
        "campaigns_affected": result["campaigns_affected"],
        "campaigns_started": started_campaign_ids,
    }


@router.delete("/{cid}", status_code=204)
async def delete_campaign_endpoint(
    cid: int,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a campaign and its campaign_leads junction rows.

    Leads themselves are NOT deleted — they're shared across campaigns and
    keep their own history. Pauses the campaign first (the in-memory dialer
    holds no campaign-level state; once status moves off 'active' it's
    skipped, and removing the row makes any in-flight reference 404).
    """
    ok = await crepo.delete_campaign(db, cid)
    if not ok:
        raise HTTPException(404, "Campaign not found")
    await write_audit(db, user_id=user.id, action="campaign.delete", entity="campaign", entity_id=cid)
    await db.commit()
    log.info("campaign %d deleted by user %d", cid, user.id)


# Trigger an immediate dialer tick (used after Start so we don't wait the full interval).
import asyncio  # noqa: E402


async def _kick_dialer() -> None:
    try:
        d = get_dialer()
        await d._tick()  # noqa: SLF001 - intentional one-shot
    except Exception:
        log.exception("kick_dialer failed")


class CsvCampaignResult(BaseModel):
    campaign_id: int
    campaign_name: str
    inserted: int
    updated: int
    skipped: int
    attached: int
    errors: list[str] = []


@router.post("/upload-and-start", response_model=CsvCampaignResult)
async def upload_and_start_endpoint(
    file: UploadFile = File(...),
    name: str = Form(...),
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CsvCampaignResult:
    """One-shot CSV → campaign → start dialing.

    The Campaigns page uploads a CSV here. We:
      1. Parse the CSV (auto-detects 10-digit Indian phones in any cell)
      2. Upsert every row as a Lead
      3. Create a new Campaign with the given name
      4. Attach all detected leads
      5. Mark campaign active + kick the dialer immediately
    Results flow into /pipeline and /calls automatically as the dialer
    works through them.
    """
    content = await file.read()
    lead_ids, inserted, updated, skipped, errors = await parse_and_upsert_leads_csv(
        db, content, phone_from_first_column=True,
    )
    if not lead_ids:
        raise HTTPException(
            400,
            "No usable phone numbers found in CSV. "
            "Expected schema: phone in column L, first name in column E, last "
            "name in column G. Phone must be a 10-digit Indian mobile starting "
            "with 6/7/8/9 (+91, leading zero, dashes, spaces are tolerated).",
        )

    # Create the campaign.
    campaign = await crepo.create_campaign(
        db,
        name=name.strip() or f"CSV upload ({inserted + updated} leads)",
        created_by_id=user.id,
        working_hours=None,
        retry_policy=None,
        script_template=None,
    )
    await db.commit()
    await db.refresh(campaign)

    # Attach all newly-upserted leads.
    attached = await crepo.attach_leads(db, campaign.id, lead_ids)
    await db.commit()

    # Activate + kick the dialer so the first call goes out immediately.
    await crepo.update_campaign(db, campaign.id, status="active")
    await write_audit(
        db, user_id=user.id, action="campaign.upload_and_start",
        entity="campaign", entity_id=campaign.id,
    )
    await db.commit()
    asyncio.create_task(_kick_dialer())

    log.info(
        "csv→campaign: id=%d name=%r leads=%d (ins=%d upd=%d skipped=%d attached=%d)",
        campaign.id, name, len(lead_ids), inserted, updated, skipped, attached,
    )
    return CsvCampaignResult(
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        attached=attached,
        errors=errors[:50],
    )
