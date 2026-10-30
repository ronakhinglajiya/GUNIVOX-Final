"""Pre-TTS text cleanup so Sarvam Bulbul reads things naturally.

The LLM sometimes emits text that's *correct* but sounds awkward when spoken:
- "Hons" / "(Hons.)"  →  Sarvam spells it H-O-N-S
- "&"                 →  read as "ampersand"
- "(Hons.)"            →  parenthesis pause + spelled-out "Hons"
- "B.Tech CSE (CS)"    →  parens stripped
- "hostel facility ઉપલબ્ધ છે" → Sarvam reads "hostel facility" with a
  Gujarati phonetic mapping that sounds cluttered + merged. The LLM
  emits common admissions loan-words (hostel/counsellor/scholarship)
  in English even though their Gujarati transliterations exist; we
  swap them here before TTS sees them.

Three layers of fixup, in order:

  1. Program codes  — 'B.Tech' → 'બીટેક', 'M.Sc' → 'એમએસસી', 'MBA' → 'એમબીએ',
     'GME' → 'જીએમઈ' etc. Sarvam reads Gujarati script natively.
     2026-05-21: caller fed back "B.Tech and eligibility sound gibberish";
     converting program codes to Gujarati script fixed it.

  2. Subject + structural words — 'Physics' → 'ફિઝિક્સ', 'Chemistry' →
     'કેમેસ્ટ્રી', 'Maths' → 'મેથ્સ', 'English' → 'ઇંગ્લિશ'. Sarvam
     pronounces those Gujarati transliterations cleanly. Also:
     '10+2' → 'ધોરણ 12', '60%' → '60 ટકા',
     'Prof.' → 'પ્રોફેસર', 'Dr.' → 'ડૉક્ટર'.

  3. Common admissions loan-words — hostel, counsellor, scholarship,
     etc. — already mapped (large table below).

  4. Phone-number spacing — 10-digit runs get spaces every 2 digits so
     Sarvam reads them as pairs instead of one giant number.

  5. Structural fixup — Hons → Honours, parens stripped, whitespace
     collapsed.

We post-process every text chunk on its way to TTS through `normalize()`.
"""
from __future__ import annotations

import re

# ─── 0. Compound English phrases (must run BEFORE program codes + loan-words) ──
# These multi-word phrases need to be captured before single-word loan-words
# eat their first half. E.g. "Computer Applications" must match before
# "Computer" gets remapped on its own. (2026-06-23)
_PHRASE_CODES: list[tuple[re.Pattern[str], str]] = [
    # Info-Tech campaign: "B.Sc IT" must be spoken "bee-ess-see, aai-tee" —
    # two clusters (B.Sc | IT) with a PAUSE between them. A plain space ran
    # "essee-aaitee" together (mushed), so we use a COMMA to force the pause
    # (commas pause in Sarvam; dots get vocalized as "dot"). 2026-07-02.
    (re.compile(r"(?<![A-Za-z])B\.?\s*Sc\.?\s*IT(?![A-Za-z])", re.IGNORECASE), "બી એસસી, આઈટી"),
    # Campaign counsellor name (2026-07-02): Sarvam reads the Latin "Amit
    # Suthar" awkwardly. Transliterate so it speaks cleanly in Gujarati.
    (re.compile(r"\bAmit\s+Suthar\b", re.IGNORECASE), "અમિત સુથાર"),
    (re.compile(r"\bComputer\s+Applications?\b", re.IGNORECASE), "કમ્પ્યુટર એપ્લિકેશન્સ"),
    # Info-Tech campaign (2026-06-24): keep "Information Technology" and the
    # "CA and IT" catalog form reading cleanly in Gujarati.
    (re.compile(r"\bInformation\s+Technology\b", re.IGNORECASE), "ઇન્ફોર્મેશન ટેકનોલોજી"),
    (re.compile(r"\bCA\s+and\s+IT\b", re.IGNORECASE), "સીએ અને આઈટી"),
    (re.compile(r"\bCyber\s+Security\b", re.IGNORECASE), "સાયબર સિક્યુરિટી"),
    (re.compile(r"\bData\s+Science\b", re.IGNORECASE), "ડેટા સાયન્સ"),
    (re.compile(r"\bMachine\s+Learning\b", re.IGNORECASE), "મશીન લર્નિંગ"),
    (re.compile(r"\bArtificial\s+Intelligence\b", re.IGNORECASE), "આર્ટિફિશિયલ ઇન્ટેલિજન્સ"),
    (re.compile(r"\bSoftware\s+Engineering\b", re.IGNORECASE), "સોફ્ટવેર એન્જિનિયરિંગ"),
    (re.compile(r"\b(\d+)\s*Years?\s+Integrated\b", re.IGNORECASE), r"\1 વર્ષનો ઇન્ટિગ્રેટેડ"),
    (re.compile(r"\bIntegrated\b", re.IGNORECASE), "ઇન્ટિગ્રેટેડ"),
    (re.compile(r"\(\s*Hons\.?\s*\)", re.IGNORECASE), "ઓનર્સ"),
    (re.compile(r"\bHons\.?\b", re.IGNORECASE), "ઓનર્સ"),
    (re.compile(r"\bHonours\b", re.IGNORECASE), "ઓનર્સ"),
    (re.compile(r"\bHonors\b", re.IGNORECASE), "ઓનર્સ"),
    (re.compile(r"\bBachelors?\b", re.IGNORECASE), "બેચલર"),
    (re.compile(r"\bMasters?\b", re.IGNORECASE), "માસ્ટર"),
]


# ─── 1. Program codes (must run BEFORE the dot-acronym + loan-word maps) ──
# Match the canonical written forms and the spaced forms (B. Tech / B . Tech).
# All patterns have a non-letter lookahead/behind to avoid munching things
# like "B.Tech" inside a URL. Each entry: (regex, Gujarati replacement).
_PROGRAM_CODES: list[tuple[re.Pattern[str], str]] = [
    # 4-letter and compound forms first (more specific)
    (re.compile(r"(?<![A-Za-z])MBBS(?![A-Za-z])"), "એમબીબીએસ"),
    (re.compile(r"(?<![A-Za-z])B\.?\s*Pharm\.?(?![A-Za-z])", re.IGNORECASE), "બીફાર્મ"),
    (re.compile(r"(?<![A-Za-z])M\.?\s*Pharm\.?(?![A-Za-z])", re.IGNORECASE), "એમફાર્મ"),
    (re.compile(r"(?<![A-Za-z])B\.?\s*Arch\.?(?![A-Za-z])", re.IGNORECASE), "બીઆર્ક"),
    (re.compile(r"(?<![A-Za-z])M\.?\s*Arch\.?(?![A-Za-z])", re.IGNORECASE), "એમઆર્ક"),
    (re.compile(r"(?<![A-Za-z])B\.?\s*Design(?![A-Za-z])", re.IGNORECASE), "બીડિઝાઇન"),
    (re.compile(r"(?<![A-Za-z])B\.?\s*Tech\.?(?![A-Za-z])", re.IGNORECASE), "બીટેક"),
    (re.compile(r"(?<![A-Za-z])M\.?\s*Tech\.?(?![A-Za-z])", re.IGNORECASE), "એમટેક"),
    (re.compile(r"(?<![A-Za-z])B\.?\s*Sc\.?(?![A-Za-z])", re.IGNORECASE), "બીએસસી"),
    (re.compile(r"(?<![A-Za-z])M\.?\s*Sc\.?(?![A-Za-z])", re.IGNORECASE), "એમએસસી"),
    (re.compile(r"(?<![A-Za-z])B\.?\s*Com\.?(?![A-Za-z])", re.IGNORECASE), "બીકોમ"),
    (re.compile(r"(?<![A-Za-z])M\.?\s*Com\.?(?![A-Za-z])", re.IGNORECASE), "એમકોમ"),
    (re.compile(r"(?<![A-Za-z])B\.?\s*Ed\.?(?![A-Za-z])", re.IGNORECASE), "બીએડ"),
    (re.compile(r"(?<![A-Za-z])M\.?\s*Ed\.?(?![A-Za-z])", re.IGNORECASE), "એમએડ"),
    (re.compile(r"(?<![A-Za-z])B\.?\s*E\.?(?![A-Za-z])"), "બીઈ"),       # case-sensitive: avoid matching 'be'
    (re.compile(r"(?<![A-Za-z])M\.?\s*A\.?(?![A-Za-z])"), "એમએ"),        # case-sensitive
    (re.compile(r"(?<![A-Za-z])MBA(?![A-Za-z])"), "એમબીએ"),
    (re.compile(r"(?<![A-Za-z])BBA(?![A-Za-z])"), "બીબીએ"),
    (re.compile(r"(?<![A-Za-z])BCA(?![A-Za-z])"), "બીસીએ"),
    (re.compile(r"(?<![A-Za-z])MCA(?![A-Za-z])"), "એમસીએ"),
    (re.compile(r"(?<![A-Za-z])Ph\.?\s*D\.?(?![A-Za-z])", re.IGNORECASE), "પીએચડી"),
    # Special program brands
    (re.compile(r"(?<![A-Za-z])GME(?![A-Za-z])"), "જીએમઈ"),
    (re.compile(r"(?<![A-Za-z])DNS(?![A-Za-z])"), "ડીએનએસ"),
    (re.compile(r"(?<![A-Za-z])GNM(?![A-Za-z])"), "જીએનએમ"),
    # Common 2-3 letter tech acronyms — Sarvam reads bare "IT" / "AI" with
    # Gujarati phonetics that sound clipped; explicit Gujarati spelling
    # ('આઈટી', 'એઆઈ') reads cleanly. Programme codes already covered above.
    (re.compile(r"(?<![A-Za-z])IT(?![A-Za-z])"), "આઈટી"),
    (re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])"), "એઆઈ"),
    (re.compile(r"(?<![A-Za-z])ML(?![A-Za-z])"), "એમએલ"),
    (re.compile(r"(?<![A-Za-z])HR(?![A-Za-z])"), "એચઆર"),
    (re.compile(r"(?<![A-Za-z])CSE(?![A-Za-z])"), "સીએસઈ"),
    (re.compile(r"(?<![A-Za-z])BDA(?![A-Za-z])"), "બીડીએ"),
    (re.compile(r"(?<![A-Za-z])ECE(?![A-Za-z])"), "ઇસીઈ"),
    (re.compile(r"(?<![A-Za-z])CS(?![A-Za-z])"), "સીએસ"),
    (re.compile(r"(?<![A-Za-z])EC(?![A-Za-z])"), "ઇસી"),
    (re.compile(r"(?<![A-Za-z])HCN(?![A-Za-z])"), "એચસીએન"),
    (re.compile(r"(?<![A-Za-z])EMI(?![A-Za-z])"), "ઈએમઆઈ"),
    (re.compile(r"(?<![A-Za-z])GPA(?![A-Za-z])"), "જીપીએ"),
    (re.compile(r"(?<![A-Za-z])CSB(?![A-Za-z])"), "સીએસબી"),
    # 'Graduate' as a standalone word (e.g. 'Graduate Marine Engineering')
    (re.compile(r"(?<![A-Za-z])Graduate(?![A-Za-z])", re.IGNORECASE), "ગ્રેજ્યુએટ"),
]


# ─── 2. Titles (Prof. / Dr.) ────────────────────────────────────────────
_TITLES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<![A-Za-z])Prof\.?(?![A-Za-z])", re.IGNORECASE), "પ્રોફેસર"),
    (re.compile(r"(?<![A-Za-z])Dr\.?(?![A-Za-z])", re.IGNORECASE), "ડૉક્ટર"),
    (re.compile(r"(?<![A-Za-z])Mr\.?(?![A-Za-z])", re.IGNORECASE), "મિસ્ટર"),
    (re.compile(r"(?<![A-Za-z])Mrs\.?(?![A-Za-z])", re.IGNORECASE), "મિસિસ"),
    (re.compile(r"(?<![A-Za-z])Ms\.?(?![A-Za-z])"), "મિઝ"),
]


# ─── 3. Subjects + percent + 10+2 ───────────────────────────────────────
_STRUCTURAL: list[tuple[re.Pattern[str], str]] = [
    # 10+2 → ધોરણ 12 (how Indians actually say it). Must come before percent.
    (re.compile(r"\b10\s*\+\s*2\b"), "ધોરણ 12"),
    (re.compile(r"\b12\s*\+\s*2\b"), "ધોરણ 12"),  # typo guard
    # Percent: "60%" → "60 ટકા"
    (re.compile(r"(\d+(?:\.\d+)?)\s*%"), r"\1 ટકા"),
    # Plus → "વત્તા" so "60+50" reads naturally if it appears
    # (we keep the digits as-is)
]


# ─── 4. Subject names (English subjects → Gujarati script) ──────────────
_SUBJECTS: list[tuple[str, str]] = [
    ("Mathematics", "મેથેમેટિક્સ"),
    ("Computer Science", "કોમ્પ્યુટર સાયન્સ"),
    ("Physics", "ફિઝિક્સ"),
    ("Chemistry", "કેમેસ્ટ્રી"),
    ("Biology", "બાયોલોજી"),
    ("Maths", "મેથ્સ"),
    ("Math", "મેથ્સ"),
    ("English", "ઇંગ્લિશ"),
    ("Statistics", "સ્ટેટિસ્ટિક્સ"),
    ("Economics", "ઇકોનોમિક્સ"),
    ("Accounts", "એકાઉન્ટ્સ"),
    ("Accountancy", "એકાઉન્ટન્સી"),
    ("PCM", "પીસીએમ"),
    ("PCB", "પીસીબી"),
]
_SUBJECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<![A-Za-z]){re.escape(en)}(?![A-Za-z])", re.IGNORECASE), gu)
    for en, gu in _SUBJECTS
]


# ─── 5. Common admissions loan-words (large table — same as before) ────
_EN_TO_GU: list[tuple[str, str]] = [
    # Multi-word phrases (must come first)
    ("cyber security", "સાયબર સિક્યુરિટી"),
    ("data science", "ડેટા સાયન્સ"),
    ("machine learning", "મશીન લર્નિંગ"),
    ("artificial intelligence", "આર્ટિફિશિયલ ઇન્ટેલિજન્સ"),
    ("business analytics", "બિઝનેસ એનાલિટિક્સ"),
    ("international business", "ઇન્ટરનેશનલ બિઝનેસ"),
    ("infrastructure management", "ઇન્ફ્રાસ્ટ્રક્ચર મેનેજમેન્ટ"),
    ("food technology", "ફૂડ ટેકનોલોજી"),
    ("food science", "ફૂડ સાયન્સ"),
    ("nautical science", "નોટિકલ સાયન્સ"),
    ("marine engineering", "મરીન એન્જિનિયરિંગ"),
    ("computer engineering", "કોમ્પ્યુટર એન્જિનિયરિંગ"),
    ("automobile engineering", "ઓટોમોબાઇલ એન્જિનિયરિંગ"),
    # Common single-word loan-words (alphabetical)
    ("admission", "એડમિશન"),
    ("admissions", "એડમિશન"),
    ("agriculture", "એગ્રીકલ્ચર"),
    ("and", "અને"),
    ("automobile", "ઓટોમોબાઇલ"),
    ("available", "ઉપલબ્ધ"),
    ("biomedical", "બાયોમેડિકલ"),
    ("biotechnology", "બાયોટેકનોલોજી"),
    ("boys", "છોકરાઓ"),
    ("campus", "કેમ્પસ"),
    ("chemical", "કેમિકલ"),
    ("civil", "સિવિલ"),
    ("commerce", "કોમર્સ"),
    ("computer", "કોમ્પ્યુટર"),
    ("confirm", "કન્ફર્મ"),
    ("counsellor", "કાઉન્સેલર"),
    ("counselor", "કાઉન્સેલર"),
    ("course", "કોર્સ"),
    ("courses", "કોર્સ"),
    ("design", "ડિઝાઇન"),
    ("details", "વિગત"),
    ("duration", "ડ્યુરેશન"),
    ("electrical", "ઇલેક્ટ્રિકલ"),
    ("eligibility", "એલિજિબિલિટી"),
    ("eligible", "એલિજિબલ"),
    ("engineering", "એન્જિનિયરિંગ"),
    ("enroll", "જોડાવા"),
    ("enrollment", "એનરોલમેન્ટ"),
    ("exact", "ચોક્કસ"),
    ("explain", "સમજાવ"),
    ("facility", "ફેસિલિટી"),
    ("facilities", "ફેસિલિટી"),
    ("fee", "ફી"),
    ("fees", "ફી"),
    ("girls", "છોકરીઓ"),
    ("form", "ફોર્મ"),
    ("forms", "ફોર્મ"),
    ("hospitality", "હોસ્પિટાલિટી"),
    ("hostel", "હોસ્ટેલ"),
    ("income", "આવક"),
    ("library", "લાઇબ્રેરી"),
    ("management", "મેનેજમેન્ટ"),
    ("marine", "મરીન"),
    ("marketing", "માર્કેટિંગ"),
    ("mechanical", "મેકેનિકલ"),
    ("mechatronics", "મેકાટ્રોનિક્સ"),
    ("merit", "મેરિટ"),
    ("microbiology", "માઇક્રોબાયોલોજી"),
    ("nautical", "નોટિકલ"),
    ("nursing", "નર્સિંગ"),
    ("option", "ઓપ્શન"),
    ("options", "ઓપ્શન"),
    ("pharmacy", "ફાર્મસી"),
    ("placement", "પ્લેસમેન્ટ"),
    ("placements", "પ્લેસમેન્ટ"),
    ("process", "પ્રક્રિયા"),
    ("program", "પ્રોગ્રામ"),
    ("programs", "પ્રોગ્રામ"),
    ("programme", "પ્રોગ્રામ"),
    ("scholarship", "સ્કોલરશિપ"),
    ("scholarships", "સ્કોલરશિપ"),
    ("science", "સાયન્સ"),
    ("security", "સિક્યુરિટી"),
    ("semester", "સેમેસ્ટર"),
    ("specialisation", "સ્પેશિયલાઇઝેશન"),
    ("specialization", "સ્પેશિયલાઇઝેશન"),
    ("students", "વિદ્યાર્થી"),
    ("technology", "ટેકનોલોજી"),
    ("transport", "ટ્રાન્સપોર્ટ"),
    ("university", "યુનિવર્સિટી"),
    ("year", "વર્ષ"),
    ("years", "વર્ષ"),
]
_EN_TO_GU_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<![A-Za-z]){re.escape(en)}(?![A-Za-z])", re.IGNORECASE), gu)
    for en, gu in _EN_TO_GU
]


# ─── 6. Phone-number pacing ─────────────────────────────────────────────
# 10-digit runs → spelled out digit-by-digit in GUJARATI words with commas
# so Sarvam reads each one slowly with a natural pause:
#   9601185224 → "નવ, છ, શૂન્ય, એક, એક, આઠ, પાંચ, બે, બે, ચાર."
# 2026-07-02: switched from English digit words ("nine, six, …") to Gujarati.
# In a fully-Gujarati sentence the mid-utterance jump to English digits
# sounded jittery/jarring; Gujarati digits keep one continuous voice. The
# commas still force prosodic pauses so the caller can write them down.
_PHONE_RE = re.compile(r"(?<!\d)(\d{10,11})(?!\d)")
# Also catch already-spaced / dash-separated 10-digit runs ("9 4 2 6 ..."
# or "9-4-2-6-..."). LLM sometimes pre-spaces phone digits and our digit
# regex above wouldn't match. Capture the whole run, then re-normalise.
_PHONE_SPACED_RE = re.compile(
    r"(?<!\d)(\d(?:[\s\-.]+\d){9,10})(?!\d)"
)
_DIGIT_TO_EN_WORD: dict[str, str] = {
    "0": "શૂન્ય",
    "1": "એક",
    "2": "બે",
    "3": "ત્રણ",
    "4": "ચાર",
    "5": "પાંચ",
    "6": "છ",
    "7": "સાત",
    "8": "આઠ",
    "9": "નવ",
}


def _space_phone(m: re.Match[str]) -> str:
    s = m.group(1)
    # Strip non-digit separators so pre-spaced "9 4 2 6 ..." collapses
    # to the same canonical 10-digit string before we spell it out.
    digits = "".join(c for c in s if c.isdigit())
    # Period (sentence break) between digits → a much LONGER pause than a
    # comma. The Gujarati digit words are short (નવ, છ, એક) so comma gaps read
    # too fast; making each digit its own "sentence" forces a real pause so
    # the caller can note each one. Sentence-final periods are NOT vocalized
    # by Sarvam (normal turns end in "." with no spoken "dot"). 2026-07-02.
    return ". ".join(_DIGIT_TO_EN_WORD.get(c, c) for c in digits)


# ─── 7. Structural cleanups (parens / Hons / ampersand / whitespace) ───
# Word-demerge (2026-05-22): Sarvam slurs adjacent English↔Gujarati and
# digit↔letter tokens when there's no whitespace between them, producing
# 'MBA(Innovation' as one merged word. Insert a space at every script /
# digit boundary BEFORE the structural cleanups consume the parens.
_DEMERGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # English letter immediately followed by Gujarati: "MBAમાં" → "MBA માં"
    (re.compile(r"([A-Za-z])([઀-૿])"), r"\1 \2"),
    # Gujarati immediately followed by English: "ફીMBA" → "ફી MBA"
    (re.compile(r"([઀-૿])([A-Za-z])"), r"\1 \2"),
    # Digit immediately followed by Gujarati: "4વર્ષ" → "4 વર્ષ"
    (re.compile(r"(\d)([઀-૿])"), r"\1 \2"),
    # Gujarati immediately followed by digit: "વર્ષ4" → "વર્ષ 4"
    (re.compile(r"([઀-૿])(\d)"), r"\1 \2"),
    # Closing paren/bracket directly followed by anything: ")માટે" → ") માટે"
    (re.compile(r"([\)\]])([^\s\.,!?;:\)])"), r"\1 \2"),
    # Anything directly followed by an opening paren: "MBA(Innovation" → "MBA (Innovation"
    (re.compile(r"([^\s\(])(\()"), r"\1 \2"),
    # English-program-code DOT inside compound: "M.Sc.IT" → "M.Sc. IT" (so
    # Sarvam reads each token individually instead of mushing them).
    # Letter+dot followed immediately by uppercase letter: insert space.
    (re.compile(r"([A-Za-z]\.)([A-Z])"), r"\1 \2"),
    # CamelCase / acronym slur: lowercase→uppercase boundary like
    # "CyberSecurity" → "Cyber Security", "DataScience" → "Data Science".
    (re.compile(r"([a-z])([A-Z])"), r"\1 \2"),
    # Slash with no spaces: "AI/ML" → "AI / ML" (Sarvam reads slashes as
    # "slash" — separating helps it pause naturally).
    (re.compile(r"([A-Za-z])/([A-Za-z])"), r"\1 / \2"),
    # Multiple periods / period-letter slur: "B.Tech.Marine" → "B.Tech. Marine"
    # (catches the rare case where canonical names lack a space after the
    # trailing period of a degree code).
    (re.compile(r"(B\.Tech\.|M\.Tech\.|B\.Sc\.|M\.Sc\.|B\.Pharm\.|M\.Pharm\.|B\.Arch\.|B\.Com\.|M\.Com\.)([A-Z])"), r"\1 \2"),
]

_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\s*&\s*"), " and "),
    # Slashes in spec lists ("Marketing/Finance/HR") → comma pauses so Sarvam
    # reads them as a natural list instead of saying "slash" aloud.
    (re.compile(r"\s*/\s*"), ", "),
    # Unwrap parentheses/brackets but KEEP their content. Limit raised
    # 40→160 (2026-05-27) so long specialisation lists like
    # "MBA (Marketing, Finance, HR, International Business, Entrepreneurship)"
    # also get unwrapped — at 40 chars they kept their parens and Sarvam
    # voiced "bracket". Run twice via the leftover-stripper below to catch
    # any second group / nesting.
    (re.compile(r"\s*[(\[]([^()\[\]]{1,160})[)\]]\s*"), r" \1 "),
    # Strip ANY leftover punctuation Sarvam vocalizes as a word — stray or
    # unbalanced brackets, pipes, slashes, asterisks, hashes, underscores,
    # angle brackets, quotes, backticks. Replace with a space (never voiced).
    (re.compile(r"[()\[\]{}|<>\\*#@~^_=+\"'`]"), " "),
    (re.compile(r"[ \t\n]+"), " "),
]


def normalize(text: str) -> str:
    if not text:
        return text
    out = text
    # 0) Multi-word English phrases (Computer Applications, Cyber Security,
    #    Honours, Bachelors, N Years Integrated, etc.) — must run before the
    #    single-word loan-word map eats their first token.
    for pat, repl in _PHRASE_CODES:
        out = pat.sub(repl, out)
    # 1) Program codes — B.Tech → બીટેક, M.Sc → એમએસસી, MBA → એમબીએ, GME → જીએમઈ.
    #    Done FIRST so later steps don't try to re-process the now-Gujarati form.
    for pat, repl in _PROGRAM_CODES:
        out = pat.sub(repl, out)
    # 2) Titles — Prof. → પ્રોફેસર, Dr. → ડૉક્ટર, etc.
    for pat, repl in _TITLES:
        out = pat.sub(repl, out)
    # 3) Structural shorthand — 10+2 → ધોરણ 12, 60% → 60 ટકા.
    for pat, repl in _STRUCTURAL:
        out = pat.sub(repl, out)
    # 4) Subject names — Physics → ફિઝિક્સ, etc.
    for pat, repl in _SUBJECT_PATTERNS:
        out = pat.sub(repl, out)
    # 5) Common admissions loan-words — hostel, counsellor, scholarship, etc.
    for pat, repl in _EN_TO_GU_PATTERNS:
        out = pat.sub(repl, out)
    # 6) Phone-number pacing — first catch any pre-spaced 10-digit run
    #    (LLM occasionally emits "9 4 2 6 ..."), then plain runs.
    out = _PHONE_SPACED_RE.sub(_space_phone, out)
    out = _PHONE_RE.sub(_space_phone, out)
    # 6b) Demerge adjacent English↔Gujarati↔digit tokens so Sarvam doesn't
    #     slur them together as one mush-word.
    for pat, repl in _DEMERGE_PATTERNS:
        out = pat.sub(repl, out)
    # 7) Final structural fixes.
    for pat, repl in _REPLACEMENTS:
        out = pat.sub(repl, out)
    return out.strip()
