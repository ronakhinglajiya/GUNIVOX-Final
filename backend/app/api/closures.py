"""Closures — the back-half of the funnel.

Two views in one endpoint so a single page-load gets both:

  • Closing  : leads in APPLIED / DOCS_RECEIVED / ENROLLED. These are the
              ones the counsellor needs to follow up with paperwork.
              Grouped by funnel_stage so the admin sees stage-by-stage volume.

  • DNC      : leads with `dnd=True` — they've been removed from future
              dialing. The campaign dialer already skips them; this just
              gives the admin a roster + reasons.

Both lists are admin-and-counsellor visible (admin needs to drive closure;
counsellor needs to see who they've moved to DNC). Phone numbers are
returned RAW; the frontend masks for counsellors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_counsellor_or_admin
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["closures"])


class ClosureLead(BaseModel):
    id: int
    name: str | None
    phone: str
    email: str | None
    program_interest: str | None
    funnel_stage: str
    funnel_stage_set_at: Optional[datetime] = None
    status: str
    last_disposition: str | None
    last_disposition_at: Optional[datetime] = None
    dnd: bool
    notes: str | None


class StageBucket(BaseModel):
    stage: str
    count: int
    leads: list[ClosureLead]


class ClosuresResponse(BaseModel):
    closing: list[StageBucket]           # APPLIED / DOCS_RECEIVED / ENROLLED
    dnc: list[ClosureLead]
    totals: dict[str, int]               # one number per FunnelStage value + 'dnc' + 'enrolled_pct'
    server_time: datetime


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_dto(lead: models.Lead) -> ClosureLead:
    return ClosureLead(
        id=lead.id,
        name=lead.name,
        phone=lead.phone,
        email=lead.email,
        program_interest=lead.program_interest,
        funnel_stage=lead.funnel_stage,
        funnel_stage_set_at=_utc(lead.funnel_stage_set_at),
        status=lead.status,
        last_disposition=lead.last_disposition,
        last_disposition_at=_utc(lead.last_disposition_at),
        dnd=lead.dnd,
        notes=lead.notes,
    )


@router.get("/closures", response_model=ClosuresResponse)
async def closures_endpoint(
    limit_per_stage: int = Query(100, ge=1, le=500),
    _user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> ClosuresResponse:
    """One-shot payload: closing buckets (3 stages) + DNC list + totals."""
    # ── Closing buckets ──
    closing_stages = [
        models.FunnelStage.APPLIED.value,
        models.FunnelStage.DOCS_RECEIVED.value,
        models.FunnelStage.ENROLLED.value,
    ]
    buckets: list[StageBucket] = []
    for stage in closing_stages:
        rows = (
            await db.execute(
                select(models.Lead)
                .where(models.Lead.funnel_stage == stage)
                .order_by(
                    models.Lead.funnel_stage_set_at.desc().nulls_last(),
                    models.Lead.id.desc(),
                )
                .limit(limit_per_stage)
            )
        ).scalars().all()
        total_count = (
            await db.execute(
                select(func.count()).select_from(models.Lead).where(
                    models.Lead.funnel_stage == stage
                )
            )
        ).scalar_one()
        buckets.append(
            StageBucket(
                stage=stage,
                count=int(total_count),
                leads=[_to_dto(l) for l in rows],
            )
        )

    # ── DNC list (dnd=True), newest first ──
    dnc_rows = (
        await db.execute(
            select(models.Lead)
            .where(models.Lead.dnd.is_(True))
            .order_by(
                models.Lead.last_disposition_at.desc().nulls_last(),
                models.Lead.id.desc(),
            )
            .limit(limit_per_stage * 2)
        )
    ).scalars().all()

    # ── Totals: count per funnel_stage + dnc + enrolled_pct ──
    stage_totals_rows = (
        await db.execute(
            select(models.Lead.funnel_stage, func.count())
            .group_by(models.Lead.funnel_stage)
        )
    ).all()
    totals = {row[0]: int(row[1]) for row in stage_totals_rows}
    total_dnc = (
        await db.execute(
            select(func.count()).select_from(models.Lead).where(models.Lead.dnd.is_(True))
        )
    ).scalar_one()
    totals["dnc"] = int(total_dnc)
    grand_total = (
        await db.execute(select(func.count()).select_from(models.Lead))
    ).scalar_one()
    enrolled = totals.get(models.FunnelStage.ENROLLED.value, 0)
    totals["enrolled_pct"] = (
        round(100.0 * enrolled / grand_total, 1) if grand_total else 0
    )

    return ClosuresResponse(
        closing=buckets,
        dnc=[_to_dto(l) for l in dnc_rows],
        totals=totals,
        server_time=datetime.now(timezone.utc),
    )
