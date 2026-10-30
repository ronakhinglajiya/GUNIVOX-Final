"""Lightweight language detection for gu / hi / en based on Unicode ranges.

Avoids heavyweight langdetect dependency. Good enough for short student
utterances (a few words to a few sentences).
"""
from __future__ import annotations

GUJARATI_RANGE = (0x0A80, 0x0AFF)
DEVANAGARI_RANGE = (0x0900, 0x097F)

ASCII_LETTER_THRESHOLD = 0.6  # fraction of ASCII letters above which we call it English


def _in_range(ch: str, lo: int, hi: int) -> bool:
    return lo <= ord(ch) <= hi


def detect_language(text: str, fallback: str = "gu") -> str:
    """Return one of 'gu', 'hi', 'en'.

    Heuristic:
      1. Count Gujarati, Devanagari, ASCII-letter characters.
      2. If a non-ASCII script dominates, return that.
      3. Otherwise if ASCII letters dominate, return 'en'.
      4. Otherwise fall back.
    """
    if not text:
        return fallback

    gu = hi = en = 0
    for ch in text:
        if _in_range(ch, *GUJARATI_RANGE):
            gu += 1
        elif _in_range(ch, *DEVANAGARI_RANGE):
            hi += 1
        elif "a" <= ch.lower() <= "z":
            en += 1

    total = gu + hi + en
    if total == 0:
        return fallback

    if gu >= hi and gu >= en and gu > 0:
        return "gu"
    if hi >= en and hi > 0:
        return "hi"
    if en / total >= ASCII_LETTER_THRESHOLD:
        return "en"
    return fallback
