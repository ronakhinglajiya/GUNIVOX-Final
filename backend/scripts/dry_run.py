"""Local dry-run client for the mock Vobiz WebSocket endpoint.

Connects to ws://localhost:8000/mock/stream/{call_id}, sends one user_text,
collects the AI audio response chunks, concatenates them, saves a WAV
to backend/recordings/{call_id}.wav so you can play it.

Usage (from backend/, after `.\\scripts\\dev.ps1` in another terminal):
    ../venv/Scripts/python.exe -m scripts.dry_run
    ../venv/Scripts/python.exe -m scripts.dry_run "MBA की fees कितनी है?" hi
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.telephony.audio_utils import pcm_to_wav, strip_wav_header  # noqa: E402

DEFAULT_QUERY = "B.Tech Computer Engineering ની fees કેટલી છે?"
DEFAULT_LANG = "gu"


async def dry_run(query: str, language: str, host: str = "localhost", port: int = 8000) -> int:
    call_id = f"dry-{uuid.uuid4().hex[:8]}"
    url = f"ws://{host}:{port}/mock/stream/{call_id}"
    print(f"→ {url}")
    print(f"  Query    : {query}")
    print(f"  Language : {language}")
    print()

    audio_chunks: list[bytes] = []
    assistant_texts: list[str] = []
    user_transcript: str | None = None
    first_audio_at: float | None = None
    t0 = 0.0

    try:
        async with websockets.connect(url, max_size=None) as ws:
            t0 = time.perf_counter()
            await ws.send(json.dumps({"event": "start", "language": language}))
            await ws.send(json.dumps({"event": "user_text", "text": query}))

            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    if first_audio_at is None:
                        first_audio_at = time.perf_counter()
                        ttfa = int((first_audio_at - t0) * 1000)
                        print(f"  [t={ttfa:4d}ms] first audio chunk ({len(msg)} bytes)")
                    else:
                        elapsed = int((time.perf_counter() - t0) * 1000)
                        print(f"  [t={elapsed:4d}ms] audio chunk ({len(msg)} bytes)")
                    audio_chunks.append(msg)
                else:
                    payload = json.loads(msg)
                    event = payload.get("event")
                    if event == "user_transcript":
                        user_transcript = payload.get("text")
                    elif event == "assistant_text":
                        text = payload.get("text", "")
                        assistant_texts.append(text)
                        elapsed = int((time.perf_counter() - t0) * 1000)
                        ftk = payload.get("first_token_ms")
                        tts = payload.get("tts_ms")
                        print(
                            f"  [t={elapsed:4d}ms] AI text: \"{text[:60]}{'…' if len(text) > 60 else ''}\""
                            + (f"  (first_tok={ftk}ms, tts={tts}ms)" if ftk or tts else "")
                        )
                    elif event == "turn_done":
                        break
                    else:
                        print(f"  [event={event}] {payload}")

            await ws.send(json.dumps({"event": "stop"}))

    except ConnectionRefusedError:
        print(
            f"\n❌ Could not connect to {url}.\n"
            f"   Start the server first:  .\\scripts\\dev.ps1\n"
            f"   (must be running before you run this script)"
        )
        return 1
    except Exception as e:
        print(f"\n❌ {type(e).__name__}: {e}")
        return 2

    ttfa_ms = int((first_audio_at - t0) * 1000) if first_audio_at else -1
    total_ms = int((time.perf_counter() - t0) * 1000)

    all_pcm = b""
    sample_rate = 16000
    for chunk in audio_chunks:
        pcm, sr, _ = strip_wav_header(chunk)
        all_pcm += pcm
        sample_rate = sr

    out_dir = Path(__file__).resolve().parent.parent / "recordings"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{call_id}.wav"
    out_path.write_bytes(pcm_to_wav(all_pcm, sample_rate=sample_rate))
    audio_seconds = len(all_pcm) / (sample_rate * 2)

    print()
    print("─" * 60)
    print(f"  TTFA            : {ttfa_ms} ms")
    print(f"  Total response  : {total_ms} ms")
    print(f"  Audio chunks    : {len(audio_chunks)}")
    print(f"  Full reply      : {' '.join(assistant_texts)}")
    print(f"  Output WAV      : {out_path}  ({audio_seconds:.1f}s @ {sample_rate}Hz)")
    print("─" * 60)
    print(f"  ▶ Play it: start \"{out_path}\"   (PowerShell)")
    return 0


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    language = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LANG
    rc = asyncio.run(dry_run(query, language))
    sys.exit(rc)


if __name__ == "__main__":
    main()
