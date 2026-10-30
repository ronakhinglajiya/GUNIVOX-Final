"""Dashboard stats endpoint."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_counsellor_or_admin
from app.db import repo
from app.db.session import get_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def stats(
    _=Depends(require_counsellor_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await repo.dashboard_stats(db)
