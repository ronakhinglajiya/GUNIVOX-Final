"""List every BCA program in the catalog with institute + fee + eligibility hint."""
import json
from pathlib import Path

data = json.loads(Path("data/admission_brain.json").read_text(encoding="utf-8"))
print("=== All BCA programs in the catalog ===")
for p in data:
    n = p.get("program", "")
    if "BCA" in n:
        inst = p.get("institute", "?")
        fees = p.get("fees", "?")
        dur = p.get("duration", "?")
        print(f"  {n:55} @ {inst:25} fee=₹{fees:>8} dur={dur}")
