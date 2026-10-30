"""Vobiz REST client (Plivo-shape API).

Originate, hangup, conference, redirect. Auth = HTTP Basic with auth_id + auth_token.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class OriginatedCall:
    request_uuid: str
    raw: dict


class VobizClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        auth_id: str,
        auth_token: str,
        base_url: str = "https://api.vobiz.in",
    ) -> None:
        if not auth_id or not auth_token:
            raise ValueError("Vobiz auth_id and auth_token required")
        self.http = http
        self.auth_id = auth_id
        self.auth_token = auth_token
        self.base_url = base_url.rstrip("/")
        self._account_path = f"{self.base_url}/api/v1/Account/{auth_id}"
        self._auth = httpx.BasicAuth(auth_id, auth_token)

    async def originate(
        self,
        to: str,
        from_: str,
        answer_url: str,
        answer_method: str = "POST",
        hangup_url: str | None = None,
        ring_timeout: int = 45,
    ) -> OriginatedCall:
        payload = {
            "to": to,
            "from": from_,
            "answer_url": answer_url,
            "answer_method": answer_method,
            "ring_timeout": ring_timeout,
        }
        if hangup_url:
            payload["hangup_url"] = hangup_url
            payload["hangup_method"] = "POST"
        resp = await self.http.post(
            f"{self._account_path}/Call/", auth=self._auth, json=payload
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Vobiz originate {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return OriginatedCall(request_uuid=data.get("request_uuid", ""), raw=data)

    async def hangup(self, call_uuid: str) -> None:
        """Tell Vobiz to terminate this leg. Retries on transient transport
        errors (DNS blip, connection reset) because the Windows resolver
        intermittently fails to resolve api.vobiz.ai — a one-shot DELETE
        would leave the call up forever, with the caller hearing dead air.

        Backoff: immediate, +0.5s, +1.5s (3 attempts total, max ~2s extra
        latency on hangup). Only retries on transport-level errors, NOT on
        HTTP 4xx/5xx responses (those mean Vobiz answered — different
        problem, no retry helps). 404 is treated as success (call already
        ended on Vobiz's side).
        """
        import asyncio
        import logging
        import httpx

        log = logging.getLogger("gunivox.vobiz")
        last_exc: Exception | None = None
        backoff = (0.0, 0.5, 1.5)  # delays BEFORE each attempt (3 attempts)

        for attempt, delay in enumerate(backoff, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self.http.delete(
                    f"{self._account_path}/Call/{call_uuid}/", auth=self._auth
                )
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError,
                    httpx.ConnectTimeout, httpx.ReadTimeout, OSError) as e:
                last_exc = e
                log.warning(
                    "vobiz hangup attempt %d/%d for %s failed transport: %s",
                    attempt, len(backoff), call_uuid, e,
                )
                continue

            if resp.status_code == 404:
                # 404 is benign — the call already ended on Vobiz's side
                # (caller hung up; Hangup webhook fired; client double-tapped
                # our hangup endpoint).
                log.warning(
                    "vobiz hangup 404 for %s — call already ended; treating as success",
                    call_uuid,
                )
                return
            if resp.status_code >= 400:
                # HTTP error → won't be fixed by a retry; surface immediately.
                raise RuntimeError(f"Vobiz hangup {resp.status_code}: {resp.text[:200]}")
            # 2xx → success
            if attempt > 1:
                log.info("vobiz hangup for %s succeeded on attempt %d", call_uuid, attempt)
            return

        # All 3 attempts failed at transport level.
        raise RuntimeError(
            f"Vobiz hangup for {call_uuid} failed after {len(backoff)} attempts: {last_exc}"
        ) from last_exc

    async def redirect(self, call_uuid: str, answer_url: str) -> None:
        """Redirect an active leg to a new AnswerXML URL (cold transfer)."""
        resp = await self.http.post(
            f"{self._account_path}/Call/{call_uuid}/",
            auth=self._auth,
            json={"legs": "aleg", "aleg_url": answer_url},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Vobiz redirect {resp.status_code}: {resp.text[:200]}")
