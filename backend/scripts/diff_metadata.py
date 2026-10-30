"""Quick diff: accurate metadata vs current admission_brain.json. Prints
only programs whose fees / counsellor_name / counsellor_phone differ.
"""
import json
from pathlib import Path

CUR = Path(__file__).resolve().parent.parent / "data" / "admission_brain.json"
ACC = Path(__file__).resolve().parent.parent / "data" / "metadata_full_accurate.json"

with open(CUR, encoding="utf-8") as f:
    current = {p["program"].strip(): p for p in json.load(f)}
with open(ACC, encoding="utf-8") as f:
    accurate = json.load(f)

mismatches = 0
for a in accurate:
    name = a["program"].strip()
    c = current.get(name)
    if not c:
        print(f"  MISSING IN CURRENT: {name!r}")
        mismatches += 1
        continue
    for field in ("fees", "counsellor_phone"):
        cv = (c.get(field) or "")
        av = (a.get(field) or "")
        if isinstance(cv, str): cv = cv.strip()
        if isinstance(av, str): av = av.strip()
        if cv != av:
            print(f"  {name!r}")
            print(f"    {field}: current={cv!r}  accurate={av!r}")
            mismatches += 1

print(f"\nMismatches: {mismatches}")
