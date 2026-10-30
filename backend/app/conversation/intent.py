"""Intent detection for live call turns.

Keyword-based for speed (runs in <1ms vs ~500ms for an LLM call). Covers
the only intents that need explicit handling: handoff to a human, schedule
a callback, and goodbye. Everything else is `info_query` (default).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Intent = Literal["handoff", "callback", "goodbye", "info_query"]


# Keywords per intent across gu / hi / en. Lowercased for ASCII; Gujarati &
# Devanagari already case-less. Matched against transcript with `in` checks.
HANDOFF_KEYWORDS = (
    # English
    "talk to human", "speak to human", "real person", "live person",
    "counsellor", "counselor", "advisor", "manager", "human", "agent",
    "talk to someone", "real human",
    # Hindi
    "मानव से बात", "इंसान से बात", "काउंसलर", "एजेंट",
    # Gujarati
    "વ્યક્તિ સાથે વાત", "માણસ સાથે વાત", "કાઉન્સેલર", "એજન્ટ", "વ્યક્તિ સાથે જોડો",
)

CALLBACK_KEYWORDS = (
    "call me back", "callback", "call me later", "ring me back", "later",
    "बाद में", "वापस कॉल", "फोन वापस",
    "પછી ફોન", "ફોન કરજો", "પાછળથી ફોન", "મને પાછી કૉલ",
)

GOODBYE_KEYWORDS = (
    "bye", "goodbye", "thanks", "thank you", "have a good", "that's all",
    "धन्यवाद", "अलविदा",
    "આભાર", "બાય", "શુભ દિવસ",
)


@dataclass
class IntentResult:
    intent: Intent
    matched_keyword: str | None = None


import re as _re

# Characters that count as a word boundary on EITHER side of a keyword
# match. Python's \b is Latin-only and gives false negatives on Gujarati
# / Devanagari, so we use an explicit follow/precede class. Anything that
# isn't a letter/digit (across all scripts) counts as a boundary.
_BOUNDARY_RE_CACHE: dict[str, "_re.Pattern[str]"] = {}


def _word_match(needle: str, haystack: str) -> bool:
    """Return True iff `needle` appears in `haystack` as a whole-word
    occurrence. Boundary = start/end of string, or a non-letter / non-
    digit character (spaces, punctuation). Fixes the 2026-05-21 demo bug
    where 'બાય' (bye) matched inside 'બાયોલોજી' (biology) and the goodbye
    intent fired on a great PCB lead's first real question."""
    if not needle or not haystack:
        return False
    pat = _BOUNDARY_RE_CACHE.get(needle)
    if pat is None:
        # Word-char class for boundary detection. Python's \w is Unicode-
        # aware but EXCLUDES combining marks (Mn/Mc) like Gujarati vowel
        # signs. That meant "બાય" + "ો" satisfied (?!\w) and goodbye fired
        # on "બાયોલોજી" (biology). Explicitly include Devanagari (0900-
        # 097F) and Gujarati (0A80-0AFF) ranges — both include their
        # combining marks — so the boundary check rejects mid-Indic-word
        # matches.
        WORD = r"[\wऀ-ॿ઀-૿]"
        pat = _re.compile(rf"(?<!{WORD}){_re.escape(needle)}(?!{WORD})")
        _BOUNDARY_RE_CACHE[needle] = pat
    return pat.search(haystack) is not None


def detect_intent(text: str) -> IntentResult:
    """Return the highest-priority detected intent.

    Priority order: handoff > callback > goodbye > info_query.

    Uses whole-word matching (not substring) so e.g. 'બાય' (bye) doesn't
    falsely fire on 'બાયોલોજી' (biology), or 'human' on 'humanity'.
    """
    if not text:
        return IntentResult("info_query")
    low = text.lower()
    for kw in HANDOFF_KEYWORDS:
        if _word_match(kw.lower(), low):
            return IntentResult("handoff", kw)
    for kw in CALLBACK_KEYWORDS:
        if _word_match(kw.lower(), low):
            return IntentResult("callback", kw)
    for kw in GOODBYE_KEYWORDS:
        if _word_match(kw.lower(), low):
            return IntentResult("goodbye", kw)
    return IntentResult("info_query")
