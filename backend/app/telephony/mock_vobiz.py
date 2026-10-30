"""Mock Vobiz WebSocket harness for local end-to-end testing.

Mimics Vobiz's bidirectional media stream so we can develop and test the
brain + bridge without burning real telephony minutes.

Protocol (simplified version of Plivo/Vobiz <Stream>):
  - Client connects to /mock/stream/{call_id}
  - Client sends JSON 'start' message: {"event": "start", "language": "gu"}
  - Client sends binary PCM frames (16kHz mono L16) for user audio
  - Client sends JSON 'utterance_end' to flag end-of-turn (mock VAD)
  - Server sends back binary audio (Sarvam Bulbul TTS bytes)
  - Server sends JSON status updates: {"event": "transcript", "text": "..."}

This is intentionally similar to but not exactly Plivo XML <Stream> format,
since Phase 2 will write the actual Vobiz adapter.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.brain.engine import BrainEngine
from app.brain.llm import LLM
from app.brain.rag import RAGEngine
from app.brain.tts import StreamingTTS
from app.clients import get_openai_client
from app.config import get_settings
from app.conversation import TurnHistory, detect_language

log = logging.getLogger("gunivox.mock_vobiz")
router = APIRouter(tags=["mock-vobiz"])

_engine: BrainEngine | None = None


def _get_engine() -> BrainEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        oai = get_openai_client()
        rag = RAGEngine(s.faiss_index_path, s.metadata_mapping_path, oai)
        llm = LLM(oai, s.openai_model, s.system_prompt)
        tts_stream = StreamingTTS(
            api_key=s.sarvam_api_key,
            base_url=s.sarvam_base_url,
            model=s.sarvam_tts_model,
            speaker=s.sarvam_tts_speaker,
            sample_rate=16000,
        )
        _engine = BrainEngine(rag, llm, tts_stream)
    return _engine


@router.websocket("/mock/stream/{call_id}")
async def mock_stream(ws: WebSocket, call_id: str) -> None:
    """Bidirectional audio stream for local testing.

    Expected client flow per turn:
      1. send JSON {"event": "start", "language": "gu"}              (once)
      2. send JSON {"event": "user_text", "text": "..."}              (skip audio)
         OR send binary audio frames + JSON {"event": "utterance_end"}
      3. receive binary audio chunks
      4. receive JSON {"event": "turn_done"}
    """
    await ws.accept()
    log.info("mock call %s connected", call_id)

    history = TurnHistory(call_id=call_id)
    language = "gu"
    audio_buffer: bytearray = bytearray()
    engine = _get_engine()

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            if "text" in msg and msg["text"] is not None:
                payload = json.loads(msg["text"])
                event = payload.get("event")

                if event == "start":
                    language = payload.get("language", "gu")
                    log.info("mock call %s start language=%s", call_id, language)

                elif event == "user_text":
                    user_text = payload.get("text", "")
                    if not user_text.strip():
                        continue
                    detected = detect_language(user_text, fallback=language)
                    await _handle_turn(ws, engine, history, user_text, detected)

                elif event == "utterance_end":
                    if not audio_buffer:
                        continue
                    # In real flow this would go to Sarvam STT; for mock
                    # without STT plumbed, the test client should use
                    # 'user_text' events instead. Stub:
                    log.warning("mock call %s got %d audio bytes but no STT wired", call_id, len(audio_buffer))
                    audio_buffer.clear()

                elif event == "stop":
                    log.info("mock call %s stop", call_id)
                    break

            elif "bytes" in msg and msg["bytes"] is not None:
                audio_buffer.extend(msg["bytes"])

    except WebSocketDisconnect:
        log.info("mock call %s disconnected", call_id)
    finally:
        log.info(
            "mock call %s ended turns=%d lang=%s", call_id, len(history), language
        )


async def _handle_turn(
    ws: WebSocket,
    engine: BrainEngine,
    history: TurnHistory,
    user_text: str,
    language: str,
) -> None:
    await ws.send_text(
        json.dumps({"event": "user_transcript", "text": user_text, "language": language})
    )
    async for chunk in engine.turn(user_text, history, language=language):
        if chunk.audio:
            await ws.send_bytes(chunk.audio)
        await ws.send_text(
            json.dumps(
                {
                    "event": "assistant_text",
                    "text": chunk.text,
                    "first_token_ms": chunk.first_token_ms,
                    "tts_ms": chunk.tts_ms,
                }
            )
        )
    await ws.send_text(json.dumps({"event": "turn_done"}))
