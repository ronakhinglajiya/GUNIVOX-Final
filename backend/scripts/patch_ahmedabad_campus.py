"""Tag DCS-CITY and CMSR programs as Ahmedabad Campus.

The university told us these institutes are at the Ahmedabad campus.
Same-named programs at the Mehsana main campus (DCS- Main Campus) and
VMPIM keep colliding in RAG retrieval, so we make the Ahmedabad location
explicit in BOTH the `institute` field and the `text_field_gu` (which is
what RAG embeds against).
"""
import json
import shutil
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "admission_brain.json"

# Backup once (idempotent — only writes if no backup yet)
backup = DATA.with_suffix(".json.bak")
if not backup.exists():
    shutil.copy2(DATA, backup)
    print(f"backup: {backup.name}")

with open(DATA, encoding="utf-8") as f:
    data = json.load(f)

INSTITUTE_REMAP = {
    "DCS-CITY":     "DCS Ahmedabad Campus",
    "DCS- CITY":    "DCS Ahmedabad Campus",
    "CMSR":         "CMSR Ahmedabad Campus",
    "ICT":          "ICT Ahmedabad Campus",  # B.Tech CSE variants
}

AHMEDABAD_MARKER = "આ પ્રોગ્રામ Ahmedabad કેમ્પસ (અમદાવાદ) પર ઉપલબ્ધ છે."

patched = 0
for p in data:
    old_inst = (p.get("institute") or "").strip()
    new_inst = INSTITUTE_REMAP.get(old_inst)
    if not new_inst:
        # Already patched in a previous run — re-check by current institute.
        cur = (p.get("institute") or "").strip()
        if "Ahmedabad" not in cur:
            continue
        new_inst = cur  # already correct
    p["institute"] = new_inst
    # Append the Ahmedabad marker into text_field_gu so the Gujarati
    # embedding picks up the location signal. Skip if already there.
    gu = (p.get("text_field_gu") or "").strip()
    if "અમદાવાદ" not in gu and "Ahmedabad" not in gu:
        gu = gu + " " + AHMEDABAD_MARKER
    p["text_field_gu"] = gu
    patched += 1
    print(f"  patched: {p.get('program')!r:60} | -> {new_inst!r}")

with open(DATA, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"\nTotal patched: {patched}")
print(f"Wrote: {DATA}")
