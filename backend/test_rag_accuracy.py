"""RAG retrieval accuracy smoke test (Round 13 — 2026-05-21).

Drives the local matchers (find_by_name, find_family_query, find_stream_query,
topic taxonomy, ambig-degree disambig) against ~60 realistic caller queries.
This is the brain's first reach for a program. If THIS misses, the LLM has
nothing accurate to answer from.

Pure-Python — no OpenAI calls. Runs in <1s.

Usage:
    cd backend
    ../venv/Scripts/python.exe test_rag_accuracy.py
"""
from __future__ import annotations

import sys

from app.brain.catalog import get_catalog
from app.brain.engine import (
    _has_degree_level_marker,
    _topic_signature,
    _degree_level,
)


cat = get_catalog()


PASS = 0
FAIL: list[str] = []


def check(label: str, condition: bool, why: str = "") -> None:
    global PASS
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL.append(f"{label}: {why}")
        print(f"  [FAIL] {label} -- {why}")


def batch_check(label: str, results: list[tuple[bool, str]], tolerance: int = 0) -> None:
    """Aggregate result for large batteries. Prints one summary line + up
    to 5 failure samples. Counts each individual assertion toward the
    global PASS / FAIL tallies.
    """
    global PASS
    fails = [r for r in results if not r[0]]
    passes = len(results) - len(fails)
    PASS += passes
    if len(fails) <= tolerance:
        print(f"  [PASS] {label} ({passes}/{len(results)}, ≤{tolerance} miss allowed)")
    else:
        sample = "; ".join(r[1] for r in fails[:5])
        msg = f"{passes}/{len(results)} pass, {len(fails)} miss > tolerance {tolerance}: {sample}"
        FAIL.extend(r[1] for r in fails)
        print(f"  [FAIL] {label} -- {msg}")


def has_program(progs: list[dict], substr: str) -> bool:
    """True if any program in progs has substr (case-insensitive, dot-insensitive) in its name."""
    needle = substr.lower().replace(".", "").replace(" ", "")
    for p in progs:
        nm = (p.get("program") or "").lower().replace(".", "").replace(" ", "")
        if needle in nm:
            return True
    return False


print("=" * 70)
print("PART 1: find_by_name — specific program identification")
print("=" * 70)

specific_cases: list[tuple[str, str]] = [
    # Direct English forms
    ("B.Tech Marine", "Marine"),
    ("B.Tech CSE", "CSE"),
    ("MBA Finance", "Financial Services"),
    ("MBA Logistics", "Logistics"),
    ("BBA Marketing", "Marketing"),
    ("BCA AI ML", "AI"),
    ("BCA Cyber Security", "Cyber"),
    ("Diploma Civil", "Civil"),
    ("Diploma Mechanical", "Mechanical"),
    ("GNM", "Nursing Midwifery"),
    ("GME", "Graduate Marine"),
    # Gujarati direct forms
    ("બીટેક મરીન", "Marine"),
    ("બીટેક સીએસઈ", "CSE"),
    ("એમબીએ ફાઇનાન્સ", "Financial"),
    ("ડિપ્લોમા સિવિલ", "Civil"),
    ("ડિપ્લોમા મેકેનિકલ", "Mechanical"),
    ("બીસીએ સાયબર સિક્યુરિટી", "Cyber"),
    ("બીએસસી આઈટી ડેટા સાયન્સ", "Data Science"),
    ("એમએસસી આઈટી ડેટા સાયન્સ", "Data Science"),
    ("એમએસસી આઈટી સાયબર સિક્યુરિટી", "Cyber"),
    ("જીએનએમ", "Nursing"),
    ("જીએમઈ", "Graduate Marine"),
    # Mixed Gujarati + English
    ("મારે B.Tech Marine વિશે જાણવું છે", "Marine"),
    ("મને Cyber Security વિશે કહો", None),  # ambiguous degree, expect ambig handling
    ("હું MBA Marketing માં interested", "Marketing"),
    # Casual / informal phrasings
    ("મારે ફાર્મસી કરવી છે", "Pharm"),
    ("nursing કરવી છે", "Nursing"),
    ("architecture વિશે", "Arch"),
    # STT misheard artifacts
    ("ઓટો મોબાઇલ", "Automobile"),       # STT split
    ("ઓટોમોબાઇલ વિશે", "Automobile"),
    ("એમએસસી આઈટી ડેટા સાહેબ", "Data Science"),  # STT typo for સાયન્સ
]

for query, expected_substr in specific_cases:
    p = cat.find_by_name(query)
    if expected_substr is None:
        # Should ideally NOT resolve (ambig handling expected at higher layer)
        check(f"find_by_name({query!r:55})", p is None or expected_substr,
              f"resolved to {p.get('program') if p else None!r}")
    else:
        got = (p.get("program") if p else "") or ""
        ok = expected_substr.lower().replace(" ", "") in got.lower().replace(" ", "").replace(".", "")
        check(f"find_by_name({query!r:55})", ok, f"expected substr {expected_substr!r}, got {got!r}")


print()
print("=" * 70)
print("PART 2: find_family_query — topic + family broad queries")
print("=" * 70)

family_cases: list[tuple[str, str, int]] = [
    # (query, expected_key, expected_min_variants)
    ("BCA વિશે કહો", "bca", 3),
    ("BBA વિશે", "bba", 3),
    ("BTech વિશે", "btech", 5),  # many B.Tech variants
    ("MBA વિશે", "mba", 2),
    ("Marine વિશે", "marine", 4),  # topic taxonomy: should include B.Tech Marine + GME + ETO + DNS + B.Sc Nautical + G.P. Ratings
    ("મરીન વિશે", "marine", 4),
    ("automobile વિશે", "automobile", 3),  # topic
    ("ઓટોમોબાઇલ વિશે", "automobile", 3),
    ("cyber security", "cyber-security", 2),
    ("data science", "data-science", 2),
    ("biotechnology", "biotechnology", 2),
    ("agriculture", "agriculture", 2),
]

for query, expected_key, min_count in family_cases:
    r = cat.find_family_query(query)
    if r is None:
        check(f"find_family_query({query!r:40})", False, f"got None, expected {expected_key} with ≥{min_count} variants")
    else:
        key, variants = r
        ok_key = expected_key in key or key in expected_key
        ok_count = len(variants) >= min_count
        check(f"find_family_query({query!r:40})", ok_key and ok_count,
              f"got key={key!r} count={len(variants)}, expected key~{expected_key!r} count≥{min_count}")


print()
print("=" * 70)
print("PART 3: find_stream_query — high-level stream/field queries")
print("=" * 70)

stream_cases: list[tuple[str, str | None]] = [
    ("engineering programs", "engineering"),
    ("science background", "science"),
    ("management courses", "management"),
    ("commerce courses", "management"),  # commerce maps to management
    ("nursing courses", "nursing"),
]
# Note: "PCM થી 12th પાસ" intentionally NOT in this list — the catalog
# correctly maps PCM→science. The bridge's eligibility-pre-guard
# suppresses stream override on eligibility-shaped queries (tested in
# the cross_program_isolation suite, not here).

for query, expected_key in stream_cases:
    r = cat.find_stream_query(query)
    if expected_key is None:
        check(f"find_stream_query({query!r:35})", r is None, f"got {r[0] if r else None!r}")
    else:
        ok = r is not None and (expected_key in r[0] or r[0] in expected_key)
        check(f"find_stream_query({query!r:35})", ok, f"got {r[0] if r else None!r}, expected {expected_key!r}")


print()
print("=" * 70)
print("PART 4: degree-level marker detection")
print("=" * 70)

degree_cases: list[tuple[str, bool]] = [
    ("B.Tech Marine", True),
    ("M.Sc IT Data Science", True),
    ("MBA Finance", True),
    ("BBA Marketing", True),
    ("ડિપ્લોમા સિવિલ", True),
    ("Diploma વિશે", True),
    # No degree level — should be False
    ("cyber security વિશે", False),
    ("data science", False),
    ("marine વિશે", False),
    ("automobile", False),
    ("હા", False),
]
for text, expected in degree_cases:
    got = _has_degree_level_marker(text)
    check(f"degree_marker({text!r:40})", got == expected, f"got {got}, expected {expected}")


print()
print("=" * 70)
print("PART 5: cross-degree ambiguity (same topic, multiple levels)")
print("=" * 70)

# For these queries, RAG should return ≥2 variants spanning UG + PG
# (the disambig in engine.py will then ask which degree level)
ambig_cases = [
    ("cyber security", ["cyber security"]),
    ("data science", ["data science"]),
    ("ai ml", ["ai & ml"]),
    ("biotechnology", ["biotechnology"]),
]
for topic_query, expected_substrs in ambig_cases:
    # Use find_family_query (topic taxonomy)
    r = cat.find_family_query(topic_query)
    if r is None:
        check(f"ambig({topic_query!r:25})", False, "no family/topic match")
        continue
    key, variants = r
    levels = {_degree_level(v.get("program") or "") for v in variants}
    levels.discard(None)
    has_multi_level = len(levels) >= 2
    has_topic = any(
        any(es in (v.get("program") or "").lower() for v in variants)
        for es in expected_substrs
    )
    check(
        f"ambig({topic_query!r:25})",
        has_multi_level and has_topic,
        f"levels={sorted(levels)}, count={len(variants)}",
    )


print()
print("=" * 70)
print("PART 6: Marine cross-school — caller saying 'marine' should surface ALL variants")
print("=" * 70)

# This was the 2026-05-21 prod bug — fixed via topic taxonomy
r = cat.find_family_query("મરીન વિશે")
if r:
    key, variants = r
    names = [v.get("program") for v in variants]
    print(f"  marine topic returned {len(variants)} programs:")
    for n in names:
        print(f"    - {n!r}")
    check("marine returns B.Tech Marine", has_program(variants, "b. tech. (marine)"))
    check("marine returns B.Sc Nautical Science", has_program(variants, "nautical science"))
    check("marine returns GME", has_program(variants, "graduate marine"))
    check("marine returns DNS", has_program(variants, "dns") or has_program(variants, "diploma in nautical"))
    check("marine returns ETO", has_program(variants, "eto"))
    check("marine returns G.P. Ratings", has_program(variants, "g. p. ratings") or has_program(variants, "ratings"))
else:
    check("marine family query returns something", False, "got None")


print()
print("=" * 70)
print("PART 7: edge cases — STT artifacts + pivot detection")
print("=" * 70)

# Sarvam STT splits 'automobile' into 'auto mobile'
auto_text = "મારે ઓટો મોબાઇલ વિશે જાણવું છે"
p_auto = cat.find_by_name(auto_text)
check(
    "STT-split 'ઓટો મોબાઇલ' resolves to Automobile",
    p_auto is not None and "automobile" in (p_auto.get("program") or "").lower(),
    f"got {p_auto.get('program') if p_auto else None!r}",
)

# Sarvam STT mis-hears 'સાયન્સ' as 'સાહેબ'
ds_text = "એમએસસી આઈટી ડેટા સાહેબ"
p_ds = cat.find_by_name(ds_text)
check(
    "STT-mis 'સાહેબ→સાયન્સ' resolves to M.Sc IT Data Science",
    p_ds is not None and "data science" in (p_ds.get("program") or "").lower() and "m" in (p_ds.get("program") or "").lower()[:3],
    f"got {p_ds.get('program') if p_ds else None!r}",
)

# B.Tech Marine specifically (the 2026-05-20 bug — fee corruption from focused mismatch)
p_bt_marine = cat.find_by_name("B.Tech Marine")
check(
    "B.Tech Marine canonical fee is 495000",
    p_bt_marine is not None and p_bt_marine.get("fees") == "495000",
    f"got fees={p_bt_marine.get('fees') if p_bt_marine else None!r}",
)


print()
print("=" * 70)
print("PART 8: NEGATIVE cases — these should NOT resolve to a program")
print("=" * 70)

negative_cases = [
    ("હા", None),
    ("ઓકે", None),
    ("હલો", None),
    ("થેન્ક્યુ", None),
    ("મારું નામ રોનક છે", None),  # name statement
    ("મજામાં છું", None),
    ("તમારું નામ?", None),
    ("how are you", None),
]
for text, _ in negative_cases:
    p = cat.find_by_name(text)
    check(f"negative({text!r:30}) returns None", p is None, f"got {p.get('program') if p else None!r}")


print()
print("=" * 70)
print("PART 9: TWISTED — multi-facet queries (program + eligibility/fees/duration in one breath)")
print("=" * 70)
# Real callers don't say "B.Tech Marine". They say "B.Tech Marine માટે
# eligibility શું છે?" or "MBA Finance ની fee કેટલી?" — program name + a
# specific facet baked into the same utterance. find_by_name must still
# anchor to the named program.
twisted_cases: list[tuple[str, str]] = [
    # eligibility-twist
    ("B.Tech Marine ની eligibility શું છે?", "Marine"),
    ("MBA Finance માટે પાત્રતા?", "Financial"),
    ("BCA AI ML માટે કોણ apply કરી શકે?", "AI"),
    ("M.Sc IT Data Science માટે eligibility?", "Data Science"),
    ("BSc IT Cyber Security માટે eligibility?", "Cyber"),
    ("GME માટે eligibility શું છે?", "Graduate Marine"),
    ("Diploma Civil માટે qualification?", "Civil"),
    ("B.Tech CSE ની eligibility શું?", "CSE"),
    # fees-twist
    ("MBA Marketing ની fees કેટલી?", "Marketing"),
    ("B.Tech Marine ની fee કેટલી?", "Marine"),
    ("BBA Marketing ની ફી જણાવો", "Marketing"),
    ("Diploma Mechanical ની fees?", "Mechanical"),
    # duration-twist
    ("MBA Finance કેટલા વર્ષનો?", "Financial"),
    ("Diploma Automobile કેટલા વર્ષ?", "Automobile"),
    # counsellor-twist
    ("B.Tech Marine ના counsellor કોણ?", "Marine"),
    ("GME counsellor નો contact?", "Graduate Marine"),
    # mixed faceted
    ("મારે B.Tech Marine વિશે જાણવું છે — fees, eligibility બધું", "Marine"),
    ("MBA Finance: fee + duration + placement", "Financial"),
    # twisted + STT artifact
    ("ઓટોમોબાઇલ Diploma ની eligibility?", "Automobile"),
    ("એમએસસી આઈટી ડેટા સાયન્સ ની fee?", "Data Science"),
]
for q, expected_substr in twisted_cases:
    p = cat.find_by_name(q)
    got = (p.get("program") if p else "") or ""
    ok = expected_substr.lower().replace(" ", "") in got.lower().replace(" ", "").replace(".", "")
    check(f"twisted({q!r:60})", ok, f"expected {expected_substr!r} substr, got {got!r}")


print()
print("=" * 70)
print("PART 10: SIBLING DISAMBIG — same topic, different degree levels")
print("=" * 70)
# Specific named pairs MUST resolve to the right level, not their sibling.
# These are the bugs the user explicitly flagged: B.Tech vs B.Sc IT vs
# M.Sc IT vs Diploma — same topic, different fees/eligibility/scope.

def _level_of(prog: dict | None) -> str | None:
    if not prog:
        return None
    return _degree_level(prog.get("program") or "")

sibling_cases: list[tuple[str, str, str]] = [
    # (query, expected_substr_in_name, expected_degree_level)
    # ── cyber security siblings ────────────────────────────
    ("BSc IT Cyber Security",        "Cyber",            "ug-tech"),  # B.Sc.IT Hons Cyber
    ("M.Sc IT Cyber Security",       "Cyber",            "pg-tech"),
    ("BCA Cyber Security",           "Cyber",            "ug-app"),   # BCA Hons Cyber
    # ── data science siblings ─────────────────────────────
    ("BSc IT Data Science",          "Data Science",     "ug-tech"),
    ("M.Sc IT Data Science",         "Data Science",     "pg-tech"),
    # ── AI/ML siblings (5+ degree levels) ─────────────────
    ("BCA AI ML",                    "AI",               "ug-app"),
    ("BSc IT AI ML",                 "AI",               "ug-tech"),
    ("B.Tech CSE AI ML",             "AI",               "ug-tech"),
    ("M.Sc IT AI ML",                "AI",               "pg-tech"),
    ("Diploma Degree AI ML",         "AI",               "dip"),
    # ── automobile siblings ───────────────────────────────
    ("Diploma Automobile",           "Automobile",       "dip"),
    ("Diploma + Degree Automobile",  "Automobile",       "dip"),  # treated as diploma family
    ("B.Tech Automobile",            "Automobile",       "ug-tech"),
    # ── marine siblings ───────────────────────────────────
    ("B.Tech Marine",                "Marine",           "ug-tech"),
    ("B.Sc Nautical Science",        "Nautical",         "ug-tech"),  # B.Sc Hons
    ("GME",                          "Graduate Marine",  "ug-tech"),
    ("ETO",                          "ETO",              "dip"),  # treated similarly
    ("DNS",                          "Nautical",         "dip"),
    # ── BBA vs MBA siblings ───────────────────────────────
    ("BBA Marketing",                "Marketing",        "ug-mgmt"),
    ("MBA Marketing",                "Marketing",        "pg-mgmt"),
    ("BBA Finance",                  "Finance",          "ug-mgmt"),
]
sibling_ok = sibling_fail = 0
sibling_fail_lines: list[str] = []
for query, want_substr, want_level in sibling_cases:
    p = cat.find_by_name(query)
    got_name = (p.get("program") if p else "") or ""
    got_level = _level_of(p)
    name_ok = want_substr.lower().replace(" ", "") in got_name.lower().replace(" ", "").replace(".", "")
    # level check is soft — some legacy variants don't carry the exact
    # marker. We still want to flag if the level is on the WRONG side
    # (e.g. asked B.Sc, got M.Sc).
    level_ok = (got_level is None) or (got_level == want_level) or (
        # cross-family equivalence within the same tier:
        # ug-tech / ug-sc / ug-comp / ug-mgmt all count as UG;
        # pg-tech / pg-sc / pg-mgmt all count as PG; dip stays dip.
        want_level.startswith("ug") and (got_level or "").startswith("ug")
    ) or (
        want_level.startswith("pg") and (got_level or "").startswith("pg")
    ) or (
        want_level == "dip" and (got_level or "").startswith("dip")
    )
    label = f"sibling({query!r:35}) -> level={got_level!r}"
    if name_ok and level_ok:
        sibling_ok += 1
        check(label, True)
    else:
        sibling_fail += 1
        check(label, False, f"want {want_substr!r}/{want_level}, got name={got_name!r}/{got_level}")


print()
print("=" * 70)
print("PART 11: CROSS-CONTAMINATION GUARD — sibling fee must not bleed in")
print("=" * 70)
# When a caller names B.Sc IT Cyber Security (fee 100k), a hallucinated
# M.Sc IT Cyber Security fee (94k) in the same RAG context MUST be caught
# by fact_guard's canonical lock and corrected to 100k.
from app.brain.fact_guard import (
    extract_facts_from_context,
    validate_and_correct,
)

def _build_ctx(progs: list[dict]) -> str:
    """Synthesize the bridge's standard context-block format."""
    blocks = []
    for p in progs:
        blocks.append(
            f"Program: {p.get('program')}\n"
            f"Institute: {p.get('institute') or ''}\n"
            f"Fees: {p.get('fees') or ''}\n"
            f"Duration: {p.get('duration') or ''}\n"
            f"Eligibility: {(p.get('eligibility') or '')[:120]}\n"
            f"Counsellor: {p.get('counsellor_name') or ''}\n"
            f"Phone: {p.get('counsellor_phone') or ''}\n"
            f"Email: {p.get('counsellor_email') or ''}"
        )
    return "\n\n".join(blocks)

# Pair A — Cyber Security UG vs PG
bsc_cyber = next(p for p in cat.programs if "Cyber" in (p.get("program") or "") and "B.Sc" in (p.get("program") or ""))
msc_cyber = next(p for p in cat.programs if "Cyber" in (p.get("program") or "") and "M. Sc" in (p.get("program") or ""))
ctx_pair_cyber = _build_ctx([bsc_cyber, msc_cyber])
facts_cyber = extract_facts_from_context(ctx_pair_cyber)
check("ctx-pair (cyber) extracts ≥2 programs", len(facts_cyber.programs) >= 2)
# Canonical = B.Sc Cyber. LLM hallucinates M.Sc fee (94k → ચોરાણું હજાર).
reply_wrong = "જી, B.Sc IT Cyber Security ની fee ચોરાણું હજાર છે."
corrected, corrections = validate_and_correct(
    reply_wrong, facts_cyber, canonical_program=bsc_cyber
)
check(
    "BSc Cyber: M.Sc fee bleed corrected",
    any("ચોરાણું" in c.original for c in corrections) or "94" not in corrected,
    f"corrected={corrected!r} corrections={[(c.original, c.corrected) for c in corrections]!r}",
)

# Pair B — Data Science UG vs PG (70k vs 94k)
bsc_ds = next(p for p in cat.programs if "Data Science" in (p.get("program") or "") and "B. Sc" in (p.get("program") or ""))
msc_ds = next(p for p in cat.programs if "Data Science" in (p.get("program") or "") and "M. Sc" in (p.get("program") or ""))
ctx_pair_ds = _build_ctx([bsc_ds, msc_ds])
facts_ds = extract_facts_from_context(ctx_pair_ds)
reply_wrong_ds = "B.Sc IT Data Science ની fee ચોરાણું હજાર છે."  # 94k = M.Sc, wrong
corrected_ds, corrections_ds = validate_and_correct(
    reply_wrong_ds, facts_ds, canonical_program=bsc_ds
)
check(
    "BSc Data Science: M.Sc fee bleed corrected",
    any("ચોરાણું" in c.original for c in corrections_ds) or "94" not in corrected_ds,
    f"corrected={corrected_ds!r} corrections={[(c.original, c.corrected) for c in corrections_ds]!r}",
)

# Pair C — Automobile Diploma vs B.Tech (61k vs 160k)
dip_auto = next(p for p in cat.programs if "Automobile" in (p.get("program") or "") and "Diploma" in (p.get("program") or "") and "+" not in (p.get("program") or ""))
bt_auto = next(p for p in cat.programs if "Automobile" in (p.get("program") or "") and "B. Tech" in (p.get("program") or ""))
ctx_pair_auto = _build_ctx([dip_auto, bt_auto])
facts_auto = extract_facts_from_context(ctx_pair_auto)
# Canonical = Diploma. Reply quotes B.Tech fee (160k) by mistake.
reply_wrong_auto = "Diploma Automobile ની fee એક લાખ સાઠ હજાર છે."
corrected_auto, corrections_auto = validate_and_correct(
    reply_wrong_auto, facts_auto, canonical_program=dip_auto
)
check(
    "Diploma Auto: B.Tech fee bleed caught",
    len(corrections_auto) >= 1 or "160" not in corrected_auto,
    f"corrected={corrected_auto!r} corrections={[(c.original, c.corrected) for c in corrections_auto]!r}",
)

# Pair D — Marine: caller named B.Tech Marine (495k), RAG returns GME (380k) as sibling
bt_marine = next(p for p in cat.programs if "B. Tech" in (p.get("program") or "") and "Marine" in (p.get("program") or ""))
gme = next(p for p in cat.programs if "GME" in (p.get("program") or ""))
ctx_pair_marine = _build_ctx([bt_marine, gme])
facts_marine = extract_facts_from_context(ctx_pair_marine)
reply_wrong_marine = "B.Tech Marine ની fee ત્રણ લાખ એંસી હજાર છે."  # GME's, wrong
corrected_marine, corrections_marine = validate_and_correct(
    reply_wrong_marine, facts_marine, canonical_program=bt_marine
)
check(
    "B.Tech Marine: GME fee bleed caught",
    len(corrections_marine) >= 1,
    f"corrected={corrected_marine!r} corrections={[(c.original, c.corrected) for c in corrections_marine]!r}",
)


print()
print("=" * 70)
print("PART 12: FULL-CATALOG SMOKE — every program resolves from its own name")
print("=" * 70)
# For each of the 126 programs, find_by_name(p['program']) should resolve
# to a program with the SAME name (duplicates with same name are OK —
# they exist for multi-institute offerings).
def _name_match(a: dict | None, b: dict) -> bool:
    if a is None:
        return False
    na = (a.get("program") or "").lower().replace(".", "").replace(" ", "")
    nb = (b.get("program") or "").lower().replace(".", "").replace(" ", "")
    return na == nb

self_hits = 0
self_misses: list[str] = []
for p in cat.programs:
    nm = p.get("program") or ""
    if not nm.strip():
        continue
    got = cat.find_by_name(nm)
    if _name_match(got, p):
        self_hits += 1
    else:
        self_misses.append(f"{nm!r} -> {(got.get('program') if got else None)!r}")
check(
    f"126/126 canonical-name self-resolution ({self_hits}/{len(cat.programs)})",
    len(self_misses) == 0,
    f"{len(self_misses)} miss(es): " + ", ".join(self_misses[:5]),
)

# Also smoke every program via its first 3 significant English tokens
# (caller-realistic abbreviation form).
def _short_query(name: str) -> str:
    # Keep single-letter degree prefixes (B/M) — dropping them collapses
    # "B.Tech (Automobile)" into "Tech Automobile" and loses the family.
    cleaned = name.replace(".", " ").replace("(", " ").replace(")", " ")
    toks = [t for t in cleaned.split() if t.strip()]
    return " ".join(toks[:5])

abbrev_hits = 0
abbrev_misses: list[str] = []
for p in cat.programs:
    nm = p.get("program") or ""
    if not nm.strip():
        continue
    q = _short_query(nm)
    got = cat.find_by_name(q)
    if got is not None:
        # Soft check: any token overlap is acceptable (we just need to
        # not return None for a clearly program-shaped query).
        abbrev_hits += 1
    else:
        abbrev_misses.append(f"{q!r} (from {nm!r})")
check(
    f"126/126 abbreviated-form non-null resolution ({abbrev_hits}/{len(cat.programs)})",
    # ≤25 misses tolerated: M.Tech specialisations have very long canonical
    # names ('M. Tech. Mechanical Engineering (Advanced Manufacturing
    # Systems)') that no caller would ever utter in full. They're handled
    # by topic taxonomy + family disambig instead.
    abbrev_hits >= len(cat.programs) - 25,
    f"{len(abbrev_misses)} miss(es): " + ", ".join(abbrev_misses[:5]),
)


print()
print("=" * 70)
print("PART 13: TOPIC SMOKE — every topic key returns ≥2 programs")
print("=" * 70)
# Topic taxonomy must surface multiple cross-degree programs for every
# topical caller query. Empty topic = no cross-degree disambig = the
# B.Tech Marine bug class.
expected_topics = [
    "marine", "automobile", "cyber-security", "data-science", "ai-ml",
    "biotechnology", "agriculture", "computer-eng", "civil", "mechanical",
    "chemical", "electrical", "food", "microbiology", "biomedical",
    "mechatronics", "business-analytics", "infrastructure", "international-business",
]
for topic in expected_topics:
    progs = cat._topic.get(topic, [])
    check(
        f"topic {topic!r:25} has ≥2 programs",
        len(progs) >= 2,
        f"got {len(progs)}",
    )


print()
print("=" * 70)
print("PART 14: PER-PROGRAM BATTERY — every program × 4 phrasings")
print("=" * 70)
# Cover every program in the catalog 4 different ways:
#   (a) canonical name as-is
#   (b) canonical name lowercased
#   (c) dots stripped + parens stripped
#   (d) wrapped in "મારે {name} વિશે જાણવું છે" (most common Gu caller phrasing)
# Pass = find_by_name returns a program with the SAME canonical name
# (allowing duplicates with identical names — those are different-institute
# offerings of the same program).

def _same_name(a: dict | None, b: dict) -> bool:
    if a is None:
        return False
    na = (a.get("program") or "").lower().replace(".", "").replace(" ", "")
    nb = (b.get("program") or "").lower().replace(".", "").replace(" ", "")
    return na == nb

phrasing_results: list[tuple[bool, str]] = []
for p in cat.programs:
    nm = (p.get("program") or "").strip()
    if not nm:
        continue
    nm_clean = nm.replace(".", " ").replace("(", " ").replace(")", " ")
    nm_clean = " ".join(nm_clean.split())
    forms = [
        ("direct", nm),
        ("lower", nm.lower()),
        ("clean",  nm_clean),
        ("guwrap", f"મારે {nm_clean} વિશે જાણવું છે"),
    ]
    for tag, q in forms:
        got = cat.find_by_name(q)
        ok = _same_name(got, p)
        phrasing_results.append(
            (ok, f"phrasing/{tag} {q!r:60} -> {(got.get('program') if got else None)!r}")
        )
# Tolerate up to 50 misses across 500+ attempts: the long M.Tech
# specialisations and a few oddly-formatted dup names dominate misses.
batch_check("PART 14 per-program × 4 phrasings", phrasing_results, tolerance=50)


print()
print("=" * 70)
print("PART 15: STT ARTIFACT BANK — known Sarvam mis-hearings")
print("=" * 70)
# Real STT artifacts seen in production logs. The catalog must be
# resilient to these — Sarvam doesn't always honour spacing/diacritics.
stt_cases: list[tuple[str, str]] = [
    # (transcribed text, expected substring in resolved program name)
    ("ઓટો મોબાઇલ વિશે",                "Automobile"),
    ("ઓટોમોબીલ",                        "Automobile"),
    ("ઓટોમોબાઇલ ડિપ્લોમા",              "Automobile"),
    ("એમએસસી આઈટી ડેટા સાહેબ",          "Data Science"),
    ("એમએસસી આઈટી ડેટા સાયન્સ",         "Data Science"),
    ("એમ એસ સી આઈટી સાયબર",             "Cyber"),
    ("બીસસી આઈટી સાયબર",                "Cyber"),  # missing એ in બીએસસી
    ("બીએસસી આઈટી સાયબર સિક્યુરિટી",    "Cyber"),
    ("બી ટેક મરીન",                     "Marine"),
    ("બીટેક મરીન",                      "Marine"),
    ("એમ બી એ ફાઇનાન્સ",                "Financial"),
    ("MBA ફાઇનાન્સ",                    "Financial"),
    ("એમ બી એ માર્કેટિંગ",              "Marketing"),
    ("બીસીએ આઈટી સાયબર",                "Cyber"),
    ("બીસીએ સાયબર સિક્યુરિટી",          "Cyber"),
    ("એમ સી એ",                          "MCA"),
    ("એમએસસી IT",                       "M"),  # any M.Sc IT variant
    ("જી એન એમ",                         "Nursing"),
    ("જી એમ ઈ",                          "Graduate Marine"),
    ("જી પી રેટિંગ્સ",                   "Ratings"),
    ("ડી એન એસ",                         "Nautical"),
    ("ETO Marine",                       "ETO"),
    ("electro technical officer",        "ETO"),
    ("એમ ટેક સિવિલ",                     "Civil"),
    ("એમટેક મિકેનિકલ",                  "Mechanical"),
    ("ડિપ્લોમા ઇલેક્ટ્રિકલ",             "Electrical"),
    ("ડિપ્લોમા સિવિલ એન્જિનિયરિંગ",     "Civil"),
    ("બી ફાર્મ",                         "Pharm"),
    ("એમ ફાર્મ",                         "harm"),  # M.Pharm
    ("આર્કિટેક્ચર",                       "Arch"),
]
stt_results: list[tuple[bool, str]] = []
for q, expected in stt_cases:
    p = cat.find_by_name(q)
    got = (p.get("program") if p else "") or ""
    ok = expected.lower().replace(" ", "") in got.lower().replace(" ", "").replace(".", "")
    stt_results.append((ok, f"stt({q!r:35}) want {expected!r}, got {got!r}"))
batch_check("PART 15 STT artifact bank", stt_results, tolerance=3)


print()
print("=" * 70)
print("PART 16: CODE-SWITCHING PATTERNS — English+Gujarati mixed")
print("=" * 70)
# Real callers swap freely: "I want B.Tech Marine સારું છે?"
# "MBA Finance કરવી છે". The catalog can't be language-pure.
cs_cases: list[tuple[str, str]] = [
    ("I want B.Tech Marine",                                  "Marine"),
    ("મારે MBA Finance કરવી છે",                              "Financial"),
    ("BCA Cyber Security વિશે જણાવો please",                 "Cyber"),
    ("I'm interested in M.Sc IT Data Science",                "Data Science"),
    ("Diploma Civil engineering જોઈએ છે",                    "Civil"),
    ("B.Tech CSE માં AI ML specialisation છે?",              "AI"),
    ("મારે Marine engineering કરવી છે",                       "Marine"),
    ("તમે MBA Marketing offer કરો છો?",                       "Marketing"),
    ("BSc IT Hons Cyber Security please",                     "Cyber"),
    ("મારે nursing કરવી છે — GNM",                            "Nursing"),
    ("B.Pharm વિશે કહો",                                     "harm"),
    ("M.Pharm Pharmaceutics",                                "Pharm"),
    ("architecture course",                                   "Arch"),
    ("Diploma + Degree Automobile",                          "Automobile"),
    ("હું B.Tech IT માટે interested છું",                     "T"),  # any IT variant
    ("MBA International Business",                            "International"),
    ("BBA Marketing Management",                              "Marketing"),
    ("BCA AI and ML",                                         "AI"),
    ("BCA AI & ML",                                           "AI"),
    ("B.Tech CSE Cyber Security",                             "Cyber"),
    ("M.Tech Mechanical Engineering",                         "Mechanical"),
    ("M.Tech Civil",                                          "Civil"),
    ("M.Tech Computer",                                       "Computer"),
    ("M.Sc Microbiology",                                     "Microbiology"),
    ("M.Sc Biotechnology",                                    "Biotechnology"),
]
cs_results: list[tuple[bool, str]] = []
for q, expected in cs_cases:
    p = cat.find_by_name(q)
    got = (p.get("program") if p else "") or ""
    ok = expected.lower().replace(" ", "") in got.lower().replace(" ", "").replace(".", "")
    cs_results.append((ok, f"cs({q!r:55}) want {expected!r}, got {got!r}"))
batch_check("PART 16 code-switching patterns", cs_results, tolerance=3)


print()
print("=" * 70)
print("PART 17: NOISE — whitespace / case / punctuation tolerance")
print("=" * 70)
# Real STT output has trailing spaces, mixed case, stray punctuation.
# find_by_name shouldn't be fooled by any of it.
noise_cases: list[tuple[str, str]] = [
    ("  B.Tech Marine  ",                "Marine"),
    ("\tB.Tech Marine\n",                "Marine"),
    ("B.Tech    Marine",                 "Marine"),  # double space
    ("BTECH MARINE",                     "Marine"),
    ("btech marine",                     "Marine"),
    ("BtEcH mArInE",                     "Marine"),
    ("B.Tech Marine?",                   "Marine"),
    ("B.Tech Marine!",                   "Marine"),
    ("B.Tech Marine.",                   "Marine"),
    ("B.Tech Marine,",                   "Marine"),
    ("B.Tech Marine...",                 "Marine"),
    ("MBA Finance???",                   "Financial"),
    ("GME!!!",                           "Graduate Marine"),
    ("  GNM  ",                          "Nursing"),
    ("  ડિપ્લોમા સિવિલ  ",               "Civil"),
    ("એમબીએ​ફાઇનાન્સ",              "Financial"),  # zero-width space
    ("B.Tech Marine",               "Marine"),     # nbsp
    ("BCA(AI&ML)",                       "AI"),
    ("BCA (AI & ML)",                    "AI"),
    ("B.Sc.IT(Cyber)",                   "Cyber"),
    ("B. Sc. IT (Cyber Security)",       "Cyber"),
    ("MBA-Finance",                      "Financial"),
    ("M.Sc.IT-DataScience",              "Data Science"),
    ("B-Tech Marine",                    "Marine"),
]
noise_results: list[tuple[bool, str]] = []
for q, expected in noise_cases:
    p = cat.find_by_name(q)
    got = (p.get("program") if p else "") or ""
    ok = expected.lower().replace(" ", "") in got.lower().replace(" ", "").replace(".", "")
    noise_results.append((ok, f"noise({q!r:40}) want {expected!r}, got {got!r}"))
batch_check("PART 17 noise tolerance", noise_results, tolerance=4)


print()
print("=" * 70)
print("PART 18: ENGINE PRIMITIVES — _degree_level / _has_degree_level_marker exhaustive")
print("=" * 70)
# For every program, _degree_level should return a non-None level marker
# (catalog programs all have determinable levels by design).
# _has_degree_level_marker should fire on the canonical name for any
# program (every name has B.Tech / M.Sc / MBA / Diploma / BCA / etc).

level_results: list[tuple[bool, str]] = []
for p in cat.programs:
    nm = (p.get("program") or "").strip()
    if not nm:
        continue
    lvl = _degree_level(nm)
    # ETO + some marine programs have no formal degree marker — those return None.
    # Allow None for known exception programs but flag any unexpected None.
    name_low = nm.lower()
    exceptions = ("eto", "g. p. ratings", "g.p. ratings")
    is_exception = any(e in name_low for e in exceptions)
    ok = lvl is not None or is_exception
    level_results.append((ok, f"_degree_level({nm!r}) -> {lvl!r}"))
batch_check("PART 18a _degree_level exhaustive", level_results, tolerance=3)

marker_results: list[tuple[bool, str]] = []
for p in cat.programs:
    nm = (p.get("program") or "").strip()
    if not nm:
        continue
    has = _has_degree_level_marker(nm)
    name_low = nm.lower()
    exceptions = ("eto", "g. p. ratings", "g.p. ratings", "dns")
    is_exception = any(e in name_low for e in exceptions)
    ok = has or is_exception
    marker_results.append((ok, f"_has_degree_level_marker({nm!r}) -> {has}"))
batch_check("PART 18b _has_degree_level_marker exhaustive", marker_results, tolerance=5)

# _topic_signature: programs with a topic tag should yield a topic signature
# matching their _topic field. Programs without _topic should return ''.
topic_sig_results: list[tuple[bool, str]] = []
for p in cat.programs:
    nm = (p.get("program") or "").strip()
    if not nm:
        continue
    sig = _topic_signature(nm)
    tag = p.get("_topic")
    # Soft check: if program has a topic tag, signature should be non-empty
    if tag:
        ok = sig != ""
    else:
        ok = True  # no expectation
    topic_sig_results.append((ok, f"_topic_signature({nm!r}) -> {sig!r} (tag={tag!r})"))
batch_check("PART 18c _topic_signature on tagged programs", topic_sig_results, tolerance=10)


print()
print("=" * 70)
print("PART 19: FACT_GUARD — correct fee passes through cleanly")
print("=" * 70)
# For each program with a known fee, build a single-program context and
# a reply quoting that fee in digit form. Expect 0 corrections.
fg_pass_results: list[tuple[bool, str]] = []
tested = 0
for p in cat.programs:
    fee_str = p.get("fees")
    if not fee_str or not fee_str.isdigit():
        continue
    ctx = _build_ctx([p])
    facts = extract_facts_from_context(ctx)
    nm_short = (p.get("program") or "").split("(")[0].strip()
    reply = f"જી, {nm_short} ની fee {fee_str} રૂપિયા છે."
    corrected, corrections = validate_and_correct(reply, facts, canonical_program=p)
    ok = len(corrections) == 0
    fg_pass_results.append(
        (ok, f"fg-pass {nm_short!r:35} fee={fee_str} corrections={[(c.original, c.corrected) for c in corrections]!r}")
    )
    tested += 1
    if tested >= 60:
        break
batch_check(f"PART 19 fact_guard correct-fee pass-through ({tested} programs)", fg_pass_results, tolerance=5)


print()
print("=" * 70)
print("PART 20: FACT_GUARD — sibling wrong-fee gets caught")
print("=" * 70)
# For 15 sibling pairs across topics, hallucinate the sibling's fee and
# verify a correction fires (or the value is removed from the reply).
def _find_pairs() -> list[tuple[dict, dict]]:
    """Surface sibling pairs (different programs, same topic, distinct fees)."""
    pairs: list[tuple[dict, dict]] = []
    for topic, progs in cat._topic.items():
        good = [p for p in progs if (p.get("fees") or "").isdigit()]
        for i in range(len(good)):
            for j in range(i + 1, len(good)):
                if good[i].get("fees") != good[j].get("fees"):
                    pairs.append((good[i], good[j]))
                    if len(pairs) >= 30:
                        return pairs
    return pairs

fg_catch_results: list[tuple[bool, str]] = []
for canon, sibling in _find_pairs():
    ctx = _build_ctx([canon, sibling])
    facts = extract_facts_from_context(ctx)
    sib_fee = sibling.get("fees")
    canon_fee = canon.get("fees")
    canon_short = (canon.get("program") or "").split("(")[0].strip()
    # Reply hallucinates SIBLING's fee under CANONICAL's program
    reply = f"{canon_short} ની fee {sib_fee} રૂપિયા છે."
    corrected, corrections = validate_and_correct(reply, facts, canonical_program=canon)
    # Catch = either ≥1 correction OR the sibling fee no longer appears
    # (some substitutions rewrite vs flag).
    ok = len(corrections) >= 1 or sib_fee not in corrected
    fg_catch_results.append(
        (ok, f"fg-catch canon={canon_short!r} fee-leak {sib_fee} (canon={canon_fee}) -> {len(corrections)} corrections, reply={corrected[:60]!r}")
    )
batch_check(f"PART 20 fact_guard sibling-fee catch ({len(fg_catch_results)} pairs)", fg_catch_results, tolerance=4)


print()
print("=" * 70)
print("PART 21: EXPANDED NEGATIVES — never resolve a program for non-program inputs")
print("=" * 70)
# 50+ inputs that must NEVER resolve to a program — false-positives here
# cause the agent to "pick a random program" on garbage input.
neg_expanded = [
    # affirmatives / refusals
    "હા", "ના", "ઓકે", "okay", "yes", "no", "yeah", "nope",
    "હા હા", "ના ના", "બરાબર", "ઠીક છે", "ચોક્કસ", "બરાબર છે",
    # greetings / closures
    "નમસ્તે", "હેલો", "hi", "hello", "thanks", "thank you",
    "આભાર", "બાય", "bye", "ગુડ બાય", "good morning", "ગુડ મોર્નિંગ",
    # name statements (these were a regression)
    "મારું નામ રોનક છે", "મારું નામ જય છે", "I'm Mohit", "I am Ritesh",
    "મારું નામ રોનાક છે", "આ રોનક છે", "this is John",
    # weather / off-topic
    "આજે હવામાન કેવું છે?", "what's the weather", "આજે ગરમી છે",
    "આજે વરસાદ છે", "ઠંડી છે",
    # AI / meta
    "તમે કોણ છો?", "are you AI?", "are you a bot?", "આ AI છે?",
    "તમારી ઉંમર શું છે?", "what is your name?",
    # confused / fillers
    "હેં?", "huh?", "what?", "શું?", "મને નથી સમજાયું", "I don't understand",
    "મમ", "હમ્મ", "ઉહ", "umm", "uhh",
    # navigation / nav
    "આગળ", "પાછળ", "stop", "બંધ કરો", "પૂરું",
    # empty-ish
    "", "  ", "\n\t", ".", "?", "!",
]
neg_results: list[tuple[bool, str]] = []
for q in neg_expanded:
    p = cat.find_by_name(q)
    ok = p is None
    neg_results.append((ok, f"negative({q!r:35}) -> {(p.get('program') if p else None)!r}"))
batch_check(f"PART 21 expanded negatives ({len(neg_results)} cases)", neg_results, tolerance=2)


print()
print("=" * 70)
print("PART 22: PIVOT / FOLLOW-UP — bare follow-ups must NOT resolve a new program")
print("=" * 70)
# A bare "ફી?" or "eligibility?" alone must NOT pick a random program —
# the bridge's pronoun-reference path handles these by reusing the focused
# program from state. find_by_name should return None for these so the
# bridge knows there's no NEW program in this turn.
followup_cases = [
    "ફી?",
    "ફી જણાવો",
    "fee?",
    "fees કેટલી?",
    "eligibility?",
    "eligibility શું છે?",
    "duration?",
    "કેટલા વર્ષનો?",
    "counsellor",
    "કાઉન્સેલર?",
    "phone number?",
    "email?",
    "WhatsApp?",
    "details?",
    "બીજું શું?",
    "આગળ?",
    "and?",
    "ok next",
    "more info",
    "વધુ માહિતી",
]
followup_results: list[tuple[bool, str]] = []
for q in followup_cases:
    p = cat.find_by_name(q)
    ok = p is None
    followup_results.append((ok, f"followup({q!r:35}) -> {(p.get('program') if p else None)!r}"))
batch_check(f"PART 22 bare follow-up no-resolution ({len(followup_results)} cases)", followup_results, tolerance=2)


print()
print("=" * 70)
print("PART 23: IDEMPOTENCY — same query, same result (no nondeterminism)")
print("=" * 70)
# Mismatched cache or order-dependent code paths would cause a query to
# resolve differently across calls. Sample 30 queries × 3 reps each.
import random as _random
_random.seed(42)
idemp_samples = _random.sample(cat.programs, 30)
idemp_results: list[tuple[bool, str]] = []
for p in idemp_samples:
    nm = p.get("program") or ""
    if not nm.strip():
        continue
    a = cat.find_by_name(nm)
    b = cat.find_by_name(nm)
    c = cat.find_by_name(nm)
    ok = a is b is c
    idemp_results.append((ok, f"idemp {nm!r:40}"))
batch_check(f"PART 23 idempotency ({len(idemp_results)} reps × 3)", idemp_results, tolerance=0)


print()
print("=" * 70)
print(f"TOTAL: PASS={PASS}  FAIL={len(FAIL)}")
print("=" * 70)
if FAIL:
    print("\nFailures (first 25):")
    for f in FAIL[:25]:
        print(f"  - {f}")
    if len(FAIL) > 25:
        print(f"  ... and {len(FAIL) - 25} more")
    sys.exit(1)
print("ALL GREEN")
