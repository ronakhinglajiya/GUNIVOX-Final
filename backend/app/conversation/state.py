"""Per-call conversation state — what's been told and what's been asked.

Layer 2 of the human-brain roadmap (2026-05-20).

The bridge already maintains the heavy session slots (caller_name,
focused_program, programs_discussed, sticky asked_about_* booleans) for
the post-call lead classifier. What the LLM HAS NOT had so far is a
clean turn-time signal of "what have I already covered in this call?"
Without it, the model re-quotes the same fee number two turns in a row,
re-introduces the counsellor every time a fee is mentioned, and offers a
callback every other turn. A human counsellor doesn't do that — they
remember.

This module computes a fresh `CallState` from the turn history at the
top of every turn (cheap — single linear scan) and renders it as a
terse English brief that goes into the system-message stack alongside
the focused-program slot. The brief is intentionally short so it doesn't
crowd out the slot info or the RAG context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Topic detection vocabulary. Each topic maps to a tuple of lowercase
# substring keywords. We match across both English and Gujarati so the
# detector works regardless of the language the caller switches into.
# Kept short — only high-signal keywords that don't false-positive on
# polite filler (e.g. we don't include "આ" or "is" or "the").
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fees": (
        "ફી", "fee", "fees", "ખર્ચ", "cost", "price", "tuition",
        "rs", "rupees", "rupaye", "रुपये", "પૈસા", "ચાર્જ",
    ),
    "eligibility": (
        "eligib", "પાત્ર", "qualif", "qualify",
        "12th", "10th", "ધોરણ", "પર્સન્ટેજ", "percentage", "percent",
        "marks", "ગુણ", "જરૂરી",
    ),
    "counsellor": (
        "કાઉન્સ", "counsel", "professor", "પ્રોફેસ",
        "ટીચર", "teacher", "advisor", "મેન્ટર", "mentor",
    ),
    "duration": (
        "duration", "years", "year course", "વર્ષ", "month",
        "મહિન", "semester", "સેમેસ્ટર",
    ),
    "scholarship": (
        "scholarship", "છાત્રવૃ", "scholar", "concession",
        "ડિસ્કાઉન્ટ", "discount", "waive", "freeship", "છૂટ",
    ),
    "transport": (
        " bus", "બસ", "transport", "પિક અપ", "પિક-અપ", "pick up",
        "pickup",
    ),
    "hostel": (
        "hostel", "હોસ્ટેલ", "stay", "રહેવા", "accommodation",
        "boarding", "મેસ", "mess",
    ),
    "placement": (
        "placement", "પ્લેસમેન્ટ", "job", "નોકરી",
        "company", "kompani", "કંપની", "package", "પગાર", "salary",
    ),
    "location": (
        "where is", "address", "location", "મેહસાણા", "mehsana",
        "ગામ", "campus", "ક્યાં", "kyaa",
    ),
    "admission": (
        "admission", "પ્રવેશ", "apply", "application",
        "ફોર્મ", " form", "process", "પ્રક્રિયા",
    ),
    "callback": (
        "callback", "call back", "ફોન કરો", "phone karo",
        "મુલાકાત", "visit", "call me",
    ),
    "timeline": (
        "deadline", "last date", "ક્યારે", "kyare", "when does",
        "when is", "schedule", "શરૂ", "begin",
    ),
}


# Used to tell whether an assistant turn that mentions "fees" actually
# *quoted a number* (vs. just acknowledging the topic). We treat any
# 4–7 digit run OR the Gujarati unit words લાખ/હજાર as "number spoken".
_FEE_NUMBER_RE = re.compile(r"\d{4,7}|હજાર|લાખ|lakh|hazaar")
_CONTACT_RE = re.compile(r"\d{10}|\b[\w.+-]+@[\w.\-]+\.\w+")


@dataclass
class CallState:
    asked_topics: set[str]      # topics the caller has raised
    told_topics: set[str]       # topics the assistant has covered
    quoted_fee: bool            # has the assistant put a fee number on the wire?
    gave_counsellor: bool       # has the assistant shared counsellor contact?
    promised_callback: bool     # has the assistant offered a callback?
    user_turn_count: int


# Pre-compile word-boundary regex for ASCII keywords (Latin-script). Short
# English tokens like "rs", "fee" would otherwise substring-match inside
# "schola(rs)hip" / "(fee)dback", causing false-positive topic hits. Gujarati
# keywords are kept as plain substring matches because (a) `\b` doesn't fire
# at Gujarati word boundaries cleanly and (b) the multi-codepoint Gujarati
# tokens are distinctive enough not to collide.
def _is_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


_TOPIC_PATTERNS: dict[str, list] = {}
for _topic, _kws in _TOPIC_KEYWORDS.items():
    _entries: list = []
    for _kw in _kws:
        _stripped = _kw.strip()
        if _is_ascii(_stripped):
            _entries.append(("rx", re.compile(rf"\b{re.escape(_stripped)}\b", re.IGNORECASE)))
        else:
            _entries.append(("sub", _kw.lower()))
    _TOPIC_PATTERNS[_topic] = _entries


def detect_topics(text: str) -> set[str]:
    """Return the set of topics referenced in `text`."""
    low = (text or "").lower()
    if not low:
        return set()
    hits: set[str] = set()
    for topic, entries in _TOPIC_PATTERNS.items():
        for kind, val in entries:
            if kind == "sub":
                if val in low:
                    hits.add(topic)
                    break
            else:  # "rx"
                if val.search(low):
                    hits.add(topic)
                    break
    return hits


def compute_state(history, user_turn_count: int) -> CallState:
    """Scan `history` and derive a state snapshot.

    `history` is a TurnHistory; we read its `.turns` list, each turn has
    `.role` ("user" / "assistant") and `.text`.
    """
    asked: set[str] = set()
    told: set[str] = set()
    quoted_fee = False
    gave_counsellor = False
    promised_callback = False
    for t in getattr(history, "turns", []):
        topics = detect_topics(t.text or "")
        if t.role == "user":
            asked |= topics
        elif t.role == "assistant":
            told |= topics
            text = t.text or ""
            if "fees" in topics and _FEE_NUMBER_RE.search(text):
                quoted_fee = True
            if "counsellor" in topics and _CONTACT_RE.search(text):
                gave_counsellor = True
            if "callback" in topics:
                promised_callback = True
    return CallState(
        asked_topics=asked,
        told_topics=told,
        quoted_fee=quoted_fee,
        gave_counsellor=gave_counsellor,
        promised_callback=promised_callback,
        user_turn_count=user_turn_count,
    )


def render_state_for_llm(state: CallState) -> str:
    """Render the state as a TERSE English brief for the LLM.

    Kept short on purpose — every byte here is input tokens on EVERY
    turn, and the LLM only needs a flag, not a rationale. The system
    prompt already enforces "don't repeat yourself"; this just tells
    it WHAT it's already said.

    Returns empty string when nothing's worth saying (no topics
    covered yet AND no pending questions) — caller skips injection.
    """
    flags: list[str] = []
    if state.quoted_fee:
        flags.append("fee_quoted")
    if state.gave_counsellor:
        flags.append("counsellor_shared")
    if state.promised_callback:
        flags.append("callback_offered")

    unanswered = sorted(state.asked_topics - state.told_topics)
    parts: list[str] = []
    if flags:
        parts.append(f"already={','.join(flags)}")
    if unanswered:
        parts.append(f"pending={','.join(unanswered)}")
    if not parts:
        return ""
    # System prompt knows how to interpret — no need to repeat the rule.
    return " ".join(parts)
