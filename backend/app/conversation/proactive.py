"""Per-turn proactive-surfacing recommender.

Layer 5 of the human-brain roadmap (2026-05-21).

State (L2) tracks what's been covered. Signals (L3) read the caller.
Dialog (L4) steers the conversation forward. Proactive (L5) makes the
agent ANTICIPATORY — surfacing scholarship / hostel / transport /
placement at the right moments, without being asked.

Four surfacings, deterministic detection (<1ms, no LLM):

  mention_scholarship  — caller signalled affordability concern
                         (low-income marker, "middle class", fee
                         hesitation pattern), but no scholarship has
                         been mentioned yet. Agent should weave in
                         "merit + income scholarship available" once.

  mention_hostel       — caller named a city/distance marker that
                         isn't Mehsana, suggesting they'd need
                         residential. Agent should volunteer hostel.

  mention_transport    — caller named a city that the university
                         shuttle covers (Mehsana / Ahmedabad /
                         Gandhinagar). Agent should mention shuttle.

  mention_placement    — caller asked about career / future / job /
                         scope. Agent should add a one-line placement
                         note proactively.

To avoid bloating replies, AT MOST ONE surfacing fires per turn.
Priority order: hostel > scholarship > placement > transport (the
order in which a real counsellor would weight these — accommodation
is the biggest unspoken concern, fees second, etc.). Each surfacing
also de-dupes against `state.told_topics` so we never surface the
same one twice in a call.
"""
from __future__ import annotations

from dataclasses import dataclass


# Distance-negation markers — if the user says these alongside a shuttle
# city, they're DESCRIBING distance ('I'm in Surat which is far from
# Mehsana'), not commuting. Suppresses the misleading transport surfacing
# from the 2026-05-21 call where 'સુરત રહું છું જે મહેસાણાથી બહુ' triggered
# the shuttle message even though Surat isn't on the route.
_DISTANCE_NEGATION_TOKENS: tuple[str, ...] = (
    "દૂર", "દૂરથી", "દૂર છે", "બહુ", "far", "far away",
    "outside", "બીજા શહેર", "બીજી જગ્યા",
)


# Cities + distance markers that suggest the caller doesn't live in
# Mehsana. Hostel surfacing fires on any of these.
_OUT_OF_TOWN_TOKENS: tuple[str, ...] = (
    "મુંબઈ", "મુંબઇ", "દિલ્હી", "બેંગ્લોર", "બેંગાલુરુ", "ચેન્નઈ", "કોલકાતા",
    "પુણે", "જયપુર", "ઇન્દોર", "લખનઉ", "નાગપુર", "વારાણસી",
    "વડોદરા", "બરોડા", "સુરત", "રાજકોટ", "ભાવનગર", "જામનગર", "જૂનાગઢ",
    "બીજા શહેર", "બીજી જગ્યા", "દૂર", "દૂરથી", "outside",
    "mumbai", "delhi", "bangalore", "bengaluru", "chennai", "kolkata",
    "pune", "jaipur", "indore", "lucknow", "nagpur",
    "vadodara", "baroda", "surat", "rajkot", "bhavnagar", "jamnagar",
    "out of state", "out of gujarat", "different city",
    "મારે રહેવા", "stay arrangement", "accommodation",
)

# Cities the university shuttle covers — transport surfacing fires.
_SHUTTLE_CITY_TOKENS: tuple[str, ...] = (
    "મહેસાણા", "મેહસાણા", "અમદાવાદ", "ગાંધીનગર",
    "mehsana", "ahmedabad", "gandhinagar",
)

# Affordability-concern markers (separate from the price_shock signal —
# those fire ONLY right after a fee is quoted). These are slower
# socioeconomic cues that warrant proactively mentioning scholarship.
_AFFORDABILITY_TOKENS: tuple[str, ...] = (
    "મિડલ ક્લાસ", "મિડ્લ ક્લાસ", "મધ્યમ વર્ગ",
    "મારા પપ્પા", "મારા પિતા", "ફાધર", "father",
    "આર્થિક", "નાણાકીય", "income", "low income", "આવક",
    "loan", "લોન", "EMI", "ઈએમઆઈ",
    "middle class", "modest", "afford",
    "ગવર્નમેન્ટ જોબ", "government job",
)

# Career / future / job tokens — placement surfacing fires.
_CAREER_TOKENS: tuple[str, ...] = (
    "career", "career scope", "scope", "future",
    "job", "નોકરી", "ભવિષ્ય", "કેરિયર", "career કરવી",
    "placement", "પ્લેસમેન્ટ", "company", "કંપની",
    "package", "salary", "પગાર",
)


@dataclass
class ProactiveSurfacings:
    mention_scholarship: bool = False
    mention_hostel: bool = False
    mention_transport: bool = False
    mention_placement: bool = False
    matched: dict[str, str] | None = None


def _has_any(text_low: str, text_raw: str, tokens: tuple[str, ...]) -> str | None:
    for t in tokens:
        tl = t.lower()
        if tl in text_low or t in text_raw:
            return t
    return None


def compute_proactive(
    history,
    state,
    signals,
    current_user_text: str,
) -> ProactiveSurfacings:
    """Compute proactive surfacings for this turn.

    Args:
        history: TurnHistory (read-only).
        state: CallState — used for `told_topics` de-dup.
        signals: TurnSignals — used to dampen surfacings during
            confused / price_shock turns (agent shouldn't add NEW
            facts when caller's already overwhelmed).
        current_user_text: just-spoken transcript.

    Returns ProactiveSurfacings — AT MOST one flag set (priority order).
    """
    out = ProactiveSurfacings()
    if not current_user_text:
        return out

    # Don't surface anything when caller is confused or in price_shock —
    # those signals get their OWN directives, layering on more would
    # overwhelm. (Caller in price_shock already gets scholarship via
    # the signals directive — that's the right moment for it.)
    if signals.confused or signals.price_shock:
        return out

    matched: dict[str, str] = {}
    low = current_user_text.lower()

    told = getattr(state, "told_topics", set()) or set()

    # Priority 1: hostel
    if "hostel" not in told:
        hit = _has_any(low, current_user_text, _OUT_OF_TOWN_TOKENS)
        if hit:
            out.mention_hostel = True
            matched["mention_hostel"] = hit
            out.matched = matched
            return out

    # Priority 2: scholarship — only after a fee has been mentioned at
    # least once (we don't bait scholarship before the caller knows the
    # price). And not if already covered.
    if "scholarship" not in told and getattr(state, "quoted_fee", False):
        hit = _has_any(low, current_user_text, _AFFORDABILITY_TOKENS)
        if hit:
            out.mention_scholarship = True
            matched["mention_scholarship"] = hit
            out.matched = matched
            return out

    # Priority 3: placement
    if "placement" not in told:
        hit = _has_any(low, current_user_text, _CAREER_TOKENS)
        if hit:
            out.mention_placement = True
            matched["mention_placement"] = hit
            out.matched = matched
            return out

    # Priority 4: transport. Requires shuttle-city mention AND no
    # distance-negation in the same text — caller saying "I'm in Surat
    # which is far from Mehsana" is NOT a transport-eligible commute.
    if "transport" not in told:
        hit = _has_any(low, current_user_text, _SHUTTLE_CITY_TOKENS)
        if hit:
            far_hit = _has_any(low, current_user_text, _DISTANCE_NEGATION_TOKENS)
            if not far_hit:
                out.mention_transport = True
                matched["mention_transport"] = hit
                out.matched = matched
                return out

    return out


def render_proactive_for_llm(p: ProactiveSurfacings) -> str:
    """Render the single active surfacing (if any) as a terse hint
    line. Empty string when nothing fires so the engine can skip
    injection. Same format as the other Layer-N hints."""
    flags = []
    if p.mention_hostel:
        flags.append("mention_hostel")
    if p.mention_scholarship:
        flags.append("mention_scholarship")
    if p.mention_placement:
        flags.append("mention_placement")
    if p.mention_transport:
        flags.append("mention_transport")
    if not flags:
        return ""
    return ",".join(flags)
