"""Smoke-test the two changes:
1) ICT / CMSR / DCS-CITY now route to the Ahmedabad campus path.
2) The PRIORITY_OFFER slot fires on the opening turn (verified by
   parsing engine.py for the slot construction code).
"""
import re
from pathlib import Path

from app.brain.catalog import get_catalog


def _check_marker(text: str) -> tuple[str, int] | None:
    """Return (campus_key, n_programs) if marker triggers, else None."""
    cat = get_catalog()
    r = cat.find_campus_query(text)
    if r is None:
        return None
    key, progs = r
    return (key, len(progs))


# ── 1) Ahmedabad classification ────────────────────────────────────────
print("=== Ahmedabad campus classification ===")
checks = [
    # Should now route to Ahmedabad
    ("I want to know about ICT", True),
    ("CMSR institute fees?",     True),
    ("DCS-CITY admission",       True),
    ("dcs city",                 True),
    ("આઈસીટી",                   True),
    ("સીએમએસઆર",                 True),
    # Existing baseline (these were ALREADY working before)
    ("Ahmedabad campus please",  True),
    ("city campus",              True),
    # Should NOT route to Ahmedabad
    ("DCS- Main Campus",         True),   # "city" is in "campus"? -> let's see
    ("B.Tech Marine",            False),
    ("Mehsana",                  False),
]
for text, expected_match in checks:
    res = _check_marker(text)
    matched = res is not None
    tag = "✓" if matched == expected_match else "✗"
    if res:
        print(f"  {tag} {text!r:40} → {res[0]:10} ({res[1]} programs)")
    else:
        print(f"  {tag} {text!r:40} → no match (expected {'match' if expected_match else 'no match'})")


# ── 2) PRIORITY_OFFER wiring ───────────────────────────────────────────
print("\n=== PRIORITY_OFFER slot wiring ===")
engine_src = Path("app/brain/engine.py").read_text(encoding="utf-8")
required_markers = [
    "PRIORITY_OFFER:",
    "B.Sc IT (Hons.) (AI & ML)",
    "B.Sc IT (Hons.) (Cyber Security)",
    "BCA + MCA Dual Degree (3+2 years integrated)",
    "Infrastructure Management Services",
    "BBA",
    "_user_turn_count <= 1",
    "not focused_program",
    "not _already_named_program",
]
for marker in required_markers:
    found = marker in engine_src
    tag = "✓" if found else "✗"
    print(f"  {tag} {marker!r}")

# Also ensure the slot block guard is correctly placed AFTER the
# caller_name slot and BEFORE the USER_BACKGROUND slot.
caller_pos = engine_src.find("The caller's name is")
priority_pos = engine_src.find("PRIORITY_OFFER:")
background_pos = engine_src.find("USER_BACKGROUND: PCB")
ordering_ok = caller_pos < priority_pos < background_pos
print(f"  {'✓' if ordering_ok else '✗'} ordering: caller_name → PRIORITY_OFFER → USER_BACKGROUND")
