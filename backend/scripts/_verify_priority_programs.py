"""Verify the 6 priority programs exist in admission_brain.json and
report exactly which campuses they live at. No assumptions — read the
file."""
import json
from pathlib import Path

data = json.loads(Path("data/admission_brain.json").read_text(encoding="utf-8"))

# User's 6 flagship programs (as named in their message)
targets = [
    ("BSc IT (Honors) (AI & ML)",                ["AI & ML", "AI&ML"]),
    ("BSc IT (Honours) (Cyber Security)",        ["Cyber Security", "Cyber Secuirity"]),
    ("BSc (honors) (IT) — general track",        ["B.Sc. IT (Hons.)", "B. Sc. IT (Hons.)"]),
    ("BCA + MCA (3+2 Years Dual Degree)",        ["BCA with MCA", "BCA + MCA", "BCA+MCA"]),
    ("BSc IT (Honors) (Infrastructure Mgmt)",    ["Infrastructure Management"]),
    ("BBA",                                       ["BBA"]),
]

print("PRIORITY PROGRAM VERIFICATION")
print("=" * 78)
for label, needles in targets:
    print(f"\n  {label}")
    # Find every program in the catalog that matches any needle (substring,
    # case-insensitive)
    matches = []
    for p in data:
        name = p.get("program", "")
        nlow = name.lower()
        for n in needles:
            if n.lower() in nlow:
                # For the general-track row, EXCLUDE any specialization variants
                if "general track" in label.lower() and any(
                    spec in nlow for spec in (
                        "ai & ml", "cyber security", "cyber secuirity",
                        "infrastructure", "data science",
                    )
                ):
                    continue
                matches.append((name, p.get("institute"), p.get("fees")))
                break
    if not matches:
        print("    ✗ NOT FOUND in catalog")
        continue
    for name, inst, fees in matches:
        campus = (
            "AHMEDABAD" if "Ahmedabad" in (inst or "")
            else "MEHSANA"  if "Main Campus" in (inst or "")
            else (inst or "?")
        )
        print(f"    • {name[:48]:48} {inst[:25]:25} ₹{fees}  [{campus}]")
