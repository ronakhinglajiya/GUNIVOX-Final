"""Add a Gujarati `text_field_gu` to each program in admission_brain.json.

Why: callers ask in Gujarati ("ડેટા સાયન્સ વિશે"). The English-only FAISS index
doesn't match Indic-script queries well, so RAG returns junk and the LLM
falls back to its off-topic guardrail. Indexing both English AND Gujarati
text per program means queries in either language match without paying the
~500ms translate-at-query-time cost.

We preserve every English program code, fee number, counsellor name, phone,
and email VERBATIM — those have to flow through to the LLM unchanged so the
voice agent can quote them accurately. Only the descriptive prose (program
title, eligibility, etc.) is translated.

Usage (from backend/):
    ../venv/Scripts/python.exe -m scripts.translate_brain
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI  # noqa: E402

from app.clients import close_clients, get_openai_client  # noqa: E402
from app.config import get_settings  # noqa: E402

# Lower-cost model, plenty good for short translations.
TRANSLATE_MODEL = "gpt-4o-mini"

# 8 in flight is comfortable for OpenAI free-tier rate limits.
CONCURRENCY = 8

SYSTEM_PROMPT = (
    "You translate university admissions program descriptions from English "
    "to Gujarati for a voice agent. RULES:\n"
    "1. PRESERVE EXACTLY: program codes (B.Tech, B.Sc., M.Sc., MBA, BCA, "
    "CSE, IT, etc.), specialisation names in parentheses (Data Science, "
    "CS, AI & ML, Finance, etc.), numeric fees, counsellor names (Prof. "
    "X, Dr. Y), phone numbers, email addresses. Keep them in English/digits.\n"
    "2. ADD GUJARATI TRANSLITERATION of the program name and key specialisation "
    "right after the English code, separated by 'એટલે કે'. This is CRITICAL "
    "for voice-agent retrieval — callers say things like 'બીટેક મરીન' or "
    "'એમ એસસી આઈટી ડેટા સાયન્સ' and we need that exact Gujarati script in "
    "the indexed text. Examples:\n"
    "   • 'B. Tech. (Marine)' → 'B. Tech. (Marine) એટલે કે બીટેક મરીન'\n"
    "   • 'M. Sc. IT (Data Science)' → 'M. Sc. IT (Data Science) એટલે કે "
    "એમએસસી આઈટી ડેટા સાયન્સ'\n"
    "   • 'BCA (Hons.) (AI & ML)' → 'BCA (Hons.) (AI and ML) એટલે કે "
    "બીસીએ ઓનર્સ એઆઈ એન્ડ એમએલ'\n"
    "   • 'GNM' → 'GNM એટલે કે જીએનએમ જનરલ નર્સિંગ મિડવાઈફરી'\n"
    "   • 'MBA (Financial Services)' → 'MBA (Financial Services) એટલે કે "
    "એમબીએ ફાઈનાન્શિયલ સર્વિસિસ'\n"
    "3. TRANSLATE: surrounding prose, eligibility wording, institute "
    "descriptors.\n"
    "4. OUTPUT FORMAT: Output ONLY the Gujarati text (no preamble, no "
    "quotes, no bullets).\n"
    "5. VOICE-FRIENDLY: write as one or two flowing sentences a counsellor "
    "would speak. No markdown.\n"
    "6. KEY VOCAB: કાર્યક્રમ=program, ઈન્સ્ટિટ્યૂટ=institute, અવધિ=duration, "
    "ફી=fees, પાત્રતા=eligibility, કાઉન્સેલર=counsellor, વર્ષ=year, રૂપિયા=INR, "
    "ધોરણ=standard."
)


def format_english(item: dict) -> str:
    """The same English text_field we already index, used as the source."""
    return (
        f"Program: {item.get('program', 'N/A')}. "
        f"Institute: {item.get('institute', 'N/A')}. "
        f"Duration: {item.get('duration', 'N/A')}. "
        f"Fees: {item.get('fees', 'N/A')} INR. "
        f"Eligibility: {item.get('eligibility', 'N/A')}. "
        f"Counsellor: {item.get('counsellor_name', 'N/A')}, "
        f"Phone: {item.get('counsellor_phone', 'N/A')}, "
        f"Email: {item.get('counsellor_email', 'N/A')}."
    )


async def translate_one(
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    idx: int,
    english_text: str,
) -> tuple[int, str]:
    async with sem:
        resp = await client.chat.completions.create(
            model=TRANSLATE_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": english_text},
            ],
            temperature=0,
            max_tokens=400,
        )
        out = (resp.choices[0].message.content or "").strip()
        return idx, out


async def main() -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY not set in .env")

    src = settings.admission_brain_json
    print(f"Reading {src}")
    with open(src, "r", encoding="utf-8") as f:
        data: list[dict] = json.load(f)

    force = "--force" in sys.argv
    # By default skip records that already have text_field_gu (so this script
    # is rerunnable cheaply). Pass --force to retranslate everything (use this
    # after updating the SYSTEM_PROMPT, e.g. to add Gujarati transliterations).
    todo = [
        (i, format_english(item))
        for i, item in enumerate(data)
        if force or not item.get("text_field_gu")
    ]
    print(
        f"{len(todo)} of {len(data)} records need Gujarati text "
        f"({'force' if force else 'incremental'})"
    )
    if not todo:
        print("Nothing to do. (Pass --force to retranslate everything.)")
        return

    client = get_openai_client()
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [translate_one(client, sem, idx, txt) for idx, txt in todo]

    done = 0
    for fut in asyncio.as_completed(tasks):
        idx, gu = await fut
        data[idx]["text_field_gu"] = gu
        done += 1
        if done % 10 == 0 or done == len(tasks):
            print(f"  translated {done}/{len(tasks)}")

    # Write back atomically.
    tmp = src.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(src)
    print(f"Wrote {src} with text_field_gu on {done} records")

    await close_clients()


if __name__ == "__main__":
    asyncio.run(main())
