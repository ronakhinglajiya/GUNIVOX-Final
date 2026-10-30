"""1000-case stream/background probe (2026-05-21).

The demo bug: caller said 'મેં 12th સાયન્સ બી ગ્રુપમાંથી કરેલું છે'
(I did 12th science B-group = PCB). find_by_name MATCHED this as
B.Sc Nautical Science because 'બી'+'સાયન્સ' overlapped a B.Sc alias.
The agent then locked focus on Nautical Science instead of asking
which PCB-eligible area the caller wanted.

This probe drives 1000+ background-disclosure utterances through
find_by_name and asserts NONE of them resolve to a specific program.
Companion probe verifies that utterances which DO name a program AND
mention a background (mixed) still resolve correctly.

Run:  ../venv/Scripts/python.exe probe_stream_1000.py
"""
from __future__ import annotations

import random
import sys

from app.brain.catalog import get_catalog, _looks_like_stream_background

cat = get_catalog()
random.seed(42)

PASS = 0
FAIL = 0
FAIL_SAMPLES: list[str] = []


def record(ok: bool, msg: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        if len(FAIL_SAMPLES) < 30:
            FAIL_SAMPLES.append(msg)


# ── Generator components ─────────────────────────────────────────────
SUBJECTS_PCM = [
    "physics chemistry maths", "physics chemistry math",
    "phy chem maths", "PCM",
    "ફિઝિક્સ કેમેસ્ટ્રી મેથ્સ", "ફિઝિક્સ કેમેસ્ટ્રી મેથ",
    "પીસીએમ",
]
SUBJECTS_PCB = [
    "physics chemistry biology", "phy chem bio", "PCB",
    "ફિઝિક્સ કેમેસ્ટ્રી બાયોલોજી",
    "પીસીબી",
]
GROUP_PCM = ["A group", "A-group", "એ ગ્રુપ", "એ ગ્રુપમાંથી"]
GROUP_PCB = ["B group", "B-group", "બી ગ્રુપ", "બી ગ્રુપમાંથી"]

GRADE_PHRASES_EN = [
    "I did 12th science", "I completed 12th science",
    "after 12th science", "10+2 science",
    "12th science background",
]
GRADE_PHRASES_GU = [
    "મેં ધોરણ 12 સાયન્સ કરેલું છે",
    "મેં ટ્વેલ્થ સાયન્સ કરેલું છે",
    "મેં ટ્વેલ્થમાં સાયન્સ કરેલું છે",
    "બારમું સાયન્સ કરેલું છે",
    "12th માં સાયન્સ હતું",
]

WRAPPERS_EN = [
    "{phrase}",
    "I did {phrase}",
    "I have done {phrase}",
    "{phrase}, what courses are available?",
    "completed {phrase} after 12th",
    "after 12th {phrase}, what should I do?",
    "I'm from {phrase} background, any course?",
]
WRAPPERS_GU = [
    "{phrase}",
    "મેં {phrase} કરેલું છે",
    "{phrase}, કયા કોર્સ છે?",
    "બારમું {phrase} પછી શું કરું?",
    "{phrase} માંથી છું",
    "{phrase} પાસ કરેલું છે",
]


# ── PART A: PCM utterances must NOT match a program ──────────────────
print("=" * 72)
print("PART A: PCM-background utterances (must NOT match a program)")
print("=" * 72)
a_total = 0
for subj in SUBJECTS_PCM:
    for w in WRAPPERS_EN:
        a_total += 1
        q = w.format(phrase=subj)
        p = cat.find_by_name(q)
        record(p is None, f"A-PCM-en: {q!r} → {(p.get('program') if p else None)!r}")
    for w in WRAPPERS_GU:
        a_total += 1
        q = w.format(phrase=subj)
        p = cat.find_by_name(q)
        record(p is None, f"A-PCM-gu: {q!r} → {(p.get('program') if p else None)!r}")
for grp in GROUP_PCM:
    for w in WRAPPERS_EN + WRAPPERS_GU:
        a_total += 1
        q = w.format(phrase=grp)
        p = cat.find_by_name(q)
        record(p is None, f"A-PCM-group: {q!r} → {(p.get('program') if p else None)!r}")
for gp in GRADE_PHRASES_EN + GRADE_PHRASES_GU:
    for tail in ("", " A group", " PCM", " એ ગ્રુપ", " પીસીએમ"):
        a_total += 1
        q = gp + tail
        p = cat.find_by_name(q)
        # Pure grade-statement with no group might not resolve to background → check stream marker
        if tail == "" and not _looks_like_stream_background(q):
            # Plain "I did 12th science" without group might match nothing OR
            # something — either way, accept as not-a-program-name resolution
            ok = p is None
        else:
            ok = p is None
        record(ok, f"A-grade: {q!r} → {(p.get('program') if p else None)!r}")
print(f"  PART A: {a_total} PCM scenarios")


# ── PART B: PCB utterances must NOT match a program ──────────────────
print()
print("=" * 72)
print("PART B: PCB-background utterances (must NOT match a program)")
print("=" * 72)
b_total = 0
for subj in SUBJECTS_PCB:
    for w in WRAPPERS_EN:
        b_total += 1
        q = w.format(phrase=subj)
        p = cat.find_by_name(q)
        record(p is None, f"B-PCB-en: {q!r} → {(p.get('program') if p else None)!r}")
    for w in WRAPPERS_GU:
        b_total += 1
        q = w.format(phrase=subj)
        p = cat.find_by_name(q)
        record(p is None, f"B-PCB-gu: {q!r} → {(p.get('program') if p else None)!r}")
for grp in GROUP_PCB:
    for w in WRAPPERS_EN + WRAPPERS_GU:
        b_total += 1
        q = w.format(phrase=grp)
        p = cat.find_by_name(q)
        record(p is None, f"B-PCB-group: {q!r} → {(p.get('program') if p else None)!r}")
print(f"  PART B: {b_total} PCB scenarios")


# ── PART C: combined "I did PCM, want X" — explicit program MUST win ─
print()
print("=" * 72)
print("PART C: combined background+program → program must still resolve")
print("=" * 72)
c_total = 0
COMBOS = [
    ("PCM", "B.Tech CSE", "CSE"),
    ("PCM", "B.Tech Marine", "Marine"),
    ("PCM", "BCA AI ML", "AI"),
    ("PCM", "B.Sc IT Cyber Security", "Cyber"),
    ("PCM", "MBA Finance", "Financial"),
    ("PCM", "Diploma Civil", "Civil"),
    ("PCB", "B.Pharm", "Pharm"),
    ("PCB", "GNM", "Nursing"),
    ("PCB", "BPT", "Physio"),
    ("PCB", "B.Sc Nursing", "Nursing"),
]
TEMPLATES = [
    "I did {bg} and want {prog}",
    "After {bg}, I'm interested in {prog}",
    "{bg} background — {prog} please",
    "મેં {bg} કરેલું છે, {prog} માં ઇન્ટરેસ્ટ છે",
    "{bg} પછી {prog} લેવું છે",
]
for bg, prog, expected in COMBOS:
    for tpl in TEMPLATES:
        c_total += 1
        q = tpl.format(bg=bg, prog=prog)
        p = cat.find_by_name(q)
        got = (p.get("program") if p else "") or ""
        ok = expected.lower().replace(" ", "") in got.lower().replace(" ", "").replace(".", "")
        record(ok, f"C-combo: {q!r} → {got!r} (want substr {expected!r})")
print(f"  PART C: {c_total} combined scenarios (program must still win)")


# ── PART D: noise/typo permutations of background utterances ─────────
print()
print("=" * 72)
print("PART D: noise/typo permutations of background utterances")
print("=" * 72)
d_total = 0
BASE_BG = [
    "PCM",
    "PCB",
    "A group",
    "B group",
    "physics chemistry maths",
    "physics chemistry biology",
    "બી ગ્રુપમાંથી",
    "એ ગ્રુપમાંથી",
    "ફિઝિક્સ કેમેસ્ટ્રી મેથ્સ",
    "ફિઝિક્સ કેમેસ્ટ્રી બાયોલોજી",
]
NOISE_MUTATIONS = [
    lambda s: s.upper(),
    lambda s: s.lower(),
    lambda s: s.title(),
    lambda s: f"  {s}  ",
    lambda s: f"{s}?",
    lambda s: f"{s}.",
    lambda s: f"{s}!",
    lambda s: f"{s}...",
    lambda s: s.replace(" ", "  "),  # double space
    lambda s: s + " " + s,  # repeated
]
for bg in BASE_BG:
    for mut in NOISE_MUTATIONS:
        d_total += 1
        q = mut(bg)
        p = cat.find_by_name(q)
        record(p is None, f"D-noise: {q!r} → {(p.get('program') if p else None)!r}")
print(f"  PART D: {d_total} noise/typo scenarios")


# ── PART E: real-call transcripts (from prod logs) ────────────────────
print()
print("=" * 72)
print("PART E: real prod-log transcripts that should be backgrounds")
print("=" * 72)
real_cases = [
    "મેં ટ્વેલ્થમાં સાયન્સ કરેલું છે બી ગ્રુપમાંથી",
    "મેં ફિઝિક્સ કેમેસ્ટ્રી મેથ્સ કરેલું છે તો એના માટે કોઈ કોર્સીસ અવેલેબલ છે",
    "મેં 12th કોમર્સ કરેલું છે તો કયો પ્રોગ્રામ બેસ્ટ રહેશે?",
    "બારમું સાયન્સ બી ગ્રુપ",
    "ધોરણ 12 માં PCM",
    "I did my 12th in science PCM",
    "after twelfth science B group",
    "મારે જાણવું છે કે PCM પછી શું કરું",
    "PCB stream માં છું, કયો કોર્સ સારો?",
    "મારે A group માં અભ્યાસ કર્યો છે",
    "I'm from A-group, suggest a course",
    "B-group માંથી જ આવ્યો છું, options?",
]
e_total = 0
for q in real_cases:
    e_total += 1
    p = cat.find_by_name(q)
    record(p is None, f"E-real: {q!r} → {(p.get('program') if p else None)!r}")
print(f"  PART E: {e_total} real-transcript scenarios")


# ── PART F: random fuzz — random combos + random insertions ──────────
print()
print("=" * 72)
print("PART F: random fuzz combos (500 cases)")
print("=" * 72)
INSERT_BITS = [
    "મારે", "મને", "I want", "please", "kindly", "ji", "જી", "હા", "yes",
    "વધુ", "વિશે", "જણાવો", "tell me", "more info",
]
f_total = 0
for _ in range(500):
    f_total += 1
    bg = random.choice(BASE_BG + GROUP_PCB + GROUP_PCM)
    bits = random.sample(INSERT_BITS, k=random.randint(0, 3))
    parts = bits + [bg] + random.sample(INSERT_BITS, k=random.randint(0, 2))
    q = " ".join(parts)
    p = cat.find_by_name(q)
    record(p is None, f"F-fuzz: {q!r} → {(p.get('program') if p else None)!r}")
print(f"  PART F: {f_total} random fuzz scenarios")


# ── Report ───────────────────────────────────────────────────────────
total = PASS + FAIL
pct = (PASS / total) * 100 if total else 0
print()
print("=" * 72)
print(f"STREAM PROBE — ACCURACY: {PASS}/{total} = {pct:.2f}%")
print("=" * 72)
print(f"  PART A (PCM):                    {a_total}")
print(f"  PART B (PCB):                    {b_total}")
print(f"  PART C (combined w/ program):    {c_total}")
print(f"  PART D (noise/typo):             {d_total}")
print(f"  PART E (real prod transcripts):  {e_total}")
print(f"  PART F (random fuzz):            {f_total}")
print(f"  TOTAL ASSERTIONS:                {total}")
print(f"  PASS:  {PASS}")
print(f"  FAIL:  {FAIL}")

if FAIL_SAMPLES:
    print(f"\nFirst {len(FAIL_SAMPLES)} failures:")
    for s in FAIL_SAMPLES:
        print(f"  - {s[:200]}")

sys.exit(0)
