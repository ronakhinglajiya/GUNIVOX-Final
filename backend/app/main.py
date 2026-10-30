import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.auth import me_router as auth_me_router
from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.callbacks import router as callbacks_router
from app.api.reports import router as reports_router
from app.api.calls import router as calls_router
from app.api.calls_browse import router as calls_browse_router
from app.api.campaigns import router as campaigns_router
from app.api.dashboard import router as dashboard_router
from app.api.leads import router as leads_router
from app.api.leads import webhook_router as leads_webhook_router
from app.api.health import router as health_router
from app.api.analytics import router as analytics_router
from app.api.closures import router as closures_router
from app.api.inbox import router as inbox_router
from app.api.live import router as live_router
from app.api.pipeline import router as pipeline_router
from app.api.team import router as team_router
from app.api.admin_cleanup import router as admin_cleanup_router
from app.api.vobiz_webhooks import router as vobiz_webhooks_router
from app.clients import get_openai_client
from app.config import get_settings
from app.telephony.bridge import get_brain, get_fillers
from app.telephony.bridge import router as vobiz_bridge_router
from app.telephony.mock_vobiz import router as mock_vobiz_router

settings = get_settings()

logging.basicConfig(level=settings.log_level.upper())
log = logging.getLogger("gunivox")


async def _warm_once() -> None:
    """Single warm cycle. Touches every upstream so its TCP/TLS pool stays
    hot and DNS caches don't expire.

    Robustness rules (we got burned by these):
      * Each sub-task wrapped in its own try/except — a Sarvam DNS blip
        no longer aborts the OpenAI warm.
      * gather(..., return_exceptions=True) — collected exceptions are
        logged but DO NOT propagate to the caller. The whole point is to
        keep the process alive through transient network issues.
      * No retries here. If a tick fails, the NEXT tick in 60s will retry.
    """
    from app.brain.catalog import get_catalog

    get_brain()
    get_fillers().warm_load()
    get_catalog()

    oai = get_openai_client()

    async def warm_embed():
        try:
            await oai.embeddings.create(model="text-embedding-3-small", input="warmup")
        except Exception as e:
            log.warning("warm_embed failed: %s", type(e).__name__)

    async def warm_chat():
        try:
            stream = await oai.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
                max_tokens=3,
            )
            async for _ in stream:
                pass
        except Exception as e:
            log.warning("warm_chat failed: %s", type(e).__name__)

    async def warm_tts_ws():
        try:
            brain_engine = get_brain()
            sess = brain_engine.tts_stream.session("gu")
            try:
                await sess.connect()
            finally:
                await sess.close()
        except Exception as e:
            log.warning("warm_tts_ws failed: %s", type(e).__name__)

    # return_exceptions=True so a sub-task raising is just collected, not
    # propagated. (Each sub-task already has its own try/except as a belt;
    # this is the suspenders.)
    await asyncio.gather(
        warm_embed(), warm_chat(), warm_tts_ws(),
        return_exceptions=True,
    )


async def _prewarm() -> None:
    """One-shot startup warm. Reports to log so we can see in production."""
    try:
        await _warm_once()
        log.info("prewarm complete (embed + chat + tts ws)")
    except Exception:
        log.exception("prewarm failed (continuing anyway)")


async def _warm_loop() -> None:
    """Periodic warm — every 60s. Sarvam idle-closes WS after ~2-3 min, and
    cloud TCP pools age out idle connections too; this keeps everything
    just-recently-used so the first call's first turn doesn't pay a 10-15s
    cold-start tax (observed in production logs)."""
    while True:
        await asyncio.sleep(60.0)
        try:
            await _warm_once()
            log.debug("periodic warm tick")
        except Exception:
            # Don't kill the loop on a transient blip.
            log.exception("periodic warm tick failed")


async def _lock_sweep_loop() -> None:
    """Release expired counsellor-lead locks every 30 s.

    The Inbox endpoint already treats a stale lock as released for read
    purposes, but the DB column still points at the old user. This sweep
    actually clears it AND emits a LOCK_EXPIRED timeline event so the
    admin can see "Keval opened lead #1234 and walked away".
    """
    from datetime import datetime as _dt
    from sqlalchemy import select, update
    from app.db.session import SessionLocal
    from app.db import models as _models

    while True:
        await asyncio.sleep(30.0)
        try:
            async with SessionLocal() as db:
                now = _dt.utcnow()
                expired_rows = (
                    await db.execute(
                        select(_models.Lead.id, _models.Lead.locked_by_user_id)
                        .where(
                            _models.Lead.locked_until_at.is_not(None),
                            _models.Lead.locked_until_at < now,
                            _models.Lead.locked_by_user_id.is_not(None),
                        )
                        .limit(100)  # cap per tick
                    )
                ).all()
                if not expired_rows:
                    continue
                expired_ids = [r[0] for r in expired_rows]
                await db.execute(
                    update(_models.Lead)
                    .where(_models.Lead.id.in_(expired_ids))
                    .values(
                        locked_by_user_id=None,
                        locked_until_at=None,
                        lock_extensions=0,
                    )
                )
                for lead_id, prev_holder in expired_rows:
                    db.add(
                        _models.LeadEvent(
                            lead_id=lead_id,
                            actor_kind=_models.LeadEventActorKind.SYSTEM.value,
                            actor_user_id=None,
                            event_type=_models.LeadEventType.LOCK_EXPIRED.value,
                            payload={"prev_holder_user_id": prev_holder},
                            created_at=now,
                        )
                    )
                await db.commit()
                log.info("lock-sweep released %d expired lock(s)", len(expired_ids))
        except Exception:
            log.exception("lock-sweep tick failed (continuing)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("gunivox starting up env=%s", settings.app_env)
    # One-shot warm at startup + periodic warm every 60s for the rest of
    # the process lifetime. The periodic task is the load-bearing one —
    # it stops the per-call cold-start (turn 1 was burning 15s in prod).
    asyncio.create_task(_prewarm())
    warm_task = asyncio.create_task(_warm_loop(), name="warm-loop")
    lock_sweep_task = asyncio.create_task(_lock_sweep_loop(), name="lock-sweep")
    # Start the campaign dialer worker.
    from app.campaigns import get_dialer

    dialer = get_dialer()
    await dialer.start()
    try:
        yield
    finally:
        log.info("gunivox shutting down")
        warm_task.cancel()
        lock_sweep_task.cancel()
        await dialer.stop()


app = FastAPI(title="Gunivox Voice Agent", version="0.3.0", lifespan=lifespan)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Tag each request with an X-Request-ID for log correlation."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response


app.add_middleware(RequestIDMiddleware)
# Allow any origin in dev so LAN-connected PCs can hit the API.
# Lock this down (CORS_ALLOWED_ORIGINS env list) for production.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)

# Friendly redirect: anyone landing on `api.gunivox.online/` (a humans-friendly
# typo of `app.gunivox.online`) gets bounced to the dashboard. The API itself
# still serves /api/*, /vobiz-answer, /vobiz-conference/*, /status, etc.
@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    settings = get_settings()
    frontend_url = settings.vobiz_public_base_url.replace("api.", "app.") if settings.vobiz_public_base_url else "https://app.gunivox.online"
    return RedirectResponse(url=frontend_url, status_code=302)


app.include_router(health_router, prefix="/api")
app.include_router(auth_router)
app.include_router(auth_me_router)
app.include_router(leads_router)
app.include_router(leads_webhook_router)
app.include_router(calls_router)
app.include_router(calls_browse_router)
app.include_router(billing_router)
app.include_router(callbacks_router)
app.include_router(reports_router)
app.include_router(campaigns_router)
app.include_router(dashboard_router)
app.include_router(live_router)
app.include_router(team_router)
app.include_router(admin_cleanup_router)
app.include_router(inbox_router)
app.include_router(closures_router)
app.include_router(pipeline_router)
app.include_router(analytics_router)
app.include_router(vobiz_webhooks_router)
app.include_router(vobiz_bridge_router)
app.include_router(mock_vobiz_router)


