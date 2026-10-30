"""Health-check endpoints.

  GET /api/health        -> {"status":"ok"} (always cheap)
  GET /api/health/db     -> verifies DB engine can connect
  GET /api/health/full   -> deep check: DB + OpenAI + Sarvam + Vobiz config
                            (CACHED 30s — see _health_full_cache)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from app.clients import get_http_client, get_openai_client
from app.config import get_settings
from app.db.session import engine

log = logging.getLogger("gunivox.health")
router = APIRouter(tags=["health"])

# Cached /health/full result. The frontend dashboard polls this endpoint
# frequently; without the cache each poll runs live network probes (OpenAI
# embedding + Sarvam HTTP) that cost ~1-5s. With a 30s TTL the first call
# does the real probes and the next 29s of polls return instantly.
# Status dashboards don't need sub-second freshness.
_HEALTH_FULL_TTL = 30.0  # seconds
_health_full_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_health_full_lock = asyncio.Lock()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
async def health_db() -> dict[str, str]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": "reachable"}
    except Exception as e:
        return {"status": "error", "db": "unreachable", "detail": str(e)}


async def _check_openai() -> dict[str, Any]:
    try:
        oai = get_openai_client()
        await asyncio.wait_for(
            oai.embeddings.create(model="text-embedding-3-small", input="ok"),
            timeout=5,
        )
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _check_sarvam(http: httpx.AsyncClient) -> dict[str, Any]:
    try:
        r = await http.get("https://api.sarvam.ai/", timeout=5)
        return {"ok": r.status_code < 500, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _check_vobiz_config() -> dict[str, Any]:
    s = get_settings()
    out: dict[str, Any] = {
        "auth_id_set": bool(s.vobiz_auth_id),
        "auth_token_set": bool(s.vobiz_auth_token),
        "caller_id_set": bool(s.vobiz_caller_id),
        "public_base_url_set": bool(s.vobiz_public_base_url),
    }
    out["ok"] = all(v for k, v in out.items() if k != "ok")
    return out


async def _build_health_full() -> dict[str, Any]:
    """Actually run the four probes. Called only on a cache miss."""
    http = get_http_client()
    db_check, openai_check, sarvam_check, vobiz_check = await asyncio.gather(
        health_db(),
        _check_openai(),
        _check_sarvam(http),
        _check_vobiz_config(),
        return_exceptions=False,
    )
    overall_ok = (
        db_check.get("status") == "ok"
        and openai_check.get("ok")
        and sarvam_check.get("ok")
        and vobiz_check.get("ok")
    )
    return {
        "status": "ok" if overall_ok else "degraded",
        "db": db_check,
        "openai": openai_check,
        "sarvam": sarvam_check,
        "vobiz_config": vobiz_check,
        "cached_at": time.time(),
    }


@router.get("/health/full")
async def health_full() -> dict[str, Any]:
    """Cached 30s. Concurrent requests during a miss share one rebuild
    via _health_full_lock — prevents a thundering-herd of dashboard polls
    from each launching their own OpenAI + Sarvam probes."""
    now = time.time()
    cached = _health_full_cache["data"]
    if cached is not None and now < _health_full_cache["expires_at"]:
        return {**cached, "age_s": round(now - cached["cached_at"], 1)}

    async with _health_full_lock:
        # Re-check after acquiring the lock — another request may have
        # rebuilt while we were waiting.
        now = time.time()
        cached = _health_full_cache["data"]
        if cached is not None and now < _health_full_cache["expires_at"]:
            return {**cached, "age_s": round(now - cached["cached_at"], 1)}

        data = await _build_health_full()
        _health_full_cache["data"] = data
        _health_full_cache["expires_at"] = now + _HEALTH_FULL_TTL
        return {**data, "age_s": 0.0}
