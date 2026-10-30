"""FastAPI auth dependencies: current_user, require_admin, require_counsellor."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_token
from app.db.models import User
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# Throttle last_seen_at writes to once per 60s per user (per worker process)
# so the admin Team page sees fresh presence without hammering the DB on
# every authenticated request. Module-level dict — lost on restart, which
# just means the first request after restart triggers a write. Fine.
_LAST_SEEN_TTL = timedelta(seconds=60)
_last_seen_cache: dict[int, datetime] = {}


async def current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token") from None
    row = await db.execute(select(User).where(User.id == user_id))
    user = row.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Refresh last_seen_at (throttled). Fire-and-forget commit — if it fails,
    # we don't want to fail the user's actual request.
    now = datetime.utcnow()
    last_write = _last_seen_cache.get(user.id)
    if last_write is None or (now - last_write) >= _LAST_SEEN_TTL:
        _last_seen_cache[user.id] = now
        try:
            await db.execute(
                update(User).where(User.id == user.id).values(last_seen_at=now)
            )
            await db.commit()
            user.last_seen_at = now
        except Exception:
            await db.rollback()

    return user


def require_role(*roles: str):
    async def _check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {' or '.join(roles)}",
            )
        return user

    return _check


require_admin = require_role("admin")
require_counsellor_or_admin = require_role("admin", "counsellor")
