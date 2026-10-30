"""Counsellor Inbox — kanban + lock + timeline (Phase 1 of the closing workflow).

The counsellor opens /inbox, sees leads bucketed by AI-classified temperature
(hot / warm / cold). Clicking a card calls POST /api/leads/{id}/lock; if the
acquire succeeds, the counsellor has 5 minutes (extendable up to 15) to make
a manual call and submit a disposition + notes via POST /unlock. Hot leads
unclaimed for >30 min are flagged for admin attention (the SLA badge is
rendered client-side).

All endpoints are admin-or-counsellor. Counsellor sees the same list of
leads as admin — Phase 1 doesn't filter by assignment.
"""
from __future__ import annotations

from datetime import date as _date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_counsellor_or_admin
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["inbox"])

# ────────────────────────────── policy ────────────────────────────────
LOCK_DURATION = timedelta(minutes=5)
MAX_EXTENSIONS = 2          # → 5 + 5 + 5 = 15 min max
HOT_SLA = timedelta(minutes=30)
MIN_NOTES_LEN = 10          # chars required, except for NOTES_OPTIONAL_DISPOSITIONS
INBOX_PAGE_SIZE = 100       # per bucket — applied to LIVE mode only
INBOX_HISTORY_CAP = 500     # per bucket — applied when a date range is set
IST = ZoneInfo("Asia/Kolkata")


def _ist_date_range_to_utc(from_str: str, to_str: str) -> tuple[datetime, datetime]:
    """Both ends inclusive: from=to=YYYY-MM-DD covers all of that IST day.
    Returns naive UTC datetimes (matches how lead_events.created_at is stored)."""
    try:
        d_from = _date.fromisoformat(from_str)
        d_to = _date.fromisoformat(to_str)
    except ValueError as e:
        raise HTTPException(400, f"Invalid date: {e}") from None
    if d_to < d_from:
        raise HTTPException(400, "`to` must be on or after `from`.")
    start_ist = datetime(d_from.year, d_from.month, d_from.day, tzinfo=IST)
    end_ist = datetime(d_to.year, d_to.month, d_to.day, tzinfo=IST) + timedelta(days=1)
    return (
        start_ist.astimezone(timezone.utc).replace(tzinfo=None),
        end_ist.astimezone(timezone.utc).replace(tzinfo=None),
    )


# ────────────────────────────── DTOs ──────────────────────────────────
class LeadCard(BaseModel):
    id: int
    name: str | None
    phone: str                     # raw — frontend masks for counsellors
    program_interest: str | None
    lead_temperature: str | None   # 'hot' / 'warm' / 'cold' / None
    last_disposition: str | None
    last_attempted_at: datetime | None
    locked_by_user_id: int | None
    locked_by_name: str | None
    locked_until_at: datetime | None
    lock_extensions: int
    hot_since: datetime | None     # for SLA timer on hot cards
    # surfaced flags
    is_locked_by_me: bool = False
    is_locked_by_other: bool = False


class InboxResponse(BaseModel):
    hot: list[LeadCard]
    warm: list[LeadCard]
    cold: list[LeadCard]
    my_active: list[LeadCard]      # currently locked by ME — top of the page
    server_time: datetime
    # Echoed when the caller used a date-range filter; lets the frontend
    # show "Showing leads I worked on between 2026-06-09 → 2026-06-11".
    filter_from: str | None = None
    filter_to: str | None = None


class LockResponse(BaseModel):
    lead_id: int
    locked_by_user_id: int
    locked_until_at: datetime
    lock_extensions: int
    server_time: datetime


class UnlockBody(BaseModel):
    disposition: str
    notes: str | None = Field(default=None, max_length=4000)
    callback_at: datetime | None = None
    next_temperature: str | None = None   # optional manual override
    place_manual_call: bool = False       # cosmetic flag → MANUAL_CALL_PLACED event


class TimelineEvent(BaseModel):
    id: int
    event_type: str
    actor_kind: str
    actor_user_id: int | None
    actor_name: str | None
    payload: dict[str, Any] | None
    created_at: datetime


class TimelineResponse(BaseModel):
    lead_id: int
    events: list[TimelineEvent]


# ───────────────────── helpers ─────────────────────────────────────────
async def _user_name(db: AsyncSession, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = await db.execute(
        select(models.User.name).where(models.User.id == user_id)
    )
    return row.scalar_one_or_none()


async def _hot_since(db: AsyncSession, lead_id: int) -> datetime | None:
    """When did this lead most recently become hot? Used for the 30-min SLA."""
    row = await db.execute(
        select(models.LeadEvent.created_at)
        .where(
            models.LeadEvent.lead_id == lead_id,
            models.LeadEvent.event_type == models.LeadEventType.TEMPERATURE_SET.value,
        )
        .order_by(models.LeadEvent.created_at.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


def _to_card(
    lead: models.Lead,
    me_id: int,
    locked_by_name: str | None,
    hot_since: datetime | None,
) -> LeadCard:
    # locked_until_at is stored NAIVE UTC — re-attach so the wire format ends
    # with +00:00 (same fix as the Team page).
    lu = lead.locked_until_at
    if lu is not None and lu.tzinfo is None:
        lu = lu.replace(tzinfo=timezone.utc)
    return LeadCard(
        id=lead.id,
        name=lead.name,
        phone=lead.phone,
        program_interest=lead.program_interest,
        lead_temperature=lead.lead_temperature,
        last_disposition=lead.last_disposition,
        last_attempted_at=lead.last_attempted_at,
        locked_by_user_id=lead.locked_by_user_id,
        locked_by_name=locked_by_name,
        locked_until_at=lu,
        lock_extensions=lead.lock_extensions or 0,
        hot_since=hot_since,
        is_locked_by_me=(lead.locked_by_user_id == me_id and lu is not None),
        is_locked_by_other=(
            lead.locked_by_user_id is not None
            and lead.locked_by_user_id != me_id
            and lu is not None
        ),
    )


async def _is_lock_live(lead: models.Lead) -> bool:
    """A lock is 'live' iff locked_until_at is in the future."""
    if lead.locked_until_at is None or lead.locked_by_user_id is None:
        return False
    until = lead.locked_until_at
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < until


async def _emit(
    db: AsyncSession,
    lead_id: int,
    *,
    event_type: str,
    actor_kind: str,
    actor_user_id: int | None,
    payload: dict[str, Any] | None = None,
) -> None:
    ev = models.LeadEvent(
        lead_id=lead_id,
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        event_type=event_type,
        payload=payload,
        created_at=datetime.utcnow(),
    )
    db.add(ev)


# ───────────────────── INBOX ───────────────────────────────────────────
@router.get("/inbox", response_model=InboxResponse)
async def get_inbox(
    date_from: str | None = Query(
        None, alias="from",
        description="History mode: only show leads I disposition-saved on/after "
                    "this IST date (YYYY-MM-DD). Both `from` and `to` required.",
    ),
    date_to: str | None = Query(
        None, alias="to",
        description="History mode: end of the IST date range (inclusive).",
    ),
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> InboxResponse:
    """Return the kanban: leads bucketed by temperature.

    Two modes:
      • LIVE (default, no date params): every lead in the system, bucketed
        by current Lead.lead_temperature, capped at INBOX_PAGE_SIZE/bucket.
      • HISTORY (from+to): only leads where I (the requesting user) saved
        a disposition (LOCK_RELEASED event) in the IST date range. Same
        hot/warm/cold buckets, capped at INBOX_HISTORY_CAP/bucket. Useful
        for "show me what I worked on yesterday".

    Within each bucket: hot-since asc, then updated_at desc, then id desc.
    Leads currently locked by the requester land in `my_active` regardless
    of which mode is on.
    """
    # Validate the from/to pair — must both be present or both absent.
    if (date_from and not date_to) or (date_to and not date_from):
        raise HTTPException(
            400, "`from` and `to` must be provided together for history mode.",
        )

    base = select(models.Lead).where(
        models.Lead.dnd.is_(False),
    )

    # History mode: restrict the base query to leads I disposition-saved
    # in the IST window. Cap raised because the user is explicitly
    # asking for a complete view of their work.
    history_lead_ids: set[int] | None = None
    bucket_cap = INBOX_PAGE_SIZE
    if date_from and date_to:
        start_utc, end_utc = _ist_date_range_to_utc(date_from, date_to)
        rows = await db.execute(
            select(func.distinct(models.LeadEvent.lead_id)).where(
                models.LeadEvent.actor_user_id == user.id,
                models.LeadEvent.event_type == models.LeadEventType.LOCK_RELEASED.value,
                models.LeadEvent.created_at >= start_utc,
                models.LeadEvent.created_at < end_utc,
            )
        )
        history_lead_ids = {int(r) for r in rows.scalars() if r is not None}
        bucket_cap = INBOX_HISTORY_CAP

    async def _fetch_bucket(temperature: str) -> list[models.Lead]:
        # 2026-06-11: hide NULL-name leads from the kanban buckets.
        # ~6,300 rows had their Excel-mangled names ('9.18E+11') nulled by
        # `_null_excel_garbage_names.py`; their data stays in the DB but
        # the user explicitly asked to not surface them as actionable
        # cards. `my_active` is NOT filtered — if a counsellor already
        # locked a now-nameless lead it still surfaces in their queue.
        q = base.where(
            models.Lead.lead_temperature == temperature,
            models.Lead.name.is_not(None),
        )
        if history_lead_ids is not None:
            if not history_lead_ids:
                return []
            q = q.where(models.Lead.id.in_(history_lead_ids))
        rows = await db.execute(
            q.order_by(models.Lead.updated_at.desc().nulls_last(), models.Lead.id.desc())
             .limit(bucket_cap)
        )
        return list(rows.scalars().all())

    hot_rows = await _fetch_bucket("hot")
    warm_rows = await _fetch_bucket("warm")
    cold_rows = await _fetch_bucket("cold")

    # Leads I'm holding right now (across any temperature).
    my_rows = (
        await db.execute(
            base.where(
                models.Lead.locked_by_user_id == user.id,
                models.Lead.locked_until_at.is_not(None),
            ).order_by(models.Lead.locked_until_at.asc())
        )
    ).scalars().all()

    # Resolve locker names + hot-since in one shot
    all_lead_ids = {l.id for l in hot_rows + warm_rows + cold_rows + list(my_rows)}
    locker_ids = {
        l.locked_by_user_id
        for l in hot_rows + warm_rows + cold_rows + list(my_rows)
        if l.locked_by_user_id
    }
    locker_names: dict[int, str] = {}
    if locker_ids:
        rows = await db.execute(
            select(models.User.id, models.User.name).where(models.User.id.in_(locker_ids))
        )
        locker_names = {uid: name for uid, name in rows}

    hot_since_map: dict[int, datetime] = {}
    if all_lead_ids:
        rows = await db.execute(
            select(
                models.LeadEvent.lead_id,
                func.max(models.LeadEvent.created_at),
            )
            .where(
                models.LeadEvent.lead_id.in_(all_lead_ids),
                models.LeadEvent.event_type == models.LeadEventType.TEMPERATURE_SET.value,
            )
            .group_by(models.LeadEvent.lead_id)
        )
        for lead_id, ts in rows:
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            hot_since_map[lead_id] = ts

    def _build(rows: list[models.Lead]) -> list[LeadCard]:
        out = []
        for l in rows:
            # If lock is expired, don't surface a stale lock badge.
            if l.locked_until_at is not None:
                lu = l.locked_until_at
                if lu.tzinfo is None:
                    lu = lu.replace(tzinfo=timezone.utc)
                if lu < datetime.now(timezone.utc):
                    l.locked_by_user_id = None
                    l.locked_until_at = None
            out.append(
                _to_card(
                    l,
                    me_id=user.id,
                    locked_by_name=locker_names.get(l.locked_by_user_id or 0),
                    hot_since=hot_since_map.get(l.id),
                )
            )
        return out

    my_active = _build(list(my_rows))
    my_ids = {c.id for c in my_active}
    # Don't double-list a lead I'm holding in its temperature bucket.
    hot = [c for c in _build(hot_rows) if c.id not in my_ids]
    warm = [c for c in _build(warm_rows) if c.id not in my_ids]
    cold = [c for c in _build(cold_rows) if c.id not in my_ids]

    return InboxResponse(
        hot=hot,
        warm=warm,
        cold=cold,
        my_active=my_active,
        server_time=datetime.now(timezone.utc),
        filter_from=date_from,
        filter_to=date_to,
    )


# ───────────────────── LOCK ───────────────────────────────────────────
@router.post("/leads/{lead_id}/lock", response_model=LockResponse)
async def lock_lead(
    lead_id: int,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> LockResponse:
    """Atomically acquire the lead lock. 409 if someone else holds it.

    The UPDATE..WHERE clause is the atomic acquire: SQLite serializes
    writes, so even with concurrent requests only one row gets bumped.
    """
    now = datetime.utcnow()
    expires_at = now + LOCK_DURATION

    # Atomic acquire: only succeeds if lock is unset OR expired OR already mine.
    stmt = (
        update(models.Lead)
        .where(
            models.Lead.id == lead_id,
            or_(
                models.Lead.locked_by_user_id.is_(None),
                models.Lead.locked_until_at.is_(None),
                models.Lead.locked_until_at < now,
                models.Lead.locked_by_user_id == user.id,
            ),
        )
        .values(
            locked_by_user_id=user.id,
            locked_until_at=expires_at,
            lock_extensions=0,
        )
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        # Either the lead doesn't exist or it's locked by someone else.
        row = await db.execute(
            select(models.Lead, models.User.name).join(
                models.User, models.User.id == models.Lead.locked_by_user_id, isouter=True
            ).where(models.Lead.id == lead_id)
        )
        rec = row.first()
        if rec is None:
            raise HTTPException(404, "Lead not found")
        lead, name = rec
        raise HTTPException(
            409,
            {
                "error": "lead_locked",
                "locked_by_user_id": lead.locked_by_user_id,
                "locked_by_name": name,
                "locked_until_at": (
                    lead.locked_until_at.isoformat()
                    if lead.locked_until_at
                    else None
                ),
            },
        )

    await _emit(
        db,
        lead_id,
        event_type=models.LeadEventType.LOCK_ACQUIRED.value,
        actor_kind=models.LeadEventActorKind.COUNSELLOR.value
        if user.role == "counsellor"
        else models.LeadEventActorKind.ADMIN.value,
        actor_user_id=user.id,
        payload={"duration_minutes": int(LOCK_DURATION.total_seconds() // 60)},
    )
    await db.commit()
    return LockResponse(
        lead_id=lead_id,
        locked_by_user_id=user.id,
        locked_until_at=expires_at.replace(tzinfo=timezone.utc),
        lock_extensions=0,
        server_time=datetime.now(timezone.utc),
    )


# ───────────────────── EXTEND ─────────────────────────────────────────
@router.post("/leads/{lead_id}/extend", response_model=LockResponse)
async def extend_lock(
    lead_id: int,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> LockResponse:
    """Add 5 more minutes to my own lock. Capped at MAX_EXTENSIONS."""
    row = await db.execute(
        select(models.Lead).where(models.Lead.id == lead_id)
    )
    lead = row.scalar_one_or_none()
    if lead is None:
        raise HTTPException(404, "Lead not found")
    if lead.locked_by_user_id != user.id or not await _is_lock_live(lead):
        raise HTTPException(409, "You don't hold this lock")
    if (lead.lock_extensions or 0) >= MAX_EXTENSIONS:
        raise HTTPException(
            429,
            f"Already extended {MAX_EXTENSIONS}× — max lock time reached. "
            "Submit your disposition + notes to close out.",
        )

    new_until = (
        lead.locked_until_at + LOCK_DURATION
        if lead.locked_until_at
        else datetime.utcnow() + LOCK_DURATION
    )
    new_count = (lead.lock_extensions or 0) + 1
    lead.locked_until_at = new_until
    lead.lock_extensions = new_count
    await _emit(
        db,
        lead_id,
        event_type=models.LeadEventType.LOCK_EXTENDED.value,
        actor_kind=models.LeadEventActorKind.COUNSELLOR.value
        if user.role == "counsellor"
        else models.LeadEventActorKind.ADMIN.value,
        actor_user_id=user.id,
        payload={"extension_n": new_count, "new_until_utc": new_until.isoformat()},
    )
    await db.commit()
    return LockResponse(
        lead_id=lead_id,
        locked_by_user_id=user.id,
        locked_until_at=new_until.replace(tzinfo=timezone.utc),
        lock_extensions=new_count,
        server_time=datetime.now(timezone.utc),
    )


# ───────────────────── UNLOCK ─────────────────────────────────────────
@router.post("/leads/{lead_id}/unlock", response_model=LeadCard)
async def unlock_lead(
    lead_id: int,
    body: UnlockBody,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> LeadCard:
    """Release the lock with a disposition + (usually) notes.

    Validations:
      - I hold the lock (or admin override allowed)
      - disposition is a valid Disposition enum value
      - notes len >= MIN_NOTES_LEN unless disposition ∈ NOTES_OPTIONAL_DISPOSITIONS
      - callback_at is required iff disposition == CALLBACK_REQUESTED
    """
    valid_dispositions = {d.value for d in models.Disposition}
    if body.disposition not in valid_dispositions:
        raise HTTPException(422, f"Unknown disposition: {body.disposition!r}")
    if (
        body.disposition not in models.NOTES_OPTIONAL_DISPOSITIONS
        and (not body.notes or len(body.notes.strip()) < MIN_NOTES_LEN)
    ):
        raise HTTPException(
            422,
            f"Notes must be at least {MIN_NOTES_LEN} characters for this disposition.",
        )
    if (
        body.disposition == models.Disposition.CALLBACK_REQUESTED.value
        and body.callback_at is None
    ):
        raise HTTPException(422, "callback_at is required when disposition is callback_requested.")

    row = await db.execute(select(models.Lead).where(models.Lead.id == lead_id))
    lead = row.scalar_one_or_none()
    if lead is None:
        raise HTTPException(404, "Lead not found")
    # Only the lock-holder or an admin can release.
    if lead.locked_by_user_id != user.id and user.role != "admin":
        raise HTTPException(403, "You don't hold this lock")

    actor_kind = (
        models.LeadEventActorKind.COUNSELLOR.value
        if user.role == "counsellor"
        else models.LeadEventActorKind.ADMIN.value
    )

    # Persist disposition + notes
    now_utc = datetime.utcnow()
    lead.last_disposition = body.disposition
    lead.last_disposition_at = now_utc
    if body.notes and body.notes.strip():
        existing = (lead.notes or "").rstrip()
        stamp = now_utc.strftime("%Y-%m-%d %H:%M UTC")
        header = f"\n\n[{stamp}] {user.name}:"
        lead.notes = f"{existing}{header}\n{body.notes.strip()}".lstrip()

    # Optional manual temperature override
    if body.next_temperature in {"hot", "warm", "cold"}:
        old_t = lead.lead_temperature
        lead.lead_temperature = body.next_temperature
        await _emit(
            db,
            lead_id,
            event_type=models.LeadEventType.TEMPERATURE_OVERRIDDEN.value,
            actor_kind=actor_kind,
            actor_user_id=user.id,
            payload={"from": old_t, "to": body.next_temperature},
        )

    # ── Phase 2: apply the disposition → (funnel_stage, status, dnd) mapping ──
    mapping = models.DISPOSITION_MAPPING.get(body.disposition)
    if mapping is not None:
        new_stage, new_status, set_dnd = mapping
        # funnel_stage: only write if mapping has a non-None target AND it's
        # actually a change. Prevents redundant timeline noise.
        if new_stage is not None and lead.funnel_stage != new_stage.value:
            old_stage = lead.funnel_stage
            lead.funnel_stage = new_stage.value
            lead.funnel_stage_set_at = now_utc
            await _emit(
                db,
                lead_id,
                event_type=models.LeadEventType.FUNNEL_STAGE_CHANGED.value,
                actor_kind=actor_kind,
                actor_user_id=user.id,
                payload={
                    "from": old_stage,
                    "to": new_stage.value,
                    "via_disposition": body.disposition,
                },
            )
        # workflow status: always overwrite (it's a "latest outcome" field)
        if lead.status != new_status:
            lead.status = new_status
        # DNC: mark for do-not-call. Once set, never auto-unset.
        if set_dnd and not lead.dnd:
            lead.dnd = True
            await _emit(
                db,
                lead_id,
                event_type=models.LeadEventType.DND_SET.value,
                actor_kind=actor_kind,
                actor_user_id=user.id,
                payload={"reason": body.disposition},
            )

    # Release the lock
    lead.locked_by_user_id = None
    lead.locked_until_at = None
    lead.lock_extensions = 0

    # Emit timeline events
    await _emit(
        db,
        lead_id,
        event_type=models.LeadEventType.DISPOSITION_SET.value,
        actor_kind=actor_kind,
        actor_user_id=user.id,
        payload={"disposition": body.disposition},
    )
    if body.notes and body.notes.strip():
        await _emit(
            db,
            lead_id,
            event_type=models.LeadEventType.NOTES_UPDATED.value,
            actor_kind=actor_kind,
            actor_user_id=user.id,
            payload={"length": len(body.notes.strip())},
        )
    if body.place_manual_call:
        await _emit(
            db,
            lead_id,
            event_type=models.LeadEventType.MANUAL_CALL_PLACED.value,
            actor_kind=actor_kind,
            actor_user_id=user.id,
            payload=None,
        )
    if body.disposition == models.Disposition.CALLBACK_REQUESTED.value:
        # Create the actual callback row too (the existing /callbacks page surfaces it).
        cb = models.Callback(
            lead_id=lead_id,
            counsellor_id=user.id,
            scheduled_at=body.callback_at,
            status="pending",
            notes=body.notes,
        )
        db.add(cb)
        await _emit(
            db,
            lead_id,
            event_type=models.LeadEventType.CALLBACK_SCHEDULED.value,
            actor_kind=actor_kind,
            actor_user_id=user.id,
            payload={"scheduled_at": body.callback_at.isoformat()},
        )

    await _emit(
        db,
        lead_id,
        event_type=models.LeadEventType.LOCK_RELEASED.value,
        actor_kind=actor_kind,
        actor_user_id=user.id,
        payload={
            "disposition": body.disposition,
            "notes_provided": bool(body.notes and body.notes.strip()),
        },
    )
    await db.commit()
    await db.refresh(lead)
    return _to_card(lead, me_id=user.id, locked_by_name=None, hot_since=None)


# ───────────────────── TIMELINE ──────────────────────────────────────
@router.get("/leads/{lead_id}/timeline", response_model=TimelineResponse)
async def get_timeline(
    lead_id: int,
    limit: int = Query(200, ge=1, le=1000),
    _user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> TimelineResponse:
    """Return the event log for one lead, newest first."""
    row = await db.execute(
        select(models.LeadEvent.lead_id).where(models.LeadEvent.lead_id == lead_id).limit(1)
    )
    if not row.scalar_one_or_none():
        # Either the lead has no events yet OR it doesn't exist. Confirm.
        lead = await db.execute(select(models.Lead.id).where(models.Lead.id == lead_id))
        if not lead.scalar_one_or_none():
            raise HTTPException(404, "Lead not found")

    events_rows = await db.execute(
        select(models.LeadEvent, models.User.name)
        .join(
            models.User,
            models.User.id == models.LeadEvent.actor_user_id,
            isouter=True,
        )
        .where(models.LeadEvent.lead_id == lead_id)
        .order_by(models.LeadEvent.created_at.desc(), models.LeadEvent.id.desc())
        .limit(limit)
    )
    events: list[TimelineEvent] = []
    for ev, actor_name in events_rows:
        ts = ev.created_at
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        events.append(
            TimelineEvent(
                id=ev.id,
                event_type=ev.event_type,
                actor_kind=ev.actor_kind,
                actor_user_id=ev.actor_user_id,
                actor_name=actor_name,
                payload=ev.payload,
                created_at=ts,
            )
        )
    return TimelineResponse(lead_id=lead_id, events=events)
