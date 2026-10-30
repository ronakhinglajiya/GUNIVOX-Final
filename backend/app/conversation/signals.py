"""Per-turn intent + emotional signal detection.

Layer 3 of the human-brain roadmap (2026-05-21).

A pure-deterministic detector — no LLM round-trip, runs <1ms per turn.
Mirrors the design of `conversation/state.py`. The output is a small set
of flags that flow into the LLM context alongside the state brief; the
system prompt has matching behavioural directives that tell the LLM how
to respond when a flag fires.

The four signals (chosen for high-value-per-flag, low false-positive
risk):

  price_shock     — caller sounded uneasy AFTER we quoted a fee.
                    Pivot to scholarship/EMI instead of re-quoting.

  hesitation      — slow speaker / hesitant filler / "let me think".
                    Slow down, ask ONE qualifying question.

  ready_to_enroll — explicit closing intent ("મારે જોડાવું છે",
                    "form કેવી રીતે", "next step"). Offer warm
                    counsellor transfer or admission link.

  confused        — caller asked the same thing twice OR said "didn't
                    understand". Restate previous answer shorter.

Deeper signals (urgency, objection, buying-stage) need an LLM-driven
classifier and add ~300ms latency. Out of scope for this lean pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ─── Keyword vocabulary (lowercased; matched as substrings) ─────────────

_PRICE_SHOCK_KEYWORDS: tuple[str, ...] = (
    # Gujarati — "expensive / too much / can't afford / a lot"
    "ઘણું", "મુશ્કેલ", "આટલા તો નથી", "વધારે", "ખર્ચ વધારે",
    "મોંઘું", "મોંઘા", "પોસાય નહીં", "પોસાતું નથી",
    "ઓહ", "અરે", "ઓય",
    # Hindi
    "ज्यादा है", "महंगा", "महंगी",
    # English
    "expensive", "too much", "too high", "out of budget", "can't afford",
    "cannot afford", "afford", "a lot", "lot of money", "high fee",
)

_HESITATION_KEYWORDS: tuple[str, ...] = (
    "વિચારું", "મારે વિચારવા", "પછી કહું", "પછી જણાવું", "વિચારી લઉં",
    "જોઉં છું", "जरा सोच", "सोचना", "let me think", "let me see",
    "i need to think", "i'll think", "i will think", "give me time",
    "give me some time", "thinking",
)

_READY_TO_ENROLL_KEYWORDS: tuple[str, ...] = (
    "જોડાવું", "જોડાવો", "જોડાવ", "joinवू", "join करवू",
    "admission લેવી", "admission લેવા", "admission લેવુ",
    "form ભરવો", "form ભરવાનો", "form ભરવાનું", "form કેવી રીતે",
    "આગળ શું", "આગળનું", "next step", "next steps", "process શું",
    "apply કરવું", "apply કરવાનું", "apply કેવી રીતે",
    "મારે જોઈએ છે", "મારે કરવું છે",
    "enroll", "enrollment", "want to join", "want to enroll",
    "ready to join", "where do i apply", "how to apply",
    "sign me up", "sign up",
)

_CONFUSED_KEYWORDS: tuple[str, ...] = (
    "સમજાયું નહીં", "સમજાય નહીં", "સમજાતું નથી", "ફરી કહો",
    "ફરી બોલો", "ફરી જણાવો", "ફરી કહેશો", "પાછું બોલો",
    "didn't understand", "did not understand", "didn't get", "say again",
    "say that again", "what was that", "come again", "repeat please",
    "samjyu nahi", "samajh nahi", "नहीं समझा", "फिर बोलिए",
)


def _has_any(text_lower: str, kws: tuple[str, ...]) -> str | None:
    """Return the first matching keyword (for logging) or None."""
    for kw in kws:
        if kw in text_lower:
            return kw
    return None


# ─── Dataclass + helpers ────────────────────────────────────────────────

@dataclass
class TurnSignals:
    price_shock: bool = False
    hesitation: bool = False
    ready_to_enroll: bool = False
    confused: bool = False
    # Debug: which keyword(s) triggered, for log/audit.
    matched: dict[str, str] | None = None


def _normalise_for_compare(text: str) -> str:
    """Strip punctuation + collapse whitespace + lowercase. Used for the
    'caller repeated themselves' check."""
    t = re.sub(r"[^\w\s]", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def compute_signals(
    history,
    current_user_text: str,
    state,
) -> TurnSignals:
    """Compute per-turn signals from the history + current user text.

    Args:
        history: TurnHistory (we read .turns).
        current_user_text: the user's just-spoken transcript.
        state: a CallState (see conversation/state.py) — needed for
            `quoted_fee` to gate price_shock.
    """
    sig = TurnSignals()
    if not current_user_text:
        return sig
    matched: dict[str, str] = {}
    low = current_user_text.lower()

    # ── price_shock ──
    # Gate on "we just quoted a fee" so a generic "ઘણું છે" early in the
    # call doesn't trigger. quoted_fee comes from CallState which scans
    # the WHOLE history; we additionally check that the IMMEDIATELY
    # previous assistant turn talked about fees, otherwise it's not
    # really shock at the price.
    if getattr(state, "quoted_fee", False):
        # Was the last assistant turn fee-related? (cheap check)
        last_asst = next(
            (t for t in reversed(getattr(history, "turns", [])) if t.role == "assistant"),
            None,
        )
        fee_in_last = bool(
            last_asst and re.search(r"\d{4,7}|હજાર|લાખ|lakh|hazaar", last_asst.text or "")
        )
        kw = _has_any(low, _PRICE_SHOCK_KEYWORDS)
        # Also count "very short / silent reaction" right after a fee
        # quote as soft price-shock (e.g. user said "હમ" or "..." after
        # hearing the fee). Tighter threshold than `hesitation` so it
        # doesn't double-fire.
        very_short = len(current_user_text.strip()) <= 4
        if fee_in_last and (kw or very_short):
            sig.price_shock = True
            matched["price_shock"] = kw or "short-reaction-after-fee"

    # ── hesitation ──
    # ONLY fire on explicit hesitation keywords ("let me think", "વિચારું",
    # etc.). The short-utterance heuristic was REMOVED 2026-05-21 — it was
    # firing on every brief natural answer (caller's name, "BCA", "MBA
    # Finance") and triggering an inappropriate "કોઈ ઉતાવળ નથી" empathy
    # reply that sounded condescending. Real hesitation has linguistic
    # tells; brevity alone doesn't.
    kw = _has_any(low, _HESITATION_KEYWORDS)
    if kw:
        sig.hesitation = True
        matched["hesitation"] = kw

    # ── ready_to_enroll ──
    kw = _has_any(low, _READY_TO_ENROLL_KEYWORDS)
    if kw:
        sig.ready_to_enroll = True
        matched["ready_to_enroll"] = kw

    # ── confused ──
    kw = _has_any(low, _CONFUSED_KEYWORDS)
    if kw:
        sig.confused = True
        matched["confused"] = kw
    else:
        # Or: caller's current text closely matches their text from 2
        # turns ago (re-asking the same thing).
        user_turns = [t for t in getattr(history, "turns", []) if t.role == "user"]
        if len(user_turns) >= 2:
            prev_user = _normalise_for_compare(user_turns[-2].text or "")
            curr = _normalise_for_compare(current_user_text)
            if prev_user and curr and len(curr) >= 6 and _similar(prev_user, curr):
                sig.confused = True
                matched["confused"] = "repeated-question"

    sig.matched = matched or None
    return sig


_CLOSING_OR_YESNO_KWS = (
    "thank", "thanks", "bye", "આભાર", "બાય", "ઓકે", "okay", "ok",
    "હા", "ના", "yes", "no", "haa", "ji",
)


def _looks_like_closing_or_yesno(text_lower: str) -> bool:
    return any(kw in text_lower for kw in _CLOSING_OR_YESNO_KWS)


_DEGREE_TOKENS = (
    "બીએસસી", "બીટેક", "બીબીએ", "બીસીએ", "બીકોમ",
    "એમએસસી", "એમટેક", "એમબીએ", "એમસીએ", "એમકોમ",
    "b.sc", "bsc", "b.tech", "btech", "bba", "bca", "m.sc", "msc",
    "m.tech", "mtech", "mba", "mca",
)


def _looks_like_degree_marker(text: str) -> bool:
    low = text.lower()
    return any(t in low for t in _DEGREE_TOKENS) or any(t in text for t in _DEGREE_TOKENS)


def _similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """Quick word-overlap similarity: |intersection| / max(|a|, |b|)."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return False
    overlap = len(sa & sb) / max(len(sa), len(sb))
    return overlap >= threshold


# ─── Render for LLM ─────────────────────────────────────────────────────

def render_signals_for_llm(signals: TurnSignals) -> str:
    """Render the firing signals as a terse English brief for the LLM.

    Empty string when nothing fired so the engine can skip injection.
    Format matches the state-brief style: short, comma-separated,
    behavioural directive at the end.
    """
    flags: list[str] = []
    if signals.price_shock:
        flags.append("price_shock")
    if signals.hesitation:
        flags.append("hesitation")
    if signals.ready_to_enroll:
        flags.append("ready_to_enroll")
    if signals.confused:
        flags.append("confused")
    if not flags:
        return ""
    # Terse — system prompt already binds these tags to behavior.
    return ",".join(flags)
