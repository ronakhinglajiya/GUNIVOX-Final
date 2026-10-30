"""Sarvam Bulbul v2 TTS — batch HTTP + streaming WebSocket.

`TTS` (HTTP):       used by scripts/generate_fillers.py to pre-render WAV files.
`StreamingTTS` (WS): used at runtime — stream LLM tokens in, get PCM chunks
                    out as soon as Sarvam has enough text. Much lower
                    first-chunk latency than the HTTP endpoint.

Sarvam streaming protocol (verified 2026-05):
  wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v2
  Auth: Api-Subscription-Key
  First send: {"type": "config", "data": {...}}
  Each text: {"type": "text", "data": {"text": "..."}}
  Receive:    {"type": "audio", "data": {"audio": "<base64 linear16>"}}
  Close:      {"type": "flush"} then read until {"type":"events","data":{"event_type":"final"}}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator

import httpx
import websockets

log = logging.getLogger("gunivox.tts")

LANG_CODE_MAP = {
    "gu": "gu-IN",
    "hi": "hi-IN",
    "en": "en-IN",
}

# Sarvam Bulbul v2 speakers (verified 2026-05):
# Female: anushka, manisha, vidya, arya, priya, neha, pooja, simran, kavya
# Male:   abhilash, karun, hitesh, aditya, rahul, rohan, amit, dev
DEFAULT_SPEAKER = "anushka"


class TTS:
    """One-shot HTTP TTS. Used for pre-rendering filler WAVs at build time."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        api_key: str,
        url: str,
        model: str = "bulbul:v2",
        sample_rate: int = 16000,
        speaker: str = DEFAULT_SPEAKER,
    ) -> None:
        self.http = http_client
        self.api_key = api_key
        self.url = url
        self.model = model
        self.sample_rate = sample_rate
        self.speaker = speaker

    async def synthesize(self, text: str, language: str = "gu") -> bytes:
        if not text.strip():
            return b""
        payload = {
            "inputs": [text],
            "target_language_code": LANG_CODE_MAP.get(language, "gu-IN"),
            "speaker": self.speaker,
            "model": self.model,
            "speech_sample_rate": self.sample_rate,
            "enable_preprocessing": True,
        }
        resp = await self.http.post(
            self.url,
            headers={
                "Api-Subscription-Key": self.api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Sarvam TTS {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        audios = data.get("audios") or []
        return base64.b64decode(audios[0]) if audios else b""


class StreamingTTS:
    """Sarvam streaming TTS over WebSocket.

    Two modes:
      - Stateless: call `.synthesize(text_iter, language)` — opens a fresh WS
        each call. Simple but pays ~300ms TCP+TLS+config per turn.
      - Session (recommended for calls): use `async with .session(language) as s:`
        which opens ONE WS for the whole call and reuses it across turns.

    Each pcm_chunk is raw linear16 (signed-16-bit-LE) PCM at `sample_rate` mono.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "wss://api.sarvam.ai",
        model: str = "bulbul:v2",
        speaker: str = DEFAULT_SPEAKER,
        sample_rate: int = 16000,
        # Sarvam accepts min_buffer_size in 30..50. 40 is fastest empirically
        # (~1.3s first-audio vs ~2.2s at 30, fails to fire at 50 with short text).
        min_buffer_size: int = 40,
        max_chunk_length: int = 100,
        enable_preprocessing: bool = True,
    ) -> None:
        self.api_key = api_key
        self.base_url = (
            base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        )
        self.model = model
        self.speaker = speaker
        self.sample_rate = sample_rate
        self.min_buffer_size = min_buffer_size
        self.max_chunk_length = max_chunk_length
        self.enable_preprocessing = enable_preprocessing

    def _connect_url(self) -> str:
        return f"{self.base_url}/text-to-speech/ws?model={self.model}"

    def _config_payload(self, language: str) -> dict:
        return {
            "type": "config",
            "data": {
                "target_language_code": LANG_CODE_MAP.get(language, "gu-IN"),
                "speaker": self.speaker,
                "speech_sample_rate": self.sample_rate,
                "model": self.model,
                "enable_preprocessing": self.enable_preprocessing,
                "output_audio_codec": "linear16",
                "min_buffer_size": self.min_buffer_size,
                "max_chunk_length": self.max_chunk_length,
            },
        }

    def session(self, language: str = "gu") -> "StreamingTTSSession":
        """Create a session that holds one persistent WS across multiple turns."""
        return StreamingTTSSession(self, language)

    async def synthesize(
        self,
        text_chunks: AsyncIterator[str],
        language: str = "gu",
        first_audio_timeout: float = 8.0,
        idle_timeout: float = 1.5,
    ) -> AsyncIterator[bytes]:
        """Stateless one-shot: open WS, synthesize, close. ~300ms slower than session()."""
        async with self.session(language) as sess:
            async for pcm in sess.synthesize(text_chunks, first_audio_timeout, idle_timeout):
                yield pcm


class StreamingTTSSession:
    """One persistent Sarvam TTS WebSocket, reusable across many turns.

    Opens the WS + sends config in `connect()`. `synthesize()` can be called
    repeatedly — each call pumps text + flush and yields audio until idle.
    """

    def __init__(self, tts: StreamingTTS, language: str = "gu") -> None:
        self._tts = tts
        self._language = language
        self._ws = None
        self._lock = asyncio.Lock()  # one turn at a time

    async def __aenter__(self) -> "StreamingTTSSession":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def connect(self) -> None:
        # Sarvam closes the TTS WS after ~2–3 minutes of idle. If our cached
        # ws is closed, drop it so we open fresh below. Also call .close()
        # on a stale handle to release sockets.
        if self._ws is not None:
            if getattr(self._ws, "closed", False):
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
            else:
                return
        url = self._tts._connect_url()
        headers = {"Api-Subscription-Key": self._tts.api_key}
        # ping_interval=8 (2026-05-27): keep the socket warm with frequent
        # WS-level pings between turns so it doesn't go half-dead during the
        # caller's pauses. Default (20s) let the WS stale out mid-call on a
        # live call (turn 8 → 7.3s dead-air spike). 8s keeps it alive across
        # normal think-gaps while still detecting a truly dropped link fast.
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=None,
            ping_interval=8,
            ping_timeout=20,
        )
        await self._ws.send(json.dumps(self._tts._config_payload(self._language)))

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def synthesize(
        self,
        text_chunks: AsyncIterator[str],
        first_audio_timeout: float = 5.0,
        idle_timeout: float = 3.0,
    ) -> AsyncIterator[bytes]:
        """Pump text + flush, yield PCM chunks until Sarvam goes idle.

        first_audio_timeout 8→3→2→5.0s (2026-05-27): 2.0s was too aggressive —
        Sarvam's first audio on a fresh WS routinely takes 2-3s, so 2.0s
        FALSE-fired 'stale WS', forcing an unnecessary reconnect+retry that
        itself added latency. 5.0s clears Sarvam's real first-audio range
        while still catching a genuinely dead socket. (ping_interval=8 keeps
        the WS warm so true staleness is rare anyway.)

        idle_timeout 1.5→3.0s (2026-05-27): THIS was cutting long replies
        mid-sentence. After max_tokens went 120→240, replies are ~2x longer
        and Sarvam streams them in segments with >1.5s gaps between bursts.
        The 1.5s idle cutoff fired DURING one of those gaps and dropped the
        rest of the audio — the DB had the full text but the caller heard it
        stop midway. 3.0s tolerates Sarvam's inter-segment pauses; the clean
        end is still the 'final' event, idle is only the fallback.

        Retry-on-empty (2026-05-26): if the WS dies with ZERO audio produced
        (stale half-dead socket), we reconnect ONCE and re-synthesize the
        SAME text on a fresh WS — so the caller actually hears the reply
        (~1s late) instead of total silence. A live call hit this on turn 4
        ('your voice cut out'). The text is buffered as it streams so the
        replay has the full sentence even though the original stream was
        consumed. Retry fires ONLY on the zero-audio failure path; healthy
        turns stream byte-for-byte as before."""
        import time as _time

        async with self._lock:
            # Buffer text as it streams so a failed attempt can be replayed.
            collected: list[str] = []

            async def _collecting(src: AsyncIterator[str]) -> AsyncIterator[str]:
                async for c in src:
                    if c:
                        collected.append(c)
                    yield c

            async def _replay() -> AsyncIterator[str]:
                for c in collected:
                    yield c

            async def _attempt(source: AsyncIterator[str]) -> AsyncIterator[bytes]:
                """One synth attempt on self._ws. On a zero-audio death it
                closes the WS and sets self._ws=None so the caller can retry.
                Keeps draining `source` into `collected` even if sends fail,
                so the replay buffer ends up with the full text."""
                if self._ws is None:
                    await self.connect()
                ws = self._ws
                assert ws is not None
                pump_done_at: float | None = None
                send_alive = True

                async def pump_text() -> None:
                    nonlocal pump_done_at, send_alive
                    try:
                        async for chunk in source:
                            if not chunk:
                                continue
                            if not send_alive:
                                continue  # WS dead — keep draining into collected
                            try:
                                await ws.send(
                                    json.dumps({"type": "text", "data": {"text": chunk}})
                                )
                            except (websockets.ConnectionClosed, OSError):
                                send_alive = False
                        if send_alive:
                            try:
                                await ws.send(json.dumps({"type": "flush"}))
                            except (websockets.ConnectionClosed, OSError):
                                send_alive = False
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        log.exception("tts text pump failed")
                    finally:
                        pump_done_at = _time.time()

                pump_task = asyncio.create_task(pump_text())
                last_audio_at: float | None = None
                try:
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        except asyncio.TimeoutError:
                            if pump_done_at is None:
                                continue
                            now = _time.time()
                            if last_audio_at is None:
                                if now - pump_done_at > first_audio_timeout:
                                    log.warning(
                                        "tts no audio after %.1fs — discarding stale WS",
                                        first_audio_timeout,
                                    )
                                    try:
                                        await ws.close()
                                    except Exception:
                                        pass
                                    self._ws = None
                                    break
                            else:
                                if now - last_audio_at > idle_timeout:
                                    break
                            continue
                        except websockets.ConnectionClosed:
                            self._ws = None
                            break

                        if isinstance(raw, bytes):
                            continue
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        msg_type = msg.get("type")
                        if msg_type == "audio":
                            b64 = (msg.get("data") or {}).get("audio")
                            if b64:
                                last_audio_at = _time.time()
                                yield base64.b64decode(b64)
                        elif msg_type == "events":
                            if (msg.get("data") or {}).get("event_type") == "final":
                                break
                        elif msg_type == "error":
                            log.error("sarvam tts error: %s", msg)
                            break
                finally:
                    pump_task.cancel()
                    try:
                        await pump_task
                    except BaseException:
                        pass

            # Attempt 1 — stream the live LLM text (buffered into `collected`).
            got_audio = False
            async for pcm in _attempt(_collecting(text_chunks)):
                got_audio = True
                yield pcm

            # Attempt 2 — only if the WS died with zero audio. Reconnect and
            # replay the buffered text once so the caller hears the reply.
            if not got_audio and self._ws is None and collected:
                log.warning(
                    "tts retry: re-synthesizing %d buffered chunk(s) on fresh WS",
                    len(collected),
                )
                try:
                    await self.connect()
                    async for pcm in _attempt(_replay()):
                        yield pcm
                except Exception:
                    log.exception("tts retry failed")
