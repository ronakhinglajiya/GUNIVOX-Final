"""Callback scheduling REST."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, require_counsellor_or_admin
from app.db import models
from app.db import repo
from app.db.session import get_db
from app.schemas.callback import CallbackIn, CallbackList, CallbackOut, CallbackUpdate

router = APIRouter(prefix="/api/callbacks", tags=["callbacks"])


@router.get("", response_model=CallbackList)
async def list_endpoint(
    status: str | None = None,
    mine: bool = False,
    # 2026-06-03: raised 200 → 1000 to match the Pipeline page's fetch.
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CallbackList:
    # 2026-06-04: dropped the auto-filter to "counsellor's own callbacks".
    # Counsellor now sees all callbacks like admin does — they're continuing
    # existing campaigns and need full visibility. The `mine=true` query
    # param is still honoured if explicitly passed (admin or counsellor).
    counsellor_id = user.id if mine else None
    rows, total = await repo.list_callbacks(
        db, counsellor_id=counsellor_id, status=status, limit=limit, offset=offset
    )
    return CallbackList(total=total, items=[CallbackOut.model_validate(r) for r in rows])


@router.post("", response_model=CallbackOut, status_code=201)
async def create_endpoint(
    body: CallbackIn,
    user: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CallbackOut:
    cb = await repo.create_callback(
        db,
        lead_id=body.lead_id,
        scheduled_at=body.scheduled_at,
        counsellor_id=body.counsellor_id or (user.id if user.role == "counsellor" else None),
        notes=body.notes,
    )
    await db.commit()
    await db.refresh(cb)
    return CallbackOut.model_validate(cb)


@router.put("/{cb_id}", response_model=CallbackOut)
async def update_endpoint(
    cb_id: int,
    body: CallbackUpdate,
    _: models.User = Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> CallbackOut:
    cb = await repo.update_callback(db, cb_id, **body.model_dump(exclude_none=True))
    if not cb:
        raise HTTPException(404, "Callback not found")
    await db.commit()
    await db.refresh(cb)
    return CallbackOut.model_validate(cb)
