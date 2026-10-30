"""DialerService — background worker that drives autonomous outbound calls.

Loop (every `tick_seconds`):
  1. For each active campaign:
     a. Check we're inside its working_hours window (timezone-aware).
     b. While we have spare concurrency, pick the next eligible lead
        (not DND, attempts < max, last call > gap_hours ago).
     c. Originate a call via Vobiz, mark campaign_lead status=in_progress,
        bump lead.attempt_count + last_attempted_at.
  2. Sleep, repeat.

Concurrency is bounded by `max_concurrent_calls` (default 3, matching the
1–5 target from the project spec).

The originator uses the same /api/calls/originate flow under the hood — we
import its core, not the HTTP endpoint, to avoid a self-loopback round trip.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import get_http_client
from app.config import get_settings
from app.db import models, repo
from app.db import campaigns_repo as crepo
from app.db.session import SessionLocal
from app.telephony.vobiz import VobizClient

log = logging.getLogger("gunivox.dialer")


def _now_in_tz(tz_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(ZoneInfo("Asia/Kolkata"))


def _within_working_hours(wh: dict | None, now: datetime) -> bool:
    """`wh` is the JSON stored on Campaign. Returns True if `now` is inside."""
    if not wh:
        # No restriction → always allowed.
        return True
    try:
        days = wh.get("days") or [0, 1, 2, 3, 4, 5]
        if now.weekday() not in days:
            return False
        start = wh.get("start", "00:00")
        end = wh.get("end", "23:59")
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
        cur_min = now.hour * 60 + now.minute
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        return start_min <= cur_min <= end_min
    except Exception:
        return True


def _http_url(base: str, path: str) -> str:
    if not base:
        return path
    base = base.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


class DialerService:
    def __init__(
        self,
        *,
        tick_seconds: float = 30.0,
        max_concurrent_calls: int = 1,
        max_concurrent_global: int = 5,
    ) -> None:
        self.tick_seconds = tick_seconds
        self.max_concurrent_calls = max_concurrent_calls
        self.max_concurrent_global = max_concurrent_global
        self.active_calls: set[int] = set()  # campaign_lead ids currently dialing
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Serializes _tick() across the periodic loop AND any external kicks
        # (bridge.py call-end, vobiz_webhooks.py status, campaigns.py start).
        # Without this, two concurrent _tick coroutines both read the in_flight
        # COUNT before either commits its mark_attempt → both pick leads and
        # the global cap is overshot. Adversarial review 2026-06-04 flagged
        # this. Lock cost is negligible; ticks are short and infrequent.
        self._tick_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="dialer-loop")
        log.info(
            "dialer started (tick=%.1fs, per_campaign=%d, global=%d)",
            self.tick_seconds,
            self.max_concurrent_calls,
            self.max_concurrent_global,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await self._task
            except BaseException:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("dialer tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                pass

    # How long a campaign_lead is allowed to sit at status=in_progress
    # before the dialer's safety net resurrects it as failed_retryable so
    # the campaign can continue. 3 min is comfortably longer than any
    # normal call (the longest call we've seen was ~6 min, and those have
    # the bridge's _finalize_call settling them at call-end). This catches
    # only the genuinely orphaned rows: bridge crash, WS never disconnected,
    # Vobiz dropped weirdly mid-call, etc.
    STALE_IN_PROGRESS_MINUTES: int = 3

    async def _recover_stale_in_progress(self, db: AsyncSession) -> int:
        """Safety net: resurrect campaign_leads stuck at in_progress.

        Without this, ONE missed _finalize_call (which can happen on a
        weird Vobiz drop, an unexpected exception in the bridge, a backend
        restart mid-call, etc.) would freeze the entire dialer because
        the DB-driven concurrency gate sees in_flight=1 forever.

        Returns the number of rows resurrected.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.STALE_IN_PROGRESS_MINUTES)
        # SQLite stores naïve UTC; strip tzinfo so the comparison works.
        cutoff_naive = cutoff.replace(tzinfo=None)
        result = await db.execute(
            update(models.CampaignLead)
            .where(
                models.CampaignLead.status == "in_progress",
                models.CampaignLead.updated_at < cutoff_naive,
            )
            .values(status="failed_retryable")
        )
        n = int(result.rowcount or 0)
        if n:
            log.warning(
                "dialer: recovered %d stale in_progress campaign_lead(s) "
                "(> %d min since updated_at) — marking failed_retryable",
                n, self.STALE_IN_PROGRESS_MINUTES,
            )
        return n

    async def _tick(self) -> None:
        # Serialize concurrent tick invocations (periodic loop + external
        # kicks). Without the lock, two ticks would each read in_flight=N,
        # both pick a lead, and we'd briefly exceed max_concurrent_global.
        # With the lock, the second tick simply waits for the first to
        # finish — and when it runs, it sees the updated count.
        async with self._tick_lock:
            async with SessionLocal() as db:
                # Self-heal BEFORE looking at concurrency. Without this, one
                # orphaned in_progress row stalls the campaign forever (the
                # DB gate refuses to dial because in_flight≥1).
                recovered = await self._recover_stale_in_progress(db)
                if recovered:
                    await db.commit()
                campaigns, _ = await crepo.list_campaigns(db, status="active", limit=20)
                for c in campaigns:
                    await self._process_campaign(db, c)
                await db.commit()

    async def _process_campaign(
        self, db: AsyncSession, campaign: models.Campaign
    ) -> None:
        wh = campaign.working_hours or {}
        tz_name = wh.get("timezone", "Asia/Kolkata")
        now = _now_in_tz(tz_name)
        if not _within_working_hours(wh, now):
            return

        rp = campaign.retry_policy or {}
        gap_hours = float(rp.get("gap_hours", 4.0))
        max_attempts = int(rp.get("max_attempts", 3))

        # Dial as many eligible leads as our concurrency budget allows.
        #
        # CONCURRENCY GATE — 2-tier DB-driven (2026-06-04):
        # Two caps are checked, and we stop dialing as soon as EITHER one
        # is hit:
        #
        #   1. PER-CAMPAIGN cap (`self.max_concurrent_calls`, default 1):
        #      Counts in_progress CampaignLead rows whose `campaign_id`
        #      matches THIS campaign. With the default of 1, each campaign
        #      dials strictly one-at-a-time — lead N must FULLY end before
        #      lead N+1 starts within the same campaign.
        #
        #   2. GLOBAL cap (`self.max_concurrent_global`, default 5):
        #      Counts in_progress CampaignLead rows across ALL campaigns.
        #      Acts as a system-wide safety net so that if many campaigns
        #      are active simultaneously, total in-flight calls never
        #      exceed the global ceiling.
        #
        # Together, this lets multiple campaigns run in parallel (each
        # respecting its own per-campaign cap) while keeping a hard system
        # ceiling. With per-campaign=1 and global=5, two active campaigns
        # produce up to two concurrent calls (one per campaign), six
        # active campaigns produce at most five.
        #
        # Why DB-driven (history): the previous gate
        # `len(self.active_calls) < max_concurrent_calls` was wrong because
        # `_dial_one`'s `finally` block discards from active_calls the
        # moment Vobiz's REST originate returns (~1-2s), but the actual
        # phone call is still ringing/talking for another 60+ seconds.
        # With max_concurrent=1 this caused a "call bomb". We count
        # `CampaignLead.status = 'in_progress'` rows in the DB instead.
        # That row stays at in_progress until `_finalize_call` (in
        # bridge.py) settles it to done / failed_retryable when the WS
        # actually disconnects. So the DB count is "real" in-flight calls.
        per_campaign_stmt = (
            select(func.count()).select_from(models.CampaignLead)
            .where(
                models.CampaignLead.status == "in_progress",
                models.CampaignLead.campaign_id == campaign.id,
            )
        )
        global_stmt = (
            select(func.count()).select_from(models.CampaignLead)
            .where(models.CampaignLead.status == "in_progress")
        )
        while True:
            per_campaign_in_flight = (await db.execute(per_campaign_stmt)).scalar_one()
            if per_campaign_in_flight >= self.max_concurrent_calls:
                # This campaign already has its cap of calls running;
                # wait for the bridge's _finalize_call to flip status,
                # which kicks the dialer again so we re-enter here.
                break
            global_in_flight = (await db.execute(global_stmt)).scalar_one()
            if global_in_flight >= self.max_concurrent_global:
                # System-wide safety cap hit; back off until any in-flight
                # call (in any campaign) settles.
                break
            pair = await crepo.pick_next_eligible_lead(
                db, campaign, gap_hours=gap_hours, max_attempts=max_attempts
            )
            if pair is None:
                break
            cl, lead = pair
            if cl.id in self.active_calls:
                # Defensive: shouldn't happen because we set status=in_progress.
                break
            self.active_calls.add(cl.id)
            # Mark in_progress BEFORE awaiting Vobiz so the next iteration
            # of this while-loop sees the in_flight count incremented.
            await crepo.mark_attempt(db, cl.id, new_status="in_progress")
            await db.commit()  # commit so in_flight count is visible
            asyncio.create_task(self._dial_one(cl.id, lead.id, campaign.id))

    async def _dial_one(
        self, cl_id: int, lead_id: int, campaign_id: int
    ) -> None:
        """Originate one call. Settle the campaign_lead status after."""
        s = get_settings()
        try:
            async with SessionLocal() as db:
                lead = await repo.get_lead(db, lead_id)
                if not lead:
                    return
                if lead.dnd:
                    await crepo.mark_attempt(db, cl_id, new_status="failed", increment_attempts=False)
                    await db.commit()
                    return

                if not (s.vobiz_auth_id and s.vobiz_auth_token):
                    log.error("dialer: VOBIZ creds missing")
                    await crepo.mark_attempt(db, cl_id, new_status="failed_retryable")
                    await db.commit()
                    return

                client = VobizClient(
                    get_http_client(),
                    auth_id=s.vobiz_auth_id,
                    auth_token=s.vobiz_auth_token,
                    base_url=s.vobiz_base_url,
                )
                answer_url = _http_url(s.vobiz_public_base_url, "/vobiz-answer")
                hangup_url = _http_url(s.vobiz_public_base_url, "/status")

                log.info(
                    "dialer: campaign=%d lead=%d phone=%s",
                    campaign_id, lead_id, lead.phone,
                )
                try:
                    result = await client.originate(
                        to=lead.phone,
                        from_=s.vobiz_caller_id,
                        answer_url=answer_url,
                        hangup_url=hangup_url,
                    )
                except Exception:
                    log.exception("dialer: vobiz originate failed")
                    await crepo.mark_attempt(db, cl_id, new_status="failed_retryable")
                    await db.commit()
                    return

                await repo.create_call(
                    db,
                    lead_id=lead_id,
                    vobiz_call_id=result.request_uuid,
                    language="gu",
                    campaign_id=campaign_id,
                )
                await repo.update_lead(
                    db, lead_id, attempt_count=lead.attempt_count + 1
                )
                # Note: status is "in_progress" until /status webhook lands.
                # /status updates the Call but not the campaign_lead — that's
                # left as future work (a status-webhook→campaign_lead resolver).
                await db.commit()
        finally:
            self.active_calls.discard(cl_id)


_dialer: Optional[DialerService] = None


def get_dialer() -> DialerService:
    global _dialer
    if _dialer is None:
        from app.config import get_settings

        s = get_settings()
        _dialer = DialerService(
            tick_seconds=s.dialer_tick_seconds,
            max_concurrent_calls=s.dialer_max_concurrent,
            max_concurrent_global=s.dialer_max_concurrent_global,
        )
    return _dialer
