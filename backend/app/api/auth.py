"""Auth REST endpoints: login, signup, password reset, current-user info."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, hash_password, issue_token, verify_password
from app.db import models
from app.db.repo import create_user, get_user_by_email, get_user_by_id
from app.db.session import get_db
from app.schemas.auth import (
    ForgotIn,
    LoginRequest,
    ResetIn,
    SignupIn,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# Module-level password-reset token store.
# token -> (user_id, expires_at). In-memory by design (demo mode).
_RESET_TOKENS: dict[str, tuple[int, datetime]] = {}
_RESET_TTL = timedelta(hours=1)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await get_user_by_email(db, body.email)
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = issue_token(str(user.id), user.role)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/signup", response_model=TokenResponse)
async def signup(body: SignupIn, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Public signup. Always creates a counsellor — never an admin."""
    existing = await get_user_by_email(db, body.email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = await create_user(
        db,
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        role="counsellor",  # hard-coded — public signup never grants admin
    )
    # create_user uses model defaults (is_active=True). Make availability=True
    # for newly-signed-up counsellors so they can immediately take handoffs.
    user.is_available = True
    user.is_active = True
    await db.commit()
    await db.refresh(user)

    token = issue_token(str(user.id), user.role)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotIn, db: AsyncSession = Depends(get_db)
) -> dict:
    """Issue a single-use reset token. Demo mode returns it in the response."""
    user = await get_user_by_email(db, body.email)
    response: dict = {
        "ok": True,
        "message": "If the email exists, a reset link has been sent.",
        "debug_token": None,
        "debug_user_email": None,
    }
    if user is not None and user.is_active:
        token = secrets.token_urlsafe(24)
        _RESET_TOKENS[token] = (
            int(user.id),
            datetime.now(timezone.utc) + _RESET_TTL,
        )
        response["debug_token"] = token
        response["debug_user_email"] = user.email
    return response


@router.post("/reset-password")
async def reset_password(
    body: ResetIn, db: AsyncSession = Depends(get_db)
) -> dict:
    entry = _RESET_TOKENS.get(body.token)
    if entry is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    user_id, expires_at = entry
    if datetime.now(timezone.utc) >= expires_at:
        _RESET_TOKENS.pop(body.token, None)
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user = await get_user_by_id(db, user_id)
    if user is None:
        _RESET_TOKENS.pop(body.token, None)
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    user.password_hash = hash_password(body.new_password)
    await db.commit()
    _RESET_TOKENS.pop(body.token, None)  # one-time use
    return {"ok": True}


me_router = APIRouter(prefix="/api", tags=["auth"])


@me_router.get("/me", response_model=UserOut)
async def me(user: models.User = Depends(current_user)) -> UserOut:
    return UserOut.model_validate(user)


@me_router.put("/me/availability", response_model=UserOut)
async def set_availability(
    is_available: bool,
    user: models.User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user.is_available = is_available
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)
