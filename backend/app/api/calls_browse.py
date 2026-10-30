"""Call-browsing REST: list, detail with transcript, recording download.

The existing app/api/calls.py is the originator (POST /api/calls/originate).
This file adds the read-side endpoints needed by the admin frontend.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_counsellor_or_admin
from app.config import get_settings
from app.db import models
from app.db import repo
from app.db.session import get_db
from app.schemas.call import CallDetail, CallList, CallOut, TurnOut

router = APIRouter(prefix="/api/calls", tags=["calls"])


@router.get("", response_model=CallList)
async def list_endpoint(
    lead_id: int | None = None,
    # 2026-06-03: raised 200 → 1000 because the Calls page bumped its fetch
    # to 500 to keep the temperature-chip counts honest against the full DB.
    # Anything above 200 was throwing HTTP 422 (less_than_equal violation).
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CallList:
    # 2026-06-04: dropped the per-counsellor filter. The use case is
    # "counsellor continues an existing campaign on PC #2 while admin runs
    # PC #1", so they need to see ALL calls just like admin does — not just
    # the ones assigned to them. Phone numbers are still UI-masked in the
    # frontend for the counsellor role.
    rows, total = await repo.list_calls(
        db, lead_id=lead_id, counsellor_id=None, limit=limit, offset=offset
    )
    return CallList(total=total, items=[CallOut.model_validate(r) for r in rows])


@router.get("/{call_id}", response_model=CallDetail)
async def detail_endpoint(
    call_id: int,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CallDetail:
    call = await repo.get_call(db, call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    turns = await repo.list_turns(db, call_id)
    return CallDetail(
        **CallOut.model_validate(call).model_dump(),
        turns=[TurnOut.model_validate(t) for t in turns],
    )


@router.get("/{call_id}/recording")
async def recording_endpoint(
    call_id: int,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    call = await repo.get_call(db, call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    if not call.recording_path:
        raise HTTPException(404, "No recording for this call")
    path = Path(call.recording_path)
    if not path.is_absolute():
        path = get_settings().recordings_dir / path.name
    if not path.exists():
        raise HTTPException(404, "Recording file missing on disk")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"call-{call_id}.wav",
    )
