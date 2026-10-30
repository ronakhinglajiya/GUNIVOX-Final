"""Fact-validation layer for LLM replies.

Layer 1 of the human-brain roadmap (2026-05-20). The LLM is bolted to a
pinned program record in context, but it occasionally hallucinates a
fee/phone/counsellor that doesn't appear in that context. Production
example: M.Sc IT Cyber Security was quoted at '1 લાખ 80 હજાર' when the
JSON value is 94000 — the model picked a sibling-variant's number out
of context and merged it onto the focused variant.

What this module does:

  1. `extract_facts_from_context(ctx)` parses the pinned program records
     and produces a whitelist of every (fee, phone, email, counsellor
     name, duration, eligibility-keyword) that's allowed to appear in
     the reply.

  2. `validate_and_correct(reply, ctx)` scans the LLM's reply for
     fact-shaped tokens (digit groups, phone-shaped strings, emails,
     Indian-rupee word-numbers like 'એક લાખ નેવુ હજાર'). For each one
     it asks: does this match SOME value in the whitelist? If yes →
     pass through. If no → it's a hallucination → swap with the
     correct value from the FIRST program in context, or strip it.

  3. Returns the corrected reply + a list of corrections so we can log
     audit trails.

The guard is conservative: it only TOUCHES tokens it can clearly
identify as fact-shaped and unsupported. Anything in the natural-
language wrap is left alone.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("gunivox.factguard")


# ─── Fact extraction from RAG context ───────────────────────────────────

@dataclass
class ContextFacts:
    """Whitelist of every fact that may appear in the LLM's reply."""
    programs: list[dict]              # the program dicts parsed out of context
    fees_digits: set[str]             # e.g. {"94000", "190000", "70000"}
    fees_lakh_words: set[str]         # e.g. {"એક લાખ નેવુ હજાર"}
    phones: set[str]                  # e.g. {"6351822641"}
    emails: set[str]                  # e.g. {"avp04@ganpatuniversity.ac.in"}
    counsellors: set[str]             # last-names + full names, lowercased
    durations: set[str]               # e.g. {"4 year", "2 year"}


# Patterns for parsing program records out of a context block. The
# context comes from format_program_context(p) in catalog.py:
#   "Program: B.Tech (Marine). Institute: ... Fees: 495000 INR. ...
#    Counsellor: Prof. Ashil Patel, Phone: 6351822641,
#    Email: avp04@ganpatuniversity.ac.in."
_FEES_RE      = re.compile(r"Fees:\s*([\d,]+)\s*INR", re.IGNORECASE)
_PHONE_RE     = re.compile(r"Phone:\s*([\d+\- ]{7,})", re.IGNORECASE)
_EMAIL_RE     = re.compile(r"Email:\s*([\w.+\-]+@[\w.\-]+)", re.IGNORECASE)
_COUNS_RE     = re.compile(r"Counsellor:\s*([^,]+?)(?=,|\.|$)", re.IGNORECASE)
_DURATION_RE  = re.compile(r"Duration:\s*([\w \-./]+?)(?=\.|$)", re.IGNORECASE)
# Program-name extractor. The catalog's format_program_context emits the
# program-line as 'Program: B. Tech. (Marine) . Institute: ...' — with a
# literal ' . ' separator. The previous regex `(?=\.|$)` stopped at the
# FIRST period, which truncated names like 'B. Tech.' to just 'B'. That
# silently broke Guardrail B's canonical-lock check (program-name
# comparison) for every program with a period in its name. 2026-05-21.
# New regex stops at any of (Institute|Fees|Duration|Counsellor|Phone|
# Email) — covers both the real production context shape and any
# alternative orderings. Periods INSIDE the program name are preserved.
_PROGRAM_RE   = re.compile(
    r"Program:\s*(.+?)\s*"
    r"(?=\.\s*(?:Institute|Fees|Duration|Counsellor|Phone|Email):|"
    r"\n|(?:Institute|Fees|Duration|Counsellor|Phone|Email):|$)",
    re.IGNORECASE,
)

# Number-word translator: maps spoken Gujarati fee word-strings to digits
# so we can compare 'એક લાખ નેવુ હજાર' against '190000'. Covers ALL the
# numeric tokens that appear in our admissions catalog's fee values plus
# the common hallucination edge cases the LLM emits (અંસઠ=68, અઠયોતેર=78,
# એક્સઠ=61 etc). If a future fee number appears that needs a new
# Gujarati word, add it here; otherwise the parse falls through and the
# guard treats the phrase as "needs correction" anyway.
_GU_NUMBER_WORDS: dict[str, int] = {
    # 1-9
    "એક": 1, "બે": 2, "ત્રણ": 3, "ચાર": 4, "પાંચ": 5,
    "છ": 6, "સાત": 7, "આઠ": 8, "નવ": 9,
    # 10-19
    "દસ": 10, "અગિયાર": 11, "બાર": 12, "તેર": 13, "ચૌદ": 14,
    "પંદર": 15, "સોળ": 16, "સત્તર": 17, "અઢાર": 18, "ઓગણીસ": 19,
    # 20-29
    "વીસ": 20, "એકવીસ": 21, "બાવીસ": 22, "ત્રેવીસ": 23, "ચોવીસ": 24,
    "પચ્ચીસ": 25, "છવ્વીસ": 26, "સત્તાવીસ": 27, "અઠ્યાવીસ": 28, "ઓગણત્રીસ": 29,
    # 30-39
    "ત્રીસ": 30, "એકત્રીસ": 31, "બત્રીસ": 32, "તેત્રીસ": 33, "ચોત્રીસ": 34,
    "પાંત્રીસ": 35, "છત્રીસ": 36, "સાડત્રીસ": 37, "આડત્રીસ": 38, "ઓગણચાલીસ": 39,
    # 40-49
    "ચાલીસ": 40, "એકતાળીસ": 41, "બેતાળીસ": 42, "ત્રેતાળીસ": 43, "ચુંમાળીસ": 44,
    "પીસ્તાળીસ": 45, "છેતાળીસ": 46, "સુડતાળીસ": 47, "અડતાળીસ": 48, "ઓગણપચાસ": 49,
    # 50-59
    "પચાસ": 50, "એકાવન": 51, "બાવન": 52, "ત્રેપન": 53, "ચોપન": 54,
    "પંચાવન": 55, "છપ્પન": 56, "સત્તાવન": 57, "અઠ્ઠાવન": 58, "ઓગણસાઠ": 59,
    # 60-69
    "સાઠ": 60, "એકસઠ": 61, "બાસઠ": 62, "ત્રેસઠ": 63, "ચોસઠ": 64,
    "પાંસઠ": 65, "છાસઠ": 66, "સડસઠ": 67, "અડસઠ": 68, "અંસઠ": 68,
    "ઓગણસિત્તેર": 69,
    # 70-79
    "સિત્તેર": 70, "એકોતેર": 71, "બોતેર": 72, "તોતેર": 73, "ચુમ્મોતેર": 74,
    "પંચોતેર": 75, "છોતેર": 76, "સિત્યોતેર": 77, "ઇઠ્યોતેર": 78, "અઠ્યોતેર": 78,
    "ઓગણાસી": 79,
    # 80-89
    "એંસી": 80, "એક્યાસી": 81, "બ્યાસી": 82, "ત્યાસી": 83, "ચોર્યાસી": 84,
    "પંચાસી": 85, "છ્યાસી": 86, "સત્યાસી": 87, "અઠ્યાસી": 88, "નેવ્યાસી": 89,
    # 90-99
    "નેવુ": 90, "નેવું": 90, "એકાણું": 91, "બાણું": 92, "ત્રાણું": 93,
    "ચોરાણું": 94, "પંચાણું": 95, "છન્નું": 96, "સતાણું": 97, "અઠ્ઠાણું": 98,
    "નવ્વાણું": 99,
}


def _gu_words_to_digits(s: str) -> str | None:
    """Best-effort: convert a Gujarati number-words string to canonical
    digits. 'એક લાખ નેવુ હજાર' → '190000'. Returns None if it can't.
    Conservative — only handles patterns we've actually seen in TTS-
    bound LLM output for fees in the 10000-2000000 range.
    """
    words = s.lower().split()
    if not words:
        return None
    total = 0
    pending = 0  # multiplier-pending number
    for w in words:
        if w in _GU_NUMBER_WORDS:
            pending = (pending or 1) * _GU_NUMBER_WORDS[w] if pending and pending < 10 else _GU_NUMBER_WORDS[w]
            # combine pending if it's already a tens value (e.g. 'નેવુ' = 90, next 'હજાર' multiplies)
        elif w in ("લાખ", "lakh", "lac"):
            total += (pending or 1) * 100_000
            pending = 0
        elif w in ("હજાર", "hazaar", "thousand"):
            total += (pending or 1) * 1_000
            pending = 0
        else:
            # unknown word, abort — we don't want false positives
            return None
    total += pending
    return str(total) if total > 0 else None


def extract_facts_from_context(context: str) -> ContextFacts:
    """Parse the pinned RAG context into a whitelist of known facts."""
    facts = ContextFacts(
        programs=[], fees_digits=set(), fees_lakh_words=set(),
        phones=set(), emails=set(), counsellors=set(), durations=set(),
    )
    if not context:
        return facts

    # Split on "Program:" so each program record is a chunk.
    chunks = re.split(r"(?=Program:)", context)
    for chunk in chunks:
        if not chunk.strip():
            continue
        record: dict[str, str] = {}
        if m := _PROGRAM_RE.search(chunk):
            record["program"] = m.group(1).strip()
        if m := _FEES_RE.search(chunk):
            digits = m.group(1).replace(",", "").strip()
            if digits.isdigit():
                facts.fees_digits.add(digits)
                record["fees"] = digits
        if m := _PHONE_RE.search(chunk):
            phone = re.sub(r"\D", "", m.group(1))
            if phone:
                facts.phones.add(phone)
                record["phone"] = phone
        if m := _EMAIL_RE.search(chunk):
            email = m.group(1).strip().lower()
            facts.emails.add(email)
            record["email"] = email
        if m := _COUNS_RE.search(chunk):
            name = m.group(1).strip().lower()
            facts.counsellors.add(name)
            # also add last-token (commonly the surname people use)
            last = name.split()[-1] if name else ""
            if last and len(last) > 2:
                facts.counsellors.add(last)
            record["counsellor"] = name
        if m := _DURATION_RE.search(chunk):
            facts.durations.add(m.group(1).strip().lower())
            record["duration"] = m.group(1).strip()
        if record:
            facts.programs.append(record)
    return facts


# ─── Reply validation ──────────────────────────────────────────────────

# Match any digit-group that could be a fee or phone in the reply.
# Phones tend to be 10 digits in a row; fees 4-7 digits. We extract any
# 4+ digit run and classify by length.
_DIGIT_RUN_RE = re.compile(r"\d{4,}")

# Match Gujarati phone-as-words isn't a real pattern in our LLM output
# (TTS pronounces digits one-by-one). So we only need to validate
# digit-runs.

# Match the lakh/hazaar word-numbers the LLM emits for fees.
# e.g. 'એક લાખ નેવુ હજાર', '4 લાખ 95 હજાર', '94 હજાર', 'ચોરાણું હજાર',
# AND 'એક લાખ અંસઠ હજાર' (one-lakh-sixty-eight-thousand — the 168000
# hallucination that broke production 2026-05-20).
#
# ATOMIC MATCH RULE: the regex must consume the WHOLE fee phrase as one
# match span — anchored on a known number-token, walking through up to
# 3 intervening tokens (which can be ANY Gujarati word so we don't break
# on words we haven't yet added to _GU_NUMBER_WORDS), ending on લાખ or
# હજાર, with optional 2nd unit-segment. Previously the regex matched
# 'એક લાખ' and 'હજાર' as SEPARATE spans which the str.replace() pass
# then double-substituted, causing 'ચોરાણું હજાર ... ચોરાણું હજાર' in
# production audio. validate_and_correct now uses re.sub() so each
# match is replaced atomically — no double-pass.
_NUMBER_TOKEN_ALT = "|".join(
    [r"\d+"] + [re.escape(w) for w in _GU_NUMBER_WORDS]
)
_GU_FEE_WORDS_RE = re.compile(
    # Required leading number token (digit OR known Gujarati number word)
    rf"(?:{_NUMBER_TOKEN_ALT})"
    # Optional intervening Gujarati words / digits — non-greedy, max 3
    r"(?:\s+\S+){0,3}?\s+"
    # Required unit
    r"(?:લાખ|હજાર)"
    # Optional second segment: lakhs followed by thousands (or vice versa)
    r"(?:(?:\s+\S+){1,4}?\s+(?:લાખ|હજાર))?"
)

# Match an email shape
_EMAIL_REPLY_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.\w+")

# Phone-capture context markers (Round 8 Fix C). When ANY of these appears
# within ~60 chars of a 4-7 digit run, treat the digits as part of a phone
# number the user is dictating, NOT a fee. Prevents the "7990" → "84000"
# substitution that mangled a caller's phone capture (2026-05-21).
_PHONE_CAPTURE_MARKERS: tuple[str, ...] = (
    "નંબર", "ફોન", "phone", "phone number", "મોબાઇલ", "mobile",
    "WhatsApp", "whatsapp", "વોટ્સએપ",
    "મોકલ", "send", "share", "આપો",
    "contact",
)


@dataclass
class Correction:
    kind: str
    original: str
    corrected: str | None
    reason: str


def _digit_value_for_fee(fee_str: str) -> str | None:
    """Convert a fee phrase (e.g. '4 લાખ 95 હજાર', 'એક લાખ અંસઠ હજાર')
    to its digit value as a string. Returns None if any token in the
    phrase isn't a digit, a known Gujarati number word, or a unit
    (લાખ/હજાર). That None signals the guard to treat the phrase as a
    hallucination and substitute the canonical RAG value.
    """
    parts = fee_str.lower().strip().split()
    if not parts:
        return None
    total = 0
    multiplier_buffer = 0
    saw_unit = False
    for p in parts:
        if p.isdigit():
            multiplier_buffer = int(p)
        elif p in _GU_NUMBER_WORDS:
            multiplier_buffer = _GU_NUMBER_WORDS[p]
        elif p in ("લાખ", "lakh", "lac"):
            total += (multiplier_buffer or 1) * 100_000
            multiplier_buffer = 0
            saw_unit = True
        elif p in ("હજાર", "hazaar", "hazar", "thousand"):
            total += (multiplier_buffer or 1) * 1_000
            multiplier_buffer = 0
            saw_unit = True
        else:
            # Unknown token in the middle. Return None so guard treats
            # it as hallucination — preferable to a "best guess" that
            # ships a wrong number to the caller.
            return None
    total += multiplier_buffer
    if not saw_unit:
        # No લાખ / હજાર seen at all — this isn't really a fee phrase.
        return None
    return str(total) if total > 0 else None


def _digits_to_gu_words(digits: str) -> str:
    """Render a fee digit-value as Gujarati spoken words.

    Covers every fee number that appears in admission_brain.json:
      • Single-unit thousands: 61000, 65000, 70000, 75000, 80000, 84000,
        87000, 90000, 94000, 99000 …
      • Lakh + thousands: 100000, 110000, 130000, 160000, 180000, 190000,
        225000, 250000, 380000, 495000 …

    Falls back to raw digits (`'94000'`) only if we genuinely don't know
    how to spell a number — in which case Sarvam will read digit-by-digit
    but the VALUE is still right. Per user instruction (2026-05-20),
    correctness of the number ALWAYS beats prettiness of the pronunciation.
    """
    inv = {v: k for k, v in _GU_NUMBER_WORDS.items() if isinstance(v, int)}
    try:
        n = int(digits)
    except ValueError:
        return digits
    if n < 100:
        return inv.get(n, str(n))
    if n < 1_000:
        # 100-999 (hundreds) — uncommon in fees, fall back to digits
        return str(n)
    if n < 100_000:
        thousands, remainder = divmod(n, 1_000)
        if remainder == 0 and thousands in inv:
            return f"{inv[thousands]} હજાર"
        # Composite like 99500 — fall back to safe digit form (the value
        # is right, that's what matters; Sarvam reads it digit-by-digit).
        return str(n)
    if n < 10_000_000:
        lakhs, rest = divmod(n, 100_000)
        thousands, _ = divmod(rest, 1_000)
        lakh_word = inv.get(lakhs, str(lakhs))
        out = f"{lakh_word} લાખ"
        if thousands:
            t_word = inv.get(thousands, str(thousands))
            out += f" {t_word} હજાર"
        return out
    return str(n)


def validate_and_correct(
    reply: str,
    facts: ContextFacts,
    canonical_program: dict | None = None,
) -> tuple[str, list[Correction]]:
    """Validate every fact-shaped token in `reply` against the whitelist.

    Each fact category (fees, phones, emails) is processed with a SINGLE
    re.sub() pass + a callback that decides per-match whether to keep or
    replace. This is the load-bearing change vs the original — the old
    code used sequential str.replace() which would re-match the same
    substring inside an already-substituted output, producing duplicated
    fee phrases like 'ચોરાણું હજાર ... ચોરાણું હજાર'. re.sub() walks the
    INPUT linearly and never re-scans output, so atomic.

    Args:
        reply: LLM-produced text chunk on its way to TTS.
        facts: parsed whitelist from the LLM's reference context.
        canonical_program: when set, the SINGLE program whose facts this
            reply is supposed to be about. Guardrail B (2026-05-21): the
            fees-whitelist collapses to ONLY this program's fee, so a
            hallucinated sibling-fee value gets caught and corrected.
            When None (true browse mode — family / stream listing), the
            old union-of-all-fees whitelist is used.

    Returns (corrected_reply, corrections_made).
    """
    corrections: list[Correction] = []

    # SESSION-LEVEL fallback canonical (used when the chunk doesn't name a
    # specific program). Chunk-local resolution (below) overrides this
    # whenever the LLM names a program in the same chunk as the fee/email.
    #
    # 2026-05-21 demo-call bug: the OLD elif fell back to `facts.programs[0]`
    # — "whatever RAG returned first". When RAG retrieved an UNRELATED
    # program (e.g. BPT for a B.Tech Mechanical query — embedding similarity
    # on 'Bachelor'), fact_guard treated BPT's 87000 as canonical and
    # "corrected" the LLM's hallucinated mechanical fee to BPT's number,
    # broadcasting a wildly wrong fee for a program the caller never asked
    # about. Now: NO automatic fallback. If the engine didn't pass an
    # explicit canonical, the fee handler below removes any non-whitelisted
    # fee phrase instead of substituting blindly.
    if canonical_program is not None and canonical_program.get("fees"):
        session_canonical_digits = str(canonical_program["fees"]).strip()
        session_canonical_record = canonical_program
    else:
        session_canonical_digits = None
        session_canonical_record = None

    def _local_canonical(window: str) -> dict | None:
        """Find the program named IN this text window. Longest-name wins
        so 'M. Tech Mechanical Engineering (Additive Manufacturing)' is
        preferred over a bare 'M. Tech' family hit. Returns the catalog
        record (dict) or None if no program name is mentioned in the
        window.

        Used for chunk-local canonical resolution (Round 8 2026-05-21 fix):
        when the LLM chunk says 'M.Tech Additive ... ફી 110250' the canonical
        for that chunk must be M.Tech Additive's fee, not the session's
        stale Diploma (Mechanical) focused program. Without this, fact_guard
        substitutes the LLM's CORRECT M.Tech fee with the wrong Diploma fee.

        Matching is DOT-INSENSITIVE because the catalog has 'M. Tech.'
        with an inner period but Sarvam-bound TTS output strips many
        periods (text_normalize converts 'M.Tech' → 'M Tech' / 'એમટેક' but
        the LLM also emits a no-dot form). Without normalisation the
        substring check fails.
        """
        if not window:
            return None
        wl_norm = _norm_for_name_match(window)
        best: dict | None = None
        best_len = 0
        # Pass 1: look in the RAG-retrieved programs (fast + most common).
        for p in facts.programs:
            nm = (p.get("program") or "").strip()
            if len(nm) < 6:
                continue
            nm_norm = _norm_for_name_match(nm)
            if nm_norm and nm_norm in wl_norm and len(nm_norm) > best_len:
                best = p
                best_len = len(nm_norm)
        if best is not None:
            return best
        # Pass 2 (2026-05-22): fall back to the FULL catalog. LLM history-
        # bleed produces replies that name a program which wasn't in this
        # turn's RAG context (e.g. "MBA Innovation માટે ફી છે..." when RAG
        # retrieved Data Science). Without this fallback, fact_guard
        # broadcasts the focused-program's fee under the hallucinated
        # program's name — wildly wrong info. Looking up the named program
        # in the catalog lets us anchor to its actual fee.
        try:
            from app.brain.catalog import get_catalog
            cat = get_catalog()
            for p in cat.programs:
                nm = (p.get("program") or "").strip()
                if len(nm) < 6:
                    continue
                nm_norm = _norm_for_name_match(nm)
                if nm_norm and nm_norm in wl_norm and len(nm_norm) > best_len:
                    best = p
                    best_len = len(nm_norm)
        except Exception:
            pass
        return best

    def _resolve_canonical(m: re.Match) -> tuple[str | None, set[str], dict | None]:
        """Return (canonical_digits, effective_fees, canonical_record) for
        the match `m`. Tries chunk-local first (window around the match),
        falls back to session canonical."""
        start = max(0, m.start() - 120)
        end = min(len(m.string), m.end() + 120)
        window = m.string[start:end]
        local = _local_canonical(window)
        if local is not None and local.get("fees"):
            d = str(local["fees"]).strip()
            return d, {d}, local
        if session_canonical_digits is not None:
            return session_canonical_digits, {session_canonical_digits}, session_canonical_record
        return None, facts.fees_digits, None

    # ── Fees ──────────────────────────────────────────────────────────
    def _fee_handler(m: re.Match) -> str:
        phrase = m.group(0).strip()
        # Tiny single-token phrases ('હજાર' alone, 'લાખ' alone) — skip,
        # they're not fee statements. Real fees always start with a count.
        tokens = phrase.split()
        if len(tokens) < 2:
            return m.group(0)

        digit_val = _digit_value_for_fee(phrase)
        # Chunk-local canonical resolution (Round 8). Whitelist is
        # whichever program is NAMED in the same chunk as the fee. Falls
        # back to session canonical if no program is named in the window.
        local_canonical_digits, local_effective_fees, _local_rec = _resolve_canonical(m)

        # Whitelist hit → leave untouched.
        if digit_val is not None and digit_val in local_effective_fees:
            return m.group(0)

        # No canonical to substitute against → LEAVE THE ORIGINAL PHRASE
        # untouched. We tried two stricter behaviours and both caused worse
        # user-facing damage than the unverified number itself:
        #   1) Substituting facts.programs[0]'s fee = broadcast a WRONG
        #      sibling's fee whenever RAG returned an unrelated program
        #      (BPT-for-Mechanical bug, 2026-05-21).
        #   2) Stripping the phrase + injecting a safety string = gibberish
        #      when the LLM streamed the fee across chunks ("ચાર લાખ" in
        #      chunk-N, "પંચાણું હજાર" in chunk-N+1 — both got separately
        #      replaced with the safety phrase, producing nonsense like
        #      "fee exact-counsellor-please exact-counsellor-please છે").
        # Log it as a fact_guard miss so we can analyse later, but ship the
        # LLM's own (uncorrected) text. The facts.programs[0] fallback
        # remains REMOVED upstream so we still never broadcast a sibling
        # fee — we just don't try to fabricate a replacement.
        if local_canonical_digits is None:
            log.warning(
                "fact_guard unanchored fee %r (parsed=%s) left as-is — "
                "no canonical context to validate against",
                phrase, digit_val,
            )
            return m.group(0)

        correct_words = _digits_to_gu_words(local_canonical_digits)
        corrections.append(Correction(
            kind="fee",
            original=phrase,
            corrected=correct_words,
            reason=(
                f"parsed={digit_val} not in whitelist {local_effective_fees}"
                if digit_val is not None
                else f"unparseable phrase, falling back to canonical {local_canonical_digits}"
            ),
        ))
        return correct_words

    corrected = _GU_FEE_WORDS_RE.sub(_fee_handler, reply)

    # ── Digit runs (phones + naked-digit fees) ────────────────────────
    def _digits_handler(m: re.Match) -> str:
        digits = m.group(0)
        if len(digits) >= 9:
            # Phone-shaped. Must match a context phone.
            if digits in facts.phones:
                return digits
            if facts.phones:
                replacement = next(iter(facts.phones))
                corrections.append(Correction(
                    kind="phone",
                    original=digits,
                    corrected=replacement,
                    reason=f"phone {digits} not in {facts.phones}",
                ))
                return replacement
            return digits  # nothing to correct against
        # 4-7 digit run = naked fee number (LLM occasionally emits this).
        # PHONE-CAPTURE GUARD (Round 8 Fix C): when the surrounding text
        # is asking for / repeating a phone number, a 4-7 digit run is
        # almost certainly the user's phone digits dictated incrementally,
        # NOT a fee. Don't substitute. Without this, "તમારો નંબર 7990 છે"
        # gets 7990 swapped for some random program's 84000.
        start = max(0, m.start() - 60)
        end = min(len(m.string), m.end() + 30)
        nearby = m.string[start:end]
        nearby_lower = nearby.lower()
        if any(
            kw in nearby_lower or kw in nearby for kw in _PHONE_CAPTURE_MARKERS
        ):
            return digits
        # YEAR / CALENDAR GUARD (2026-05-21): a 4-digit run in 2020-2030
        # with a calendar word nearby ("વર્ષ", "year", "March", "Jan",
        # "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
        # "Nov", "Dec", "session", "બેચ", "intake") is a year, NOT a fee.
        if 4 == len(digits) and digits.isdigit() and 2020 <= int(digits) <= 2035:
            year_markers = (
                "વર્ષ", "year", "session", "બેચ", "intake", "admission",
                "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december",
                "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
                "oct", "nov", "dec",
            )
            if any(kw in nearby_lower for kw in year_markers):
                return digits
        # Otherwise: 4-7 digit naked-fee path with chunk-local canonical.
        local_canonical_digits, local_effective_fees, _local_rec = _resolve_canonical(m)
        if 4 <= len(digits) <= 7 and digits not in local_effective_fees:
            if local_canonical_digits is not None:
                corrections.append(Correction(
                    kind="fee_digits",
                    original=digits,
                    corrected=local_canonical_digits,
                    reason=f"digit-fee {digits} not in {local_effective_fees}",
                ))
                return local_canonical_digits
        return digits

    corrected = _DIGIT_RUN_RE.sub(_digits_handler, corrected)

    # ── Emails ────────────────────────────────────────────────────────
    def _email_handler(m: re.Match) -> str:
        email = m.group(0).lower()
        if email in facts.emails:
            return m.group(0)
        # Chunk-local resolution for emails too (Round 8 Fix B): use the
        # email of whichever program is named in the surrounding text.
        # Without this, "M.Tech Additive ... ip.chaudhari@..." with stale
        # session canonical Diploma Mech swapped the email to Diploma's
        # counsellor (jignesh.m.patel@) — wrong person.
        _, _, local_rec = _resolve_canonical(m)
        if local_rec is not None and local_rec.get("email"):
            replacement = str(local_rec["email"]).strip().lower()
            if replacement != email:
                corrections.append(Correction(
                    kind="email",
                    original=email,
                    corrected=replacement,
                    reason=f"email {email} -> chunk-local canonical {replacement}",
                ))
                return replacement
            return m.group(0)
        # Fallback to legacy first-in-facts.emails behavior
        if facts.emails:
            replacement = next(iter(facts.emails))
            corrections.append(Correction(
                kind="email",
                original=email,
                corrected=replacement,
                reason=f"email {email} not in {facts.emails}",
            ))
            return replacement
        return m.group(0)

    corrected = _EMAIL_REPLY_RE.sub(_email_handler, corrected)

    # ── Hallucinated-program check (Round 8 Fix D) ────────────────────
    # Scan the reply for program-shaped name mentions and verify each
    # appears in the LLM's reference context. Log warnings when the LLM
    # invents a name like 'B.Tech CSE (Cyber Security)' (which doesn't
    # exist in our catalog). Log-only for now — useful audit trail
    # without risking false-positive rewrites of legitimate phrasing.
    _check_for_hallucinated_programs(corrected, facts)

    # ── No-info interceptor (Round 10 — 2026-05-21) ────────────────────
    # When the LLM emits 'મારી પાસે માહિતી નથી' / 'no info' BUT the context
    # actually contains program records, that's a false refusal — the LLM
    # gave up instead of asking a clarifying question. Rewrite to a
    # clarification prompt. Without this, callers heard "મારી પાસે માહિતી
    # નથી, બીજો કોઈ પ્રોગ્રામ જણાવશો?" on incomplete utterances like
    # "તમારી" (mid-sentence continuation) — production bug 2026-05-21.
    if facts.programs:
        corrected, intercepted = _intercept_no_info_refusal(corrected)
        if intercepted:
            log.warning(
                "FACT GUARD intercepted false no-info refusal (context had %d programs)",
                len(facts.programs),
            )

    if corrections:
        log.warning(
            "FACT GUARD corrected %d item(s) in reply: %s",
            len(corrections),
            [(c.kind, c.original, c.corrected) for c in corrections],
        )

    return corrected, corrections


# Pattern: a degree marker + (optional intermediate words) + a parenthesized
# specialisation. Catches phrases like 'B.Tech CSE (Cyber Security)',
# 'M. Sc. IT (Data Science)', 'Diploma (Civil)', 'B. Sc. Hons. (Biotechnology)'.
_PROGRAM_NAME_CANDIDATE_RE = re.compile(
    r"\b("
    r"(?:B\.?\s*Tech|M\.?\s*Tech|B\.?\s*Sc|M\.?\s*Sc|MBA|BBA|BCA|MCA|"
    r"Diploma|B\.?\s*Arch|M\.?\s*Arch|B\.?\s*Pharm|M\.?\s*Pharm|"
    r"B\.?\s*Com|M\.?\s*Com|B\.?\s*Ed|M\.?\s*Ed|MBBS|GME|DNS|GNM|"
    r"Graduate Marine Engineering"
    r")"
    r"(?:[\s.A-Za-z]{0,40}?)"     # optional intermediate ("CSE", "IT (Hons.)" etc.)
    r"\(\s*([A-Za-z][A-Za-z &.\-/0-9]{2,60})\s*\)"
    r")",
    re.IGNORECASE,
)


# No-info refusal patterns the LLM uses when it gives up. Each fires only
# when the context HAS programs (proving the refusal is false). Order:
# longer / more specific first so the replacement consumes the whole
# refusal clause cleanly. (Round 10 — 2026-05-21)
_NO_INFO_REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Full pattern: "[એ/આ] [પ્રોગ્રામ] વિશે મારી પાસે માહિતી નથી[, બીજો કોઈ પ્રોગ્રામ જણાવશો?]"
    re.compile(
        r"(?:આ|એ)\s*(?:પ્રોગ્રામ\s+)?વિશે\s*"
        r"મારી\s*પાસે\s*(?:કોઈ\s*)?(?:માહિતી|રેકોર્ડ|વિગત)\s*નથી"
        r"[,\s]*(?:બીજો\s*કોઈ\s*પ્રોગ્રામ\s*જણાવશો\??)?\.?",
    ),
    # Shorter: "મારી પાસે માહિતી નથી[, ...]"
    re.compile(
        r"મારી\s*પાસે\s*(?:કોઈ\s*)?(?:માહિતી|રેકોર્ડ|વિગત)\s*નથી"
        r"[,\s]*(?:બીજો\s*કોઈ\s*પ્રોગ્રામ\s*જણાવશો\??)?\.?",
    ),
    # English fallback
    re.compile(
        r"I\s+don'?t\s+have\s+(?:any\s+)?(?:information|info|record|details)\s+(?:about|on)\s+(?:this|that)?",
        re.IGNORECASE,
    ),
)

# Replacement: a clarification prompt that fits where the refusal stood.
_NO_INFO_REPLACEMENT = "વધુ details આપશો, હું મદદ કરીશ?"


def _intercept_no_info_refusal(text: str) -> tuple[str, bool]:
    """If `text` contains a 'no info' refusal phrase, replace it with a
    clarification prompt. Caller must verify context HAS programs before
    invoking (otherwise the refusal might be legitimate).

    Returns (new_text, intercepted_flag)."""
    new_text = text
    intercepted = False
    for pat in _NO_INFO_REFUSAL_PATTERNS:
        if pat.search(new_text):
            new_text = pat.sub(_NO_INFO_REPLACEMENT, new_text, count=1)
            intercepted = True
            break
    return new_text, intercepted


def _norm_for_name_match(s: str) -> str:
    """Strip dots, collapse whitespace, lowercase. Used to fuzzy-compare
    catalog program names against LLM-emitted names that may differ in
    period placement: catalog 'M. Tech.' vs LLM 'M. Tech' (no inner dot)
    vs TTS-normalised 'M Tech'. All collapse to 'm tech' here."""
    if not s:
        return ""
    out = s.replace(".", " ").lower()
    out = " ".join(out.split())
    return out


def _check_for_hallucinated_programs(reply: str, facts: ContextFacts) -> None:
    """Scan `reply` for program-name mentions. For each, log a warning if
    the exact name doesn't appear in `facts.programs` (the LLM's reference
    context). This catches cases like the LLM composing 'B.Tech CSE (Cyber
    Security)' from B.Tech CSE variants + a Cyber Security context —
    that program doesn't exist in our catalog (2026-05-21 prod bug).

    Log-only. Doesn't rewrite the reply (too risky for false-positives on
    legitimate but creative phrasings). Engine logs let us see the failure
    rate, and we can promote to active correction in a follow-up round.
    """
    if not reply or not facts.programs:
        return

    # Catalog program names from THIS turn's context, dot-normalised
    # so 'M. Tech.' matches LLM-emitted 'M Tech' / 'M. Tech'.
    ctx_names_norm = {
        _norm_for_name_match(p.get("program") or "")
        for p in facts.programs
        if p.get("program")
    }
    ctx_names_norm.discard("")
    if not ctx_names_norm:
        return

    for m in _PROGRAM_NAME_CANDIDATE_RE.finditer(reply):
        candidate = m.group(1).strip()
        cand_norm = _norm_for_name_match(candidate)
        # Substring match in either direction (catalog form ⊆ candidate,
        # or candidate ⊆ catalog form) — covers shortened/expanded phrasings.
        if any(nm in cand_norm or cand_norm in nm for nm in ctx_names_norm):
            continue
        # Possibly hallucinated.
        log.warning(
            "FACT GUARD possible hallucinated program in reply: %r "
            "(catalog context: %s)",
            candidate, sorted(ctx_names_norm),
        )
