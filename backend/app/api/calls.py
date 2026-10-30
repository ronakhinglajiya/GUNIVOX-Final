"""Admin REST endpoints for triggering outbound calls.

  POST /api/calls/originate    -> trigger an outbound Vobiz call

Vobiz-direct webhooks (/vobiz-answer, /status) live in app/api/vobiz_webhooks.py
since they must be at root path to match the Vobiz Voice Application config.
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import get_http_client
from app.config import get_settings
from app.db import repo
from app.db.session import get_db
from app.events import get_event_bus
from app.telephony.vobiz import VobizClient

log = logging.getLogger("gunivox.calls")
router = APIRouter(prefix="/api/calls", tags=["calls"])


from app.auth import require_counsellor_or_admin
from app.db import models
from app.handoff import HandoffError, NoCounsellorAvailable, transfer_to_counsellor
from pydantic import BaseModel


class TransferRequest(BaseModel):
    counsellor_id: int | None = None


@router.post("/{call_id}/hangup")
async def hangup_endpoint(
    call_id: int,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Terminate the active call leg via Vobiz. Thin wrapper — no changes
    to the call-handling logic. The bridge handler's WebSocketDisconnect
    branch will fire normally when Vobiz drops the leg.
    """
    call = await db.get(models.Call, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    if not call.vobiz_call_id:
        raise HTTPException(400, "call has no vobiz_call_id (was it ever originated?)")
    if call.ended_at is not None:
        return {"ok": True, "already_ended": True, "call_id": call_id}

    s = get_settings()
    client = VobizClient(
        get_http_client(),
        auth_id=s.vobiz_auth_id,
        auth_token=s.vobiz_auth_token,
        base_url=s.vobiz_base_url,
    )
    try:
        await client.hangup(call.vobiz_call_id)
    except Exception as e:
        log.exception("hangup failed for call %d", call_id)
        raise HTTPException(502, f"Vobiz hangup failed: {e}") from e

    log.info("hangup requested call_id=%d vobiz=%s", call_id, call.vobiz_call_id)
    return {"ok": True, "call_id": call_id, "vobiz_call_id": call.vobiz_call_id}


@router.post("/{call_id}/transfer")
async def transfer_endpoint(
    call_id: int,
    body: TransferRequest = Body(default_factory=TransferRequest),
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Warm-transfer an in-progress call to an available counsellor."""
    try:
        result = await transfer_to_counsellor(
            db, call_id, counsellor_id=body.counsellor_id
        )
        await db.commit()
        return {
            "ok": True,
            "call_id": result.call_id,
            "counsellor_id": result.counsellor_id,
            "conference_room": result.conference_room,
        }
    except NoCounsellorAvailable as e:
        raise HTTPException(503, str(e)) from e
    except HandoffError as e:
        raise HTTPException(400, str(e)) from e


def _http_url(base: str, path: str) -> str:
    if not base:
        return path
    if base.endswith("/"):
        base = base[:-1]
    if not path.startswith("/"):
        path = "/" + path
    return base + path


@router.post("/originate")
async def originate_call(
    payload: Annotated[dict, Body(...)],
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Body: {"to": "+91...", "from": "<caller_id>" (optional)}.

    Side effects: ensures a Lead exists for `to`, creates a Call row, then
    triggers Vobiz. The Call's `vobiz_call_id` is set to Vobiz's `request_uuid`
    (same as the CallUUID Vobiz uses in webhooks).
    """
    s = get_settings()
    if not s.vobiz_auth_id or not s.vobiz_auth_token:
        raise HTTPException(500, "VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN not set in .env")
    if not s.vobiz_public_base_url:
        raise HTTPException(500, "VOBIZ_PUBLIC_BASE_URL not set (need a public hostname)")

    to = payload.get("to")
    if not to:
        raise HTTPException(400, "'to' is required")
    from_ = payload.get("from") or s.vobiz_caller_id
    if not from_:
        raise HTTPException(400, "'from' (or VOBIZ_CALLER_ID) is required")

    # DND check.
    if await repo.is_dnd(db, to):
        raise HTTPException(403, f"{to} is on the DND list")

    # Ensure a Lead exists for this phone (create with minimal info if new).
    lead, _ = await repo.upsert_lead(
        db, phone=to, source=payload.get("source") or "originate"
    )

    client = VobizClient(
        get_http_client(),
        auth_id=s.vobiz_auth_id,
        auth_token=s.vobiz_auth_token,
        base_url=s.vobiz_base_url,
    )
    answer_url = _http_url(s.vobiz_public_base_url, "/vobiz-answer")
    hangup_url = _http_url(s.vobiz_public_base_url, "/status")

    log.info("originate lead_id=%s to=%s", lead.id, to)
    try:
        result = await client.originate(
            to=to, from_=from_, answer_url=answer_url, hangup_url=hangup_url
        )
    except Exception as e:
        # Vobiz rejected the call (bad caller ID, no balance, unreachable API).
        # Surface the provider's own message as a 502 so the dialer UI can show
        # *why* — an opaque 500 + traceback left the operator with no idea that
        # e.g. the caller ID had stopped being owned by the account.
        log.error("originate failed lead_id=%s to=%s: %s", lead.id, to, e)
        raise HTTPException(502, f"Telephony provider rejected the call: {e}") from e

    call = await repo.create_call(
        db,
        lead_id=lead.id,
        vobiz_call_id=result.request_uuid,
        language="gu",
    )
    # Bump attempt counter on the lead.
    await repo.update_lead(
        db, lead.id, attempt_count=lead.attempt_count + 1
    )
    await db.commit()

    get_event_bus().publish(
        "call.queued",
        call_id=call.id,
        lead_id=lead.id,
        phone=lead.phone,
    )

    return {
        "call_id": call.id,
        "lead_id": lead.id,
        "vobiz_call_id": result.request_uuid,
        "answer_url": answer_url,
    }
