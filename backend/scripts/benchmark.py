"""End-to-end latency benchmark for the voice agent brain.

Measures: RAG retrieve, LLM time-to-first-token, TTS time-to-first-chunk,
and total time-to-first-audio (TTFA).

Usage (from backend/):
    ../venv/Scripts/python.exe -m scripts.benchmark
    ../venv/Scripts/python.exe -m scripts.benchmark --queries 5 --save-audio
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.engine import BrainEngine  # noqa: E402
from app.brain.llm import LLM  # noqa: E402
from app.brain.rag import RAGEngine  # noqa: E402
from app.brain.tts import TTS  # noqa: E402
from app.clients import close_clients, get_http_client, get_openai_client  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.conversation import TurnHistory, detect_language  # noqa: E402

DEFAULT_QUERIES = [
    ("gu", "B.Tech Computer Engineering ની fees કેટલી છે?"),
    ("gu", "Diploma in Automobile ની eligibility શું છે?"),
    ("hi", "MBA program की duration कितनी है?"),
    ("en", "Who is the counsellor for B.Tech Mechanical?"),
    ("gu", "મારે ગુનીવોક્સમાં admission લેવી છે, શું કરવું?"),
]


async def run_one(
    engine: BrainEngine, query: str, language: str
) -> dict[str, float | int]:
    history = TurnHistory(call_id="bench")
    t0 = time.perf_counter()

    rag_start = time.perf_counter()
    docs = await engine.rag.retrieve(query, k=3)
    rag_ms = int((time.perf_counter() - rag_start) * 1000)

    history.add_user(query, language=language)
    context = engine.rag.format_context(docs)

    first_audio_at: float | None = None
    first_token_ms: int | None = None
    first_tts_ms: int | None = None
    full_text_parts: list[str] = []

    async for chunk in engine.turn(query, history, language=language):
        if first_audio_at is None:
            first_audio_at = time.perf_counter()
            first_token_ms = chunk.first_token_ms
            first_tts_ms = chunk.tts_ms
        full_text_parts.append(chunk.text)

    ttfa_ms = (
        int((first_audio_at - t0) * 1000) if first_audio_at is not None else -1
    )
    total_ms = int((time.perf_counter() - t0) * 1000)

    return {
        "rag_ms": rag_ms,
        "llm_first_token_ms": first_token_ms or -1,
        "first_tts_ms": first_tts_ms or -1,
        "ttfa_ms": ttfa_ms,
        "total_ms": total_ms,
        "reply": " ".join(full_text_parts),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=int, default=len(DEFAULT_QUERIES))
    parser.add_argument("--save-audio", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.sarvam_api_key:
        raise SystemExit("SARVAM_API_KEY not set; cannot benchmark TTS.")

    openai_client = get_openai_client()
    http_client = get_http_client()

    rag = RAGEngine(settings.faiss_index_path, settings.metadata_mapping_path, openai_client)
    llm = LLM(openai_client, settings.openai_model, settings.system_prompt)
    tts = TTS(
        http_client,
        api_key=settings.sarvam_api_key,
        url=settings.sarvam_tts_url,
        model=settings.sarvam_tts_model,
    )
    engine = BrainEngine(rag, llm, tts)

    queries = DEFAULT_QUERIES[: args.queries]
    print(f"Running {len(queries)} queries against gpt-4o-mini + Sarvam TTS\n")

    results: list[dict] = []
    for i, (lang, q) in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] ({lang}) {q}")
        try:
            r = await run_one(engine, q, lang)
            results.append(r)
            print(
                f"  rag={r['rag_ms']}ms  llm_first_tok={r['llm_first_token_ms']}ms  "
                f"tts_first={r['first_tts_ms']}ms  TTFA={r['ttfa_ms']}ms  "
                f"total={r['total_ms']}ms"
            )
            print(f"  reply: {r['reply'][:80]}...")
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")

    if results:
        print("\n--- Summary ---")
        for key in ("rag_ms", "llm_first_token_ms", "first_tts_ms", "ttfa_ms", "total_ms"):
            vals = [r[key] for r in results if r[key] > 0]
            if not vals:
                continue
            print(
                f"{key:22s}  p50={int(statistics.median(vals)):5d}ms  "
                f"min={min(vals):5d}ms  max={max(vals):5d}ms"
            )

    await close_clients()


if __name__ == "__main__":
    asyncio.run(main())
