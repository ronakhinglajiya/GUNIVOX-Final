"""Random-question accuracy probe (one-off, not part of regression).

100 caller-realistic questions mixing specific-program asks, broad
topical browses, off-topic / non-program inputs, and STT-mangled forms.
Reports % accuracy.
"""
from __future__ import annotations

import sys
from app.brain.catalog import get_catalog

cat = get_catalog()


def _name_substr(prog: dict | None, want: str) -> bool:
    if prog is None:
        return False
    name = (prog.get("program") or "").lower().replace(".", "").replace(" ", "")
    needle = want.lower().replace(".", "").replace(" ", "")
    return needle in name


def probe_specific(q: str, want_substr: str) -> tuple[bool, str]:
    p = cat.find_by_name(q)
    ok = _name_substr(p, want_substr)
    return ok, f"{q!r} -> {(p.get('program') if p else None)!r} (want substr {want_substr!r})"


def probe_topic(q: str, want_topic: str, min_variants: int) -> tuple[bool, str]:
    r = cat.find_family_query(q)
    if r is None:
        return False, f"{q!r} -> None (want {want_topic} ≥{min_variants})"
    key, variants = r
    ok = (want_topic in key or key in want_topic) and len(variants) >= min_variants
    return ok, f"{q!r} -> key={key!r} count={len(variants)} (want {want_topic} ≥{min_variants})"


def probe_none(q: str) -> tuple[bool, str]:
    p = cat.find_by_name(q)
    ok = p is None
    return ok, f"{q!r} -> {(p.get('program') if p else None)!r} (want None)"


# ── 100 cases ────────────────────────────────────────────────────────
SPECIFIC: list[tuple[str, str]] = [
    # Standard Gujarati + English direct forms
    ("B.Tech Marine વિશે કહો",                 "Marine"),
    ("MBA Finance કરવી છે",                    "Financial"),
    ("BCA Cyber Security",                     "Cyber"),
    ("M.Sc IT Data Science",                   "Data Science"),
    ("B.Sc IT Hons Cyber Security",            "Cyber"),
    ("Diploma Civil",                          "Civil"),
    ("GNM",                                    "Nursing Midwifery"),
    ("GME",                                    "Graduate Marine"),
    ("DNS",                                    "Nautical"),
    ("ETO",                                    "ETO"),
    ("G.P. Ratings",                           "Ratings"),
    ("B.Tech CSE",                             "CSE"),
    ("B.Tech CSE AI ML",                       "AI"),
    ("BBA Marketing Management",               "Marketing"),
    ("BBA International Business",             "International"),
    ("MBA Logistics",                          "Logistics"),
    ("MBA Healthcare",                         "Healthcare"),
    ("MBA HR",                                 "HR"),
    ("BCA AI ML",                              "AI"),
    ("B.Sc IT AI ML",                          "AI"),
    ("M.Sc IT AI ML",                          "AI"),
    ("M.Sc IT Cyber Security",                 "Cyber"),
    ("Diploma Automobile",                     "Automobile"),
    ("B.Tech Automobile",                      "Automobile"),
    ("B.Tech Biomedical",                      "Biomedical"),
    ("B.Tech Civil",                           "Civil"),
    ("B.Tech Electrical",                      "Electrical"),
    ("B.Tech Mechanical",                      "Mechanical"),
    ("B.Tech Chemical",                        "Chemical"),
    ("B.Tech Mechatronics",                    "Mechatronics"),
    ("B.Tech Food Technology",                 "Food"),
    ("B.Sc Agriculture",                       "Agriculture"),
    ("M.Sc Microbiology",                      "Microbiology"),
    ("M.Sc Biotechnology",                     "Biotechnology"),
    ("B.Tech Agriculture Engineering",         "Agriculture"),
    ("B.Pharm",                                "Pharm"),
    ("M.Pharm Pharmaceutics",                  "Pharmaceutics"),
    ("M.Pharm Pharmacology",                   "Pharmacology"),
    ("B.Arch Architecture",                    "Arch"),
    ("B.Design Interior Design",               "Interior"),
    ("B.Design Product Design",                "Product"),
    ("B.Sc Nursing",                           "Nursing"),
    ("BPT Physiotherapy",                      "Physio"),
    ("M.Tech Civil",                           "Civil"),
    ("M.Tech Mechanical",                      "Mechanical"),
    ("MCA",                                    "MCA"),
    ("B.Sc Nautical Science",                  "Nautical"),
    ("B.Com",                                  "Com"),
    ("BA Honours",                             "BA"),
    ("MBA Agribusiness",                       "Agribusiness"),
]

TOPIC: list[tuple[str, str, int]] = [
    ("marine વિશે કહો",                       "marine",                 4),
    ("automobile વિશે",                       "automobile",             3),
    ("cyber security",                        "cyber-security",         2),
    ("data science",                          "data-science",           2),
    ("AI ML",                                 "ai-ml",                  3),
    ("biotechnology",                         "biotechnology",          2),
    ("agriculture વિશે",                      "agriculture",            2),
    ("civil engineering",                     "civil",                  2),
    ("mechanical engineering",                "mechanical",             2),
    ("chemical",                              "chemical",               2),
    ("food technology",                       "food",                   2),
    ("microbiology",                          "microbiology",           2),
    ("biomedical",                            "biomedical",             2),
    ("mechatronics",                          "mechatronics",           2),
    ("business analytics",                    "business-analytics",     2),
    ("international business",                "international-business", 2),
    ("computer engineering",                  "computer-eng",           2),
    ("BCA વિશે",                              "bca",                    3),
    ("MBA વિશે",                              "mba",                    5),
    ("BTech વિશે જણાવો",                      "btech",                  5),
]

NEGATIVE = [
    "હા", "ઓકે", "નમસ્તે", "મારું નામ રોનક છે", "આભાર",
    "આજે હવામાન કેવું છે?", "તમે કોણ છો?", "are you AI?",
    "thanks", "ફી કેટલી?", "eligibility?", "મમ", "umm",
    "ઠીક છે", "bye",
]

STT_MANGLED: list[tuple[str, str]] = [
    ("ઓટો મોબાઇલ",                  "Automobile"),
    ("બીટેક મરીન",                  "Marine"),
    ("એમબીએ ફાઇનાન્સ",              "Financial"),
    ("બીસીએ સાયબર",                 "Cyber"),
    ("એમએસસી આઈટી ડેટા સાહેબ",      "Data Science"),
    ("જી એન એમ",                    "Nursing"),
    ("જી એમ ઈ",                     "Graduate Marine"),
    ("બીસસી આઈટી સાયબર",            "Cyber"),
    ("ડિપ્લોમા સિવિલ એન્જિનિયરિંગ", "Civil"),
    ("મારે ફાર્મસી કરવી છે",        "Pharm"),
    ("આર્કિટેક્ચર",                  "Arch"),
    ("નર્સિંગ",                      "Nursing"),
    ("બીબીએ માર્કેટિંગ",             "Marketing"),
    ("એમએસસી IT",                   "M"),  # any M.Sc IT
    ("એમ સી એ",                      "MCA"),
]

# ── Run ──────────────────────────────────────────────────────────────
hits = misses = 0
miss_lines: list[str] = []

print("=" * 72)
print("RANDOM PROBE — 100 caller-realistic questions")
print("=" * 72)

print("\n[A] Specific program ask (50)")
for q, want in SPECIFIC:
    ok, line = probe_specific(q, want)
    if ok: hits += 1
    else:  misses += 1; miss_lines.append("A: " + line)

print(f"   {len(SPECIFIC) - sum(1 for q,w in SPECIFIC if not probe_specific(q,w)[0])}/{len(SPECIFIC)} hit")

print("\n[B] Broad topic browse (20)")
b_hits = 0
for q, want, n in TOPIC:
    ok, line = probe_topic(q, want, n)
    if ok: hits += 1; b_hits += 1
    else:  misses += 1; miss_lines.append("B: " + line)
print(f"   {b_hits}/{len(TOPIC)} hit")

print("\n[C] Negative — must not resolve (15)")
c_hits = 0
for q in NEGATIVE:
    ok, line = probe_none(q)
    if ok: hits += 1; c_hits += 1
    else:  misses += 1; miss_lines.append("C: " + line)
print(f"   {c_hits}/{len(NEGATIVE)} hit")

print("\n[D] STT-mangled form (15)")
d_hits = 0
for q, want in STT_MANGLED:
    ok, line = probe_specific(q, want)
    if ok: hits += 1; d_hits += 1
    else:  misses += 1; miss_lines.append("D: " + line)
print(f"   {d_hits}/{len(STT_MANGLED)} hit")

total = hits + misses
pct = (hits / total) * 100 if total else 0
print()
print("=" * 72)
print(f"ACCURACY: {hits}/{total} = {pct:.1f}%")
print("=" * 72)

if miss_lines:
    print(f"\nMisses ({len(miss_lines)}):")
    for ml in miss_lines:
        print(f"  - {ml}")
sys.exit(0 if not miss_lines else 0)  # always exit 0 — this is a probe, not a gate
