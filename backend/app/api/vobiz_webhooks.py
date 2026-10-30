"""Vobiz-direct webhook endpoints at root path (matches Vobiz app config).

These URLs are configured inside the Vobiz Voice Application dashboard:
  Answer URL : POST https://api.gunivox.online/vobiz-answer
  Hangup URL : POST https://api.gunivox.online/status

Vobiz hits /vobiz-answer when the call connects — we return AnswerXML that
opens a bidirectional <Stream> to /vobiz/stream/{CallUUID}.

Vobiz hits /status with call lifecycle events (answered, hangup, etc.).
"""
from __future__ import annotations

import logging
import uuid

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.config import get_settings
from app.db import repo
from app.db.session import SessionLocal
from app.telephony.answerxml import (
    conference_response,
    play_then_hangup_response,
    stream_response,
)

log = logging.getLogger("gunivox.vobiz_webhooks")
router = APIRouter(tags=["vobiz-webhooks"])

# Where pre-rendered filler WAVs live on disk. Exposed publicly at /audio/<name>.wav
# so Vobiz's <Play> directive can fetch them over HTTPS via the tunnel.
_FILLER_ROOT = Path(__file__).resolve().parents[2] / "data" / "fillers"

NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _ws_url(base: str, call_id: str) -> str:
    if base.startswith("https://"):
        wss = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        wss = "ws://" + base[len("http://") :]
    else:
        wss = "wss://" + base
    return wss.rstrip("/") + f"/vobiz/stream/{call_id}"


async def _read_params(request: Request) -> dict:
    params: dict = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            params.update({k: str(v) for k, v in form.items()})
        except Exception:
            try:
                j = await request.json()
                if isinstance(j, dict):
                    params.update({k: str(v) for k, v in j.items()})
            except Exception:
                pass
    return params


@router.api_route("/vobiz-answer", methods=["GET", "POST"])
async def vobiz_answer(request: Request) -> Response:
    params = await _read_params(request)
    call_uuid = (
        params.get("CallUUID")
        or params.get("call_uuid")
        or params.get("RequestUUID")
        or uuid.uuid4().hex
    )
    s = get_settings()
    if not s.vobiz_public_base_url:
        log.error("VOBIZ_PUBLIC_BASE_URL not set")
        return Response(content="", media_type="application/xml", status_code=500)

    # Inbound detection: outbound calls have a matching Call row pre-created
    # by /api/calls/originate (vobiz_call_id = the request_uuid Vobiz returns,
    # which is the same as the CallUUID Vobiz sends here). If no row matches,
    # this is an inbound call — return the canned "thanks for calling, here's
    # the admissions number" greeting and hang up. No WebSocket, no LLM.
    async with SessionLocal() as db:
        outbound_call = await repo.get_call_by_vobiz_id(db, call_uuid)
    if outbound_call is None:
        audio_url = s.vobiz_public_base_url.rstrip("/") + "/audio/inbound_greeting.wav"
        xml = play_then_hangup_response(audio_url)
        log.info(
            "vobiz-answer INBOUND call_uuid=%s play=%s xml_len=%d",
            call_uuid, audio_url, len(xml),
        )
        return Response(content=xml, media_type="application/xml", headers=NO_CACHE)

    ws_url = _ws_url(s.vobiz_public_base_url, call_uuid)
    xml = stream_response(ws_url, bidirectional=True, keepCallAlive=True, sample_rate=16000)
    log.info("vobiz-answer call_uuid=%s ws=%s xml_len=%d", call_uuid, ws_url, len(xml))
    return Response(
        content=xml, media_type="application/xml", headers=NO_CACHE,
    )


@router.get("/audio/{filename}")
async def serve_audio(filename: str) -> FileResponse:
    """Serve a pre-rendered filler WAV publicly so Vobiz <Play> can fetch it.

    Path-traversal safe: only the filename basename is honoured, only .wav
    extension is allowed, and we resolve+ensure the result still lives under
    the fillers root. Only Gujarati fillers are served (no per-language
    selector needed for the inbound greeting flow).
    """
    safe = Path(filename).name  # strip any '../' / leading paths
    if not safe.endswith(".wav"):
        raise HTTPException(404, "not found")
    candidate = (_FILLER_ROOT / "gu" / safe).resolve()
    if not str(candidate).startswith(str((_FILLER_ROOT / "gu").resolve())) or not candidate.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(
        path=str(candidate),
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.api_route("/vobiz-conference/{room}", methods=["GET", "POST"])
async def vobiz_conference_xml(room: str) -> Response:
    """Returns <Conference>room</Conference> XML for warm-transfer handoffs.

    Both the user leg (redirected here by /api/calls/{id}/transfer) and the
    counsellor leg (newly dialed by the transfer service) fetch this URL —
    they end up in the same conference room.
    """
    xml = conference_response(
        room,
        record=True,
        end_on_exit=False,
        start_on_enter=True,
    )
    log.info("vobiz-conference room=%s xml_len=%d", room, len(xml))
    return Response(content=xml, media_type="application/xml", headers=NO_CACHE)


@router.api_route("/status", methods=["GET", "POST"])
async def vobiz_status(request: Request) -> dict:
    """Vobiz fires this on every call lifecycle event (Hangup most importantly).

    Critical for campaign dialing: when Vobiz reports a Hangup with
    status=busy / no-answer / failed, the call NEVER opened a bridge
    WebSocket, so the bridge's _finalize_call never ran. Without this
    handler doing the settle work, the campaign_lead sat at in_progress
    forever, the DB concurrency gate refused to dial more, and the
    dialer's 3-min safety net was the only thing freeing the slot —
    burning 3 minutes per busy line.

    Now this handler:
      1. Updates the Call row (ended_at, duration_s, outcome)
      2. Settles the in_progress campaign_lead based on outcome
      3. Kicks the dialer so the next lead originates within ~1s
    """
    from datetime import datetime, timezone
    from app.db.session import SessionLocal
    from app.db import repo

    params = await _read_params(request)
    call_uuid = params.get("CallUUID") or params.get("call_uuid")
    event = (params.get("Event") or "").lower()
    raw_status = (params.get("CallStatus") or "").lower().strip()
    try:
        duration_s = float(params.get("Duration") or 0)
    except (TypeError, ValueError):
        duration_s = 0.0

    log.info(
        "vobiz status call_uuid=%s event=%s status=%s duration=%s",
        call_uuid, params.get("Event"), params.get("CallStatus"), params.get("Duration"),
    )

    # We act on Hangup events only — that's when Vobiz signals the call is
    # over (with its final disposition). Other events (Ring, Bridged, etc.)
    # are informational; ignore.
    if event != "hangup" or not call_uuid:
        return {"ok": True}

    # Normalize the Vobiz disposition to our outcome vocab.
    # Plivo-style values: completed | busy | no-answer | failed | canceled
    if raw_status in ("completed", "answered"):
        outcome = "completed"
    elif raw_status in ("busy",):
        outcome = "busy"
    elif raw_status in ("no-answer", "noanswer", "no_answer"):
        outcome = "no_answer"
    elif raw_status in ("failed", "error"):
        outcome = "failed"
    elif raw_status in ("canceled", "cancelled"):
        outcome = "canceled"
    else:
        outcome = raw_status or "unknown"

    # Did_not_connect cases (everything except completed) → failed_retryable
    # so the campaign's retry policy gets another shot at this lead.
    cl_target_status = "done" if outcome == "completed" else "failed_retryable"

    try:
        async with SessionLocal() as db:
            call = await repo.get_call_by_vobiz_id(db, call_uuid)
            if call is None:
                log.warning("vobiz status: no Call row for uuid=%s — ignoring", call_uuid)
                return {"ok": True}

            # Only settle the Call if the bridge hasn't already done so
            # (the bridge's _finalize_call runs on a successful conversation
            # and writes ended_at + duration_s itself; we'd race it).
            if call.ended_at is None:
                await repo.finalize_call(
                    db, call.id,
                    duration_s=duration_s,
                    outcome=outcome,
                    recording_path=None,
                    ttfa_p50_ms=None,
                )
                log.info(
                    "vobiz status: settled Call %d via webhook outcome=%s duration=%.1fs",
                    call.id, outcome, duration_s,
                )

            # Settle the matching in_progress campaign_lead (if any) so the
            # dialer's concurrency gate frees up immediately.
            from app.db import campaigns_repo as crepo
            cl = await crepo.find_in_progress_for_lead(db, call.lead_id)
            if cl is not None:
                await crepo.mark_attempt(
                    db, cl.id, new_status=cl_target_status,
                    increment_attempts=False,
                )
                log.info(
                    "vobiz status: campaign_lead %d -> %s (call_id=%d outcome=%s)",
                    cl.id, cl_target_status, call.id, outcome,
                )
            await db.commit()

        # Kick the dialer so the NEXT queued lead starts within ~1s
        # instead of waiting up to the 5s polling interval.
        try:
            from app.campaigns import get_dialer
            import asyncio
            d = get_dialer()
            if d is not None:
                asyncio.create_task(d._tick())
        except Exception:
            log.exception("vobiz status: dialer kick failed (continuing)")
    except Exception:
        log.exception("vobiz status: settle path failed")

    return {"ok": True}
