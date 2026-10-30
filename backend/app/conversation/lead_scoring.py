"""Lead-temperature classifier: post-call, hot / warm / cold + reasoning.

Runs once per call at hangup. Takes the full transcript plus a handful of
behavioural flags collected during the call (asked-about-fees, asked-about-
counsellor, asked-for-callback, programs-discussed) and asks gpt-4o-mini to
classify the lead.

Why a separate post-call pass instead of inferring per-turn: the
classification is for the OUTREACH PIPELINE, not the in-call voice agent.
It runs after audio is sent so latency doesn't matter, and it can see the
whole conversation in one shot — much better signal than mid-call snapshots.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

log = logging.getLogger("gunivox.lead")

CLASSIFIER_MODEL = "gpt-4o-mini"


@dataclass
class LeadScore:
    temperature: str  # 'hot' | 'warm' | 'cold'
    program: str | None
    reasons: str


CLASSIFIER_SYSTEM_PROMPT = """You classify the temperature of an admissions sales lead from a phone-call transcript. Output STRICT JSON only — no preamble, no markdown.

OUTPUT SCHEMA:
{"temperature": "hot" | "warm" | "cold", "program": "<program name they were most interested in, or null>", "reasons": "<one sentence, under 25 words>"}

RUBRIC (apply in order):

HOT — at least ONE of:
  • Asked about a SPECIFIC program AND asked at least one of {fees, counsellor name, eligibility, admission process, when classes start}.
  • Said they want a callback / will visit / asked to talk to a counsellor.
  • Gave their name + contact-readiness signal ('please call me back', 'send me details', 'when can I come').

WARM:
  • Asked about 2+ different programs without diving deep into any single one (browsing).
  • Engaged for 4+ turns with on-topic questions but no clear buying signal yet.
  • Gave their name and asked at least one substantive program question.

COLD — at least ONE of:
  • Call ended within ~45 seconds with minimal engagement.
  • Replies were mostly filler ('હા', 'okay', 'hmm') with no real questions.
  • Hung up immediately after the greeting or after the AI's first answer.
  • Explicit disinterest ('no thanks', 'wrong number', 'I'm not interested').

If ambiguous between two temperatures, pick the cooler one (under-promise on lead quality).

Reasons should be ONE concrete sentence pointing at evidence — e.g. 'Asked about B.Tech Marine fees and counsellor in 6 turns' or 'Hung up after 18 seconds without engaging'. Not generic ('seemed interested').
"""


async def classify_lead(
    openai_client: AsyncOpenAI,
    transcript: list[dict],
    *,
    duration_s: float,
    programs_discussed: list[str],
    caller_name: str | None,
    asked_about_fees: bool,
    asked_about_counsellor: bool,
    asked_about_admission: bool,
    asked_for_callback: bool,
) -> LeadScore | None:
    """Run the classifier. Returns None if there's not enough transcript to
    score (zero turns) or if the LLM call fails.

    transcript: list of {role, text} dicts from TurnHistory.to_openai_messages.
    """
    if not transcript:
        return None

    # Build a compact summary of the signals so the classifier doesn't have
    # to infer them from the raw transcript (cheaper, more reliable).
    signals = {
        "call_duration_seconds": round(duration_s, 1),
        "turn_count": len([t for t in transcript if t["role"] == "user"]),
        "caller_name_given": bool(caller_name),
        "programs_discussed": programs_discussed[:6],
        "asked_about_fees": asked_about_fees,
        "asked_about_counsellor": asked_about_counsellor,
        "asked_about_admission": asked_about_admission,
        "asked_for_callback": asked_for_callback,
    }

    # Cap transcript length so we don't waste tokens on multi-minute calls.
    # NOTE: `transcript` comes from TurnHistory.to_openai_messages() which
    # returns {'role', 'content'} dicts — NOT {'role', 'text'}. Earlier code
    # said t['text'] and crashed every call, leaving lead_temperature NULL
    # silently. Use .get('content') and fall back to 'text' in case a future
    # caller passes a different shape.
    capped = transcript[-30:]
    transcript_text = "\n".join(
        f"{(t.get('role') or '').upper()}: {((t.get('content') or t.get('text') or '')).strip()}"
        for t in capped
    )

    user_msg = (
        "SIGNALS:\n"
        + json.dumps(signals, ensure_ascii=False)
        + "\n\nTRANSCRIPT:\n"
        + transcript_text
        + "\n\nReturn ONLY the JSON object."
    )

    try:
        resp = await openai_client.chat.completions.create(
            model=CLASSIFIER_MODEL,
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
    except Exception:
        log.exception("lead classifier call failed")
        return None

    raw = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("lead classifier returned non-JSON: %r", raw[:200])
        return None

    temp = (data.get("temperature") or "").strip().lower()
    if temp not in {"hot", "warm", "cold"}:
        log.warning("lead classifier invalid temperature: %r", temp)
        return None

    program = data.get("program")
    if isinstance(program, str) and not program.strip():
        program = None
    reasons = (data.get("reasons") or "").strip() or "no reasoning provided"

    log.info(
        "lead classified: %s | program=%r | reasons=%s",
        temp, program, reasons[:120],
    )
    return LeadScore(temperature=temp, program=program, reasons=reasons)
