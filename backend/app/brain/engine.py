"""BrainEngine: end-to-end conversational turn (RAG → LLM stream → TTS stream).

LLM tokens stream into Sarvam's streaming TTS WebSocket. Sarvam emits PCM
chunks as soon as it has enough text — much lower TTFA than batch TTS.

We buffer LLM tokens into ~25-char chunks before forwarding to TTS so Sarvam
gets meaningful input (Sarvam rejects tiny single-token fragments).
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.brain.llm import LLM
from app.brain.rag import RAGEngine
from app.brain.tts import StreamingTTS, StreamingTTSSession
from app.conversation.memory import TurnHistory

log = logging.getLogger("gunivox.brain")

# Unicode ranges of allowed languages per Sarvam (must include at least 1 in each TTS message).
SCRIPT_RANGES = {
    "gu": (0x0A80, 0x0AFF),
    "hi": (0x0900, 0x097F),
}

# Flush rules. We want the FIRST chunk to fly to TTS as fast as possible
# (FIRST_FLUSH_MIN = 4 chars + a break char) — that's the TTFA optimisation.
# Subsequent chunks accumulate up to HARD_FLUSH_MAX before a forced flush.
# Raised from 80 -> 120 on 2026-05-20: smaller chunks caused Sarvam to
# produce audio with merged-sounding words across chunk boundaries
# (prosody resets on every TTS message). 120 matches the LLM max_tokens
# cap so most replies fit in a single flushed chunk.
BREAK_CHARS = re.compile(r"[।.!?,;:\n ]")
# Lowered 4 → 2 (2026-05-21): replies almost always open with "જી,"/"હા,"/
# "સારું,"/"નમસ્તે," (per the FIRST WORD rule in SYSTEM_PROMPT). The 2-char
# opener + comma is enough to flush a clean first TTS message, cutting
# perceived TTFA by ~200-400ms without making Sarvam choppy.
FIRST_FLUSH_MIN = 2
# Raised 120 → 220 (2026-05-23). Prod call (B.Tech Marine) had LLM emit
# "ચાર લાખ પચાસ હજાર" but the buffer was already ~100 chars in, the
# break-char + length condition triggered AT "ચાર લાખ" because the
# _ends_in_pending_fee defer is bypassed when len ≥ HARD_FLUSH_MAX.
# fact_guard then substituted just "ચાર લાખ" → "ચાર લાખ પંચાણું હજાર"
# and the trailing "પચાસ હજાર" arrived in the next chunk as an orphan
# → caller heard "4-lakh-95-thousand 50-thousand" jumble. 220 gives the
# defer logic enough headroom to hold the full lakh+hazaar phrase
# together every time without sacrificing too much per-chunk latency.
HARD_FLUSH_MAX = 220

# Tokens that indicate the buffer is mid-fee-phrase. If the buffer ends here
# we DEFER flush so the fact-guard sees the whole "X લાખ Y હજાર" together
# (instead of "4 લાખ ..." and "95 હજાર ..." landing in separate chunks and
# each getting independently "corrected" to the canonical fee — that's the
# 2026-05-20 production bug where the caller heard the fee spoken twice).
_PENDING_FEE_TAILS = ("લાખ", "lakh", "lac")


def _ends_in_pending_fee(s: str) -> bool:
    """Return True if `s` ends in a state that probably continues into a
    fee unit. The flush is deferred until the phrase completes (or
    HARD_FLUSH_MAX caps the wait).

    Triggers on:
      • trailing 'લાખ' / 'lakh' — next token is likely '<number> હજાર'.
      • trailing digit run of 1-3 chars — next token is likely 'લાખ' or 'હજાર'.
      • trailing Gujarati number-word with a 'લાખ' in the recent context
        (mid-second-half: 'એક લાખ અંસઠ' waiting for 'હજાર').
      • trailing Gujarati number-word that's a known fee-leading word
        ('ચાર', 'એક', …) — could be the start of a fee.

    We DO NOT defer on trailing 'હજાર', because thousands is the
    last unit in an Indian-rupee fee phrase. Once we see 'હજાર' the
    phrase is complete and safe to flush.
    """
    if not s:
        return False
    t = s.rstrip(" \t\n,.;:!?")
    if not t:
        return False
    # 1) Trailing લાખ / lakh — definitely pending Y હજાર.
    for tail in _PENDING_FEE_TAILS:
        if t.endswith(tail):
            return True
    # 2) Trailing short digit run — could be followed by લાખ or હજાર.
    last_token = t.rsplit(None, 1)[-1] if " " in t else t
    if last_token.isdigit() and 1 <= len(last_token) <= 3:
        return True
    # 3) Trailing Gujarati number-word. Import inline to avoid a circular
    #    import at module load (fact_guard imports text_normalize which
    #    transitively imports this module on some configs).
    from app.brain.fact_guard import _GU_NUMBER_WORDS
    if last_token in _GU_NUMBER_WORDS:
        # If there's a recent 'લાખ' before this number-word, we're mid-
        # second-half (e.g. "એક લાખ અંસઠ" waiting for "હજાર") — defer.
        # If no recent લાખ, this could still be the start of "ચાર લાખ ..." —
        # defer briefly so the next token (likely 'લાખ') can join.
        return True
    return False


# Degree-level markers — strings that, when present in the user's text,
# indicate they've already qualified the program level (B.Sc vs M.Sc,
# B.Tech vs M.Tech, BBA vs MBA, etc.). If ABSENT and the RAG retrieval
# returns variants spanning multiple degree levels for the same topic,
# we'd be choosing arbitrarily — better to ask the caller which level
# they want. 2026-05-20 bug: caller said "cyber security વિશે" (no degree
# marker), agent inherited M.Sc from the previous turn's focused program
# and silently picked M.Sc Cyber Security.
_DEGREE_MARKERS_EN = (
    "b.sc", "bsc ", "b.tech", "btech", "b.b.a", "bba ", "b.c.a", "bca ",
    "b.arch", "barch", "b.design", "b.pharm", "b.com", "b.ed ",
    "m.sc", "msc ", "m.tech", "mtech", "m.b.a", "mba ", "m.c.a", "mca ",
    "m.arch", "m.phil", "m.pharm", "m.ed ", "mbbs",
    "bachelor", "master", "diploma", "ph.d", "phd",
)
_DEGREE_MARKERS_GU = (
    "બીએસસી", "બીટેક", "બીબીએ", "બીસીએ", "બીઆર્ક", "બીકોમ", "બીફાર્મ",
    "એમએસસી", "એમટેક", "એમબીએ", "એમસીએ", "એમઆર્ક", "એમકોમ", "એમફાર્મ",
    "ડિપ્લોમા", "પીએચડી", "બેચલર", "માસ્ટર",
)


def _has_degree_level_marker(text: str) -> bool:
    """True if the user has named a degree level (B.Sc, M.Sc, B.Tech, …).
    Used to decide whether to ask for clarification when retrieved
    variants span multiple degree levels."""
    if not text:
        return False
    # Normalise so "B. Tech." and "B.Tech" both reduce to "b.tech".
    # Catalog canonical names use spaces around dots; user input usually
    # doesn't — this collapse handles both.
    low = " " + re.sub(r"\.\s+", ".", text.lower()) + " "
    if any(m in low for m in _DEGREE_MARKERS_EN):
        return True
    return any(m in text for m in _DEGREE_MARKERS_GU)


# Words that don't discriminate between programs. Anything left after
# stripping these is a "topic" signature. 'degree' is here so the catalog
# entry 'Diploma + Degree (Automobile)' groups with 'Diploma (Automobile)'
# and 'B. Tech. (Automobile)' on signature {'automobile'} (2026-05-21 prod
# bug: caller asked about automobile, agent said 'no info'). 'graduate' is
# here so 'Graduate Marine Engineering (GME)' reduces to {'marine', 'gme'}
# and (via synonyms below) groups with B.Tech (Marine).
_TOPIC_STOPWORDS = frozenset({
    "and", "the", "with", "for", "in", "of", "by", "research",
    "engineering", "science", "management", "studies", "degree",
    "graduate", "hons", "hons.", "honours",
})

# Synonym map: tokens that mean the same topic. Applied INSIDE
# _topic_signature so all variants collapse onto a canonical token. Used
# to bridge cross-school marine (B.Tech Marine, B.Sc Nautical Science,
# DNS Diploma in Nautical Science, GME Graduate Marine Engineering) under
# one signature {'marine'} so they group + disambig fires when the
# caller says 'મરીન વિશે'.
_TOPIC_SYNONYMS: dict[str, str] = {
    "nautical": "marine",
    "gme": "marine",
    "dns": "marine",
}

# Regex that strips the degree-prefix from a program name so we can compare
# variants across degree levels. e.g. 'B. Tech. (Automobile)' and 'Diploma
# (Automobile)' both reduce to '(automobile)' which then tokenises to
# {'automobile'} — the topic signature.
_DEGREE_PREFIX_RE = re.compile(
    r"\b(?:b\.?\s*sc\.?|m\.?\s*sc\.?|b\.?\s*tech\.?|m\.?\s*tech\.?|"
    r"b\.?\s*b\.?\s*a\.?|m\.?\s*b\.?\s*a\.?|b\.?\s*c\.?\s*a\.?|m\.?\s*c\.?\s*a\.?|"
    r"b\.?\s*com\.?|m\.?\s*com\.?|b\.?\s*arch\.?|m\.?\s*arch\.?|"
    r"b\.?\s*pharm\.?|m\.?\s*pharm\.?|b\.?\s*ed\.?|m\.?\s*ed\.?|"
    r"b\.?\s*design|mbbs|diploma\s+in|diploma)\b\.?",
    re.IGNORECASE,
)


def _topic_signature(name: str) -> frozenset[str]:
    """Return the set of discriminator tokens in a program name — everything
    that's NOT the degree prefix, Honours marker, or a generic word.

    Two programs with the SAME topic_signature are 'the same topic at a
    different degree level' (e.g. 'B. Tech. (Automobile)' and 'Diploma
    (Automobile)' both signature to {'automobile'}). Used by the
    degree-level disambiguator below — covers all 18 ambiguous topic
    groups in the catalog, not just the paren-suffix cases.
    """
    if not name:
        return frozenset()
    n = _DEGREE_PREFIX_RE.sub(" ", name.lower())
    n = re.sub(r"\(\s*hons\.?\s*\)|hons\.?", " ", n)
    n = re.sub(r"[()\.\,&]+", " ", n)
    tokens = {t for t in n.split() if len(t) >= 3 and t not in _TOPIC_STOPWORDS}
    # Apply synonym normalisation so e.g. 'nautical' and 'gme' collapse
    # onto 'marine' — groups all four marine programs in disambig.
    if tokens & _TOPIC_SYNONYMS.keys():
        tokens = {_TOPIC_SYNONYMS.get(t, t) for t in tokens}
    return frozenset(tokens)


def _degree_level(name: str) -> str | None:
    """Classify a program by degree level + school. Granular enough that
    'B.Sc Agriculture' and 'B.Tech Agriculture Engineering' come out as
    different levels ('ug-sc' vs 'ug-tech') and trigger the ambiguity
    detector — same with BBA vs MBA, B.Pharm vs M.Pharm, etc.

    Returns: 'ug-sc' / 'ug-tech' / 'ug-mgmt' / 'ug-com' / 'ug-comp' /
    'ug-arch' / 'ug-design' / 'ug-pharm' / 'ug-ed' / 'ug-mbbs' /
    'ug-arts' / 'ug-pt' /
    'pg-sc' / 'pg-tech' / 'pg-mgmt' / 'pg-com' / 'pg-comp' / 'pg-arch' /
    'pg-pharm' / 'pg-ed' / 'pg-phil' / 'pg-pt' /
    'dip' / 'dip-nursing' / None.
    """
    # Normalise: collapse "B. Pharm." → "b.pharm" so canonical catalog
    # names (which carry spaces around dots) match the same substring
    # checks as compact user input.
    n = (name or "").lower()
    n = re.sub(r"\.\s+", ".", n)  # "b. tech" → "b.tech"
    # UG
    if "b.sc" in n: return "ug-sc"
    if "b.tech" in n: return "ug-tech"
    if "b.b.a" in n or "bba" in n: return "ug-mgmt"
    if "b.com" in n: return "ug-com"
    if "b.c.a" in n or "bca" in n: return "ug-comp"
    if "b.arch" in n: return "ug-arch"
    if "b.design" in n: return "ug-design"
    if "b.pharm" in n: return "ug-pharm"
    if "b.ed" in n: return "ug-ed"
    if "mbbs" in n: return "ug-mbbs"
    if "bpt" in n or "physiotherapy" in n: return "ug-pt"
    if n.startswith("ba ") or " ba " in (" " + n) or "(ba)" in n or "ba (honours)" in n: return "ug-arts"
    # PG
    if "m.sc" in n: return "pg-sc"
    if "m.tech" in n: return "pg-tech"
    if "m.b.a" in n or "mba" in n: return "pg-mgmt"
    if "m.com" in n: return "pg-com"
    if "m.c.a" in n or "mca" in n: return "pg-comp"
    if "m.arch" in n: return "pg-arch"
    if "m.phil" in n: return "pg-phil"
    if "m.pharm" in n: return "pg-pharm"
    if "m.ed" in n: return "pg-ed"
    if "mpt" in n: return "pg-pt"
    # Diploma + similar entry-level
    if "diploma" in n: return "dip"
    if " dns" in (" " + n) or n.startswith("dns"): return "dip"
    if "gnm" in n: return "dip-nursing"
    # Graduate Marine Engineering — industry-equivalent UG-tech path.
    # Without this rule GME would fall through to None and not group
    # with B.Tech (Marine) in the disambiguator.
    if "graduate marine" in n or "gme" in n: return "ug-tech"
    return None


# When user-text contains these markers, they're asking about ALTERNATIVES
# (other programs / variants). We must NOT skip RAG — the focused program
# alone won't surface alternatives. Round 9 latency optimization.
_TOPIC_BROADENING_TOKENS: tuple[str, ...] = (
    "બીજો", "બીજી", "બીજું", "બીજા", "બીજે", "બીજાય",
    "વિકલ્પ", "વિકલ્પો", "વિકલ્પમાં",
    "બીજો કોર્સ", "બીજો પ્રોગ્રામ", "બીજો option",
    "other", "alternatives", "alternative", "more options",
    "what else", "anything else", "any other",
    "બીજું શું", "વધારે", "વધુ",
)


def _looks_topic_broadening(text: str) -> bool:
    """True if the user is asking for ALTERNATIVES to the focused program.
    Used by the RAG-skip fast path — if the user wants alternatives, we
    must run full RAG (the focused program alone is insufficient)."""
    if not text:
        return False
    low = text.lower()
    return any(t.lower() in low or t in text for t in _TOPIC_BROADENING_TOKENS)


def _has_target_char(text: str, language: str) -> bool:
    rng = SCRIPT_RANGES.get(language)
    if rng is None:
        return any("a" <= c.lower() <= "z" for c in text)
    return any(rng[0] <= ord(c) <= rng[1] for c in text)


@dataclass
class TurnChunk:
    """One audio chunk yielded during a turn. PCM linear16 mono at session sample rate."""

    audio: bytes
    is_first: bool
    elapsed_ms: int


class BrainEngine:
    def __init__(self, rag: RAGEngine, llm: LLM, tts_stream: StreamingTTS) -> None:
        self.rag = rag
        self.llm = llm
        self.tts_stream = tts_stream

    async def turn(
        self,
        user_text: str,
        history: TurnHistory,
        language: str,
        rag_k: int = 2,
        tts_session: StreamingTTSSession | None = None,
        skip_rag: bool = False,
        caller_name: str | None = None,
        focused_program: dict | None = None,
        family_list: tuple[str, list[dict]] | None = None,
        stream_list: tuple[str, list[dict]] | None = None,
        conversation_state_brief: str | None = None,
        turn_signals_hint: str | None = None,
        meta_question: bool = False,
        turn_dialog_hint: str | None = None,
        turn_proactive_hint: str | None = None,
        campus_list: tuple[str, list[dict]] | None = None,
    ) -> AsyncIterator[TurnChunk]:
        """If tts_session is provided, reuse it (lower latency on turns 2+).

        skip_rag — bypass RAG entirely (used on turn 1, name exchange).
        caller_name + focused_program — persistent session slots.
        family_list — when user asked broadly about a family (BCA, BBA, …)
            and there are >1 variants, pass (family_key, variants) here so
            the LLM can list them and ask 'which one?'. SUPPRESSES RAG so
            the LLM doesn't get drowned in unrelated programs.
        stream_list — same idea for streams (Engineering, Commerce, …).
        """
        from app.brain.catalog import format_program_context

        history.add_user(user_text, language=language)

        # ── Campaign hard-lock: drop a NON-target focused_program ──────────
        # (2026-06-24) When a single-course campaign is active, the caller
        # merely NAMING another program (e.g. "મારે B.Tech કરવું છે") makes
        # RAG focus it — which then builds that program's fee into context
        # AND fires the "answer about THIS program" slot below, so the LLM
        # happily quotes a B.Tech fee/duration and the pivot never happens.
        # Nulling a non-target focus here forces the CAMPAIGN_RULE pivot to
        # own the mention. Target = a B.Sc IT program at DCS Main Campus.
        from app.config import get_settings as _gs
        if focused_program is not None and _gs().bsc_it_only_campaign:
            _fp_norm = (focused_program.get("program") or "").lower().replace(" ", "")
            _fp_inst = (focused_program.get("institute") or "").strip()
            _is_target = (
                _fp_inst == "DCS- Main Campus"
                and ("b.sc" in _fp_norm or "bsc" in _fp_norm)
                and "it" in _fp_norm
            )
            if not _is_target:
                log.info(
                    "campaign lock: dropped non-target focused_program %r (%s)",
                    focused_program.get("program"), _fp_inst or "no-institute",
                )
                focused_program = None

        # Disambiguator flags — initialized here so they're defined on every
        # code path. Without this, the slot_lines block below tries to read
        # `ask_degree` and gets UnboundLocalError when the RAG branch is
        # skipped (turn 1, family/stream override). Caller-1 production
        # crash 2026-05-21: no greeting played, user had to say "હલો" to
        # nudge the agent.
        ask_degree = False
        ambiguous_topic: str | None = None
        ambiguous_doc_ids: set[int] = set()

        # ── Family / stream override ──────────────────────────────────────
        # If the user asked about a category, the LLM must answer from a
        # CURATED list, not whatever RAG happens to retrieve. We replace
        # the RAG context entirely with the family/stream subset.
        family_override = None
        if family_list is not None:
            fam_key, variants = family_list
            # Campaign scope (2026-07-02): scrap Ahmedabad campus everywhere.
            if _gs().bsc_it_only_campaign:
                variants = [
                    v for v in variants
                    if "ahmedabad" not in (v.get("institute") or "").lower()
                ]
            # De-dup by program name, cap at 6 so the LLM message stays short
            seen: set[str] = set()
            unique: list[dict] = []
            for v in variants:
                name = (v.get("program") or "").strip()
                if name and name not in seen:
                    seen.add(name)
                    unique.append(v)
                if len(unique) >= 6:
                    break
            family_override = (fam_key, unique)

        stream_override = None
        if stream_list is not None and family_override is None:
            stream_key, programs = stream_list
            # Campaign scope (2026-07-02): scrap Ahmedabad campus everywhere.
            if _gs().bsc_it_only_campaign:
                programs = [
                    v for v in programs
                    if "ahmedabad" not in (v.get("institute") or "").lower()
                ]
            # Streams can have 40+ programs — cap to ~8 most-representative.
            seen = set()
            unique = []
            for v in programs:
                name = (v.get("program") or "").strip()
                if name and name not in seen:
                    seen.add(name)
                    unique.append(v)
                if len(unique) >= 8:
                    break
            stream_override = (stream_key, unique)

        # Hard-lock guard: if the caller has named a SPECIFIC variant
        # (focused_program set) AND a family override was about to fire,
        # collapse to just that one variant. Multi-variant context was
        # causing fee-number cross-contamination (M.Sc. IT Cyber Security
        # being quoted at 1.8L when JSON says 94000 — the LLM was
        # plucking a number from a sibling variant's record). One record
        # in context => physically impossible to mix.
        if (
            focused_program is not None
            and family_override is not None
        ):
            fam_key, unique = family_override
            focused_name = (focused_program.get("program") or "").strip().lower()
            if any(
                (v.get("program") or "").strip().lower() == focused_name
                for v in unique
            ):
                log.info(
                    "family override SUPPRESSED: caller named specific variant %r, locking to it",
                    focused_program.get("program"),
                )
                family_override = None  # fall through to focused-program path

        # Family-override hard-lock #2 — broader rule. If the user's
        # CURRENT utterance names any specific program via catalog
        # find_by_name (regardless of whether that program appears in
        # the family list), the family listing is overreach. Caller's
        # intent is a specific variant — let the focused-program +
        # RAG path handle it. 2026-05-21 prod bug: caller said "B.Tech
        # Marine"; catalog matched B.Tech Marine; family override
        # ALSO fired for the 'marine' family which contains GME / ETO /
        # G.P. Ratings (NOT B.Tech Marine, which is tagged under btech).
        # Above hard-lock failed because B.Tech Marine wasn't in the
        # marine family's variant list. fact_guard then "corrected" the
        # correct 4 લાખ 95 હજાર to GME's 3 લાખ 80 હજાર. This guard
        # mirrors the stream-override one below.
        if family_override is not None:
            from app.brain.catalog import get_catalog as _get_cat
            if _get_cat().find_by_name(user_text) is not None:
                log.info(
                    "family override SUPPRESSED: utterance %r names a specific variant",
                    user_text[:80],
                )
                family_override = None

        # Stream-override hard-lock: if the user's CURRENT utterance names
        # any specific program (via catalog.find_by_name), the stream
        # listing is overreach — the caller's intent is to ask about a
        # specific variant, not browse a category. Suppress so the RAG
        # path runs and retrieves the actual variant docs. Without this
        # guard, "B.Sc IT Data Science" got matched by find_stream_query
        # → Science stream override fired → LLM saw 8 program names and
        # said "no info about that program" (2026-05-20 production bug).
        if stream_override is not None:
            from app.brain.catalog import get_catalog as _get_cat
            if _get_cat().find_by_name(user_text) is not None:
                log.info(
                    "stream override SUPPRESSED: utterance %r names a specific variant",
                    user_text[:80],
                )
                stream_override = None

        if meta_question:
            # Meta-question mode (Round 7). Caller is asking ABOUT the
            # conversation itself ("what did I ask?", "what's my name?",
            # "first question?"). NO catalog data — answer purely from
            # history. Without this the LLM gets random RAG hits and
            # hallucinates "you asked about BCA" when the caller actually
            # asked about Marine + Diploma Civil (2026-05-21 prod log).
            #
            # The LLM already has the full conversation history in
            # `messages`. The directive below tells it to USE that
            # history and forbids any catalog invention.
            context_blocks: list[str] = [
                "META_QUESTION_MODE: The caller is asking about THIS conversation "
                "(what they said, what you said, their name, previous topics). "
                "Answer ONLY from the conversation history above. DO NOT consult "
                "the program catalog. DO NOT invent programs. If you genuinely "
                "don't remember a specific detail, say so plainly. Cite specific "
                "user turns when you can ('તમે Marine વિશે પૂછ્યું હતું, અને "
                "પછી Diploma Civil વિશે'). Reply in 1-2 short sentences."
            ]
            # Drop focused_program PIN too — meta-question is not about
            # any specific program. slot_lines will skip the active-program
            # line when focused_program is None.
            focused_program = None
        elif skip_rag:
            context_blocks: list[str] = []
        elif campus_list is not None:
            # CAMPUS OVERRIDE (2026-05-21) — caller mentioned a campus
            # location ("Ahmedabad" / "અમદાવાદ"). Lock the LLM to ONLY
            # the programs at that campus so it can't hallucinate
            # "Ahmedabad વિશે માહિતી નથી" or quote a Mehsana program
            # under the Ahmedabad campus name. The catalog returns the
            # full filtered list; we hand them all to the LLM as
            # context blocks + a directive to ASK which one.
            campus_key, campus_progs = campus_list
            context_blocks = [
                f"USER_ASKED_ABOUT_CAMPUS: {campus_key}. "
                f"The caller mentioned the {campus_key.capitalize()} campus. "
                f"Programs AVAILABLE at this campus are listed below. If the "
                f"caller named a SPECIFIC program (e.g. 'BBA Finance') that is "
                f"NOT in this list, gently say it's at the Mehsana main campus "
                f"and suggest the CLOSEST {campus_key.capitalize()}-campus "
                f"alternative from the list. If the caller asked generically "
                f"about the campus, list 3-4 program names from below and ask "
                f"which area interests them. Reply in 1-2 short sentences."
            ]
            context_blocks.extend(format_program_context(v) for v in campus_progs)
            log.info("campus override: %s -> %d programs", campus_key, len(campus_progs))
            # Drop focused_program PIN — the campus-list scope replaces it.
            focused_program = None
        elif family_override is not None:
            fam_key, unique = family_override
            context_blocks = [
                f"USER_ASKED_ABOUT_FAMILY: {fam_key}. "
                f"List these variants by name only (no fees/details) and ask which they want:"
            ]
            context_blocks.extend(format_program_context(v) for v in unique)
            log.info("family override: %s -> %d variants", fam_key, len(unique))
        elif stream_override is not None:
            stream_key, unique = stream_override
            context_blocks = [
                f"USER_ASKED_ABOUT_STREAM: {stream_key}. "
                f"List the program CATEGORIES under this stream (no fees), and ask which they want:"
            ]
            context_blocks.extend(format_program_context(v) for v in unique)
            log.info("stream override: %s -> %d programs", stream_key, len(unique))
        else:
            # Retrieval-query strategy:
            #   • Short follow-up ("ફી શું છે?", "હમ") — current msg has no
            #     program signal, so concat the previous assistant reply +
            #     last 2 user turns to inherit topic context.
            #   • Substantive current msg (>=25 chars or names a program) —
            #     use ONLY the current user msg. Mixing in the assistant's
            #     previous answer about a DIFFERENT program (e.g. Marine,
            #     when user just pivoted to Cyber Security) poisons the
            #     embedding and drowns the new topic. Fixed in production
            #     after the bug surfaced in the log.
            from app.brain.catalog import get_catalog

            catalog = get_catalog()
            user_match = catalog.find_by_name(user_text)
            mentions_a_program = user_match is not None

            # RAG runs EVERY turn — explicit user instruction (2026-05-21):
            # "use RAG as a brain strictly". The earlier Round 9 fast-path
            # that skipped RAG on follow-up turns has been removed — it
            # introduced an UnboundLocalError that silently crashed every
            # follow-up turn, AND it deviated from the brain-by-RAG design.
            # Embed-cache (RAGEngine) and pre-warmed translate cache keep
            # this fast without skipping retrieval.
            use_current_only = mentions_a_program or len(user_text.strip()) >= 25

            if use_current_only:
                retrieval_query = user_text.strip()
            else:
                user_msgs = [t.text for t in history.turns if t.role == "user"]
                asst_msgs = [t.text for t in history.turns if t.role == "assistant"]
                parts: list[str] = []
                if asst_msgs:
                    parts.append(asst_msgs[-1])
                parts.extend(user_msgs[-2:] if len(user_msgs) >= 2 else user_msgs)
                retrieval_query = " ".join(parts)

            # Cap at 200 chars so embed latency stays predictable turn-to-
            # turn (long histories had pushed it to ~400ms before).
            if len(retrieval_query) > 200:
                retrieval_query = retrieval_query[-200:]

            # Adaptive rag_k: when focused_program is already set, retrieve
            # just 1 doc — the focused record is the anchor, RAG only
            # needs to surface 1 closely-related sibling at most. Cuts
            # ~400 tokens of context per follow-up turn → ~200-300ms
            # faster LLM first-token. (Round 12 latency fix.)
            effective_k = 1 if focused_program is not None else rag_k
            docs = await self.rag.retrieve(retrieval_query, k=effective_k)
            # Campaign scope (2026-07-02): scrap the Ahmedabad campus entirely.
            # The B.Sc IT campaign is DCS Main Campus ONLY, so drop every
            # retrieved doc whose institute is an Ahmedabad campus — otherwise
            # its same-named, different-fee/counsellor records could surface.
            if _gs().bsc_it_only_campaign:
                docs = [
                    d for d in docs
                    if "ahmedabad" not in (d.get("institute") or "").lower()
                ]
            programs = [d.get("program", "?") for d in docs]
            log.info(
                "rag retrieved %s for query: %r",
                programs, retrieval_query[:160],
            )

            # Stale-focus guard. If focused_program's topic signature has
            # ZERO overlap with any retrieved doc's signature, the user
            # has pivoted to an unrelated topic and the focused pin is
            # poisoning the LLM context. Drop it. (2026-05-21 prod bug:
            # caller was focused on Cyber Security, asked about
            # 'ઓટોમોબાઇલ' (automobile); RAG retrieved automobile docs but
            # the Cyber Security pin + slot line made the LLM say
            # "no info" instead of using the automobile docs.)
            #
            # CRITICAL CARVE-OUT (2026-05-21 demo-call): only drop focused
            # when the CURRENT user utterance actually names a different
            # program. If the user just said a bare follow-up like
            # "fees and eligibility" / "duration?" / "બીજું શું?",
            # `mentions_a_program` is False — RAG's random retrieval should
            # NOT be allowed to wipe the focused program the caller is
            # clearly still discussing. Bug case: focused=BBA Logistics,
            # user said "ફીસ અને એલિજિબિલિટી", RAG retrieved B.Tech CSE
            # at random, focused was dropped → fact_guard lost its anchor →
            # fee hallucination leaked through.
            # ALSO carve out the case where the user JUST named the focused
            # program in THIS turn (user_match == focused). The drop logic
            # was wiping the correct focus when RAG returned an unrelated
            # doc — e.g. user said "ડેટા સાયન્સ", focused locked to B.Sc
            # IT Data Science correctly, but RAG returned M.Sc Food Science
            # (embedding similarity on "Science"). Guard dropped the
            # correct focus → agent said "I can't help with B.Sc IT Data
            # Science" on the user's clearest possible utterance.
            user_match_is_focus = (
                user_match is not None
                and focused_program is not None
                and (user_match.get("program") or "") == (focused_program.get("program") or "")
            )
            if (
                focused_program is not None
                and docs
                and mentions_a_program
                and not user_match_is_focus
            ):
                focused_sig = _topic_signature(focused_program.get("program") or "")
                rag_sigs = [_topic_signature(d.get("program") or "") for d in docs]
                if focused_sig and not any(focused_sig & s for s in rag_sigs):
                    log.info(
                        "focused_program %r dropped: zero topic overlap with RAG %s",
                        focused_program.get("program"),
                        [d.get("program") for d in docs],
                    )
                    focused_program = None

            # Guardrail A: SINGLE-SOURCE CONTEXT LOCK (2026-05-21).
            # When focused_program is set AND the user's current utterance
            # explicitly names that program (catalog.find_by_name returns
            # the same record), the context must contain ONLY that one
            # record. No RAG-retrieved siblings. Reason: sibling fees in
            # the LLM context allow hallucinated cross-program mixups
            # that fact_guard's whitelist treats as "valid" (the sibling
            # fee IS in the whitelist) and passes through. With single-
            # source lock, no sibling fee exists in context, so any
            # incorrect fee gets caught.
            single_source_lock = False
            if focused_program is not None:
                from app.brain.catalog import get_catalog as _gc_a
                _user_match = _gc_a().find_by_name(user_text)
                if (
                    _user_match is not None
                    and (_user_match.get("program") or "").strip().lower()
                    == (focused_program.get("program") or "").strip().lower()
                ):
                    single_source_lock = True
                    log.info(
                        "single-source lock: focused=%r matches user utterance",
                        focused_program.get("program"),
                    )

            # Degree-level disambiguator (2026-05-20, generalised 2026-05-21).
            # Group retrieved docs by topic signature (token set minus degree
            # prefix / Hons / stopwords). Any group with 2+ docs spanning
            # multiple degree levels = ambiguous → ASK rather than silently
            # inheriting the focused program's degree level.
            #
            # Covers all 18 cross-level pairs in the catalog: IT specialisations
            # (B.Sc vs M.Sc), B.Tech vs Diploma (Automobile, Civil, Computer,
            # Electrical, Mechanical, Mechatronics), B.Tech vs M.Tech (Biomedical,
            # Chemical, Computer), B.Sc vs M.Sc (Biotechnology, Microbiology,
            # Food Tech), BBA vs MBA (Business Analytics, International Business),
            # cross-school pairs (Agriculture: B.Sc Hons vs B.Tech).
            if docs and not _has_degree_level_marker(user_text):
                from collections import defaultdict
                groups: dict[frozenset[str], list[dict]] = defaultdict(list)
                for d in docs:
                    sig = _topic_signature(d.get("program") or "")
                    if sig:
                        groups[sig].append(d)
                for sig, group in groups.items():
                    if len(group) < 2:
                        continue
                    levels = {
                        lvl for lvl in
                        (_degree_level(g.get("program") or "") for g in group)
                        if lvl is not None
                    }
                    if len(levels) > 1:
                        ask_degree = True
                        ambiguous_topic = " ".join(sorted(sig))
                        ambiguous_doc_ids = {id(g) for g in group}
                        log.info(
                            "degree ambiguous: topic=%r levels=%s — asking caller",
                            ambiguous_topic, sorted(levels),
                        )
                        break

            context_blocks = []
            seen_programs: set[str] = set()
            if ask_degree:
                # AMBIGUOUS-TOPIC MODE. Build context with ONLY the variant
                # NAMES (no fees, no counsellors, no details) so the LLM
                # physically cannot quote a number. The directive is also
                # promoted to slot_lines (below) so it sits in SESSION
                # SLOTS rather than Reference context — same authority as
                # caller_name. Earlier attempt put it in context_blocks
                # only; the LLM ignored it and hallucinated a B.Tech Cyber
                # Security with B.Tech Marine's fee (2026-05-20 prod log).
                #
                # Only list variants from the AMBIGUOUS GROUP (same topic
                # signature) — unrelated docs from the same RAG batch would
                # confuse the question.
                variant_names = []
                for d in docs:
                    if id(d) not in ambiguous_doc_ids:
                        continue
                    nm = (d.get("program") or "").strip()
                    if nm and nm not in seen_programs:
                        seen_programs.add(nm)
                        variant_names.append(nm)
                if variant_names:
                    context_blocks.append(
                        "VARIANTS_AVAILABLE_FOR_THIS_TOPIC (names only, "
                        "no fees in context — DO NOT invent fees):\n"
                        + "\n".join(f"- {n}" for n in variant_names)
                    )
                # Drop the pin so slot_lines doesn't add a stale active-program line.
                focused_program = None
            elif focused_program:
                context_blocks.append(format_program_context(focused_program))
                seen_programs.add((focused_program.get("program") or "").strip())
            # Add RAG-retrieved programs that aren't already pinned. This
            # lets the LLM mention alternatives if the user is browsing,
            # without overwriting the focused program.
            #
            # SKIPPED in ASK_DEGREE mode — we deliberately keep the LLM
            # blind to fees/details when the topic is ambiguous, so it
            # can't invent or merge numbers from sibling variants.
            #
            # ALSO SKIPPED under single_source_lock — caller named a
            # specific variant, so any RAG sibling in context is a
            # cross-contamination risk. Guardrail A.
            if not ask_degree and not single_source_lock:
                for d in docs:
                    pkey = (d.get("program") or "").strip()
                    if pkey in seen_programs:
                        continue
                    seen_programs.add(pkey)
                    context_blocks.append(format_program_context(d))

        context = "\n".join(context_blocks) if context_blocks else ""

        # ── Campaign counsellor lock (2026-07-02) ─────────────────────────
        # For the B.Sc IT campaign, EVERY counsellor reference must be
        # Dr. Amit Suthar at the DCS Main Campus. We rewrite the counsellor
        # line in the pinned context so BOTH the LLM prompt AND the fact-guard
        # whitelist (built from this same string below) carry ONLY his name +
        # Main-Campus number. Net effect: the agent can never surface any
        # other counsellor, and the only phone number it can voice is his.
        if _gs().bsc_it_only_campaign:
            _amit_line = (
                "Counsellor: Dr. Amit Suthar, Phone: 9998583001, "
                "Email: abs01@ganpatuniversity.ac.in."
            )
            if context and "Counsellor:" in context:
                context = re.sub(r"Counsellor:[^\n]*", _amit_line, context)
            else:
                # Browse mode / no program pinned this turn — still expose
                # Amit's contact so a transfer offer works AND his number is
                # whitelisted for the fact-guard (otherwise it'd be stripped).
                context = f"{context}\n{_amit_line}".strip() if context else _amit_line

        # Build the message list. Inject a session-context system message
        # AFTER the static system prompt (handled by LLM.stream) so the
        # LLM always sees the current name + focused program at turn time.
        messages = history.to_openai_messages()
        slot_lines: list[str] = []

        # ── CAMPAIGN_RULE: hard B.Sc IT-only lock (2026-06-24) ────────────
        # Highest-priority directive — injected on EVERY turn so the LLM
        # cannot drift back to BCA/BBA/B.Tech/MBA/etc. as the conversation
        # progresses past the opening exchange. Toggled by env flag
        # BSC_IT_ONLY_CAMPAIGN (defaults True). DCS Main Campus only — the
        # Ahmedabad city campus is EXCLUDED. Flip to false when the campaign
        # ends — no code change needed.
        from app.config import get_settings as _gs
        if _gs().bsc_it_only_campaign:
            slot_lines.append(
                "CAMPAIGN_RULE [HIGHEST PRIORITY — OVERRIDES ALL OTHER SLOTS, RAG CONTEXT, "
                "AND PRIOR TURNS]: This campaign sells ONLY B.Sc IT at Ganpat University's "
                "DCS Main Campus (Mehsana). The Ahmedabad city campus is NOT offered in "
                "this campaign — never mention or route to it. "
                "Behave like a warm, persuasive human admissions counsellor — NOT a menu "
                "bot. Internally know these 4 B.Sc IT variants exist (do NOT enumerate "
                "them to the caller unless STEP 3 below is reached): "
                "B.Sc IT Honours (general Information Technology track); "
                "B.Sc IT Honours with Data Science specialisation; "
                "B.Sc IT Honours with Cyber Security specialisation; "
                "B.Sc IT Honours with AI and ML specialisation. "
                "DEFAULT BEHAVIOR — for EVERY caller utterance that is NOT an explicit "
                "by-name inquiry about a specific non-B.Sc-IT program, follow this "
                "3-step pitch in order: "
                "STEP 1 (turns 1-2 OR when caller is exploring): ONE warm value-led "
                "Gujarati sentence about B.Sc IT's value — a 4-year IT honours degree "
                "with deep hands-on training in software, data, cyber and AI, "
                "industry-aligned curriculum and strong placement record. End with ONE "
                "soft question to gauge interest, like asking if they're interested in "
                "computers or the IT field. DO NOT mention specialisation names yet. "
                "DO NOT enumerate variants. "
                "STEP 2 (caller showed interest): mention ONE differentiator — that "
                "B.Sc IT has specialisations and ask which direction interests them "
                "(general IT, data science, cyber security, or AI). Mention AT MOST "
                "TWO variant names if pressed for examples — NEVER all four. "
                "STEP 3 (caller picked a direction): name the ONE matching specific "
                "variant and offer to connect them with a counsellor for fees and "
                "admission details. "
                "PIVOT BEHAVIOR — fires ONLY when the caller's CURRENT user message "
                "explicitly names a specific non-B.Sc-IT program by name (examples that "
                "trigger pivot: 'BCA', 'BBA', 'B.Com', 'B.Tech', 'MBA', 'BA', "
                "'Nursing', 'B.Pharm', 'BPT', 'B.Design', 'B.Arch', 'Marine', "
                "'B.Sc Agri', 'GNM', or their Gujarati equivalents). DO NOT fire the "
                "pivot on: greetings, test phrases, gibberish, open questions like "
                "'what courses do you have' or 'tell me about admissions', stream "
                "mentions ('arts', 'commerce', 'PCB'), or anything ambiguous. When "
                "the pivot DOES fire — acknowledge briefly in ONE Gujarati clause "
                "that the program is offered at Ganpat (phrase it naturally in your "
                "own words — do NOT use a fixed template), then IMMEDIATELY redirect "
                "to selling B.Sc IT's value (career outcomes, hands-on technical depth, "
                "IT industry demand, our placement record). DO NOT quote fees, "
                "duration, eligibility for the non-B.Sc-IT program. End with a soft "
                "hook back toward B.Sc IT interest. "
                "HARD RULES — "
                "(1) NEVER recite all 4 B.Sc IT names in one sentence, EVEN when the "
                "caller says 'tell me about all options' or 'list all 4'. Instead, "
                "describe B.Sc IT as ONE program family with specialisations and ask "
                "what direction interests them. "
                "(2) NEVER speak like a menu ('Option 1: general IT, Option 2: Data "
                "Science, ...'). NEVER use numbered lists for B.Sc IT variants. "
                "(3) NEVER recommend BCA, BBA, B.Tech, MBA, BPT, Nursing, B.Pharm, "
                "B.Com, BA, B.Design, B.Arch, B.Sc Agri, Marine, GNM, or any other "
                "program. "
                "(4) If the caller refuses B.Sc IT after the pivot, politely note "
                "their interest and offer a counsellor transfer — do NOT keep pushing. "
                "(5) This rule applies on EVERY turn, regardless of caller's 12th "
                "background or prior focus. If RAG context has non-B.Sc-IT programs OR "
                "the Ahmedabad campus, IGNORE it — speak ONLY about B.Sc IT at the "
                "Main Campus. "
                "(6) Use warm Gujarati, short conversational lines, ONE focused "
                "question per turn. Sound like a person, not a script. Do NOT parrot "
                "phrases from this rule — generate fresh natural phrasing each turn. "
                "(7) COUNSELLOR: our counsellor for this campaign is Dr. Amit Suthar "
                "(DCS Main Campus), phone 9998583001. When the caller asks for a "
                "counsellor/number or wants to talk to a person, give ONLY Dr. Amit "
                "Suthar and ONLY this number. NEVER name any other counsellor and "
                "NEVER read any other phone number. "
                "(8) We do NOT offer any bridge course. NEVER mention a bridge "
                "course, foundation course, or maths bridge — even if the caller "
                "is from Commerce/Arts. B.Sc IT is simply open to all streams; "
                "state that plainly without inventing any bridge/foundation."
            )

        # ADMISSION_TIMING (2026-07-02): the LLM kept inferring a specific
        # intake window ("એડમિશન માર્ચ-એપ્રિલમાં શરૂ થાય છે"). Admissions are
        # OPEN NOW — never name a month/season. Always-on (not campaign-gated).
        slot_lines.append(
            "ADMISSION_TIMING: When asked when admissions open / start / the last "
            "date / intake, say ONLY that our admissions are open now (Gujarati: "
            "'અમારા એડમિશન અત્યારે ખુલ્લા છે'). NEVER mention a specific month, "
            "season, or window like 'March-April' / 'માર્ચ-એપ્રિલ'. Do NOT invent "
            "form-submission deadlines. If they want exact dates, offer to connect "
            "a counsellor."
        )

        if caller_name:
            slot_lines.append(f"The caller's name is {caller_name}. NEVER re-ask for it; use it occasionally.")

        # PRIORITY_OFFER slot (2026-06-07, narrowed 2026-06-13). On the
        # OPENING exchange — right after the caller has been greeted and
        # gave their name, before they've volunteered a program or a
        # 12th-grade background — the AI should proactively introduce
        # the active campaign's flagship programs and ask which one
        # interests them.
        #
        # 2026-06-13: switched to BCA-only for the arts-student campaign
        # launching tomorrow. BCA programs accept any 12th stream
        # (Science / Commerce / Arts) and have no Maths prereq, so they're
        # the right pitch for an arts-only cohort. The previous 6-program
        # flagship list is kept commented just below for easy swap-back
        # once the campaign ends.
        #
        # Gating (all must hold):
        #   • caller_name is set this turn or last → opening window
        #   • user_turn_count ≤ 1 → we're still in the very first
        #     back-and-forth (one user reply happened, no more)
        #   • focused_program NOT set → caller hasn't already named
        #     a specific program (otherwise normal RAG flow handles it)
        #   • user_text doesn't look like a stream/background → the
        #     existing USER_BACKGROUND slot below handles that path
        #   • user_text doesn't directly name a catalog program → if
        #     they named one, defer to that focused flow
        from app.brain.catalog import _looks_like_stream_background, get_catalog
        _user_turn_count = sum(1 for t in history.turns if t.role == "user")
        _already_named_program = False
        if user_text:
            try:
                _already_named_program = get_catalog().find_by_name(user_text) is not None
            except Exception:
                _already_named_program = False
        if (
            caller_name
            and _user_turn_count <= 1
            and not focused_program
            and not (user_text and _looks_like_stream_background(user_text))
            and not _already_named_program
        ):
            # ── ACTIVE: B.Sc IT-only persuasive pitch (Main Campus) ────────
            # The commerce/Dept-of-Computer-Science framing is now in the FIRST
            # line (the greet filler), so turn 1 just bridges into B.Sc IT's
            # value — do NOT repeat the commerce opening here.
            slot_lines.append(
                "PRIORITY_OFFER: OPENING exchange — caller just gave their name. "
                "Open with ONE warm, persuasive sentence about B.Sc IT's value at "
                "Ganpat University's Main Campus (4-year IT honours degree, deep "
                "hands-on training in software/data/cyber/AI, industry-aligned, "
                "strong placements). End with ONE soft question about their "
                "interest in computers/IT — e.g. 'શું તમને કમ્પ્યુટર અને "
                "IT ક્ષેત્રમાં રસ છે?'. DO NOT repeat the commerce/Department "
                "greeting already said. DO NOT list specialisation names. DO NOT "
                "enumerate the 4 B.Sc IT variants. DO NOT quote any fee. DO NOT "
                "mention any non-B.Sc-IT program or the Ahmedabad campus. Sound "
                "like a real counsellor, not a menu."
            )
            # ── PAUSED: 6-flagship list (swap-back when arts campaign ends) ──
            # slot_lines.append(
            #     "PRIORITY_OFFER: This is the OPENING exchange. The caller just gave "
            #     "their name. Before answering open-ended questions, briefly "
            #     "introduce our 6 flagship programs by name and ask which one "
            #     "interests them. The 6 are: B.Sc IT (Hons.) (AI & ML), B.Sc IT "
            #     "(Hons.) (Cyber Security), B.Sc IT (Hons.) (general IT track), "
            #     "BCA + MCA Dual Degree (3+2 years integrated), B.Sc IT (Hons.) "
            #     "(Infrastructure Management Services), and BBA. Reply with ONE "
            #     "warm short line that lists ALL six by name and asks which area "
            #     "interests them. DO NOT quote any fee. DO NOT add ANY other "
            #     "program. DO NOT ask follow-up questions about background yet."
            # )

        # USER_BACKGROUND slot (2026-05-21 demo bug). When the caller's
        # current utterance describes their 12th-grade academic background
        # (A-group/PCM, B-group/PCB, etc.) WITHOUT naming a specific
        # Ganpat program, inject a directive line telling the LLM what
        # eligible streams to suggest. Without this, the LLM was either
        # picking a random program ("congrats on B group, let me tell you
        # about B.Sc Nautical Science") OR locking onto whatever was
        # currently focused (Food Science Tech). Both wrong.
        if user_text and _looks_like_stream_background(user_text):
            low = user_text.lower()
            _bsc_it_only = _gs().bsc_it_only_campaign
            if _bsc_it_only:
                # B.Sc IT-only campaign — caller's background doesn't change the
                # pitch. Emit a unified B.Sc-IT-pivot directive that wins over
                # the stream-specific branches below.
                slot_lines.append(
                    "USER_BACKGROUND: caller mentioned their 12th-grade stream. "
                    "IGNORE the stream for recommendation purposes — campaign is "
                    "B.Sc IT-only at the Main Campus (see CAMPAIGN_RULE above). "
                    "Reassure them briefly that B.Sc IT is open to commerce/any "
                    "stream students, then "
                    "sell B.Sc IT's value in ONE warm sentence (IT careers, software, "
                    "data, cyber, placements) and ask ONE soft question about their "
                    "interest in computers/IT. DO NOT list the 4 B.Sc IT variants by "
                    "name. DO NOT mention BCA, B.Pharm, Nursing, GNM, BPT, B.Sc Agri, "
                    "B.Tech, BBA, B.Com, MBA, BA, B.Design, B.Arch, Marine, or any "
                    "other program, and DO NOT mention the Ahmedabad campus."
                )
                focused_program = None
            elif any(k in low for k in (
                "pcb", "b group", "b-group", "બી ગ્રુપ", "પીસીબી",
                "physics chemistry biology", "phy chem bio",
                "ફિઝિક્સ કેમેસ્ટ્રી બાયોલોજી",
            )):
                slot_lines.append(
                    "USER_BACKGROUND: PCB (Physics/Chemistry/Biology, 12th science B-group). "
                    "Eligible Ganpat programs include: B.Pharm, B.Sc Nursing, GNM, "
                    "Bachelor of Physiotherapy, B.Sc Agriculture. Reply with ONE short "
                    "line listing 3-4 of these by name and ask which interests them. "
                    "DO NOT pick a single program. DO NOT quote any fee."
                )
            elif any(k in low for k in (
                "pcm", "a group", "a-group", "એ ગ્રુપ", "પીસીએમ",
                "physics chemistry maths", "phy chem maths",
                "ફિઝિક્સ કેમેસ્ટ્રી મેથ્સ",
            )):
                slot_lines.append(
                    "USER_BACKGROUND: PCM (Physics/Chemistry/Maths, 12th science A-group). "
                    "Eligible Ganpat programs include: B.Tech (CSE / Marine / Mechanical / "
                    "Civil / Electrical / Automobile), B.Sc IT, BCA, B.Arch, B.Design. "
                    "Reply with ONE short line listing 3-4 of these by name and ask "
                    "which area interests them. DO NOT pick a single program. DO NOT "
                    "quote any fee."
                )
            elif any(k in low for k in (
                "commerce", "કોમર્સ", "કોમર્સમાં", "12 commerce", "12th commerce",
                "બારમું કોમર્સ", "બાર કોમર્સ", "12 કોમર્સ", "બારમા કોમર્સ",
            )):
                # Detect computer / IT interest in the SAME utterance OR
                # in the last few user turns. If present → lead with B.Sc IT.
                # User asked 2026-05-23: "commerce student interested in
                # computer should hear B.Sc options FIRST priority".
                recent_user_text = " ".join(
                    [t.text or "" for t in history.turns[-6:] if t.role == "user"]
                ) + " " + user_text
                rlow = recent_user_text.lower()
                wants_computer = any(k in rlow for k in (
                    "computer", "કમ્પ્યુટર", "કોમ્પ્યુટર",
                    "cyber", "સાયબર", "data science", "ડેટા સાયન્સ",
                    "ai", "ml", "એઆઈ", "એમએલ", "machine learning",
                    "programming", "પ્રોગ્રામિંગ", "coding", "કોડિંગ",
                    "it field", "આઈટી", "ઇન્ફોર્મેશન ટેકનોલોજી",
                ))
                if wants_computer:
                    slot_lines.append(
                        "USER_BACKGROUND: COMMERCE + COMPUTER-INTEREST. Caller did 12th Commerce "
                        "AND wants computers/IT/cyber/data. LEAD WITH B.Sc IT — ONLY B.Sc IT. "
                        "Mention ONLY these three by name in this order: B.Sc IT (Hons.) "
                        "(Cyber Security), B.Sc IT (Hons.) (Data Science), B.Sc IT (Hons.) "
                        "(AI & ML). DO NOT mention BCA, BBA, B.Com, MBA, or any other program "
                        "in this turn. DO NOT lead with 'BCA (Honours)' — that is the WRONG "
                        "first answer for this caller profile. NOTE: B.Sc IT needs a short "
                        "bridge course for non-Maths Commerce students — mention honestly. "
                        "NEVER recommend B.Tech (needs PCM). Reply with ONE short line listing "
                        "these three B.Sc IT options and ask which interests them. DO NOT "
                        "quote any fee."
                    )
                else:
                    slot_lines.append(
                        "USER_BACKGROUND: COMMERCE (12th Commerce — NO Physics, NO Maths, NO Biology). "
                        "Eligible programs at Ganpat: B.Sc IT (Hons) — Cyber Security / Data Science / "
                        "AI&ML (bridge course needed for non-Maths), BBA (Finance / Marketing / General "
                        "/ Business Analytics / International Business / FinTech), B.Com (Hons), BCA, "
                        "BA (Hons) Psychology/Economics, MBA (after graduation). "
                        "NEVER recommend B.Tech, B.Pharm, B.Sc Nursing, BPT, B.Sc Agri, B.Arch, "
                        "Marine — these REQUIRE PCM or PCB. Reply with ONE short line that "
                        "LEADS WITH B.Sc IT (Hons.) as the FIRST option, followed by 2-3 others "
                        "(e.g. BBA, BCA, B.Com). B.Sc IT MUST be mentioned FIRST in the sentence — "
                        "do NOT put BBA/BCA/B.Com before it. Ask which interests them. "
                        "DO NOT quote any fee."
                    )
            elif any(k in low for k in (
                "arts", "આર્ટ્સ", "આર્ટસ", "12 arts", "12th arts",
                "બારમું આર્ટ્સ", "બાર આર્ટ્સ",
            )):
                # 2026-06-13: narrowed to BCA-only for the arts-student
                # campaign. Old full-list version is preserved below for
                # easy swap-back when the campaign ends.
                # ── ACTIVE: BCA-only pitch ───────────────────────────
                slot_lines.append(
                    "USER_BACKGROUND: ARTS (12th Arts — NO science subjects). "
                    "Campaign focus: BCA only. Reassure them briefly that BCA welcomes "
                    "arts students (no maths/science prerequisite), then pitch BCA's "
                    "value in ONE warm sentence (career into IT/software/cyber, strong "
                    "placements, 3-year degree) and ask ONE soft question about their "
                    "interest in computers/IT. DO NOT list the 4 BCA variants by name "
                    "in this turn. DO NOT mention BBA, BA, B.Com, MBA, B.Design, B.Sc IT, "
                    "B.Tech, B.Pharm, Nursing, BPT, B.Sc Agri, B.Arch, Marine, or any "
                    "other program. DO NOT quote any fee."
                )
                # ── PAUSED: full-list version (swap back after campaign) ──
                # slot_lines.append(
                #     "USER_BACKGROUND: ARTS (12th Arts — NO science subjects). Eligible programs "
                #     "at Ganpat: BBA (any specialisation), BA (Hons) Psychology/Economics, BCA, "
                #     "B.Com (Hons), MBA (after graduation), B.Design. NEVER recommend B.Tech, "
                #     "B.Pharm, Nursing, BPT, B.Sc Agri, B.Arch, Marine, B.Sc IT — these REQUIRE "
                #     "PCM or PCB. Reply with ONE short line listing 3-4 eligible options."
                # )
            else:
                slot_lines.append(
                    "USER_BACKGROUND: caller is describing their 12th-grade stream. "
                    "Ask which area (engineering, science, computer applications, "
                    "management, pharmacy, nursing) they're considering. ONE short "
                    "line. DO NOT pick a specific program. DO NOT quote any fee."
                )
            # Suppress the active-program pin so the LLM doesn't try to
            # answer about a previously-focused program when the caller's
            # current turn is a background-disclosure / pivot.
            focused_program = None

        # ACADEMIC_LEVEL hard filter (2026-05-23, hardened 2026-05-24).
        # Caller's last completed qualification dictates the LEVEL we can
        # suggest:
        #   10th pass  → Diploma only       (no Bachelor, no Master)
        #   12th pass  → Bachelor only      (no Master)
        #   Graduate   → Master only        (no Bachelor — already have one)
        # Detection is NEWEST-FIRST: scan the most recent user turn first
        # so a pivot ("my brother is 12th") OVERRIDES an older mention
        # ("I'm a graduate"). Without this, the agent recommended MCA to
        # a 12th-pass brother because graduate-mention won by ordering.

        # Detection patterns (substring match — case-insensitive).
        # Order WITHIN a level doesn't matter; ordering BETWEEN levels does.
        GRADUATE_TOKENS = (
            "graduate", "graduation", "ગ્રેજ્યુએટ", "ગ્રેજ્યુએશન",
            "bsc complete", "bcom complete", "bba complete", "bca complete",
            "bsc કરેલું", "bcom કરેલું", "bba કરેલું", "bca કરેલું",
            "bsc પૂર્ણ", "bcom પૂર્ણ", "bba પૂર્ણ", "bca પૂર્ણ",
            # "કર્યું" (did) completion forms — callers say "મેં બીએ કર્યું છે"
            # far more often than "કરેલું". Missing these let a BA graduate
            # fall through to undefined level → got offered a Bachelor (BCA).
            "bsc કર્યું", "bcom કર્યું", "bba કર્યું", "bca કર્યું",
            "બીએસસી કર્યું", "બીકોમ કર્યું", "બીબીએ કર્યું", "બીસીએ કર્યું",
            "બીએસસી કરેલું", "બીકોમ કરેલું", "બીબીએ કરેલું", "બીસીએ કરેલું",
            # B.A. (Arts bachelor) — was entirely missing.
            "ba complete", "ba કરેલું", "ba કર્યું",
            "બીએ કર્યું", "બીએ કરેલું", "બીએ પૂર્ણ", "બીએ પાસ", "બીએ કમ્પ્લીટ",
            "bachelor done", "bachelor complete",
            "બેચલર પૂર્ણ", "બેચલર કરેલું", "બેચલર પાસ", "બેચલર કર્યું",
            "after graduation", "ગ્રેજ્યુએશન પછી", "ગ્રેજ્યુએશન કર્યું",
            "પોસ્ટ ગ્રેજ્યુએટ",
        )
        TWELFTH_TOKENS = (
            "12th pass", "12 pass", "12 પાસ", "12th પાસ",
            "12 કર્યું", "12th કર્યું", "12 પૂર્ણ", "12th પૂર્ણ",
            "12 પૂરું", "12th પૂરું",
            "12 commerce", "12th commerce", "12 કોમર્સ", "12th કોમર્સ",
            "12 science", "12th science", "12 સાયન્સ", "12th સાયન્સ",
            "12 arts", "12th arts", "12 આર્ટ્સ", "12th આર્ટ્સ",
            "બારમું પૂર્ણ", "બારમું પાસ", "બારમું પૂરું", "બારમું કર્યું",
            "બાર પાસ", "બાર પૂર્ણ", "બાર પૂરું", "બાર કર્યું",
            "બારમા પાસ", "બારમા પૂર્ણ",
            "ધોરણ 12 પાસ", "ધોરણ ૧૨ પાસ", "ધોરણ 12 પૂર્ણ",
            "twelfth pass", "twelfth complete",
            "ટ્વેલ્થ પાસ", "ટ્વેલ્થ કર્યું", "ટ્વેલ્થ પૂર્ણ",
            "after 12th", "after 12", "બારમા પછી", "12 પછી", "12th પછી",
        )
        TENTH_TOKENS = (
            "10th pass", "10 pass", "10 પાસ", "10th પાસ",
            "10 કર્યું", "10th કર્યું",
            "10 પૂર્ણ", "10 પૂરું", "10th પૂર્ણ", "10th પૂરું",
            "દસમું પાસ", "દસમું પૂર્ણ", "દસમું પૂરું", "દસમું કર્યું",
            "દસ પાસ", "ધોરણ 10 પાસ", "ધોરણ ૧૦ પાસ",
            "tenth pass", "tenth complete",
            "ટેન્થ પાસ",
            "after 10th", "after 10", "દસમા પછી",
        )

        def _detect_level_in(text: str) -> str | None:
            tlow = (text or "").lower()
            if not tlow:
                return None
            if any(k in tlow for k in GRADUATE_TOKENS):
                return "graduate"
            if any(k in tlow for k in TWELFTH_TOKENS):
                return "12th"
            if any(k in tlow for k in TENTH_TOKENS):
                return "10th"
            return None

        # STICKY level (2026-05-26): take the HIGHEST level mentioned anywhere
        # in the WHOLE call, not just the most recent turn. A caller's academic
        # ceiling only RISES as they reveal more — you can't un-graduate. The
        # old code (a) scanned only the last 8 turns, so a graduate signal from
        # turn 1 scrolled out by turn 5, and (b) took the newest match, so a
        # later non-graduate turn could downgrade it. A live BA graduate who
        # said "I'm not good at finance, what else?" then got offered BCA (a
        # Bachelor) — and caught it: "મેં તો ઓલરેડી બીએ કરેલું છે". Now once
        # graduate is seen, the caller STAYS graduate → only Master's offered.
        _LEVEL_RANK = {"10th": 1, "12th": 2, "graduate": 3}
        academic_level: str | None = None
        best_rank = 0
        for t in history.turns:
            if t.role != "user":
                continue
            lv = _detect_level_in(t.text or "")
            if lv and _LEVEL_RANK[lv] > best_rank:
                best_rank = _LEVEL_RANK[lv]
                academic_level = lv
        # The current utterance may not be in history yet — count it too.
        lv_now = _detect_level_in(user_text or "")
        if lv_now and _LEVEL_RANK[lv_now] > best_rank:
            academic_level = lv_now

        if academic_level == "10th":
            slot_lines.append(
                "ACADEMIC_LEVEL: 10th-pass. ONLY recommend DIPLOMA programs (3-year "
                "Diploma in Automobile / Civil / Computer / Electrical / IT / Mechanical / "
                "Mechatronics) or the 6-year Diploma+Degree dual programs. "
                "NEVER recommend any Bachelor (B.Tech, B.Sc, BBA, BCA, BCom, B.Pharm, "
                "B.Arch, B.Design, BPT, B.Sc Nursing, GNM) or Master (M.Sc, M.Tech, MBA, "
                "MCA, M.Pharm) to a 10th-pass caller — they're not eligible. If caller "
                "asks about a Bachelor/Master program, gently say 'એ માટે પહેલા 12 પાસ "
                "કરવી પડશે' and suggest the matching Diploma instead."
            )
        elif academic_level == "12th":
            slot_lines.append(
                "ACADEMIC_LEVEL: 12th-pass. ONLY recommend BACHELOR programs (B.Tech, "
                "B.Sc, B.Sc IT, BBA, BCA, BCom, BA, B.Pharm, B.Arch, B.Design, BPT, "
                "B.Sc Nursing, GNM, GME, DNS, ETO). NEVER recommend any Master "
                "(M.Sc, M.Tech, MBA, MCA, M.Pharm) to a 12th-pass caller — they need a "
                "Bachelor degree first. If caller asks about a Master, say "
                "'માસ્ટર્સ માટે પહેલા Bachelor પૂર્ણ કરવી પડશે' and suggest the matching "
                "Bachelor program instead. Diploma is also possible but Bachelor is "
                "the natural next step after 12th."
            )
        elif academic_level == "graduate":
            slot_lines.append(
                "ACADEMIC_LEVEL: Graduate / Bachelor-complete. ONLY recommend MASTER "
                "programs (M.Sc, M.Sc IT, M.Tech, MBA, MCA, M.Pharm) or PG Diploma. "
                "NEVER recommend a Bachelor program to a graduate — they've already "
                "done one. If caller asks about a Bachelor, gently confirm if they "
                "want a SECOND bachelor (unusual) — otherwise point them to the "
                "Master in their field of interest."
            )

        # ICT-CAMPUS ASK (2026-05-23). All B.Tech CSE variants live at
        # ICT institute = Ahmedabad campus. UVPCE Mehsana has separate
        # B.Tech (Computer) + B.Tech (IT) programs at lower fee. When the
        # focused program is at ICT Ahmedabad, the agent must
        # PROACTIVELY ASK which campus the caller prefers so they
        # understand the fee + location difference before committing.
        focused_inst = (focused_program or {}).get("institute") if focused_program else ""
        if focused_inst and "ICT Ahmedabad" in focused_inst:
            slot_lines.append(
                "ICT_CAMPUS_ASK: The focused program is at ICT Ahmedabad Campus. The "
                "caller may not know that Ganpat has BOTH a Mehsana main campus AND an "
                "Ahmedabad campus, with DIFFERENT fees for similar computer programs. "
                "In your next reply, ASK explicitly: 'આ B.Tech CSE Ahmedabad campus પર "
                "છે. Mehsana main campus પર B.Tech (Computer) અને B.Tech (IT) પણ છે — "
                "તમને કયો campus preference છે?' Do this BEFORE quoting fees or "
                "eligibility. ONE short clarifying line."
            )
        if ask_degree and ambiguous_topic:
            # Highest-priority directive: caller named a topic that has UG+PG
            # variants without a degree-level qualifier. Force a clarifying
            # question, ONE line, no fees, no details.
            slot_lines.append(
                f"DEGREE_CLARIFY_REQUIRED: caller mentioned '{ambiguous_topic}' "
                f"without specifying B.Sc/M.Sc/B.Tech/M.Tech. You MUST reply with "
                f"ONLY a single clarifying question listing the available "
                f"degree levels by name (NO fees, NO duration, NO counsellor). "
                f"Example reply: '{ambiguous_topic} માં B.Sc IT અને M.Sc IT બંને છે "
                f"— તમે કયું ઇચ્છો છો?'. DO NOT mention any other program. DO NOT "
                f"invent a 'B.Tech {ambiguous_topic}' — it does not exist."
            )
        elif focused_program:
            slot_lines.append(
                f"The active program in this conversation is {focused_program.get('program')}. "
                "For follow-up questions (counsellor, fees, eligibility, duration) answer about THIS program "
                "unless the caller explicitly names a different one."
            )
        # Single combined system message: slots + (optional) Layer-2 state
        # brief + (optional) Layer-3 signals. Merging avoids multiple
        # system messages on every LLM call (saves ~30-50 input tokens per
        # turn → ~150-300ms latency). Round 9: tags shortened (SLOTS /
        # STATE / SIGNALS / DIALOG / PROACTIVE) and renders made terse
        # (just the comma-separated flag list, system prompt has the rules).
        combined_parts: list[str] = []
        if slot_lines:
            combined_parts.append("SESSION SLOTS: " + " ".join(slot_lines))
        if conversation_state_brief:
            combined_parts.append("STATE: " + conversation_state_brief)
        if turn_signals_hint:
            combined_parts.append("SIGNALS: signals=" + turn_signals_hint)
        if turn_dialog_hint:
            combined_parts.append("DIALOG: dialog=" + turn_dialog_hint)
        if turn_proactive_hint:
            combined_parts.append("PROACTIVE: proactive=" + turn_proactive_hint)
        if combined_parts:
            messages = [
                {"role": "system", "content": " ".join(combined_parts)}
            ] + messages

        turn_start = time.perf_counter()
        full_response_parts: list[str] = []
        first_audio_at: float | None = None

        # Fact-validation whitelist built ONCE up front from the pinned
        # context. Every TTS-bound chunk passes through `validate_and_correct`
        # below — fees / phones / emails that don't appear in this
        # whitelist get auto-rewritten BEFORE Sarvam speaks them. Layer 1
        # of the human-brain roadmap (2026-05-20).
        from app.brain.fact_guard import (
            extract_facts_from_context,
            validate_and_correct,
        )

        context_facts = extract_facts_from_context(context or "")

        # Guardrail B: pick the canonical program for fact_guard's
        # substitution. When focused_program is set AND its record is
        # actually in the LLM's context, the fee whitelist collapses to
        # JUST that program's fee — sibling fees get dropped, so a
        # hallucinated sibling-fee value gets caught and corrected.
        #
        # When focused_program is None (true browse mode — family /
        # stream listing), canonical is None and fact_guard falls back
        # to legacy union-of-fees behavior — correct because the LLM is
        # listing variants and ANY variant's fee in the context is valid.
        #
        # When focused_program is set BUT its record is NOT in the
        # context (defensive: family-override branch took over and the
        # focused pin is stale from a prior turn), canonical is also
        # None to avoid a wrong forced-substitution.
        fact_canonical = None
        if focused_program:
            focused_name_lc = (focused_program.get("program") or "").strip().lower()
            ctx_names = {
                (p.get("program") or "").strip().lower()
                for p in context_facts.programs
            }
            if focused_name_lc in ctx_names:
                fact_canonical = focused_program

        # Vector-hygiene audit log. Single line per turn showing exactly
        # what the LLM is going to see + which fee is canonical. Makes
        # cross-program leaks visible at audit time.
        log.info(
            "context-audit programs=%s canonical_fee=%s focused=%r",
            [(p.get("program") or "?")[:60] for p in context_facts.programs] or "[]",
            (fact_canonical or {}).get("fees") if fact_canonical else None,
            (fact_canonical or {}).get("program") if fact_canonical else None,
        )

        async def llm_text_stream() -> AsyncIterator[str]:
            """Buffer LLM tokens until we can ship a valid TTS chunk.

            We flush as soon as the buffer has BOTH (1) at least one character
            in the target script (Sarvam rejects messages without one), AND
            (2) a natural break or enough chars. The first flush should be as
            small as possible to minimize TTFA.

            Before yielding to TTS, every chunk is passed through the fact
            guard so a hallucinated fee/phone/email gets replaced with the
            correct one from the pinned context.
            """
            from app.brain.text_normalize import normalize

            # Cross-chunk state for orphan-fee-tail stripping. When chunk N
            # ends with a JUST-SUBSTITUTED fee phrase, set this so chunk
            # N+1 strips any leading "<num-word> હજાર|લાખ" — that's the
            # tail of the original WRONG fee that escaped chunk N's regex.
            # Without this the caller hears "<correct fee> <orphan> હજાર છે".
            from app.brain.fact_guard import _GU_NUMBER_WORDS as _GU_NUM_WORDS
            stripped_state = {"strip_next_orphan_tail": False}
            _ORPHAN_TAIL_RE = re.compile(
                r"^[\s,.]*(?:" + "|".join(re.escape(w) for w in _GU_NUM_WORDS) +
                r"|\d+)\s+(?:લાખ|હજાર|lakh|hazaar|thousand)\.?[\s,]?"
            )

            def _guard(text: str) -> str:
                # If previous chunk ended with a corrected fee, the very
                # next chunk often starts with the LLM's continuation of
                # the original wrong fee ("પચાસ હજાર" trailing 495000 →
                # caller hears "4-lakh-95-thousand 50-thousand").  Strip
                # that orphan once if it's at the start of this chunk.
                if stripped_state["strip_next_orphan_tail"]:
                    new_text = _ORPHAN_TAIL_RE.sub("", text, count=1)
                    if new_text != text:
                        log.info(
                            "fact_guard orphan-tail stripped: %r -> %r",
                            text[:60], new_text[:60],
                        )
                        text = new_text
                    stripped_state["strip_next_orphan_tail"] = False
                if not context_facts.programs:
                    return text  # nothing to validate against
                corrected, corrections = validate_and_correct(
                    text, context_facts, canonical_program=fact_canonical,
                )
                # If a fee correction fired AND the chunk ENDS at/near the
                # corrected fee, the LLM's trailing number-words may arrive
                # in the next chunk as an orphan — arm the strip flag.
                if any(c.kind == "fee" for c in corrections):
                    # Did the corrected fee land near the end? If yes,
                    # whatever number-word arrives at the start of the next
                    # chunk is almost certainly the original-fee tail.
                    if corrected.rstrip(" ,.;:!?\n").endswith(
                        tuple(("લાખ", "હજાર", "lakh", "hazaar", "thousand"))
                    ):
                        stripped_state["strip_next_orphan_tail"] = True
                return corrected

            buf = ""
            first_sent = False
            # Temperature override: if the context has any program record
            # (fees, counsellor, eligibility — fact territory), drop to 0
            # so the model stays bolted to the provided numbers. The
            # default 0.3 was letting the model nudge fee figures to
            # "rounder" wrong numbers (94000 -> 180000 was a real bug).
            fact_grounded = "Program:" in (context or "") or "USER_ASKED_ABOUT" in (context or "")
            turn_temperature = 0.0 if fact_grounded else None
            async for token in self.llm.stream(
                messages,
                context=context,
                language_hint=language,
                temperature=turn_temperature,
            ):
                full_response_parts.append(token)
                buf += token
                stripped = buf.strip()
                if not _has_target_char(stripped, language):
                    if len(stripped) >= HARD_FLUSH_MAX:
                        yield _guard(normalize(stripped))
                        buf = ""
                        first_sent = True
                    continue
                # Has at least one valid-script char.
                min_size = FIRST_FLUSH_MIN if not first_sent else 12
                # Defer flush if buf ends mid-fee-phrase, so fact_guard sees
                # the WHOLE "X લાખ Y હજાર" as one span. HARD_FLUSH_MAX still
                # caps the wait to keep TTFA bounded.
                pending_fee = _ends_in_pending_fee(stripped)
                if (
                    BREAK_CHARS.search(token)
                    and len(stripped) >= min_size
                    and not pending_fee
                ) or len(stripped) >= HARD_FLUSH_MAX:
                    yield _guard(normalize(stripped))
                    buf = ""
                    first_sent = True
            tail = buf.strip()
            if tail and _has_target_char(tail, language):
                yield _guard(normalize(tail))

        # Use the pre-opened session if available (saves ~300ms vs fresh WS).
        if tts_session is not None:
            tts_iter = tts_session.synthesize(llm_text_stream())
        else:
            tts_iter = self.tts_stream.synthesize(llm_text_stream(), language=language)

        try:
            async for pcm_chunk in tts_iter:
                if not pcm_chunk:
                    continue
                now = time.perf_counter()
                is_first = first_audio_at is None
                if is_first:
                    first_audio_at = now
                yield TurnChunk(
                    audio=pcm_chunk,
                    is_first=is_first,
                    elapsed_ms=int((now - turn_start) * 1000),
                )
        finally:
            full_text = "".join(full_response_parts).strip()
            if full_text:
                latency_ms = int((time.perf_counter() - turn_start) * 1000)
                history.add_assistant(full_text, latency_ms=latency_ms)
                log.info("turn complete: %s (latency=%dms)", full_text[:120], latency_ms)
