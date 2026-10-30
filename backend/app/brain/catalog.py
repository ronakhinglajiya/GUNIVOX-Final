"""Program catalog: the single source of truth for the 126 admissions programs.

This module loads admission_brain.json once at startup, exposes:

  * `find_by_name(text)` – returns the best-matching program dict for a substring
    of a program name appearing inside `text`. Used to detect which program the
    assistant just talked about (so we can lock the conversation's focus
    slot) and to extract program mentions from a transcript.

  * `find_all_mentions(text)` – every distinct program named in `text`. Used
    by the lead-temperature classifier and the post-call interest tracker.

Why a separate module instead of querying FAISS: for our flow we need
*exact* string matches of program codes ("B.Tech CSE", "M.Sc. IT (Data
Science)"), not embedding-similar. Embedding-based retrieval matches by
*topic* and is fine for "what programs do you have for engineering" but
wrong for "did the AI just say B.Tech Marine in its last reply?"
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("gunivox.catalog")


# Aliases that callers / the LLM use for programs whose canonical name uses
# a different form. Maps an alias substring to a canonical program-name
# substring that DOES appear in the JSON. Keep alphanumeric — punctuation
# is stripped before matching. All keys lowercased.
_PROGRAM_ALIASES: dict[str, str] = {
    # B.Tech
    "b.tech marine engineering": "b. tech. (marine)",
    "btech marine": "b. tech. (marine)",
    "b tech marine": "b. tech. (marine)",
    "computer engineering": "b.tech cse",
    "computer science": "b.tech cse",
    # B.Sc IT specialisations
    "bsc data science": "b. sc. it  (hons.) (data science)",
    "bsc it data science": "b. sc. it  (hons.) (data science)",
    "b.sc it honours data science": "b. sc. it  (hons.) (data science)",
    "msc data science": "m. sc. it (data science)",
    "msc it data science": "m. sc. it (data science)",
    # MBA — alias the common informal forms
    "mba finance": "mba (financial services)",
    "mba financial services": "mba (financial services)",
    "mba logistics": "mba (logistics and supply chain management)",
    "mba supply chain": "mba (logistics and supply chain management)",
    "mba marketing": "mba (marketing/finance/hr/international business/entrepreneurship/supply chain management)",
    "mba hr": "mba (marketing/finance/hr/international business/entrepreneurship/supply chain management)",
    "mba healthcare": "mba (healthcare and hospital management)",
    "mba hospital management": "mba (healthcare and hospital management)",
    "mba international business": "mba (international business)",
    "mba business analytics": "mba (business analytics)",
    "mba agribusiness": "mba (agribusiness)",
    # BBA
    "bba general": "bba (hons.) (general)",
    "bba finance": "bba (hons.) (finance )",
    "bba marketing": "bba (hons.) (marketing management)",
    "bba marketing management": "bba (hons.) (marketing management)",
    "bba international business": "bba (hons.) (international business)",
    "bba business analytics": "bba (hons.) (business analytics)",
    "bba fintech": "bba (hons) (fintech, ai and blockchain management) ",
    "bba blockchain": "bba (hons) (fintech, ai and blockchain management) ",
    "bba logistics": "bba (logistics) - with logistic skill council",
    # BCA
    "bca ai ml": "bca (honours) (computer applications) (ai & ml)",
    "bca ai and ml": "bca (honours) (computer applications) (ai & ml)",
    "bca cyber security": "bca (honours) (computer applications) (cyber secuirity)",
    "bca cybersecurity": "bca (honours) (computer applications) (cyber secuirity)",
    "bca computer applications": "bca (honours) (computer applications)",
    # M.Sc IT specialisations
    "msc it cyber security": "m. sc. it (cyber security)",
    "msc cyber security": "m. sc. it (cyber security)",
    "msc it ai ml": "m. sc. it (ai & ml)",
    "msc ai ml": "m. sc. it (ai & ml)",
    "msc artificial intelligence": "m.sc. it (artificial intelligence and machine learning)",
    "msc machine learning": "m.sc. it (artificial intelligence and machine learning)",
    # B.Sc IT specialisations
    "bsc it cyber security": "b.sc. it  (hons.) (cyber security)",
    "bsc cyber security": "b.sc. it  (hons.) (cyber security)",
    "bsc it ai ml": "b. sc. it  (hons.) (ai & ml)",
    "bsc ai ml": "b. sc. it  (hons.) (ai & ml)",
    # Nursing
    "gnm": "general nursing midwifery (gnm)",
    "general nursing midwifery": "general nursing midwifery (gnm)",
    "nursing": "b. sc. (nursing)",
    "bsc nursing": "b. sc. (nursing)",
    # Marine
    "graduate marine engineering": "graduate marine engineering (gme)",
    "gme": "graduate marine engineering (gme)",
    "dns": "dns (diploma in nautical science)",
    "nautical science": "b. sc. (nautical science)",
    "eto marine": "eto (marine)",
    "eto": "eto (marine)",
    "electro technical officer": "eto (marine)",
    "gp ratings": "g. p. ratings",
    "g.p. ratings": "g. p. ratings",
    # Architecture / Design
    "architecture": "b. arch. (architecture)",
    "barch": "b. arch. (architecture)",
    "interior design": "b. design (interior design)",
    "product design": "b. design (product design)",
    # Pharmacy
    "bpharm": "b. pharm. (pharmacy)",
    "b.pharm": "b. pharm. (pharmacy)",
    "bachelor of pharmacy": "b. pharm. (pharmacy)",
    "pharmacy": "b. pharm. (pharmacy)",
    "mpharm pharmacology": "m. pharm. (pharmacology)",
    "mpharm pharmaceutics": "m. pharm. (pharmaceutics)",
    "mpharm quality assurance": "m. pharm. (pharmaceutical quality assurance)",
    "mpharm regulatory": "m. pharm. (regulatory affairs)",
    "m pharm": "m. pharm. (pharmacy)",
    # Marine — common bare-word forms surfaced by STT
    "marine engineering": "b. tech. (marine)",
    # M.Tech bare-spec forms — target the real catalog names
    "m tech civil": "m. tech. civil (construction engg. & management)",
    "m tech mechanical": "m. tech. mechanical engineering (advanced manufacturing systems)",
    "m tech computer": "m. tech. (computer)",
    "m tech electrical": "m. tech. ee (electrical power system / renewable energy)",
    "mtech civil": "m. tech. civil (construction engg. & management)",
    "mtech mechanical": "m. tech. mechanical engineering (advanced manufacturing systems)",
    "mtech computer": "m. tech. (computer)",
    "m.tech civil": "m. tech. civil (construction engg. & management)",
    "m.tech mechanical": "m. tech. mechanical engineering (advanced manufacturing systems)",
    # Standalone degree codes — common caller utterances
    "mca": "master of computer applications (mca)",
    "bpt": "bachelor of physiotherapy (bpt)",
    "physiotherapy": "bachelor of physiotherapy (bpt)",
    "bachelor of physiotherapy": "bachelor of physiotherapy (bpt)",
    "b.com": "b. com. (hons.) (general)",
    "bcom": "b. com. (hons.) (general)",
    "b com": "b. com. (hons.) (general)",
    "ba": "ba (honours)  psychology / economics",
    "ba honours": "ba (honours)  psychology / economics",
    "b.a": "ba (honours)  psychology / economics",
    "bsc agriculture": "b.sc.(hons) agriculture",
    "b sc agriculture": "b.sc.(hons) agriculture",
    "b.sc agriculture": "b.sc.(hons) agriculture",
    # Space-prefix variants for B.Sc IT specialisations (user types with space after B)
    "b sc it ai ml": "b. sc. it  (hons.) (ai & ml)",
    "b sc it data science": "b. sc. it  (hons.) (data science)",
    "b sc it cyber security": "b.sc. it  (hons.) (cyber security)",
    "b sc it cyber": "b.sc. it  (hons.) (cyber security)",
    # B.Tech CSE Cyber Security disambiguation
    "btech cse cyber": "b.tech cse (cyber security)",
    "b.tech cse cyber security": "b.tech cse (cyber security)",
    "b tech cse cyber security": "b.tech cse (cyber security)",
}


# Gujarati alias overrides — processed via the Gujarati alias path
# (which preserves Devanagari/Gujarati script during normalisation).
# Each maps a compact-Gujarati alias to a target program name substring.
# Resolves at catalog __init__ to specific program records.
_GU_MANUAL_ALIASES: dict[str, str] = {
    "એમબીએફાઇનાન્સ": "mba (financial services)",
    "એમબીએફાઇનાન્શિયલ": "mba (financial services)",
    "એમબીએમાર્કેટિંગ": "mba (marketing/finance/hr/international business/entrepreneurship/supply chain management)",
    "એમબીએલોજિસ્ટિક્સ": "mba (logistics and supply chain management)",
    "એમબીએઇન્ટરનેશનલબિઝનેસ": "mba (international business)",
    "એમબીએહેલ્થકેર": "mba (healthcare and hospital management)",
    "એમબીએએચઆર": "mba (marketing/finance/hr/international business/entrepreneurship/supply chain management)",
    "બીબીએફાઇનાન્સ": "bba (hons.) (finance )",
    "બીબીએમાર્કેટિંગ": "bba (hons.) (marketing management)",
    "બીસીએસાયબરસિક્યુરિટી": "bca (honours) (computer applications) (cyber secuirity)",
    "બીસીએએઆઈએમએલ": "bca (honours) (computer applications) (ai & ml)",
    "આર્કિટેક્ચર": "b. arch. (architecture)",
    "બીઆર્ક": "b. arch. (architecture)",
    "ફાર્મસી": "b. pharm. (pharmacy)",
    "બીફાર્મ": "b. pharm. (pharmacy)",
    "બેચલરઓફફાર્મસી": "b. pharm. (pharmacy)",
    "નર્સિંગ": "b. sc. (nursing)",
    "બીએસસીનર્સિંગ": "b. sc. (nursing)",
    "જીએનએમ": "general nursing midwifery (gnm)",
    "બીટેકસીએસઈ": "b.tech cse",
    "બીટેકકોમ્પ્યુટર": "b.tech cse",
    "કોમ્પ્યુટરસાયન્સ": "b.tech cse",
    "બીટેકસિવિલ": "b. tech. (civil)",
    "બીટેકમેકેનિકલ": "b. tech. (mechanical)",
    "બીટેકઇલેક્ટ્રિકલ": "b. tech. (electrical)",
    "બીટેકઓટોમોબાઇલ": "b. tech. (automobile)",
    # Common STT-spaced + typo variants surfaced 2026-05-21
    "ઓટોમોબીલ": "diploma (automobile)",  # bare-typo form, prefer diploma (most common entry)
    "ઓટોમોબાઇલ": "diploma (automobile)",
    "મરીનએન્જિનિયરિંગ": "b. tech. (marine)",
    "એમસીએ": "master of computer applications (mca)",
    "જીપીરેટિંગ્સ": "g. p. ratings",
}


def _normalise(s: str) -> str:
    """Lowercase, drop everything that isn't [a-z0-9 ], COLLAPSE whitespace.

    The whitespace-collapse step is load-bearing: 'B. Tech. (Marine)' had
    its periods + paren replaced with spaces, leaving 'b  tech  marine '
    (double-spaces). 'B. Tech Marine' (no inner period) normalised to
    'b  tech marine' (single space). Without the collapse those don't
    substring-match. Bug surfaced in production logs."""
    out = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    out = re.sub(r"\s+", " ", out).strip()
    return out


# Gujarati Unicode range. Used by `_gu_normalise` so we can match queries
# in Gujarati script (which `_normalise` strips to spaces).
_GU_RANGE = "઀-૿"


def _gu_normalise(s: str) -> str:
    """Lowercase, drop everything that isn't [a-z0-9 Gujarati], collapse
    whitespace. **Preserves word boundaries** — needed for token-level
    overlap scoring. Substring callers that need 'જી એન એમ' to match
    'જીએનએમ' should `.replace(' ', '')` on both sides at compare time
    (we did that in `find_family_query`/`find_by_name`)."""
    out = re.sub(rf"[^a-z0-9{_GU_RANGE}\s]+", " ", s.lower())
    out = re.sub(r"\s+", " ", out).strip()
    return out


# Regex that pulls the Gujarati transliteration around 'એટલે કે'.
# Some rows put the Gujarati AFTER the marker (B.Tech style):
#     'B. Tech. (Marine) એટલે કે બીટેક મરીન MARINE…'
# Others put the Gujarati BEFORE (M.Sc style):
#     'એમએસસી આઈટી (ડેટા સાયન્સ) એટલે કે M. Sc. IT (Data Science)…'
# We capture both directions — without _GU_ALIAS_PAT_PRE the M.Sc rows
# ended up with ZERO aliases indexed, which made 'એમએસસી આઈટી ડેટા
# સાયન્સ' fall through to the B.Sc variant on disambiguation
# (2026-05-20 production bug).
_GU_ALIAS_PAT = re.compile(
    rf"એટલે કે\s+([{_GU_RANGE}\s\(\)]+?)(?=[A-Za-z\.,\n]|$)"
)
_GU_ALIAS_PAT_PRE = re.compile(
    rf"(?:^|[\.,\n])\s*([{_GU_RANGE}\s\(\)]+?)\s+એટલે કે"
)


def _gu_alias_captures(gu_text: str) -> list[str]:
    """Return every Gujarati alias capture from `gu_text` — both
    after-marker and before-marker forms. Used by the alias index
    builder and `_score_variant`."""
    if not gu_text:
        return []
    out: list[str] = []
    for m in _GU_ALIAS_PAT.finditer(gu_text):
        out.append(m.group(1))
    for m in _GU_ALIAS_PAT_PRE.finditer(gu_text):
        out.append(m.group(1))
    return out


def _gu_alias_candidates(capture: str) -> list[str]:
    """Generate match aliases from a 'એટલે કે' capture.

    A capture like 'એમએસસી આઈટી ડેટા સાયન્સ' has the specialisation at the
    end ('ડેટા સાયન્સ'), the broader code at the start ('એમએસસી આઈટી').
    Callers may say either piece in isolation — 'મારે ડેટા સાયન્સ વિશે'.
    So we emit:
      • first word (the compact code: 'એમએસસી', 'જીએનએમ', 'બીટેક')
      • LAST word (the specialisation: 'ઓટોમોબાઇલ', 'સાયન્સ', 'સિક્યુરિટી') —
        caller often drops the degree prefix and says just the specialisation
        ("ઓટોમોબાઇલ વિશે"). Without this candidate the catalog matcher
        returned None and the LLM fell back to 'no info' (2026-05-21 prod bug).
      • all CONSECUTIVE 2-word windows  ('એમએસસી આઈટી', 'આઈટી ડેટા', 'ડેટા સાયન્સ')
      • all consecutive 3-word windows for slightly more specific phrasing
      • the full capture
    Each is normalised; we keep only ones ≥3 normalised chars and dedupe."""
    parts = [w for w in capture.split() if w.strip()]
    if not parts:
        return []
    raw: list[str] = []
    raw.append(parts[0])
    if len(parts) >= 2:
        raw.append(parts[-1])
    for i in range(len(parts) - 1):
        raw.append(" ".join(parts[i : i + 2]))
    for i in range(len(parts) - 2):
        raw.append(" ".join(parts[i : i + 3]))
    raw.append(" ".join(parts))  # full phrase
    seen: set[str] = set()
    final: list[str] = []
    for a in raw:
        norm = _gu_normalise(a)
        if len(norm) >= 3 and norm not in seen:
            seen.add(norm)
            final.append(norm)
    return final


# Academic-background markers. When the caller's utterance is describing
# WHAT THEY STUDIED in 12th (PCM, PCB, Commerce, Arts, A-group, B-group,
# subject names) — and ISN'T also naming a specific Ganpat program — we
# must NOT match a random program by accidental token overlap (e.g.
# "બી ગ્રુપ" + "સાયન્સ" was matching B.Sc Nautical). The engine should
# ask which program/area they want instead.
_STREAM_BACKGROUND_MARKERS: tuple[str, ...] = (
    # English (lowercased substring checks)
    "pcm", "pcb", "p.c.m", "p.c.b",
    "a group", "b group", "a-group", "b-group", "a grp", "b grp",
    "science background", "commerce background", "arts background",
    "12th science", "12th commerce", "12th arts",
    "10+2 science", "10 + 2 science", "10+2 commerce",
    "physics chemistry maths", "physics chemistry biology",
    "physics chemistry math",
    "phy chem maths", "phy chem bio", "phy chem math",
    "completed science", "did science in 12th",
    "after 12th", "after twelfth",
    # Gujarati / Hindi
    "પીસીએમ", "પીસીબી",
    "એ ગ્રુપ", "બી ગ્રુપ", "એ ગ્રુપમાંથી", "બી ગ્રુપમાંથી",
    "ધોરણ ૧૦", "ધોરણ ૧૨", "ધોરણ 10", "ધોરણ 12",
    "બારમા સાયન્સ", "બારમાં સાયન્સ", "બારમું સાયન્સ",
    "ટ્વેલ્થ સાયન્સ", "ટ્વેલ્થમાં સાયન્સ",
    "ફિઝિક્સ કેમેસ્ટ્રી મેથ્સ", "ફિઝિક્સ કેમેસ્ટ્રી બાયોલોજી",
    "ફિઝિક્સ કેમેસ્ટ્રી મેથ",
    # Catch generic "12 માં X હતું" / "ધોરણ 12 X" forms too
    "12 માં સાયન્સ", "12 મા સાયન્સ", "12 ની સાયન્સ",
    "ધોરણ 12 માં સાયન્સ", "ધોરણ ૧૨ માં સાયન્સ",
    "ધોરણ બાર સાયન્સ", "બારમું સાયન્સ", "12 સાયન્સ હતું",
    "સાયન્સ હતું", "સાયન્સ હતું ધોરણ",
    "12 માં કોમર્સ", "ધોરણ 12 માં કોમર્સ", "બારમું કોમર્સ",
    "12 માં આર્ટ્સ", "બારમું આર્ટ્સ",
    # More Commerce/Arts caller variations (Ketan-call patterns)
    "12 કોમર્સ", "બાર કોમર્સ", "બારમા કોમર્સ", "બારમાં કોમર્સ",
    "12th commerce", "12 commerce", "commerce student", "commerce માં",
    "કોમર્સ સ્ટુડન્ટ", "કોમર્સ માં",
    "12 આર્ટ્સ", "બાર આર્ટ્સ", "12th arts", "arts student",
    "આર્ટ્સ સ્ટુડન્ટ", "આર્ટ્સ માં",
    # "મેં X કર્યું" — caller-narrates background without "12th" prefix
    # (Ronak call 2026-05-25: "મેં કોમર્સ કર્યું છે" didn't fire any
    # background slot because none of these existed)
    "કોમર્સ કર્યું", "કોમર્સ પૂર્ણ", "કોમર્સ પાસ", "કોમર્સ કરેલું",
    "સાયન્સ કર્યું", "સાયન્સ પૂર્ણ", "સાયન્સ પાસ", "સાયન્સ કરેલું",
    "આર્ટ્સ કર્યું", "આર્ટ્સ પૂર્ણ", "આર્ટ્સ પાસ", "આર્ટ્સ કરેલું",
    "commerce કર્યું", "commerce કરેલું",
    "science કર્યું", "science કરેલું",
    "arts કર્યું", "arts કરેલું",
    "did commerce", "completed commerce", "passed commerce",
    "did science", "completed science", "passed science",
    "did arts", "completed arts", "passed arts",
)

# Tokens that, when present, mean the caller IS naming a specific program
# even if a background marker also appears (e.g. "I did PCM, want B.Tech
# CSE" — the B.Tech CSE part wins). Used as the override-allow list.
_EXPLICIT_PROGRAM_TOKENS: tuple[str, ...] = (
    "b.tech", "btech", "b. tech", "બીટેક",
    "m.tech", "mtech", "m. tech", "એમટેક",
    "b.sc", "bsc ", "b. sc",  # require trailing space/dot so "બી ગ્રુપ" doesn't trip
    "m.sc", "msc ", "m. sc",
    "mba", "bba", "bca", "mca", "gnm", "gme", "dns", "eto",
    "b.pharm", "m.pharm", "b.com", "bcom", "b.arch", "barch", "b.design",
    "bpt", "physiotherapy", "diploma",
    "એમબીએ", "બીબીએ", "બીસીએ", "એમસીએ",
    "બીફાર્મ", "એમફાર્મ", "બીઆર્ક", "બીકોમ",
    "ડિપ્લોમા", "જીએનએમ", "જીએમઈ",
    "marine", "મરીન", "nautical", "નોટિકલ",
    "cyber", "સાયબર", "data science", "ડેટા સાયન્સ",
    "automobile", "ઓટોમોબાઇલ",
)


# Pure family/degree-code tokens. If the caller's utterance is ONLY
# these (possibly repeated or with filler words like "વિશે"/"કહો"), the
# intent is to browse a FAMILY, not pin a specific program. Demo bug
# 2026-05-21: "બીટેક બીટેક" → B.Tech (Automobile); "MBA" → BA Psych;
# "બીસીએ" → BCA Hons; "એમબીએ" → MBA Agribusiness. All wrong — the
# caller said the family code only.
_BARE_FAMILY_TOKENS: tuple[str, ...] = (
    # Only families with ≥3 variants are listed — single-variant codes
    # (MCA, BCom, BA, BArch, BPharm) should resolve directly to their
    # one program when the caller says them alone.
    # English
    "btech", "b.tech", "b tech",
    "mtech", "m.tech", "m tech",
    "bsc", "b.sc", "b sc",
    "msc", "m.sc", "m sc",
    "bba", "b.b.a",
    "mba", "m.b.a",
    "bca", "b.c.a",
    "mpharm", "m.pharm",
    "diploma",
    # Gujarati
    "બીટેક", "એમટેક", "બીએસસી", "એમએસસી",
    "બીબીએ", "એમબીએ", "બીસીએ",
    "એમફાર્મ",
    "ડિપ્લોમા",
)

# Filler tokens we strip before checking for bare family. Repeated
# family token ("btech btech") collapses to one. Common Gujarati filler
# phrases the caller might add around a bare family token.
_BARE_FAMILY_FILLER: tuple[str, ...] = (
    "વિશે", "વિષે", "કહો", "જણાવો", "બતાવો", "જાણવું", "જાણવું છે",
    "મારે", "મને", "please", "kindly", "tell", "me", "about",
    "જી", "હા", "ji", "haa", "yes", "ok", "okay",
)


def _looks_like_bare_family(text: str) -> bool:
    """True if the text is ONLY a family token (possibly repeated, with
    filler words). Caller said e.g. "બીટેક" or "MBA" without naming a
    specialisation — they want the variant list, not a pin."""
    if not text:
        return False
    low = text.lower().strip()
    # Strip common filler tokens
    cleaned = low
    for f in _BARE_FAMILY_FILLER:
        cleaned = cleaned.replace(f, " ")
    # Remove punctuation
    for ch in ".,!?;:":
        cleaned = cleaned.replace(ch, " ")
    tokens = [t for t in cleaned.split() if t]
    if not tokens or len(tokens) > 4:
        return False
    # Every remaining token must be a known family marker
    return all(t in _BARE_FAMILY_TOKENS for t in tokens)


def _looks_like_stream_background(text: str) -> bool:
    """Return True if the text is describing the caller's 12th-grade
    academic background WITHOUT naming a specific Ganpat program. Used
    by find_by_name as an early-out guard."""
    if not text:
        return False
    low = text.lower()
    has_marker = any(m in low for m in _STREAM_BACKGROUND_MARKERS)
    if not has_marker:
        return False
    # If text ALSO names an explicit program, let the normal path resolve
    # that program — background marker is just context, not the headline.
    has_program = any(t in low for t in _EXPLICIT_PROGRAM_TOKENS)
    return not has_program


class ProgramCatalog:
    """Loaded once; immutable. Use the module-level `get_catalog()` accessor."""

    def __init__(self, programs: list[dict]) -> None:
        self.programs = programs
        # Pre-compute normalised names + a sorted-by-length list so longer
        # matches win (so "B.Tech CSE (CS)" beats bare "B.Tech CSE").
        self._by_norm: list[tuple[str, dict]] = []
        for p in programs:
            name = (p.get("program") or "").strip()
            if not name:
                continue
            self._by_norm.append((_normalise(name), p))
        # Longest first so specific variants win over generic substrings.
        self._by_norm.sort(key=lambda x: len(x[0]), reverse=True)
        # English alias index pointing to a real program.
        self._alias_index: list[tuple[str, dict]] = []
        for alias, target in _PROGRAM_ALIASES.items():
            target_norm = _normalise(target)
            hit = next((p for norm, p in self._by_norm if target_norm in norm), None)
            if hit:
                self._alias_index.append((_normalise(alias), hit))
        self._alias_index.sort(key=lambda x: len(x[0]), reverse=True)
        # Gujarati alias index: stores COMPACT (no-space) form so
        # 'જી એન એમ' input matches 'જીએનએમ' alias. _gu_normalise preserves
        # word spaces for tokenisation; this index strips them for substring
        # match.
        self._gu_alias_index: list[tuple[str, dict]] = []
        for p in programs:
            gu_text = p.get("text_field_gu") or ""
            for capture in _gu_alias_captures(gu_text):
                for alias_norm in _gu_alias_candidates(capture):
                    compact = alias_norm.replace(" ", "")
                    if len(compact) >= 3:
                        self._gu_alias_index.append((compact, p))

        # Round 13 (2026-05-21): manual Gujarati alias overrides for
        # informal forms that callers use but don't appear in the JSON's
        # text_field_gu. Resolves "એમબીએ ફાઇનાન્સ" → MBA (Financial Services)
        # instead of the wrong-match-by-token-order default.
        for gu_alias, target_substr in _GU_MANUAL_ALIASES.items():
            target_norm = _normalise(target_substr)
            hit = next(
                (p for norm, p in self._by_norm if target_norm in norm),
                None,
            )
            if hit:
                compact = _gu_normalise(gu_alias).replace(" ", "")
                if len(compact) >= 3:
                    self._gu_alias_index.append((compact, hit))

        self._gu_alias_index.sort(key=lambda x: len(x[0]), reverse=True)

        # Family + stream taxonomies. Each program is tagged once at load
        # time so `find_family_variants` / `find_stream_programs` are O(n).
        self._family: dict[str, list[dict]] = {}
        self._stream: dict[str, list[dict]] = {}
        # Topic taxonomy (Guardrail C). A program may belong to BOTH a
        # family (degree code) AND a topic — e.g. B.Tech (Marine) is in
        # family='btech' AND topic='marine'. `find_family_query` checks
        # topic FIRST, so caller saying "marine વિશે" gets all marine
        # programs back, not just the 3 tagged under family='marine'.
        self._topic: dict[str, list[dict]] = {}
        for p in programs:
            name = (p.get("program") or "").strip()
            if not name:
                continue
            fam, strm = _tag_program(name)
            p["_family"] = fam
            p["_stream"] = strm
            self._family.setdefault(fam, []).append(p)
            self._stream.setdefault(strm, []).append(p)
            topic = _topic_for_program(name)
            if topic:
                p["_topic"] = topic
                self._topic.setdefault(topic, []).append(p)

        log.info(
            "ProgramCatalog loaded: %d programs, %d en-aliases, %d gu-aliases, %d families, %d streams",
            len(self._by_norm), len(self._alias_index), len(self._gu_alias_index),
            len(self._family), len(self._stream),
        )

    def find_by_name(self, text: str) -> dict | None:
        """Return the program dict whose canonical name or alias appears as
        a substring of `text`.

        STREAM-MARKER GUARD (2026-05-21 demo bug): when the text describes
        the caller's ACADEMIC BACKGROUND ("12th science B-group", "PCM",
        "PCB", "physics chemistry maths") and doesn't explicitly name a
        program, return None. Without this, "મેં ટ્વેલ્થમાં સાયન્સ કરેલું
        છે બી ગ્રુપમાંથી" was falsely matching B.Sc. Nautical Science
        because "બી"+"સાયન્સ" overlapped the B.Sc Gujarati alias. The
        engine should then ask which program/stream the caller is
        interested in, not silently pin a random B.Sc variant.

        English pass uses longest-match-wins on the canonical name list,
        then the English alias list — same as before.

        Gujarati pass is DISCRIMINATING: collects ALL aliases that
        substring-match the user's compact text, then picks the program
        whose full Gujarati name has the highest TOKEN OVERLAP with the
        user's utterance. Fixes the 2026-05-20 bug where 'બીએસસી આઈટી
        ડેટા સાયન્સ' matched the Cyber Security variant (both share the
        'બીએસસીઆઈટી' prefix, but length-tied first-wins gave the wrong
        sibling — now ડેટા/સાયન્સ token overlap pulls Data Science ahead).
        """
        if not text:
            return None
        # Stream-marker guard: caller is describing their academic background,
        # not naming a program. Return None so the engine asks instead of
        # silently picking a wrong program.
        if _looks_like_stream_background(text):
            return None
        # Family-only guard (2026-05-21 demo bug): "બીટેક", "બીસીએ", "MBA",
        # "એમબીએ" alone (no specialisation suffix) was falsely matching a
        # specific variant (Automobile / BCA / BA Psych / Agribusiness) via
        # the Gujarati alias index. The caller said the FAMILY, not a
        # specific program — return None so `find_family_query` handles
        # it and the agent asks "which variant?".
        if _looks_like_bare_family(text):
            return None
        en_norm = _normalise(text)
        for prog_norm, prog in self._by_norm:
            if prog_norm in en_norm:
                return prog
        for alias_norm, prog in self._alias_index:
            # Short aliases (≤4 chars like "ba", "mca") need word-boundary
            # matching — otherwise "BA" substring-matches inside "BBA" /
            # "MBA" and we pin to BA Honours on the agent's own "B.Tech,
            # BBA, MBA" line (2026-05-22 prod bug).
            if len(alias_norm) <= 4:
                # _normalise collapses dots+symbols to spaces, so word
                # boundaries are simply " alias " or start/end of string.
                padded = " " + en_norm + " "
                if (" " + alias_norm + " ") in padded:
                    return prog
                continue
            if alias_norm in en_norm:
                return prog
        gu_compact = _gu_normalise(text).replace(" ", "")
        if not gu_compact:
            return None
        # Collect every program whose alias substring-matches, scored by
        # discriminator-token overlap. Use id() to dedupe programs (each
        # program has many aliases).
        user_tokens = [t for t in _gu_normalise(text).split() if len(t) >= 3]
        candidates: dict[int, tuple[int, int, dict]] = {}  # id(prog) -> (overlap, max_alias_len, prog)
        for alias_norm, prog in self._gu_alias_index:
            if alias_norm not in gu_compact:
                continue
            pid = id(prog)
            prog_compact = _gu_normalise(prog.get("text_field_gu") or "").replace(" ", "")
            overlap = sum(1 for tok in user_tokens if tok in prog_compact)
            existing = candidates.get(pid)
            if existing is None or (overlap, len(alias_norm)) > (existing[0], existing[1]):
                candidates[pid] = (overlap, len(alias_norm), prog)
        if not candidates:
            return None
        # Highest token-overlap wins; tie-break on alias length.
        best = max(candidates.values(), key=lambda c: (c[0], c[1]))
        return best[2]

    def _detect_family(self, text: str) -> str | None:
        """Return the family key whose alias matches the text (longest wins)."""
        if not text:
            return None
        q_compact = _normalise_q(text).replace(" ", "")
        en_q = _normalise(text)
        candidates: list[tuple[int, str]] = []
        for fam_key, aliases in _FAMILY_ALIASES.items():
            for alias in aliases:
                norm_alias = _gu_normalise(alias).replace(" ", "")
                en_alias = _normalise(alias)
                hit_gu = norm_alias and norm_alias in q_compact
                hit_en = en_alias and en_alias in en_q
                if hit_gu or hit_en:
                    candidates.append((max(len(norm_alias), len(en_alias)), fam_key))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _score_variant(self, text: str, variant: dict, family_tokens: set[str]) -> int:
        """Count how many DISCRIMINATING tokens of `text` appear in
        `variant`'s normalised name + gu transliteration. Family-name tokens
        (e.g. 'bca' when discriminating BCA variants) are excluded since
        every variant has them. Returns the overlap count."""
        en_tokens = set(_normalise(text).split()) - family_tokens
        gu_tokens = set(_gu_normalise(text).split()) - family_tokens
        v_en = set(_normalise(variant.get("program") or "").split())
        v_gu_alias = " ".join(_gu_alias_captures(variant.get("text_field_gu") or ""))
        v_gu = set(_gu_normalise(v_gu_alias).split())
        return len((en_tokens & v_en) | (gu_tokens & v_gu))

    def find_variant_in_family(self, text: str, family_key: str) -> dict | None:
        """Pick the variant within `family_key` whose tokens best overlap
        with the user's text. Returns the unique winner if there is one,
        or None if multiple distinct programs tie (caller falls back to
        family-subset listing). Same-name duplicates count as one program."""
        variants = self._family.get(family_key, [])
        if not variants:
            return None
        family_tokens: set[str] = set()
        for alias in _FAMILY_ALIASES.get(family_key, []):
            family_tokens.update(_normalise(alias).split())
            family_tokens.update(_gu_normalise(alias).split())
        family_tokens.discard("")
        scored = [(self._score_variant(text, v, family_tokens), v) for v in variants]
        scored.sort(key=lambda x: x[0], reverse=True)
        top_score = scored[0][0]
        if top_score < 1:
            return None
        # Collect everything tied at top_score; if all share the same
        # program name (JSON duplicates), it IS a single unique variant.
        top: list[dict] = []
        names: set[str] = set()
        for score, v in scored:
            if score < top_score:
                break
            top.append(v)
            names.add((v.get("program") or "").strip())
        if len(names) == 1:
            return top[0]
        return None

    def _detect_topic(self, text: str) -> str | None:
        """Find which topic key the user's text matches (Guardrail C).
        Topic detection happens BEFORE family detection in find_family_query
        so cross-degree topics like 'marine' return all topic variants
        instead of being limited to one degree-code family. Longest alias
        wins on ambiguity (matches the _detect_family pattern)."""
        if not text:
            return None
        q_compact = _gu_normalise(text).replace(" ", "")
        en_q = _normalise(text)
        candidates: list[tuple[int, str]] = []
        for topic_key, aliases in _TOPIC_ALIASES.items():
            for alias in aliases:
                norm_alias = _gu_normalise(alias).replace(" ", "")
                en_alias = _normalise(alias)
                hit_gu = norm_alias and norm_alias in q_compact
                hit_en = en_alias and en_alias in en_q
                if hit_gu or hit_en:
                    candidates.append((max(len(norm_alias), len(en_alias)), topic_key))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def find_family_query(self, text: str) -> tuple[str, list[dict]] | None:
        """Detect 'tell me about [family]' / 'tell me about [topic]' queries.

        Returns one of:
          - (topic_key, ALL topic variants) — user named a TOPIC with ≥2
            programs across degree levels ('marine વિશે' → all 6+ marine
            programs across B.Tech / B.Sc / DNS / GME / ETO / G.P.R).
            Topic check runs FIRST so cross-school topics aren't missed.
          - (family_key, ALL variants) — user named the family with no
            discriminating signal ('BCA વિશે કહો' → all 3 BCA records).
          - (family_key, MATCHING subset) — user gave a partial qualifier
            that narrows to >1 variants ('M.Tech Mechanical' → both
            Mechanical M.Tech specialisations).
          - None — user named ONE specific variant; defer to
            `find_variant_in_family` / `find_by_name`.
        """
        # 1) Topic check first (Guardrail C). Cross-school / cross-degree
        #    cases like marine + automobile + biotechnology resolve here.
        topic_key = self._detect_topic(text)
        if topic_key:
            topic_progs = self._topic.get(topic_key, [])
            if len(topic_progs) >= 2:
                # Dedupe by program name and return as a family-shaped tuple.
                # Same-name duplicates exist in the JSON (e.g. two B.Sc IT
                # Cyber Security records with different counsellors) — we
                # don't want the LLM listing the same program twice.
                seen: set[str] = set()
                unique: list[dict] = []
                for p in topic_progs:
                    nm = (p.get("program") or "").strip()
                    if nm and nm not in seen:
                        seen.add(nm)
                        unique.append(p)
                if len(unique) >= 2:
                    return topic_key, unique

        # 2) Fall back to existing degree-code family detection.
        fam_key = self._detect_family(text)
        if not fam_key:
            return None
        variants = self._family.get(fam_key, [])
        if len(variants) <= 1:
            return None

        family_tokens: set[str] = set()
        for alias in _FAMILY_ALIASES.get(fam_key, []):
            family_tokens.update(_normalise(alias).split())
            family_tokens.update(_gu_normalise(alias).split())
        family_tokens.discard("")
        scored = [(self._score_variant(text, v, family_tokens), v) for v in variants]
        scored.sort(key=lambda x: x[0], reverse=True)
        top_score = scored[0][0]
        if top_score == 0:
            # No discriminating signal — full family list.
            return fam_key, variants
        # Subset = all variants tied at the top score, deduped by program
        # name (the JSON has same-name duplicates for some Cyber Security
        # records — we don't want to list "Cyber Security" twice).
        subset: list[dict] = []
        seen_names: set[str] = set()
        for score, v in scored:
            if score < top_score:
                break
            name = (v.get("program") or "").strip()
            if name and name not in seen_names:
                seen_names.add(name)
                subset.append(v)
        # If the subset narrows to exactly 1 unique program, that's a
        # specific variant — defer to single-program logic.
        if len(subset) <= 1:
            return None
        return fam_key, subset

    def find_stream_query(self, text: str) -> tuple[str, list[dict]] | None:
        """Detect stream-overview queries: 'Commerce માટે કયા courses?',
        'Science A વાળા માટે?'. Returns (stream_key, programs) or None.

        Different from `find_family_query` — streams are higher-level
        (engineering, management, nursing…) and span MULTIPLE families.
        """
        if not text:
            return None
        q = _normalise_q(text)
        en_q = _normalise(text)
        candidates: list[tuple[int, str]] = []
        for stream_key, aliases in _STREAM_ALIASES.items():
            for alias in aliases:
                norm_alias = _gu_normalise(alias).replace(" ", "")
                en_alias = _normalise(alias)
                if (norm_alias and norm_alias in q.replace(" ", "")) or (en_alias and en_alias in en_q):
                    candidates.append((len(norm_alias) + len(en_alias), stream_key))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        stream_key = candidates[0][1]
        progs = self._stream.get(stream_key, [])
        return (stream_key, progs) if progs else None

    def find_campus_query(self, text: str) -> tuple[str, list[dict]] | None:
        """Detect campus-mention queries — caller asks about a specific
        campus location ('અમદાવાદ', 'Ahmedabad', 'city campus'). Returns
        (campus_key, programs) or None.

        Currently supports the Ahmedabad campus (CMSR + DCS-CITY
        institutes). The Mehsana main campus is the default and doesn't
        need an override — every other program is there.
        """
        if not text:
            return None
        low = text.lower()
        ahmedabad_markers = (
            # English
            "ahmedabad", "amdavad", "city campus", "city",
            # Gujarati
            "અમદાવાદ", "અમદાવાદમાં", "અમદાવાદનો", "અમદાવાદની",
            "સિટી કેમ્પસ", "સિટી", "અમદાવાદ કેમ્પસ",
            # 2026-06-07: these institute codes are EXCLUSIVELY at the
            # Ahmedabad campus, so a caller naming any of them should
            # be routed to the Ahmedabad fee context.
            #   ICT  → ICT Ahmedabad Campus
            #   CMSR → CMSR Ahmedabad Campus
            #   DCS-CITY / DCS CITY → DCS Ahmedabad Campus
            # (Plain "DCS" intentionally excluded — ambiguous between
            # DCS Ahmedabad Campus and DCS- Main Campus in Mehsana.)
            "ict", "cmsr", "dcs-city", "dcs city", "dcs- city",
            "આઈસીટી", "સીએમએસઆર", "ડીસીએસ સિટી", "ડીસીએસ-સિટી",
        )
        if not any(m in low or m in text for m in ahmedabad_markers):
            return None
        progs = [
            p for p in self.programs
            if "Ahmedabad" in (p.get("institute") or "")
        ]
        return ("ahmedabad", progs) if progs else None

    def find_all_mentions(self, text: str) -> list[dict]:
        """Every distinct program named anywhere in `text`. Used by the
        lead classifier; covers all three index passes."""
        if not text:
            return []
        en_norm = _normalise(text)
        gu_norm_compact = _gu_normalise(text).replace(" ", "")
        seen: set[str] = set()
        out: list[dict] = []

        def _push(prog: dict) -> None:
            key = (prog.get("program") or "").strip()
            if key and key not in seen:
                seen.add(key)
                out.append(prog)

        for prog_norm, prog in self._by_norm:
            if prog_norm in en_norm:
                _push(prog)
        for alias_norm, prog in self._alias_index:
            if alias_norm in en_norm:
                _push(prog)
        for alias_norm, prog in self._gu_alias_index:
            if alias_norm in gu_norm_compact:
                _push(prog)
        return out


def _tag_program(name: str) -> tuple[str, str]:
    """Classify a program's family (e.g. 'BCA', 'B.Tech', 'M.Sc IT') and the
    stream-category (engineering / management / nursing / etc.) from its
    canonical name. Returns ('family_key', 'stream_key').

    Family keys are deliberately COARSE so 'BCA Honours Computer
    Applications' and 'BCA Honours AI ML' share family='bca' — that's how
    we know to list all variants when the caller says just 'BCA વિશે કહો'.

    Stream keys are higher-level groupings used for the 'Science A વાળા
    માટે કયા course available છે?' query — they map to BACKGROUNDS, not
    to one specific program.
    """
    n = name.lower()
    # Family detection (most specific first).
    if "msc it" in n or "m. sc. it" in n or "m.sc. it" in n:
        family = "msc-it"
    elif "msc " in n or "m. sc." in n or "m.sc." in n:
        family = "msc"
    elif "mtech" in n or "m.tech" in n or "m. tech" in n:
        family = "mtech"
    elif "mba" in n:
        family = "mba"
    elif "mca" in n:
        family = "mca"
    elif "m. pharm" in n or "m.pharm" in n:
        family = "mpharm"
    elif "btech cse" in n or "b.tech cse" in n:
        family = "btech-cse"
    elif "btech" in n or "b. tech" in n or "b.tech" in n:
        family = "btech"
    elif "bsc it" in n or "b. sc. it" in n or "b.sc. it" in n or "b.sc.it" in n:
        family = "bsc-it"
    elif "b. sc." in n or "b.sc." in n or "bsc " in n:
        family = "bsc"
    elif "bca" in n:
        family = "bca"
    elif "bba" in n:
        family = "bba"
    elif "b. com" in n or "b.com" in n or "bcom" in n:
        family = "bcom"
    elif "b. pharm" in n or "b.pharm" in n:
        family = "bpharm"
    elif "b. arch" in n or "b.arch" in n or "architecture" in n:
        family = "barch"
    elif "diploma + degree" in n:
        family = "diploma-degree"
    elif "diploma" in n:
        family = "diploma"
    elif "gnm" in n or "general nursing midwifery" in n:
        family = "nursing"
    elif "b. sc. (nursing)" in n or "nursing" in n:
        family = "nursing"
    elif "marine" in n or "nautical" in n or "gme" in n or "eto " in n or "g. p. ratings" in n:
        family = "marine"
    else:
        family = "other"

    # Stream / background mapping.
    if family in ("btech", "btech-cse", "barch"):
        stream = "engineering"
    elif family in ("bsc", "msc", "bsc-it", "msc-it", "mtech"):
        stream = "science"
    elif family in ("bca", "mca"):
        stream = "computer"
    elif family in ("bba", "mba", "bcom"):
        stream = "management"
    elif family in ("bpharm", "mpharm"):
        stream = "pharmacy"
    elif family == "nursing":
        stream = "nursing"
    elif family == "marine":
        stream = "marine"
    elif family in ("diploma", "diploma-degree"):
        stream = "diploma"
    else:
        stream = "other"

    return family, stream


# How a caller refers to each family / stream — used for substring matching
# against the (normalised) user message. Each key is a family/stream code;
# values are aliases in BOTH English and Gujarati (no-space gu form).
_FAMILY_ALIASES: dict[str, list[str]] = {
    "bca": ["bca", "બીસીએ"],
    "bba": ["bba", "બીબીએ"],
    "bcom": ["bcom", "b.com", "b com", "બીકોમ"],
    "btech": ["btech", "b.tech", "b tech", "બીટેક"],
    "btech-cse": ["btech cse", "b.tech cse", "cse", "computer engineering", "computer science", "બીટેકસીએસઈ"],
    "mtech": ["mtech", "m.tech", "m tech", "એમટેક"],
    "bsc": ["bsc", "b.sc", "b sc", "બીએસસી"],
    "bsc-it": ["bsc it", "b.sc it", "b.sc.it", "બીએસસીઆઈટી"],
    "msc": ["msc", "m.sc", "m sc", "એમએસસી"],
    "msc-it": ["msc it", "m.sc it", "m.sc.it", "એમએસસીઆઈટી"],
    "mba": ["mba", "એમબીએ"],
    "mca": ["mca", "એમસીએ"],
    "bpharm": ["b.pharm", "bpharm", "બીફાર્મ", "ફાર્મસી"],
    "mpharm": ["m.pharm", "mpharm", "એમફાર્મ"],
    "barch": ["b.arch", "barch", "architecture", "આર્કિટેક્ચર"],
    "diploma": ["diploma", "ડિપ્લોમા"],
    "nursing": ["gnm", "nursing", "midwifery", "જીએનએમ", "નર્સિંગ"],
    "marine": ["marine", "nautical", "gme", "marine engineering", "મરીન", "નોટિકલ"],
}


# Guardrail C (2026-05-21): topic taxonomy. The catalog's `_family` dict
# groups programs by DEGREE CODE (btech / bsc / msc / ...) — that's how
# the existing browse path works. But callers often think in TOPIC
# terms: "marine વિશે" means ANY marine-related program regardless of
# degree level (B.Tech Marine, B.Sc Nautical Science, DNS, GME, ETO,
# G.P. Ratings). With the family-only taxonomy, find_family_query
# returned only the 3 programs tagged 'marine' in _tag_program and
# missed B.Tech Marine entirely — which made the LLM context contain
# only GME / ETO / G.P. Ratings, and fact_guard then "corrected" the
# correct B.Tech Marine fee (4 લાખ 95 હજાર) to GME's (3 લાખ 80 હજાર).
#
# `_TOPIC_ALIASES` is a separate keyword-to-topic dict checked BEFORE
# family in find_family_query. Topic membership is derived from
# program names via `_topic_for_program` at catalog load time, so a
# program can be in both a family (e.g. 'btech') AND a topic ('marine').
_TOPIC_ALIASES: dict[str, list[str]] = {
    "marine":               ["marine", "nautical", "gme", "મરીન", "નોટિકલ", "જીએમઈ"],
    "automobile":           ["automobile", "ઓટોમોબાઇલ"],
    "civil":                ["civil engineering", "civil", "સિવિલ"],
    "mechanical":           ["mechanical engineering", "mechanical", "મેકેનિકલ"],
    "electrical":           ["electrical engineering", "electrical", "ઇલેક્ટ્રિકલ"],
    "mechatronics":         ["mechatronics", "મેકાટ્રોનિક્સ"],
    "computer-eng":         ["computer engineering", "કોમ્પ્યુટર એન્જિનિયરિંગ"],
    "biomedical":           ["biomedical", "બાયોમેડિકલ"],
    "chemical":             ["chemical engineering", "chemical", "કેમિકલ"],
    "biotechnology":        ["biotechnology", "biotech", "બાયોટેકનોલોજી"],
    "microbiology":         ["microbiology", "માઇક્રોબાયોલોજી"],
    "agriculture":          ["agriculture", "એગ્રીકલ્ચર"],
    "food":                 ["food technology", "food science", "ફૂડ ટેકનોલોજી", "ફૂડ સાયન્સ"],
    "cyber-security":       ["cyber security", "cybersecurity", "સાયબર સિક્યુરિટી"],
    "data-science":         ["data science", "ડેટા સાયન્સ"],
    "ai-ml":                ["ai and ml", "ai & ml", "artificial intelligence", "machine learning", "એઆઈ એન્ડ એમએલ", "મશીન લર્નિંગ"],
    "infrastructure":       ["infrastructure management", "ઇન્ફ્રાસ્ટ્રક્ચર"],
    "business-analytics":   ["business analytics", "બિઝનેસ એનાલિટિક્સ"],
    "international-business":["international business", "ઇન્ટરનેશનલ બિઝનેસ"],
}


def _topic_for_program(name: str) -> str | None:
    """Classify a program by TOPIC (cross-cuts degree levels). Returns
    None if the program doesn't fit a known topic — e.g. plain BCA /
    BBA without a specialisation suffix. Used to build the topic
    taxonomy at catalog load time.
    """
    n = (name or "").lower()
    if "marine" in n or "nautical" in n or n.startswith("dns") or " dns" in (" " + n) \
       or "gme" in n.split() or "g. p. ratings" in n or "g.p. ratings" in n:
        return "marine"
    if "automobile" in n:
        return "automobile"
    if "(civil)" in n or n.endswith("civil") or " civil " in (" " + n + " "):
        return "civil"
    if "(mechanical)" in n or "mechanical engineering" in n:
        return "mechanical"
    if "(electrical)" in n or "electrical engineering" in n:
        return "electrical"
    if "mechatronics" in n:
        return "mechatronics"
    if "biomedical" in n:
        return "biomedical"
    if "chemical)" in n or "chemical engineering" in n:
        return "chemical"
    if "biotechnology" in n or "biotech)" in n:
        return "biotechnology"
    if "microbiology" in n:
        return "microbiology"
    if "agriculture" in n:
        return "agriculture"
    if "food science" in n or "food technology" in n:
        return "food"
    if "cyber security" in n:
        return "cyber-security"
    if "data science" in n:
        return "data-science"
    if "ai & ml" in n or "ai and ml" in n or "artificial intelligence" in n or "machine learning" in n:
        return "ai-ml"
    if "infrastructure management" in n:
        return "infrastructure"
    if "business analytics" in n:
        return "business-analytics"
    if "international business" in n:
        return "international-business"
    if "(computer)" in n and ("b. tech" in n or "b.tech" in n or "m. tech" in n or "m.tech" in n or "diploma" in n):
        # Engineering "Computer" branch — distinct from CSE / computer science
        return "computer-eng"
    return None

# What variant qualifiers the user might say to specify ONE variant from a
# family. If the user msg contains the family code AND any of these, we
# treat it as a specific-variant query (existing focused-program logic).
# Otherwise it's a family-list query.
_VARIANT_KEYWORDS = [
    # English specialisation words
    "honours", "hons", "ai", "ml", "cyber", "security", "data science",
    "finance", "marketing", "hr", "international", "analytics", "fintech",
    "computer applications", "civil", "mechanical", "marine", "automobile",
    "chemical", "electrical", "electronics", "ec", "agriculture", "it",
    "biomedical", "embedded", "structural", "construction", "manufacturing",
    "innovation", "entrepreneurship", "logistics", "supply chain",
    "psychology", "economics", "food science", "nanoscience",
    "regulatory", "quality assurance", "infrastructure", "business analytics",
    "general", "marketing management", "human resource",
    # Gujarati specialisation words (no-space, lowercased)
    "ઓનર્સ", "ડેટાસાયન્સ", "સાયબરસિક્યુરિટી", "એઆઈ", "એમએલ",
    "ફાઇનાન્સ", "ફાઇનાન્શિયલ", "માર્કેટિંગ", "એચઆર", "ઇન્ટરનેશનલ",
    "ફિનટેક", "બિઝનેસએનાલિટિક્સ", "મિકેનિકલ", "સિવિલ", "મરીન",
    "કોમ્પ્યુટર", "આઈટી", "ઓટોમોબાઇલ", "કેમિકલ", "ઇલેક્ટ્રિકલ",
]

# Map common stream-words the caller might say to the stream key.
_STREAM_ALIASES: dict[str, list[str]] = {
    "engineering": ["engineering", "engineer", "pcm", "physics chemistry maths", "physics chemistry mathematics", "એન્જિનિયરિંગ"],
    "science": ["science", "pcm", "pcb", "biology", "વિજ્ઞાન", "સાયન્સ"],
    "management": ["commerce", "management", "business", "વાણિજ્ય", "કોમર્સ", "મેનેજમેન્ટ"],
    "computer": ["computer", "it", "computer applications", "કમ્પ્યુટર", "આઈટી"],
    "pharmacy": ["pharmacy", "ફાર્મસી"],
    "nursing": ["nursing", "midwifery", "નર્સિંગ"],
    "marine": ["marine", "nautical", "shipping", "મરીન"],
    "diploma": ["diploma", "10th", "ssc", "10 pass", "ડિપ્લોમા"],
}


def _normalise_q(s: str) -> str:
    """Aggressive normalisation for family/stream substring matching.
    Lowercases, removes Gujarati-internal spaces (so 'જી એન એમ' → 'જીએનએમ')
    and squashes ASCII whitespace."""
    return _gu_normalise(s).strip()


_catalog: ProgramCatalog | None = None


def get_catalog() -> ProgramCatalog:
    global _catalog
    if _catalog is None:
        from app.config import get_settings

        s = get_settings()
        with open(s.admission_brain_json, "r", encoding="utf-8") as f:
            data: list[dict] = json.load(f)
        _catalog = ProgramCatalog(data)
    return _catalog


def format_program_context(p: dict) -> str:
    """Render one program record into the same shape RAG returns, so we can
    inject the focused program into the LLM context without changing the
    downstream prompt assumptions."""
    return (
        f"Program: {p.get('program', 'N/A')}. "
        f"Institute: {p.get('institute', 'N/A')}. "
        f"Duration: {p.get('duration', 'N/A')}. "
        f"Fees: {p.get('fees', 'N/A')} INR. "
        f"Eligibility: {p.get('eligibility', 'N/A')}. "
        f"Counsellor: {p.get('counsellor_name', 'N/A')}, "
        f"Phone: {p.get('counsellor_phone', 'N/A')}, "
        f"Email: {p.get('counsellor_email', 'N/A')}."
    )
