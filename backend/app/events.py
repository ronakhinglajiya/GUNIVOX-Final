"""In-process pub/sub for live UI updates.

The bridge / dialer / REST endpoints `publish()` small JSON-serialisable dicts.
Each connected `/api/live` WebSocket has a subscriber queue that drains
events to the browser.

Lightweight — no Redis required. Works for our single-process backend and
the 1–5 concurrent UI clients we target. If you ever go multi-worker, swap
this for Redis pub/sub or NATS — the EventBus interface stays the same.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("gunivox.events")


class EventBus:
    """Fanout pub/sub. publish() never blocks; slow subscribers lose events."""

    def __init__(self, max_queue: int = 256) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._max_queue = max_queue
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subs.add(q)
        log.debug("subscriber added (total=%d)", len(self._subs))
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subs.discard(q)
        log.debug("subscriber removed (total=%d)", len(self._subs))

    def publish(self, event_type: str, **payload: Any) -> None:
        """Synchronous-friendly publish: fire-and-forget, never raises."""
        event = {"type": event_type, **payload}
        dropped = 0
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            log.warning("event %s: dropped for %d slow subscribers", event_type, dropped)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


_bus = EventBus()


def get_event_bus() -> EventBus:
    return _bus
