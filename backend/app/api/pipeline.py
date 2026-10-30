"""Lead Pipeline — strategic kanban view.

Reads from the SAME source as /api/inbox and /api/closures so the three
pages stay perfectly consistent:

  Hot/Warm/Cold = Lead.lead_temperature ∈ {hot, warm, cold}
                  AND funnel_stage NOT IN (applied, docs_received, enrolled)
                  AND dnd = False

  Closing       = funnel_stage IN (applied, docs_received, enrolled)
                  AND dnd = False

DND leads are NOT shown here — they live on /closures under the DNC tab.
Unclassified leads (lead_temperature IS NULL) are NOT shown here either —
they haven't entered the pipeline yet. View them under /leads.

Server-side counts mean the totals match exactly with /api/inbox (which
shows the same buckets but capped at 100 cards per bucket for performance)
and /api/closures (which owns the Closing detail view).

DATE FILTER (?date=YYYY-MM-DD, IST):
  Switches the page semantics from "current state" to "classifications
  that happened on this date". Each bucket is then computed from
  `lead_events`:
    - Hot/Warm/Cold = leads whose LATEST temperature_set event on that
      day classified them as that temperature.
    - Closing      = leads whose funnel_stage_changed event on that day
      landed them in applied / docs_received / enrolled.
"""
from __future__ import annotations

from datetime import date as _date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_counsellor_or_admin
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["pipeline"])

CLOSING_STAGES = list(models.CLOSING_FUNNEL_STAGES)
IST = ZoneInfo("Asia/Kolkata")


def _ist_date_to_utc_window(date_str: str) -> tuple[datetime, datetime]:
    """YYYY-MM-DD (IST) → [start_utc, end_utc) covering that civil day."""
    try:
        d = _date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, f"Invalid date '{date_str}'; expected YYYY-MM-DD.")
    start_ist = datetime(d.year, d.month, d.day, tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    # Strip tzinfo so we compare against naive UTC stored in lead_events.created_at.
    return (
        start_ist.astimezone(timezone.utc).replace(tzinfo=None),
        end_ist.astimezone(timezone.utc).replace(tzinfo=None),
    )


class PipelineLead(BaseModel):
    id: int
    name: str | None
    phone: str
    program_interest: str | None
    lead_temperature: str | None
    funnel_stage: str
    last_disposition: str | None
    last_attempted_at: Optional[datetime] = None
    funnel_stage_set_at: Optional[datetime] = None
    attempt_count: int


class PipelineBucket(BaseModel):
    key: str         # 'hot' | 'warm' | 'cold' | 'closing'
    count: int       # total in DB (NOT length of `leads` which is paginated)
    leads: list[PipelineLead]


class PipelineResponse(BaseModel):
    buckets: list[PipelineBucket]
    total_in_pipeline: int     # sum of all bucket counts
    server_time: datetime
    # When the caller asked for a specific date this echoes it back, plus
    # the IST window translated to UTC. Frontend uses these to label the
    # banner ("Classifications on 2026-06-05"). None when date filter off.
    filter_date: str | None = None
    filter_window_start_utc: datetime | None = None
    filter_window_end_utc: datetime | None = None


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_dto(lead: models.Lead) -> PipelineLead:
    return PipelineLead(
        id=lead.id,
        name=lead.name,
        phone=lead.phone,
        program_interest=lead.program_interest,
        lead_temperature=lead.lead_temperature,
        funnel_stage=lead.funnel_stage,
        last_disposition=lead.last_disposition,
        last_attempted_at=_utc(lead.last_attempted_at),
        funnel_stage_set_at=_utc(lead.funnel_stage_set_at),
        attempt_count=lead.attempt_count or 0,
    )


@router.get("/pipeline", response_model=PipelineResponse)
async def pipeline_endpoint(
    date: str | None = Query(
        None,
        description="YYYY-MM-DD (IST). When set, buckets are computed from "
                    "classification events on that day instead of current state.",
    ),
    limit_per_bucket: int = Query(200, ge=1, le=1000),
    _user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> PipelineResponse:
    """Return the strategic pipeline kanban: hot / warm / cold / closing.

    Two modes:
      - date=None  : current state (Lead.lead_temperature + funnel_stage)
      - date=set   : buckets = leads classified on that IST calendar day,
                     read from lead_events.
    """
    if date:
        return await _pipeline_for_date(db, date, limit_per_bucket)
    return await _pipeline_current(db, limit_per_bucket)


async def _pipeline_current(
    db: AsyncSession, limit_per_bucket: int
) -> PipelineResponse:
    """Default: bucket leads by their CURRENT lead_temperature + funnel_stage."""
    base_filter = models.Lead.dnd.is_(False)
    not_closing = models.Lead.funnel_stage.not_in(CLOSING_STAGES)

    async def _bucket(key: str, temp_filter, ordering_col) -> PipelineBucket:
        if key == "closing":
            count_q = select(func.count()).select_from(models.Lead).where(
                base_filter, models.Lead.funnel_stage.in_(CLOSING_STAGES)
            )
            list_q = (
                select(models.Lead)
                .where(base_filter, models.Lead.funnel_stage.in_(CLOSING_STAGES))
                .order_by(ordering_col.desc().nulls_last(), models.Lead.id.desc())
                .limit(limit_per_bucket)
            )
        else:
            count_q = select(func.count()).select_from(models.Lead).where(
                base_filter, temp_filter, not_closing
            )
            list_q = (
                select(models.Lead)
                .where(base_filter, temp_filter, not_closing)
                .order_by(ordering_col.desc().nulls_last(), models.Lead.id.desc())
                .limit(limit_per_bucket)
            )
        total = (await db.execute(count_q)).scalar_one()
        rows = (await db.execute(list_q)).scalars().all()
        return PipelineBucket(key=key, count=int(total), leads=[_to_dto(l) for l in rows])

    hot = await _bucket("hot", models.Lead.lead_temperature == "hot", models.Lead.updated_at)
    warm = await _bucket("warm", models.Lead.lead_temperature == "warm", models.Lead.updated_at)
    cold = await _bucket("cold", models.Lead.lead_temperature == "cold", models.Lead.updated_at)
    closing = await _bucket("closing", None, models.Lead.funnel_stage_set_at)

    return PipelineResponse(
        buckets=[hot, warm, cold, closing],
        total_in_pipeline=hot.count + warm.count + cold.count + closing.count,
        server_time=datetime.now(timezone.utc),
    )


async def _pipeline_for_date(
    db: AsyncSession, date_str: str, limit_per_bucket: int
) -> PipelineResponse:
    """Bucket leads by classification events on a specific IST date.

    Implementation:
      • For temperature buckets, find every lead's LATEST temperature_set
        event on this date and group by payload['to'].
      • For closing bucket, leads whose funnel_stage_changed event on
        this date landed them in a closing stage (applied/docs/enrolled).
    """
    start_utc, end_utc = _ist_date_to_utc_window(date_str)

    # ─── temperature_set events on this day, latest per lead ───
    latest_temp_sub = (
        select(
            models.LeadEvent.lead_id,
            func.max(models.LeadEvent.id).label("ev_id"),
        )
        .where(
            models.LeadEvent.event_type == models.LeadEventType.TEMPERATURE_SET.value,
            models.LeadEvent.created_at >= start_utc,
            models.LeadEvent.created_at < end_utc,
        )
        .group_by(models.LeadEvent.lead_id)
        .subquery()
    )
    # SQLite JSON path — payload['to'] = 'hot' | 'warm' | 'cold'
    temp_expr = func.json_extract(models.LeadEvent.payload, "$.to")

    temp_rows = (
        await db.execute(
            select(models.LeadEvent.lead_id, temp_expr.label("temp"))
            .join(latest_temp_sub, models.LeadEvent.id == latest_temp_sub.c.ev_id)
        )
    ).all()
    # Map lead_id → that day's last classified temperature.
    lead_temp_on_day: dict[int, str] = {
        int(lead_id): str(temp) for lead_id, temp in temp_rows if temp
    }

    # ─── funnel_stage_changed events on this day, latest per lead ───
    latest_stage_sub = (
        select(
            models.LeadEvent.lead_id,
            func.max(models.LeadEvent.id).label("ev_id"),
        )
        .where(
            models.LeadEvent.event_type == models.LeadEventType.FUNNEL_STAGE_CHANGED.value,
            models.LeadEvent.created_at >= start_utc,
            models.LeadEvent.created_at < end_utc,
        )
        .group_by(models.LeadEvent.lead_id)
        .subquery()
    )
    stage_expr = func.json_extract(models.LeadEvent.payload, "$.to")
    stage_rows = (
        await db.execute(
            select(models.LeadEvent.lead_id, stage_expr.label("stage"))
            .join(latest_stage_sub, models.LeadEvent.id == latest_stage_sub.c.ev_id)
        )
    ).all()
    lead_stage_on_day: dict[int, str] = {
        int(lead_id): str(stage) for lead_id, stage in stage_rows if stage
    }

    # Closing bucket: only leads whose funnel_stage_changed event that day
    # landed them in CLOSING_STAGES.
    closing_ids = {
        lid for lid, st in lead_stage_on_day.items() if st in CLOSING_FUNNEL_STAGES_SET
    }
    # Temperature buckets: exclude leads we've already placed in closing
    # (a single lead can have BOTH events on the same day if a counsellor
    # took it from cold to applied that day; the closing event wins).
    hot_ids = {
        lid for lid, t in lead_temp_on_day.items()
        if t == "hot" and lid not in closing_ids
    }
    warm_ids = {
        lid for lid, t in lead_temp_on_day.items()
        if t == "warm" and lid not in closing_ids
    }
    cold_ids = {
        lid for lid, t in lead_temp_on_day.items()
        if t == "cold" and lid not in closing_ids
    }

    async def _materialise(ids: set[int], limit: int) -> tuple[int, list[PipelineLead]]:
        if not ids:
            return 0, []
        rows = (
            await db.execute(
                select(models.Lead)
                .where(models.Lead.id.in_(ids))
                .order_by(models.Lead.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        return len(ids), [_to_dto(l) for l in rows]

    hot_total, hot_leads = await _materialise(hot_ids, limit_per_bucket)
    warm_total, warm_leads = await _materialise(warm_ids, limit_per_bucket)
    cold_total, cold_leads = await _materialise(cold_ids, limit_per_bucket)
    closing_total, closing_leads = await _materialise(closing_ids, limit_per_bucket)

    buckets = [
        PipelineBucket(key="hot", count=hot_total, leads=hot_leads),
        PipelineBucket(key="warm", count=warm_total, leads=warm_leads),
        PipelineBucket(key="cold", count=cold_total, leads=cold_leads),
        PipelineBucket(key="closing", count=closing_total, leads=closing_leads),
    ]
    return PipelineResponse(
        buckets=buckets,
        total_in_pipeline=hot_total + warm_total + cold_total + closing_total,
        server_time=datetime.now(timezone.utc),
        filter_date=date_str,
        filter_window_start_utc=start_utc.replace(tzinfo=timezone.utc),
        filter_window_end_utc=end_utc.replace(tzinfo=timezone.utc),
    )


CLOSING_FUNNEL_STAGES_SET = set(CLOSING_STAGES)
