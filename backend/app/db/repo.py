"""Thin repository helpers — small, focused DB ops the API + bridge call.

Kept as plain functions (not classes) to stay testable. All take an AsyncSession.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models


# --- Users ---

async def get_user_by_email(db: AsyncSession, email: str) -> models.User | None:
    row = await db.execute(select(models.User).where(models.User.email == email))
    return row.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: int) -> models.User | None:
    row = await db.execute(select(models.User).where(models.User.id == user_id))
    return row.scalar_one_or_none()


async def find_available_counsellor(
    db: AsyncSession,
) -> models.User | None:
    """Pick an available counsellor for warm transfer.

    Round-robin-ish: returns the available counsellor with the smallest id.
    Future: track last-handoff-at on User and pick least-recently-used.
    """
    row = await db.execute(
        select(models.User).where(
            (models.User.role == "counsellor")
            & (models.User.is_available.is_(True))
            & (models.User.is_active.is_(True))
            & (models.User.phone.isnot(None))
        ).order_by(models.User.id.asc()).limit(1)
    )
    return row.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password_hash: str,
    name: str,
    role: str = "counsellor",
    phone: str | None = None,
) -> models.User:
    user = models.User(
        email=email,
        password_hash=password_hash,
        name=name,
        role=role,
        phone=phone,
    )
    db.add(user)
    await db.flush()
    return user


# --- Leads ---

async def get_lead(db: AsyncSession, lead_id: int) -> models.Lead | None:
    row = await db.execute(select(models.Lead).where(models.Lead.id == lead_id))
    return row.scalar_one_or_none()


async def get_lead_by_phone(db: AsyncSession, phone: str) -> models.Lead | None:
    row = await db.execute(select(models.Lead).where(models.Lead.phone == phone))
    return row.scalar_one_or_none()


async def upsert_lead(db: AsyncSession, **fields: Any) -> tuple[models.Lead, bool]:
    """Insert or update by phone. Returns (lead, created_flag)."""
    phone = fields["phone"]
    existing = await get_lead_by_phone(db, phone)
    if existing:
        for k, v in fields.items():
            if v is not None:
                setattr(existing, k, v)
        await db.flush()
        return existing, False
    lead = models.Lead(**fields)
    db.add(lead)
    await db.flush()
    return lead, True


async def list_leads(
    db: AsyncSession,
    *,
    q: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[models.Lead], int]:
    stmt = select(models.Lead)
    count_stmt = select(func.count()).select_from(models.Lead)
    if q:
        like = f"%{q}%"
        cond = or_(
            models.Lead.name.ilike(like),
            models.Lead.phone.ilike(like),
            models.Lead.email.ilike(like),
            models.Lead.program_interest.ilike(like),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if status:
        stmt = stmt.where(models.Lead.status == status)
        count_stmt = count_stmt.where(models.Lead.status == status)
    stmt = stmt.order_by(models.Lead.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return list(rows), int(total)


async def update_lead(
    db: AsyncSession, lead_id: int, **fields: Any
) -> models.Lead | None:
    lead = await get_lead(db, lead_id)
    if not lead:
        return None
    for k, v in fields.items():
        if v is not None:
            setattr(lead, k, v)
    await db.flush()
    return lead


async def delete_lead(db: AsyncSession, lead_id: int) -> bool:
    lead = await get_lead(db, lead_id)
    if not lead:
        return False
    await db.delete(lead)
    await db.flush()
    return True


# --- Calls ---

async def create_call(
    db: AsyncSession,
    *,
    lead_id: int,
    vobiz_call_id: str | None = None,
    language: str | None = None,
    campaign_id: int | None = None,
) -> models.Call:
    # Pre-fill caller_name from the Lead if known (e.g. lead came in via CSV
    # with first/last name in cols E + G). The Calls list, dialer, and PDFs
    # can then display the real name BEFORE the bridge ever runs — instead
    # of "Lead #N". The bridge's finalize_call won't overwrite this.
    initial_caller_name: str | None = None
    lead = await db.get(models.Lead, lead_id)
    if lead and (lead.name or "").strip():
        initial_caller_name = lead.name.strip()

    call = models.Call(
        lead_id=lead_id,
        vobiz_call_id=vobiz_call_id,
        language=language,
        campaign_id=campaign_id,
        started_at=datetime.now(timezone.utc),
        caller_name=initial_caller_name,
    )
    db.add(call)
    await db.flush()
    return call


async def get_call(db: AsyncSession, call_id: int) -> models.Call | None:
    row = await db.execute(select(models.Call).where(models.Call.id == call_id))
    return row.scalar_one_or_none()


async def get_call_by_vobiz_id(
    db: AsyncSession, vobiz_call_id: str
) -> models.Call | None:
    row = await db.execute(
        select(models.Call).where(models.Call.vobiz_call_id == vobiz_call_id)
    )
    return row.scalar_one_or_none()


async def finalize_call(
    db: AsyncSession,
    call_id: int,
    *,
    duration_s: float | None = None,
    outcome: str | None = None,
    recording_path: str | None = None,
    ttfa_p50_ms: int | None = None,
    summary: str | None = None,
    lead_temperature: str | None = None,
    lead_program: str | None = None,
    lead_reasons: str | None = None,
    caller_name: str | None = None,
) -> None:
    values: dict[str, object] = {
        "ended_at": datetime.now(timezone.utc),
        "duration_s": duration_s,
        "outcome": outcome,
        "recording_path": recording_path,
        "ttfa_p50_ms": ttfa_p50_ms,
        "summary": summary,
    }
    # Only overwrite lead fields if we have values — keeps a re-finalize
    # call from clobbering an earlier good classification.
    if lead_temperature is not None:
        values["lead_temperature"] = lead_temperature
    if lead_program is not None:
        values["lead_program"] = lead_program
    if lead_reasons is not None:
        values["lead_reasons"] = lead_reasons
    if caller_name is not None:
        # COALESCE preserves the existing caller_name if it was set at
        # create_call time (e.g. CSV-imported lead with name in cols E + G).
        # Transcribed names from mid-call STT don't overwrite the trusted
        # CSV name — but if Call.caller_name was NULL (manual dialer with
        # no lead name), the transcribed name still fills it in.
        values["caller_name"] = func.coalesce(models.Call.caller_name, caller_name)
    await db.execute(
        update(models.Call).where(models.Call.id == call_id).values(**values)
    )

    # Propagate the resolved caller name to the persistent Lead record so it
    # isn't lost when the Call row ages out of view. Without this the agent
    # would greet the caller by name on the call but the Lead in the pipeline
    # stayed nameless (data loss). Only fill an EMPTY lead name — never
    # clobber a name a human already entered.
    if caller_name:
        call_row = await db.get(models.Call, call_id)
        if call_row is not None and call_row.lead_id is not None:
            lead = await db.get(models.Lead, call_row.lead_id)
            if lead is not None and not (lead.name or "").strip():
                lead.name = caller_name

    # Mirror the AI-classified temperature onto the persistent Lead row
    # so the Inbox kanban can filter without joining calls every page-
    # load — and emit a timeline event so the counsellor sees WHEN it
    # became hot. Skip if classifier didn't run (lead_temperature None).
    if lead_temperature is not None:
        call_row = await db.get(models.Call, call_id)
        if call_row is not None and call_row.lead_id is not None:
            lead = await db.get(models.Lead, call_row.lead_id)
            if lead is not None and lead.lead_temperature != lead_temperature:
                old_temp = lead.lead_temperature
                lead.lead_temperature = lead_temperature
                db.add(
                    models.LeadEvent(
                        lead_id=lead.id,
                        actor_kind=models.LeadEventActorKind.AI.value,
                        actor_user_id=None,
                        event_type=models.LeadEventType.TEMPERATURE_SET.value,
                        payload={
                            "from": old_temp,
                            "to": lead_temperature,
                            "call_id": call_id,
                            "reasons": lead_reasons,
                            "program": lead_program,
                        },
                        created_at=datetime.utcnow(),
                    )
                )

    # Always log a per-call timeline marker so the lead's history shows
    # every AI attempt. Keep it cheap — a single row per call.
    call_row_final = await db.get(models.Call, call_id)
    if call_row_final is not None and call_row_final.lead_id is not None:
        db.add(
            models.LeadEvent(
                lead_id=call_row_final.lead_id,
                actor_kind=models.LeadEventActorKind.AI.value,
                actor_user_id=None,
                event_type=models.LeadEventType.AI_CALL_ENDED.value,
                payload={
                    "call_id": call_id,
                    "outcome": outcome,
                    "duration_s": duration_s,
                    "temperature": lead_temperature,
                },
                created_at=datetime.utcnow(),
            )
        )


async def list_calls(
    db: AsyncSession,
    *,
    lead_id: int | None = None,
    counsellor_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[models.Call], int]:
    stmt = select(models.Call)
    count_stmt = select(func.count()).select_from(models.Call)
    if lead_id is not None:
        stmt = stmt.where(models.Call.lead_id == lead_id)
        count_stmt = count_stmt.where(models.Call.lead_id == lead_id)
    if counsellor_id is not None:
        stmt = stmt.where(models.Call.assigned_counsellor_id == counsellor_id)
        count_stmt = count_stmt.where(
            models.Call.assigned_counsellor_id == counsellor_id
        )
    stmt = stmt.order_by(models.Call.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return list(rows), int(total)


# --- Turns ---

async def add_turn(
    db: AsyncSession,
    *,
    call_id: int,
    turn_index: int,
    role: str,
    text: str | None = None,
    audio_path: str | None = None,
    latency_ms: int | None = None,
) -> models.Turn:
    turn = models.Turn(
        call_id=call_id,
        turn_index=turn_index,
        role=role,
        text=text,
        audio_path=audio_path,
        latency_ms=latency_ms,
    )
    db.add(turn)
    await db.flush()
    return turn


async def list_turns(db: AsyncSession, call_id: int) -> list[models.Turn]:
    rows = await db.execute(
        select(models.Turn)
        .where(models.Turn.call_id == call_id)
        .order_by(models.Turn.turn_index.asc())
    )
    return list(rows.scalars().all())


# --- Callbacks ---

async def create_callback(
    db: AsyncSession,
    *,
    lead_id: int,
    scheduled_at: datetime,
    counsellor_id: int | None = None,
    notes: str | None = None,
) -> models.Callback:
    cb = models.Callback(
        lead_id=lead_id,
        scheduled_at=scheduled_at,
        counsellor_id=counsellor_id,
        notes=notes,
    )
    db.add(cb)
    await db.flush()
    return cb


async def list_callbacks(
    db: AsyncSession,
    *,
    counsellor_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[models.Callback], int]:
    stmt = select(models.Callback)
    count_stmt = select(func.count()).select_from(models.Callback)
    if counsellor_id is not None:
        stmt = stmt.where(models.Callback.counsellor_id == counsellor_id)
        count_stmt = count_stmt.where(models.Callback.counsellor_id == counsellor_id)
    if status:
        stmt = stmt.where(models.Callback.status == status)
        count_stmt = count_stmt.where(models.Callback.status == status)
    stmt = stmt.order_by(models.Callback.scheduled_at.asc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return list(rows), int(total)


async def update_callback(
    db: AsyncSession, cb_id: int, **fields: Any
) -> models.Callback | None:
    row = await db.execute(
        select(models.Callback).where(models.Callback.id == cb_id)
    )
    cb = row.scalar_one_or_none()
    if not cb:
        return None
    for k, v in fields.items():
        if v is not None:
            setattr(cb, k, v)
    await db.flush()
    return cb


# --- DND ---

async def is_dnd(db: AsyncSession, phone: str) -> bool:
    row = await db.execute(
        select(models.DNDEntry.id).where(models.DNDEntry.phone == phone)
    )
    return row.scalar_one_or_none() is not None


async def add_dnd(
    db: AsyncSession, phone: str, reason: str | None = None
) -> models.DNDEntry:
    existing = await db.execute(
        select(models.DNDEntry).where(models.DNDEntry.phone == phone)
    )
    e = existing.scalar_one_or_none()
    if e:
        return e
    entry = models.DNDEntry(phone=phone, reason=reason)
    db.add(entry)
    await db.flush()
    return entry


# --- Dashboard stats ---

async def dashboard_stats(db: AsyncSession) -> dict[str, Any]:
    total_leads = (await db.execute(select(func.count(models.Lead.id)))).scalar_one()
    total_calls = (await db.execute(select(func.count(models.Call.id)))).scalar_one()
    pending_callbacks = (
        await db.execute(
            select(func.count(models.Callback.id)).where(
                models.Callback.status == "pending"
            )
        )
    ).scalar_one()
    dnd_count = (
        await db.execute(select(func.count(models.DNDEntry.id)))
    ).scalar_one()
    # Lead-temperature breakdown so the dashboard can show hot / warm / cold tiles.
    temp_rows = await db.execute(
        select(models.Call.lead_temperature, func.count(models.Call.id))
        .where(models.Call.lead_temperature.isnot(None))
        .group_by(models.Call.lead_temperature)
    )
    lead_counts = {"hot": 0, "warm": 0, "cold": 0}
    for temp, count in temp_rows.all():
        if temp in lead_counts:
            lead_counts[temp] = int(count)
    return {
        "total_leads": int(total_leads),
        "total_calls": int(total_calls),
        "pending_callbacks": int(pending_callbacks),
        "dnd_count": int(dnd_count),
        "lead_temperature": lead_counts,
    }
