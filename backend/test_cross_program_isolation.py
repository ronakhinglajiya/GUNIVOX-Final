"""Cross-program isolation regression suite (Guardrails A + B + C).

Run before every backend restart that touches engine.py / fact_guard.py /
catalog.py. Exercises the 11 failure modes we've seen + the topic + family
combinations that haven't been hit yet.

Usage:
    cd backend
    ../venv/Scripts/python.exe test_cross_program_isolation.py

Each case asserts:
  1. The override path taken (single-source / family / topic / ASK_DEGREE).
  2. fact_canonical is the expected program (or None for browse mode).
  3. fact_guard would NOT cross-contaminate fees.
"""
from __future__ import annotations

import sys

from app.brain.catalog import get_catalog, format_program_context
from app.brain.engine import (
    _has_degree_level_marker,
    _topic_signature,
    _degree_level,
)
from app.brain.fact_guard import (
    extract_facts_from_context,
    validate_and_correct,
)


cat = get_catalog()


def _simulate_canonical_lock(user_text: str, focused: dict | None) -> tuple[bool, dict | None]:
    """Mirror the engine.py single-source-lock decision: True iff
    focused_program is set AND the user's current utterance names that
    same program via find_by_name."""
    if focused is None:
        return False, None
    user_match = cat.find_by_name(user_text)
    if user_match is None:
        return False, focused
    same_program = (
        (user_match.get("program") or "").strip().lower()
        == (focused.get("program") or "").strip().lower()
    )
    return same_program, focused


def _build_focused_context(focused: dict) -> str:
    """Single-source-lock context = ONE program record."""
    return format_program_context(focused)


def _build_browse_context(progs: list[dict]) -> str:
    """Browse mode context = many program records (family/topic listing)."""
    return "\n".join(format_program_context(p) for p in progs)


# ─── Test cases ─────────────────────────────────────────────────────────

PASS = 0
FAIL: list[str] = []


def assert_case(label: str, condition: bool, why: str = "") -> None:
    global PASS
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL.append(f"{label}: {why}")
        print(f"  [FAIL] {label} -- {why}")


# Resolve a few canonical program records up front (by exact catalog name)
def _by_name(needle: str) -> dict:
    for p in cat.programs:
        if (p.get("program") or "").strip() == needle:
            return p
    raise KeyError(f"program not in catalog: {needle!r}")

marine_btech = _by_name("B. Tech. (Marine)")
gme          = _by_name("Graduate Marine Engineering (GME)")
auto_btech   = _by_name("B. Tech. (Automobile)")
auto_dip     = _by_name("Diploma (Automobile)")
bsc_ds       = _by_name("B. Sc. IT  (Hons.) (Data Science)")
msc_ds       = _by_name("M. Sc. IT (Data Science)")
mba_logistics = next(p for p in cat.programs if "Logistics" in (p.get("program") or "") and "MBA" in (p.get("program") or ""))


# ── 1. B.Tech Marine specific: single-source lock → Marine only ─────────
print("Case 1: B.Tech Marine specific")
user = "મને B.Tech Marine વિશે કહો"
locked, focused = _simulate_canonical_lock(user, marine_btech)
assert_case("single_source_lock fires", locked)
ctx = _build_focused_context(marine_btech)
facts = extract_facts_from_context(ctx)
fixed, corr = validate_and_correct(
    "B.Tech Marine માટે ફી 4 લાખ 95 હજાર પ્રતિ વર્ષ છે.",
    facts,
    canonical_program=marine_btech,
)
assert_case("correct fee passes through", not corr)
# Verify the hallucinated GME fee gets caught
fixed_bad, corr_bad = validate_and_correct(
    "B.Tech Marine માટે ફી 3 લાખ 80 હજાર છે.",
    facts,
    canonical_program=marine_btech,
)
assert_case(
    "hallucinated sibling fee caught",
    bool(corr_bad) and corr_bad[0].corrected == "ચાર લાખ પંચાણું હજાર",
    f"got {corr_bad}",
)


# ── 2. GME specific: single-source lock → GME only ──────────────────────
print("Case 2: GME specific")
user = "GME વિશે કહો"
locked, focused = _simulate_canonical_lock(user, gme)
assert_case("single_source_lock fires", locked)
ctx = _build_focused_context(gme)
facts = extract_facts_from_context(ctx)
fixed, corr = validate_and_correct(
    "GME માટે ફી 3 લાખ 80 હજાર છે.",
    facts,
    canonical_program=gme,
)
assert_case("correct GME fee passes", not corr)


# ── 3. Broad "marine" query: topic taxonomy → 6 programs ────────────────
print("Case 3: broad marine query (topic)")
r = cat.find_family_query("મરીન વિશે")
assert_case("find_family_query returns marine topic", r is not None and r[0] == "marine")
assert_case(
    "marine topic has all 6 cross-degree programs",
    bool(r) and len(r[1]) == 6,
    f"got {len(r[1]) if r else 0}",
)


# ── 4. Broad "automobile" query: topic taxonomy → 3 programs ────────────
print("Case 4: broad automobile query (topic)")
r = cat.find_family_query("automobile વિશે")
assert_case("find_family_query returns automobile topic", r is not None and r[0] == "automobile")
assert_case(
    "automobile topic has all 3 programs",
    bool(r) and len(r[1]) == 3,
    f"got {len(r[1]) if r else 0}",
)


# ── 5. B.Tech Automobile specific: single-source lock ───────────────────
print("Case 5: B.Tech Automobile specific")
user = "બીટેક automobile વિશે"
locked, focused = _simulate_canonical_lock(user, auto_btech)
assert_case("single_source_lock fires", locked)


# ── 6. "Cyber Security" no-degree: ASK_DEGREE mode (no fees in context) ─
print("Case 6: bare 'cyber security' → ASK_DEGREE")
user = "સાયબર સિક્યુરિટી"
has_marker = _has_degree_level_marker(user)
assert_case("no degree marker detected", not has_marker)
# In ASK_DEGREE mode the engine builds context with NAMES ONLY (no fees);
# fact_guard's whitelist is empty → no substitution possible.
ctx_empty = "VARIANTS_AVAILABLE_FOR_THIS_TOPIC:\n- B.Sc. IT  (Hons.) (Cyber Security)\n- M. Sc. IT (Cyber Security)"
facts_empty = extract_facts_from_context(ctx_empty)
assert_case("ASK_DEGREE context has zero fees", not facts_empty.fees_digits)


# ── 7. B.Sc IT Data Science specific ────────────────────────────────────
print("Case 7: B.Sc IT Data Science specific")
# Use Gujarati form because the English-pass aliases don't include
# the bare "B.Sc IT Data Science" form (only "(Hons.)" variant matches).
locked, focused = _simulate_canonical_lock(
    "મારે બીએસસી આઈટી ડેટા સાયન્સ વિશે જાણવું છે", bsc_ds
)
assert_case("single_source_lock fires", locked)
ctx = _build_focused_context(bsc_ds)
facts = extract_facts_from_context(ctx)
# LLM accidentally quotes M.Sc fee (94000) for B.Sc context → must correct
fixed, corr = validate_and_correct(
    "B.Sc IT Data Science માટે ફી ચોરાણું હજાર છે.",
    facts,
    canonical_program=bsc_ds,
)
assert_case(
    "M.Sc DS fee caught when canonical is B.Sc DS",
    bool(corr),
    f"corr={corr}",
)


# ── 8. M.Sc IT Data Science specific ────────────────────────────────────
print("Case 8: M.Sc IT Data Science specific")
locked, focused = _simulate_canonical_lock(
    "મારે એમએસસી આઈટી ડેટા સાયન્સ વિશે", msc_ds
)
assert_case("single_source_lock fires", locked)
ctx = _build_focused_context(msc_ds)
facts = extract_facts_from_context(ctx)
# Correct M.Sc fee passes
fixed, corr = validate_and_correct(
    "M.Sc IT Data Science માટે ફી ચોરાણું હજાર છે.",
    facts,
    canonical_program=msc_ds,
)
assert_case("correct M.Sc DS fee passes", not corr)


# ── 9. Follow-up "ફી શું છે?" after Marine focus ─────────────────────────
print("Case 9: follow-up fee question on Marine")
# focused=Marine from prior turn; current user text is "ફી શું છે?"
# (doesn't name a program). single_source_lock is False (find_by_name on
# bare "ફી શું છે?" returns None) — so RAG-retrieved siblings COULD enter
# context. But fact_canonical = focused = Marine, so the canonical lock
# in fact_guard ensures Marine's fee is the only valid value.
user = "ફી શું છે?"
locked, focused = _simulate_canonical_lock(user, marine_btech)
assert_case("lock does NOT fire on bare follow-up", not locked)
# Simulate context = [Marine, GME] (RAG retrieved both)
ctx = format_program_context(marine_btech) + "\n" + format_program_context(gme)
facts = extract_facts_from_context(ctx)
# LLM correctly emits Marine fee → passes
fixed, corr = validate_and_correct(
    "ફી 4 લાખ 95 હજાર છે.",
    facts,
    canonical_program=marine_btech,
)
assert_case("Marine fee passes when canonical=Marine", not corr)
# LLM accidentally emits GME fee → CAUGHT by canonical lock (sibling fees
# get dropped from effective whitelist).
fixed_bad, corr_bad = validate_and_correct(
    "ફી 3 લાખ 80 હજાર છે.",
    facts,
    canonical_program=marine_btech,
)
assert_case(
    "sibling GME fee caught even with sibling in context",
    bool(corr_bad),
    f"corr={corr_bad}",
)


# ── 10. Pivot from Marine to M.Sc IT Cyber Security → focused changes ──
print("Case 10: pivot to M.Sc IT Cyber Security")
user = "હવે એમએસસી આઈટી સાયબર સિક્યુરિટી વિશે કહો"
match = cat.find_by_name(user)
assert_case("catalog matches pivot target", match is not None)
if match:
    # The discriminating-token scorer should pick M.Sc (not B.Sc) here.
    assert_case(
        "pivot resolves to M.Sc variant",
        "m. sc" in (match.get("program") or "").lower(),
        f"got {match.get('program')!r}",
    )
    locked, focused = _simulate_canonical_lock(user, match)
    assert_case("single_source_lock fires on pivot", locked)


# ── 11. Broad marine query after Marine focus → drop lock, browse mode ──
print("Case 11: broad marine query post-focus → browse")
# focused=Marine, but user says "marine વિશે બીજા કોર્સ?" (broad).
# find_by_name might or might not match — what matters is find_family_query
# fires and returns the topic group.
user = "marine વિશે બીજા કોર્સ"
r = cat.find_family_query(user)
assert_case("topic group returned for broad query", r is not None)
if r:
    assert_case("topic group has 6 programs", len(r[1]) == 6, f"got {len(r[1])}")


# ── Regression: word-boundary on common acronyms ─────────────────────────
print("Regression: word-boundary checks (no false positives)")
# 'standard' / 'demand' / 'pandemic' must not get touched by "and"
# transliteration (text_normalize)
from app.brain.text_normalize import normalize
for inp in ["standard process", "demand high", "pandemic year"]:
    out = normalize(inp)
    assert_case(
        f"'and' boundary intact for {inp!r}",
        "અને" not in out or " and " in inp,
        f"got {out!r}",
    )


# ─── Summary ────────────────────────────────────────────────────────────
print()
print(f"PASS: {PASS}")
print(f"FAIL: {len(FAIL)}")
if FAIL:
    print()
    print("Failures:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
print()
print("Cross-program isolation regression: ALL GREEN")
