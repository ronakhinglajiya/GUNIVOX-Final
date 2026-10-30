"""RAG retrieval over admissions knowledge base.

Index is English-only. For queries containing Gujarati/Hindi script, we
translate to English first (text-embedding-3-small doesn't bridge Indic-
script ↔ Latin-script terms like 'કોમ્પ્યુટર' ↔ 'Computer' well).

A small in-memory LRU cache avoids re-translating common phrases.
"""
from __future__ import annotations

import logging
import pickle
from functools import lru_cache
from pathlib import Path

import faiss
import numpy as np
from openai import AsyncOpenAI

log = logging.getLogger("gunivox.rag")

EMBED_MODEL = "text-embedding-3-small"
TRANSLATE_MODEL = "gpt-4o-mini"


def _has_indic_script(text: str) -> bool:
    # Gujarati: U+0A80–U+0AFF, Devanagari: U+0900–U+097F
    return any(0x0900 <= ord(c) <= 0x0AFF for c in text)


# Any of these in the query → English signal is strong enough; skip translation.
# Widened 2026-05-21 to cover all 17 ambiguous-topic groups in the catalog
# + the university-facts queries (scholarship/hostel/placement/transport).
# Each skip saves a ~300ms translate chat round-trip.
ENGLISH_PROGRAM_KEYWORDS = frozenset(
    [
        "b.tech", "btech", "b tech", "bba", "bca", "b.sc", "bsc",
        "m.tech", "mtech", "mba", "m.sc", "msc", "mca",
        "diploma", "phd", "ph.d", "cse", "csb", "cs", "ec", "ece", "it",
        "civil", "mechanical", "mech", "electrical", "elec",
        "finance", "marketing", "hr", "automobile", "auto",
        "nursing", "pharmacy", "hospitality", "computer",
        "fee", "fees", "duration", "eligibility", "admission",
        # Ambiguous-topic keywords (caller often emits these in English)
        "cyber", "security", "data", "science", "ai", "ml",
        "machine", "learning", "infrastructure", "biotechnology",
        "microbiology", "biomedical", "chemical", "agriculture",
        "food", "technology", "analytics", "international", "business",
        "marine", "nautical", "mechatronics",
        # University facts
        "scholarship", "hostel", "placement", "transport", "campus",
        "location", "address",
    ]
)


# Topic phrases callers commonly emit in Gujarati — and their canonical
# English translations. Seeded into `_translate_cache` at boot so the
# first occurrence of e.g. 'સાયબર સિક્યુરિટી' DOESN'T cost a ~300ms
# translate chat call. Listed in user-utterance form (what Sarvam STT
# typically delivers), not catalog form, so the cache key matches.
TRANSLATE_CACHE_SEED: dict[str, str] = {
    # Ambiguous topics
    "સાયબર સિક્યુરિટી": "Cyber Security",
    "ડેટા સાયન્સ": "Data Science",
    "એઆઈ એન્ડ એમએલ": "AI and ML",
    "આર્ટિફિશિયલ ઇન્ટેલિજન્સ": "Artificial Intelligence",
    "મશીન લર્નિંગ": "Machine Learning",
    "ઇન્ફ્રાસ્ટ્રક્ચર મેનેજમેન્ટ": "Infrastructure Management",
    "ઓટોમોબાઇલ": "Automobile",
    "ઓટોમોબાઇલ એન્જિનિયરિંગ": "Automobile Engineering",
    "બાયોટેકનોલોજી": "Biotechnology",
    "માઇક્રોબાયોલોજી": "Microbiology",
    "બાયોમેડિકલ": "Biomedical",
    "કેમિકલ": "Chemical",
    "એગ્રીકલ્ચર": "Agriculture",
    "ફૂડ ટેકનોલોજી": "Food Technology",
    "બિઝનેસ એનાલિટિક્સ": "Business Analytics",
    "ઇન્ટરનેશનલ બિઝનેસ": "International Business",
    "મરીન": "Marine",
    "મરીન એન્જિનિયરિંગ": "Marine Engineering",
    "નોટિકલ સાયન્સ": "Nautical Science",
    "મેકાટ્રોનિક્સ": "Mechatronics",
    "મેકેનિકલ": "Mechanical",
    "ઇલેક્ટ્રિકલ": "Electrical",
    "સિવિલ": "Civil",
    "કોમ્પ્યુટર": "Computer",
    "આઈટી": "IT",
    # Common follow-up vocab
    "ફી": "fees",
    "ફીસ": "fees",
    "ખર્ચ": "cost",
    "કાઉન્સેલર": "counsellor",
    "પ્રોગ્રામ": "program",
    "કોર્સ": "course",
    "પ્રવેશ": "admission",
    "પાત્રતા": "eligibility",
    "લાયકાત": "eligibility",
    # University facts
    "છાત્રવૃત્તિ": "scholarship",
    "હોસ્ટેલ": "hostel",
    "પ્લેસમેન્ટ": "placement",
    "ટ્રાન્સપોર્ટ": "transport",
    "બસ": "bus",
}


def _needs_translation(text: str) -> bool:
    """Translate Indic queries before embedding.

    We tried both pure bilingual indexing (no translate) and translate+bilingual.
    Pure-bilingual missed queries like "ડેટા સાયન્સ" because the Gujarati
    index text preserves "Data Science" verbatim (program code) and there's
    no semantic bridge to "ડેટા સાયન્સ". The translate path reliably maps
    "ડેટા સાયન્સ" → "Data Science" → strong match. Translation is cached so
    repeated queries are free.

    Skip when the Gujarati already contains an English OR Gujarati
    program keyword (e.g. "BCA વિશે પૂછું" / "બીટેક મરીન"). The catalog's
    find_by_name already does an O(1) alias lookup upstream of RAG, so
    for known program codes we don't need the translate→embed round-trip.
    Round 14 (2026-05-21): added Gujarati program keywords to kill the
    300ms first-mention translate cost on ANY known program.
    """
    if not _has_indic_script(text):
        return False
    lower = text.lower()
    if any(kw in lower for kw in ENGLISH_PROGRAM_KEYWORDS):
        return False
    if any(kw in text for kw in GUJARATI_PROGRAM_KEYWORDS):
        return False
    return True


# Round 14: Gujarati program/topic keywords. When ANY of these appears in
# a Gujarati query, we skip translate — the catalog's find_by_name +
# topic taxonomy will handle it directly with zero OpenAI overhead.
GUJARATI_PROGRAM_KEYWORDS = frozenset(
    [
        # Degree codes
        "બીટેક", "એમટેક", "બીએસસી", "એમએસસી", "બીબીએ", "એમબીએ",
        "બીસીએ", "એમસીએ", "બીકોમ", "એમકોમ", "બીઆર્ક", "એમઆર્ક",
        "બીફાર્મ", "એમફાર્મ", "બીએડ", "એમએડ",
        "ડિપ્લોમા", "ડિગ્રી",
        # Vocational brands
        "જીએનએમ", "જીએમઈ", "ડીએનએસ", "જીપી રેટિંગ", "ઇટીઓ",
        # Streams / fields
        "મરીન", "ઓટોમોબાઇલ", "સિવિલ", "મેકેનિકલ", "ઇલેક્ટ્રિકલ",
        "મેકાટ્રોનિક્સ", "કેમિકલ", "બાયોમેડિકલ", "બાયોટેકનોલોજી",
        "માઇક્રોબાયોલોજી", "એગ્રીકલ્ચર", "ફૂડ", "નર્સિંગ", "ફાર્મસી",
        "આર્કિટેક્ચર", "ડિઝાઇન", "નોટિકલ",
        # IT specialisations
        "કોમ્પ્યુટર", "આઈટી", "સાયબર સિક્યુરિટી", "ડેટા સાયન્સ",
        "મશીન લર્નિંગ", "આર્ટિફિશિયલ ઇન્ટેલિજન્સ", "એઆઈ", "એમએલ",
        "ઇન્ફ્રાસ્ટ્રક્ચર",
        # Management specialisations
        "ફાઇનાન્સ", "માર્કેટિંગ", "એચઆર", "બિઝનેસ એનાલિટિક્સ",
        "ઇન્ટરનેશનલ બિઝનેસ", "લોજિસ્ટિક્સ", "હેલ્થકેર",
        "ફિનટેક", "બ્લોકચેન",
        # Common admissions-vocab nouns
        "ફી", "ફીસ", "ખર્ચ", "કાઉન્સેલર", "એડમિશન", "પ્રવેશ",
        "હોસ્ટેલ", "સ્કોલરશિપ", "પ્લેસમેન્ટ", "ટ્રાન્સપોર્ટ",
        "પ્રોગ્રામ", "કોર્સ", "પાત્રતા", "લાયકાત",
    ]
)


class RAGEngine:
    def __init__(
        self,
        index_path: Path,
        metadata_path: Path,
        openai_client: AsyncOpenAI,
        embed_model: str = EMBED_MODEL,
    ) -> None:
        self.index = faiss.read_index(str(index_path))
        with open(metadata_path, "rb") as f:
            self.metadata: dict[int, dict] = pickle.load(f)
        self.client = openai_client
        self.embed_model = embed_model
        # Pre-seeded with common topic translations so the first occurrence
        # of e.g. 'સાયબર સિક્યુરિટી' on a call doesn't pay the ~300ms
        # translate chat round-trip (saves a turn-1 latency hit on every
        # fresh call). See TRANSLATE_CACHE_SEED above.
        self._translate_cache: dict[str, str] = dict(TRANSLATE_CACHE_SEED)
        # Round 14 (2026-05-21): auto-extend translate cache with all 126
        # programs' Gujarati ↔ English mappings parsed from the catalog's
        # text_field_gu '<gujarati> એટલે કે <english>' captures. Kills
        # the first-mention translate penalty for ANY program, not just
        # the 39 pre-seeded common topics. Cost: zero OpenAI calls (pure
        # string parsing). Adds ~100-200 cache entries.
        self._seed_translate_from_catalog()
        # Embedding LRU cache (Round 9 latency fix). Repeat queries within
        # and across calls hit instantly — saves the ~80-150ms embed
        # round-trip. Cap at 256 entries (~600KB at 1536 dims float32) so
        # memory stays bounded for a long-running process. Recently-used
        # eviction is implemented manually via an OrderedDict because
        # functools.lru_cache doesn't work on async methods.
        from collections import OrderedDict
        self._embed_cache: OrderedDict[str, "np.ndarray"] = OrderedDict()
        self._embed_cache_cap = 256
        log.info("rag translate cache seeded with %d phrases", len(self._translate_cache))

    def _seed_translate_from_catalog(self) -> None:
        """Parse '<gujarati> એટલે કે <english>' patterns from every
        program's text_field_gu in the FAISS metadata, and add bidirectional
        mappings to the translate cache. Also seeds common query wrappings
        ('મારે X વિશે જાણવું છે') for each program.

        Result: any caller mentioning any program by its Gujarati form
        gets cache-hit translate, saving ~300ms first-mention.
        """
        import re as _re
        # The pattern: a Gujarati phrase + 'એટલે કે' + an English phrase
        # (until next ASCII break / period). Same shape used in catalog's
        # _GU_ALIAS_PAT but here we want BOTH sides.
        # Capture English run until next Gujarati script char or newline —
        # NOT stopping at periods (otherwise "M. Tech." truncates to "M").
        eval_re = _re.compile(
            r"([઀-૿\s\(\)]+?)\s*એટલે\s*કે\s+([A-Za-z][A-Za-z0-9 \-&.,()/]+?)(?=\s*[઀-૿]|\n|$)"
        )
        added = 0
        for item in self.metadata.values():
            gu_text = (item.get("text_field_gu") or "")
            if not gu_text:
                continue
            for m in eval_re.finditer(gu_text):
                gu_phrase = m.group(1).strip()
                en_phrase = m.group(2).strip().rstrip(".,")
                if len(gu_phrase) < 3 or len(en_phrase) < 3:
                    continue
                # Direct: Gujarati phrase → English phrase
                if gu_phrase not in self._translate_cache:
                    self._translate_cache[gu_phrase] = en_phrase
                    added += 1
                # Common wrapping: "મારે X વિશે જાણવું છે"
                wrap1 = f"મારે {gu_phrase} વિશે જાણવું છે"
                if wrap1 not in self._translate_cache:
                    self._translate_cache[wrap1] = f"I want to know about {en_phrase}"
                    added += 1
                # Common wrapping: "X વિશે કહો"
                wrap2 = f"{gu_phrase} વિશે કહો"
                if wrap2 not in self._translate_cache:
                    self._translate_cache[wrap2] = f"Tell me about {en_phrase}"
                    added += 1
                # "મારે X વિશે" (shorter)
                wrap3 = f"મારે {gu_phrase} વિશે"
                if wrap3 not in self._translate_cache:
                    self._translate_cache[wrap3] = f"I want {en_phrase}"
                    added += 1
        log.info("rag translate cache auto-extended with %d catalog-derived phrases", added)

    async def embed(self, text: str) -> np.ndarray:
        key = text.strip()
        if key in self._embed_cache:
            # Move to MRU end
            self._embed_cache.move_to_end(key)
            return self._embed_cache[key]
        resp = await self.client.embeddings.create(model=self.embed_model, input=text)
        vec = np.array(resp.data[0].embedding, dtype="float32").reshape(1, -1)
        faiss.normalize_L2(vec)
        # Insert + evict LRU if over cap
        self._embed_cache[key] = vec
        if len(self._embed_cache) > self._embed_cache_cap:
            self._embed_cache.popitem(last=False)  # evict LRU
        return vec

    async def translate_to_english(self, text: str) -> str:
        cached = self._translate_cache.get(text)
        if cached is not None:
            return cached
        resp = await self.client.chat.completions.create(
            model=TRANSLATE_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate to English. Preserve program names (B.Tech, MBA, "
                        "CSE, Computer Engineering, etc.). Output ONLY the translation."
                    ),
                },
                {"role": "user", "content": text},
            ],
            max_tokens=50,
            temperature=0,
        )
        out = (resp.choices[0].message.content or "").strip()
        if out and len(self._translate_cache) < 500:
            self._translate_cache[text] = out
        return out

    async def retrieve(self, query: str, k: int = 5) -> list[dict]:
        search_query = query
        if _needs_translation(query):
            try:
                translated = await self.translate_to_english(query)
                if translated:
                    search_query = translated
                    log.info("rag translated %r -> %r", query, translated)
            except Exception as e:
                log.warning("translate failed, using original: %s", e)

        # The index is bilingual (English + Gujarati entries for the same
        # program). Retrieve OVERSAMPLE candidates so we have headroom to
        # dedupe by program name and still return k unique programs.
        oversample = max(k * 4, 8)
        vec = await self.embed(search_query)
        scores, ids = self.index.search(vec, oversample)

        results: list[dict] = []
        seen_programs: set[str] = set()
        for score, idx in zip(scores[0], ids[0]):
            if idx == -1:
                continue
            item = self.metadata.get(int(idx))
            if item is None:
                continue
            pkey = (item.get("program") or "").strip().lower()
            if pkey in seen_programs:
                continue
            seen_programs.add(pkey)
            results.append({**item, "_score": float(score)})
            if len(results) >= k:
                break
        return results

    @staticmethod
    def format_context(results: list[dict]) -> str:
        if not results:
            return "No relevant programs found."
        blocks: list[str] = []
        for i, r in enumerate(results, start=1):
            blocks.append(f"[{i}] {r.get('text_field', '')}")
        return "\n".join(blocks)

    @staticmethod
    def format_context(results: list[dict]) -> str:
        if not results:
            return "No relevant programs found."
        blocks: list[str] = []
        for i, r in enumerate(results, start=1):
            blocks.append(f"[{i}] {r.get('text_field', '')}")
        return "\n".join(blocks)
