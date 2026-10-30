"""Shared async HTTP / OpenAI clients. One per process."""
from __future__ import annotations

from functools import lru_cache

import httpx
from openai import AsyncOpenAI

from app.config import get_settings


@lru_cache
def get_openai_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(api_key=settings.openai_api_key)


@lru_cache
def get_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
    )


async def close_clients() -> None:
    await get_http_client().aclose()
    await get_openai_client().close()
