"""Pre-recorded filler audio per language.

Plays instantly while RAG/LLM/TTS work in the background. Massive
perceived-latency win: the user hears something within ~100ms of finishing
their utterance, even though the real answer takes 2-3 seconds to compute.

Fillers are TTS'd once via `scripts/generate_fillers.py` and saved to
backend/data/fillers/{language}/{kind}.wav. Multi-variant fillers (e.g.
thinking_1.wav, thinking_2.wav) let us rotate so the user doesn't hear the
exact same phrase every turn.
"""
from __future__ import annotations

import random
from pathlib import Path

DEFAULT_FILLERS: dict[str, dict[str, str]] = {
    "gu": {
        # Counselor-style opening (2026-07-03): commerce-career framing +
        # Dept of Computer Science identity in the FIRST line, then asks for
        # the caller's name so we can address them by it later.
        "greet": "નમસ્તે! કોમર્સના વિદ્યાર્થીઓ માટે સારી કારકિર્દી માટે આ કૉલ છે. બારમા કોમર્સ પછી પણ કમ્પ્યુટર ક્ષેત્રે ઉજ્જવળ ભવિષ્ય શક્ય છે. આ કૉલ ગણપત યુનિવર્સિટીના ડિપાર્ટમેન્ટ ઑફ કમ્પ્યુટર સાયન્સ તરફથી છે. તો સૌથી પહેલા, તમારું શુભ નામ જણાવશો?",
        # Single-shot fallback used if no _N variants are on disk.
        "thinking": "હું ચેક કરીને કહું.",
        # Short turn-1 acknowledgement after the caller gives their name.
        # Does NOT say "let me check" — that would sound robotic when the
        # appropriate response is just "hello back".
        "name_greet": "નમસ્તે!",
        "let_me_check": "જરા તપાસું.",
        "acknowledge": "હા, ચોક્કસ.",
        "goodbye": "આભાર, શુભ દિવસ.",
        # Inbound-call greeting — someone dials our public number and hears
        # this single self-contained message, then the call ends. Phone
        # number is digit-by-digit-with-commas so Sarvam reads each digit
        # clearly instead of slurring them as a single 10-digit number.
        "inbound_greeting": (
            "નમસ્તે, ગણપત યુનિવર્સિટીમાં તમારું સ્વાગત છે. "
            "એડમિશન્સ ટીમનો સંપર્ક કરવા માટે, હું તમને નંબર આપું છું. "
            "નવ, નવ, સાત, આઠ, નવ, નવ, છ, નવ, ચાર, ચાર. આભાર."
        ),
        # Outbound-call dead-silence check-in. Played after 10s of caller
        # silence. If they still don't respond within 3s, the watchdog
        # plays `silence_end` and hangs up.
        "silence_check_in": "શું તમે કોલ પર છો?",
        "silence_end": "કોઈ જવાબ ના મળવાને કારણે અમે કોલને એન્ડ કરી રહ્યા છીએ.",
    },
    "hi": {
        "greet": "नमस्ते, मैं गुनीवोक्स गणपत यूनिवर्सिटी से बात कर रहा हूँ. आप कैसे हैं? आपका शुभ नाम बताइएगा?",
        "thinking": "मैं चेक करके बताता हूँ.",
        "name_greet": "नमस्ते!",
        "let_me_check": "ज़रा देखता हूँ.",
        "acknowledge": "जी हाँ, ज़रूर.",
        "goodbye": "धन्यवाद, शुभ दिन.",
    },
    "en": {
        "greet": "Hi, I'm Gunivox calling from Ganpat University. How are you doing today? May I know your name please?",
        "thinking": "Let me check and get back to you.",
        "name_greet": "Hello!",
        "let_me_check": "Let me check.",
        "acknowledge": "Sure.",
        "goodbye": "Thank you.",
    },
}

# Pool of thinking-filler variants. The bridge picks one at random per turn so
# the caller hears variety instead of the same phrase every time. Each variant
# is ~1-1.5s spoken — long enough to mask the LLM+TTS warmup, short enough not
# to feel padded.
#
# Rewrite 2026-05-20: user said "જરા તપાસું" sounded robotic. Real Gujarati
# admissions counsellors don't use literal "let me check" — they use filler
# particles like "જી જી" / "હા હા", soft conjunctions ("તો"), and
# present-continuous verbs that feel conversational. Each variant below was
# rewritten in that register — sentences a human at a desk would actually
# say while pulling up a file.
THINKING_VARIANTS: dict[str, list[str]] = {
    "gu": [
        "જી, એક સેકન્ડ આપો હં.",        # "Yes, give me one second OK" — soft, polite
        "હા હા, જરા જોવા દો.",          # "Yes yes, let me have a look" — natural doubled-yes
        "બરાબર, હું જોઈ રહી છું.",       # "Alright, I'm looking" (female) — present continuous
        "જી જી, એક મિનિટ.",            # "Yes yes, one minute" — fast acknowledgement
    ],
    "hi": [
        "जी, एक सेकंड दीजिए.",          # "Yes, give me a second" — polite request form
        "हाँ हाँ, ज़रा देखता हूँ.",        # "Yes yes, just looking" — doubled-yes filler
        "ठीक है, मैं देख रहा हूँ.",       # "Alright, I'm checking" — present continuous
        "जी जी, एक मिनट.",             # "Yes yes, one minute" — fast acknowledgement
    ],
    "en": [
        "Sure, just a moment.",
        "Mhm, let me take a look.",
        "Alright, checking on that now.",
        "One sec, pulling that up for you.",
    ],
}


class FillerLibrary:
    """Loads pre-rendered filler WAVs from disk into memory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[tuple[str, str], bytes] = {}

    def get(self, kind: str, language: str = "gu") -> bytes:
        key = (language, kind)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path = self.root / language / f"{kind}.wav"
        if not path.exists():
            return b""
        data = path.read_bytes()
        self._cache[key] = data
        return data

    def has(self, kind: str, language: str = "gu") -> bool:
        return (self.root / language / f"{kind}.wav").exists()

    def get_random_thinking(self, language: str = "gu") -> bytes:
        """Return a random thinking-filler variant if any are on disk; else
        fall back to the single-shot thinking.wav. Naming convention:
        thinking_1.wav, thinking_2.wav … (no padding).
        """
        variants = []
        lang_dir = self.root / language
        if lang_dir.exists():
            for i in range(1, 10):
                if (lang_dir / f"thinking_{i}.wav").exists():
                    variants.append(f"thinking_{i}")
        if not variants:
            return self.get("thinking", language)
        kind = random.choice(variants)
        return self.get(kind, language)

    def warm_load(self) -> int:
        count = 0
        for language, kinds in DEFAULT_FILLERS.items():
            for kind in kinds:
                if self.has(kind, language):
                    self.get(kind, language)
                    count += 1
            # Pre-load variant WAVs too so we don't pay disk-read cost on
            # the first turn that randomly picks each one.
            lang_dir = self.root / language
            if lang_dir.exists():
                for i in range(1, 10):
                    name = f"thinking_{i}"
                    if (lang_dir / f"{name}.wav").exists():
                        self.get(name, language)
                        count += 1
        return count
