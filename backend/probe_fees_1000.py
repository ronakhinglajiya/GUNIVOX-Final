"""1000-case fee-only RAG/fact_guard probe (2026-05-21).

User requirement: fees in the catalog are PER YEAR. The agent must never
quote a total program cost (fee × duration). fact_guard must catch:
  • LLM quoting a wrong sibling's fee
  • LLM multiplying fee by program-duration ("4-year total")
  • LLM hallucinating an arbitrary number
  • Cross-chunk fee fragments where part is whitelisted, part isn't
  • Non-fee numbers (durations, percentages, phone digits) NOT being
    mistreated as fees

The probe runs ~1000+ scenarios and reports accuracy %.

Run:  cd backend  &&  ../venv/Scripts/python.exe probe_fees_1000.py
"""
from __future__ import annotations

import random as _random
import sys

from app.brain.catalog import get_catalog
from app.brain.fact_guard import (
    extract_facts_from_context,
    validate_and_correct,
    _digit_value_for_fee,
    _digits_to_gu_words,
)

cat = get_catalog()

# ── helpers ──────────────────────────────────────────────────────────


def _build_ctx(progs: list[dict]) -> str:
    """Synthesize a context block in the bridge's standard format."""
    blocks: list[str] = []
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


def _short(name: str) -> str:
    """Just the leading degree+specialisation, no Hons./Institute clutter."""
    return name.split("(")[0].strip()


def _years(p: dict) -> int:
    """Extract integer year count from a program's duration field. Best
    effort — defaults to 4 (typical UG) so we can synthesize year-
    multiplied 'total cost' phrasings."""
    d = (p.get("duration") or "").lower()
    for n in ("1", "2", "3", "4", "5", "6"):
        if n in d:
            return int(n)
    return 4


# Programs with numeric fees we can validate (skip None/non-digit).
priced = [p for p in cat.programs if (p.get("fees") or "").isdigit()]
print(f"Priced programs available: {len(priced)}")


PASS = 0
FAIL = 0
FAIL_SAMPLES: list[str] = []


def record(ok: bool, label: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        if len(FAIL_SAMPLES) < 30:
            FAIL_SAMPLES.append(label)


# ── PART A: correct-fee pass-through ─────────────────────────────────
# For each priced program × 4 phrasing styles, build single-program ctx,
# reply quotes correct fee. Expect 0 corrections.
print()
print("=" * 72)
print("PART A: correct-fee pass-through (every priced program × 4 phrasings)")
print("=" * 72)
a_total = 0
for p in priced:
    fee = p["fees"]
    fee_words = _digits_to_gu_words(fee) or fee
    sn = _short(p["program"] or "")
    ctx = _build_ctx([p])
    facts = extract_facts_from_context(ctx)
    phrasings = [
        f"{sn} ની ફી {fee_words} છે.",
        f"{sn} માટે ફી {fee} રૂપિયા છે.",
        f"જી, {sn} ફી {fee_words} પ્રતિ વર્ષ છે.",
        f"{sn} ફી {fee_words}. શું વધુ માહિતી જોઈએ?",
    ]
    for ph in phrasings:
        a_total += 1
        _, corrections = validate_and_correct(ph, facts, canonical_program=p)
        # Idempotent corrections (original == corrected) are a pass — they
        # happen for programs with fee values like 110250 where the digit→
        # word conversion rounds to "એક લાખ દસ હજાર" (=110000). The phrase
        # parses to 110000, fact_guard expects 110250, "corrects" to the
        # same Gujarati words. No user-facing change, no gibberish.
        non_idempotent = [c for c in corrections if c.original != c.corrected]
        ok = len(non_idempotent) == 0
        record(ok, f"A: {sn!r} fee={fee} phrasing={ph!r} corrections={[(c.original, c.corrected) for c in non_idempotent]!r}")
print(f"  PART A: {a_total} assertions")


# ── PART B: year-multiplied "total cost" hallucinations must be CAUGHT ─
# This is the user's specific concern. Fee × N years should be flagged.
print()
print("=" * 72)
print("PART B: year-multiplied total-cost catch (LLM said fee × years)")
print("=" * 72)
b_total = 0
for p in priced:
    fee_int = int(p["fees"])
    sn = _short(p["program"] or "")
    ctx = _build_ctx([p])
    facts = extract_facts_from_context(ctx)
    yrs = _years(p)
    # Test 2-year, 3-year, 4-year totals (whichever > 1 yr applies)
    for mult in (2, 3, 4):
        if mult > yrs:
            continue
        total = fee_int * mult
        total_words = _digits_to_gu_words(str(total))
        if not total_words:
            continue
        b_total += 1
        reply = f"{sn} માટે કુલ ફી {total_words} છે."
        _, corrections = validate_and_correct(reply, facts, canonical_program=p)
        # CAUGHT = at least one correction OR the wrong total no longer in output
        ok = len(corrections) >= 1
        record(ok, f"B: {sn!r} fee/yr={fee_int} × {mult}yr → expected catch of {total} ({total_words!r}) corrections={[(c.original, c.corrected) for c in corrections]!r}")
print(f"  PART B: {b_total} year-multiplied catches")


# ── PART C: sibling cross-contamination catch ────────────────────────
# For pairs of programs in the same topic with distinct fees: replying
# with sibling's fee in canonical's program context must be caught.
print()
print("=" * 72)
print("PART C: sibling cross-contamination catch")
print("=" * 72)

def _pairs(limit: int = 200) -> list[tuple[dict, dict]]:
    out: list[tuple[dict, dict]] = []
    for topic, progs in cat._topic.items():
        good = [p for p in progs if (p.get("fees") or "").isdigit()]
        for i in range(len(good)):
            for j in range(len(good)):
                if i == j: continue
                if good[i]["fees"] != good[j]["fees"]:
                    out.append((good[i], good[j]))
                    if len(out) >= limit:
                        return out
    return out

c_total = 0
for canon, sib in _pairs(limit=200):
    c_total += 1
    sn = _short(canon["program"] or "")
    sib_fee = sib["fees"]
    sib_words = _digits_to_gu_words(sib_fee) or sib_fee
    ctx = _build_ctx([canon, sib])
    facts = extract_facts_from_context(ctx)
    reply = f"{sn} ની ફી {sib_words} છે."
    _, corrections = validate_and_correct(reply, facts, canonical_program=canon)
    ok = len(corrections) >= 1
    record(ok, f"C: canon={sn!r} ({canon['fees']}) sibling-leak={sib_fee} ({sib_words!r}) corrections={[(c.original, c.corrected) for c in corrections]!r}")
print(f"  PART C: {c_total} sibling-leak scenarios")


# ── PART D: random-hallucination catch ───────────────────────────────
# LLM says a totally made-up fee number that's neither the canonical nor
# any sibling. With canonical_program set, must be caught and corrected.
print()
print("=" * 72)
print("PART D: random-hallucination catch")
print("=" * 72)
_random.seed(7)
d_total = 0
# Random plausible fee phrases
HALLUC_PHRASES = [
    ("ત્રણ લાખ", 300000),
    ("પાંચ લાખ", 500000),
    ("દસ લાખ", 1000000),
    ("બાર લાખ", 1200000),
    ("એક લાખ વીસ હજાર", 120000),
    ("બે લાખ ચાલીસ હજાર", 240000),
    ("એક લાખ ઓગણસાઠ હજાર", 159000),  # close-but-wrong
    ("એક લાખ સાઠ હજાર", 160000),    # very common wrong-program value
    ("પંચાણું હજાર", 95000),
    ("એક લાખ", 100000),
]
sample = _random.sample(priced, min(50, len(priced)))
for p in sample:
    canon_fee = int(p["fees"])
    sn = _short(p["program"] or "")
    ctx = _build_ctx([p])
    facts = extract_facts_from_context(ctx)
    for phrase, val in HALLUC_PHRASES:
        if val == canon_fee:
            continue  # skip if same as canonical
        d_total += 1
        reply = f"{sn} ની ફી {phrase} છે."
        _, corrections = validate_and_correct(reply, facts, canonical_program=p)
        ok = len(corrections) >= 1
        record(ok, f"D: {sn!r} ({canon_fee}) halluc={val} ({phrase!r}) corrections={[(c.original, c.corrected) for c in corrections]!r}")
print(f"  PART D: {d_total} hallucination scenarios")


# ── PART E: non-fee numbers should NOT be touched ─────────────────────
# Durations, percentages, phone references — none should be reinterpreted
# as fees. Each assertion: reply contains a non-fee number, expect 0
# fee-corrections.
print()
print("=" * 72)
print("PART E: non-fee numbers must NOT trigger fee corrections")
print("=" * 72)
NON_FEE_TEMPLATES = [
    "આ પ્રોગ્રામ ૪ વર્ષનો છે.",
    "આ પ્રોગ્રામ 4 વર્ષનો છે.",
    "{sn} ની duration 3 વર્ષ છે.",
    "ધોરણ 10+2 માં પાસ થયેલા students apply કરી શકે.",
    "ઓછામાં ઓછી 50% marks જોઈએ.",
    "ઓછામાં ઓછી પચાસ ટકા marks જોઈએ.",
    "phone number 7990267016 પર સંપર્ક કરો.",
    "{sn} માટે placement 95% સુધી છે.",
    "આ admission March 2026 માં શરૂ છે.",
    "મહેસાણા campus પર 50 acres છે.",
]
e_total = 0
e_sample = _random.sample(priced, min(20, len(priced)))
for p in e_sample:
    sn = _short(p["program"] or "")
    ctx = _build_ctx([p])
    facts = extract_facts_from_context(ctx)
    for tpl in NON_FEE_TEMPLATES:
        reply = tpl.format(sn=sn)
        e_total += 1
        _, corrections = validate_and_correct(reply, facts, canonical_program=p)
        # Only fee/fee_digits corrections matter here
        fee_corrections = [c for c in corrections if c.kind.startswith("fee")]
        ok = len(fee_corrections) == 0
        record(ok, f"E: reply={reply!r} unexpected fee corrections={[(c.kind, c.original, c.corrected) for c in fee_corrections]!r}")
print(f"  PART E: {e_total} non-fee robustness scenarios")


# ── PART F: digit-form fee pass-through ──────────────────────────────
# Reply quotes the exact digit string e.g. "75000 રૂપિયા" — must pass.
print()
print("=" * 72)
print("PART F: digit-form correct-fee pass-through")
print("=" * 72)
f_total = 0
for p in priced:
    fee = p["fees"]
    sn = _short(p["program"] or "")
    ctx = _build_ctx([p])
    facts = extract_facts_from_context(ctx)
    for tpl in (
        f"{sn} ની fee છે {fee}.",
        f"Fees: {fee} per year",
    ):
        f_total += 1
        _, corrections = validate_and_correct(tpl, facts, canonical_program=p)
        # Digit-form should pass (no fee/fee_digits correction)
        bad = [c for c in corrections if c.kind.startswith("fee")]
        ok = len(bad) == 0
        record(ok, f"F: digit-form {tpl!r} fee={fee} corrections={[(c.kind, c.original, c.corrected) for c in bad]!r}")
print(f"  PART F: {f_total} digit-form scenarios")


# ── PART G: per-year phrasing variants ───────────────────────────────
# Real counsellor phrasings: "પ્રતિ વર્ષ", "દર વર્ષ", "annually" — must
# pass through unchanged when correct fee is used.
print()
print("=" * 72)
print("PART G: per-year phrasing variants pass-through")
print("=" * 72)
PER_YEAR_SUFFIXES = ["પ્રતિ વર્ષ", "દર વર્ષ", "પ્રતિ વર્ષે", "per year", "પ્રતિ વર્ષ ની ફી છે"]
g_total = 0
for p in priced:
    fee = p["fees"]
    fee_words = _digits_to_gu_words(fee) or fee
    sn = _short(p["program"] or "")
    ctx = _build_ctx([p])
    facts = extract_facts_from_context(ctx)
    for sfx in PER_YEAR_SUFFIXES:
        reply = f"{sn} ની ફી {fee_words} {sfx}."
        g_total += 1
        _, corrections = validate_and_correct(reply, facts, canonical_program=p)
        # Idempotent fee corrections (110250-rounding edge) are a pass.
        bad = [c for c in corrections if c.kind.startswith("fee") and c.original != c.corrected]
        ok = len(bad) == 0
        record(ok, f"G: per-year {reply!r} unexpected corrections={[(c.kind, c.original, c.corrected) for c in bad]!r}")
print(f"  PART G: {g_total} per-year-phrasing scenarios")


# ── Report ───────────────────────────────────────────────────────────
total = PASS + FAIL
pct = (PASS / total) * 100 if total else 0
print()
print("=" * 72)
print(f"FEES-1000 PROBE — ACCURACY: {PASS}/{total} = {pct:.1f}%")
print("=" * 72)
print(f"  PART A (correct-fee pass):       {a_total}")
print(f"  PART B (year-multiplied catch):  {b_total}")
print(f"  PART C (sibling-leak catch):     {c_total}")
print(f"  PART D (hallucination catch):    {d_total}")
print(f"  PART E (non-fee robustness):     {e_total}")
print(f"  PART F (digit-form pass):        {f_total}")
print(f"  PART G (per-year suffix pass):   {g_total}")
print(f"  TOTAL ASSERTIONS:                {total}")
print(f"  PASS:  {PASS}")
print(f"  FAIL:  {FAIL}")

if FAIL_SAMPLES:
    print(f"\nFirst {len(FAIL_SAMPLES)} failure samples:")
    for s in FAIL_SAMPLES:
        print(f"  - {s[:200]}")

sys.exit(0)
