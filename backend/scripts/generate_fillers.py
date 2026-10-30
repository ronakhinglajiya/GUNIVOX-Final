"""Pre-render filler audio to backend/data/fillers/{language}/{kind}.wav.

Runs once after a fresh checkout (or whenever you tweak DEFAULT_FILLERS).
Cost: ~15 Sarvam TTS calls.

Usage (from backend/):
    ../venv/Scripts/python.exe -m scripts.generate_fillers
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.fillers import DEFAULT_FILLERS, THINKING_VARIANTS  # noqa: E402
from app.brain.tts import TTS  # noqa: E402
from app.clients import close_clients, get_http_client  # noqa: E402
from app.config import get_settings  # noqa: E402


async def main() -> None:
    settings = get_settings()
    if not settings.sarvam_api_key:
        raise SystemExit("SARVAM_API_KEY not set in .env")

    out_root = settings.recordings_dir.parent / "data" / "fillers"
    out_root.mkdir(parents=True, exist_ok=True)

    http = get_http_client()
    tts = TTS(
        http,
        api_key=settings.sarvam_api_key,
        url=settings.sarvam_tts_url,
        model=settings.sarvam_tts_model,
        speaker=settings.sarvam_tts_speaker,
    )

    total = 0
    for language, kinds in DEFAULT_FILLERS.items():
        lang_dir = out_root / language
        lang_dir.mkdir(parents=True, exist_ok=True)
        for kind, text in kinds.items():
            target = lang_dir / f"{kind}.wav"
            if target.exists():
                print(f"skip   {target.relative_to(out_root.parent)}")
                continue
            print(f"render {language}/{kind}: {text[:60]}")
            audio = await tts.synthesize(text, language=language)
            target.write_bytes(audio)
            total += 1

    # Render the thinking-filler variants pool (rotated at runtime).
    for language, variants in THINKING_VARIANTS.items():
        lang_dir = out_root / language
        lang_dir.mkdir(parents=True, exist_ok=True)
        for i, text in enumerate(variants, start=1):
            target = lang_dir / f"thinking_{i}.wav"
            if target.exists():
                print(f"skip   {target.relative_to(out_root.parent)}")
                continue
            print(f"render {language}/thinking_{i}: {text[:60]}")
            audio = await tts.synthesize(text, language=language)
            target.write_bytes(audio)
            total += 1

    print(f"\nGenerated {total} filler WAVs at {out_root}")

    await close_clients()


if __name__ == "__main__":
    asyncio.run(main())
