"""Analytics hub — single endpoint, single page-load, 7 widgets of data.

Admin-only. Returns a windowed snapshot of:

  1. Conversion funnel       — total → connected → engaged → applied → docs → enrolled
  2. AI performance tile     — pickup %, avg duration, median TTFA, % >2min
  3. Counsellor leaderboard  — calls, talk time, conversions per user
  4. Live SLA alerts         — hot waiting, callbacks overdue, locks orphaned
  5. Outcome breakdown       — completed / no_answer / busy / failed / transferred
  6. Time-of-day heatmap     — 7×24 grid of pickup_rate + hot_rate
  7. Program demand          — top programs × temperature mix

Window options: today / 7d / 30d / all (IST calendar). All times are UTC
in the wire format.
"""
from __future__ import annotations

from datetime import date as _date, datetime, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Integer, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/api/admin", tags=["analytics"])

IST = ZoneInfo("Asia/Kolkata")
HOT_SLA = timedelta(minutes=30)
LONG_CONVERSATION_THRESHOLD_S = 120  # 2 min

Window = Literal["today", "7d", "30d", "all"]


# ───────────────── DTOs ─────────────────────────────────────────────────
class FunnelStep(BaseModel):
    key: str
    label: str
    count: int


class Funnel(BaseModel):
    steps: list[FunnelStep]
    total_leads: int           # entry count for the funnel


class AITile(BaseModel):
    total_dials: int
    pickup_rate_pct: float     # of dials, % with started_at AND duration > 0
    avg_duration_s: float
    median_ttfa_ms: int | None
    pct_long_conversations: float  # % of connected calls >= 2 min
    sparkline_calls_per_day: list[dict]  # [{date: 'YYYY-MM-DD', calls: N}], last 7


class LeaderboardRow(BaseModel):
    user_id: int
    name: str
    calls: int
    talk_time_sec: int
    hot_handled: int
    conversion_pct: float       # of leads they touched, % that reached applied+
    hours_signed_in: float      # approx, based on last_seen_at (best-effort)


class SLAAlerts(BaseModel):
    hot_unclaimed_over_30min: int
    callbacks_overdue: int
    locks_orphaned: int         # locks with no disposition that already expired


class OutcomeSlice(BaseModel):
    outcome: str
    count: int


class HeatmapCell(BaseModel):
    weekday: int                # 0 = Mon, 6 = Sun (IST)
    hour: int                   # 0..23 (IST)
    dials: int
    connects: int
    hots: int


class ProgramRow(BaseModel):
    program: str
    hot: int
    warm: int
    cold: int
    total: int


class AnalyticsResponse(BaseModel):
    window: Window
    window_start_utc: Optional[datetime]   # None when window=all
    window_end_utc: datetime               # always now (UTC)
    # Echoed back when the caller used a custom date range. Lets the
    # frontend label the page banner ("Showing 2026-06-03 → 2026-06-05").
    filter_from: Optional[str] = None
    filter_to: Optional[str] = None
    funnel: Funnel
    ai: AITile
    leaderboard: list[LeaderboardRow]
    sla: SLAAlerts
    outcomes: list[OutcomeSlice]
    heatmap: list[HeatmapCell]
    program_demand: list[ProgramRow]
    server_time: datetime


# ───────────────── helpers ──────────────────────────────────────────────
def _window_to_utc(window: Window) -> tuple[Optional[datetime], datetime]:
    """Translate the window choice into a UTC half-open interval.

    today  → [IST 00:00 today, now)
    7d     → [now-7d, now)
    30d    → [now-30d, now)
    all    → [None, now)         — no lower bound
    """
    now_utc = datetime.now(timezone.utc)
    if window == "all":
        return None, now_utc
    if window == "today":
        today_ist = datetime.now(IST)
        start_ist = datetime(today_ist.year, today_ist.month, today_ist.day, tzinfo=IST)
        return start_ist.astimezone(timezone.utc), now_utc
    if window == "7d":
        return now_utc - timedelta(days=7), now_utc
    if window == "30d":
        return now_utc - timedelta(days=30), now_utc
    raise HTTPException(400, f"Unknown window: {window!r}")


def _date_range_to_utc(from_str: str, to_str: str) -> tuple[datetime, datetime]:
    """Custom IST date range → UTC [start, end). Inclusive on both ends:
    a from=2026-06-03&to=2026-06-05 query covers all three IST days.
    """
    try:
        d_from = _date.fromisoformat(from_str)
        d_to = _date.fromisoformat(to_str)
    except ValueError as e:
        raise HTTPException(400, f"Invalid date: {e}") from None
    if d_to < d_from:
        raise HTTPException(400, "`to` must be on or after `from`.")
    start_ist = datetime(d_from.year, d_from.month, d_from.day, tzinfo=IST)
    end_ist = datetime(d_to.year, d_to.month, d_to.day, tzinfo=IST) + timedelta(days=1)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


def _utc_to_naive(dt: datetime) -> datetime:
    """Strip tzinfo for comparison against SQLite-stored naive UTC columns."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _apply_window(stmt, col, start_utc: Optional[datetime], end_utc: datetime):
    """Apply a [start, end) filter on the given column. Skip if start is None."""
    if start_utc is not None:
        stmt = stmt.where(col >= _utc_to_naive(start_utc))
    stmt = stmt.where(col < _utc_to_naive(end_utc))
    return stmt


# ───────────────── widget builders ──────────────────────────────────────
async def _build_funnel(
    db: AsyncSession, start_utc: Optional[datetime], end_utc: datetime
) -> Funnel:
    """Funnel: total → connected → engaged → applied → docs_received → enrolled.

    "Connected" = at least one call with duration_s > 0. The other stages
    come from Lead.funnel_stage. When a window is set, restrict to leads
    created in that window.
    """
    lead_q = select(func.count()).select_from(models.Lead)
    if start_utc is not None:
        lead_q = _apply_window(lead_q, models.Lead.created_at, start_utc, end_utc)
    total = (await db.execute(lead_q)).scalar_one()

    # Connected: distinct lead_ids in calls (within window if set) where
    # duration_s > 0.
    conn_q = select(func.count(func.distinct(models.Call.lead_id))).where(
        models.Call.duration_s.is_not(None),
        models.Call.duration_s > 0,
    )
    if start_utc is not None:
        conn_q = _apply_window(conn_q, models.Call.started_at, start_utc, end_utc)
    connected = (await db.execute(conn_q)).scalar_one()

    async def _stage_count(stage: str) -> int:
        q = select(func.count()).select_from(models.Lead).where(
            models.Lead.funnel_stage == stage
        )
        # For windowed views, count leads that REACHED this stage in the
        # window, using funnel_stage_changed events. For 'all', use current
        # state.
        if start_utc is not None:
            q = (
                select(func.count(func.distinct(models.LeadEvent.lead_id)))
                .where(
                    models.LeadEvent.event_type
                    == models.LeadEventType.FUNNEL_STAGE_CHANGED.value,
                    func.json_extract(models.LeadEvent.payload, "$.to") == stage,
                )
            )
            q = _apply_window(q, models.LeadEvent.created_at, start_utc, end_utc)
        return int((await db.execute(q)).scalar_one())

    engaged = await _stage_count(models.FunnelStage.ENGAGED.value)
    applied = await _stage_count(models.FunnelStage.APPLIED.value)
    docs = await _stage_count(models.FunnelStage.DOCS_RECEIVED.value)
    enrolled = await _stage_count(models.FunnelStage.ENROLLED.value)

    return Funnel(
        total_leads=int(total),
        steps=[
            FunnelStep(key="total",         label="Total leads",        count=int(total)),
            FunnelStep(key="connected",     label="Connected (answered)", count=int(connected)),
            FunnelStep(key="engaged",       label="Engaged",            count=engaged),
            FunnelStep(key="applied",       label="Applied",            count=applied),
            FunnelStep(key="docs_received", label="Documents received", count=docs),
            FunnelStep(key="enrolled",      label="Enrolled",           count=enrolled),
        ],
    )


async def _build_ai_tile(
    db: AsyncSession, start_utc: Optional[datetime], end_utc: datetime
) -> AITile:
    """AI performance — pickup, avg duration, median TTFA, sparkline."""
    base = select(models.Call.id, models.Call.duration_s, models.Call.ttfa_p50_ms)
    base = _apply_window(base, models.Call.started_at, start_utc, end_utc)

    # Total dials.
    total_q = select(func.count()).select_from(models.Call)
    total_q = _apply_window(total_q, models.Call.started_at, start_utc, end_utc)
    total = int((await db.execute(total_q)).scalar_one())

    if total == 0:
        return AITile(
            total_dials=0,
            pickup_rate_pct=0.0,
            avg_duration_s=0.0,
            median_ttfa_ms=None,
            pct_long_conversations=0.0,
            sparkline_calls_per_day=[],
        )

    # Connected dials.
    conn_q = select(func.count()).select_from(models.Call).where(
        models.Call.duration_s.is_not(None), models.Call.duration_s > 0
    )
    conn_q = _apply_window(conn_q, models.Call.started_at, start_utc, end_utc)
    connected = int((await db.execute(conn_q)).scalar_one())

    # Avg duration over connected calls.
    avg_q = select(func.avg(models.Call.duration_s)).where(
        models.Call.duration_s.is_not(None), models.Call.duration_s > 0
    )
    avg_q = _apply_window(avg_q, models.Call.started_at, start_utc, end_utc)
    avg_dur = float((await db.execute(avg_q)).scalar_one() or 0)

    # % long conversations (>= 2 min).
    long_q = select(func.count()).select_from(models.Call).where(
        models.Call.duration_s.is_not(None),
        models.Call.duration_s >= LONG_CONVERSATION_THRESHOLD_S,
    )
    long_q = _apply_window(long_q, models.Call.started_at, start_utc, end_utc)
    long_count = int((await db.execute(long_q)).scalar_one())

    # Median TTFA — SQLite doesn't have percentile_cont, do it in Python.
    ttfa_q = select(models.Call.ttfa_p50_ms).where(models.Call.ttfa_p50_ms.is_not(None))
    ttfa_q = _apply_window(ttfa_q, models.Call.started_at, start_utc, end_utc)
    ttfa_rows = (await db.execute(ttfa_q)).scalars().all()
    median_ttfa: int | None = None
    if ttfa_rows:
        sorted_ttfa = sorted(ttfa_rows)
        median_ttfa = int(sorted_ttfa[len(sorted_ttfa) // 2])

    # Sparkline: calls per day for the last 7 IST days.
    sparkline_start = (datetime.now(IST) - timedelta(days=6)).date()
    sparkline_q = select(
        func.date(
            func.datetime(models.Call.started_at, "+5 hours", "+30 minutes")
        ).label("d"),
        func.count().label("n"),
    ).where(models.Call.started_at.is_not(None)).group_by("d").order_by("d")
    sparkline_rows = (await db.execute(sparkline_q)).all()
    by_day = {row[0]: int(row[1]) for row in sparkline_rows if row[0]}
    sparkline = []
    for i in range(7):
        d = sparkline_start + timedelta(days=i)
        sparkline.append({"date": d.isoformat(), "calls": by_day.get(d.isoformat(), 0)})

    return AITile(
        total_dials=total,
        pickup_rate_pct=round(100.0 * connected / total, 1) if total else 0.0,
        avg_duration_s=round(avg_dur, 1),
        median_ttfa_ms=median_ttfa,
        pct_long_conversations=round(100.0 * long_count / connected, 1)
        if connected
        else 0.0,
        sparkline_calls_per_day=sparkline,
    )


async def _build_leaderboard(
    db: AsyncSession, start_utc: Optional[datetime], end_utc: datetime
) -> list[LeaderboardRow]:
    """One row per user (admin + counsellor). Sort by calls desc."""
    users = (await db.execute(select(models.User))).scalars().all()
    rows: list[LeaderboardRow] = []
    for u in users:
        call_q = select(func.count(), func.coalesce(func.sum(models.Call.duration_s), 0)).where(
            models.Call.assigned_counsellor_id == u.id
        )
        call_q = _apply_window(call_q, models.Call.started_at, start_utc, end_utc)
        cnt, dur = (await db.execute(call_q)).one()

        # Hot leads handled = locks they acquired where the lead was hot at the time.
        # Approximation: count LOCK_RELEASED events by this user where the
        # lead is currently hot OR was hot per the most recent temperature_set
        # before the lock. Simpler: count releases where Lead.lead_temperature='hot'.
        hot_q = (
            select(func.count(func.distinct(models.LeadEvent.lead_id)))
            .join(models.Lead, models.Lead.id == models.LeadEvent.lead_id)
            .where(
                models.LeadEvent.event_type == models.LeadEventType.LOCK_RELEASED.value,
                models.LeadEvent.actor_user_id == u.id,
                models.Lead.lead_temperature == "hot",
            )
        )
        hot_q = _apply_window(hot_q, models.LeadEvent.created_at, start_utc, end_utc)
        hot_handled = int((await db.execute(hot_q)).scalar_one())

        # Conversion: of leads they touched (any LOCK_RELEASED), how many
        # reached applied+ now? Coarse but useful directionally.
        touched_q = select(
            func.count(func.distinct(models.LeadEvent.lead_id))
        ).where(
            models.LeadEvent.event_type == models.LeadEventType.LOCK_RELEASED.value,
            models.LeadEvent.actor_user_id == u.id,
        )
        touched_q = _apply_window(touched_q, models.LeadEvent.created_at, start_utc, end_utc)
        touched = int((await db.execute(touched_q)).scalar_one())

        converted_q = (
            select(func.count(func.distinct(models.LeadEvent.lead_id)))
            .join(models.Lead, models.Lead.id == models.LeadEvent.lead_id)
            .where(
                models.LeadEvent.event_type == models.LeadEventType.LOCK_RELEASED.value,
                models.LeadEvent.actor_user_id == u.id,
                models.Lead.funnel_stage.in_(
                    [
                        models.FunnelStage.APPLIED.value,
                        models.FunnelStage.DOCS_RECEIVED.value,
                        models.FunnelStage.ENROLLED.value,
                    ]
                ),
            )
        )
        converted_q = _apply_window(
            converted_q, models.LeadEvent.created_at, start_utc, end_utc
        )
        converted = int((await db.execute(converted_q)).scalar_one())

        conversion_pct = round(100.0 * converted / touched, 1) if touched else 0.0

        # Hours signed-in: extremely rough — last_seen_at - first_seen_today.
        # Without a sessions table this is a placeholder. Return 0 when no
        # presence data.
        hours = 0.0
        if u.last_seen_at:
            # If they were seen recently, count from first event today.
            first_today_q = (
                select(func.min(models.LeadEvent.created_at))
                .where(models.LeadEvent.actor_user_id == u.id)
            )
            first_today_q = _apply_window(
                first_today_q, models.LeadEvent.created_at, start_utc, end_utc
            )
            first_today = (await db.execute(first_today_q)).scalar_one_or_none()
            if first_today:
                seen_at = u.last_seen_at
                if seen_at.tzinfo is None:
                    seen_at = seen_at.replace(tzinfo=timezone.utc)
                if first_today.tzinfo is None:
                    first_today = first_today.replace(tzinfo=timezone.utc)
                hours = max(0.0, (seen_at - first_today).total_seconds() / 3600.0)

        rows.append(
            LeaderboardRow(
                user_id=u.id,
                name=u.name,
                calls=int(cnt),
                talk_time_sec=int(dur or 0),
                hot_handled=hot_handled,
                conversion_pct=conversion_pct,
                hours_signed_in=round(hours, 1),
            )
        )

    rows.sort(key=lambda r: (-r.calls, -r.talk_time_sec))
    return rows


async def _build_sla(db: AsyncSession) -> SLAAlerts:
    """Always live (no window) — these are 'right now' problems."""
    now_naive = datetime.utcnow()

    # Hot leads classified > 30 min ago but never opened (no lock_acquired
    # after that classification).
    cutoff = now_naive - HOT_SLA
    hot_q = (
        select(func.count(func.distinct(models.LeadEvent.lead_id)))
        .join(models.Lead, models.Lead.id == models.LeadEvent.lead_id)
        .where(
            models.LeadEvent.event_type == models.LeadEventType.TEMPERATURE_SET.value,
            func.json_extract(models.LeadEvent.payload, "$.to") == "hot",
            models.LeadEvent.created_at < cutoff,
            models.Lead.lead_temperature == "hot",
            models.Lead.locked_by_user_id.is_(None),
            models.Lead.dnd.is_(False),
        )
    )
    hot_breaches = int((await db.execute(hot_q)).scalar_one())

    # Callbacks overdue: scheduled in the past, status still pending.
    cb_q = select(func.count()).select_from(models.Callback).where(
        models.Callback.scheduled_at < datetime.now(timezone.utc),
        models.Callback.status == "pending",
    )
    cb_overdue = int((await db.execute(cb_q)).scalar_one())

    # Locks orphaned: locked_until_at < now AND locked_by_user_id IS NOT NULL.
    # The sweep usually catches these within 30s; if any remain, surface it.
    orphan_q = select(func.count()).select_from(models.Lead).where(
        models.Lead.locked_until_at.is_not(None),
        models.Lead.locked_until_at < now_naive,
        models.Lead.locked_by_user_id.is_not(None),
    )
    orphans = int((await db.execute(orphan_q)).scalar_one())

    return SLAAlerts(
        hot_unclaimed_over_30min=hot_breaches,
        callbacks_overdue=cb_overdue,
        locks_orphaned=orphans,
    )


async def _build_outcomes(
    db: AsyncSession, start_utc: Optional[datetime], end_utc: datetime
) -> list[OutcomeSlice]:
    q = (
        select(models.Call.outcome, func.count())
        .where(models.Call.outcome.is_not(None))
        .group_by(models.Call.outcome)
    )
    q = _apply_window(q, models.Call.started_at, start_utc, end_utc)
    rows = (await db.execute(q)).all()
    return [OutcomeSlice(outcome=r[0], count=int(r[1])) for r in rows]


async def _build_heatmap(
    db: AsyncSession, start_utc: Optional[datetime], end_utc: datetime
) -> list[HeatmapCell]:
    """7×24 grid in IST. Returns only cells with at least 1 dial."""
    # Shift the UTC timestamp by IST offset (+5:30) so SQLite weekday/hour
    # extract IST values directly.
    ist_dt = func.datetime(models.Call.started_at, "+5 hours", "+30 minutes")
    weekday = func.cast(func.strftime("%w", ist_dt), Integer)  # 0=Sun..6=Sat
    hour = func.cast(func.strftime("%H", ist_dt), Integer)

    dials_expr = func.count()
    connects_expr = func.sum(
        case((models.Call.duration_s > 0, 1), else_=0)
    )
    hots_expr = func.sum(
        case((models.Call.lead_temperature == "hot", 1), else_=0)
    )

    q = (
        select(weekday, hour, dials_expr, connects_expr, hots_expr)
        .where(models.Call.started_at.is_not(None))
        .group_by(weekday, hour)
    )
    q = _apply_window(q, models.Call.started_at, start_utc, end_utc)
    rows = (await db.execute(q)).all()

    cells: list[HeatmapCell] = []
    for wd, hr, dials, connects, hots in rows:
        # Convert SQLite weekday (0=Sun..6=Sat) to our 0=Mon..6=Sun.
        wd_int = int(wd)
        weekday_mon0 = (wd_int + 6) % 7
        cells.append(
            HeatmapCell(
                weekday=weekday_mon0,
                hour=int(hr),
                dials=int(dials),
                connects=int(connects or 0),
                hots=int(hots or 0),
            )
        )
    return cells


async def _build_program_demand(
    db: AsyncSession, start_utc: Optional[datetime], end_utc: datetime
) -> list[ProgramRow]:
    """Top 12 programs by total mentions, with temperature mix."""
    program_col = models.Call.lead_program
    hot_expr = func.sum(case((models.Call.lead_temperature == "hot", 1), else_=0))
    warm_expr = func.sum(case((models.Call.lead_temperature == "warm", 1), else_=0))
    cold_expr = func.sum(case((models.Call.lead_temperature == "cold", 1), else_=0))
    total_expr = func.count()

    q = (
        select(program_col, hot_expr, warm_expr, cold_expr, total_expr)
        .where(program_col.is_not(None))
        .group_by(program_col)
        .order_by(total_expr.desc())
        .limit(12)
    )
    q = _apply_window(q, models.Call.started_at, start_utc, end_utc)
    rows = (await db.execute(q)).all()

    return [
        ProgramRow(
            program=r[0],
            hot=int(r[1] or 0),
            warm=int(r[2] or 0),
            cold=int(r[3] or 0),
            total=int(r[4]),
        )
        for r in rows
    ]


# ───────────────── endpoint ─────────────────────────────────────────────
@router.get("/analytics", response_model=AnalyticsResponse)
async def analytics_endpoint(
    window: Window = Query("7d", description="today | 7d | 30d | all"),
    date_from: str | None = Query(
        None,
        alias="from",
        description="Custom range start (YYYY-MM-DD, IST). When set with `to`, "
                    "overrides `window`.",
    ),
    date_to: str | None = Query(
        None,
        alias="to",
        description="Custom range end (YYYY-MM-DD, IST inclusive). Both `from` "
                    "and `to` required together.",
    ),
    _admin: models.User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsResponse:
    # Custom range wins over the window preset when both bounds are present.
    if (date_from and not date_to) or (date_to and not date_from):
        raise HTTPException(
            400, "`from` and `to` must be provided together for a custom range."
        )
    if date_from and date_to:
        start_utc, end_utc = _date_range_to_utc(date_from, date_to)
        active_filter_from: str | None = date_from
        active_filter_to: str | None = date_to
    else:
        start_utc, end_utc = _window_to_utc(window)
        active_filter_from = None
        active_filter_to = None

    funnel = await _build_funnel(db, start_utc, end_utc)
    ai = await _build_ai_tile(db, start_utc, end_utc)
    leaderboard = await _build_leaderboard(db, start_utc, end_utc)
    sla = await _build_sla(db)
    outcomes = await _build_outcomes(db, start_utc, end_utc)
    heatmap = await _build_heatmap(db, start_utc, end_utc)
    program_demand = await _build_program_demand(db, start_utc, end_utc)

    return AnalyticsResponse(
        window=window,
        window_start_utc=start_utc,
        window_end_utc=end_utc,
        filter_from=active_filter_from,
        filter_to=active_filter_to,
        funnel=funnel,
        ai=ai,
        leaderboard=leaderboard,
        sla=sla,
        outcomes=outcomes,
        heatmap=heatmap,
        program_demand=program_demand,
        server_time=datetime.now(timezone.utc),
    )
