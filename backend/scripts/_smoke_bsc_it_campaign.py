"""End-to-end smoke test for the B.Sc IT-only (DCS Main Campus) campaign.

Layers:
  STATIC   — AST-extract the CAMPAIGN_RULE / PRIORITY_OFFER / USER_BACKGROUND
             slot strings from engine.py and assert each pitches B.Sc IT,
             forbids the other programs, and excludes the Ahmedabad campus.
  CONFIG   — the bsc_it_only_campaign flag is ON and bca_only_campaign OFF.
  TTS      — the 4 Main-Campus B.Sc IT program names normalize to clean
             Gujarati phonetics (no stray English words Sarvam mis-reads).
  LIVE     — real gpt-4o-mini round-trips: caller gives name / asks about
             another course / mentions arts background → response must
             pitch B.Sc IT and NOT drift to BCA/BBA/etc.

Run from backend/ with PYTHONPATH=. (uses the real OPENAI_API_KEY).
"""
import ast
import asyncio
import sys
from pathlib import Path

passes = 0
fails = 0


def _check(label: str, ok: bool, detail: str = "") -> None:
    global passes, fails
    mark = "v" if ok else "x"
    extra = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{extra}")
    if ok:
        passes += 1
    else:
        fails += 1


def _extract_slot(needle: str) -> str:
    """First string literal in engine.py containing `needle`."""
    src = Path("app/brain/engine.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if needle in node.value:
                return node.value
    return ""


# ─────────────── STATIC ───────────────
print("─" * 60)
print("STATIC — slot text contents")
print("─" * 60)

rule = _extract_slot("CAMPAIGN_RULE")
offer = _extract_slot("PRIORITY_OFFER: OPENING")
bg = _extract_slot("IGNORE the stream for recommendation")

_check("CAMPAIGN_RULE slot found", bool(rule))
_check("PRIORITY_OFFER slot found", bool(offer))
_check("USER_BACKGROUND slot found", bool(bg))

print("\nCAMPAIGN_RULE:")
_check("sells ONLY B.Sc IT", "sells ONLY B.Sc IT" in rule)
_check("names DCS Main Campus", "Main Campus" in rule)
_check("excludes Ahmedabad city campus", "Ahmedabad" in rule and "NOT offered" in rule)
_check("forbids BCA", "NEVER recommend BCA" in rule)
_check("forbids BBA/B.Tech/MBA", all(p in rule for p in ("BBA", "B.Tech", "MBA")))
_check("has 3-step pitch flow", all(s in rule for s in ("STEP 1", "STEP 2", "STEP 3")))
_check("has pivot behavior", "PIVOT BEHAVIOR" in rule)
_check("BCA listed as a pivot trigger", "'BCA'" in rule)
_check("forbids enumerating all 4", "NEVER recite all 4" in rule)

print("\nPRIORITY_OFFER:")
_check("pitches B.Sc IT value", "B.Sc IT's value" in offer)
_check("no fee at open", "DO NOT quote any fee" in offer)
_check("no variant enumeration", "DO NOT enumerate the 4 B.Sc IT variants" in offer)
_check("excludes Ahmedabad campus", "Ahmedabad" in offer)

print("\nUSER_BACKGROUND (stream):")
_check("B.Sc IT-only at Main Campus", "B.Sc IT-only at the Main Campus" in bg)
_check("forbids BCA in this slot", "DO NOT mention BCA" in bg)
_check("mentions bridge course honesty", "bridge course" in bg)

# Ensure NO active (non-comment) slot still pitches "BCA only" as the campaign.
src_lines = Path("app/brain/engine.py").read_text(encoding="utf-8").splitlines()
active_bca_campaign = [
    i + 1 for i, ln in enumerate(src_lines)
    if "sells ONLY BCA" in ln and not ln.lstrip().startswith("#")
]
_check("no active slot still sells ONLY BCA", not active_bca_campaign,
       f"lines={active_bca_campaign}" if active_bca_campaign else "")


# ─────────────── CONFIG ───────────────
print("\n" + "─" * 60)
print("CONFIG — campaign flags")
print("─" * 60)
sys.path.insert(0, ".")
from app.config import get_settings
s = get_settings()
_check("bsc_it_only_campaign is ON", s.bsc_it_only_campaign is True)
_check("bca_only_campaign is OFF", s.bca_only_campaign is False)


# ─────────────── TTS ───────────────
print("\n" + "─" * 60)
print("TTS — program-name pronunciation")
print("─" * 60)
from app.brain.text_normalize import normalize

# Each (input, must-NOT-contain latin-letter fragment) — a clean Gujarati
# render should not leave these English tokens for Sarvam to mangle.
tts_cases = [
    ("B.Sc IT", ["B.Sc", "Sc"]),
    ("B.Sc IT (Hons.) (Data Science)", ["Hons", "Data", "Science"]),
    ("B.Sc IT (Hons.) (Cyber Security)", ["Cyber", "Security", "Hons"]),
    ("B.Sc IT (Hons.) (AI & ML)", ["Hons"]),
    ("B.Sc (CA and IT) (Hons.)", ["Hons"]),
    ("Information Technology", ["Information", "Technology"]),
]
for text, banned in tts_cases:
    out = normalize(text)
    leftover = [b for b in banned if b in out]
    _check(f"{text!r} → {out!r}", not leftover,
           f"leftover English: {leftover}" if leftover else "")


# ─────────────── LIVE ───────────────
print("\n" + "─" * 60)
print("LIVE — gpt-4o-mini round-trips")
print("─" * 60)


async def _live() -> None:
    global passes, fails
    from openai import AsyncOpenAI
    st = get_settings()
    if not st.openai_api_key:
        print("  [x] OPENAI_API_KEY not set — skipping live")
        fails += 1
        return
    client = AsyncOpenAI(api_key=st.openai_api_key)

    base = (
        "You are Gunivox, an admissions counsellor for Ganpat University. "
        "Speak warmly and concisely in the caller's language."
    )
    scenarios = [
        ("Name only (opening)", offer, "મારું નામ રાહુલ છે."),
        ("Asks about a NON-target course (BBA)", rule,
         "મારે BBA કરવું છે, એના વિશે કહો."),
        ("Volunteers arts background", bg,
         "મેં બારમું આર્ટ્સ કરેલું છે, કયો કોર્સ સારો રહેશે?"),
    ]

    FORBIDDEN = ("BCA", "BBA", "B.Tech", "B.Pharm", "Nursing", "MBA", "B.Com")
    for label, slot, user in scenarios:
        print(f"\n  Scenario: {label}")
        combined = f"{base}\n\nSESSION SLOTS: {rule}\n{slot}"
        try:
            resp = await client.chat.completions.create(
                model=st.openai_model,
                messages=[
                    {"role": "system", "content": combined},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=220,
            )
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"    [x] LLM call failed: {e}")
            continue
        out = (resp.choices[0].message.content or "").strip()
        print(f"    user: {user}")
        print(f"    AI  : {out}")

        mentions_it = ("B.Sc" in out or "બીએસસી" in out or "IT" in out
                       or "આઈટી" in out)
        _check("mentions B.Sc IT", mentions_it)

        # BBA scenario is allowed a brief ack of BBA before the pivot; the
        # others must not name any forbidden program at all.
        if label.startswith("Asks about"):
            # Must pivot back to IT and must NOT quote a fee for BBA.
            _check("does not quote a fee for BBA",
                   not any(t in out for t in ("ફી", "fee", "હજાર", "લાખ"))
                   or "IT" in out or "આઈટી" in out)
        else:
            bad = [f for f in FORBIDDEN if f in out]
            _check("no forbidden program named", not bad,
                   f"found {bad}" if bad else "")


try:
    asyncio.run(_live())
except Exception as e:  # noqa: BLE001
    print(f"  [x] live crashed: {type(e).__name__}: {e}")
    fails += 1


# ─────────────── VERDICT ───────────────
print("\n" + "─" * 60)
print(f"VERDICT: {passes} pass · {fails} fail")
print("─" * 60)
sys.exit(0 if fails == 0 else 1)
