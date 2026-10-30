"""Admin Team page — presence + today's call stats per counsellor.

Single endpoint: GET /api/admin/team. Returns every non-admin user with:
  - presence (online / away / offline / never), derived from last_seen_at
  - calls_today (count, IST calendar day)
  - duration_today_sec (sum of duration_s)
  - active_call (boolean — Call where assigned_counsellor_id=user, ended_at IS NULL)

Admin-only. The frontend polls this every ~10s for live presence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])

IST = ZoneInfo("Asia/Kolkata")
ONLINE_WINDOW = timedelta(minutes=2)   # last activity within 2 min → "online"
AWAY_WINDOW = timedelta(minutes=15)    # 2..15 min → "away", else "offline"


class TeamMember(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    last_seen_at: Optional[datetime] = None
    presence: str  # "online" | "away" | "offline" | "never"
    calls_today: int = 0
    duration_today_sec: int = 0
    on_call_now: bool = False


class TeamList(BaseModel):
    members: list[TeamMember]
    server_time: datetime


def _presence(last_seen: Optional[datetime]) -> str:
    if last_seen is None:
        return "never"
    # last_seen_at is written via datetime.utcnow() (naive UTC). Normalize.
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - last_seen
    if delta <= ONLINE_WINDOW:
        return "online"
    if delta <= AWAY_WINDOW:
        return "away"
    return "offline"


def _ist_today_utc_window() -> tuple[datetime, datetime]:
    """Return the UTC half-open window covering the current IST calendar day."""
    now_ist = datetime.now(IST)
    start_ist = datetime(now_ist.year, now_ist.month, now_ist.day, tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


@router.get("/team", response_model=TeamList)
async def team_endpoint(
    _admin: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TeamList:
    # Pull every user — the team page shows admins too (so the admin sees
    # themselves online) but we tag the role so the UI can style differently.
    users_rows = await db.execute(
        select(models.User).order_by(models.User.role.desc(), models.User.name.asc())
    )
    users = list(users_rows.scalars().all())

    if not users:
        return TeamList(members=[], server_time=datetime.now(timezone.utc))

    user_ids = [u.id for u in users]
    today_start_utc, today_end_utc = _ist_today_utc_window()

    # Today's calls per counsellor — assigned_counsellor_id is the FK set when
    # the dialer or manual dialer kicks a call. Group by it.
    today_stmt = (
        select(
            models.Call.assigned_counsellor_id,
            func.count(models.Call.id).label("count"),
            func.coalesce(func.sum(models.Call.duration_s), 0).label("dur"),
        )
        .where(models.Call.assigned_counsellor_id.in_(user_ids))
        .where(models.Call.started_at >= today_start_utc)
        .where(models.Call.started_at < today_end_utc)
        .group_by(models.Call.assigned_counsellor_id)
    )
    today_rows = await db.execute(today_stmt)
    today_by_user = {
        row.assigned_counsellor_id: (int(row.count), int(row.dur or 0))
        for row in today_rows
    }

    # Active calls — calls assigned to a user that haven't ended.
    active_stmt = (
        select(models.Call.assigned_counsellor_id, func.count(models.Call.id))
        .where(models.Call.assigned_counsellor_id.in_(user_ids))
        .where(models.Call.ended_at.is_(None))
        .group_by(models.Call.assigned_counsellor_id)
    )
    active_rows = await db.execute(active_stmt)
    on_call_user_ids = {r[0] for r in active_rows if r[1] and r[1] > 0}

    members: list[TeamMember] = []
    for u in users:
        cnt, dur = today_by_user.get(u.id, (0, 0))
        # last_seen_at is stored NAIVE (utcnow()) because the DB column is
        # plain DateTime, not DateTime(timezone=True). Pydantic would
        # serialize that as "2026-06-05T15:30:00" — no Z — which JS
        # `new Date()` interprets as the user's local time, throwing the
        # "5h ago" UTC-vs-IST skew on the Team page. Re-attach UTC tzinfo
        # so the wire format ends with +00:00 / Z.
        last_seen_utc = (
            u.last_seen_at.replace(tzinfo=timezone.utc) if u.last_seen_at else None
        )
        members.append(
            TeamMember(
                id=u.id,
                name=u.name,
                email=u.email,
                role=u.role,
                is_active=u.is_active,
                last_seen_at=last_seen_utc,
                presence=_presence(u.last_seen_at),
                calls_today=cnt,
                duration_today_sec=dur,
                on_call_now=u.id in on_call_user_ids,
            )
        )

    return TeamList(members=members, server_time=datetime.now(timezone.utc))
