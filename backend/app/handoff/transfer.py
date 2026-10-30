"""Warm transfer: bridge a live call into a Vobiz conference where a counsellor joins.

Flow:
  1. Pick an available counsellor (User.is_available=True, has phone).
  2. Build a conference room name unique to this call.
  3. Originate a NEW outbound call to the counsellor's phone with an answer URL
     that returns <Conference>room</Conference>.
  4. Redirect the in-progress user leg to the SAME answer URL — both legs end up
     in the same conference room.
  5. Update the Call row with assigned_counsellor_id + outcome='transferred'.

This is best-effort: if the counsellor doesn't pick up within ring_timeout,
the redirected user leg sits in an empty conference. Future improvement:
fall back to a scheduled callback if the originate fails or times out.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import get_http_client
from app.config import get_settings
from app.db import models, repo
from app.telephony.vobiz import VobizClient

log = logging.getLogger("gunivox.handoff")


class HandoffError(Exception):
    """Generic transfer failure."""


class NoCounsellorAvailable(HandoffError):
    """No is_available=True counsellor with a phone configured."""


@dataclass
class TransferResult:
    call_id: int
    counsellor_id: int
    counsellor_phone: str
    conference_room: str
    counsellor_request_uuid: str


def _http_url(base: str, path: str) -> str:
    if not base:
        return path
    base = base.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


async def transfer_to_counsellor(
    db: AsyncSession,
    call_id: int,
    *,
    counsellor_id: int | None = None,
) -> TransferResult:
    """Trigger a warm transfer for `call_id`.

    Returns details about the conference + counsellor leg. Raises HandoffError
    on missing data (call not found, no counsellor, no phone) or Vobiz failure.
    """
    s = get_settings()
    if not (s.vobiz_auth_id and s.vobiz_auth_token and s.vobiz_public_base_url):
        raise HandoffError("Vobiz credentials or public URL not configured")

    call = await repo.get_call(db, call_id)
    if not call:
        raise HandoffError("Call not found")
    if not call.vobiz_call_id:
        raise HandoffError("Call has no vobiz_call_id (not yet in progress)")

    # Find a counsellor.
    if counsellor_id is not None:
        counsellor = await repo.get_user_by_id(db, counsellor_id)
        if not counsellor or counsellor.role != "counsellor":
            raise HandoffError(f"counsellor_id={counsellor_id} not found")
    else:
        counsellor = await repo.find_available_counsellor(db)
        if not counsellor:
            raise NoCounsellorAvailable("No available counsellor right now")

    if not counsellor.phone:
        raise HandoffError(f"Counsellor {counsellor.email} has no phone configured")

    conference_room = f"handoff-{call.vobiz_call_id[:8]}-{call_id}"
    conf_answer_url = _http_url(
        s.vobiz_public_base_url, f"/vobiz-conference/{conference_room}"
    )

    client = VobizClient(
        get_http_client(),
        auth_id=s.vobiz_auth_id,
        auth_token=s.vobiz_auth_token,
        base_url=s.vobiz_base_url,
    )

    # 1. Dial the counsellor into the conference.
    log.info(
        "handoff: dialing counsellor %s (%s) into conference %s for call %d",
        counsellor.email, counsellor.phone, conference_room, call_id,
    )
    try:
        result = await client.originate(
            to=counsellor.phone,
            from_=s.vobiz_caller_id,
            answer_url=conf_answer_url,
        )
    except Exception as e:
        raise HandoffError(f"counsellor originate failed: {e}") from e

    # 2. Redirect the existing user leg into the same conference.
    log.info(
        "handoff: redirecting user leg %s into conference %s",
        call.vobiz_call_id, conference_room,
    )
    try:
        await client.redirect(call.vobiz_call_id, conf_answer_url)
    except Exception as e:
        # Counsellor leg already placed; mark call but surface the error.
        log.exception("handoff: redirect failed (counsellor leg already placed)")
        await repo.finalize_call(
            db, call_id, outcome="transfer_failed",
        )
        raise HandoffError(f"redirect failed: {e}") from e

    # 3. Mark the call as transferred + assigned.
    call.assigned_counsellor_id = counsellor.id
    call.outcome = "transferred_to_counsellor"
    await db.flush()

    return TransferResult(
        call_id=call_id,
        counsellor_id=counsellor.id,
        counsellor_phone=counsellor.phone,
        conference_room=conference_room,
        counsellor_request_uuid=result.request_uuid,
    )
