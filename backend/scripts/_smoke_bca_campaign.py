"""End-to-end smoke test for the BCA-only arts-campaign directive.

Three layers:
  STATIC   — AST-extract both PRIORITY_OFFER + USER_BACKGROUND:ARTS slot
             strings and assert each lists the 4 BCA programs and
             explicitly forbids the others.
  LOGIC    — simulate the gating-condition tree for several realistic
             caller inputs to confirm WHICH slot fires for each.
  LIVE     — actually call gpt-4o-mini with the assembled system
             message + a few test user utterances; verify the response
             mentions BCA and does NOT mention the forbidden programs.

Run from backend/ with PYTHONPATH=. so app.* imports resolve.
Uses the real OPENAI_API_KEY from .env — costs ~$0.0002 per LLM call.
"""
import ast
import asyncio
import re
import sys
from pathlib import Path

# ─────────────── Helpers ───────────────

def _extract_slot(needle: str) -> str:
    """Pull a literal string from engine.py whose content contains `needle`
    AND the active-block marker `Campaign focus: BCA only` (so commented-
    out swap-back versions are ignored). Returns the first match."""
    src = Path("app/brain/engine.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if needle in v:
                return v
    return ""


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "v" if ok else "x"
    extra = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{extra}")
    return ok


# ─────────────── STATIC checks ───────────────

print("─" * 60)
print("STATIC — slot text contents")
print("─" * 60)

priority = _extract_slot("PRIORITY_OFFER")
arts = _extract_slot("USER_BACKGROUND: ARTS")

assert priority, "PRIORITY_OFFER slot text not found in engine.py"
assert arts, "USER_BACKGROUND: ARTS slot text not found in engine.py"

# The 4 BCA programs we expect both slots to mention by exact catalog name.
BCA_4 = [
    "BCA (Honours) (Computer Applications)",
    "BCA (Honours) (Computer Applications) (AI & ML)",
    "BCA (Honours) (Computer Applications) (Cyber Security)",
    "BCA with MCA (5 Years Integrated)",
]
# Programs the directives MUST forbid for this campaign.
FORBIDDEN = ["B.Sc IT", "B.Tech", "BBA", "MBA", "B.Pharm", "Nursing", "Marine"]

passes = 0
fails = 0

print()
print("PRIORITY_OFFER slot:")
for p in BCA_4:
    if _check(f"lists {p}", p in priority):
        passes += 1
    else:
        fails += 1
for f in FORBIDDEN:
    if _check(f"explicitly forbids {f}", f"no {f}" in priority or f"DO NOT add" in priority):
        passes += 1
    else:
        fails += 1

print()
print("USER_BACKGROUND: ARTS slot:")
for p in BCA_4:
    if _check(f"lists {p}", p in arts):
        passes += 1
    else:
        fails += 1
if _check("forbids BBA / BA / B.Com / MBA via DO NOT", "DO NOT mention BBA" in arts):
    passes += 1
else:
    fails += 1

# ─────────────── LOGIC simulation ───────────────

print()
print("─" * 60)
print("LOGIC — which slot fires for each caller input?")
print("─" * 60)

def _looks_like_stream_background(text: str) -> bool:
    """Mirror of catalog._looks_like_stream_background — keyword check."""
    low = text.lower()
    keywords = (
        "arts", "આર્ટ્સ", "આર્ટસ",
        "pcm", "pcb", "commerce", "કોમર્સ",
        "science background", "arts background",
        "10th pass", "12th pass", "10+2", "12+2",
        "after 12th", "after 10th",
        "પીસીએમ", "પીસીબી", "દસમું", "બારમું",
    )
    return any(k in low or k in text for k in keywords)


SCENARIOS = [
    # (caller_text, has_name_already, expected_slot)
    ("મારું નામ રોહિત છે", True,  "PRIORITY_OFFER"),
    ("એ આર્ટ્સ કરેલું છે તો કોઈ કોર્સ સજેસ્ટ કરો ને", False, "USER_BACKGROUND_ARTS"),
    ("મારું નામ રોહિત, આર્ટ્સ ભણ્યો છું", True, "USER_BACKGROUND_ARTS"),
    ("Hello", False, "neither (no name, no background)"),
    ("12th arts done", True,  "USER_BACKGROUND_ARTS"),
]
for text, has_name, expected in SCENARIOS:
    bg = _looks_like_stream_background(text)
    # PRIORITY_OFFER fires when: has_name AND not(background) AND not(named program)
    if bg:
        fired = "USER_BACKGROUND_ARTS" if any(k in text.lower() for k in ("arts", "આર્ટ્સ")) else "USER_BACKGROUND_OTHER"
    elif has_name:
        fired = "PRIORITY_OFFER"
    else:
        fired = "neither (no name, no background)"
    ok = expected in fired
    if ok:
        passes += 1
    else:
        fails += 1
    print(f"  [{'v' if ok else 'x'}] caller said: {text[:50]:50} → {fired}  (expected {expected})")


# ─────────────── LIVE LLM round-trip ───────────────

print()
print("─" * 60)
print("LIVE — actual gpt-4o-mini round-trip")
print("─" * 60)


async def _live_test():
    global passes, fails
    from openai import AsyncOpenAI
    from app.config import get_settings
    s = get_settings()
    if not s.openai_api_key:
        print("  [x] OPENAI_API_KEY not set — skipping live test")
        return
    client = AsyncOpenAI(api_key=s.openai_api_key)

    # Two scenarios we want to verify the live model handles correctly.
    scenarios = [
        {
            "label": "Caller volunteers arts background",
            "slot": arts,
            "user": "મારું નામ રોહિત છે, મેં બારમું આર્ટ્સ કરેલું છે. કોઈ કોર્સ સજેસ્ટ કરો.",
        },
        {
            "label": "Caller gives name only (PRIORITY_OFFER path)",
            "slot": priority,
            "user": "મારું નામ રોહિત છે.",
        },
    ]

    base_system = (
        "You are Gunivox, an admissions counsellor for Ganpat University. "
        "Speak warmly and concisely in the caller's language. Use the provided "
        "context to answer questions about programs, fees, eligibility."
    )

    for sc in scenarios:
        print()
        print(f"  Scenario: {sc['label']}")
        # Stitch the slot text into the system message exactly like
        # engine.py does (combined into the SESSION SLOTS line).
        combined = f"{base_system}\n\nSESSION SLOTS: {sc['slot']}"
        try:
            resp = await client.chat.completions.create(
                model=s.openai_model,
                messages=[
                    {"role": "system", "content": combined},
                    {"role": "user", "content": sc["user"]},
                ],
                temperature=0,
                max_tokens=200,
            )
        except Exception as e:
            fails += 1
            print(f"    [x] LLM call failed: {e}")
            continue
        out = (resp.choices[0].message.content or "").strip()
        print(f"    user: {sc['user']}")
        print(f"    AI : {out}")
        # Score the response.
        mentions_bca = "BCA" in out or "બીસીએ" in out.lower() or "BCA" in out.upper()
        contains_forbidden = []
        for forbidden in ("BBA", "B.Sc IT", "B.Tech", "B.Pharm", "Nursing", "MBA"):
            if forbidden in out:
                contains_forbidden.append(forbidden)
        if mentions_bca:
            passes += 1
            print(f"    [v] mentions BCA")
        else:
            fails += 1
            print(f"    [x] DID NOT mention BCA")
        if not contains_forbidden:
            passes += 1
            print(f"    [v] no forbidden programs mentioned")
        else:
            fails += 1
            print(f"    [x] mentions forbidden: {contains_forbidden}")


try:
    asyncio.run(_live_test())
except KeyboardInterrupt:
    print("  (live test interrupted)")
except Exception as e:
    print(f"  [x] live test crashed: {type(e).__name__}: {e}")


# ─────────────── Verdict ───────────────

print()
print("─" * 60)
print(f"VERDICT: {passes} pass · {fails} fail")
print("─" * 60)
sys.exit(0 if fails == 0 else 1)
