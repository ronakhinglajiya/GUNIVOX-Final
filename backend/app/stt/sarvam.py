"""Sarvam Speech-to-Text wrappers (Saaras v3 + Saarika).

Streaming (recommended for the Vobiz bridge):
  wss://api.sarvam.ai/speech-to-text/ws?language-code=gu-IN&model=saaras:v3
        &sample_rate=16000&input_audio_codec=pcm_s16le&vad_signals=true

  Auth header:  Api-Subscription-Key: <key>

  Audio frames (we send) — each frame is a tiny WAV blob (RIFF header + PCM):
    {"audio": {"data": "<base64 wav>", "sample_rate": "16000", "encoding": "audio/wav"}}

  Transcripts (we receive):
    {"type": "data", "data": {"transcript": "...", "language_code": "gu-IN", ...}}
    {"type": "speech_start"}  | {"type": "speech_end"}     (when vad_signals=true)
"""
from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import websockets

from app.telephony.audio_utils import pcm_to_wav

LANG_CODE_MAP = {
    "gu": "gu-IN",
    "hi": "hi-IN",
    "en": "en-IN",
    "auto": "unknown",
}


@dataclass
class STTResult:
    text: str
    language: str | None
    is_final: bool


class SarvamSTT:
    """One-shot HTTP STT for sending a complete utterance file."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        api_key: str,
        base_url: str = "https://api.sarvam.ai",
        model: str = "saaras:v3",
    ) -> None:
        self.http = http_client
        self.api_key = api_key
        self.url = f"{base_url.rstrip('/')}/speech-to-text"
        self.model = model

    async def transcribe(
        self, audio: bytes, language: str = "gu", filename: str = "utterance.wav"
    ) -> STTResult:
        files = {"file": (filename, audio, "audio/wav")}
        data = {
            "model": self.model,
            "language_code": LANG_CODE_MAP.get(language, "gu-IN"),
        }
        resp = await self.http.post(
            self.url,
            headers={"Api-Subscription-Key": self.api_key},
            files=files,
            data=data,
        )
        resp.raise_for_status()
        payload = resp.json()
        return STTResult(
            text=payload.get("transcript", ""),
            language=payload.get("language_code"),
            is_final=True,
        )


class SarvamStreamSTT:
    """Saaras v3 streaming STT over WebSocket.

    Send PCM s16le 16kHz frames; receive {type: 'data', data: {transcript: ...}}
    plus speech_start/speech_end VAD signals when enabled.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "wss://api.sarvam.ai",
        model: str = "saaras:v3",
        language: str = "gu",
        sample_rate: int = 16000,
        vad_signals: bool = True,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self.vad_signals = vad_signals

    def _connect_url(self) -> str:
        lang = LANG_CODE_MAP.get(self.language, "gu-IN")
        params = (
            f"language-code={lang}"
            f"&model={self.model}"
            f"&sample_rate={self.sample_rate}"
            f"&vad_signals={'true' if self.vad_signals else 'false'}"
        )
        return f"{self.base_url}/speech-to-text/ws?{params}"

    async def stream(
        self,
        audio_frames: AsyncIterator[bytes],
        on_speech_start: callable | None = None,
    ) -> AsyncIterator[STTResult]:
        """Stream audio and yield transcripts."""
        import logging as _logging
        log = _logging.getLogger("gunivox.sarvam_stt")
        headers = {"Api-Subscription-Key": self.api_key}
        log.info("connecting to %s", self._connect_url())
        try:
            ws = await websockets.connect(
                self._connect_url(), additional_headers=headers, max_size=None
            )
        except Exception as e:
            log.error("sarvam STT WS connect FAILED: %s", e)
            raise
        log.info("sarvam STT WS connected, pumping audio")
        async with ws:
            send_task = asyncio.create_task(self._pump_audio(ws, audio_frames))
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("non-JSON: %r", raw[:200])
                        continue

                    msg_type = msg.get("type")

                    # VAD events from Saaras come as {type: "events", data: {signal_type: START_SPEECH|END_SPEECH}}
                    if msg_type == "events":
                        signal = (msg.get("data") or {}).get("signal_type")
                        log.info("sarvam VAD: %s", signal)
                        if signal == "START_SPEECH" and on_speech_start:
                            try:
                                on_speech_start()
                            except Exception:
                                log.exception("on_speech_start callback failed")
                        continue

                    if msg_type == "error":
                        log.error("sarvam error: %s", msg)
                        continue
                    if msg_type != "data":
                        continue

                    data = msg.get("data") or {}
                    transcript = (data.get("transcript") or "").strip()
                    if not transcript:
                        continue
                    log.info("transcript: %s", transcript)
                    yield STTResult(
                        text=transcript,
                        language=data.get("language_code"),
                        is_final=True,
                    )
            finally:
                send_task.cancel()
                try:
                    await send_task
                except BaseException:
                    pass

    async def _pump_audio(self, ws, frames: AsyncIterator[bytes]) -> None:
        """Buffer ~100ms of PCM before sending to Sarvam (better STT accuracy than 20ms)."""
        import logging as _logging
        log = _logging.getLogger("gunivox.sarvam_stt")
        target_bytes = int(self.sample_rate * 2 * 0.1)  # 100ms PCM L16 mono
        buf = bytearray()
        sent_count = 0
        async for frame in frames:
            if not frame:
                continue
            buf.extend(frame)
            if len(buf) < target_bytes:
                continue
            wav = pcm_to_wav(bytes(buf), sample_rate=self.sample_rate, channels=1)
            buf.clear()
            payload = base64.b64encode(wav).decode("ascii")
            await ws.send(
                json.dumps(
                    {
                        "audio": {
                            "data": payload,
                            "sample_rate": str(self.sample_rate),
                            "encoding": "audio/wav",
                        }
                    }
                )
            )
            sent_count += 1
            if sent_count == 1:
                log.info("STT first audio chunk sent")
            elif sent_count % 50 == 0:
                log.info("STT pumped %d audio chunks", sent_count)
        # Flush any remaining buffered audio at end of stream
        if buf:
            wav = pcm_to_wav(bytes(buf), sample_rate=self.sample_rate, channels=1)
            payload = base64.b64encode(wav).decode("ascii")
            try:
                await ws.send(
                    json.dumps(
                        {
                            "audio": {
                                "data": payload,
                                "sample_rate": str(self.sample_rate),
                                "encoding": "audio/wav",
                            }
                        }
                    )
                )
            except Exception:
                pass
