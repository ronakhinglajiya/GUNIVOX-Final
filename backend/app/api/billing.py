"""Billing REST — summary + per-call list, post-epoch only.

  GET /api/billing/summary         → totals + tariff info
  GET /api/billing/calls           → paginated per-call list with amount

Pricing rules live in `app/billing.py` (single source of truth).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.billing import (
    BILLING_EPOCH,
    RATE_INR_PER_MINUTE,
    amount_inr,
    billable_seconds,
    is_billable_period,
)
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/api/billing", tags=["billing"])


# ─── Schemas ──────────────────────────────────────────────────────────


class BillingSummary(BaseModel):
    rate_inr_per_minute: float
    epoch_iso: str
    total_calls: int           # billable calls (post-epoch with duration ≥ 1s)
    total_calls_excluded: int  # pre-epoch calls excluded from billing
    total_billable_seconds: int
    total_billable_minutes: float
    total_amount_inr: float
    calls_today: int
    amount_today_inr: float


class BillingCallRow(BaseModel):
    call_id: int
    lead_id: int | None
    phone: str | None
    caller_name: str | None
    started_at: datetime
    duration_s: float
    billable_seconds: int
    amount_inr: float


class BillingCallList(BaseModel):
    total: int
    items: list[BillingCallRow]


# ─── Routes ───────────────────────────────────────────────────────────


@router.get("/summary", response_model=BillingSummary)
async def billing_summary(
    _: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> BillingSummary:
    # Pull ALL calls so we can show both billable totals AND the count of
    # pre-epoch calls we're deliberately ignoring (transparency).
    rows = (
        await db.execute(
            select(
                models.Call.id,
                models.Call.created_at,
                models.Call.duration_s,
            )
        )
    ).all()

    total_calls = 0
    total_calls_excluded = 0
    total_secs = 0
    total_amt = 0.0
    calls_today = 0
    amount_today = 0.0

    # "Today" in UTC — UI converts to IST for display.
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    for cid, created_at, duration_s in rows:
        if not is_billable_period(created_at):
            total_calls_excluded += 1
            continue
        secs = billable_seconds(duration_s)
        if secs == 0:
            # Zero-duration calls (never connected) — still post-epoch but
            # we don't count them in `total_calls` since they're free.
            continue
        amt = amount_inr(duration_s)
        total_calls += 1
        total_secs += secs
        total_amt += amt
        if created_at is not None:
            ca = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            if ca >= today_start:
                calls_today += 1
                amount_today += amt

    return BillingSummary(
        rate_inr_per_minute=RATE_INR_PER_MINUTE,
        epoch_iso=BILLING_EPOCH.isoformat(),
        total_calls=total_calls,
        total_calls_excluded=total_calls_excluded,
        total_billable_seconds=total_secs,
        total_billable_minutes=round(total_secs / 60.0, 2),
        total_amount_inr=round(total_amt, 2),
        calls_today=calls_today,
        amount_today_inr=round(amount_today, 2),
    )


@router.get("/calls", response_model=BillingCallList)
async def billing_calls(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> BillingCallList:
    """Per-call billing rows, post-epoch only, newest first."""
    base = (
        select(models.Call, models.Lead)
        .outerjoin(models.Lead, models.Lead.id == models.Call.lead_id)
        .where(models.Call.created_at >= BILLING_EPOCH)
        .order_by(models.Call.id.desc())
    )
    total = (
        await db.execute(
            select(func.count(models.Call.id)).where(models.Call.created_at >= BILLING_EPOCH)
        )
    ).scalar_one()
    rows = (await db.execute(base.limit(limit).offset(offset))).all()

    items: list[BillingCallRow] = []
    for call, lead in rows:
        dur = float(call.duration_s or 0)
        items.append(BillingCallRow(
            call_id=call.id,
            lead_id=call.lead_id,
            phone=getattr(lead, "phone", None) if lead else None,
            caller_name=call.caller_name or (getattr(lead, "name", None) if lead else None),
            started_at=call.created_at,
            duration_s=dur,
            billable_seconds=billable_seconds(dur),
            amount_inr=amount_inr(dur),
        ))
    return BillingCallList(total=int(total), items=items)
