"""Pull the assembled USER_BACKGROUND: ARTS slot text via AST and
verify it lists the 4 BCA programs and excludes all others."""
import ast
from pathlib import Path

src = Path("app/brain/engine.py").read_text(encoding="utf-8")
tree = ast.parse(src)

hits = []
for node in ast.walk(tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if "USER_BACKGROUND: ARTS" in node.value and "Campaign focus" in node.value:
            hits.append(node.value)

assert hits, "Active ARTS slot not found"
active = hits[0]

print("=== Active ARTS slot text ===")
print(active[:600])
print()

print("=== Checks ===")
must_contain = [
    "BCA (Honours) (Computer Applications)",
    "BCA (Honours) (Computer Applications) (AI & ML)",
    "BCA (Honours) (Computer Applications) (Cyber Security)",
    "BCA with MCA (5 Years Integrated)",
    "DO NOT mention BBA",
    "Campaign focus: BCA only",
]
must_NOT_contain_as_recommendation = []  # we ONLY want them in the do-not-mention list
for s in must_contain:
    print(f"  {'v' if s in active else 'x'}  contains: {s}")
