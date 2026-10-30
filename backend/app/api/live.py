"""WebSocket endpoint that streams real-time events to the admin UI.

Browser connects to `wss://api.gunivox.online/api/live?token=<JWT>`. Token
is validated against the same auth.decode_token we use everywhere else,
so an expired/invalid token gets closed before the WS handshake completes.

After accept we send a `hello` frame, then forward every published event.
A `ping` is sent every 25s when idle so intermediate proxies (Cloudflare)
don't close the socket as idle.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.auth.jwt import decode_token
from app.events import get_event_bus

log = logging.getLogger("gunivox.live")
router = APIRouter(tags=["live"])

IDLE_PING_SECONDS = 25.0


@router.websocket("/api/live")
async def live(ws: WebSocket) -> None:
    token = ws.query_params.get("token")
    payload = decode_token(token) if token else None
    if not payload or "sub" not in payload:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    bus = get_event_bus()
    q = await bus.subscribe()
    user_id = payload.get("sub")
    role = payload.get("role")
    log.info("live ws connected user=%s role=%s (subs=%d)", user_id, role, bus.subscriber_count)

    await ws.send_text(json.dumps({"type": "hello", "user_id": user_id, "role": role}))

    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=IDLE_PING_SECONDS)
                await ws.send_text(json.dumps(event))
            except asyncio.TimeoutError:
                # Idle keepalive
                await ws.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        log.info("live ws disconnected user=%s", user_id)
    except Exception:
        log.exception("live ws error")
    finally:
        await bus.unsubscribe(q)
